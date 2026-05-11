"""Evaluate FAQ matching approaches on curated synthetic tickets.

The production ``check_faq_resolution.py`` skill stays deterministic. This
script is a separate experiment that compares:

1. The current transparent scoring heuristic.
2. A pure LLM judge that sees the ticket plus the full FAQ table.
3. A hybrid "fancy" approach: deterministic retrieval first, then an LLM
   reranks only the top candidate FAQs and applies a stricter decision policy.

The scenario set intentionally includes 50 plausible tickets. Thirty are clear
FAQ matches. Twenty are clear "not in FAQ" cases, so false positives are visible.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from dotenv import load_dotenv
from openai import OpenAI

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CFR_PATH = Path(__file__).resolve().with_name("check_faq_resolution.py")
_spec = importlib.util.spec_from_file_location("check_faq_resolution", _CFR_PATH)
assert _spec and _spec.loader
cfr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cfr
_spec.loader.exec_module(cfr)

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_CACHE = Path("/tmp/customer-ticket-process-faq-llm-cache.json")
DEFAULT_REPORT = _REPO_ROOT / "FAQ_MATCHING_EVALUATION.md"


@dataclass(frozen=True)
class TicketCase:
    case_id: str
    expected_faq_id: str | None
    category: str
    system_name: str
    subject: str
    description: str
    error_or_symptom_detail: str
    steps_already_tried: str
    business_impact_text: str
    expected_outcome: str = "The affected service works as expected."

    @property
    def expected_match(self) -> bool:
        return self.expected_faq_id is not None

    def ticket_row(self) -> dict:
        return {
            "ticket_id": self.case_id,
            "subject": self.subject,
            "description": self.description,
            "affected_system": self.system_name,
            "customer_reported_urgency": "medium",
            "business_impact_text": self.business_impact_text,
            "error_or_symptom_detail": self.error_or_symptom_detail,
            "steps_already_tried": self.steps_already_tried,
            "expected_outcome": self.expected_outcome,
        }

    def triage_row(self) -> dict:
        return {
            "ticket_id": self.case_id,
            "assigned_category": self.category,
            "assigned_priority": "medium",
            "recommended_specialist_group": "",
        }


FAQ_MATCH_CASES: list[TicketCase] = [
    TicketCase(
        "FAQ-MATCH-001",
        "FAQ-001",
        "login_access",
        "Customer Portal",
        "Customer Portal loops between SSO and portal",
        "The sign-in page redirects to SSO, comes back, then loops again without completing.",
        "Browser redirect loop on SSO sign-in.",
        "Cleared cookies and tried a private window.",
        "Renewal team cannot access account notes.",
    ),
    TicketCase(
        "FAQ-MATCH-002",
        "FAQ-002",
        "login_access",
        "Identity Provider",
        "MFA code never arrives",
        "SMS and push MFA codes do not arrive for the user.",
        "MFA code not received during sign-in.",
        "Restarted phone and checked the MFA method.",
        "Manager cannot approve time cards.",
    ),
    TicketCase(
        "FAQ-MATCH-003",
        "FAQ-003",
        "login_access",
        "Identity Provider",
        "Account locked after failed attempts",
        "The user cannot sign in after several failed password attempts.",
        "Backend shows account locked.",
        "Waited ten minutes and retried.",
        "User is blocked from payroll approval.",
    ),
    TicketCase(
        "FAQ-MATCH-004",
        "FAQ-004",
        "login_access",
        "CRM",
        "CRM SSO email mapping fails",
        "The user's email is not mapped in CRM SSO settings, so login fails.",
        "CRM SSO says email is unmapped.",
        "Confirmed username and tenant id.",
        "Sales leader cannot access opportunity review.",
    ),
    TicketCase(
        "FAQ-MATCH-005",
        "FAQ-005",
        "login_access",
        "Customer Portal",
        "Session expired on every portal action",
        "The portal shows session expired after every click.",
        "Session token expires immediately after sign-in.",
        "Signed out and cleared browser storage.",
        "Support supervisors cannot update records.",
    ),
    TicketCase(
        "FAQ-MATCH-006",
        "FAQ-006",
        "password_reset",
        "Identity Provider",
        "Self-service password reset failed",
        "The password reset link does not arrive after using self service.",
        "Self-service reset email never arrives.",
        "Checked spam and confirmed the on-file email.",
        "One analyst cannot access reports.",
    ),
    TicketCase(
        "FAQ-MATCH-007",
        "FAQ-007",
        "password_reset",
        "Identity Provider",
        "Password reset link expired",
        "The reset link says expired before the user can use it.",
        "Expired reset link message.",
        "Requested a reset twice.",
        "User is blocked from sign-in.",
    ),
    TicketCase(
        "FAQ-MATCH-008",
        "FAQ-008",
        "password_reset",
        "Customer Portal",
        "Service account password expired",
        "A background job fails with an auth error after password rotation.",
        "Service account credential appears expired.",
        "Captured the service account name.",
        "Nightly integration did not complete.",
    ),
    TicketCase(
        "FAQ-MATCH-009",
        "FAQ-009",
        "password_reset",
        "CRM",
        "Password blocked by complexity policy",
        "The user cannot pick a new password because the CRM policy rejects it.",
        "New password fails complexity policy.",
        "Tried two passphrases.",
        "User cannot finish CRM setup.",
    ),
    TicketCase(
        "FAQ-MATCH-010",
        "FAQ-010",
        "password_reset",
        "Identity Provider",
        "Privileged password reset requires approval",
        "The privileged account password reset cannot proceed without manager approval.",
        "Manager approval required for privileged account reset.",
        "Collected account name and manager email.",
        "Admin task is blocked.",
    ),
    TicketCase(
        "FAQ-MATCH-011",
        "FAQ-011",
        "billing_account",
        "Billing System",
        "Invoice PDF download spins forever",
        "The invoice PDF download spinner never finishes.",
        "Invoice PDF cannot be downloaded.",
        "Tried a second browser and captured invoice number.",
        "Accounting cannot process payment.",
    ),
    TicketCase(
        "FAQ-MATCH-012",
        "FAQ-012",
        "billing_account",
        "Billing System",
        "Invoice tax rate uses old office location",
        "The invoice total is too high because the old tax rate is still applied after relocation.",
        "Wrong tax rate after office relocation.",
        "Confirmed the new address and effective date.",
        "Finance is holding payment.",
    ),
    TicketCase(
        "FAQ-MATCH-013",
        "FAQ-013",
        "billing_account",
        "Customer Portal",
        "Invoice email not received",
        "The customer reports the invoice email never arrived.",
        "Invoice email delivery missing.",
        "Checked recipient email and invoice number.",
        "Month-end close is delayed.",
    ),
    TicketCase(
        "FAQ-MATCH-014",
        "FAQ-014",
        "software_bug",
        "Customer Portal",
        "Portal shows stale UI after release",
        "The user still sees pre-release UI after yesterday's deploy.",
        "Stale cache after release.",
        "Hard refreshed and captured browser version.",
        "Training screenshots do not match the app.",
    ),
    TicketCase(
        "FAQ-MATCH-015",
        "FAQ-015",
        "software_bug",
        "Inventory App",
        "Saved filter disappeared after release",
        "A saved inventory filter is gone after the latest release.",
        "Saved filter lost on release.",
        "Captured filter name and approximate creation date.",
        "Warehouse team cannot run replenishment list.",
    ),
    TicketCase(
        "FAQ-MATCH-016",
        "FAQ-016",
        "hardware_issue",
        "Customer Portal",
        "Wireless mouse stopped responding",
        "The wireless mouse no longer tracks or clicks.",
        "Wireless mouse battery or pairing issue.",
        "Captured mouse model.",
        "User productivity is reduced.",
    ),
    TicketCase(
        "FAQ-MATCH-017",
        "FAQ-017",
        "hardware_issue",
        "Customer Portal",
        "Shared printer is offline",
        "The team printer reports offline and jobs are stuck.",
        "Shared printer offline.",
        "Captured printer name and floor.",
        "Invoices cannot be printed.",
    ),
    TicketCase(
        "FAQ-MATCH-018",
        "FAQ-018",
        "hardware_issue",
        "Customer Portal",
        "Headset audio crackles during calls",
        "The headset audio crackles during support calls.",
        "Headset audio quality problem.",
        "Captured headset and laptop model.",
        "Customer calls are hard to understand.",
    ),
    TicketCase(
        "FAQ-MATCH-019",
        "FAQ-019",
        "network_connectivity",
        "VPN",
        "VPN drops every few minutes",
        "The VPN disconnects every few minutes from the west region client.",
        "VPN drops at short intervals.",
        "Restarted client and captured VPN version.",
        "Warehouse staff lose access to internal tools.",
    ),
    TicketCase(
        "FAQ-MATCH-020",
        "FAQ-020",
        "network_connectivity",
        "VPN",
        "Cannot reach internal apps over VPN",
        "The VPN connects but internal applications are unreachable.",
        "VPN split tunnel route appears missing.",
        "Captured region and route details.",
        "Remote employees cannot work.",
    ),
    TicketCase(
        "FAQ-MATCH-021",
        "FAQ-021",
        "network_connectivity",
        "Identity Provider",
        "Conference room wifi reauth fails",
        "The conference room AP requires reauth and fails for multiple devices.",
        "Wi-Fi reauthorization fails in conference room.",
        "Captured conference room name.",
        "Board meeting devices cannot connect.",
    ),
    TicketCase(
        "FAQ-MATCH-022",
        "FAQ-022",
        "network_connectivity",
        "VPN",
        "Internal pages load slowly over VPN",
        "Internal apps load slowly and DNS lookups lag over VPN.",
        "DNS resolver lag on VPN.",
        "Flushed local DNS cache and captured region.",
        "Remote support is delayed.",
    ),
    TicketCase(
        "FAQ-MATCH-023",
        "FAQ-024",
        "email_calendar",
        "Email",
        "Out of office auto reply not sending",
        "Auto-reply is enabled for a date range but no out-of-office messages are sent.",
        "Out-of-office auto reply is disabled.",
        "Captured date range and rule preview.",
        "External partners do not see vacation notice.",
    ),
    TicketCase(
        "FAQ-MATCH-024",
        "FAQ-025",
        "email_calendar",
        "Email",
        "Shared mailbox disappeared from Outlook",
        "The contracts shared mailbox disappeared from the user's profile.",
        "Shared mailbox missing.",
        "Restarted mail client and captured mailbox name.",
        "Contract intake is delayed.",
    ),
    TicketCase(
        "FAQ-MATCH-025",
        "FAQ-026",
        "email_calendar",
        "Email",
        "Calendar invites double book",
        "Calendar invites appear twice and double-book the user's calendar.",
        "Duplicate calendar source double books invites.",
        "Captured calendar source list.",
        "Executive calendar is unreliable.",
    ),
    TicketCase(
        "FAQ-MATCH-026",
        "FAQ-027",
        "data_reporting",
        "Analytics Dashboard",
        "Dashboard data appears stale",
        "The Analytics Dashboard did not refresh overnight and still shows stale data.",
        "Dashboard refresh delay.",
        "Captured dashboard name and expected refresh time.",
        "QBR deck is blocked.",
    ),
    TicketCase(
        "FAQ-MATCH-027",
        "FAQ-028",
        "data_reporting",
        "CRM",
        "CRM export missing records",
        "The CRM export is missing records because the inactive flag filter seems wrong.",
        "CRM export excludes expected records.",
        "Captured filter id and date range.",
        "Sales ops cannot finish renewal analysis.",
    ),
    TicketCase(
        "FAQ-MATCH-028",
        "FAQ-029",
        "data_reporting",
        "Analytics Dashboard",
        "Dashboard fails to render",
        "The dashboard fails to render because analytics assets are blocked by the browser.",
        "Browser blocks analytics assets.",
        "Captured browser and extensions.",
        "Analysts cannot view dashboard.",
    ),
    TicketCase(
        "FAQ-MATCH-029",
        "FAQ-030",
        "security_request",
        "Identity Provider",
        "Suspicious login alert",
        "Security received a suspicious login alert for a user account.",
        "Suspicious login remediation needed.",
        "Captured username and report timestamp.",
        "Security team is investigating possible account risk.",
    ),
    TicketCase(
        "FAQ-MATCH-030",
        "FAQ-031",
        "security_request",
        "Identity Provider",
        "Standard new hire access request",
        "A new hire needs standard role access on their start date.",
        "New-hire standard role access request.",
        "Collected manager name and start date.",
        "Employee onboarding is blocked.",
    ),
]


NO_FAQ_CASES: list[TicketCase] = [
    TicketCase(
        "NOFAQ-001",
        None,
        "billing_account",
        "Billing System",
        "Billing API returns 502 during subscription sync",
        "The Billing System API returns 502 during nightly subscription sync.",
        "Subscription sync endpoint fails with request ids from two regions.",
        "Retried from two regions and captured request ids.",
        "Production migration is blocked.",
    ),
    TicketCase(
        "NOFAQ-002",
        None,
        "software_bug",
        "CRM",
        "Marketplace webhook times out",
        "A new CRM marketplace connector webhook returns 504 timeout responses.",
        "Webhook callback times out for marketplace connector.",
        "Captured webhook ids and retried connector.",
        "Lead routing is delayed for launch.",
    ),
    TicketCase(
        "NOFAQ-003",
        None,
        "security_request",
        "Customer Portal",
        "Vendor entitlement approval fails",
        "A vendor entitlement workflow returns an approval error for a time-limited exception.",
        "Privileged vendor entitlement approval fails.",
        "Collected manager approval and vendor contact details.",
        "Regulated support window is at risk.",
    ),
    TicketCase(
        "NOFAQ-004",
        None,
        "network_connectivity",
        "Billing System",
        "Private endpoint resolves to stale host",
        "A private endpoint route resolves to a stale host during integration runs.",
        "Private route points to old host for integration endpoint.",
        "Captured route output from two locations.",
        "Nightly integration cannot reach endpoint.",
    ),
    TicketCase(
        "NOFAQ-005",
        None,
        "data_reporting",
        "Analytics Dashboard",
        "Forecast widget crashes on custom segment",
        "The forecast widget crashes only when a custom enterprise segment is selected.",
        "Custom segment forecast widget throws a client error.",
        "Captured segment id and browser console trace.",
        "Revenue forecast review is blocked.",
    ),
    TicketCase(
        "NOFAQ-006",
        None,
        "software_bug",
        "Inventory App",
        "Barcode scanner duplicates scan events",
        "Inventory App records two scan events for one barcode scan.",
        "Duplicate barcode scan events in mobile inventory flow.",
        "Tested with two scanners and captured device ids.",
        "Cycle count is inaccurate.",
    ),
    TicketCase(
        "NOFAQ-007",
        None,
        "billing_account",
        "Billing System",
        "Usage tier rounding is wrong",
        "Usage charges are rounded to the wrong pricing tier for metered billing.",
        "Metered usage tier calculation appears incorrect.",
        "Exported raw usage and invoice line items.",
        "Finance cannot approve invoice.",
    ),
    TicketCase(
        "NOFAQ-008",
        None,
        "login_access",
        "Customer Portal",
        "Login works but wrong tenant opens",
        "The user can sign in, but the portal opens the wrong customer tenant.",
        "Authenticated user lands in wrong tenant context.",
        "Captured username and expected tenant id.",
        "Support team might update the wrong account.",
    ),
    TicketCase(
        "NOFAQ-009",
        None,
        "email_calendar",
        "Email",
        "Legal hold banner missing",
        "Email messages under legal hold no longer display the compliance banner.",
        "Legal hold compliance banner absent from messages.",
        "Captured mailbox and sample message ids.",
        "Compliance review is blocked.",
    ),
    TicketCase(
        "NOFAQ-010",
        None,
        "security_request",
        "Identity Provider",
        "SCIM deprovisioning lag",
        "Terminated users remain active for hours after SCIM deprovisioning events.",
        "SCIM deprovisioning does not disable users promptly.",
        "Captured event ids and timestamps.",
        "Security offboarding SLA is at risk.",
    ),
    TicketCase(
        "NOFAQ-011",
        None,
        "data_reporting",
        "CRM",
        "Pipeline attribution model changed unexpectedly",
        "The CRM pipeline dashboard uses a new attribution model that was not approved.",
        "Attribution logic changed without release notice.",
        "Compared last week's and today's calculations.",
        "Board reporting is inconsistent.",
    ),
    TicketCase(
        "NOFAQ-012",
        None,
        "software_bug",
        "Customer Portal",
        "Bulk upload partially commits bad rows",
        "A bulk upload fails halfway but still commits some invalid customer rows.",
        "Partial commit after failed bulk upload.",
        "Captured upload file and row numbers.",
        "Customer records need reconciliation.",
    ),
    TicketCase(
        "NOFAQ-013",
        None,
        "network_connectivity",
        "VPN",
        "VPN client accepts connection but drops device posture claims",
        "The VPN connects, but downstream apps cannot see device posture claims.",
        "Device posture claims missing after VPN authentication.",
        "Captured client version and policy id.",
        "Sensitive apps deny access.",
    ),
    TicketCase(
        "NOFAQ-014",
        None,
        "billing_account",
        "Billing System",
        "Credit memo workflow stuck in approval",
        "A credit memo is stuck in approval even after the approver clicked approve.",
        "Credit memo approval state does not advance.",
        "Captured credit memo id and approval event.",
        "Customer refund is delayed.",
    ),
    TicketCase(
        "NOFAQ-015",
        None,
        "hardware_issue",
        "Customer Portal",
        "Loaner laptop cannot enroll in MDM",
        "A loaner laptop fails device enrollment during MDM setup.",
        "MDM enrollment fails on loaner laptop.",
        "Captured serial number and enrollment error.",
        "Employee cannot start onboarding.",
    ),
    TicketCase(
        "NOFAQ-016",
        None,
        "other",
        "Customer Portal",
        "Need new workflow for partner approvals",
        "The customer wants a new approval workflow for partner exception requests.",
        "Net-new workflow request with no existing support procedure.",
        "Collected draft requirements.",
        "Operations wants a process recommendation.",
    ),
    TicketCase(
        "NOFAQ-017",
        None,
        "software_bug",
        "CRM",
        "Mobile CRM saves notes to wrong opportunity",
        "Notes entered in the mobile CRM app attach to the previous opportunity.",
        "Mobile note save targets wrong opportunity record.",
        "Captured app version and two opportunity ids.",
        "Sales notes are being misfiled.",
    ),
    TicketCase(
        "NOFAQ-018",
        None,
        "data_reporting",
        "Analytics Dashboard",
        "Cohort retention chart uses wrong timezone",
        "The retention chart shifts users by one day because timezone conversion is wrong.",
        "Timezone mismatch in cohort retention chart.",
        "Compared UTC export with local dashboard.",
        "Executive retention metrics are incorrect.",
    ),
    TicketCase(
        "NOFAQ-019",
        None,
        "security_request",
        "Identity Provider",
        "Conditional access policy excludes contractors",
        "A conditional access rule accidentally excludes contractors from MFA enforcement.",
        "Contractor group excluded from conditional access policy.",
        "Captured policy id and group membership.",
        "Security control gap needs review.",
    ),
    TicketCase(
        "NOFAQ-020",
        None,
        "email_calendar",
        "Email",
        "Executive delegate approval loop",
        "Delegate access approval emails bounce between two executive assistants.",
        "Delegated mailbox approval loop.",
        "Captured approver emails and timestamps.",
        "Executive assistant cannot complete coverage setup.",
    ),
]

ALL_CASES = FAQ_MATCH_CASES + NO_FAQ_CASES


def _load_faqs(data_dir: Path) -> list[dict]:
    frame = cfr.read_csv(data_dir, "raw/faq_knowledge_base.csv").filter(pl.col("active_flag"))
    return frame.to_dicts()


def _faq_for_prompt(faq: dict) -> dict:
    return {
        "faq_id": faq.get("faq_id", ""),
        "category": faq.get("category", ""),
        "system_name": faq.get("system_name", ""),
        "issue_pattern": faq.get("issue_pattern", ""),
        "symptoms": faq.get("symptoms", ""),
        "solution_steps": faq.get("solution_steps", ""),
        "required_customer_info": faq.get("required_customer_info", ""),
    }


def _case_for_prompt(case: TicketCase) -> dict:
    return {
        "ticket_id": case.case_id,
        "category_from_triage": case.category,
        "affected_system": case.system_name,
        "subject": case.subject,
        "description": case.description,
        "error_or_symptom_detail": case.error_or_symptom_detail,
        "steps_already_tried": case.steps_already_tried,
        "business_impact_text": case.business_impact_text,
    }


def current_heuristic_prediction(case: TicketCase, faqs: list[dict]) -> dict:
    context = {
        "ticket": case.ticket_row(),
        "triage": case.triage_row(),
        "faqs": pl.DataFrame(faqs),
        "triage_source": "curated_case",
    }
    ranked = cfr.rank_faq_candidates(context, context["faqs"])
    decision = cfr.decide_faq_applicability(context, ranked)
    top = ranked.row(0, named=True) if ranked.height else {}
    return {
        "method": "current_heuristic",
        "predicted_match": bool(decision["faq_match_found"])
        and decision["recommended_next_step"] == "draft-faq-response",
        "predicted_faq_id": decision["faq_id"] or None,
        "confidence": float(decision["match_confidence"]),
        "reason": decision["faq_application_reason"],
        "candidate_faq_ids": decision["candidate_faq_ids"],
        "top_score": top.get("score", ""),
        "recommended_next_step": decision["recommended_next_step"],
    }


def hybrid_candidate_faqs(case: TicketCase, faqs: list[dict], *, top_k: int = 5) -> list[dict]:
    context = {
        "ticket": case.ticket_row(),
        "triage": case.triage_row(),
        "faqs": pl.DataFrame(faqs),
        "triage_source": "curated_case",
    }
    ranked = cfr.rank_faq_candidates(context, context["faqs"])
    ids = ranked.head(top_k)["faq_id"].to_list()
    by_id = {faq["faq_id"]: faq for faq in faqs}
    return [by_id[faq_id] for faq_id in ids if faq_id in by_id]


def _cache_key(method: str, case: TicketCase, model: str, faq_ids: list[str]) -> str:
    raw = json.dumps(
        {
            "method": method,
            "model": model,
            "case": _case_for_prompt(case),
            "faq_ids": faq_ids,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _load_env() -> None:
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(Path.home() / ".env")


def _openai_client() -> OpenAI:
    _load_env()
    triton_key = os.environ.get("TRITONAI_API_KEY", "").strip()
    if triton_key:
        return OpenAI(api_key=triton_key, base_url="https://tritonai-api.ucsd.edu/v1")
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        return OpenAI(api_key=openai_key)
    raise RuntimeError("No TRITONAI_API_KEY or OPENAI_API_KEY found in environment, .env, or ~/.env.")


def _system_prompt() -> str:
    return (
        "You are evaluating whether a support ticket is resolved by one existing FAQ. "
        "Return only JSON. Be conservative: if the FAQ does not directly address the "
        "reported symptom and affected system, choose no_match. Do not match merely "
        "because the category is similar."
    )


def _pure_llm_prompt(case: TicketCase, faqs: list[dict]) -> str:
    return json.dumps(
        {
            "task": "Choose the single best FAQ for this ticket, or choose no_match.",
            "decision_policy": [
                "Pick an FAQ only if the symptom/problem is directly covered by that FAQ.",
                "Category and system are helpful context but are not enough by themselves.",
                "If the ticket appears plausible but absent from the FAQ table, choose no_match.",
                "Confidence should reflect likelihood that the FAQ would resolve the ticket.",
            ],
            "ticket": _case_for_prompt(case),
            "faqs": [_faq_for_prompt(faq) for faq in faqs],
            "required_json": {
                "faq_match_found": "boolean",
                "faq_id": "FAQ id string or empty string",
                "confidence": "number between 0 and 1",
                "reason": "brief explanation",
                "ticket_evidence": "short quote or paraphrase from ticket",
                "faq_evidence": "short quote or paraphrase from FAQ, or empty if no_match",
            },
        },
        indent=2,
    )


def _hybrid_llm_prompt(case: TicketCase, candidates: list[dict]) -> str:
    return json.dumps(
        {
            "task": "Rerank the candidate FAQs for this ticket, or choose no_match.",
            "decision_policy": [
                "The candidate list came from a simple lexical retriever and may contain false positives.",
                "Choose an FAQ only if it directly resolves the ticket's symptom.",
                "Choose no_match if all candidates are merely same category/system but not the same issue.",
                "If confidence is below 0.70, choose no_match or mark needs_human_review true.",
            ],
            "ticket": _case_for_prompt(case),
            "candidate_faqs": [_faq_for_prompt(faq) for faq in candidates],
            "required_json": {
                "faq_match_found": "boolean",
                "faq_id": "FAQ id string or empty string",
                "confidence": "number between 0 and 1",
                "needs_human_review": "boolean",
                "reason": "brief explanation",
                "ticket_evidence": "short quote or paraphrase from ticket",
                "faq_evidence": "short quote or paraphrase from FAQ, or empty if no_match",
            },
        },
        indent=2,
    )


def _normalize_llm_result(method: str, raw: dict, candidate_ids: list[str]) -> dict:
    faq_id = str(raw.get("faq_id") or "").strip()
    if faq_id.lower() in {"none", "no_match", "no match", "null"}:
        faq_id = ""
    match = bool(raw.get("faq_match_found")) and bool(faq_id)
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)
    return {
        "method": method,
        "predicted_match": match,
        "predicted_faq_id": faq_id or None,
        "confidence": confidence,
        "reason": str(raw.get("reason") or ""),
        "ticket_evidence": str(raw.get("ticket_evidence") or ""),
        "faq_evidence": str(raw.get("faq_evidence") or ""),
        "needs_human_review": bool(raw.get("needs_human_review", confidence < 0.70)),
        "candidate_faq_ids": "|".join(candidate_ids),
    }


def _call_json(client: OpenAI, *, model: str, prompt: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=700,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content or "{}"
    return json.loads(text)


def llm_prediction(
    method: str,
    case: TicketCase,
    faqs: list[dict],
    *,
    model: str,
    cache: dict,
    cache_path: Path,
    client: OpenAI,
    top_k: int = 5,
) -> dict:
    if method == "pure_llm":
        prompt_faqs = faqs
        prompt = _pure_llm_prompt(case, prompt_faqs)
    elif method == "hybrid_llm_rerank":
        prompt_faqs = hybrid_candidate_faqs(case, faqs, top_k=top_k)
        prompt = _hybrid_llm_prompt(case, prompt_faqs)
    else:
        raise ValueError(f"unknown LLM method: {method}")

    faq_ids = [faq["faq_id"] for faq in prompt_faqs]
    key = _cache_key(method, case, model, faq_ids)
    if key not in cache:
        raw = _call_json(client, model=model, prompt=prompt)
        cache[key] = raw
        _save_cache(cache_path, cache)
    return _normalize_llm_result(method, cache[key], faq_ids)


def prediction_correct(case: TicketCase, prediction: dict) -> bool:
    if case.expected_faq_id is None:
        return not prediction["predicted_match"]
    return prediction["predicted_match"] and prediction["predicted_faq_id"] == case.expected_faq_id


def evaluate_predictions(rows: list[dict]) -> dict:
    by_method: dict[str, list[dict]] = {}
    for row in rows:
        by_method.setdefault(row["method"], []).append(row)

    metrics: dict[str, dict] = {}
    for method, items in by_method.items():
        total = len(items)
        correct = sum(1 for item in items if item["correct"])
        positives = [item for item in items if item["expected_match"]]
        negatives = [item for item in items if not item["expected_match"]]
        false_positives = [item for item in negatives if item["predicted_match"]]
        false_negatives = [item for item in positives if not item["predicted_match"]]
        wrong_faq = [
            item
            for item in positives
            if item["predicted_match"] and item["predicted_faq_id"] != item["expected_faq_id"]
        ]
        metrics[method] = {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total else 0.0,
            "positive_cases": len(positives),
            "negative_cases": len(negatives),
            "positive_recall": (sum(1 for item in positives if item["correct"]) / len(positives) if positives else 0.0),
            "negative_recall": (sum(1 for item in negatives if item["correct"]) / len(negatives) if negatives else 0.0),
            "false_positives": len(false_positives),
            "false_negatives": len(false_negatives),
            "wrong_faq": len(wrong_faq),
        }
    return metrics


def run_evaluation(
    *,
    data_dir: Path,
    model: str,
    cache_path: Path,
    use_llm: bool,
    limit: int | None = None,
) -> tuple[list[dict], dict]:
    cases = ALL_CASES[:limit] if limit else ALL_CASES
    faqs = _load_faqs(data_dir)
    rows: list[dict] = []

    for case in cases:
        prediction = current_heuristic_prediction(case, faqs)
        rows.append(_row(case, prediction))

    if use_llm:
        client = _openai_client()
        cache = _load_cache(cache_path)
        for case in cases:
            for method in ["pure_llm", "hybrid_llm_rerank"]:
                prediction = llm_prediction(
                    method,
                    case,
                    faqs,
                    model=model,
                    cache=cache,
                    cache_path=cache_path,
                    client=client,
                )
                rows.append(_row(case, prediction))

    return rows, evaluate_predictions(rows)


def _row(case: TicketCase, prediction: dict) -> dict:
    return {
        "case_id": case.case_id,
        "subject": case.subject,
        "category": case.category,
        "system_name": case.system_name,
        "expected_match": case.expected_match,
        "expected_faq_id": case.expected_faq_id,
        **prediction,
        "correct": prediction_correct(case, prediction),
    }


def _markdown_metrics(metrics: dict) -> str:
    lines = [
        "| Method | Accuracy | Positive recall | Negative recall | False positives | False negatives | Wrong FAQ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for method, metric in metrics.items():
        lines.append(
            "| {method} | {accuracy:.1%} ({correct}/{total}) | {positive_recall:.1%} | "
            "{negative_recall:.1%} | {false_positives} | {false_negatives} | {wrong_faq} |".format(
                method=method,
                **metric,
            )
        )
    return "\n".join(lines)


def _markdown_errors(rows: list[dict]) -> str:
    errors = [row for row in rows if not row["correct"]]
    if not errors:
        return "No errors for the methods that were run."
    lines = [
        "| Method | Case | Expected | Predicted | Confidence | Reason |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in errors:
        expected = row["expected_faq_id"] or "no_match"
        predicted = row["predicted_faq_id"] or "no_match"
        reason = str(row.get("reason") or "").replace("|", "\\|")
        lines.append(
            f"| {row['method']} | {row['case_id']} | {expected} | {predicted} | "
            f"{float(row['confidence']):.2f} | {reason} |"
        )
    return "\n".join(lines)


def _markdown_case_inventory() -> str:
    lines = [
        "| Case | Expected | Category | System | Subject |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in ALL_CASES:
        lines.append(
            f"| {case.case_id} | {case.expected_faq_id or 'no_match'} | "
            f"{case.category} | {case.system_name} | {case.subject} |"
        )
    return "\n".join(lines)


def render_report(rows: list[dict], metrics: dict, *, model: str, use_llm: bool) -> str:
    llm_note = (
        f"The LLM methods were run with model `{model}`."
        if use_llm
        else "The LLM methods were not run because `--use-llm` was not supplied."
    )
    return f"""# FAQ Matching Evaluation

This report compares three approaches for connecting support tickets to FAQ
entries:

1. **Current heuristic**: deterministic category/system/token-overlap scoring.
2. **Pure LLM**: the ticket and the full FAQ table are passed to an LLM, which
   selects the best FAQ or `no_match` with confidence.
3. **Hybrid LLM rerank**: the current heuristic retrieves the top 5 candidates,
   then an LLM reranks those candidates and may choose `no_match`.

{llm_note}

## Test Set

- Total tickets: {len(ALL_CASES)}
- Clear FAQ matches: {len(FAQ_MATCH_CASES)}
- Clearly not in FAQ: {len(NO_FAQ_CASES)}

The 20 no-FAQ tickets are intentionally plausible but absent from the FAQ table.
They are designed to expose false positives.

## Results

{_markdown_metrics(metrics)}

## Errors

{_markdown_errors(rows)}

## Assessment

The current heuristic is transparent and cheap, but it is brittle. Category and
system matches contribute enough points to cross the match threshold even when
the symptom overlap is weak. That makes it vulnerable to false positives on
plausible but unseen issues.

The pure LLM approach tests whether a model can make the FAQ/no-FAQ judgment
from the ticket and the FAQ text directly. It is more semantically flexible, but
it is also harder to make deterministic, can be more expensive, and needs strong
JSON/grounding constraints.

The hybrid rerank approach is the most operationally attractive pattern: use
cheap deterministic retrieval to narrow the candidate set, then use GenAI only
for the judgment step. It should be easier to audit because the LLM sees a small
candidate list and must explain why the selected FAQ directly resolves the
ticket.

## Recommended GenAI Improvement Path

1. Tighten the current heuristic so category + system alone cannot create a
   match.
2. Add semantic retrieval or embeddings to find candidate FAQs by meaning, not
   just token overlap.
3. Use an LLM to rerank the top candidates and choose `no_match` when none
   directly applies.
4. Require structured JSON with evidence from both the ticket and FAQ.
5. Route low-confidence cases to specialist review instead of drafting an FAQ
   response.
6. Use specialist-resolved no-FAQ cases to draft candidate FAQ entries, but
   require human approval before adding them to the knowledge base.

## Case Inventory

{_markdown_case_inventory()}
"""


def write_report(path: Path, report: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--data-dir", default=str(_REPO_ROOT / "data"))
    parser.add_argument("--model", default=os.environ.get("FAQ_MATCH_LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    rows, metrics = run_evaluation(
        data_dir=Path(args.data_dir),
        model=args.model,
        cache_path=Path(args.cache),
        use_llm=args.use_llm,
        limit=args.limit,
    )
    report = render_report(rows, metrics, model=args.model, use_llm=args.use_llm)
    write_report(Path(args.report), report)

    payload = {
        "report": str(Path(args.report).resolve()),
        "model": args.model,
        "use_llm": args.use_llm,
        "metrics": metrics,
    }
    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote {payload['report']}")
        print(_markdown_metrics(metrics))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
