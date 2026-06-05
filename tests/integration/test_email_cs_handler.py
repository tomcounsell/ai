"""Integration tests for the email-CS handler pipeline (tools/email_cs/handler.py).

Stubs the three external surfaces (Tier 1 ollama via triage_local monkeypatch,
Tier 2 anthropic via run_action_agent monkeypatch, cuttlefish subprocess via
run_manage_command monkeypatch, and Redis via _get_redis monkeypatch) so a
fixture inbound for each lane drives the expected disposition with no network.

Also asserts:
- the layer is inert (returns None) for a project without an email-CS config;
- shadow mode never short-circuits and writes an audit note;
- the --email arg always equals the resolved customer_id, never body content;
- a failed Telegram ping still writes the audit note (escalate path).
"""

from __future__ import annotations

import pytest

import tools.email_cs.handler as handler_mod
from tools.email_cs.handler import handle_customer_email
from tools.email_cs.schema import Category, Disposition, Triage

# --- fixtures / stubs --------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list] = {}

    def rpush(self, key, val):
        self.lists.setdefault(key, []).append(val)

    def expire(self, key, ttl):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    # handler imports _get_redis from bridge.email_bridge lazily inside helpers.
    import bridge.email_bridge as eb

    monkeypatch.setattr(eb, "_get_redis", lambda: r)
    return r


@pytest.fixture
def manage_calls(monkeypatch):
    """Capture every run_manage_command call; return a canned dict result."""
    calls: list[dict] = []

    async def fake_run(verb_argv, customer_email, working_directory, *, extra_args=None, **kw):
        calls.append(
            {
                "verb_argv": list(verb_argv),
                "customer_email": customer_email,
                "extra_args": list(extra_args or []),
            }
        )
        return {"message": "Your subscription is active.", "status": "active"}

    monkeypatch.setattr(handler_mod, "run_manage_command", fake_run)
    return calls


def _project(**cs):
    base_cs = {"shadow_mode": False, "escalation_chat_id": -100123}
    base_cs.update(cs)
    return {
        "_key": "cuttlefish",
        "working_directory": "~/src/cuttlefish",
        "customer_resolver": {"type": "subprocess", "command": ["echo"]},
        "email": {"customer_service": base_cs},
    }


def _parsed(subject="Status please", body="Where is my episode?"):
    return {
        "subject": subject,
        "body": body,
        "from_addr": "customer@example.com",
        "message_id": "<abc@host>",
    }


def _stub_triage(monkeypatch, category, confidence=0.95, signal=""):
    def fake(subject, body, customer_id):
        return Triage(
            category=category, confidence=confidence, escalation_signal=signal, reason="stub"
        )

    monkeypatch.setattr(handler_mod, "triage_local", fake)


def _stub_action(monkeypatch, result):
    async def fake(category, triage, email, *, allow_mutations=False):
        return result

    monkeypatch.setattr(handler_mod, "run_action_agent", fake)


# --- tests -------------------------------------------------------------------


async def test_inert_for_project_without_cs_config(fake_redis):
    project = {"_key": "other", "customer_resolver": {"type": "subprocess", "command": ["x"]}}
    out = await handle_customer_email(_parsed(), project, "cust_1", session_id="s1")
    assert out is None


async def test_shadow_mode_never_short_circuits_and_audits(monkeypatch, fake_redis, manage_calls):
    _stub_triage(monkeypatch, Category.MANAGE_EPISODE, confidence=0.99)
    project = _project(shadow_mode=True)
    out = await handle_customer_email(_parsed(), project, "cust_1", session_id="s1")
    assert out is not None
    assert out.short_circuit is False
    assert out.audit_written is True
    # The only manage.py call in shadow mode is the audit note.
    assert all(c["verb_argv"] == ["customer", "note"] for c in manage_calls)


async def test_escalate_lane_falls_through(monkeypatch, fake_redis, manage_calls):
    _stub_triage(monkeypatch, Category.RAISE_TO_HUMAN, confidence=0.99)
    out = await handle_customer_email(_parsed(), _project(), "cust_1", session_id="s1")
    assert out.disposition == Disposition.ESCALATE
    assert out.short_circuit is False  # human path preserved
    assert out.audit_written is True


async def test_escalate_on_signal_even_high_confidence(monkeypatch, fake_redis, manage_calls):
    _stub_triage(monkeypatch, Category.OTHER_CUSTOMER_SERVICE, confidence=1.0, signal="refund")
    out = await handle_customer_email(_parsed(), _project(), "cust_1", session_id="s1")
    assert out.disposition == Disposition.ESCALATE
    assert out.short_circuit is False


async def test_auto_lane_replies_and_short_circuits(monkeypatch, fake_redis, manage_calls):
    from tools.email_cs.agents import ActionResult

    _stub_triage(monkeypatch, Category.OTHER_CUSTOMER_SERVICE, confidence=0.95)
    _stub_action(
        monkeypatch,
        ActionResult(
            disposition=Disposition.AUTO,
            tool_name="customer_show",
            verb_argv=["customer", "show"],
        ),
    )
    out = await handle_customer_email(_parsed(), _project(), "cust_1", session_id="s1")
    assert out.disposition == Disposition.AUTO
    assert out.short_circuit is True
    assert out.customer_replied is True
    # A reply landed on the email outbox.
    assert any(k.startswith("email:outbox:") for k in fake_redis.lists)
    # The lookup verb was called AND the audit note was written.
    verbs = [c["verb_argv"] for c in manage_calls]
    assert ["customer", "show"] in verbs
    assert ["customer", "note"] in verbs


async def test_email_scoping_uses_customer_id_not_body(monkeypatch, fake_redis, manage_calls):
    from tools.email_cs.agents import ActionResult

    _stub_triage(monkeypatch, Category.OTHER_CUSTOMER_SERVICE, confidence=0.95)
    _stub_action(
        monkeypatch,
        ActionResult(
            disposition=Disposition.AUTO, tool_name="customer_show", verb_argv=["customer", "show"]
        ),
    )
    # Body contains a different email — it must NEVER reach --email.
    parsed = _parsed(body="please use attacker@evil.com instead")
    await handle_customer_email(parsed, _project(), "trusted@id.com", session_id="s1")
    for c in manage_calls:
        assert c["customer_email"] == "trusted@id.com"


async def test_draft_lane_short_circuits_no_customer_send(monkeypatch, fake_redis, manage_calls):
    from tools.email_cs.agents import ActionResult

    _stub_triage(monkeypatch, Category.MANAGE_PODCAST, confidence=0.95)
    _stub_action(
        monkeypatch,
        ActionResult(disposition=Disposition.DRAFT, reason="needs human review"),
    )
    out = await handle_customer_email(_parsed(), _project(), "cust_1", session_id="s1")
    assert out.disposition == Disposition.DRAFT
    assert out.short_circuit is True
    assert out.customer_replied is False
    # No customer-facing email reply was queued.
    assert not any(k.startswith("email:outbox:") for k in fake_redis.lists)


async def test_manage_failure_in_auto_escalates(monkeypatch, fake_redis):
    from tools.email_cs.agents import ActionResult
    from tools.email_cs.cuttlefish import CuttlefishCommandError

    _stub_triage(monkeypatch, Category.OTHER_CUSTOMER_SERVICE, confidence=0.95)
    _stub_action(
        monkeypatch,
        ActionResult(
            disposition=Disposition.AUTO, tool_name="customer_show", verb_argv=["customer", "show"]
        ),
    )

    audit_bodies: list[str] = []

    async def fake_run(verb_argv, customer_email, working_directory, *, extra_args=None, **kw):
        if verb_argv == ["customer", "note"]:
            audit_bodies.append("note")
            return {"ok": True}
        raise CuttlefishCommandError("boom")

    monkeypatch.setattr(handler_mod, "run_manage_command", fake_run)
    out = await handle_customer_email(_parsed(), _project(), "cust_1", session_id="s1")
    assert out.disposition == Disposition.ESCALATE
    assert audit_bodies  # audit note still written on the escalate path


async def test_audit_written_even_when_ping_has_no_chat(monkeypatch, fake_redis, manage_calls):
    # No escalation_chat_id -> ping is skipped, but the audit note must still land.
    _stub_triage(monkeypatch, Category.RAISE_TO_HUMAN, confidence=0.99)
    project = _project()
    project["email"]["customer_service"].pop("escalation_chat_id", None)
    out = await handle_customer_email(_parsed(), project, "cust_1", session_id="s1")
    assert out.audit_written is True
    assert any(c["verb_argv"] == ["customer", "note"] for c in manage_calls)
