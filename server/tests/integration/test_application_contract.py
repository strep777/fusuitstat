"""掉落/服务申请列表的契约回归（R36）。

缺陷：`GET /stock-applications` 按 06 文档返回**本人申请的数组**，而前端按 `Page<T>`
解析（`payload.items`）。`undefined` 进入渲染后，`applications.value.filter(...)`
在渲染期抛 TypeError，页面永久停在「正在读取商品信息」——商品详情与「我的申请」两页
对已登录用户完全不可用。

本文件把三件事固定住：
1. 列表响应就是数组（不是 `{items:...}`）；
2. `product_id` 过滤生效（否则「只看最新 N 条」会漏掉目标商品，误判为未获准）；
3. 他人申请不可见。
"""
from __future__ import annotations

BASE = "/api/v1"
ORIGIN = "http://127.0.0.1:5173"


def _headers(token: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def _make_approval_product(db, *, slug: str = "e2e-approval-product"):
    from tests.conftest import create_product, create_sku

    product = create_product(db, slug=slug, title="需要审批的掉落商品", requires_approval=True)
    create_sku(db, product_id=product.id, price_cents=2400000, stock_on_hand=1)
    db.commit()
    return product


def test_stock_applications_returns_array_not_page(db, client):
    """响应必须是数组：前端按 `Page<T>` 解析会让 `.items` 变 undefined 并拖垮渲染。"""
    from tests.conftest import create_user, issue_login

    user = create_user(db, nickname="申请契约用户", phone="13800000401")
    db.flush()
    token = issue_login(db, client, user)
    product = _make_approval_product(db)

    created = client.post(
        f"{BASE}/stock-applications",
        json={"product_id": product.id, "reason": "希望申请该掉落角色，用于演出。", "kind": "drop"},
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text

    listed = client.get(f"{BASE}/stock-applications", headers={"Origin": ORIGIN})
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert isinstance(payload, list), f"必须是数组，实际是 {type(payload).__name__}"
    assert payload and payload[0]["product_id"] == product.id
    assert "items" not in payload[0]


def test_stock_applications_filter_by_product_id(db, client):
    """`product_id` 过滤必须生效，否则商品详情页会漏掉本商品的审批状态。"""
    from tests.conftest import create_user, issue_login

    user = create_user(db, nickname="过滤用户", phone="13800000402")
    db.flush()
    token = issue_login(db, client, user)
    first = _make_approval_product(db, slug="e2e-filter-a")
    second = _make_approval_product(db, slug="e2e-filter-b")

    for product in (first, second):
        created = client.post(
            f"{BASE}/stock-applications",
            json={"product_id": product.id, "reason": "申请理由需要足够长。", "kind": "drop"},
            headers=_headers(token),
        )
        assert created.status_code == 201, created.text

    only_first = client.get(
        f"{BASE}/stock-applications", params={"product_id": first.id}, headers={"Origin": ORIGIN}
    ).json()
    assert [row["product_id"] for row in only_first] == [first.id]

    all_rows = client.get(f"{BASE}/stock-applications", headers={"Origin": ORIGIN}).json()
    assert {row["product_id"] for row in all_rows} == {first.id, second.id}


def test_stock_applications_are_owner_scoped(db, client):
    """他人申请不可见：列表只按会话用户过滤。"""
    from tests.conftest import create_user, issue_login

    mine = create_user(db, nickname="本人", phone="13800000403")
    other = create_user(db, nickname="他人", phone="13800000404")
    db.flush()
    my_token = issue_login(db, client, mine)
    product = _make_approval_product(db, slug="e2e-owner-scope")

    created = client.post(
        f"{BASE}/stock-applications",
        json={"product_id": product.id, "reason": "本人的申请理由。", "kind": "drop"},
        headers=_headers(my_token),
    )
    assert created.status_code == 201, created.text
    application_id = created.json()["id"]

    # 换人：清掉 cookie 再用另一个账号登录。
    client.cookies.clear()
    other_token = issue_login(db, client, other)
    visible = client.get(
        f"{BASE}/stock-applications", params={"product_id": product.id}, headers={"Origin": ORIGIN}
    ).json()
    assert visible == [], "不得看到他人申请"

    foreign = client.get(
        f"{BASE}/stock-applications/{application_id}",
        headers={"Origin": ORIGIN, "X-CSRF-Token": other_token},
    )
    assert foreign.status_code == 404, foreign.text
