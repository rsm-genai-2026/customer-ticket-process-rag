"""Generate synthetic CSV datasets for the human customer-support ticket process.

The generator implements the human ("Current process") workflow described in
``customer-ticket-process.pdf``:

    User -> IT team intake/triage -> FAQ check
        FAQ match  -> IT team drafts/sends FAQ-based message -> customer reply
        no match   -> IT specialist investigates -> IT team relays solution -> customer reply
    Accepted   -> ticket closed, feedback recorded
    Rejected   -> IT team verifies rejection -> (optional) one reopen cycle

All data is synthetic, deterministic for a given ``--seed``, and safe to
distribute. The generator depends only on the standard library, ``numpy``,
and ``polars``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

SIMULATION_START = datetime(2026, 1, 15, 6, 0, 0)
SIMULATION_DAYS = 75

# ---------------------------------------------------------------------------
# Reference dictionaries
# ---------------------------------------------------------------------------

CATEGORY_ROWS = [
    ("CAT001", "login_access", "User cannot sign in to a company system.", "identity_security", 0.70, 0.5, 8.0),
    (
        "CAT002",
        "password_reset",
        "User requests a password reset, unlock, or new credentials.",
        "identity_security",
        0.85,
        0.25,
        4.0,
    ),
    (
        "CAT003",
        "billing_account",
        "Issues with invoices, account billing, taxes, or payment methods.",
        "billing_finance",
        0.40,
        2.0,
        24.0,
    ),
    (
        "CAT004",
        "software_bug",
        "Suspected defect in a company application.",
        "application_engineering",
        0.20,
        4.0,
        72.0,
    ),
    (
        "CAT005",
        "hardware_issue",
        "Hardware fault, peripheral, or end-user device problem.",
        "hardware_field",
        0.25,
        4.0,
        48.0,
    ),
    (
        "CAT006",
        "network_connectivity",
        "VPN, Wi-Fi, or general connectivity reliability.",
        "network_infra",
        0.50,
        1.0,
        24.0,
    ),
    (
        "CAT007",
        "email_calendar",
        "Email, calendar, or scheduling configuration issues.",
        "identity_security",
        0.70,
        0.5,
        8.0,
    ),
    (
        "CAT008",
        "data_reporting",
        "Reporting, analytics dashboards, or data export problems.",
        "data_analytics",
        0.30,
        4.0,
        48.0,
    ),
    (
        "CAT009",
        "security_request",
        "Security review, suspicious activity, or sensitive access request.",
        "identity_security",
        0.35,
        4.0,
        72.0,
    ),
    (
        "CAT010",
        "other",
        "General requests not covered by other categories.",
        "application_engineering",
        0.20,
        4.0,
        72.0,
    ),
]

PRIORITY_ROWS = [
    (
        "low",
        1,
        "Minor inconvenience for a single user.",
        24,
        72,
        "Cosmetic issues, documentation requests, single-user non-blocker.",
    ),
    (
        "medium",
        2,
        "Standard support request affecting one user or a small group.",
        8,
        48,
        "Single user blocked from non-critical workflow, small team partial impact.",
    ),
    (
        "high",
        3,
        "Material impact on a customer team or revenue-impacting workflow.",
        2,
        24,
        "Whole department blocked, customer demo blocker, billing not running.",
    ),
    (
        "urgent",
        4,
        "Critical outage or active security incident.",
        1,
        8,
        "Site outage, suspected breach, payments halted, broad system unavailability.",
    ),
]

SYSTEM_ROWS = [
    ("SYS01", "Customer Portal", "Customer Success", "login_access, password_reset, software_bug"),
    ("SYS02", "Billing System", "Finance Operations", "billing_account, data_reporting"),
    ("SYS03", "CRM", "Revenue Operations", "data_reporting, software_bug, login_access"),
    ("SYS04", "Email", "Workplace IT", "email_calendar"),
    ("SYS05", "VPN", "Network Engineering", "network_connectivity"),
    ("SYS06", "Analytics Dashboard", "Data Platform", "data_reporting, software_bug"),
    ("SYS07", "Inventory App", "Operations", "data_reporting, software_bug"),
    ("SYS08", "Identity Provider", "Security Engineering", "login_access, password_reset, security_request"),
]

STATUS_ROWS = [
    ("submitted", "Ticket has been submitted by the user but not yet picked up.", False),
    ("triaged", "IT team member has classified and prioritized the ticket.", False),
    ("faq_checked", "IT team member has checked the FAQ knowledge base.", False),
    ("response_sent", "An FAQ-based or specialist-based response has been sent to the customer.", False),
    ("escalated", "Ticket has been forwarded to an IT specialist.", False),
    ("specialist_review", "IT specialist is investigating the issue.", False),
    ("specialist_solution_created", "IT specialist has provided a solution back to IT team.", False),
    ("customer_replied", "Customer has replied to the IT team's resolution message.", False),
    ("reopened", "Ticket was rejected, verified, and routed back for further review.", False),
    ("closed", "Ticket is closed and feedback recorded.", True),
]

CATEGORY_TICKET_WEIGHTS = {
    "login_access": 0.18,
    "password_reset": 0.15,
    "billing_account": 0.10,
    "software_bug": 0.10,
    "hardware_issue": 0.07,
    "network_connectivity": 0.12,
    "email_calendar": 0.10,
    "data_reporting": 0.07,
    "security_request": 0.06,
    "other": 0.05,
}

CATEGORY_TO_SYSTEMS = {
    "login_access": ["Customer Portal", "Identity Provider", "CRM"],
    "password_reset": ["Identity Provider", "CRM", "Customer Portal"],
    "billing_account": ["Billing System", "Customer Portal"],
    "software_bug": ["CRM", "Customer Portal", "Inventory App", "Analytics Dashboard", "Billing System"],
    "hardware_issue": ["Customer Portal"],
    "network_connectivity": ["VPN", "Identity Provider"],
    "email_calendar": ["Email"],
    "data_reporting": ["Analytics Dashboard", "CRM", "Inventory App"],
    "security_request": ["Identity Provider"],
    "other": ["Customer Portal"],
}

CATEGORY_TO_GROUP = {row[1]: row[3] for row in CATEGORY_ROWS}

# ---------------------------------------------------------------------------
# Name and text pools
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Alex",
    "Bea",
    "Chen",
    "Dani",
    "Erin",
    "Felix",
    "Grace",
    "Hassan",
    "Imani",
    "Jorge",
    "Kai",
    "Lin",
    "Maya",
    "Nikhil",
    "Olu",
    "Priya",
    "Quinn",
    "Ravi",
    "Sana",
    "Tomo",
    "Uma",
    "Vera",
    "Wren",
    "Xavi",
    "Yuki",
    "Zane",
    "Marta",
    "Iris",
    "Theo",
    "Noor",
]
LAST_NAMES = [
    "Adler",
    "Bose",
    "Chen",
    "Devine",
    "Eskola",
    "Fontaine",
    "Grover",
    "Holm",
    "Ivanov",
    "Jeong",
    "Kapoor",
    "Liang",
    "Mancini",
    "Nair",
    "Okafor",
    "Patel",
    "Quintero",
    "Rao",
    "Singh",
    "Tanaka",
    "Underwood",
    "Vega",
    "Walsh",
    "Xu",
    "Yamada",
    "Zhao",
]
COMPANY_PREFIXES = [
    "Aurora",
    "Northstar",
    "Pacific",
    "Coastal",
    "Granite",
    "Vertex",
    "Lumen",
    "Beacon",
    "Cobalt",
    "Summit",
    "Cypress",
    "Helios",
    "Meridian",
    "Quartz",
    "Polaris",
    "Sable",
    "Ember",
    "Pioneer",
    "Tessera",
    "Ironhill",
]
COMPANY_SUFFIXES = [
    "Logistics",
    "Foods",
    "Marine Solutions",
    "Robotics",
    "Health",
    "Analytics",
    "Bioworks",
    "Energy",
    "Systems",
    "Capital",
    "Manufacturing",
    "Studios",
]
INDUSTRIES = [
    "logistics",
    "manufacturing",
    "healthcare",
    "retail",
    "professional_services",
    "financial_services",
    "media",
    "biotech",
    "energy",
    "education",
]
REGIONS = ["NA-West", "NA-East", "NA-Central", "EU-West", "EU-Central", "APAC-East", "APAC-South", "LATAM"]

SPECIALIST_GROUPS = [
    "identity_security",
    "network_infra",
    "billing_finance",
    "application_engineering",
    "hardware_field",
    "data_analytics",
]

GROUP_TO_SYSTEMS = {
    "identity_security": ["Identity Provider", "Customer Portal", "Email"],
    "network_infra": ["VPN", "Identity Provider"],
    "billing_finance": ["Billing System"],
    "application_engineering": ["CRM", "Customer Portal", "Inventory App", "Analytics Dashboard"],
    "hardware_field": ["Customer Portal"],
    "data_analytics": ["Analytics Dashboard", "CRM", "Inventory App"],
}

# ---------------------------------------------------------------------------
# Ticket text templates
# ---------------------------------------------------------------------------

# Each entry: (subject, description, business_impact). {sys} is replaced.
TICKET_TEMPLATES: dict[str, list[tuple[str, str, str]]] = {
    "login_access": [
        (
            "Cannot log into customer portal",
            "I keep getting an 'invalid credentials' error when I try to sign in to the {sys}, even after typing my password carefully. tried twice already.",
            "One user blocked from logging in",
        ),
        (
            "Stuck in SSO redirect loop",
            "After I click sign in on the {sys} the page just keeps bouncing me back and forth between the SSO screen.",
            "Two team members cannot start the day's work",
        ),
        (
            "MFA prompt never arrives",
            "The MFA code never reaches my phone for {sys}. been waiting 10 min.",
            "User cannot access required system",
        ),
        (
            "Account locked after travel",
            "Getting account-locked errors on {sys} since flying to a new region.",
            "Field rep blocked from CRM during customer visit",
        ),
    ],
    "password_reset": [
        (
            "Need password reset",
            "Please reset my password for the {sys}. I tried the self-service link but it doesn't seem to work.",
            "One user blocked from logging in",
        ),
        (
            "Password reset link expired",
            "The reset link you sent expired before I could use it. need a new one for {sys}.",
            "Customer demo delayed",
        ),
        (
            "Service account password expired",
            "Our automated job failed because the {sys} service account password expired overnight.",
            "Nightly export job failing",
        ),
    ],
    "billing_account": [
        (
            "Invoice total looks wrong",
            "The invoice for last month on {sys} is much higher than expected. I think the tax rate is off.",
            "Finance team holding payment until corrected",
        ),
        (
            "Cannot download invoice PDF",
            "The Download Invoice button on {sys} just spins forever. tried Chrome and Safari.",
            "Customer cannot file expense reports",
        ),
        (
            "Wrong billing region applied",
            "We moved offices last quarter. {sys} is still applying old region pricing.",
            "Finance unable to close the month",
        ),
    ],
    "software_bug": [
        (
            "Records missing after CRM export",
            "Exported a contact list from {sys} this morning and a chunk of records are missing. it definitely had more rows yesterday.",
            "Sales pipeline data inconsistent",
        ),
        (
            "App freezes when opening report",
            "{sys} freezes for ~30 seconds whenever I open the Q4 report. happens every time.",
            "Reporting workflow unusable",
        ),
        (
            "Inventory App shows stale data",
            "The {sys} keeps showing yesterday's stock levels. cleared cache, no luck.",
            "Warehouse team picking against incorrect data",
        ),
        (
            "Saved filter not loading on dashboard",
            "My saved filter on {sys} disappeared overnight. now have to rebuild it from scratch.",
            "Manager dashboard broken before exec review",
        ),
    ],
    "hardware_issue": [
        (
            "Wireless mouse not working",
            "My mouse stopped responding this morning. swapped batteries, no change.",
            "Single user partially blocked",
        ),
        (
            "Printer offline error",
            "The shared printer keeps showing offline even after restart. on the {sys} portal it's listed as online though.",
            "Team cannot print client deliverables",
        ),
        (
            "Laptop battery drains in 1 hour",
            "New laptop's battery only lasts an hour. very urgent, traveling tomorrow.",
            "Travel-blocking hardware issue",
        ),
    ],
    "network_connectivity": [
        (
            "VPN disconnects every few minutes",
            "VPN keeps dropping every 3-5 minutes. happens on the {sys} client. very frustrating.",
            "Remote engineer cannot stay connected to internal tools",
        ),
        (
            "Wi-Fi drops in conference room",
            "Wi-Fi in conf room B keeps dropping connection. {sys} won't reauthorize properly.",
            "Customer demos interrupted",
        ),
        (
            "High latency to internal apps",
            "Everything on {sys} is super slow today. pages that normally load instantly take 20+ seconds.",
            "Whole regional team affected",
        ),
    ],
    "email_calendar": [
        (
            "Email calendar sync issue",
            "My {sys} calendar isn't syncing with my phone since this morning. missed a meeting because of it.",
            "User missed customer meeting",
        ),
        (
            "Out of office not sending",
            "Set up auto-reply on {sys} but my colleagues say they're not getting it.",
            "Stakeholders unaware of leave",
        ),
        (
            "Shared mailbox missing",
            "The shared support mailbox disappeared from my {sys}. it was there last week.",
            "Support intake delayed",
        ),
    ],
    "data_reporting": [
        (
            "Analytics dashboard not loading",
            "{sys} dashboard shows a blank screen with a spinner that never resolves. been like that all morning.",
            "Customer demo delayed because dashboard is down",
        ),
        (
            "CRM export missing records",
            "Pulled a CSV from {sys} for the QBR and a few hundred contacts are missing.",
            "QBR slides cannot be finalized",
        ),
        (
            "Stale numbers in finance dashboard",
            "Numbers on {sys} look like they're from two days ago. monthly close is on Friday.",
            "Monthly close delayed by export issue",
        ),
    ],
    "security_request": [
        (
            "Suspicious login notification",
            "Got an email saying a sign-in to {sys} from a country I've never been to. did anyone else get this?",
            "Possible account compromise",
        ),
        (
            "Need access for new employee",
            "New hire starts Monday, needs {sys} access at the analyst tier.",
            "New hire cannot access required systems",
        ),
        (
            "Vendor needs limited portal access",
            "Auditor visits Wed and needs read-only access to {sys}. please prioritize.",
            "Audit blocked without access",
        ),
    ],
    "other": [
        (
            "General question about software list",
            "Is there an approved-software list anywhere? want to install a tool but don't want to break {sys}.",
            "Productivity blocker for one user",
        ),
        (
            "Need help with a workflow",
            "Not sure who owns this but I can't figure out how to do X in {sys}. Documentation isn't helpful.",
            "User unable to complete task",
        ),
        (
            "Equipment return process",
            "Leaving the company end of month. how do I return my laptop?",
            "Offboarding workflow",
        ),
    ],
}

# ---------------------------------------------------------------------------
# FAQ knowledge base entries
# ---------------------------------------------------------------------------

FAQ_TEMPLATES: list[dict] = [
    # login_access (5)
    {
        "category": "login_access",
        "system_name": "Customer Portal",
        "issue_pattern": "browser_redirect_loop_on_sso",
        "symptoms": "Sign-in page loops between SSO and portal without completing.",
        "solution_steps": "Clear cookies for portal domain, retry sign-in in private window, verify SSO cert is current.",
        "required_customer_info": "Browser, OS, screenshot if possible.",
    },
    {
        "category": "login_access",
        "system_name": "Identity Provider",
        "issue_pattern": "mfa_code_not_received",
        "symptoms": "User reports MFA code SMS or push never arrives.",
        "solution_steps": "Reseed MFA app via self-service portal; if SMS, ask user to update phone number.",
        "required_customer_info": "Username, MFA method on file.",
    },
    {
        "category": "login_access",
        "system_name": "Identity Provider",
        "issue_pattern": "account_locked_after_failed_attempts",
        "symptoms": "User cannot sign in; backend shows account locked.",
        "solution_steps": "Use admin console to unlock account, ask user to wait 5 minutes, retry sign-in.",
        "required_customer_info": "Username and approximate time of lockout.",
    },
    {
        "category": "login_access",
        "system_name": "CRM",
        "issue_pattern": "sso_redirect_unmapped_email",
        "symptoms": "User's email is not mapped in CRM SSO settings.",
        "solution_steps": "Add user to SSO group in CRM admin, retry sign-in.",
        "required_customer_info": "Username and CRM tenant id.",
    },
    {
        "category": "login_access",
        "system_name": "Customer Portal",
        "issue_pattern": "session_token_expired",
        "symptoms": "User sees 'session expired' on every action.",
        "solution_steps": "Ask user to fully sign out, clear browser storage for portal, sign in again.",
        "required_customer_info": "Browser and OS version.",
    },
    # password_reset (5)
    {
        "category": "password_reset",
        "system_name": "Identity Provider",
        "issue_pattern": "self_service_reset_failed",
        "symptoms": "Self-service reset link does not arrive or fails.",
        "solution_steps": "Verify on-file email, resend reset link via admin console, expire any prior tokens.",
        "required_customer_info": "Username, on-file email.",
    },
    {
        "category": "password_reset",
        "system_name": "Identity Provider",
        "issue_pattern": "reset_link_expired",
        "symptoms": "User reports reset link is expired.",
        "solution_steps": "Generate a fresh reset link with a 24-hour TTL and resend.",
        "required_customer_info": "Username.",
    },
    {
        "category": "password_reset",
        "system_name": "Customer Portal",
        "issue_pattern": "service_account_password_expired",
        "symptoms": "Background job fails with auth error after rotation.",
        "solution_steps": "Rotate service account password, update secret in CI/CD, restart job.",
        "required_customer_info": "Service account name.",
    },
    {
        "category": "password_reset",
        "system_name": "CRM",
        "issue_pattern": "policy_block_on_complexity",
        "symptoms": "User cannot pick a new password due to complexity policy.",
        "solution_steps": "Share password rules; suggest passphrase; confirm reset succeeds.",
        "required_customer_info": "Username.",
    },
    {
        "category": "password_reset",
        "system_name": "Identity Provider",
        "issue_pattern": "manager_approval_for_privileged_account",
        "symptoms": "User cannot reset privileged account password without manager approval.",
        "solution_steps": "Request manager email approval, reset with elevated workflow.",
        "required_customer_info": "Account name and manager email.",
    },
    # billing_account (3)
    {
        "category": "billing_account",
        "system_name": "Billing System",
        "issue_pattern": "invoice_pdf_download_fails",
        "symptoms": "Invoice download spins forever.",
        "solution_steps": "Clear browser cache, retry; if still failing, regenerate PDF from billing admin.",
        "required_customer_info": "Invoice number and browser.",
    },
    {
        "category": "billing_account",
        "system_name": "Billing System",
        "issue_pattern": "wrong_tax_rate_after_relocation",
        "symptoms": "Customer moved offices; old tax rate still applied.",
        "solution_steps": "Update billing region in account, re-issue affected invoice with corrected tax.",
        "required_customer_info": "New address and effective date.",
    },
    {
        "category": "billing_account",
        "system_name": "Customer Portal",
        "issue_pattern": "invoice_email_not_received",
        "symptoms": "Customer reports invoice email never arrived.",
        "solution_steps": "Verify recipient in billing settings, resend from admin, check spam folder note.",
        "required_customer_info": "Recipient email and invoice number.",
    },
    # software_bug (2 — intentionally sparse)
    {
        "category": "software_bug",
        "system_name": "Customer Portal",
        "issue_pattern": "stale_cache_after_release",
        "symptoms": "User sees pre-release UI after deploy.",
        "solution_steps": "Hard refresh, clear cache, log back in.",
        "required_customer_info": "Browser version and screenshot.",
    },
    {
        "category": "software_bug",
        "system_name": "Inventory App",
        "issue_pattern": "saved_filter_lost_on_release",
        "symptoms": "User reports saved filter has disappeared.",
        "solution_steps": "Rebuild filter from template; engineering ticket opened for follow-up.",
        "required_customer_info": "Filter name and approximate creation date.",
    },
    # hardware_issue (3)
    {
        "category": "hardware_issue",
        "system_name": "Customer Portal",
        "issue_pattern": "wireless_mouse_battery",
        "symptoms": "Wireless mouse stops responding.",
        "solution_steps": "Replace AA batteries, re-pair via Bluetooth, confirm tracking.",
        "required_customer_info": "Mouse model.",
    },
    {
        "category": "hardware_issue",
        "system_name": "Customer Portal",
        "issue_pattern": "shared_printer_offline",
        "symptoms": "Shared printer reports offline.",
        "solution_steps": "Power cycle printer, clear print queue, reconnect via Print server.",
        "required_customer_info": "Printer name and floor.",
    },
    {
        "category": "hardware_issue",
        "system_name": "Customer Portal",
        "issue_pattern": "headset_audio_quality",
        "symptoms": "Headset audio crackles during calls.",
        "solution_steps": "Update audio driver, swap USB port, replace cable.",
        "required_customer_info": "Headset model and laptop model.",
    },
    # network_connectivity (4)
    {
        "category": "network_connectivity",
        "system_name": "VPN",
        "issue_pattern": "vpn_disconnects_short_intervals",
        "symptoms": "VPN drops every few minutes.",
        "solution_steps": "Update VPN profile, restart client, switch to backup gateway.",
        "required_customer_info": "Region and VPN client version.",
    },
    {
        "category": "network_connectivity",
        "system_name": "VPN",
        "issue_pattern": "vpn_split_tunnel_misconfig",
        "symptoms": "User cannot reach internal apps over VPN.",
        "solution_steps": "Push updated split-tunnel profile, reconnect.",
        "required_customer_info": "Region.",
    },
    {
        "category": "network_connectivity",
        "system_name": "Identity Provider",
        "issue_pattern": "wifi_reauth_conference_room",
        "symptoms": "Conference room AP requires reauth and fails.",
        "solution_steps": "Forget network, reconnect with corp credentials, reauthorize device.",
        "required_customer_info": "Conference room name.",
    },
    {
        "category": "network_connectivity",
        "system_name": "VPN",
        "issue_pattern": "dns_resolver_lag",
        "symptoms": "Slow page loads on internal apps.",
        "solution_steps": "Switch DNS resolver, flush local cache, retry.",
        "required_customer_info": "Region.",
    },
    # email_calendar (4)
    {
        "category": "email_calendar",
        "system_name": "Email",
        "issue_pattern": "calendar_sync_token_expired",
        "symptoms": "Calendar fails to sync to mobile device.",
        "solution_steps": "Reauthorize calendar account on device, force sync, verify recent events appear.",
        "required_customer_info": "Device OS and calendar app.",
    },
    {
        "category": "email_calendar",
        "system_name": "Email",
        "issue_pattern": "out_of_office_disabled",
        "symptoms": "Auto-reply not sending.",
        "solution_steps": "Re-enable auto-reply in mail settings, send test message, verify delivery.",
        "required_customer_info": "Date range and rule preview.",
    },
    {
        "category": "email_calendar",
        "system_name": "Email",
        "issue_pattern": "shared_mailbox_missing",
        "symptoms": "Shared mailbox disappeared from user's profile.",
        "solution_steps": "Re-grant mailbox permission, ask user to restart mail client.",
        "required_customer_info": "Mailbox name.",
    },
    {
        "category": "email_calendar",
        "system_name": "Email",
        "issue_pattern": "calendar_invite_double_booking",
        "symptoms": "Calendar invites double-book on the user's calendar.",
        "solution_steps": "Disable duplicate calendar source, reconnect single source.",
        "required_customer_info": "Calendar source list.",
    },
    # data_reporting (3)
    {
        "category": "data_reporting",
        "system_name": "Analytics Dashboard",
        "issue_pattern": "dashboard_refresh_delay",
        "symptoms": "Dashboard data appears stale.",
        "solution_steps": "Trigger manual refresh, confirm warehouse job ran, communicate ETA.",
        "required_customer_info": "Dashboard name and last expected refresh.",
    },
    {
        "category": "data_reporting",
        "system_name": "CRM",
        "issue_pattern": "export_filter_excludes_records",
        "symptoms": "CRM export missing records.",
        "solution_steps": "Adjust export filter to remove inactive flag, re-run export.",
        "required_customer_info": "Filter id and date range.",
    },
    {
        "category": "data_reporting",
        "system_name": "Analytics Dashboard",
        "issue_pattern": "browser_blocking_analytics_assets",
        "symptoms": "Dashboard fails to render due to blocked third-party assets.",
        "solution_steps": "Whitelist analytics domain in browser, reload page.",
        "required_customer_info": "Browser and any extensions enabled.",
    },
    # security_request (2)
    {
        "category": "security_request",
        "system_name": "Identity Provider",
        "issue_pattern": "suspicious_login_remediation",
        "symptoms": "User reports suspicious login alert.",
        "solution_steps": "Force password reset, reseed MFA, review last 30 days of audit log.",
        "required_customer_info": "Username and report timestamp.",
    },
    {
        "category": "security_request",
        "system_name": "Identity Provider",
        "issue_pattern": "new_hire_access_request_standard_role",
        "symptoms": "Standard new-hire access request.",
        "solution_steps": "Apply standard role bundle, notify manager, set start date.",
        "required_customer_info": "Manager name and start date.",
    },
    # other (2)
    {
        "category": "other",
        "system_name": "Customer Portal",
        "issue_pattern": "approved_software_question",
        "symptoms": "User asks about approved software list.",
        "solution_steps": "Share approved-software wiki link and request submission form for net-new asks.",
        "required_customer_info": "Software name and intended use.",
    },
    {
        "category": "other",
        "system_name": "Customer Portal",
        "issue_pattern": "equipment_return_process",
        "symptoms": "Departing employee asks how to return equipment.",
        "solution_steps": "Share return shipping label workflow and confirm receipt expectation.",
        "required_customer_info": "Last day of employment.",
    },
]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_categories() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "category_id": r[0],
                "category": r[1],
                "description": r[2],
                "default_specialist_group": r[3],
                "faq_coverage_rate": r[4],
                "typical_resolution_hours_min": r[5],
                "typical_resolution_hours_max": r[6],
            }
            for r in CATEGORY_ROWS
        ]
    )


def build_priorities() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "priority": r[0],
                "priority_rank": r[1],
                "description": r[2],
                "target_first_response_hours": r[3],
                "target_resolution_hours": r[4],
                "example_conditions": r[5],
            }
            for r in PRIORITY_ROWS
        ]
    )


def build_systems() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "system_id": r[0],
                "system_name": r[1],
                "business_owner": r[2],
                "common_issue_types": r[3],
            }
            for r in SYSTEM_ROWS
        ]
    )


def build_status_codes() -> pl.DataFrame:
    return pl.DataFrame([{"status": r[0], "description": r[1], "terminal_flag": r[2]} for r in STATUS_ROWS])


def _person_name(rng) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def build_customers(rng, n: int = 18) -> pl.DataFrame:
    rows = []
    used_names: set[str] = set()
    for i in range(1, n + 1):
        while True:
            name = f"{rng.choice(COMPANY_PREFIXES)} {rng.choice(COMPANY_SUFFIXES)}"
            if name not in used_names:
                used_names.add(name)
                break
        tier = str(rng.choice(["standard", "premium", "enterprise"], p=[0.5, 0.35, 0.15]))
        sla_plan = {"standard": "basic", "premium": "business", "enterprise": "critical"}[tier]
        active_users = int(
            {
                "standard": rng.integers(15, 75),
                "premium": rng.integers(75, 400),
                "enterprise": rng.integers(400, 2500),
            }[tier]
        )
        relationship_days_ago = int(rng.integers(60, 365 * 6))
        relationship_start = (SIMULATION_START - timedelta(days=relationship_days_ago)).date().isoformat()
        rows.append(
            {
                "customer_id": f"CUST-{i:03d}",
                "customer_name": name,
                "account_tier": tier,
                "industry": str(rng.choice(INDUSTRIES)),
                "region": str(rng.choice(REGIONS)),
                "sla_plan": sla_plan,
                "active_users": active_users,
                "relationship_start_date": relationship_start,
            }
        )
    return pl.DataFrame(rows)


def build_it_team(rng, n: int = 8) -> pl.DataFrame:
    roles = ["support_analyst"] * 4 + ["senior_support_analyst"] * 3 + ["support_lead"]
    shifts = ["NA-Day", "NA-Evening", "EU-Day", "APAC-Day"]
    rows = []
    for i in range(1, n + 1):
        role = roles[i - 1] if i - 1 < len(roles) else "support_analyst"
        cap = {
            "support_analyst": int(rng.integers(15, 25)),
            "senior_support_analyst": int(rng.integers(20, 30)),
            "support_lead": int(rng.integers(25, 35)),
        }[role]
        rows.append(
            {
                "it_member_id": f"IT-{i:03d}",
                "name": _person_name(rng),
                "role": role,
                "shift": str(rng.choice(shifts)),
                "max_daily_ticket_capacity": cap,
                "quality_score": round(float(rng.uniform(0.78, 0.97)), 2),
            }
        )
    return pl.DataFrame(rows)


def build_specialists(rng, n: int = 10) -> pl.DataFrame:
    # Cover every group at least once
    rows = []
    seniorities = ["junior", "mid", "senior", "principal"]
    seniority_weights = np.array([0.2, 0.4, 0.3, 0.1])
    seniority_weights = seniority_weights / seniority_weights.sum()
    groups = list(SPECIALIST_GROUPS)
    # Ensure each group is represented; fill remainder randomly
    assigned_groups = list(groups)
    while len(assigned_groups) < n:
        assigned_groups.append(str(rng.choice(groups)))
    rng.shuffle(assigned_groups)
    for i in range(1, n + 1):
        group = assigned_groups[i - 1]
        systems = GROUP_TO_SYSTEMS[group]
        n_sys = int(rng.integers(1, len(systems) + 1))
        chosen = list(rng.choice(systems, size=n_sys, replace=False))
        seniority = str(rng.choice(seniorities, p=seniority_weights))
        cap = {"junior": 4, "mid": 6, "senior": 8, "principal": 10}[seniority]
        rows.append(
            {
                "specialist_id": f"SP-{i:03d}",
                "name": _person_name(rng),
                "specialist_group": group,
                "systems_supported": "|".join(chosen),
                "seniority": seniority,
                "max_daily_escalation_capacity": cap,
            }
        )
    return pl.DataFrame(rows)


def build_faqs(rng) -> pl.DataFrame:
    rows = []
    for i, t in enumerate(FAQ_TEMPLATES, start=1):
        last_updated = (SIMULATION_START - timedelta(days=int(rng.integers(15, 720)))).date().isoformat()
        owner = _person_name(rng)
        active = bool(rng.random() > 0.05)  # ~95% active
        rows.append(
            {
                "faq_id": f"FAQ-{i:03d}",
                "category": t["category"],
                "system_name": t["system_name"],
                "issue_pattern": t["issue_pattern"],
                "symptoms": t["symptoms"],
                "solution_steps": t["solution_steps"],
                "required_customer_info": t["required_customer_info"],
                "last_updated": last_updated,
                "owner": owner,
                "active_flag": active,
            }
        )
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# Ticket flow generation
# ---------------------------------------------------------------------------


def _email_from_name(name: str, company: str) -> str:
    domain = company.lower().replace(" ", "") + ".example"
    handle = name.lower().replace(" ", ".")
    return f"{handle}@{domain}"


def _hours(rng, lo: float, hi: float) -> float:
    return float(rng.uniform(lo, hi))


def _bool(rng, p: float) -> bool:
    return bool(rng.random() < p)


def _pick_priority(rng, customer: dict, urgency: str, category: str) -> tuple[str, str]:
    score = 0
    if customer["account_tier"] == "enterprise":
        score += 2
    elif customer["account_tier"] == "premium":
        score += 1
    if urgency == "critical":
        score += 3
    elif urgency == "high":
        score += 2
    elif urgency == "medium":
        score += 1
    if category in ("security_request", "network_connectivity"):
        score += 1
    if category == "password_reset":
        score -= 1
    # noise: occasional over-prioritization
    if rng.random() < 0.10:
        score += 1
    if score >= 5:
        priority = "urgent"
    elif score >= 3:
        priority = "high"
    elif score >= 1:
        priority = "medium"
    else:
        priority = "low"
    reason_bits = [f"tier={customer['account_tier']}", f"urgency={urgency}", f"category={category}"]
    return priority, "; ".join(reason_bits)


def _pick_category(rng) -> str:
    cats = list(CATEGORY_TICKET_WEIGHTS.keys())
    weights = np.array(list(CATEGORY_TICKET_WEIGHTS.values()))
    weights = weights / weights.sum()
    return str(rng.choice(cats, p=weights))


def _pick_urgency(rng, customer: dict) -> str:
    if customer["account_tier"] == "enterprise":
        p = [0.15, 0.40, 0.30, 0.15]
    elif customer["account_tier"] == "premium":
        p = [0.20, 0.45, 0.25, 0.10]
    else:
        p = [0.30, 0.50, 0.15, 0.05]
    return str(rng.choice(["low", "medium", "high", "critical"], p=p))


def _make_ticket_text(rng, category: str, system: str) -> tuple[str, str, str]:
    template = TICKET_TEMPLATES[category][int(rng.integers(0, len(TICKET_TEMPLATES[category])))]
    subject, description, impact = template
    description = description.format(sys=system)
    # Add some human messiness occasionally
    if rng.random() < 0.20:
        description += " please help asap"
    if rng.random() < 0.15:
        description = description.replace("the", "teh", 1)  # typo
    return subject, description, impact


def _ticket_operational_details(rng, category: str, system: str, attachment_flag: bool) -> dict[str, str]:
    symptom = {
        "login_access": f"{system} sign-in fails before the user reaches the app home page.",
        "password_reset": f"Reset workflow for {system} does not complete for the affected account.",
        "billing_account": f"{system} billing record does not match the customer's expected account state.",
        "software_bug": f"{system} behavior is repeatable and differs from yesterday's behavior.",
        "hardware_issue": "Device or peripheral behavior remains faulty after a basic restart.",
        "network_connectivity": f"{system} connectivity is intermittent enough to interrupt work.",
        "email_calendar": f"{system} calendar or mailbox state is not syncing across devices.",
        "data_reporting": f"{system} data output is stale, incomplete, or unavailable.",
        "security_request": f"{system} access or security state needs human review before action.",
        "other": f"The user is unsure which team owns the {system} request.",
    }[category]
    tried = {
        "login_access": "Retyped password, tried a private browser window.",
        "password_reset": "Tried self-service reset link once.",
        "billing_account": "Checked the invoice/account page twice.",
        "software_bug": "Refreshed browser and repeated the action.",
        "hardware_issue": "Restarted the device and checked cables/batteries.",
        "network_connectivity": "Restarted client and retried from the same location.",
        "email_calendar": "Restarted mail/calendar app.",
        "data_reporting": "Refreshed dashboard/export and retried later.",
        "security_request": "Checked with manager or security contact before submitting.",
        "other": "Searched the help center but did not find a clear owner.",
    }[category]
    expected = {
        "login_access": "User can sign in and reach the normal landing page.",
        "password_reset": "User receives a usable reset path and can authenticate.",
        "billing_account": "Billing record matches the customer's current account details.",
        "software_bug": "The application action completes without missing records or freezing.",
        "hardware_issue": "Device works reliably enough for normal work.",
        "network_connectivity": "Connection remains stable for internal systems.",
        "email_calendar": "Mailbox/calendar state syncs consistently.",
        "data_reporting": "Report/dashboard reflects current, complete data.",
        "security_request": "Access/security action is completed with the right approvals.",
        "other": "User receives a clear owner and next step.",
    }[category]
    availability = str(
        rng.choice(
            [
                "today 09:00-11:00 local time",
                "today 13:00-16:00 local time",
                "tomorrow morning local time",
                "any time by email",
            ]
        )
    )
    attachment = (
        str(rng.choice(["screenshot of error", "short screen recording", "export sample", "invoice PDF"]))
        if attachment_flag
        else ""
    )
    return {
        "error_or_symptom_detail": symptom,
        "steps_already_tried": tried,
        "expected_outcome": expected,
        "availability_window": availability,
        "attachment_description": attachment,
    }


def _classify_with_noise(rng, true_category: str) -> tuple[str, float]:
    if rng.random() < 0.05:
        # misclassified
        cats = [c for c in CATEGORY_TICKET_WEIGHTS if c != true_category]
        assigned = str(rng.choice(cats))
        confidence = round(float(rng.uniform(0.45, 0.7)), 2)
    elif rng.random() < 0.15:
        # correctly classified but with some doubt
        assigned = true_category
        confidence = round(float(rng.uniform(0.55, 0.78)), 2)
    else:
        assigned = true_category
        confidence = round(float(rng.uniform(0.78, 0.99)), 2)
    return assigned, confidence


def _select_specialist(rng, specialists: list[dict], category: str) -> dict:
    target_group = CATEGORY_TO_GROUP[category]
    candidates = [s for s in specialists if s["specialist_group"] == target_group]
    if not candidates:
        candidates = specialists
    return candidates[int(rng.integers(0, len(candidates)))]


def _faq_decision(
    rng, category: str, classification_confidence: float, faqs_by_cat: dict
) -> tuple[bool, str | None, float, str]:
    """Return (faq_match_found, faq_id, match_confidence, notes)."""
    cat_faqs = faqs_by_cat.get(category, [])
    coverage = next(r[4] for r in CATEGORY_ROWS if r[1] == category)
    # Effective probability: scale by classification confidence
    p_match = coverage * (0.6 + 0.4 * classification_confidence)
    if not cat_faqs:
        p_match = min(p_match, 0.05)  # without entries, very rare
    found = bool(rng.random() < p_match)
    if found and cat_faqs:
        faq = cat_faqs[int(rng.integers(0, len(cat_faqs)))]
        match_conf = round(float(rng.uniform(0.65, 0.97)), 2)
        notes = f"Matched FAQ pattern: {faq['issue_pattern']}"
        return True, faq["faq_id"], match_conf, notes
    notes = (
        "No matching FAQ found. Will escalate to specialist."
        if not cat_faqs
        else "Searched FAQ knowledge base; no high-confidence match."
    )
    match_conf = round(float(rng.uniform(0.05, 0.45)), 2)
    return False, None, match_conf, notes


def _faq_search_terms(category: str, system: str, subject: str) -> str:
    subject_terms = " ".join(subject.lower().replace("'", "").split()[:5])
    return f"{system.lower()} | {category} | {subject_terms}"


def _candidate_faq_ids(faqs_by_cat: dict, category: str, selected_faq_id: str | None) -> str:
    candidates = [f["faq_id"] for f in faqs_by_cat.get(category, [])[:3]]
    if selected_faq_id and selected_faq_id not in candidates:
        candidates = [selected_faq_id] + candidates[:2]
    return "|".join(candidates)


def _customer_message_text(
    rng,
    source: str,
    faq: dict | None,
    solution_summary: str | None,
    it_member: dict,
    customer_name: str,
    ticket_subject: str,
) -> tuple[str, str]:
    quality = it_member["quality_score"]
    if source == "faq":
        steps = faq["solution_steps"] if faq else "follow standard troubleshooting steps"
        draft = (
            f"Hi {customer_name.split()[0]},\n\n"
            f"Thanks for the ticket regarding '{ticket_subject}'. We've identified this as a known issue "
            f"({faq['issue_pattern'] if faq else 'standard pattern'}). Please try the following steps:\n\n"
            f"{steps}\n\nLet me know if this resolves it.\n\nThanks,\n{it_member['name']}"
        )
    else:
        steps = solution_summary or "see attached steps"
        draft = (
            f"Hi {customer_name.split()[0]},\n\nOur specialist looked into '{ticket_subject}'. "
            f"Recommended fix:\n\n{steps}\n\nLet me know how this lands.\n\nThanks,\n{it_member['name']}"
        )
    sent = draft
    if quality < 0.85:
        # Slightly less polished phrasing
        sent = sent.replace("Thanks for the ticket", "thanks for reaching out").replace("\n\n", "\n")
    return draft, sent


def _customer_action_required(source: str, faq: dict | None, solution_summary: str | None) -> str:
    if source == "faq" and faq:
        return f"Try FAQ steps and provide: {faq['required_customer_info']}"
    if solution_summary:
        return "Apply the specialist fix or confirm whether the IT team should apply it on the user's behalf."
    return "Confirm whether the issue is resolved."


def _generate_solution_text(category: str, system: str, missing_info: bool) -> tuple[str, str, str]:
    base = {
        "login_access": (
            "Permission cache out of sync between SSO and target system",
            "Force a refresh of the user's SSO group membership and clear server-side session cache.",
        ),
        "password_reset": (
            "Edge-case in privileged-account reset workflow",
            "Run elevated reset workflow with ticket-approval token; confirm sign-in.",
        ),
        "billing_account": (
            "Tax engine using stale region cache",
            "Refresh tax engine cache for affected account; reissue invoice with corrected line items.",
        ),
        "software_bug": (
            "Race condition in background sync job",
            "Apply temporary mitigation (re-queue affected records); engineering ticket logged for permanent fix.",
        ),
        "hardware_issue": (
            "Driver mismatch with current OS image",
            "Push updated driver via MDM and confirm device reports healthy.",
        ),
        "network_connectivity": (
            "Misconfigured route on regional gateway",
            "Apply corrected route table entry and validate user can reach internal hosts.",
        ),
        "email_calendar": (
            "Stale OAuth token in calendar provider",
            "Revoke and reissue OAuth token; ask user to reauthorize on device.",
        ),
        "data_reporting": (
            "Warehouse job missed scheduled run",
            "Manually trigger affected job and adjust scheduler to avoid recurrence.",
        ),
        "security_request": (
            "Manual access review required for sensitive role",
            "Open access review, obtain manager and security approval, grant scoped role.",
        ),
        "other": (
            "Cross-team request requiring case-by-case handling",
            "Coordinate with requested team and provide tailored next steps.",
        ),
    }
    root, summary = base.get(category, ("Investigation pending", "Apply general workaround and follow up."))
    notes = f"Investigated on {system}. "
    if missing_info:
        notes += (
            "Initial information from IT team was incomplete; specialist requested clarification mid-investigation."
        )
    else:
        notes += "Sufficient information provided up front."
    return root, summary, notes


def _add_event(
    events: list[dict], ticket_id: str, ts: datetime, etype: str, actor_type: str, actor_id: str, notes: str = ""
) -> None:
    events.append(
        {
            "event_id": f"EVT-{len(events) + 1:07d}",
            "ticket_id": ticket_id,
            "event_time": ts.isoformat(timespec="seconds"),
            "event_type": etype,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "event_notes": notes,
        }
    )


def build_ticket_flows(
    rng,
    n: int,
    customers: pl.DataFrame,
    it_team: pl.DataFrame,
    specialists: pl.DataFrame,
    faqs: pl.DataFrame,
) -> dict[str, list[dict]]:
    customers_l = customers.to_dicts()
    it_team_l = it_team.to_dicts()
    specialists_l = specialists.to_dicts()
    faqs_l = faqs.to_dicts()

    faqs_by_cat: dict[str, list[dict]] = {}
    for f in faqs_l:
        if f["active_flag"]:
            faqs_by_cat.setdefault(f["category"], []).append(f)

    out: dict[str, list[dict]] = {
        "submitted_tickets": [],
        "ticket_triage": [],
        "faq_checks": [],
        "specialist_escalations": [],
        "specialist_investigations": [],
        "customer_messages": [],
        "resolution_feedback": [],
        "ticket_lifecycle_events": [],
        "ticket_summary": [],
    }
    msg_counter = 0

    for i in range(1, n + 1):
        ticket_id = f"TKT-{i:05d}"
        customer = customers_l[int(rng.integers(0, len(customers_l)))]

        submit_offset_min = int(rng.integers(0, SIMULATION_DAYS * 24 * 60))
        submitted_at = SIMULATION_START + timedelta(minutes=submit_offset_min)

        true_category = _pick_category(rng)
        system = str(rng.choice(CATEGORY_TO_SYSTEMS[true_category]))
        urgency = _pick_urgency(rng, customer)
        subject, description, impact = _make_ticket_text(rng, true_category, system)
        channel = str(rng.choice(["portal", "email", "phone"], p=[0.6, 0.3, 0.1]))
        submitted_by = _person_name(rng)
        attachment_flag = _bool(rng, 0.25)
        operational_details = _ticket_operational_details(rng, true_category, system, attachment_flag)
        out["submitted_tickets"].append(
            {
                "ticket_id": ticket_id,
                "submitted_at": submitted_at.isoformat(timespec="seconds"),
                "customer_id": customer["customer_id"],
                "submitted_by_name": submitted_by,
                "submitted_by_email": _email_from_name(submitted_by, customer["customer_name"]),
                "channel": channel,
                "subject": subject,
                "description": description,
                "affected_system": system,
                "customer_reported_urgency": urgency,
                "business_impact_text": impact,
                "attachment_flag": attachment_flag,
                **operational_details,
            }
        )
        _add_event(
            out["ticket_lifecycle_events"],
            ticket_id,
            submitted_at,
            "ticket_submitted",
            "customer",
            customer["customer_id"],
            f"channel={channel}",
        )

        # Triage
        it_member = it_team_l[int(rng.integers(0, len(it_team_l)))]
        triage_lag_h = _hours(rng, 0.25, 6.0)
        triaged_at = submitted_at + timedelta(hours=triage_lag_h)
        assigned_category, classification_confidence = _classify_with_noise(rng, true_category)
        priority, priority_reason = _pick_priority(rng, customer, urgency, assigned_category)
        sla_rule = next(r for r in PRIORITY_ROWS if r[0] == priority)
        target_first_response_at = submitted_at + timedelta(hours=sla_rule[3])
        target_resolution_at = submitted_at + timedelta(hours=sla_rule[4])
        triage_summary = (
            f"{submitted_by} at {customer['customer_name']} reports {subject.lower()} in {system}; "
            f"impact: {impact}."
        )
        classification_evidence = (
            f"subject='{subject}'; system='{system}'; symptom='{operational_details['error_or_symptom_detail']}'"
        )
        recommended_specialist_group = CATEGORY_TO_GROUP[assigned_category]
        needs_specialist_review = _bool(
            rng,
            0.35
            if assigned_category in ("software_bug", "security_request")
            else (0.25 if priority in ("high", "urgent") else 0.10),
        )
        out["ticket_triage"].append(
            {
                "ticket_id": ticket_id,
                "triaged_at": triaged_at.isoformat(timespec="seconds"),
                "it_member_id": it_member["it_member_id"],
                "assigned_category": assigned_category,
                "assigned_priority": priority,
                "priority_reason": priority_reason,
                "classification_confidence": classification_confidence,
                "needs_specialist_review_flag": needs_specialist_review,
                "intake_summary": triage_summary,
                "classification_evidence": classification_evidence,
                "recommended_specialist_group": recommended_specialist_group,
                "target_first_response_at": target_first_response_at.isoformat(timespec="seconds"),
                "target_resolution_at": target_resolution_at.isoformat(timespec="seconds"),
            }
        )
        _add_event(
            out["ticket_lifecycle_events"],
            ticket_id,
            triaged_at,
            "ticket_triaged",
            "it_team",
            it_member["it_member_id"],
            f"category={assigned_category}; priority={priority}",
        )

        # FAQ check
        faq_lag_h = _hours(rng, 0.1, 2.0)
        faq_checked_at = triaged_at + timedelta(hours=faq_lag_h)
        faq_match, faq_id, match_conf, faq_notes = _faq_decision(
            rng, assigned_category, classification_confidence, faqs_by_cat
        )
        search_terms = _faq_search_terms(assigned_category, system, subject)
        candidate_faq_ids = _candidate_faq_ids(faqs_by_cat, assigned_category, faq_id)
        required_info_available = bool(
            faq_match and (operational_details["attachment_description"] or operational_details["steps_already_tried"])
        )
        faq_application_reason = (
            "FAQ symptoms and required context appear sufficient for first response."
            if faq_match and required_info_available
            else (
                "FAQ may match, but available context is incomplete; specialist review may still be needed."
                if faq_match
                else "No FAQ candidate explains the reported symptom with enough confidence."
            )
        )
        # If confidence is low or specialist review requested, force escalate even if FAQ found
        force_escalate = needs_specialist_review or (faq_match and match_conf < 0.55)
        out["faq_checks"].append(
            {
                "ticket_id": ticket_id,
                "faq_checked_at": faq_checked_at.isoformat(timespec="seconds"),
                "it_member_id": it_member["it_member_id"],
                "faq_match_found": faq_match,
                "faq_id": faq_id if faq_match else "",
                "match_confidence": match_conf,
                "faq_match_notes": faq_notes,
                "search_terms": search_terms,
                "candidate_faq_ids": candidate_faq_ids,
                "required_customer_info_available": required_info_available,
                "faq_application_reason": faq_application_reason,
            }
        )
        _add_event(
            out["ticket_lifecycle_events"],
            ticket_id,
            faq_checked_at,
            "faq_checked",
            "it_team",
            it_member["it_member_id"],
            f"match={faq_match}; confidence={match_conf}",
        )

        first_path_uses_specialist = (not faq_match) or force_escalate
        chosen_faq = next((f for f in faqs_l if f["faq_id"] == faq_id), None) if faq_match else None

        # ----- First resolution attempt -----
        spec_escalations: list[tuple[datetime, dict, bool]] = []  # (ts, specialist, missing_info)
        spec_investigations: list[tuple[datetime, datetime, dict, bool]] = []  # (start, end, specialist, missing_info)

        last_event_ts = faq_checked_at
        if first_path_uses_specialist:
            esc_lag_h = _hours(rng, 0.1, 1.5)
            escalated_at = faq_checked_at + timedelta(hours=esc_lag_h)
            specialist = _select_specialist(rng, specialists_l, assigned_category)
            missing_info = _bool(rng, 0.20)
            esc_reason = (
                "needs specialist review per triage flag"
                if needs_specialist_review
                else ("low FAQ match confidence" if faq_match else "no FAQ match found")
            )
            info_provided = (
                "subject + description + business impact + affected system + urgency + steps already tried"
                + (" + attachment" if attachment_flag else "")
                + ("; missing reproduction steps" if missing_info else "")
            )
            handoff_summary = (
                f"{subject} in {system}; category={assigned_category}; priority={priority}; "
                f"customer tried: {operational_details['steps_already_tried']}; impact: {impact}."
            )
            specific_question = (
                f"Please identify root cause and provide customer-safe resolution steps for {system}."
            )
            evidence_included = "|".join(
                [
                    "ticket_description",
                    "business_impact",
                    "steps_already_tried",
                    "attachment" if attachment_flag else "no_attachment",
                    "faq_search_notes",
                ]
            )
            out["specialist_escalations"].append(
                {
                    "ticket_id": ticket_id,
                    "escalated_at": escalated_at.isoformat(timespec="seconds"),
                    "it_member_id": it_member["it_member_id"],
                    "specialist_id": specialist["specialist_id"],
                    "escalation_reason": esc_reason,
                    "information_provided": info_provided,
                    "missing_information_flag": missing_info,
                    "requested_specialist_group": recommended_specialist_group,
                    "handoff_summary": handoff_summary,
                    "specific_question_for_specialist": specific_question,
                    "customer_evidence_included": evidence_included,
                }
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                escalated_at,
                "ticket_escalated",
                "it_team",
                it_member["it_member_id"],
                f"to specialist {specialist['specialist_id']}; missing_info={missing_info}",
            )
            spec_escalations.append((escalated_at, specialist, missing_info))

            inv_started_at = escalated_at + timedelta(hours=_hours(rng, 0.5, 6.0))
            cat_min = next(r[5] for r in CATEGORY_ROWS if r[1] == assigned_category)
            cat_max = next(r[6] for r in CATEGORY_ROWS if r[1] == assigned_category)
            seniority_factor = {"junior": 1.3, "mid": 1.0, "senior": 0.8, "principal": 0.65}[specialist["seniority"]]
            priority_factor = {"low": 1.2, "medium": 1.0, "high": 0.8, "urgent": 0.55}[priority]
            base = _hours(rng, cat_min, cat_max)
            extra = base * 0.5 if missing_info else 0.0
            inv_hours = max(0.5, base * seniority_factor * priority_factor + extra)
            solution_created_at = inv_started_at + timedelta(hours=inv_hours)
            root, summary, sp_notes = _generate_solution_text(assigned_category, system, missing_info)
            diagnostic_steps = (
                f"Reviewed ticket symptoms; checked {system} health/logs; compared against known {assigned_category} patterns."
            )
            evidence_reviewed = "ticket_description|business_impact|faq_search_notes|system_health"
            customer_action_required = (
                "User must retry workflow and confirm result; IT may need to apply admin-side change first."
            )
            out["specialist_investigations"].append(
                {
                    "ticket_id": ticket_id,
                    "specialist_id": specialist["specialist_id"],
                    "investigation_started_at": inv_started_at.isoformat(timespec="seconds"),
                    "solution_created_at": solution_created_at.isoformat(timespec="seconds"),
                    "root_cause": root,
                    "solution_summary": summary,
                    "specialist_notes": sp_notes,
                    "requires_follow_up_flag": _bool(rng, 0.15),
                    "diagnostic_steps": diagnostic_steps,
                    "evidence_reviewed": evidence_reviewed,
                    "customer_action_required": customer_action_required,
                    "confidence_score": round(float(rng.uniform(0.72, 0.96)), 2),
                }
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                inv_started_at,
                "specialist_investigation_started",
                "it_specialist",
                specialist["specialist_id"],
                "",
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                solution_created_at,
                "specialist_solution_created",
                "it_specialist",
                specialist["specialist_id"],
                f"root_cause={root}",
            )
            spec_investigations.append((inv_started_at, solution_created_at, specialist, missing_info))
            last_event_ts = solution_created_at
            message_source = "specialist_solution"
            solution_summary_for_msg = summary
        else:
            message_source = "faq"
            solution_summary_for_msg = None

        # Drafted/sent
        draft_lag_h = _hours(rng, 0.1, 1.5)
        message_drafted_at = last_event_ts + timedelta(hours=draft_lag_h)
        send_lag_h = _hours(rng, 0.05, 0.75)
        message_sent_at = message_drafted_at + timedelta(hours=send_lag_h)
        draft_text, sent_text = _customer_message_text(
            rng,
            message_source,
            chosen_faq,
            solution_summary_for_msg,
            it_member,
            submitted_by,
            subject,
        )
        customer_action = _customer_action_required(message_source, chosen_faq, solution_summary_for_msg)
        msg_counter += 1
        out["customer_messages"].append(
            {
                "message_id": f"MSG-{msg_counter:06d}",
                "ticket_id": ticket_id,
                "message_created_at": message_drafted_at.isoformat(timespec="seconds"),
                "it_member_id": it_member["it_member_id"],
                "message_source": message_source,
                "draft_text": draft_text,
                "sent_text": sent_text,
                "sent_at": message_sent_at.isoformat(timespec="seconds"),
                "customer_action_required": customer_action,
                "included_context": "issue summary|resolution steps|request to confirm outcome",
                "follow_up_request": "Reply with whether the issue is resolved and include any remaining error details.",
                "quality_check_notes": (
                    "Includes customer-safe steps and confirmation request."
                    if it_member["quality_score"] >= 0.85
                    else "Message is brief; review tone and specificity before reuse."
                ),
            }
        )
        if message_source == "faq":
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                message_drafted_at,
                "faq_response_drafted",
                "it_team",
                it_member["it_member_id"],
                "",
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                message_sent_at,
                "faq_response_sent",
                "it_team",
                it_member["it_member_id"],
                "",
            )
        else:
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                message_drafted_at,
                "specialist_response_drafted",
                "it_team",
                it_member["it_member_id"],
                "",
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                message_sent_at,
                "specialist_response_sent",
                "it_team",
                it_member["it_member_id"],
                "",
            )

        # Customer reply
        reply_lag_h = _hours(rng, 4.0, 60.0)
        customer_reply_at = message_sent_at + timedelta(hours=reply_lag_h)

        reject_p = 0.15
        if classification_confidence < 0.6:
            reject_p += 0.15
        if message_source == "faq" and match_conf < 0.7:
            reject_p += 0.10
        if first_path_uses_specialist and spec_escalations and spec_escalations[-1][2]:
            reject_p += 0.08
        if priority == "urgent":
            reject_p += 0.05
        reject_p = min(reject_p, 0.55)

        accepted_first = not _bool(rng, reject_p)
        reopened = False
        verified_rejection = False
        rejection_reason = ""
        feedback_text = ""

        if accepted_first:
            feedback_text = str(
                rng.choice(
                    [
                        "Worked perfectly, thanks!",
                        "All set — appreciate the quick turnaround.",
                        "Resolved. Closing the loop on our end.",
                        "Confirmed fixed. Thank you.",
                    ]
                )
            )
            closed_at = customer_reply_at + timedelta(hours=_hours(rng, 0.1, 2.0))
            out["resolution_feedback"].append(
                {
                    "ticket_id": ticket_id,
                    "customer_reply_at": customer_reply_at.isoformat(timespec="seconds"),
                    "resolution_accepted": True,
                    "customer_feedback_text": feedback_text,
                    "rejection_reason": "",
                    "verified_rejection": False,
                    "reopened_flag": False,
                    "closed_at": closed_at.isoformat(timespec="seconds"),
                    "verified_by_it_member_id": "",
                    "verification_notes": "Customer confirmed resolution; no rejection verification needed.",
                    "next_action": "close_ticket",
                    "closure_reason": "accepted_by_customer",
                }
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                customer_reply_at,
                "customer_resolution_accepted",
                "customer",
                customer["customer_id"],
                "",
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                closed_at,
                "ticket_closed",
                "it_team",
                it_member["it_member_id"],
                "accepted_first_attempt",
            )
            final_accepted = True
            final_specialist_id = spec_escalations[-1][1]["specialist_id"] if spec_escalations else ""
            final_closed_at = closed_at
        else:
            verified_rejection = True
            rejection_reason = str(
                rng.choice(
                    [
                        "Steps did not resolve the issue",
                        "Issue recurred after a few hours",
                        "Solution applied but symptom persists",
                        "Need different approach",
                    ]
                )
            )
            feedback_text = "Tried the steps but the problem is still happening."
            verify_at = customer_reply_at + timedelta(hours=_hours(rng, 0.5, 4.0))
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                customer_reply_at,
                "customer_resolution_rejected",
                "customer",
                customer["customer_id"],
                rejection_reason,
            )
            _add_event(
                out["ticket_lifecycle_events"],
                ticket_id,
                verify_at,
                "rejection_verified",
                "it_team",
                it_member["it_member_id"],
                "",
            )
            reopen = _bool(rng, 0.70)
            if reopen:
                reopened = True
                reopen_at = verify_at + timedelta(hours=_hours(rng, 0.25, 2.0))
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    reopen_at,
                    "ticket_reopened",
                    "it_team",
                    it_member["it_member_id"],
                    "",
                )
                # First feedback row (rejected, not closed)
                out["resolution_feedback"].append(
                    {
                        "ticket_id": ticket_id,
                        "customer_reply_at": customer_reply_at.isoformat(timespec="seconds"),
                        "resolution_accepted": False,
                        "customer_feedback_text": feedback_text,
                        "rejection_reason": rejection_reason,
                        "verified_rejection": True,
                        "reopened_flag": True,
                        "closed_at": "",
                        "verified_by_it_member_id": it_member["it_member_id"],
                        "verification_notes": (
                            "IT reviewed the customer's reply and confirmed the symptom is still in scope."
                        ),
                        "next_action": "reopen_and_escalate",
                        "closure_reason": "pending_reopened_review",
                    }
                )
                # Reopen routes to specialist (always)
                specialist_2 = _select_specialist(rng, specialists_l, assigned_category)
                esc2_at = reopen_at + timedelta(hours=_hours(rng, 0.25, 2.0))
                missing_info_2 = _bool(rng, 0.10)
                out["specialist_escalations"].append(
                    {
                        "ticket_id": ticket_id,
                        "escalated_at": esc2_at.isoformat(timespec="seconds"),
                        "it_member_id": it_member["it_member_id"],
                        "specialist_id": specialist_2["specialist_id"],
                        "escalation_reason": "re-escalation after rejection",
                        "information_provided": "original ticket + first solution + customer feedback",
                        "missing_information_flag": missing_info_2,
                        "requested_specialist_group": recommended_specialist_group,
                        "handoff_summary": (
                            f"Customer rejected first response for {subject}; rejection='{rejection_reason}'."
                        ),
                        "specific_question_for_specialist": (
                            "Review why the first resolution failed and provide corrected customer-safe steps."
                        ),
                        "customer_evidence_included": "original_ticket|first_response|customer_rejection",
                    }
                )
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    esc2_at,
                    "ticket_escalated",
                    "it_team",
                    it_member["it_member_id"],
                    f"to specialist {specialist_2['specialist_id']}; reopen=True",
                )

                inv2_started = esc2_at + timedelta(hours=_hours(rng, 0.5, 4.0))
                cat_min = next(r[5] for r in CATEGORY_ROWS if r[1] == assigned_category)
                cat_max = next(r[6] for r in CATEGORY_ROWS if r[1] == assigned_category)
                seniority_factor = {"junior": 1.2, "mid": 0.95, "senior": 0.75, "principal": 0.6}[
                    specialist_2["seniority"]
                ]
                priority_factor = {"low": 1.1, "medium": 0.9, "high": 0.7, "urgent": 0.5}[priority]
                inv2_hours = max(0.5, _hours(rng, cat_min, cat_max) * seniority_factor * priority_factor)
                if missing_info_2:
                    inv2_hours *= 1.4
                solution2_at = inv2_started + timedelta(hours=inv2_hours)
                root2, summary2, notes2 = _generate_solution_text(assigned_category, system, missing_info_2)
                diagnostic_steps2 = (
                    f"Compared first solution with customer rejection; reviewed deeper {system} diagnostics."
                )
                out["specialist_investigations"].append(
                    {
                        "ticket_id": ticket_id,
                        "specialist_id": specialist_2["specialist_id"],
                        "investigation_started_at": inv2_started.isoformat(timespec="seconds"),
                        "solution_created_at": solution2_at.isoformat(timespec="seconds"),
                        "root_cause": root2 + " (post-rejection deeper review)",
                        "solution_summary": summary2 + " Adjusted based on customer feedback.",
                        "specialist_notes": notes2 + " Follow-up after first attempt rejected.",
                        "requires_follow_up_flag": False,
                        "diagnostic_steps": diagnostic_steps2,
                        "evidence_reviewed": "original_ticket|first_solution|customer_rejection|system_health",
                        "customer_action_required": "Retry corrected steps and confirm whether the symptom is gone.",
                        "confidence_score": round(float(rng.uniform(0.76, 0.98)), 2),
                    }
                )
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    inv2_started,
                    "specialist_investigation_started",
                    "it_specialist",
                    specialist_2["specialist_id"],
                    "post_reopen",
                )
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    solution2_at,
                    "specialist_solution_created",
                    "it_specialist",
                    specialist_2["specialist_id"],
                    "post_reopen",
                )

                draft2_at = solution2_at + timedelta(hours=_hours(rng, 0.1, 1.5))
                send2_at = draft2_at + timedelta(hours=_hours(rng, 0.05, 0.75))
                draft_text2, sent_text2 = _customer_message_text(
                    rng,
                    "specialist_solution",
                    None,
                    summary2,
                    it_member,
                    submitted_by,
                    subject,
                )
                msg_counter += 1
                out["customer_messages"].append(
                    {
                        "message_id": f"MSG-{msg_counter:06d}",
                        "ticket_id": ticket_id,
                        "message_created_at": draft2_at.isoformat(timespec="seconds"),
                        "it_member_id": it_member["it_member_id"],
                        "message_source": "specialist_solution",
                        "draft_text": draft_text2,
                        "sent_text": sent_text2,
                        "sent_at": send2_at.isoformat(timespec="seconds"),
                        "customer_action_required": _customer_action_required(
                            "specialist_solution", None, summary2
                        ),
                        "included_context": "rejection acknowledgement|corrected specialist steps|confirmation request",
                        "follow_up_request": "Reply after retrying the corrected steps.",
                        "quality_check_notes": "Acknowledges first attempt and explains corrected next step.",
                    }
                )
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    draft2_at,
                    "specialist_response_drafted",
                    "it_team",
                    it_member["it_member_id"],
                    "post_reopen",
                )
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    send2_at,
                    "specialist_response_sent",
                    "it_team",
                    it_member["it_member_id"],
                    "post_reopen",
                )

                reply2_at = send2_at + timedelta(hours=_hours(rng, 4.0, 60.0))
                accepted_second = _bool(rng, 0.75)
                if accepted_second:
                    feedback2 = "Confirmed fixed after the deeper investigation. Thanks for the persistence."
                    closed2_at = reply2_at + timedelta(hours=_hours(rng, 0.1, 2.0))
                    out["resolution_feedback"].append(
                        {
                            "ticket_id": ticket_id,
                            "customer_reply_at": reply2_at.isoformat(timespec="seconds"),
                            "resolution_accepted": True,
                            "customer_feedback_text": feedback2,
                            "rejection_reason": "",
                            "verified_rejection": False,
                            "reopened_flag": False,
                            "closed_at": closed2_at.isoformat(timespec="seconds"),
                            "verified_by_it_member_id": "",
                            "verification_notes": "Customer confirmed resolution after reopened specialist review.",
                            "next_action": "close_ticket",
                            "closure_reason": "accepted_after_reopen",
                        }
                    )
                    _add_event(
                        out["ticket_lifecycle_events"],
                        ticket_id,
                        reply2_at,
                        "customer_resolution_accepted",
                        "customer",
                        customer["customer_id"],
                        "post_reopen",
                    )
                    _add_event(
                        out["ticket_lifecycle_events"],
                        ticket_id,
                        closed2_at,
                        "ticket_closed",
                        "it_team",
                        it_member["it_member_id"],
                        "accepted_after_reopen",
                    )
                    final_accepted = True
                    final_closed_at = closed2_at
                else:
                    rejection_reason2 = "Issue still persists after re-investigation"
                    closed2_at = reply2_at + timedelta(hours=_hours(rng, 1.0, 8.0))
                    out["resolution_feedback"].append(
                        {
                            "ticket_id": ticket_id,
                            "customer_reply_at": reply2_at.isoformat(timespec="seconds"),
                            "resolution_accepted": False,
                            "customer_feedback_text": "Not fixed yet — escalated to vendor on our side.",
                            "rejection_reason": rejection_reason2,
                            "verified_rejection": True,
                            "reopened_flag": False,
                            "closed_at": closed2_at.isoformat(timespec="seconds"),
                            "verified_by_it_member_id": it_member["it_member_id"],
                            "verification_notes": (
                                "IT verified continued failure and closed as unresolved with vendor-side follow-up."
                            ),
                            "next_action": "close_unresolved_vendor_followup",
                            "closure_reason": "unresolved_after_reopen",
                        }
                    )
                    _add_event(
                        out["ticket_lifecycle_events"],
                        ticket_id,
                        reply2_at,
                        "customer_resolution_rejected",
                        "customer",
                        customer["customer_id"],
                        "post_reopen",
                    )
                    _add_event(
                        out["ticket_lifecycle_events"],
                        ticket_id,
                        closed2_at,
                        "ticket_closed",
                        "it_team",
                        it_member["it_member_id"],
                        "closed_unresolved_after_reopen",
                    )
                    final_accepted = False
                    final_closed_at = closed2_at
                final_specialist_id = specialist_2["specialist_id"]
            else:
                # Verified rejection but no reopen — close as unresolved.
                closed_at = verify_at + timedelta(hours=_hours(rng, 0.5, 6.0))
                out["resolution_feedback"].append(
                    {
                        "ticket_id": ticket_id,
                        "customer_reply_at": customer_reply_at.isoformat(timespec="seconds"),
                        "resolution_accepted": False,
                        "customer_feedback_text": feedback_text,
                        "rejection_reason": rejection_reason,
                        "verified_rejection": True,
                        "reopened_flag": False,
                        "closed_at": closed_at.isoformat(timespec="seconds"),
                        "verified_by_it_member_id": it_member["it_member_id"],
                        "verification_notes": (
                            "IT verified rejection but did not reopen because no actionable internal path remained."
                        ),
                        "next_action": "close_unresolved",
                        "closure_reason": "unresolved_no_reopen",
                    }
                )
                _add_event(
                    out["ticket_lifecycle_events"],
                    ticket_id,
                    closed_at,
                    "ticket_closed",
                    "it_team",
                    it_member["it_member_id"],
                    "closed_unresolved_no_reopen",
                )
                final_accepted = False
                final_specialist_id = spec_escalations[-1][1]["specialist_id"] if spec_escalations else ""
                final_closed_at = closed_at

        # Time metrics
        first_response_h = (message_sent_at - submitted_at).total_seconds() / 3600.0
        triage_h = (triaged_at - submitted_at).total_seconds() / 3600.0
        resolution_h = (final_closed_at - submitted_at).total_seconds() / 3600.0
        sla_first = next(r for r in PRIORITY_ROWS if r[0] == priority)
        sla_first_target = sla_first[3]
        sla_resolution_target = sla_first[4]
        out["ticket_summary"].append(
            {
                "ticket_id": ticket_id,
                "customer_id": customer["customer_id"],
                "submitted_at": submitted_at.isoformat(timespec="seconds"),
                "closed_at": final_closed_at.isoformat(timespec="seconds"),
                "final_status": "closed",
                "assigned_category": assigned_category,
                "true_category": true_category,
                "assigned_priority": priority,
                "affected_system": system,
                "faq_match_found": faq_match,
                "escalated_flag": bool(spec_escalations),
                "specialist_id": final_specialist_id,
                "resolution_accepted": final_accepted,
                "reopened_flag": reopened,
                "first_resolution_source": message_source,
                "time_to_triage_hours": round(triage_h, 2),
                "time_to_first_response_hours": round(first_response_h, 2),
                "time_to_resolution_hours": round(resolution_h, 2),
                "sla_met_first_response": first_response_h <= sla_first_target,
                "sla_met_resolution": resolution_h <= sla_resolution_target,
            }
        )

    # Sort lifecycle events deterministically: by ticket_id then time
    out["ticket_lifecycle_events"].sort(key=lambda e: (e["ticket_id"], e["event_time"]))
    # Renumber event_id after sorting
    for idx, ev in enumerate(out["ticket_lifecycle_events"], start=1):
        ev["event_id"] = f"EVT-{idx:07d}"

    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_dataframe(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--n-tickets", type=int, default=250)
    parser.add_argument("--seed", type=int, default=49502)
    parser.add_argument("--out-dir", type=str, default="data")
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    out_dir = Path(args.out_dir).resolve()
    raw = out_dir / "raw"
    proc = out_dir / "processed"
    dicts = out_dir / "dictionaries"
    for p in (raw, proc, dicts):
        p.mkdir(parents=True, exist_ok=True)

    cat_df = build_categories()
    pri_df = build_priorities()
    sys_df = build_systems()
    sta_df = build_status_codes()
    customers_df = build_customers(rng)
    it_team_df = build_it_team(rng)
    specialists_df = build_specialists(rng)
    faq_df = build_faqs(rng)

    flows = build_ticket_flows(rng, args.n_tickets, customers_df, it_team_df, specialists_df, faq_df)

    # Dictionaries
    write_dataframe(cat_df, dicts / "categories.csv")
    write_dataframe(pri_df, dicts / "priority_rules.csv")
    write_dataframe(sys_df, dicts / "systems.csv")
    write_dataframe(sta_df, dicts / "status_codes.csv")

    # Master / raw
    write_dataframe(customers_df, raw / "customers.csv")
    write_dataframe(it_team_df, raw / "it_team_members.csv")
    write_dataframe(specialists_df, raw / "it_specialists.csv")
    write_dataframe(faq_df, raw / "faq_knowledge_base.csv")
    write_dataframe(pl.DataFrame(flows["submitted_tickets"]), raw / "submitted_tickets.csv")

    # Processed
    proc_tables = {
        "ticket_triage": flows["ticket_triage"],
        "faq_checks": flows["faq_checks"],
        "specialist_escalations": flows["specialist_escalations"],
        "specialist_investigations": flows["specialist_investigations"],
        "customer_messages": flows["customer_messages"],
        "resolution_feedback": flows["resolution_feedback"],
        "ticket_lifecycle_events": flows["ticket_lifecycle_events"],
        "ticket_summary": flows["ticket_summary"],
    }
    for name, rows in proc_tables.items():
        if rows:
            df = pl.DataFrame(rows)
        else:
            df = pl.DataFrame(schema={"ticket_id": pl.Utf8})
        write_dataframe(df, proc / f"{name}.csv")

    # Summary
    n = args.n_tickets
    summary = flows["ticket_summary"]
    n_escalated = sum(1 for r in summary if r["escalated_flag"])
    n_faq_match = sum(1 for r in summary if r["faq_match_found"])
    n_accepted = sum(1 for r in summary if r["resolution_accepted"])
    n_reopened = sum(1 for r in summary if r["reopened_flag"])
    msg_count = len(flows["customer_messages"])
    event_count = len(flows["ticket_lifecycle_events"])

    print("Generated human ticketing dataset")
    print(f"  output dir              : {out_dir}")
    print(f"  tickets                 : {n}")
    print(f"  faq match rate          : {n_faq_match / n:.1%}")
    print(f"  escalation rate         : {n_escalated / n:.1%}")
    print(f"  resolution acceptance   : {n_accepted / n:.1%}")
    print(f"  reopen rate             : {n_reopened / n:.1%}")
    print(f"  customer_messages rows  : {msg_count}")
    print(f"  lifecycle events rows   : {event_count}")


if __name__ == "__main__":
    main()
