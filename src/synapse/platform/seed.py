"""Create schema and load a generated Dataset into Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from synapse.auth.passwords import DEFAULT_DEMO_PASSWORD, hash_password
from synapse.domain.dataset import Dataset
from synapse.platform.schema import (
    ActivityRow,
    AgentCheckpointRow,
    AgentRunRow,
    AuthCredentialRow,
    Base,
    BlockerRow,
    CommentRow,
    DecisionRow,
    DependencyRow,
    DocumentRow,
    EmailRow,
    IncidentRow,
    IssueRow,
    MeetingRow,
    MembershipRow,
    MemoryEntryRow,
    MilestoneRow,
    ProjectRow,
    RiskRow,
    TaskRow,
    TeamRow,
    TenantRow,
    UserRow,
)
from synapse.rag.store import ChunkRow

_ORDER = (
    MemoryEntryRow,
    AgentCheckpointRow,
    AgentRunRow,
    ChunkRow,
    AuthCredentialRow,
    ActivityRow,
    DocumentRow,
    DependencyRow,
    IncidentRow,
    BlockerRow,
    RiskRow,
    DecisionRow,
    MeetingRow,
    EmailRow,
    CommentRow,
    IssueRow,
    TaskRow,
    MilestoneRow,
    MembershipRow,
    ProjectRow,
    UserRow,
    TeamRow,
    TenantRow,
)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "ALTER TABLE document_chunks "
                "ADD COLUMN IF NOT EXISTS contradicts_live_status BOOLEAN DEFAULT FALSE"
            )
        )


async def drop_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def wipe_enterprise(session: AsyncSession) -> None:
    for table in _ORDER:
        await session.execute(delete(table))


async def load_dataset(
    session: AsyncSession, dataset: Dataset, *, password: str = DEFAULT_DEMO_PASSWORD
) -> dict[str, int]:
    await wipe_enterprise(session)
    await session.flush()

    session.add_all(
        [
            TenantRow(id=t.id, slug=t.slug, name=t.name, created_at=t.created_at)
            for t in dataset.tenants
        ]
    )
    await session.flush()
    session.add_all(
        [
            TeamRow(
                id=t.id,
                tenant_id=t.tenant_id,
                name=t.name,
                lead_user_id=t.lead_user_id,
                created_at=t.created_at,
            )
            for t in dataset.teams
        ]
    )
    await session.flush()
    session.add_all(
        [
            UserRow(
                id=u.id,
                tenant_id=u.tenant_id,
                email=u.email,
                display_name=u.display_name,
                role=str(u.role),
                team_id=u.team_id,
                is_active=u.is_active,
                created_at=u.created_at,
            )
            for u in dataset.users
        ]
    )
    await session.flush()
    session.add_all(
        [
            ProjectRow(
                id=p.id,
                tenant_id=p.tenant_id,
                key=p.key,
                name=p.name,
                description=p.description,
                status=str(p.status),
                priority=str(p.priority),
                owner_user_id=p.owner_user_id,
                team_id=p.team_id,
                start_date=p.start_date,
                target_date=p.target_date,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in dataset.projects
        ]
    )
    await session.flush()
    session.add_all(
        [
            MembershipRow(
                id=m.id,
                tenant_id=m.tenant_id,
                project_id=m.project_id,
                user_id=m.user_id,
                access_level=str(m.access_level),
            )
            for m in dataset.memberships
        ]
    )
    session.add_all(
        [
            MilestoneRow(
                id=m.id,
                tenant_id=m.tenant_id,
                project_id=m.project_id,
                name=m.name,
                due_date=m.due_date,
                status=str(m.status),
                created_at=m.created_at,
            )
            for m in dataset.milestones
        ]
    )
    await session.flush()
    session.add_all(
        [
            TaskRow(
                id=t.id,
                tenant_id=t.tenant_id,
                project_id=t.project_id,
                milestone_id=t.milestone_id,
                key=t.key,
                title=t.title,
                description=t.description,
                status=str(t.status),
                priority=str(t.priority),
                assignee_id=t.assignee_id,
                reporter_id=t.reporter_id,
                due_date=t.due_date,
                completed_at=t.completed_at,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in dataset.tasks
        ]
    )
    session.add_all(
        [
            IssueRow(
                id=i.id,
                tenant_id=i.tenant_id,
                project_id=i.project_id,
                key=i.key,
                issue_type=str(i.issue_type),
                title=i.title,
                description=i.description,
                status=str(i.status),
                priority=str(i.priority),
                assignee_id=i.assignee_id,
                reporter_id=i.reporter_id,
                created_at=i.created_at,
                updated_at=i.updated_at,
            )
            for i in dataset.issues
        ]
    )
    await session.flush()
    session.add_all(
        [
            CommentRow(
                id=c.id,
                tenant_id=c.tenant_id,
                parent_type=str(c.parent_type),
                parent_id=c.parent_id,
                author_id=c.author_id,
                body=c.body,
                is_untrusted_instruction=c.is_untrusted_instruction,
                created_at=c.created_at,
            )
            for c in dataset.comments
        ]
    )
    session.add_all(
        [
            EmailRow(
                id=e.id,
                tenant_id=e.tenant_id,
                project_id=e.project_id,
                sender_id=e.sender_id,
                recipient_ids=list(e.recipient_ids),
                subject=e.subject,
                body=e.body,
                sent_at=e.sent_at,
                created_at=e.created_at,
            )
            for e in dataset.emails
        ]
    )
    session.add_all(
        [
            MeetingRow(
                id=m.id,
                tenant_id=m.tenant_id,
                project_id=m.project_id,
                title=m.title,
                started_at=m.started_at,
                attendee_ids=list(m.attendee_ids),
                notes=m.notes,
                created_at=m.created_at,
            )
            for m in dataset.meetings
        ]
    )
    session.add_all(
        [
            DecisionRow(
                id=d.id,
                tenant_id=d.tenant_id,
                project_id=d.project_id,
                title=d.title,
                body=d.body,
                decided_at=d.decided_at,
                decided_by=d.decided_by,
                status=str(d.status),
                created_at=d.created_at,
            )
            for d in dataset.decisions
        ]
    )
    session.add_all(
        [
            RiskRow(
                id=r.id,
                tenant_id=r.tenant_id,
                project_id=r.project_id,
                title=r.title,
                description=r.description,
                severity=str(r.severity),
                status=str(r.status),
                owner_id=r.owner_id,
                identified_at=r.identified_at,
                created_at=r.created_at,
            )
            for r in dataset.risks
        ]
    )
    session.add_all(
        [
            BlockerRow(
                id=b.id,
                tenant_id=b.tenant_id,
                project_id=b.project_id,
                task_id=b.task_id,
                title=b.title,
                description=b.description,
                status=str(b.status),
                opened_at=b.opened_at,
                resolved_at=b.resolved_at,
                created_at=b.created_at,
            )
            for b in dataset.blockers
        ]
    )
    session.add_all(
        [
            IncidentRow(
                id=i.id,
                tenant_id=i.tenant_id,
                project_id=i.project_id,
                title=i.title,
                description=i.description,
                severity=str(i.severity),
                status=str(i.status),
                opened_at=i.opened_at,
                resolved_at=i.resolved_at,
                created_at=i.created_at,
            )
            for i in dataset.incidents
        ]
    )
    session.add_all(
        [
            DependencyRow(
                id=d.id,
                tenant_id=d.tenant_id,
                from_project_id=d.from_project_id,
                to_project_id=d.to_project_id,
                description=d.description,
                status=str(d.status),
                created_at=d.created_at,
            )
            for d in dataset.dependencies
        ]
    )
    session.add_all(
        [
            DocumentRow(
                id=d.id,
                tenant_id=d.tenant_id,
                project_id=d.project_id,
                title=d.title,
                doc_type=str(d.doc_type),
                access_level=str(d.access_level),
                version=d.version,
                authored_at=d.authored_at,
                is_current=d.is_current,
                body=d.body,
                contradicts_live_status=d.contradicts_live_status,
                created_at=d.created_at,
            )
            for d in dataset.documents
        ]
    )
    session.add_all(
        [
            ActivityRow(
                id=a.id,
                tenant_id=a.tenant_id,
                project_id=a.project_id,
                actor_id=a.actor_id,
                event_type=str(a.event_type),
                entity_type=a.entity_type,
                entity_id=a.entity_id,
                occurred_at=a.occurred_at,
                summary=a.summary,
                payload=a.payload,
                created_at=a.created_at,
            )
            for a in dataset.activities
        ]
    )
    await _seed_credentials(session, dataset, password=password)
    await session.commit()
    return dataset.counts()


async def _seed_credentials(session: AsyncSession, dataset: Dataset, *, password: str) -> None:
    """One shared demo password per seed. Real IdP is out of scope; JWT boundary is not."""
    hashed = hash_password(password)
    now = datetime.now(UTC)
    session.add_all(
        [
            AuthCredentialRow(user_id=u.id, password_hash=hashed, updated_at=now)
            for u in dataset.users
        ]
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
