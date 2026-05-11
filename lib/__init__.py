"""Shared infrastructure for the ticket workflow.

Imported by both ``skills/`` (LLM-based, agent-callable) and
``automations/`` (deterministic, orchestrator-callable). See
``lib/ticketing_common.py`` for the standard CLI parser, error envelope,
working-CSV writers, and audit-log helpers each ticket-workflow script
shares.
"""
