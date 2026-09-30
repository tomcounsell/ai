"""Email customer-service auto-reply layer for Cuttlefish.

Two-tier triage (Tier 1 local Ollama classification + Tier 2 Anthropic
per-category action agent) with a structural escalation gate, wired into
``bridge/email_bridge.py::_process_inbound_email()``.

See ``docs/features/email-cs-auto-reply.md`` for the architecture and the
three-phase rollout (shadow -> read-only auto -> mutating auto).
"""
