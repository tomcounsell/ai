"""Memory model for the subconscious memory system.

Level 3 popoto model with DecayingSortedField, ConfidenceField, BM25Field,
WriteFilterMixin, AccessTrackerMixin, and ExistenceFilter.

Memories are partitioned by project_key for per-project isolation.
Human messages are saved with high importance (InteractionWeight.HUMAN = 6.0),
agent observations with low importance (InteractionWeight.AGENT = 1.0).

The ExistenceFilter fingerprints on content, enabling O(1) bloom checks
for topic relevance before running the BM25 + RRF fusion retrieval.
"""

import logging

from config.memory_defaults import apply_defaults

# Apply tuned defaults before model definition
apply_defaults()

from popoto import (  # noqa: E402
    AccessTrackerMixin,
    AutoKeyField,
    BM25Field,
    ConfidenceField,
    DecayingSortedField,
    DictField,
    FloatField,
    KeyField,
    Model,
    StringField,
    WriteFilterMixin,
)
from popoto.fields.existence_filter import ExistenceFilter  # noqa: E402

logger = logging.getLogger(__name__)

# Valid source types for Memory.source field
SOURCE_HUMAN = "human"
SOURCE_AGENT = "agent"
SOURCE_SYSTEM = "system"
SOURCE_KNOWLEDGE = "knowledge"


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
        source: Origin type — "human", "agent", "system", or "knowledge".
        reference: Generic JSON pointer for actionable next steps. Used by
            knowledge-sourced memories to point to the source file, e.g.
            {"tool": "read_file", "params": {"file_path": "/path/to/doc.md"}}.
            Empty string for memories without a reference.
        metadata: Optional structured metadata dict with keys:
            category (str): "correction", "decision", "pattern", "surprise"
            file_paths (list[str]): Referenced file paths
            tags (list[str]): Domain tags (1-3 short keywords)
            tool_names (list[str]): Tool names from the session context
            dismissal_count (int): Consecutive dismissals before reset
            last_outcome (str): "acted" or "dismissed"
            outcome_history (list[dict]): Last N outcome entries, each with:
                outcome (str): "acted" or "dismissed"
                reasoning (str): One-sentence LLM explanation
                ts (int): Unix timestamp of the outcome
        relevance: Decay-sorted index, partitioned by project_key.
        confidence: Bayesian confidence, updated by ObservationProtocol.
        bm25: BM25 keyword search index on content for ranked retrieval.
        bloom: ExistenceFilter for O(1) topic pre-checks.
    """

    memory_id = AutoKeyField()
    agent_id = KeyField()
    project_key = KeyField()
    content = StringField(default="")
    importance = FloatField(default=1.0)
    source = StringField(
        default=SOURCE_AGENT
    )  # SOURCE_HUMAN, SOURCE_AGENT, SOURCE_SYSTEM, SOURCE_KNOWLEDGE
    reference = StringField(default="")  # Generic JSON pointer (e.g. tool call, URL, entity)
    metadata = DictField(default=dict)

    relevance = DecayingSortedField(
        base_score_field="importance",
        partition_by="project_key",
    )
    confidence = ConfidenceField(initial_confidence=0.5)
    bm25 = BM25Field(source="content")
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
