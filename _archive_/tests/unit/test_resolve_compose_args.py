"""Unit tests for `_resolve_compose_args` -- single source of truth mapping
session context to ``(persona, access_level, channel)``.

Both ``agent/sdk_client.py:get_response_via_harness`` and
``agent/session_executor.py`` call this helper instead of duplicating the
branch ladder.
"""

from __future__ import annotations

import pytest

from agent.sdk_client import _resolve_compose_args
from config.enums import AccessLevel, PersonaType, SessionType


def test_eng_session_type_maps_to_engineer_worker():
    persona, access, channel = _resolve_compose_args(SessionType.ENG)
    assert persona == PersonaType.ENGINEER
    assert access == AccessLevel.WORKER
    assert channel is None


def test_teammate_session_type_default():
    persona, access, channel = _resolve_compose_args(SessionType.TEAMMATE)
    assert persona == PersonaType.TEAMMATE
    assert access == AccessLevel.TEAMMATE
    assert channel is None


def test_email_with_customer_service_persona_override():
    proj = {"email": {"persona": "customer-service"}}
    persona, access, channel = _resolve_compose_args(
        SessionType.TEAMMATE, project=proj, transport="email"
    )
    assert persona == PersonaType.CUSTOMER_SERVICE
    assert access == AccessLevel.CUSTOMER_SERVICE
    assert channel == "email"


def test_email_with_teammate_persona_no_override():
    """Setting `email.persona = "teammate"` is a no-op -- no override fires."""
    proj = {"email": {"persona": "teammate"}}
    persona, access, channel = _resolve_compose_args(
        SessionType.TEAMMATE, project=proj, transport="email"
    )
    assert persona == PersonaType.TEAMMATE
    assert access == AccessLevel.TEAMMATE
    # No channel propagated when no override -- preserves today's behavior.
    assert channel is None


def test_email_with_unknown_persona_falls_back_to_teammate():
    proj = {"email": {"persona": "not-a-real-persona"}}
    persona, access, channel = _resolve_compose_args(
        SessionType.TEAMMATE, project=proj, transport="email"
    )
    assert persona == PersonaType.TEAMMATE
    assert access == AccessLevel.TEAMMATE


def test_project_mode_pm_is_no_longer_recognized():
    """Legacy `project_mode == "pm"` no longer forces engineer rails.

    Commit dd926192 (#1633) merged the PM/Dev roles into the single Eng role:
    only ``project_mode == "eng"`` triggers the engineer override, and callers
    normalize unknown modes (including the legacy "pm") to None with a
    warning. A stray "pm" reaching the resolver falls through to the default
    teammate rails.
    """
    persona, access, _ = _resolve_compose_args(SessionType.TEAMMATE, project_mode="pm")
    assert persona == PersonaType.TEAMMATE
    assert access == AccessLevel.TEAMMATE


def test_project_mode_eng_overrides_teammate_session_type():
    """`project_mode == "eng"` forces engineer rails even when session_type is not ENG."""
    persona, access, _ = _resolve_compose_args(SessionType.TEAMMATE, project_mode="eng")
    assert persona == PersonaType.ENGINEER
    assert access == AccessLevel.WORKER


def test_eng_session_type_takes_precedence_over_email_override():
    """Even with an email transport + email.persona, SessionType.ENG wins."""
    proj = {"email": {"persona": "customer-service"}}
    persona, access, _ = _resolve_compose_args(SessionType.ENG, project=proj, transport="email")
    assert persona == PersonaType.ENGINEER
    assert access == AccessLevel.WORKER


@pytest.mark.parametrize(
    "session_type,expected_persona,expected_access",
    [
        (SessionType.ENG, PersonaType.ENGINEER, AccessLevel.WORKER),
        (SessionType.TEAMMATE, PersonaType.TEAMMATE, AccessLevel.TEAMMATE),
    ],
)
def test_no_project_no_transport_default_mapping(session_type, expected_persona, expected_access):
    """Default mapping with no project / no transport hints."""
    persona, access, _ = _resolve_compose_args(session_type)
    assert persona == expected_persona
    assert access == expected_access
