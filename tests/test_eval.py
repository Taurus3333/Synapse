"""Chunk 14 — golden eval scorers and suite (deterministic, no LLM judge)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from synapse.auth.principal import Principal
from synapse.data.generate import generate
from synapse.eval.cases import atlas_q2_case, harbor_status_case, load_suite
from synapse.eval.runner import run_suite, score_answer_fixture
from synapse.eval.score import score_multihop
from synapse.evidence.types import EvidenceItem, SourceKind
from synapse.memory.ltm import LongTermMemory
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, load_dataset, session_factory
from synapse.tools.runtime import ToolSession


def test_load_default_suite() -> None:
    suite = load_suite()
    ids = {c.id for c in suite}
    assert ids == {"atlas_q2_risks", "harbor_delayed"}
    atlas = atlas_q2_case()
    assert atlas.expect_live_status == "at_risk"
    assert "rsk_nw_00003" in atlas.require_pack_ids
    assert "eml_nw_00007" in atlas.require_pack_ids


def test_score_multihop_pass_and_fail() -> None:
    case = atlas_q2_case()
    items = [
        EvidenceItem(
            source_kind=SourceKind.LIVE,
            source="project_lookup",
            record_id="prj_nw_00001",
            summary="ATLAS at_risk",
        ),
        EvidenceItem(
            source_kind=SourceKind.LIVE,
            source="risk_list",
            record_id="rsk_nw_00003",
            summary="SDK risk",
        ),
        EvidenceItem(
            source_kind=SourceKind.LIVE,
            source="blocker_list",
            record_id="blk_nw_00002",
            summary="SDK blocker",
        ),
        EvidenceItem(
            source_kind=SourceKind.LIVE,
            source="email_search",
            record_id="eml_nw_00007",
            summary="Harbor SDK email",
        ),
        EvidenceItem(
            source_kind=SourceKind.LIVE,
            source="meeting_search",
            record_id="mtg_nw_00003",
            summary="Q2 retro",
        ),
    ]
    hops = [
        {"tool": "document_search", "ok": True},
        {"tool": "email_search", "ok": True},
        {"tool": "meeting_search", "ok": True},
        {"tool": "memory_search", "ok": True},
    ]
    ok = score_multihop(
        case,
        live_status="at_risk",
        pack_items=items,
        hops=hops,
        sources_present={"live": True, "email_or_meeting": True},
    )
    assert ok.passed
    assert ok.metrics["pack_id_recall"] == 1.0

    bad = score_multihop(
        case,
        live_status="active",
        pack_items=items[:2],
        hops=[{"tool": "document_search"}],
        sources_present={"live": True, "email_or_meeting": False},
    )
    assert not bad.passed
    failed = {c.name for c in bad.checks if not c.passed}
    assert "live_status" in failed
    assert "require_pack_ids" in failed


def test_score_answer_fixture_grounding() -> None:
    case = atlas_q2_case()
    good = score_answer_fixture(
        case,
        answer="ATLAS is at_risk because the Harbor SDK miss threatens cutover.",
        citations=[
            {"source": "risk_list", "id": "rsk_nw_00003"},
            {"source": "project_lookup", "id": "prj_nw_00001"},
        ],
        rejected_citations=[],
    )
    assert good.passed
    assert good.metrics["citation_precision"] == 1.0

    bad = score_answer_fixture(
        case,
        answer="Everything is on track with no major risks.",
        citations=[{"source": "hallucination", "id": "rsk_fake"}],
        rejected_citations=["rsk_fake"],
    )
    assert not bad.passed


def test_harbor_case_shape() -> None:
    h = harbor_status_case()
    assert h.expect_live_status == "delayed"
    assert "blk_nw_00003" in h.require_pack_ids


@pytest_asyncio.fixture
async def eval_tools(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    for key in (
        "SYNAPSE_TAVILY_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    await create_schema(db.engine)
    sessions = session_factory(db.engine)
    dataset = generate(seed=42, profile="ci")
    async with sessions() as session:
        await load_dataset(session, dataset, password=settings.demo_password)
    admin = next(u for u in dataset.users if u.email == "uma.berg.0@northwind.example")
    ltm = LongTermMemory(sessions)
    await ltm.write(
        tenant_id=admin.tenant_id,
        user_id=admin.id,
        project_key="ATLAS",
        kind="fact",
        content="Prior note: Harbor vendor SDK slip is the top Atlas delivery risk.",
        confidence=0.8,
    )
    tools = ToolSession(
        principal=Principal(
            user_id=admin.id, tenant_id=admin.tenant_id, role=admin.role.value
        ),
        sessions=sessions,
        embedder=None,
        ltm=ltm,
        max_calls=28,
    )
    try:
        yield tools
    finally:
        await db.close()
        clear_settings_cache()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_golden_suite_multihop(eval_tools: ToolSession) -> None:
    report = await run_suite(eval_tools)
    assert report.pass_rate == 1.0, report.to_dict()
    assert report.passed
    by_id = {s.case_id: s for s in report.scores}
    assert by_id["atlas_q2_risks"].metrics["pack_id_recall"] == 1.0
    assert by_id["harbor_delayed"].passed


@pytest.mark.integration
def test_synapse_eval_cli_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    clear_settings_cache()

    from synapse.eval.__main__ import main

    out = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        ["synapse-eval", "--case", "atlas_q2_risks", "--out", str(out)],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["cases"][0]["case_id"] == "atlas_q2_risks"
    clear_settings_cache()
