from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from synapse.domain.enums import (
    AccessLevel,
    ActivityType,
    BlockerStatus,
    DecisionStatus,
    DocumentType,
    IncidentSeverity,
    IncidentStatus,
    IssueStatus,
    IssueType,
    ParentType,
    Priority,
    ProjectStatus,
    RiskStatus,
    TaskStatus,
    UserRole,
)


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    tenant_id: str
    created_at: datetime


class Tenant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    slug: str
    name: str
    created_at: datetime


class Team(Entity):
    name: str
    lead_user_id: str | None = None


class User(Entity):
    email: str
    display_name: str
    role: UserRole
    team_id: str | None = None
    is_active: bool = True


class Project(Entity):
    key: str
    name: str
    description: str
    status: ProjectStatus
    priority: Priority
    owner_user_id: str
    team_id: str
    start_date: date
    target_date: date
    updated_at: datetime


class ProjectMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    tenant_id: str
    project_id: str
    user_id: str
    access_level: AccessLevel


class Milestone(Entity):
    project_id: str
    name: str
    due_date: date
    status: TaskStatus


class Task(Entity):
    project_id: str
    milestone_id: str | None = None
    key: str
    title: str
    description: str
    status: TaskStatus
    priority: Priority
    assignee_id: str | None = None
    reporter_id: str
    due_date: date | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class Issue(Entity):
    project_id: str
    key: str
    issue_type: IssueType
    title: str
    description: str
    status: IssueStatus
    priority: Priority
    assignee_id: str | None = None
    reporter_id: str
    updated_at: datetime


class Comment(Entity):
    parent_type: ParentType
    parent_id: str
    author_id: str
    body: str
    is_untrusted_instruction: bool = False


class Email(Entity):
    project_id: str | None = None
    sender_id: str
    recipient_ids: tuple[str, ...]
    subject: str
    body: str
    sent_at: datetime


class Meeting(Entity):
    project_id: str
    title: str
    started_at: datetime
    attendee_ids: tuple[str, ...]
    notes: str


class Decision(Entity):
    project_id: str
    title: str
    body: str
    decided_at: datetime
    decided_by: str
    status: DecisionStatus


class Risk(Entity):
    project_id: str
    title: str
    description: str
    severity: Priority
    status: RiskStatus
    owner_id: str | None = None
    identified_at: datetime


class Blocker(Entity):
    project_id: str
    task_id: str | None = None
    title: str
    description: str
    status: BlockerStatus
    opened_at: datetime
    resolved_at: datetime | None = None


class Incident(Entity):
    project_id: str
    title: str
    description: str
    severity: IncidentSeverity
    status: IncidentStatus
    opened_at: datetime
    resolved_at: datetime | None = None


class Dependency(Entity):
    from_project_id: str
    to_project_id: str
    description: str
    status: ProjectStatus


class Document(Entity):
    project_id: str
    title: str
    doc_type: DocumentType
    access_level: AccessLevel
    version: int
    authored_at: datetime
    is_current: bool
    body: str
    contradicts_live_status: bool = False


class ActivityEvent(Entity):
    project_id: str | None = None
    actor_id: str | None = None
    event_type: ActivityType
    entity_type: str
    entity_id: str
    occurred_at: datetime
    summary: str
    payload: dict[str, str] = Field(default_factory=dict)
