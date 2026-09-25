from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from synapse.domain.models import (
    ActivityEvent,
    Blocker,
    Comment,
    Decision,
    Dependency,
    Document,
    Email,
    Incident,
    Issue,
    Meeting,
    Milestone,
    Project,
    ProjectMembership,
    Risk,
    Task,
    Team,
    Tenant,
    User,
)

_COLLECTIONS = (
    "tenants",
    "teams",
    "users",
    "projects",
    "memberships",
    "milestones",
    "tasks",
    "issues",
    "comments",
    "emails",
    "meetings",
    "decisions",
    "risks",
    "blockers",
    "incidents",
    "dependencies",
    "documents",
    "activities",
)


class Dataset(BaseModel):
    """In-memory enterprise corpus. Persistence is Chunk 3."""

    model_config = ConfigDict(extra="forbid")

    seed: int
    profile: str
    as_of: str
    tenants: list[Tenant]
    teams: list[Team]
    users: list[User]
    projects: list[Project]
    memberships: list[ProjectMembership]
    milestones: list[Milestone]
    tasks: list[Task]
    issues: list[Issue]
    comments: list[Comment]
    emails: list[Email]
    meetings: list[Meeting]
    decisions: list[Decision]
    risks: list[Risk]
    blockers: list[Blocker]
    incidents: list[Incident]
    dependencies: list[Dependency]
    documents: list[Document]
    activities: list[ActivityEvent]

    def counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in _COLLECTIONS}

    def total_records(self) -> int:
        return sum(self.counts().values())

    def project_by_key(self, tenant_id: str, key: str) -> Project:
        for project in self.projects:
            if project.tenant_id == tenant_id and project.key == key:
                return project
        raise KeyError(f"project {key} not in tenant {tenant_id}")

    def tenant_by_slug(self, slug: str) -> Tenant:
        for tenant in self.tenants:
            if tenant.slug == slug:
                return tenant
        raise KeyError(slug)

    def fingerprint(self) -> str:
        """Stable identity of the generated corpus: seed, profile, and every id."""
        ids: list[str] = [f"{self.seed}:{self.profile}:{self.as_of}"]
        for name in _COLLECTIONS:
            for item in getattr(self, name):
                ids.append(f"{name}:{item.id}")
        return "\n".join(ids)

    def to_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def assert_referential_integrity(dataset: Dataset) -> None:
    tenant_ids = {t.id for t in dataset.tenants}
    user_ids = {u.id for u in dataset.users}
    team_ids = {t.id for t in dataset.teams}
    project_ids = {p.id for p in dataset.projects}
    task_ids = {t.id for t in dataset.tasks}

    def _tenant(entity: Any, field: str = "tenant_id") -> None:
        value = getattr(entity, field)
        if value not in tenant_ids:
            raise AssertionError(f"{type(entity).__name__}.{field}={value} missing")

    for team in dataset.teams:
        _tenant(team)
        if team.lead_user_id is not None and team.lead_user_id not in user_ids:
            raise AssertionError(f"team lead {team.lead_user_id} missing")
    for user in dataset.users:
        _tenant(user)
        if user.team_id is not None and user.team_id not in team_ids:
            raise AssertionError(f"user team {user.team_id} missing")
    for project in dataset.projects:
        _tenant(project)
        if project.owner_user_id not in user_ids or project.team_id not in team_ids:
            raise AssertionError(f"project {project.id} has dangling owner/team")
    for membership in dataset.memberships:
        _tenant(membership)
        if membership.project_id not in project_ids or membership.user_id not in user_ids:
            raise AssertionError(f"membership {membership.id} dangling")
    for task in dataset.tasks:
        _tenant(task)
        if task.project_id not in project_ids:
            raise AssertionError(f"task {task.id} project missing")
        if task.assignee_id is not None and task.assignee_id not in user_ids:
            raise AssertionError(f"task {task.id} assignee missing")
    for issue in dataset.issues:
        _tenant(issue)
        if issue.project_id not in project_ids:
            raise AssertionError(f"issue {issue.id} project missing")
    for email in dataset.emails:
        _tenant(email)
        if email.project_id is not None and email.project_id not in project_ids:
            raise AssertionError(f"email {email.id} project missing")
    for blocker in dataset.blockers:
        _tenant(blocker)
        if blocker.task_id is not None and blocker.task_id not in task_ids:
            raise AssertionError(f"blocker {blocker.id} task missing")
    for dep in dataset.dependencies:
        _tenant(dep)
        if dep.from_project_id not in project_ids or dep.to_project_id not in project_ids:
            raise AssertionError(f"dependency {dep.id} project missing")
        if dep.from_project_id == dep.to_project_id:
            raise AssertionError(f"dependency {dep.id} is self-referential")

    _assert_tenant_partition(dataset)


def _assert_tenant_partition(dataset: Dataset) -> None:
    """No identifier is reused across tenants; FKs stay inside one tenant."""
    seen: dict[str, str] = {}
    projects: Mapping[str, Project] = {p.id: p for p in dataset.projects}
    users: Mapping[str, User] = {u.id: u for u in dataset.users}
    tasks: Mapping[str, Task] = {t.id: t for t in dataset.tasks}

    for name, items in dataset.counts().items():
        del items
        for entity in getattr(dataset, name):
            if entity.id in seen and seen[entity.id] != getattr(entity, "tenant_id", ""):
                raise AssertionError(f"id {entity.id} reused across tenants")
            if hasattr(entity, "tenant_id"):
                seen[entity.id] = entity.tenant_id

    for task in dataset.tasks:
        if projects[task.project_id].tenant_id != task.tenant_id:
            raise AssertionError(f"task {task.id} crosses tenants")
        if task.assignee_id and users[task.assignee_id].tenant_id != task.tenant_id:
            raise AssertionError(f"task {task.id} assigned across tenants")
    for membership in dataset.memberships:
        if (
            projects[membership.project_id].tenant_id != membership.tenant_id
            or users[membership.user_id].tenant_id != membership.tenant_id
        ):
            raise AssertionError(f"membership {membership.id} crosses tenants")
    for blocker in dataset.blockers:
        if blocker.task_id and tasks[blocker.task_id].tenant_id != blocker.tenant_id:
            raise AssertionError(f"blocker {blocker.id} crosses tenants")
