"""CyclicEpisode model - structural behavioral record of a completed SDLC cycle.

Each episode captures the fingerprint (problem topology, affected layer, ambiguity),
trajectory (tool sequence, friction events, stage durations), and outcome
(resolution type, intent satisfied, review rounds) of a completed SDLC session.

Episodes are written by the Reflections pipeline cycle-close step and read by
the Observer at stage transitions. The Observer is read-only; Reflections is
the sole write path (abstraction barrier).

Vault isolation uses a `vault` KeyField with manual key prefixing (workaround
until Popoto gains Meta.namespace support). Project-scoped episodes use
`mem:{project_key}` as vault value; shared patterns use `shared`.
"""

import time
import logging

from popoto import (
    AutoKeyField,
    DictField,
    Field,
    KeyField,
    ListField,
    Model,
    SortedField,
)

logger = logging.getLogger(__name__)

# Maximum items in capped list fields (manual truncation workaround)
MAX_TOOL_SEQUENCE = 50
MAX_FRICTION_EVENTS = 20

# Fingerprint enums
PROBLEM_TOPOLOGIES = [
    "new_feature",
    "bug_fix",
    "refactor",
    "integration",
    "configuration",
    "ambiguous",
]

AFFECTED_LAYERS = [
    "model",
    "bridge",
    "agent",
    "tool",
    "config",
    "test",
    "docs",
    "infra",
    "unknown",
]

RESOLUTION_TYPES = [
    "clean_merge",
    "patch_required",
    "abandoned",
    "deferred",
    "unknown",
]


class CyclicEpisode(Model):
    """Structural behavioral record of a completed SDLC cycle.

    Fields are organized into three groups:
    - Fingerprint: structural classification for pattern matching
    - Trajectory: what happened during the cycle
    - Outcome: how it ended

    The `vault` field provides namespace isolation per project.
    Query by vault to scope results: CyclicEpisode.query.filter(vault="mem:ai")
    """

    # === Identity ===
    episode_id = AutoKeyField()
    vault = KeyField()  # "mem:{project_key}" for project scope, "shared" for patterns
    raw_ref = Field(null=True)  # AgentSession.job_id that spawned this episode
    created_at = SortedField(type=float)

    # === Fingerprint (structural classification) ===
    problem_topology = KeyField(default="ambiguous")  # one of PROBLEM_TOPOLOGIES
    affected_layer = KeyField(default="unknown")  # one of AFFECTED_LAYERS
    ambiguity_at_intake = Field(
        type=float, default=0.5
    )  # 0.0 = crystal clear, 1.0 = fully ambiguous
    acceptance_criterion_defined = Field(type=bool, default=False)

    # === Trajectory (what happened) ===
    tool_sequence = ListField(null=True)  # list of "{stage}:{tool_type}" strings
    friction_events = ListField(null=True)  # list of friction event dicts (serialized as strings)
    stage_durations = DictField(null=True)  # {stage_name: duration_seconds}
    deviation_count = Field(type=int, default=0)  # number of unexpected detours

    # === Outcome (how it ended) ===
    resolution_type = Field(default="unknown")  # one of RESOLUTION_TYPES
    intent_satisfied = Field(type=bool, default=True)
    review_round_count = Field(type=int, default=0)
    surprise_delta = Field(type=float, default=0.0)  # how much the outcome diverged from plan

    # === Metadata ===
    issue_url = Field(null=True)
    branch_name = Field(null=True)
    session_summary = Field(null=True, max_length=1000)  # brief text summary

    # === Helpers ===

    def append_tool(self, stage: str, tool_type: str) -> None:
        """Append a tool event to tool_sequence, capped at MAX_TOOL_SEQUENCE."""
        current = self.tool_sequence if isinstance(self.tool_sequence, list) else []
        entry = f"{stage}:{tool_type}"
        current.append(entry)
        if len(current) > MAX_TOOL_SEQUENCE:
            current = current[-MAX_TOOL_SEQUENCE:]
        self.tool_sequence = current

    def append_friction(self, stage: str, description: str, repetition_count: int = 1) -> None:
        """Append a friction event, capped at MAX_FRICTION_EVENTS."""
        current = self.friction_events if isinstance(self.friction_events, list) else []
        entry = f"{stage}|{description}|{repetition_count}"
        current.append(entry)
        if len(current) > MAX_FRICTION_EVENTS:
            current = current[-MAX_FRICTION_EVENTS:]
        self.friction_events = current

    def get_fingerprint(self) -> dict:
        """Return the fingerprint fields as a dict for matching."""
        return {
            "problem_topology": self.problem_topology,
            "affected_layer": self.affected_layer,
            "ambiguity_at_intake": self.ambiguity_at_intake,
            "acceptance_criterion_defined": self.acceptance_criterion_defined,
        }

    def to_export_dict(self) -> dict:
        """Serialize to dict for JSON export (content-stripped for sharing)."""
        return {
            "episode_id": self.episode_id,
            "vault": self.vault,
            "created_at": self.created_at,
            "problem_topology": self.problem_topology,
            "affected_layer": self.affected_layer,
            "ambiguity_at_intake": self.ambiguity_at_intake,
            "acceptance_criterion_defined": self.acceptance_criterion_defined,
            "tool_sequence": self.tool_sequence or [],
            "friction_events": self.friction_events or [],
            "stage_durations": self.stage_durations or {},
            "deviation_count": self.deviation_count,
            "resolution_type": self.resolution_type,
            "intent_satisfied": self.intent_satisfied,
            "review_round_count": self.review_round_count,
            "surprise_delta": self.surprise_delta,
        }

    # === Cleanup ===

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 180) -> int:
        """Delete episodes older than max_age_days. Returns count deleted."""
        cutoff = time.time() - (max_age_days * 86400)
        all_episodes = cls.query.all()
        deleted = 0
        for episode in all_episodes:
            if episode.created_at and episode.created_at < cutoff:
                episode.delete()
                deleted += 1
        return deleted
