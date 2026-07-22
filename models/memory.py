"""Memory model for the subconscious memory system.

Level 3 popoto model with DecayingSortedField, ConfidenceField, BM25Field,
GracefulEmbeddingField, WriteFilterMixin, AccessTrackerMixin, and ExistenceFilter.

Memories are partitioned by project_key for per-project isolation.
Human messages are saved with high importance (InteractionWeight.HUMAN = 6.0),
agent observations with low importance (InteractionWeight.AGENT = 1.0).

The ExistenceFilter fingerprints on content, enabling O(1) bloom checks
for topic relevance before running the BM25 + RRF fusion retrieval.
GracefulEmbeddingField generates vector embeddings via OllamaProvider on save,
enabling semantic similarity as a fourth RRF signal in retrieval. When the
provider is slow or unreachable it persists the record without a vector
(issue #1904) instead of dropping it; the memory-embedding-backfill reflection
re-embeds it once the provider is healthy.
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

from models.graceful_embedding_field import GracefulEmbeddingField  # noqa: E402
from models.memory_gate import _increment_gate_counter  # noqa: E402

logger = logging.getLogger(__name__)

# Valid source types for Memory.source field
SOURCE_HUMAN = "human"
SOURCE_AGENT = "agent"
SOURCE_SYSTEM = "system"
SOURCE_KNOWLEDGE = "knowledge"


def _key_exists(db_key) -> bool:
    """Best-effort existence check distinguishing INSERT from UPDATE.

    Returns False on any error (including Redis being unreachable), which
    defaults the caller to treating the write as an INSERT -- the safer
    choice, since it preserves the content gate's junk-blocking guarantee
    on the common insert path. An `exists()` failure while the write itself
    succeeds is nearly impossible (both use the same Redis handle); the
    only exposure is a rare junk-content UPDATE whose existence check
    errored, and Phase 4 (#2203, existing-fragment pruning) removes that
    class of record anyway.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        return bool(POPOTO_REDIS_DB.exists(str(db_key)))
    except Exception:
        return False


def _warn_if_legacy_namespace(project_key: "str | None") -> None:
    """Regression detector for retired/discouraged Memory project_key namespaces.

    Logs a warning (with stack trace) on `project_key="dm"` — the retired namespace
    from PR #820 (#811). Logs at DEBUG (with stack trace) on `project_key="default"`
    — still legitimate during single-machine bootstrap and test fixtures, but we
    want an audit trail that surfaces under LOG_LEVEL=DEBUG.

    Other values (including None and "") are no-ops.

    This guard sits inside `Memory.safe_save` and so only catches writers that go
    through that classmethod. It is deliberately bypassable by direct
    `Memory(...).save()` callers — those callsites are tracked under the follow-up
    "Unify Memory writer project_key resolution" issue (#1173 Rabbit Holes). The
    guard is a regression detector for the writers we are *not* editing in this PR
    (extraction, hooks, indexer); the bridge-side patches at telegram_bridge.py
    `:917`, `:1003-1040`, and `:2031` ARE the creation-site prevention for the
    `dm` leak.

    Never raises — wrapped in try/except so it can't crash the writer path even
    if the logger config is broken.
    """
    try:
        if project_key == "dm":
            logger.warning(
                "Memory write to retired 'dm' namespace (#811, #1173). "
                "This should never happen on current main; investigate the caller.",
                stack_info=True,
            )
        elif project_key == "default":
            logger.debug(
                "Memory write to 'default' namespace — legitimate during bootstrap "
                "or test fixtures, but tracked for the unified-helper follow-up.",
                stack_info=True,
            )
    except Exception:
        # Never let logging crash the writer path
        pass


class Memory(WriteFilterMixin, AccessTrackerMixin, Model):
    """Subconscious memory record.

    Stores observations, human instructions, and agent learnings.
    Automatically decays over time, with importance-weighted persistence.

    Fields:
        memory_id: Auto-generated unique key.
        agent_id: Who created this memory (sender name or agent identifier).
        project_key: Project partition key for isolation.
        content: The memory content text (max ~500 chars for efficiency).
        title: One-line descriptive label generated asynchronously by a local
            LLM (Ollama). Used for compact stub injection in the recall path
            (progressive disclosure). Empty string until the async title
            generator writes back; stub renders as `[category]` only on
            empty title (graceful degradation when Ollama unreachable).
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
        embedding: Vector embedding of content for semantic similarity search.
        bloom: ExistenceFilter for O(1) topic pre-checks.
    """

    memory_id = AutoKeyField()
    agent_id = KeyField()
    project_key = KeyField()
    content = StringField(default="")
    title = StringField(default="")
    importance = FloatField(default=1.0)
    source = StringField(
        default=SOURCE_AGENT
    )  # SOURCE_HUMAN, SOURCE_AGENT, SOURCE_SYSTEM, SOURCE_KNOWLEDGE
    reference = StringField(default="")  # Generic JSON pointer (e.g. tool call, URL, entity)
    metadata = DictField(default=dict)
    superseded_by = StringField(default="")
    # Empty string = active record.
    # Non-empty = memory_id of the merged replacement record.
    # Populated only by the consolidation reflection; never set by ingestion paths.
    superseded_by_rationale = StringField(default="")
    # Empty string = not superseded.
    # Non-empty = one-sentence rationale from Haiku explaining why the merge was proposed.
    # Preserves audit trail for human review months after the merge occurred.

    relevance = DecayingSortedField(
        base_score_field="importance",
        partition_by="project_key",
    )
    confidence = ConfidenceField(initial_confidence=0.5)
    bm25 = BM25Field(source="content")
    embedding = GracefulEmbeddingField(source="content")

    # Opt-in marker for EmbeddingField.garbage_collect (Popoto >= 1.6.0).
    # Without this attribute, garbage_collect is a no-op for safety —
    # see #1214 and the orphan-cleanup plan for the rationale.
    __embedding_garbage_collect__ = True
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

    def save(self, *args, **kwargs):
        """Content-gate new records before persisting (issue #2201).

        Runs on INSERT only -- an existing key means this is an UPDATE
        (e.g. the outcome/metadata re-save in
        `agent/memory_extraction.py`'s outcome-detection loop, which calls
        a bare `m.save()` on an already-persisted record). Gating that
        re-save would return `False` and silently lose the
        outcome/`dismissal_count`/`last_outcome` write on any record whose
        content happens to be below-floor or a legacy fragment already in
        the store. So the gate is skipped whenever `self.db_key` already
        exists in Redis.

        On a genuine INSERT, `gate_reason` (agent/memory_quality.py)
        classifies `self.content`; a non-None reason increments its
        `{project_key}:memory-gate:{reason}` counter and returns `False`
        without ever calling `super().save()` -- this mirrors
        `WriteFilterMixin`'s own drop contract (`save()` returning `False`
        on rejection), so `safe_save()`'s existing `result is False ->
        None` mapping and logging need no changes.

        This counts each rejected write exactly once: `save()` is called
        once per persist attempt (verified against `Model.save()` in the
        vendored popoto package, which calls `_check_write_filter()`
        exactly once per invocation), so placing the counter here --
        rather than inside `compute_filter_score()`, which
        `WriteFilterMixin` could in principle call more than once per
        `save()` -- avoids any double-count hazard.

        `compute_filter_score()` above is left completely unchanged: it
        still filters on raw `importance` only. The content gate is a
        distinct concern living entirely in this override.

        `gate_reason` is imported locally (not at module level) because
        `agent.memory_quality` is dependency-light on its own, but Python
        still executes `agent/__init__.py` on any `agent.*` submodule
        import, and that package `__init__` pulls in `agent.session_health`
        -> `models.memory` -> a circular partial-init `ImportError` at
        module load time. Deferring the import into the method body avoids
        the cycle entirely: by the time any `save()` call happens,
        `models.memory` has already finished loading.
        """
        from agent.memory_quality import gate_reason

        if not _key_exists(self.db_key):
            reason = gate_reason(self.content)
            if reason:
                _increment_gate_counter(self.project_key, reason)
                return False
        return super().save(*args, **kwargs)

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
            # Legacy-namespace audit fires only AFTER save() succeeds (#1173 C2).
            # We don't want to spam warnings on writes that ultimately got filtered
            # out by WriteFilterMixin — only on writes that actually persisted.
            _warn_if_legacy_namespace(kwargs.get("project_key"))
            logger.debug(
                f"Memory saved: source={kwargs.get('source', 'agent')}, "
                f"project={kwargs.get('project_key', 'unknown')}"
            )
            return m
        except Exception as e:
            logger.warning(f"Memory save failed (non-fatal): {e}")
            return None
