from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    LEAD = "lead"
    MEMBER = "member"
    VIEWER = "viewer"


class AccessLevel(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class ProjectStatus(StrEnum):
    PLANNING = "planning"
    ACTIVE = "active"
    AT_RISK = "at_risk"
    DELAYED = "delayed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class IssueType(StrEnum):
    BUG = "bug"
    STORY = "story"
    INCIDENT_TICKET = "incident_ticket"


class IssueStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


class RiskStatus(StrEnum):
    OPEN = "open"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    CLOSED = "closed"


class BlockerStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class IncidentSeverity(StrEnum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"


class IncidentStatus(StrEnum):
    OPEN = "open"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"


class DecisionStatus(StrEnum):
    ACTIVE = "active"
    REVERSED = "reversed"


class DocumentType(StrEnum):
    STATUS_REPORT = "status_report"
    DESIGN = "design"
    RETRO = "retro"
    POSTMORTEM = "postmortem"
    DECISION_MEMO = "decision_memo"
    CONTRACT = "contract"
    MEETING_NOTES = "meeting_notes"


class ActivityType(StrEnum):
    STATUS_CHANGE = "status_change"
    ASSIGNMENT = "assignment"
    COMMENT_ADDED = "comment_added"
    RISK_REPORTED = "risk_reported"
    BLOCKER_OPENED = "blocker_opened"
    BLOCKER_RESOLVED = "blocker_resolved"
    INCIDENT_OPENED = "incident_opened"
    DECISION_RECORDED = "decision_recorded"
    DOCUMENT_PUBLISHED = "document_published"
    EMAIL_SENT = "email_sent"
    MEETING_HELD = "meeting_held"
    MILESTONE_UPDATED = "milestone_updated"


class ParentType(StrEnum):
    TASK = "task"
    ISSUE = "issue"
    RISK = "risk"
    BLOCKER = "blocker"
    INCIDENT = "incident"
    DOCUMENT = "document"
