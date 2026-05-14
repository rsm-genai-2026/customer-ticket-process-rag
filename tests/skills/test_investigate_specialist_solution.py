"""Tests for the investigate-specialist-solution skill (LLM-based).

LLM-touching tests hit the real TritonAI gateway. The ``_mock_llm_payload``
helper produces a sample dict used to test the pure ``normalize_llm_solution``
function — that's not a mock, just example input. Requires ``TRITONAI_API_KEY``
in ``.env``.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = (
    _REPO_ROOT / "skills" / "investigate-specialist-solution" / "scripts" / "investigate_specialist_solution.py"
)
_spec = importlib.util.spec_from_file_location("investigate_specialist_solution", _MODULE_PATH)
assert _spec and _spec.loader
iss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iss)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_triage(out_dir: Path, category: str = "login_access") -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T10:00:00+00:00",
                "skill_name": "classify-prioritize-ticket",
                "assigned_category": category,
                "assigned_priority": "medium",
                "recommended_specialist_group": "identity_security",
                "target_first_response_at": "2026-04-30T18:00:00+00:00",
                "target_resolution_at": "2026-05-01T10:00:00+00:00",
                "classification_evidence": "test",
                "priority_reason": "test",
                "confidence_score": 0.9,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "triage_decisions.csv")


def _seed_escalation(
    out_dir: Path,
    specialist_id: str = "SP-001",
    missing_info: bool = False,
) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T11:00:00+00:00",
                "skill_name": "escalate-to-specialist",
                "specialist_id": specialist_id,
                "specialist_name": "Test Specialist",
                "specialist_group": "identity_security",
                "specialist_seniority": "senior",
                "specialist_supports_affected_system": True,
                "requested_specialist_group": "identity_security",
                "escalation_reason": "no FAQ match found",
                "handoff_summary": "x",
                "specific_question_for_specialist": "x",
                "customer_evidence_included": "x",
                "missing_information_flag": missing_info,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "escalation_decisions.csv")


def _mock_llm_payload(**overrides) -> dict:
    """A reasonable default LLM response that tests can mutate."""

    base = {
        "root_cause": "Permission cache out of sync between SSO and the Customer Portal.",
        "diagnostic_steps": [
            "Reviewed audit log for recent SSO assertions",
            "Checked cached group membership in target system",
            "Confirmed customer's browser version",
        ],
        "evidence_reviewed": [
            "SSO audit log for last 24h",
            "Group propagation timing",
        ],
        "solution_summary": (
            "Force a refresh of the user's SSO group membership and clear server-side "
            "session cache so the user can sign back in with the correct permissions."
        ),
        "customer_action_required": (
            "Sign out completely, wait two minutes, then sign back in. Reply to confirm whether the issue is resolved."
        ),
        "confidence": 0.82,
        "requires_follow_up_flag": False,
        "reason": "Strong evidence for SSO cache mismatch; straightforward mitigation.",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Context loading
# ---------------------------------------------------------------------------


def test_load_investigation_context_happy(tmp_path: Path) -> None:
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert ctx["specialist"]["specialist_id"] == "SP-001"
    # The new context also returns triage (may be empty dict when not seeded)
    assert "triage" in ctx


def test_load_investigation_context_no_escalation_raises(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as exc:
        iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "escalate-to-specialist" in str(exc.value)


def test_load_investigation_context_unknown_specialist_raises(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, specialist_id="SP-DOES-NOT-EXIST")
    with pytest.raises(LookupError) as exc:
        iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "SP-DOES-NOT-EXIST" in str(exc.value)


def test_load_investigation_context_includes_triage_when_present(tmp_path: Path) -> None:
    _seed_triage(tmp_path, category="login_access")
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["triage"].get("assigned_category") == "login_access"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_build_llm_prompt_includes_required_fields(tmp_path: Path) -> None:
    _seed_triage(tmp_path)
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    prompt = iss.build_llm_prompt(ctx)
    payload = json.loads(prompt)
    assert "ticket" in payload and payload["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert "triage" in payload
    assert "escalation" in payload
    assert "specialist" in payload and payload["specialist"]["specialist_id"] == "SP-001"
    assert "required_json" in payload
    assert "decision_policy" in payload and isinstance(payload["decision_policy"], list)


def test_build_llm_prompt_carries_missing_info_flag(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=True)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    payload = json.loads(iss.build_llm_prompt(ctx))
    assert payload["escalation"]["missing_information_flag"] is True


# ---------------------------------------------------------------------------
# Response normalisation
# ---------------------------------------------------------------------------


def test_normalize_llm_solution_passes_through_clean_response(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=False)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    out = iss.normalize_llm_solution(_mock_llm_payload(), ctx)
    assert out["confidence_score"] == 0.82
    assert out["requires_follow_up_flag"] is False
    assert isinstance(out["diagnostic_steps"], list) and out["diagnostic_steps"]
    assert "SSO" in out["root_cause"]


def test_normalize_caps_confidence_when_missing_info_flag(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=True)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    raw = _mock_llm_payload(confidence=0.95)
    out = iss.normalize_llm_solution(raw, ctx)
    # Cap is 0.60 in the script — never exceed it when the upstream flag is set
    assert out["confidence_score"] <= 0.60
    assert "missing" in out["specialist_notes"].lower()


def test_normalize_does_not_cap_when_missing_info_flag_is_false(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=False)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    out = iss.normalize_llm_solution(_mock_llm_payload(confidence=0.95), ctx)
    assert out["confidence_score"] == 0.95


def test_normalize_clamps_confidence_into_unit_interval(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=False)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    high = iss.normalize_llm_solution(_mock_llm_payload(confidence=5.0), ctx)
    low = iss.normalize_llm_solution(_mock_llm_payload(confidence=-1.5), ctx)
    assert 0.0 <= high["confidence_score"] <= 1.0
    assert 0.0 <= low["confidence_score"] <= 1.0


def test_normalize_handles_missing_or_blank_fields(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=False)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    out = iss.normalize_llm_solution({"confidence": 0.5}, ctx)
    # Defaults rather than KeyError
    assert out["root_cause"]
    assert out["solution_summary"]
    assert out["customer_action_required"]
    assert out["diagnostic_steps"]
    assert out["evidence_reviewed"]


def test_normalize_preserves_follow_up_flag(tmp_path: Path) -> None:
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    out = iss.normalize_llm_solution(_mock_llm_payload(requires_follow_up_flag=True), ctx)
    assert out["requires_follow_up_flag"] is True


# ---------------------------------------------------------------------------
# Row construction (calls the mocked LLM)
# ---------------------------------------------------------------------------


def test_build_solution_row_structural_invariants(tmp_path: Path) -> None:
    """Hit the real LLM; assert structural invariants and routing keys."""
    _seed_triage(tmp_path)
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    row = iss.build_solution_row(ctx, model=iss.DEFAULT_MODEL)
    assert row["specialist_id"] == "SP-001"
    assert isinstance(row["root_cause"], str) and row["root_cause"].strip()
    assert isinstance(row["solution_summary"], str) and row["solution_summary"].strip()
    confidence = float(row["confidence_score"])
    assert 0.0 <= confidence <= 1.0
    assert f"llm_model={iss.DEFAULT_MODEL}" in row["decision_summary"]


def test_build_solution_row_does_not_leak_internal_keywords(tmp_path: Path) -> None:
    """Live LLM output must not contain operationally sensitive tokens."""
    _seed_triage(tmp_path)
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    row = iss.build_solution_row(ctx, model=iss.DEFAULT_MODEL)
    customer_text = (row["solution_summary"] + " " + row["customer_action_required"]).lower()
    for forbidden in ("password hash", "audit log id"):
        assert forbidden not in customer_text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_happy_path_writes_solution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_triage(tmp_path)
    _seed_escalation(tmp_path)
    rc = iss.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    sols = tmp_path / "specialist_solutions.csv"
    assert sols.exists()
    with sols.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    for required in (
        "root_cause",
        "diagnostic_steps",
        "evidence_reviewed",
        "solution_summary",
        "customer_action_required",
        "confidence_score",
    ):
        assert required in header, required
    out = capsys.readouterr().out
    assert "Specialist solution for" in out
    assert "draft-specialist-response" in out


def test_main_caps_confidence_when_escalation_flags_missing_info(tmp_path: Path) -> None:
    """Even at maximum LLM confidence, missing-info flag caps the row at <=0.60."""
    _seed_triage(tmp_path)
    _seed_escalation(tmp_path, missing_info=True)
    rc = iss.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    row = pl.read_csv(tmp_path / "specialist_solutions.csv").row(0, named=True)
    assert float(row["confidence_score"]) <= 0.60


def test_main_no_escalation_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = iss.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 3
    assert "escalate-to-specialist" in capsys.readouterr().err


def test_main_returns_4_on_llm_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM call raises, the script must emit error_code=llm_decision_failed."""

    _seed_triage(tmp_path)
    _seed_escalation(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(iss, "call_llm_for_specialist_solution", boom)

    rc = iss.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--json",
        ]
    )
    assert rc == 4
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "llm_decision_failed"
