"""补充：撤回/下架后预渲染产物必须失效（R12 的缓存失效部分）。"""

import json
from pathlib import Path

from app.platform.ids import now_ms


def _routes_of(output_dir: Path) -> set[str]:
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    return {item["route"] for item in manifest["routes"]} | set(manifest["route_list"])


def test_unpublished_entry_disappears_from_prebuild_routes(db, tmp_path):
    """下架后重新预渲染：旧路由与新产物都不再包含该条内容（缓存按路由清单失效）。

    审计指出预渲染链路曾只把任务标成 DONE；修好后还要证明「撤回真的会让公开产物失效」，
    否则撤回只是账面上的状态变化。
    """
    from app.modules.content.models import ContentEntry
    from app.ops.prebuild import run_prebuild

    entry = ContentEntry(
        id="cnt_unpublish_regression",
        kind="news",
        slug="unpublish-regression",
        title="撤回回归公告",
        summary="撤回回归用例",
        sanitized_body="<p>正文</p>",
        body_format="html",
        status="published",
        publish_at=now_ms(),
    )
    db.add(entry)
    db.commit()

    output_dir = tmp_path / "prebuild"
    first = run_prebuild(db, output_dir=output_dir, trigger="test")
    assert first["status"] == "DONE"
    published_routes = _routes_of(output_dir)
    assert any("unpublish-regression" in route for route in published_routes), published_routes

    # 下架（08/12 文档：撤回后的公开内容不可再被获取）。
    entry.status = "archived"
    entry.unpublish_at = now_ms()
    entry.version += 1
    db.commit()

    second = run_prebuild(db, output_dir=output_dir, trigger="test")
    assert second["status"] == "DONE"
    assert second["snapshot_id"] != first["snapshot_id"], "下架后必须产生新的快照版本"
    after_routes = _routes_of(output_dir)
    assert not any("unpublish-regression" in route for route in after_routes), after_routes
    assert after_routes != published_routes, "产物必须真实变化，而不是原样复用上一版"
