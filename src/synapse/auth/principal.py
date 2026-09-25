"""Authentication and authorization — JWT principals, never trust client tenant headers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    role: str
    email: str = ""

    @property
    def can_write(self) -> bool:
        return self.role in {"admin", "lead", "member"}

    @property
    def is_tenant_admin(self) -> bool:
        return self.role in {"admin", "lead"}
