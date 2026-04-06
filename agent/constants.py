"""
Shared constants for the agent session system.

These constants were originally defined in bridge/response.py but are used by
both the bridge and the standalone worker. Canonical definitions live here;
bridge/response.py re-exports them for backward compatibility.
"""

# Reaction emoji constants used by the session execution engine
REACTION_SUCCESS = "\U0001f44d"  # Simple ack, no text reply needed
REACTION_COMPLETE = "\U0001f3c6"  # Work done, text reply attached
REACTION_ERROR = "\U0001f631"  # Something went wrong
