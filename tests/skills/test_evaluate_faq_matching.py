from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "check-faq-resolution" / "scripts" / "evaluate_faq_matching.py"
_spec = importlib.util.spec_from_file_location("evaluate_faq_matching", _MODULE_PATH)
assert _spec and _spec.loader
eval_faq = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = eval_faq
_spec.loader.exec_module(eval_faq)


def test_case_inventory_has_50_cases_and_20_no_faq() -> None:
    assert len(eval_faq.ALL_CASES) == 50
    assert len(eval_faq.FAQ_MATCH_CASES) == 30
    assert len(eval_faq.NO_FAQ_CASES) == 20
    assert sum(1 for case in eval_faq.ALL_CASES if case.expected_faq_id is None) == 20


def test_current_heuristic_evaluation_runs_without_llm() -> None:
    rows, metrics = eval_faq.run_evaluation(
        data_dir=_REPO_ROOT / "data",
        model="unused",
        cache_path=Path("/tmp/test-faq-cache-unused.json"),
        use_llm=False,
    )

    assert len(rows) == 50
    assert set(metrics) == {"current_heuristic"}
    metric = metrics["current_heuristic"]
    assert metric["total"] == 50
    assert 0 <= metric["accuracy"] <= 1
    assert metric["positive_cases"] == 30
    assert metric["negative_cases"] == 20


def test_pure_llm_prompt_passes_ticket_and_all_active_faqs() -> None:
    faqs = eval_faq._load_faqs(_REPO_ROOT / "data")
    prompt = eval_faq._pure_llm_prompt(eval_faq.ALL_CASES[0], faqs)

    assert "FAQ-001" in prompt
    assert "FAQ-033" in prompt
    assert eval_faq.ALL_CASES[0].subject in prompt
    assert "choose no_match" in prompt


def test_hybrid_candidates_are_limited() -> None:
    faqs = eval_faq._load_faqs(_REPO_ROOT / "data")
    candidates = eval_faq.hybrid_candidate_faqs(eval_faq.ALL_CASES[0], faqs, top_k=5)

    assert 1 <= len(candidates) <= 5
    assert all(candidate["faq_id"].startswith("FAQ-") for candidate in candidates)


def test_report_mentions_all_three_methods_when_rows_present() -> None:
    rows = [
        {
            "method": "current_heuristic",
            "case_id": "C1",
            "expected_match": True,
            "expected_faq_id": "FAQ-001",
            "predicted_match": True,
            "predicted_faq_id": "FAQ-001",
            "confidence": 0.9,
            "reason": "ok",
            "correct": True,
        },
        {
            "method": "pure_llm",
            "case_id": "C1",
            "expected_match": True,
            "expected_faq_id": "FAQ-001",
            "predicted_match": True,
            "predicted_faq_id": "FAQ-001",
            "confidence": 0.9,
            "reason": "ok",
            "correct": True,
        },
        {
            "method": "hybrid_llm_rerank",
            "case_id": "C1",
            "expected_match": True,
            "expected_faq_id": "FAQ-001",
            "predicted_match": True,
            "predicted_faq_id": "FAQ-001",
            "confidence": 0.9,
            "reason": "ok",
            "correct": True,
        },
    ]
    metrics = eval_faq.evaluate_predictions(rows)
    report = eval_faq.render_report(rows, metrics, model="test-model", use_llm=True)

    assert "current_heuristic" in report
    assert "pure_llm" in report
    assert "hybrid_llm_rerank" in report
    assert "Total tickets: 50" in report
