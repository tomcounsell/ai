"""Unit tests for the Tier 2 action agent (tools/email_cs/agents.py).

The Anthropic client is stubbed via the anthropic_slot context manager so these
run offline. Covers the structural gate (empty whitelist -> escalate, no API
call), the draft fallbacks (invalid tool, no tool, API error), and the auto path.
"""

from __future__ import annotations

import contextlib

import pytest

from tools.email_cs.agents import run_action_agent
from tools.email_cs.schema import Category, Disposition, Triage


def _triage(category, confidence=0.95):
    return Triage(category=category, confidence=confidence, reason="t")


class _FakeBlock:
    def __init__(self, btype, name=None, binput=None):
        self.type = btype
        self.name = name
        self.input = binput


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response, raises, capture):
        self._response = response
        self._raises = raises
        self._capture = capture

    async def create(self, **kwargs):
        if self._capture is not None:
            self._capture.update(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class _FakeClient:
    def __init__(self, response, raises, capture):
        self.messages = _FakeMessages(response, raises, capture)


def _install_fake_client(monkeypatch, *, response=None, raises=None, capture=None):
    """Patch anthropic_slot to yield a fake client returning ``response``."""

    @contextlib.asynccontextmanager
    async def fake_slot():
        yield _FakeClient(response, raises, capture)

    monkeypatch.setattr("tools.email_cs.agents.anthropic_slot", fake_slot)


@pytest.mark.asyncio
async def test_empty_whitelist_escalates_without_api_call(monkeypatch):
    # raise_to_human has an empty whitelist; the API must never be called.
    def _boom(*a, **k):
        raise AssertionError("anthropic_slot must not be used for empty whitelist")

    monkeypatch.setattr("tools.email_cs.agents.anthropic_slot", _boom)
    result = await run_action_agent(
        Category.RAISE_TO_HUMAN, _triage(Category.RAISE_TO_HUMAN), {"subject": "x", "body": "y"}
    )
    assert result.disposition == Disposition.ESCALATE
    assert result.tool_name is None


@pytest.mark.asyncio
async def test_valid_tool_yields_auto(monkeypatch):
    resp = _FakeResponse([_FakeBlock("tool_use", name="customer_show", binput={})])
    _install_fake_client(monkeypatch, response=resp)
    result = await run_action_agent(
        Category.MANAGE_EPISODE,
        _triage(Category.MANAGE_EPISODE),
        {"subject": "status", "body": "?"},
    )
    assert result.disposition == Disposition.AUTO
    assert result.tool_name == "customer_show"
    assert result.verb_argv == ["customer", "show"]


@pytest.mark.asyncio
async def test_invalid_tool_name_yields_draft(monkeypatch):
    resp = _FakeResponse([_FakeBlock("tool_use", name="refund_customer", binput={})])
    _install_fake_client(monkeypatch, response=resp)
    result = await run_action_agent(
        Category.OTHER_CUSTOMER_SERVICE,
        _triage(Category.OTHER_CUSTOMER_SERVICE),
        {"subject": "refund", "body": "money back"},
    )
    assert result.disposition == Disposition.DRAFT
    assert "invalid tool" in result.reason


@pytest.mark.asyncio
async def test_no_tool_use_block_yields_draft(monkeypatch):
    resp = _FakeResponse([_FakeBlock("text", name=None, binput=None)])
    _install_fake_client(monkeypatch, response=resp)
    result = await run_action_agent(
        Category.MANAGE_PODCAST, _triage(Category.MANAGE_PODCAST), {"subject": "x", "body": "y"}
    )
    assert result.disposition == Disposition.DRAFT


@pytest.mark.asyncio
async def test_api_error_yields_draft(monkeypatch):
    _install_fake_client(monkeypatch, raises=RuntimeError("anthropic 500"))
    result = await run_action_agent(
        Category.MANAGE_EPISODE, _triage(Category.MANAGE_EPISODE), {"subject": "x", "body": "y"}
    )
    assert result.disposition == Disposition.DRAFT
    assert "api failure" in result.reason


@pytest.mark.asyncio
async def test_mutating_verb_filtered_in_readonly_phase(monkeypatch):
    # Phase 1/2: only read-only tools are offered. The agent should never see
    # episode_provision (mutating). If the model named it, it'd be invalid->draft.
    capture: dict = {}
    resp = _FakeResponse([_FakeBlock("tool_use", name="customer_show", binput={})])
    _install_fake_client(monkeypatch, response=resp, capture=capture)
    await run_action_agent(
        Category.MANAGE_EPISODE,
        _triage(Category.MANAGE_EPISODE),
        {"subject": "x", "body": "y"},
        allow_mutations=False,
    )
    offered = {t["name"] for t in capture["tools"]}
    assert "episode_provision" not in offered
    assert "customer_show" in offered
    assert capture["tool_choice"] == {"type": "any"}


@pytest.mark.asyncio
async def test_mutating_verb_present_when_allowed(monkeypatch):
    capture: dict = {}
    resp = _FakeResponse([_FakeBlock("tool_use", name="episode_provision", binput={})])
    _install_fake_client(monkeypatch, response=resp, capture=capture)
    result = await run_action_agent(
        Category.MANAGE_EPISODE,
        _triage(Category.MANAGE_EPISODE),
        {"subject": "regenerate", "body": "please"},
        allow_mutations=True,
    )
    offered = {t["name"] for t in capture["tools"]}
    assert "episode_provision" in offered
    assert result.disposition == Disposition.AUTO
    assert result.verb_argv == ["episode", "provision"]
