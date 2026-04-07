"""Canonical enum definitions for session types, personas, and classification.

All magic strings for session routing, persona selection, and intent classification
are defined here as StrEnum members. StrEnum inherits from str, so members compare
equal to their string values (e.g., SessionType.CHAT == "chat" is True). This means
no Redis data migration is needed -- existing string values match enum members.

Usage:
    from config.enums import SessionType, PersonaType, ClassificationType

    if session.session_type == SessionType.CHAT:
        ...
"""

from enum import StrEnum


class SessionType(StrEnum):
    """Discriminator for AgentSession: chat (PM) or dev (developer)."""

    CHAT = "chat"
    DEV = "dev"


class PersonaType(StrEnum):
    """Persona identifiers from projects.json group configuration."""

    DEVELOPER = "developer"
    PROJECT_MANAGER = "project-manager"
    TEAMMATE = "teammate"


class ClassificationType(StrEnum):
    """Intent classification results from the work request classifier."""

    SDLC = "sdlc"
    QUESTION = "question"
