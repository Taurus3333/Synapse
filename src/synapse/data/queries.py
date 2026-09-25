from datetime import datetime

from synapse.data.clock import Q2_2026_END, Q2_2026_START
from synapse.domain.dataset import Dataset
from synapse.domain.enums import BlockerStatus, TaskStatus
from synapse.domain.models import Blocker, Project, Task


def project_named(dataset: Dataset, tenant_slug: str, key: str) -> Project:
    tenant = dataset.tenant_by_slug(tenant_slug)
    return dataset.project_by_key(tenant.id, key)


def tasks_for(dataset: Dataset, project_id: str) -> list[Task]:
    return [task for task in dataset.tasks if task.project_id == project_id]


def slipped_tasks(
    dataset: Dataset,
    project_id: str,
    window_start: datetime = Q2_2026_START,
    window_end: datetime = Q2_2026_END,
) -> list[Task]:
    """Tasks due in the window that were not completed on time.

    Completion after the due date, or still open at the snapshot, counts as slipped.
    This is deterministic code — the model does not compute it.
    """
    found: list[Task] = []
    for task in tasks_for(dataset, project_id):
        if task.due_date is None:
            continue
        if not (window_start.date() <= task.due_date < window_end.date()):
            continue
        if task.status != TaskStatus.DONE:
            found.append(task)
            continue
        if task.completed_at is not None and task.completed_at.date() > task.due_date:
            found.append(task)
    return found


def open_blockers(dataset: Dataset, project_id: str) -> list[Blocker]:
    return [
        blocker
        for blocker in dataset.blockers
        if blocker.project_id == project_id and blocker.status == BlockerStatus.OPEN
    ]
