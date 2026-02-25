"""Backward compatibility shim - SessionLog is now AgentSession.

All code should import from models.agent_session directly.
This module exists only for legacy imports.
"""

from models.agent_session import AgentSession as SessionLog

__all__ = ["SessionLog"]
