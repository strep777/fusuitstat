"""公开内容的发布窗口、授权与语言隔离。"""
import pytest

from app.modules.content.models import ContentEntry, SeriesSpec
from app.modules.content.repository import get_setting
from app.modules.content.service import ensure_default_settings
from app.platform.ids import now_ms


def entry(db, *, kind, slug, locale="zh-CN", status="published", **values):
    row = ContentEntry(kind=kind, slug=slug, locale=locale, title=f"{locale}-{slug}",
                       status=status, **values)
    db.add(row)
    db.flush()
    if kind == "series":
        db.add(SeriesSpec(entry_id=row.id, code=f"{locale}-{slug}", is_active=1))
    return row


def enable_english(db):
    ensure_default_settings(db)
    row = get_setting(db, "platform.feature_flags")
    row.value = {**row.value, "en_locale": True}
    db.commit()


def test_unpublished_series_never_leaks_through_aggregates(db, client):
    entry(db, kind="series", slug="public")
    entry(db, kind="series", slug="draft", status="draft")
    entry(db, kind="series", slug="future", publish_at=now_ms() + 86400000)
    entry(db, kind="series", slug="expired", unpublish_at=now_ms() - 1000)
    db.commit()
    listing = client.get("/api/v1/content/series").json()
    assert [item["slug"] for item in listing["items"]] == ["public"]
    assert [item["slug"] for item in client.get("/api/v1/site/home").json()["series"]] == ["public"]
    facets = client.get("/api/v1/content/work/facets").json()
    assert [item["value"] for item in facets["series"]] == ["zh-CN-public"]


def test_work_facets_and_sitemap_respect_authorization_and_publication_window(db, client):
    entry(db, kind="work", slug="public", display_authorized=1, species="public", work_type="public")
    entry(db, kind="work", slug="private", display_authorized=0, species="private", work_type="private")
    entry(db, kind="work", slug="expired", display_authorized=1, unpublish_at=now_ms()-1000,
          species="expired", work_type="expired")
    entry(db, kind="work", slug="future", display_authorized=1, publish_at=now_ms()+86400000,
          species="future", work_type="future")
    db.commit()
    facets = client.get("/api/v1/content/work/facets").json()
    for key in ("species", "work_types"):
        assert facets[key] == [{"value": "public", "count": 1}]
    urls = [item["loc"] for item in client.get("/api/v1/site/sitemap").json()["urls"]]
    assert any(url.endswith("/works/public") for url in urls)
    assert not any(url.endswith(("/works/private", "/works/expired", "/works/future")) for url in urls)


def test_english_content_and_home_use_only_requested_published_locale(db, client):
    enable_english(db)
    for locale in ("zh-CN", "en-US"):
        entry(db, kind="news", slug="shared", locale=locale)
        entry(db, kind="series", slug="shared", locale=locale)
        entry(db, kind="faq", slug="shared", locale=locale)
    entry(db, kind="news", slug="chinese-only")
    db.commit()
    for endpoint in ("news", "series"):
        listing = client.get(f"/api/v1/content/{endpoint}?locale=en-US").json()
        assert listing["total"] == 1
        assert listing["items"][0]["title"] == "en-US-shared"
        detail = client.get(f"/api/v1/content/{endpoint}/shared?locale=en-US").json()
        assert detail["locale"] == "en-US"
        assert detail["route"].startswith("/en/")
        assert detail["seo"]["canonical"].startswith("/en/")
    series = client.get("/api/v1/content/series/shared?locale=en-US").json()
    assert all(item["locale"] == "en-US" for item in series["faqs"])
    home = client.get("/api/v1/site/home?locale=en-US").json()
    assert home["news"]["items"][0]["locale"] == "en-US"
    assert home["series"][0]["title"] == "en-US-shared"
    assert client.get("/api/v1/content/news/chinese-only?locale=en-US").status_code == 404


def test_bilingual_prebuild_keeps_both_routes_and_excludes_draft_series(db, tmp_path):
    import json

    from app.ops.prebuild import run_prebuild

    enable_english(db)
    for locale in ("zh-CN", "en-US"):
        entry(db, kind="news", slug="shared", locale=locale)
        entry(db, kind="series", slug="shared", locale=locale)
    entry(db, kind="series", slug="draft", status="draft")
    db.commit()
    output = tmp_path / "localized-output"
    result = run_prebuild(db, output_dir=output, locales=("zh-CN", "en-US"))
    assert result["status"] == "DONE", result
    assert "/news/shared" in result["routes"] and "/en/news/shared" in result["routes"]
    assert "/en/fursuits/shared" in result["routes"]
    series = json.loads((output / "data/site/series.json").read_text(encoding="utf-8"))
    assert {item["title"] for item in series} == {"zh-CN-shared", "en-US-shared"}


def test_home_product_price_and_availability_follow_requested_region(db, client):
    from tests.conftest import create_product, create_sku

    enable_english(db)
    product = create_product(db, slug="mixed")
    create_sku(db, product_id=product.id, currency="CNY", price_cents=100, stock_on_hand=5)
    create_sku(db, product_id=product.id, currency="USD", price_cents=5000, stock_on_hand=0)
    domestic = create_product(db, slug="domestic")
    create_sku(db, product_id=domestic.id, currency="CNY", price_cents=200, stock_on_hand=5)
    db.commit()
    products = client.get("/api/v1/site/home?locale=en-US").json()["products"]
    assert products["total"] == 1
    assert products["items"][0]["currency"] == "USD"
    assert products["items"][0]["price_from_cents"] == 5000
    assert products["items"][0]["availability"] == "sold_out"


@pytest.mark.parametrize("path", ["/content/news", "/content/news/example", "/content/series",
                                  "/content/series/example", "/content/work/facets", "/site/home"])
def test_english_content_requires_enabled_feature_and_valid_locale(client, path):
    response = client.get(f"/api/v1{path}?locale=en-US")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"
    assert client.get(f"/api/v1{path}?locale=fr-FR").status_code == 422
