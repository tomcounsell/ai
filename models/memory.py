"""Memory model for the subconscious memory system.

Level 3 popoto model with DecayingSortedField, ConfidenceField,
WriteFilterMixin, AccessTrackerMixin, and ExistenceFilter.

Memories are partitioned by project_key for per-project isolation.
Human messages are saved with high importance (InteractionWeight.HUMAN = 6.0),
agent observations with low importance (InteractionWeight.AGENT = 1.0).

The ExistenceFilter fingerprints on content, enabling O(1) bloom checks
for topic relevance before running the full ContextAssembler query.
"""

import logging

from config.memory_defaults import apply_defaults

# Apply tuned defaults before model definition
apply_defaults()

from popoto import (  # noqa: E402
    AccessTrackerMixin,
    AutoKeyField,
    ConfidenceField,
    DecayingSortedField,
    FloatField,
    KeyField,
    Model,
    StringField,
    WriteFilterMixin,
)
from popoto.fields.existence_filter import ExistenceFilter  # noqa: E402

logger = logging.getLogger(__name__)


class Memory(WriteFilterMixin, AccessTrackerMixin, Model):
    """Subconscious memory record.

    Stores observations, human instructions, and agent learnings.
    Automatically decays over time, with importance-weighted persistence.

    Fields:
        memory_id: Auto-generated unique key.
        agent_id: Who created this memory (sender name or agent identifier).
        project_key: Project partition key for isolation.
        content: The memory content text (max ~500 chars for efficiency).
        importance: Numeric importance score. Human=6.0, Agent=1.0.
        source: Origin type — "human", "agent", or "system".
        relevance: Decay-sorted index, partitioned by project_key.
        confidence: Bayesian confidence, updated by ObservationProtocol.
        bloom: ExistenceFilter for O(1) topic pre-checks.
    """

    memory_id = AutoKeyField()
    agent_id = KeyField()
    project_key = KeyField()
    content = StringField(default="")
    importance = FloatField(default=1.0)
    source = StringField(default="agent")  # "human", "agent", "system"

    relevance = DecayingSortedField(
        base_score_field="importance",
        partition_by="project_key",
    )
    confidence = ConfidenceField(initial_confidence=0.5)
    bloom = ExistenceFilter(
        error_rate=0.01,
        capacity=100_000,
        fingerprint_fn=lambda inst: inst.content or "",
    )

    # WriteFilterMixin thresholds (inherited from Defaults via apply_defaults)
    _wf_min_threshold = 0.15
    _wf_priority_threshold = 0.7

    def compute_filter_score(self):
        """Score used by WriteFilterMixin to gate persistence.

        Returns the importance value. Records below _wf_min_threshold
        are silently dropped on save().
        """
        return self.importance or 0.0

    @classmethod
    def safe_save(cls, **kwargs) -> "Memory | None":
        """Save a memory record with full error handling.

        Returns the saved Memory instance, or None if save failed or was
        filtered out by WriteFilterMixin.

        This is the recommended entry point for all memory creation.
        Never raises — all exceptions are caught and logged.
        """
        try:
            m = cls(**kwargs)
            result = m.save()
            if result is False:
                logger.debug(
                    f"Memory filtered out by WriteFilter "
                    f"(importance={kwargs.get('importance', 1.0)})"
                )
                return None
            logger.debug(
                f"Memory saved: source={kwargs.get('source', 'agent')}, "
                f"project={kwargs.get('project_key', 'unknown')}"
            )
            return m
        except Exception as e:
            logger.warning(f"Memory save failed (non-fatal): {e}")
            return None
