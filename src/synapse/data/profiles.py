from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    teams_per_tenant: int
    users_per_tenant: int
    extra_projects_per_tenant: int
    milestones_per_project: int
    tasks_per_project: int
    issues_per_project: int
    emails_per_project: int
    meetings_per_project: int
    comments_per_project: int
    decisions_per_project: int
    risks_per_project: int
    blockers_per_project: int
    incidents_per_project: int
    documents_per_project: int
    atlas_slipped_tasks: int
    missing_assignee_rate: float
    stale_document: bool


CI = Profile(
    name="ci",
    teams_per_tenant=2,
    users_per_tenant=6,
    extra_projects_per_tenant=0,
    milestones_per_project=1,
    tasks_per_project=12,
    issues_per_project=4,
    emails_per_project=5,
    meetings_per_project=2,
    comments_per_project=6,
    decisions_per_project=1,
    risks_per_project=2,
    blockers_per_project=1,
    incidents_per_project=1,
    documents_per_project=2,
    atlas_slipped_tasks=4,
    missing_assignee_rate=0.08,
    stale_document=True,
)

FULL = Profile(
    name="full",
    teams_per_tenant=5,
    users_per_tenant=28,
    extra_projects_per_tenant=2,
    milestones_per_project=3,
    tasks_per_project=300,
    issues_per_project=100,
    emails_per_project=240,
    meetings_per_project=70,
    comments_per_project=280,
    decisions_per_project=18,
    risks_per_project=22,
    blockers_per_project=16,
    incidents_per_project=7,
    documents_per_project=35,
    atlas_slipped_tasks=12,
    missing_assignee_rate=0.06,
    stale_document=True,
)

PROFILES: dict[str, Profile] = {"ci": CI, "full": FULL}
