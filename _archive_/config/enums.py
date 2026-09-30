"""Canonical enum definitions for session types, personas, and classification.

All magic strings for session routing, persona selection, and intent classification
are defined here as StrEnum members. StrEnum inherits from str, so members compare
equal to their string values (e.g., SessionType.ENG == "eng" is True).

Usage:
    from config.enums import SessionType, PersonaType, ClassificationType

    if session.session_type == SessionType.ENG:
        ...
"""

import logging
from enum import StrEnum

# ``extra_context["origin"]`` value stamped on sessions created by
# ``reflections.agent_handoff`` (#3588); the runner and reflections both key on it.
REFLECTION_HANDOFF_ORIGIN = "reflection_handoff"


HUMAN_STEERED_KEY = "human_steered"


def is_reflection_handoff(session: object) -> bool:
    """True for a still-silent session created by ``reflections.agent_handoff`` (#3588).

    Keys on the explicit origin marker. Every canned human-facing notice
    (turn timeout, steer abort, failure, interrupt) checks it, because a
    handoff session has no human waiting in the Room. Once a human steers the
    session (``extra_context["human_steered"]``, stamped by
    ``agent.steering.mark_handoff_human_steered``) a human IS waiting, so the
    session stops being treated as silent and gets the ordinary notices.
    """
    extra = getattr(session, "extra_context", None)
    return (
        isinstance(extra, dict)
        and extra.get("origin") == REFLECTION_HANDOFF_ORIGIN
        and not extra.get(HUMAN_STEERED_KEY)
    )


def is_reflection_handoff_live(session: object) -> bool:
    """:func:`is_reflection_handoff` against a fresh read of the session row.

    A running executor/runner holds an in-memory copy that predates a human
    steer landing mid-run. Falls back to the in-memory copy when the row
    cannot be re-read.
    """
    if not is_reflection_handoff(session):
        return False
    try:
        from models.agent_session import AgentSession  # noqa: PLC0415

        row = AgentSession.get_by_id(getattr(session, "id", None))
    except Exception as e:  # noqa: BLE001 -- staleness fallback, never raises
        logging.getLogger(__name__).warning(
            "[handoff] live re-read of session failed, using in-memory copy: %s", e
        )
        return True
    return True if row is None else is_reflection_handoff(row)


class SessionType(StrEnum):
    """Discriminator for AgentSession: eng or teammate."""

    ENG = "eng"
    TEAMMATE = "teammate"


class PersonaType(StrEnum):
    """Persona identifiers from projects.json group configuration."""

    ENGINEER = "engineer"
    TEAMMATE = "teammate"
    CUSTOMER_SERVICE = "customer-service"


class AccessLevel(StrEnum):
    """Prompt-rails layer applied on top of a persona.

    Orthogonal to ``SessionType`` (which decides queueing, child-session shape,
    output handler) and to ``PersonaType`` (which decides voice and identity).
    AccessLevel decides which safety preamble + appendices wrap the persona
    when ``compose_system_prompt`` assembles the final agent system prompt.

    - ``WORKER``: full permissions; prepends ``WORKER_RULES`` (safety rails)
      and appends principal context + completion criteria. Maps to
      ``SessionType.ENG`` today.
    - ``TEAMMATE``: conversational, no rails. Maps to ``SessionType.TEAMMATE``
      with the teammate persona today.
    - ``CUSTOMER_SERVICE``: action-oriented, no code writes, no rails. Used by
      the email-spawned customer-service persona override today.

    AccessLevel is **prompt-only**; runtime tool restrictions are enforced
    separately by ``agent/hooks/pre_tool_use.py`` keyed on ``SessionType``.
    """

    WORKER = "worker"
    TEAMMATE = "teammate"
    CUSTOMER_SERVICE = "customer-service"


class ClassificationType(StrEnum):
    """Intent classification results from the work request classifier.

    Four-way classification:
    - SDLC: Work request that could result in code changes or a PR
    - COLLABORATION: Direct task the PM can handle without a dev-session
    - OTHER: Ambiguous task — PM uses judgment
    - QUESTION: Informational query, explanation, or opinion request
    """

    SDLC = "sdlc"
    COLLABORATION = "collaboration"
    OTHER = "other"
    QUESTION = "question"
