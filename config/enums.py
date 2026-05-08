"""Canonical enum definitions for session types, personas, and classification.

All magic strings for session routing, persona selection, and intent classification
are defined here as StrEnum members. StrEnum inherits from str, so members compare
equal to their string values (e.g., SessionType.PM == "pm" is True).

Usage:
    from config.enums import SessionType, PersonaType, ClassificationType

    if session.session_type == SessionType.PM:
        ...
"""

from enum import StrEnum


class SessionType(StrEnum):
    """Discriminator for AgentSession: pm, teammate, or dev."""

    PM = "pm"
    TEAMMATE = "teammate"
    DEV = "dev"


class PersonaType(StrEnum):
    """Persona identifiers from projects.json group configuration."""

    DEVELOPER = "developer"
    PROJECT_MANAGER = "project-manager"
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
      ``SessionType.DEV`` today.
    - ``PM_READONLY``: read-only PM mode; omits ``WORKER_RULES``; appends
      work-vault ``CLAUDE.md``. Maps to ``SessionType.PM`` today. Caller must
      pass a ``working_directory``.
    - ``TEAMMATE``: conversational, no rails. Maps to ``SessionType.TEAMMATE``
      with the teammate persona today.
    - ``CUSTOMER_SERVICE``: action-oriented, no code writes, no rails. Used by
      the email-spawned customer-service persona override today.

    AccessLevel is **prompt-only**; runtime tool restrictions are enforced
    separately by ``agent/hooks/pre_tool_use.py`` keyed on ``SessionType``.
    """

    WORKER = "worker"
    PM_READONLY = "pm-readonly"
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
