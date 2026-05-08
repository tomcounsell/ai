"""Unit tests for `_resolve_compose_args` — single source of truth mapping
session context to ``(persona, access_level, channel)``.

Replaces the old branch-by-branch testing of the two pickers
(``agent/sdk_client.py`` and ``agent/session_executor.py``). Both call sites
now import this helper.
"""

from __future__ import annotations

import pytest

from agent.sdk_client import _resolve_compose_args
from config.enums import AccessLevel, PersonaType, SessionType


def test_pm_session_type_maps_to_pm_readonly():
    persona, access, channel = _resolve_compose_args(SessionType.PM)
    assert persona == PersonaType.PROJECT_MANAGER
    assert access == AccessLevel.PM_READONLY
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
    """Setting `email.persona = "teammate"` is a no-op — no override fires."""
    proj = {"email": {"persona": "teammate"}}
    persona, access, channel = _resolve_compose_args(
        SessionType.TEAMMATE, project=proj, transport="email"
    )
    assert persona == PersonaType.TEAMMATE
    assert access == AccessLevel.TEAMMATE
    # No channel propagated when no override — preserves today's behavior.
    assert channel is None


def test_email_with_unknown_persona_falls_back_to_teammate():
    proj = {"email": {"persona": "not-a-real-persona"}}
    persona, access, channel = _resolve_compose_args(
        SessionType.TEAMMATE, project=proj, transport="email"
    )
    assert persona == PersonaType.TEAMMATE
    assert access == AccessLevel.TEAMMATE


def test_project_mode_pm_overrides_teammate_session_type():
    """`project_mode == "pm"` forces PM rails even when session_type is not PM."""
    persona, access, _ = _resolve_compose_args(SessionType.TEAMMATE, project_mode="pm")
    assert persona == PersonaType.PROJECT_MANAGER
    assert access == AccessLevel.PM_READONLY


def test_pm_session_type_takes_precedence_over_email_override():
    """Even with an email transport + email.persona, SessionType.PM wins."""
    proj = {"email": {"persona": "customer-service"}}
    persona, access, _ = _resolve_compose_args(SessionType.PM, project=proj, transport="email")
    assert persona == PersonaType.PROJECT_MANAGER
    assert access == AccessLevel.PM_READONLY


@pytest.mark.parametrize(
    "session_type,expected_persona,expected_access",
    [
        (SessionType.PM, PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY),
        (SessionType.TEAMMATE, PersonaType.TEAMMATE, AccessLevel.TEAMMATE),
    ],
)
def test_no_project_no_transport_default_mapping(session_type, expected_persona, expected_access):
    """Default mapping with no project / no transport hints."""
    persona, access, _ = _resolve_compose_args(session_type)
    assert persona == expected_persona
    assert access == expected_access
