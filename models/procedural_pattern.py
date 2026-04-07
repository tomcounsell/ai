"""ProceduralPattern model - crystallized behavioral pattern from repeated episodes.

When N episodes share a fingerprint cluster (same problem_topology + affected_layer)
with consistent outcomes, a ProceduralPattern is created or reinforced. Patterns
contain NO project-specific content (issue text, code paths, client names) -- they
are structural abstractions safe to sync across machines.

Patterns live in the `shared` namespace and are synced via iCloud JSON export/import.
The Reflections pipeline is the sole writer; the Observer is read-only.
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


class ProceduralPattern(Model):
    """A crystallized behavioral pattern derived from repeated SDLC episodes.

    Created when 3+ episodes share the same fingerprint cluster and show
    consistent tool sequences and outcomes. Contains no content -- only
    structural abstractions.
    """

    # === Identity ===
    pattern_id = AutoKeyField()
    vault = KeyField(default="shared")  # always "shared" for cross-machine sync

    # === Fingerprint cluster (what situations this applies to) ===
    problem_topology = KeyField()  # matches CyclicEpisode.problem_topology
    affected_layer = KeyField()  # matches CyclicEpisode.affected_layer

    # === Pattern data (what to do) ===
    canonical_tool_sequence = ListField(null=True)  # most common tool sequence
    warnings = ListField(null=True)  # list of warning strings for the Observer
    shortcuts = ListField(null=True)  # list of shortcut suggestions

    # === Confidence metrics ===
    success_rate = Field(type=float, default=0.0)  # success_count / sample_count
    sample_count = Field(type=int, default=0)
    success_count = Field(type=int, default=0)
    confidence = Field(type=float, default=0.0)  # derived from sample_count and success_rate
    last_reinforced = SortedField(type=float)  # Unix timestamp

    # === Metadata ===
    created_at = Field(type=float)
    source_episode_ids = ListField(null=True)  # episode IDs that contributed

    # === Methods ===

    def reinforce(self, success: bool) -> None:
        """Reinforce this pattern with a new episode outcome.

        Uses read-modify-write (workaround until Popoto gains atomic_increment).
        """
        self.sample_count = (self.sample_count or 0) + 1
        if success:
            self.success_count = (self.success_count or 0) + 1
        self.success_rate = self.success_count / self.sample_count if self.sample_count > 0 else 0.0
        self.confidence = self._compute_confidence()
        self.last_reinforced = time.time()
        self.save()
        logger.info(
            f"Pattern {self.pattern_id} reinforced: "
            f"success_rate={self.success_rate:.2f}, "
            f"sample_count={self.sample_count}, "
            f"confidence={self.confidence:.2f}"
        )

    def _compute_confidence(self) -> float:
        """Compute confidence score from sample count and success rate.

        Simple formula: confidence = success_rate * min(sample_count / 10, 1.0)
        Confidence grows with sample count up to 10 samples, then stabilizes.
        """
        if not self.sample_count:
            return 0.0
        sample_factor = min(self.sample_count / 10.0, 1.0)
        return self.success_rate * sample_factor

    def get_fingerprint_cluster(self) -> dict:
        """Return the fingerprint cluster this pattern matches."""
        return {
            "problem_topology": self.problem_topology,
            "affected_layer": self.affected_layer,
        }

    def to_export_dict(self) -> dict:
        """Serialize to dict for JSON export (cross-machine sync)."""
        return {
            "pattern_id": self.pattern_id,
            "problem_topology": self.problem_topology,
            "affected_layer": self.affected_layer,
            "canonical_tool_sequence": self.canonical_tool_sequence or [],
            "warnings": self.warnings or [],
            "shortcuts": self.shortcuts or [],
            "success_rate": self.success_rate,
            "sample_count": self.sample_count,
            "success_count": self.success_count,
            "confidence": self.confidence,
            "last_reinforced": self.last_reinforced,
            "created_at": self.created_at,
            "source_episode_ids": self.source_episode_ids or [],
        }

    @classmethod
    def from_import_dict(cls, data: dict) -> "ProceduralPattern":
        """Create or update a pattern from an imported dict.

        Idempotent: if a pattern with the same fingerprint cluster exists
        and has a newer or equal last_reinforced, it is kept unchanged.
        """
        existing = cls.query.filter(
            problem_topology=data["problem_topology"],
            affected_layer=data["affected_layer"],
        )
        if existing:
            pattern = existing[0]
            # Last-write-wins: higher sample_count breaks ties
            incoming_ts = data.get("last_reinforced", 0) or 0
            existing_ts = pattern.last_reinforced or 0
            if incoming_ts > existing_ts or (
                incoming_ts == existing_ts
                and (data.get("sample_count", 0) or 0) > (pattern.sample_count or 0)
            ):
                # Update with incoming data
                pattern.canonical_tool_sequence = data.get("canonical_tool_sequence", [])
                pattern.warnings = data.get("warnings", [])
                pattern.shortcuts = data.get("shortcuts", [])
                pattern.success_rate = data.get("success_rate", 0.0)
                pattern.sample_count = data.get("sample_count", 0)
                pattern.success_count = data.get("success_count", 0)
                pattern.confidence = data.get("confidence", 0.0)
                pattern.last_reinforced = incoming_ts
                pattern.source_episode_ids = data.get("source_episode_ids", [])
                pattern.save()
                logger.info(f"Pattern {pattern.pattern_id} updated from import")
            return pattern

        # Create new pattern
        now = time.time()
        return cls.create(
            problem_topology=data["problem_topology"],
            affected_layer=data["affected_layer"],
            canonical_tool_sequence=data.get("canonical_tool_sequence", []),
            warnings=data.get("warnings", []),
            shortcuts=data.get("shortcuts", []),
            success_rate=data.get("success_rate", 0.0),
            sample_count=data.get("sample_count", 0),
            success_count=data.get("success_count", 0),
            confidence=data.get("confidence", 0.0),
            last_reinforced=data.get("last_reinforced", now),
            created_at=data.get("created_at", now),
            source_episode_ids=data.get("source_episode_ids", []),
        )

    # === Cleanup ===

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 365) -> int:
        """Delete patterns not reinforced in max_age_days. Returns count deleted."""
        cutoff = time.time() - (max_age_days * 86400)
        all_patterns = cls.query.all()
        deleted = 0
        for pattern in all_patterns:
            last = pattern.last_reinforced or pattern.created_at or 0
            if last < cutoff:
                pattern.delete()
                deleted += 1
        return deleted
