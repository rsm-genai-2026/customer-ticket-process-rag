# FAQ Matching Evaluation

This report was generated before the production skill was simplified. It now
serves as background showing why the rule-based FAQ matcher was replaced by a
direct LLM decision.

It compares three approaches for connecting support tickets to FAQ entries:

1. **Legacy heuristic**: deterministic category/system/token-overlap scoring.
2. **Pure LLM**: the ticket and the full FAQ table are passed to an LLM, which
   selects the best FAQ or `no_match` with confidence.
3. **Hybrid LLM rerank**: the current heuristic retrieves the top 5 candidates,
   then an LLM reranks those candidates and may choose `no_match`.

The LLM methods were run with model `gpt-4.1-mini`.

## Test Set

- Total tickets: 50
- Clear FAQ matches: 30
- Clearly not in FAQ: 20

The 20 no-FAQ tickets are intentionally plausible but absent from the FAQ table.
They are designed to expose false positives.

## Results

| Method | Accuracy | Positive recall | Negative recall | False positives | False negatives | Wrong FAQ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| current_heuristic | 64.0% (32/50) | 100.0% | 10.0% | 18 | 0 | 0 |
| pure_llm | 100.0% (50/50) | 100.0% | 100.0% | 0 | 0 | 0 |
| hybrid_llm_rerank | 100.0% (50/50) | 100.0% | 100.0% | 0 | 0 | 0 |

## Errors

| Method | Case | Expected | Predicted | Confidence | Reason |
| --- | --- | --- | --- | ---: | --- |
| current_heuristic | NOFAQ-001 | no_match | FAQ-011 | 0.85 | FAQ FAQ-011 (invoice_pdf_download_fails) matches category billing_account and system Billing System with overlap [billing\|fails] |
| current_heuristic | NOFAQ-003 | no_match | FAQ-008 | 0.80 | FAQ FAQ-008 (service_account_password_expired) matches category password_reset and system Customer Portal with overlap [error\|fails\|service] |
| current_heuristic | NOFAQ-004 | no_match | FAQ-020 | 0.85 | FAQ FAQ-020 (vpn_split_tunnel_misconfig) matches category network_connectivity and system VPN with overlap [cannot\|reach] |
| current_heuristic | NOFAQ-005 | no_match | FAQ-029 | 0.95 | FAQ FAQ-029 (browser_blocking_analytics_assets) matches category data_reporting and system Analytics Dashboard with overlap [blocked\|browser] |
| current_heuristic | NOFAQ-006 | no_match | FAQ-015 | 0.85 | FAQ FAQ-015 (saved_filter_lost_on_release) matches category software_bug and system Inventory App |
| current_heuristic | NOFAQ-007 | no_match | FAQ-012 | 0.95 | FAQ FAQ-012 (wrong_tax_rate_after_relocation) matches category billing_account and system Billing System with overlap [affected\|billing\|invoice\|wrong] |
| current_heuristic | NOFAQ-008 | no_match | FAQ-005 | 0.95 | FAQ FAQ-005 (session_token_expired) matches category login_access and system Customer Portal with overlap [portal\|sign\|user] |
| current_heuristic | NOFAQ-009 | no_match | FAQ-025 | 0.90 | FAQ FAQ-025 (shared_mailbox_missing) matches category email_calendar and system Email with overlap [mailbox\|missing] |
| current_heuristic | NOFAQ-010 | no_match | FAQ-030 | 0.75 | FAQ FAQ-030 (suspicious_login_remediation) matches category security_request and system Identity Provider |
| current_heuristic | NOFAQ-011 | no_match | FAQ-028 | 0.80 | FAQ FAQ-028 (export_filter_excludes_records) matches category data_reporting and system CRM |
| current_heuristic | NOFAQ-012 | no_match | FAQ-014 | 0.80 | FAQ FAQ-014 (stale_cache_after_release) matches category software_bug and system Customer Portal |
| current_heuristic | NOFAQ-013 | no_match | FAQ-019 | 0.85 | FAQ FAQ-019 (vpn_disconnects_short_intervals) matches category network_connectivity and system VPN with overlap [client\|drops] |
| current_heuristic | NOFAQ-014 | no_match | FAQ-012 | 0.95 | FAQ FAQ-012 (wrong_tax_rate_after_relocation) matches category billing_account and system Billing System with overlap [affected\|customer] |
| current_heuristic | NOFAQ-015 | no_match | FAQ-018 | 0.85 | FAQ FAQ-018 (headset_audio_quality) matches category hardware_issue and system Customer Portal with overlap [during] |
| current_heuristic | NOFAQ-016 | no_match | FAQ-033 | 0.90 | FAQ FAQ-033 (equipment_return_process) matches category other and system Customer Portal with overlap [process\|workflow] |
| current_heuristic | NOFAQ-018 | no_match | FAQ-027 | 0.80 | FAQ FAQ-027 (dashboard_refresh_delay) matches category data_reporting and system Analytics Dashboard with overlap [dashboard] |
| current_heuristic | NOFAQ-019 | no_match | FAQ-030 | 0.80 | FAQ FAQ-030 (suspicious_login_remediation) matches category security_request and system Identity Provider with overlap [review] |
| current_heuristic | NOFAQ-020 | no_match | FAQ-025 | 0.85 | FAQ FAQ-025 (shared_mailbox_missing) matches category email_calendar and system Email with overlap [mailbox] |

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

| Case | Expected | Category | System | Subject |
| --- | --- | --- | --- | --- |
| FAQ-MATCH-001 | FAQ-001 | login_access | Customer Portal | Customer Portal loops between SSO and portal |
| FAQ-MATCH-002 | FAQ-002 | login_access | Identity Provider | MFA code never arrives |
| FAQ-MATCH-003 | FAQ-003 | login_access | Identity Provider | Account locked after failed attempts |
| FAQ-MATCH-004 | FAQ-004 | login_access | CRM | CRM SSO email mapping fails |
| FAQ-MATCH-005 | FAQ-005 | login_access | Customer Portal | Session expired on every portal action |
| FAQ-MATCH-006 | FAQ-006 | password_reset | Identity Provider | Self-service password reset failed |
| FAQ-MATCH-007 | FAQ-007 | password_reset | Identity Provider | Password reset link expired |
| FAQ-MATCH-008 | FAQ-008 | password_reset | Customer Portal | Service account password expired |
| FAQ-MATCH-009 | FAQ-009 | password_reset | CRM | Password blocked by complexity policy |
| FAQ-MATCH-010 | FAQ-010 | password_reset | Identity Provider | Privileged password reset requires approval |
| FAQ-MATCH-011 | FAQ-011 | billing_account | Billing System | Invoice PDF download spins forever |
| FAQ-MATCH-012 | FAQ-012 | billing_account | Billing System | Invoice tax rate uses old office location |
| FAQ-MATCH-013 | FAQ-013 | billing_account | Customer Portal | Invoice email not received |
| FAQ-MATCH-014 | FAQ-014 | software_bug | Customer Portal | Portal shows stale UI after release |
| FAQ-MATCH-015 | FAQ-015 | software_bug | Inventory App | Saved filter disappeared after release |
| FAQ-MATCH-016 | FAQ-016 | hardware_issue | Customer Portal | Wireless mouse stopped responding |
| FAQ-MATCH-017 | FAQ-017 | hardware_issue | Customer Portal | Shared printer is offline |
| FAQ-MATCH-018 | FAQ-018 | hardware_issue | Customer Portal | Headset audio crackles during calls |
| FAQ-MATCH-019 | FAQ-019 | network_connectivity | VPN | VPN drops every few minutes |
| FAQ-MATCH-020 | FAQ-020 | network_connectivity | VPN | Cannot reach internal apps over VPN |
| FAQ-MATCH-021 | FAQ-021 | network_connectivity | Identity Provider | Conference room wifi reauth fails |
| FAQ-MATCH-022 | FAQ-022 | network_connectivity | VPN | Internal pages load slowly over VPN |
| FAQ-MATCH-023 | FAQ-024 | email_calendar | Email | Out of office auto reply not sending |
| FAQ-MATCH-024 | FAQ-025 | email_calendar | Email | Shared mailbox disappeared from Outlook |
| FAQ-MATCH-025 | FAQ-026 | email_calendar | Email | Calendar invites double book |
| FAQ-MATCH-026 | FAQ-027 | data_reporting | Analytics Dashboard | Dashboard data appears stale |
| FAQ-MATCH-027 | FAQ-028 | data_reporting | CRM | CRM export missing records |
| FAQ-MATCH-028 | FAQ-029 | data_reporting | Analytics Dashboard | Dashboard fails to render |
| FAQ-MATCH-029 | FAQ-030 | security_request | Identity Provider | Suspicious login alert |
| FAQ-MATCH-030 | FAQ-031 | security_request | Identity Provider | Standard new hire access request |
| NOFAQ-001 | no_match | billing_account | Billing System | Billing API returns 502 during subscription sync |
| NOFAQ-002 | no_match | software_bug | CRM | Marketplace webhook times out |
| NOFAQ-003 | no_match | security_request | Customer Portal | Vendor entitlement approval fails |
| NOFAQ-004 | no_match | network_connectivity | Billing System | Private endpoint resolves to stale host |
| NOFAQ-005 | no_match | data_reporting | Analytics Dashboard | Forecast widget crashes on custom segment |
| NOFAQ-006 | no_match | software_bug | Inventory App | Barcode scanner duplicates scan events |
| NOFAQ-007 | no_match | billing_account | Billing System | Usage tier rounding is wrong |
| NOFAQ-008 | no_match | login_access | Customer Portal | Login works but wrong tenant opens |
| NOFAQ-009 | no_match | email_calendar | Email | Legal hold banner missing |
| NOFAQ-010 | no_match | security_request | Identity Provider | SCIM deprovisioning lag |
| NOFAQ-011 | no_match | data_reporting | CRM | Pipeline attribution model changed unexpectedly |
| NOFAQ-012 | no_match | software_bug | Customer Portal | Bulk upload partially commits bad rows |
| NOFAQ-013 | no_match | network_connectivity | VPN | VPN client accepts connection but drops device posture claims |
| NOFAQ-014 | no_match | billing_account | Billing System | Credit memo workflow stuck in approval |
| NOFAQ-015 | no_match | hardware_issue | Customer Portal | Loaner laptop cannot enroll in MDM |
| NOFAQ-016 | no_match | other | Customer Portal | Need new workflow for partner approvals |
| NOFAQ-017 | no_match | software_bug | CRM | Mobile CRM saves notes to wrong opportunity |
| NOFAQ-018 | no_match | data_reporting | Analytics Dashboard | Cohort retention chart uses wrong timezone |
| NOFAQ-019 | no_match | security_request | Identity Provider | Conditional access policy excludes contractors |
| NOFAQ-020 | no_match | email_calendar | Email | Executive delegate approval loop |
