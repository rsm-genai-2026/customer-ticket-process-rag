"""Curated preset queries for the RAG demo UI.

Each entry pairs a realistic-sounding user query with the doc the curator
believes is the right answer, plus a one-line description of the failure mode
or lesson the query exposes. The demo UI uses these to seed a dropdown so a
student can step through different retrieval behaviours without having to
think up their own queries first.

The queries are intentionally varied — some are easy wins for both modes,
some expose HyQ's strengths, some expose HyQ's weaknesses, some are pure
vocabulary-mismatch tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class PresetQuery:
    id: str
    label: str
    query: str
    expected_doc: str
    category: str  # one of: easy, hyq_wins, both_fail, vocab, multi_doc
    teaching_note: str


PRESET_QUERIES: list[PresetQuery] = [
    PresetQuery(
        id="vpn_drop_after_update",
        label="VPN drops after Mac update",
        query="my Mac keeps dropping the VPN every minute since the latest update",
        expected_doc="KB-012",
        category="easy",
        teaching_note=(
            "Both modes find KB-012 (the 2025-09 MeshGuard regression RCA). "
            "Lots of literal vocabulary overlap on 'VPN', 'drop', 'Mac' — a "
            "useful sanity check that the basic pipeline works."
        ),
    ),
    PresetQuery(
        id="password_reset_verification",
        label="Verifying a password reset",
        query="what's the right way to confirm someone's identity before resetting their password",
        expected_doc="KB-001",
        category="hyq_wins",
        teaching_note=(
            "HyQ ranks KB-001's 'Identity verification' section #1 thanks to "
            "the matched hypothetical question; naive finds KB-001 too but "
            "with less informative section labels (window 0 vs 'Identity "
            "verification') and lower confidence."
        ),
    ),
    PresetQuery(
        id="breach_notification_sla",
        label="Breach notification SLA",
        query="how long does the vendor say we have to notify a customer of a breach",
        expected_doc="KB-006",
        category="hyq_wins",
        teaching_note=(
            "Cleanest HyQ win in the suite. Naive misses KB-006 entirely — "
            "the chunk doesn't contain literal phrasing matching the query. "
            "HyQ surfaces it via a hypothetical question that mentions "
            "'breach notification'."
        ),
    ),
    PresetQuery(
        id="dashboard_permissions",
        label="Lost access to a dashboard",
        query="the analytics dashboard says I'm not allowed to see something I could see yesterday",
        expected_doc="KB-010",
        category="easy",
        teaching_note=(
            "HyQ scores ~0.94 on KB-010's 'Known issues' section. Notice the "
            "matched_question — it's almost a paraphrase of the query, which "
            "explains the very high score."
        ),
    ),
    PresetQuery(
        id="contaminated_ticket",
        label="Customer pasted a password into the ticket",
        query="what happens if a customer pastes their password into the ticket body",
        expected_doc="KB-005",
        category="hyq_wins",
        teaching_note=(
            "Tests the 'contaminated ticket' procedure in KB-005. Naive "
            "returns the employee handbook KB-016 first because of vocabulary "
            "overlap; HyQ correctly prioritises KB-005's policy section."
        ),
    ),
    PresetQuery(
        id="error_code_handshake",
        label="ERR_HANDSHAKE_DRIFT meaning",
        query="what does ERR_HANDSHAKE_DRIFT mean and how do I fix it",
        expected_doc="KB-018",
        category="vocab",
        teaching_note=(
            "The error code is a strong literal anchor. Naive picks it up "
            "instantly. HyQ depends on whether the generator wrote a question "
            "containing the error code; sometimes it does, sometimes not. "
            "Good illustration of when explicit keywords beat semantic search."
        ),
    ),
    PresetQuery(
        id="portal_slowness_calendar",
        label="Portal slowness — scheduled change?",
        query="is there a system change scheduled that could explain the current portal slowness",
        expected_doc="KB-015",
        category="both_fail",
        teaching_note=(
            "**The headline failure case.** Both modes return runbooks (KB-009 "
            "+ KB-010) instead of the change calendar (KB-015). The query has "
            "two clauses — 'scheduled change' (cause) and 'portal slowness' "
            "(symptom) — and the embedding gets pulled toward the more vivid "
            "symptom clause. Use this query to teach the patch-by-adding-"
            "questions lesson."
        ),
    ),
    PresetQuery(
        id="inventory_double_count",
        label="Inventory app double-counting",
        query="the cycle count is counting the same item twice",
        expected_doc="KB-017",
        category="hyq_wins",
        teaching_note=(
            "Tests the Inventory App user manual's 'Known quirks' section. "
            "Both modes find KB-017, but HyQ's matched question makes the "
            "retrieval explanatory: students can see WHY this section matched."
        ),
    ),
    PresetQuery(
        id="oncall_escalation",
        label="Who do I page after-hours?",
        query="who do I page if the data analytics specialist is unreachable",
        expected_doc="KB-014",
        category="easy",
        teaching_note=(
            "HyQ surfaces three KB-014 sections with strong scores. Naive "
            "fragments the escalation matrix doc across multiple windows, "
            "making the result less interpretable even when correct."
        ),
    ),
    PresetQuery(
        id="paraphrased_outage",
        label="Paraphrased outage query (negative case)",
        query="is there a planned outage that explains current degraded performance",
        expected_doc="KB-015",
        category="vocab",
        teaching_note=(
            "Same MEANING as the portal-slowness query but different VOCABULARY "
            "('planned outage' vs 'scheduled change', 'degraded performance' "
            "vs 'slowness'). Watch the scores drop ~0.1 across the board. "
            "Embeddings reward literal vocabulary echo, not paraphrase."
        ),
    ),
]


def to_json_serialisable() -> list[dict]:
    return [asdict(q) for q in PRESET_QUERIES]
