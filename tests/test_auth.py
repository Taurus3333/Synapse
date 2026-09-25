"""Auth unit tests — token claims and password verify (no HTTP)."""

from __future__ import annotations

import pytest

from synapse.auth.passwords import hash_password, verify_password
from synapse.auth.principal import Principal
from synapse.auth.tokens import decode_access_token, issue_access_token

SECRET = "unit-test-synapse-jwt-secret-key-32b"


def test_password_roundtrip() -> None:
    h = hash_password("synapse-demo")
    assert verify_password("synapse-demo", h)
    assert not verify_password("wrong-password", h)


def test_jwt_roundtrip_preserves_tenant() -> None:
    p = Principal(tenant_id="tnt_nw", user_id="usr_nw_00001", role="lead", email="a@b.example")
    token = issue_access_token(secret=SECRET, principal=p, ttl_minutes=10)
    out = decode_access_token(token, SECRET)
    assert out.tenant_id == "tnt_nw"
    assert out.user_id == "usr_nw_00001"
    assert out.role == "lead"
    assert out.can_write


def test_jwt_rejects_tampered_tenant() -> None:
    p = Principal(tenant_id="tnt_nw", user_id="usr_nw_00001", role="member")
    token = issue_access_token(secret=SECRET, principal=p)
    # Wrong secret must fail closed.
    with pytest.raises(ValueError):
        decode_access_token(token, "other-secret-that-is-also-long-enough!!")


def test_viewer_cannot_write() -> None:
    assert not Principal(tenant_id="tnt_nw", user_id="usr_x", role="viewer").can_write
