"""双区目录：分页、SKU、价格和库存必须使用同一币种过滤条件。"""
import pytest

from tests.conftest import create_product, create_sku


@pytest.fixture()
def catalog_prices(db):
    domestic = create_product(db, slug="only-cny", title="境内商品")
    create_sku(db, product_id=domestic.id, sku_code="CNY-A", currency="CNY", price_cents=12000)
    mixed = create_product(db, slug="both-currencies", title="双区商品")
    create_sku(db, product_id=mixed.id, sku_code="CNY-B", currency="CNY", price_cents=100, stock_on_hand=5)
    create_sku(db, product_id=mixed.id, sku_code="USD-B", currency="USD", price_cents=5000, stock_on_hand=0)
    inactive = create_product(db, slug="inactive-usd", title="下架规格")
    sku = create_sku(db, product_id=inactive.id, sku_code="USD-C", currency="USD")
    sku.is_active = 0
    db.commit()
    return domestic, mixed


def test_currency_filter_pagination_price_and_stock(client, catalog_prices):
    response = client.get("/api/v1/products", params={"currency": "USD", "limit": 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 1 and result["has_more"] is False
    item = result["items"][0]
    assert item["slug"] == "both-currencies"
    assert item["currency"] == "USD" and item["price_from_cents"] == 5000
    assert item["availability"] == "sold_out"
    assert {sku["currency"] for sku in item["skus"]} == {"USD"}
    next_page = client.get("/api/v1/products", params={"currency": "USD", "page": 2}).json()
    assert next_page["total"] == 1 and next_page["items"] == []


def test_detail_filters_skus_without_hiding_product(client, catalog_prices):
    result = client.get("/api/v1/products/both-currencies", params={"currency": "USD"}).json()
    assert result["price_from_cents"] == 5000 and result["currency"] == "USD"
    assert len(result["skus"]) == 1 and result["availability"] == "sold_out"
    absent = client.get("/api/v1/products/only-cny", params={"currency": "USD"})
    assert absent.status_code == 200
    assert absent.json()["skus"] == [] and absent.json()["price_from_cents"] is None
    assert absent.json()["availability"] == "unavailable"


@pytest.mark.parametrize("path", ["/products", "/products/both-currencies"])
def test_unsupported_currency_rejected(client, catalog_prices, path):
    response = client.get("/api/v1" + path, params={"currency": "EUR"})
    assert response.status_code == 422


def test_currency_combines_with_search_and_total(client, catalog_prices):
    result = client.get("/api/v1/products", params={"currency": "USD", "q": "境内"}).json()
    assert result["items"] == [] and result["total"] == 0
    result = client.get("/api/v1/products", params={"currency": "CNY"}).json()
    assert result["total"] == 2
