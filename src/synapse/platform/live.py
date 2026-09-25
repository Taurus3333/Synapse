"""Tenant-scoped live reads/writes. Tenant comes from the request context, never the body."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.platform.schema import (
    ActivityRow,
    BlockerRow,
    EmailRow,
    MeetingRow,
    MembershipRow,
    ProjectRow,
    RiskRow,
    TaskRow,
)


async def get_project(session: AsyncSession, tenant_id: str, key: str) -> ProjectRow | None:
    result = await session.execute(
        select(ProjectRow).where(ProjectRow.tenant_id == tenant_id, ProjectRow.key == key)
    )
    return result.scalar_one_or_none()


async def list_projects(session: AsyncSession, tenant_id: str) -> list[ProjectRow]:
    result = await session.execute(
        select(ProjectRow).where(ProjectRow.tenant_id == tenant_id).order_by(ProjectRow.key)
    )
    return list(result.scalars())


async def user_on_project(
    session: AsyncSession, tenant_id: str, project_id: str, user_id: str
) -> bool:
    result = await session.execute(
        select(MembershipRow.id).where(
            MembershipRow.tenant_id == tenant_id,
            MembershipRow.project_id == project_id,
            MembershipRow.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def list_tasks(
    session: AsyncSession, tenant_id: str, project_id: str, *, limit: int = 100
) -> list[TaskRow]:
    result = await session.execute(
        select(TaskRow)
        .where(TaskRow.tenant_id == tenant_id, TaskRow.project_id == project_id)
        .order_by(TaskRow.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def list_risks(
    session: AsyncSession, tenant_id: str, project_id: str, *, limit: int = 100
) -> list[RiskRow]:
    result = await session.execute(
        select(RiskRow)
        .where(RiskRow.tenant_id == tenant_id, RiskRow.project_id == project_id)
        .order_by(RiskRow.identified_at.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def list_blockers(
    session: AsyncSession, tenant_id: str, project_id: str, *, limit: int = 100
) -> list[BlockerRow]:
    result = await session.execute(
        select(BlockerRow)
        .where(BlockerRow.tenant_id == tenant_id, BlockerRow.project_id == project_id)
        .order_by(BlockerRow.opened_at.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def list_activity(
    session: AsyncSession,
    tenant_id: str,
    project_id: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
) -> list[ActivityRow]:
    stmt = select(ActivityRow).where(
        ActivityRow.tenant_id == tenant_id, ActivityRow.project_id == project_id
    )
    if since is not None:
        stmt = stmt.where(ActivityRow.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(ActivityRow.occurred_at < until)
    stmt = stmt.order_by(ActivityRow.occurred_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars())


def _match_query(text: str, query: str) -> bool:
    tokens = [t.lower() for t in query.split() if len(t) > 2]
    if not tokens:
        return True
    hay = text.lower()
    return any(t in hay for t in tokens)


async def search_emails(
    session: AsyncSession,
    tenant_id: str,
    project_id: str,
    *,
    query: str,
    limit: int = 10,
) -> list[EmailRow]:
    result = await session.execute(
        select(EmailRow)
        .where(EmailRow.tenant_id == tenant_id, EmailRow.project_id == project_id)
        .order_by(EmailRow.sent_at.desc())
        .limit(max(limit * 5, 25))
    )
    rows = list(result.scalars())
    matched = [r for r in rows if _match_query(f"{r.subject}\n{r.body}", query)]
    return matched[:limit]


async def search_meetings(
    session: AsyncSession,
    tenant_id: str,
    project_id: str,
    *,
    query: str,
    limit: int = 10,
) -> list[MeetingRow]:
    result = await session.execute(
        select(MeetingRow)
        .where(MeetingRow.tenant_id == tenant_id, MeetingRow.project_id == project_id)
        .order_by(MeetingRow.started_at.desc())
        .limit(max(limit * 5, 25))
    )
    rows = list(result.scalars())
    matched = [r for r in rows if _match_query(f"{r.title}\n{r.notes}", query)]
    return matched[:limit]


async def update_project_status(
    session: AsyncSession, tenant_id: str, key: str, status: str, actor_id: str | None
) -> ProjectRow | None:
    project = await get_project(session, tenant_id, key)
    if project is None:
        return None
    before = project.status
    now = datetime.now(UTC)
    project.status = status
    project.updated_at = now
    session.add(
        ActivityRow(
            id=f"act_live_{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            project_id=project.id,
            actor_id=actor_id,
            event_type="status_change",
            entity_type="project",
            entity_id=project.id,
            occurred_at=now,
            summary=f"{project.key} status {before} → {status}",
            payload={"from": before, "to": status},
            created_at=now,
        )
    )
    await session.commit()
    await session.refresh(project)
    return project


async def create_risk(
    session: AsyncSession,
    tenant_id: str,
    project_key: str,
    *,
    title: str,
    description: str,
    severity: str,
    actor_id: str | None,
) -> RiskRow | None:
    project = await get_project(session, tenant_id, project_key)
    if project is None:
        return None
    now = datetime.now(UTC)
    risk = RiskRow(
        id=f"rsk_live_{uuid4().hex[:12]}",
        tenant_id=tenant_id,
        project_id=project.id,
        title=title,
        description=description,
        severity=severity,
        status="open",
        owner_id=actor_id,
        identified_at=now,
        created_at=now,
    )
    session.add(risk)
    session.add(
        ActivityRow(
            id=f"act_live_{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            project_id=project.id,
            actor_id=actor_id,
            event_type="risk_reported",
            entity_type="risk",
            entity_id=risk.id,
            occurred_at=now,
            summary=title,
            payload={},
            created_at=now,
        )
    )
    await session.commit()
    await session.refresh(risk)
    return risk


async def create_blocker(
    session: AsyncSession,
    tenant_id: str,
    project_key: str,
    *,
    title: str,
    description: str,
    actor_id: str | None,
) -> BlockerRow | None:
    project = await get_project(session, tenant_id, project_key)
    if project is None:
        return None
    now = datetime.now(UTC)
    blocker = BlockerRow(
        id=f"blk_live_{uuid4().hex[:12]}",
        tenant_id=tenant_id,
        project_id=project.id,
        task_id=None,
        title=title,
        description=description,
        status="open",
        opened_at=now,
        resolved_at=None,
        created_at=now,
    )
    session.add(blocker)
    session.add(
        ActivityRow(
            id=f"act_live_{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            project_id=project.id,
            actor_id=actor_id,
            event_type="blocker_opened",
            entity_type="blocker",
            entity_id=blocker.id,
            occurred_at=now,
            summary=title,
            payload={},
            created_at=now,
        )
    )
    await session.commit()
    await session.refresh(blocker)
    return blocker


async def update_task_status(
    session: AsyncSession,
    tenant_id: str,
    task_id: str,
    status: str,
    actor_id: str | None,
) -> TaskRow | None:
    result = await session.execute(
        select(TaskRow).where(TaskRow.tenant_id == tenant_id, TaskRow.id == task_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        return None
    before = task.status
    now = datetime.now(UTC)
    task.status = status
    task.updated_at = now
    if status == "done":
        task.completed_at = now
    session.add(
        ActivityRow(
            id=f"act_live_{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            project_id=task.project_id,
            actor_id=actor_id,
            event_type="status_change",
            entity_type="task",
            entity_id=task.id,
            occurred_at=now,
            summary=f"{task.key} {before} → {status}",
            payload={"from": before, "to": status},
            created_at=now,
        )
    )
    await session.commit()
    await session.refresh(task)
    return task
