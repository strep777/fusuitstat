"""内容模块查询。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.content.models import (
    ContentEntry,
    ContentRevision,
    PrebuildJob,
    SeriesSpec,
    SiteSetting,
)
from app.platform.ids import new_id, now_ms


def get_entry(session: Session, entry_id: str) -> ContentEntry | None:
    return session.get(ContentEntry, entry_id)


def get_published(
    session: Session, *, kind: str, slug: str, locale: str = "zh-CN", moment: int | None = None
) -> ContentEntry | None:
    now = moment if moment is not None else now_ms()
    return session.scalars(
        sa.select(ContentEntry).where(
            ContentEntry.kind == kind,
            ContentEntry.slug == slug,
            ContentEntry.locale == locale,
            ContentEntry.status == "published",
            sa.or_(ContentEntry.publish_at.is_(None), ContentEntry.publish_at <= now),
            sa.or_(ContentEntry.unpublish_at.is_(None), ContentEntry.unpublish_at > now),
        )
    ).one_or_none()


def list_published(
    session: Session,
    *,
    kind: str,
    locale: str = "zh-CN",
    species: str | None = None,
    work_type: str | None = None,
    series: str | None = None,
    query: str | None = None,
    featured_only: bool = False,
    limit: int = 20,
    offset: int = 0,
    moment: int | None = None,
) -> tuple[list[ContentEntry], int]:
    now = moment if moment is not None else now_ms()
    conditions = [
        ContentEntry.kind == kind,
        ContentEntry.locale == locale,
        ContentEntry.status == "published",
        sa.or_(ContentEntry.publish_at.is_(None), ContentEntry.publish_at <= now),
        sa.or_(ContentEntry.unpublish_at.is_(None), ContentEntry.unpublish_at > now),
    ]
    if kind == "work":
        conditions.append(ContentEntry.display_authorized == 1)
    if species:
        conditions.append(ContentEntry.species == species)
    if work_type:
        conditions.append(ContentEntry.work_type == work_type)
    if series:
        conditions.append(ContentEntry.tags.contains([series]))
    if featured_only:
        conditions.append(ContentEntry.is_featured == 1)
    if query:
        like = f"%{query}%"
        conditions.append(
            sa.or_(ContentEntry.title.like(like), ContentEntry.summary.like(like))
        )
    count = int(
        session.scalar(sa.select(sa.func.count()).select_from(ContentEntry).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(ContentEntry)
            .where(*conditions)
            .order_by(ContentEntry.sort_order, ContentEntry.publish_at.desc(), ContentEntry.created_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, count


def list_admin(
    session: Session,
    *,
    kind: str | None = None,
    status: str | None = None,
    locale: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ContentEntry], int]:
    conditions = []
    if kind:
        conditions.append(ContentEntry.kind == kind)
    if status:
        conditions.append(ContentEntry.status == status)
    if locale:
        conditions.append(ContentEntry.locale == locale)
    if query:
        like = f"%{query}%"
        conditions.append(sa.or_(ContentEntry.title.like(like), ContentEntry.slug.like(like)))
    count = int(
        session.scalar(sa.select(sa.func.count()).select_from(ContentEntry).where(*conditions)) or 0
    )
    rows = list(
        session.scalars(
            sa.select(ContentEntry)
            .where(*conditions)
            .order_by(ContentEntry.updated_at.desc())
            .limit(min(limit, 100))
            .offset(offset)
        )
    )
    return rows, count


def next_revision(session: Session, *, entry_id: str) -> int:
    current = session.scalar(
        sa.select(sa.func.max(ContentRevision.revision)).where(ContentRevision.entry_id == entry_id)
    )
    return int(current or 0) + 1


def list_revisions(session: Session, *, entry_id: str, limit: int = 20) -> list[ContentRevision]:
    return list(
        session.scalars(
            sa.select(ContentRevision)
            .where(ContentRevision.entry_id == entry_id)
            .order_by(ContentRevision.revision.desc())
            .limit(min(limit, 50))
        )
    )


def get_series_by_code(session: Session, code: str) -> SeriesSpec | None:
    return session.scalars(sa.select(SeriesSpec).where(SeriesSpec.code == code)).one_or_none()


def get_series(session: Session, series_id: str) -> SeriesSpec | None:
    return session.get(SeriesSpec, series_id)


def get_series_for_entry(session: Session, entry_id: str) -> SeriesSpec | None:
    return session.scalars(sa.select(SeriesSpec).where(SeriesSpec.entry_id == entry_id)).one_or_none()


def list_series(session: Session, *, active_only: bool = True) -> list[tuple[SeriesSpec, ContentEntry]]:
    stmt = sa.select(SeriesSpec, ContentEntry).join(ContentEntry, ContentEntry.id == SeriesSpec.entry_id)
    if active_only:
        stmt = stmt.where(SeriesSpec.is_active == 1)
    stmt = stmt.order_by(SeriesSpec.display_order, ContentEntry.sort_order)
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def get_setting(session: Session, key: str) -> SiteSetting | None:
    return session.scalars(sa.select(SiteSetting).where(SiteSetting.key == key)).one_or_none()


def list_settings(session: Session, *, public_only: bool = False, group: str | None = None) -> list[SiteSetting]:
    stmt = sa.select(SiteSetting)
    if public_only:
        stmt = stmt.where(SiteSetting.is_public == 1)
    if group:
        stmt = stmt.where(SiteSetting.group == group)
    return list(session.scalars(stmt.order_by(SiteSetting.group, SiteSetting.key)))


def list_sitemap_entries(session: Session, *, moment: int | None = None, limit: int = 5000) -> list[ContentEntry]:
    now = moment if moment is not None else now_ms()
    return list(
        session.scalars(
            sa.select(ContentEntry)
            .where(
                ContentEntry.status == "published",
                ContentEntry.locale == "zh-CN",
                sa.or_(ContentEntry.publish_at.is_(None), ContentEntry.publish_at <= now),
            )
            .order_by(ContentEntry.updated_at.desc())
            .limit(limit)
        )
    )


def enqueue_prebuild(
    session: Session, *, trigger: str, resource_type: str, resource_id: str | None, routes: list[str],
    actor_id: str | None = None,
) -> PrebuildJob:
    job = PrebuildJob(
        id=new_id("job"),
        trigger=trigger,
        resource_type=resource_type,
        resource_id=resource_id,
        routes=routes,
        created_by=actor_id,
    )
    session.add(job)
    session.flush()
    return job


def list_prebuild_jobs(session: Session, *, limit: int = 20) -> list[PrebuildJob]:
    return list(session.scalars(sa.select(PrebuildJob).order_by(PrebuildJob.created_at.desc()).limit(limit)))


def species_facets(session: Session, *, kind: str = "work", locale: str = "zh-CN") -> list[tuple[str, int]]:
    rows = session.execute(
        sa.select(ContentEntry.species, sa.func.count())
        .where(
            ContentEntry.kind == kind,
            ContentEntry.locale == locale,
            ContentEntry.status == "published",
            ContentEntry.species.is_not(None),
        )
        .group_by(ContentEntry.species)
        .order_by(sa.func.count().desc())
    ).all()
    return [(str(row[0]), int(row[1])) for row in rows if row[0]]


def work_type_facets(session: Session, *, locale: str = "zh-CN") -> list[tuple[str, int]]:
    rows = session.execute(
        sa.select(ContentEntry.work_type, sa.func.count())
        .where(
            ContentEntry.kind == "work",
            ContentEntry.locale == locale,
            ContentEntry.status == "published",
            ContentEntry.work_type.is_not(None),
        )
        .group_by(ContentEntry.work_type)
        .order_by(sa.func.count().desc())
    ).all()
    return [(str(row[0]), int(row[1])) for row in rows if row[0]]
