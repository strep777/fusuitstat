"""受控预渲染：把已发布内容导出为静态数据与路由清单，供部署脚本使用。

边界（11/14 文档、ADR-04）：
- 本模块**不跑 Vite、不产出前端静态包**，只产出「已发布内容的静态数据 + 路由清单 +
  内容摘要」，由部署脚本用它们做真实构建与原子切换；
- 只导出已发布且已到发布时间的公开内容，草稿、未授权作品、后台与他人数据不进入产物；
- 失败保留上一版本：新数据写到临时目录，全部写成功后才改名为正式目录；任何一步抛错
  都删除临时目录，正式目录与其 ``manifest.json`` 保持不变，因此消费方读到的永远是
  一个自洽的旧快照；
- 输出前记录 ``prebuild_jobs`` 状态（RUNNING → DONE/FAILED），并写审计。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.modules.content import repository as content_repo
from app.modules.content import service as content_service
from app.modules.content.models import CONTENT_KINDS, ContentEntry, PrebuildJob
from app.platform import audit
from app.platform.config import Settings, get_settings
from app.platform.errors import AppError
from app.platform.ids import iso, new_id, now_ms

logger = logging.getLogger("starium.ops.prebuild")

PREBUILD_FORMAT = "starium.prebuild/1"
MANIFEST_NAME = "manifest.json"
#: 中文内容默认静态化；其他语言上线时再按 locale 增开目录（11 文档）。
DEFAULT_LOCALES: tuple[str, ...] = ("zh-CN",)
PER_KIND_LIMIT = 500

#: 每个路由的 HTML 壳只引用同目录 JSON，不进任何密钥、会话或后台数据。
HTML_SHELL = """<!doctype html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<script type="application/json" id="__starium_prebuild__" src="{data_file}"></script>
</head>
<body>
<div id="app" data-prebuild="{data_file}"></div>
<!-- 数据壳由 app.ops.prebuild 生成；真实静态包由部署脚本用这些数据构建。 -->
</body>
</html>
"""


def _safe_relative(route: str) -> str:
    """校验路由可安全落盘：只允许站内绝对路径，且不产生目录穿越。"""
    if not route.startswith("/") or route.startswith("//"):
        raise AppError("INTERNAL_ERROR", f"预渲染路由不合法：{route}")
    cleaned = route.strip("/")
    if not cleaned:
        return "index"
    if ".." in cleaned.split("/") or "\\" in cleaned:
        raise AppError("INTERNAL_ERROR", f"预渲染路由不合法：{route}")
    return cleaned


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _entry_data(session: Session, entry: ContentEntry) -> dict[str, Any]:
    payload = content_service.entry_payload(entry, include_body=True)
    payload["cover"] = (
        _media_payload(session, entry.cover_asset_id) if entry.cover_asset_id else None
    )
    return payload


def _media_payload(session: Session, asset_id: str) -> dict[str, Any] | None:
    """公开素材载荷（相对路径，不含签名 URL，因此可以进静态产物）。"""
    try:
        from app.modules.media.service import public_media_payload
    except ImportError:  # pragma: no cover - 媒体模块尚未交付时降级
        return None
    return public_media_payload(session, asset_id)


def prebuild_routes(session: Session, *, locales: tuple[str, ...] = DEFAULT_LOCALES) -> list[str]:
    """产出需要静态化的公开路由清单（不含后台、付款、用户中心与未公开报价）。"""
    routes: set[str] = set()
    for locale in locales:
        for kind in CONTENT_KINDS:
            rows, _total = content_repo.list_published(session, kind=kind, locale=locale, limit=PER_KIND_LIMIT)
            for entry in rows:
                routes.add(content_service.public_route_for(entry))
    for entry in content_repo.list_sitemap_entries(session):
        routes.add(content_service.public_route_for(entry))
    return sorted(routes)


def _collect_dataset(session: Session, *, locales: tuple[str, ...]) -> dict[str, Any]:
    """在内存中组装完整数据集；组装失败不写任何文件。"""
    files: dict[str, Any] = {}
    route_index: list[dict[str, Any]] = []
    seen: set[str] = set()

    for locale in locales:
        for kind in CONTENT_KINDS:
            rows, total = content_repo.list_published(
                session, kind=kind, locale=locale, limit=PER_KIND_LIMIT
            )
            items = [_entry_data(session, entry) for entry in rows]
            files[f"data/lists/{locale}/{kind}.json"] = {
                "kind": kind,
                "locale": locale,
                "total": total,
                "items": items,
            }
            for entry in rows:
                route = content_service.public_route_for(entry)
                if route in seen:
                    continue
                seen.add(route)
                name = _safe_relative(route)
                data = _entry_data(session, entry)
                files[f"data/pages/{name}.json"] = data
                route_index.append(
                    {
                        "route": route,
                        "kind": entry.kind,
                        "slug": entry.slug,
                        "locale": entry.locale,
                        "status": entry.status,
                        "revision": entry.revision,
                        "updated_at": iso(entry.updated_at),
                        "data_file": f"data/pages/{name}.json",
                        "html_file": f"{name}/index.html" if name != "index" else "index.html",
                        "digest": _digest(data),
                        "title": entry.title,
                        "description": entry.summary,
                    }
                )

    files["data/site/settings.json"] = content_service.public_settings(session)
    files["data/site/features.json"] = content_service.feature_flags(session)
    files["data/site/sitemap.json"] = content_service.sitemap_urls(session, origin="")
    files["data/site/series.json"] = [
        content_service.series_payload(spec, entry)
        for locale in locales
        for spec, entry in content_repo.list_series(session, active_only=True, locale=locale)
    ]
    files["data/site/schedule.json"] = _schedule_payload(session)
    files["data/site/products.json"] = _product_payload(session)
    files["data/site/characters.json"] = _character_payload(session)
    files["data/site/faq.json"] = [
        _entry_data(session, entry)
        for entry in content_repo.list_published(session, kind="faq", limit=PER_KIND_LIMIT)[0]
    ]
    return {"files": files, "routes": route_index}


def _schedule_payload(session: Session) -> Any:
    try:
        from app.modules.commissions import service as commission_service
    except ImportError:  # pragma: no cover - 目录模块尚未交付
        return None
    try:
        return commission_service.public_schedule_summary(session)
    except Exception as exc:  # noqa: BLE001 - 缺数据不阻塞预渲染
        logger.warning("档期摘要暂不可用：%s", type(exc).__name__)
        return None


def _product_payload(session: Session) -> list[dict[str, Any]]:
    try:
        from app.modules.catalog import repository as catalog_repo
        from app.modules.catalog import service as catalog_service
    except ImportError:  # pragma: no cover - 目录模块尚未交付
        return []
    try:
        products, _total = catalog_service.list_products(session, limit=100)
        items: list[dict[str, Any]] = []
        for product in products:
            item = catalog_service.product_payload(session, product, include_skus=True)
            skus = catalog_repo.list_skus(session, product_id=product.id, active_only=True)
            prices = [sku.price_cents for sku in skus]
            item["price_from_cents"] = min(prices) if prices else None
            items.append(item)
        return items
    except Exception as exc:  # noqa: BLE001 - 缺数据不阻塞预渲染
        logger.warning("商品数据暂不可用：%s", type(exc).__name__)
        return []


def _character_payload(session: Session) -> list[dict[str, Any]]:
    try:
        from app.modules.catalog import repository as catalog_repo
    except ImportError:  # pragma: no cover - 目录模块尚未交付
        return []
    try:
        rows, _total = catalog_repo.list_characters(session, limit=100)
    except Exception as exc:  # noqa: BLE001 - 缺数据不阻塞预渲染
        logger.warning("角色数据暂不可用：%s", type(exc).__name__)
        return []
    return [
        {
            "id": row.id,
            "slug": row.slug,
            "name": row.name,
            "species": row.species,
            "public_status": row.public_status,
            "exclusive": bool(row.exclusive),
        }
        for row in rows
    ]


def _rename_directory(source: Path, target: Path) -> None:
    """Windows 索引/防病毒短暂占用时有限重试；持续权限错误仍交给任务失败处理。"""
    for attempt in range(4):
        try:
            source.rename(target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 3:
                raise
            time.sleep(0.05 * (2 ** attempt))


def _write_dataset(
    dataset: dict[str, Any],
    *,
    output_dir: Path,
    snapshot_id: str,
    generated_at: str,
    settings: Settings,
    routes: list[str],
) -> dict[str, Any]:
    """把数据集写进临时目录并原子改名；失败保留上一版本。"""
    staging = output_dir.parent / f".{output_dir.name}.staging-{snapshot_id[-8:]}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for relative, payload in dataset["files"].items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        for item in dataset["routes"]:
            html_path = staging / str(item["html_file"])
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(
                HTML_SHELL.format(
                    locale=item["locale"],
                    title=item["title"],
                    description=(item["description"] or "")[:300],
                    canonical=item["route"],
                    data_file=_relative_from_html(str(item["html_file"]), str(item["data_file"])),
                ),
                encoding="utf-8",
            )

        manifest = {
            "format": PREBUILD_FORMAT,
            "snapshot_id": snapshot_id,
            "generated_at": generated_at,
            "public_origin": settings.public_origin,
            "site_name": settings.site_name,
            "api_prefix": settings.api_prefix,
            "counts": {
                "routes": len(dataset["routes"]),
                "files": len(dataset["files"]),
            },
            "routes": dataset["routes"],
            "route_list": routes,
            "dataset_digest": _digest(dataset["files"]),
            "notes": [
                "本产物只含已发布公开内容的数据与路由清单，不含前端资源包。",
                "真实静态构建由部署脚本用这些数据执行；构建失败时保留上一版本入口。",
                "产物不含密钥、后台数据、他人订单或附件签名 URL。",
            ],
        }
        # manifest 最后写：它存在就代表本次数据完整可消费。
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        previous = output_dir.parent / f".{output_dir.name}.previous-{snapshot_id[-8:]}"
        if output_dir.exists():
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
            _rename_directory(output_dir, previous)
        try:
            _rename_directory(staging, output_dir)
        except Exception:
            # 旧目录已移走时必须恢复正式路径。恢复也失败则保留 previous，
            # 交由失败任务与人工恢复处理，不能删除最后一份可用快照。
            if previous.exists() and not output_dir.exists():
                _rename_directory(previous, output_dir)
            raise
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _relative_from_html(html_file: str, data_file: str) -> str:
    depth = len(Path(html_file).parts) - 1
    return ("../" * depth) + data_file


def run_prebuild(
    session: Session,
    *,
    output_dir: Path | str | None = None,
    actor_id: str | None = None,
    trigger: str = "manual",
    job_id: str | None = None,
    locales: tuple[str, ...] = DEFAULT_LOCALES,
) -> dict[str, Any]:
    """执行一次受控预渲染：产出静态数据与路由清单到 ``output_dir``。

    失败时把 ``prebuild_jobs`` 标记为 FAILED 并保留上一版本产物，不抛裸异常给调用方
    之外的写入路径（脚本会拿到 ``status`` 与 ``error`` 字段用于发布决策）。
    """
    settings = get_settings()
    target = Path(output_dir) if output_dir else (settings.data_dir / "prebuild")
    target.parent.mkdir(parents=True, exist_ok=True)

    job = session.get(PrebuildJob, job_id) if job_id else None
    if job is None:
        job = content_repo.enqueue_prebuild(
            session,
            trigger=trigger,
            resource_type="site",
            resource_id=None,
            routes=[],
            actor_id=actor_id,
        )
    job.status = "RUNNING"
    job.started_at = now_ms()
    job.error = None
    # 先提交任务行：失败回滚时不会把「任务已开始」这件事一起回滚掉，
    # 否则失败记录会消失，运维看不到曾经失败过。
    session.commit()
    job_row_id = job.id

    snapshot_id = new_id("job")
    generated_at = iso(now_ms()) or ""
    try:
        dataset = _collect_dataset(session, locales=locales)
        routes = sorted({item["route"] for item in dataset["routes"]})
        manifest = _write_dataset(
            dataset,
            output_dir=target,
            snapshot_id=snapshot_id,
            generated_at=generated_at,
            settings=settings,
            routes=routes,
        )
    except Exception as exc:  # noqa: BLE001 - 失败必须留痕并保留上一版本
        session.rollback()
        failed = session.get(PrebuildJob, job_row_id)
        if failed is not None:
            failed.status = "FAILED"
            failed.finished_at = now_ms()
            failed.error = f"{type(exc).__name__}: {exc}"[:300]
        audit.record(
            session,
            actor_type="staff" if actor_id else "system",
            actor_id=actor_id,
            action="prebuild.failed",
            resource_type="prebuild_job",
            resource_id=job_row_id,
            reason=f"{type(exc).__name__}: {exc}"[:200],
        )
        session.commit()
        logger.error("预渲染失败（保留上一版本）：%s", type(exc).__name__)
        return {
            "status": "FAILED",
            "job_id": job_row_id,
            "output_dir": str(target),
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "routes": [],
        }

    job = session.get(PrebuildJob, job_row_id) or job
    job.status = "DONE"
    job.routes = routes
    job.finished_at = now_ms()
    job.error = None
    audit.record(
        session,
        actor_type="staff" if actor_id else "system",
        actor_id=actor_id,
        action="prebuild.done",
        resource_type="prebuild_job",
        resource_id=job_row_id,
        after={"routes": len(routes), "snapshot_id": snapshot_id},
    )
    session.commit()
    return {
        "status": "DONE",
        "job_id": job_row_id,
        "snapshot_id": snapshot_id,
        "generated_at": generated_at,
        "output_dir": str(target),
        "routes": routes,
        "counts": manifest["counts"],
        "dataset_digest": manifest["dataset_digest"],
        "error": None,
    }


__all__ = ["PREBUILD_FORMAT", "prebuild_routes", "run_prebuild"]
