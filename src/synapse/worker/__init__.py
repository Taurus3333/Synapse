"""Reserved for an async ask worker — unused.

Ask is synchronous today. Failed runs use Postgres DLQ-lite (`/v1/runs/failed`).
SQS + worker stays deferred until async ask is a real product requirement.
"""
