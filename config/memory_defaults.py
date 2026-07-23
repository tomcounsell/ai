"""Centralized Defaults overrides for the subconscious memory system.

Call apply_defaults() at import time (e.g., in models/memory.py) to set
popoto Defaults before the Memory model is defined. Explicit field kwargs
always win over Defaults — these are fallback baselines.

Tuning guide:
    DECAY_RATE: Controls how fast memories fade. Lower = slower decay.
        0.3 keeps memories relevant ~9 days at importance 1.0.
        0.5 (popoto default) keeps memories ~1 day at importance 1.0.
        Higher importance scores extend lifetime quadratically.

    WF_MIN_THRESHOLD: Minimum importance to persist a memory.
        0.15 is slightly more permissive than popoto default (0.2).
        Agent observations at 1.0 pass easily. Only truly noise-level
        records (< 0.15) are silently dropped.

    WF_PRIORITY_THRESHOLD: Above this, memories are tagged as priority.
        Priority memories get preferential treatment in retrieval ranking.

    INITIAL_CONFIDENCE: Starting confidence for new memories.
        0.5 is neutral — neither trusted nor distrusted.

    ACTED_CONFIDENCE_SIGNAL / CONTRADICTED_CONFIDENCE_SIGNAL:
        How strongly outcomes affect confidence.
        0.85/0.15 are slightly more conservative than popoto defaults
        (0.9/0.1) to avoid overreacting to noisy bigram detection.
"""

from popoto import Defaults

# Tuned for subconscious memory use case
MEMORY_DECAY_RATE = 0.3
MEMORY_WF_MIN_THRESHOLD = 0.15
MEMORY_WF_PRIORITY_THRESHOLD = 0.7
MEMORY_INITIAL_CONFIDENCE = 0.5
MEMORY_ACTED_SIGNAL = 0.85
MEMORY_CONTRADICTED_SIGNAL = 0.15
MEMORY_DISMISSED_WEAKEN = 0.85

# Default project key used when VALOR_PROJECT_KEY env var is not set.
# "dm" is semantically reserved for Telegram direct messages -- do not use
# it as a fallback for non-DM contexts. "default" signals a misconfigured
# project rather than silently mislabeling records as DM-sourced.
DEFAULT_PROJECT_KEY = "default"

# Reciprocal Rank Fusion (RRF) tuning
# RRF_K controls blending uniformity: higher values give more weight to lower-ranked
# items across lists. k=60 is the standard default from the original RRF paper
# (Cormack et al., 2009). Lower k (e.g., 20) favors top-ranked items more aggressively.
RRF_K = 60

# Post-fusion relevance gate.
# After rrf_fuse() returns a list of (key, score) tuples, drop entries whose
# fused score is below this floor. The default value 1 / (RRF_K + 50) requires
# a record to rank in the top-50 of at least one signal before surviving the
# gate. Concretely with RRF_K=60: floor ≈ 0.00909.
#
# Calibration math (spike-1):
#   - A record at rank 1 in 1 signal scores 1/(60+1) ≈ 0.01639 (passes)
#   - A record at rank 50 in 1 signal scores 1/(60+50) ≈ 0.00909 (boundary)
#   - A record at rank 51+ in only one signal scores below the floor (filtered)
#
# Set to None to disable the gate globally. The CLI defaults to None for
# back-compat; the recall hooks (agent/memory_hook.py + memory_bridge.py)
# pass this constant explicitly so the gate is ON by default for them.
RRF_MIN_SCORE: float | None = 1 / (RRF_K + 50)

# Bloom pre-check minimum unique-token hit count.
# Recall paths tokenize the query, drop noise words, and probe the
# ExistenceFilter bloom for each remaining token. Historically the gate
# accepted any single hit; that surfaced records for low-precision queries
# whose only overlap was a common token (e.g. "redis" inside an unrelated
# multi-word query). BLOOM_MIN_HITS=2 requires at least two distinct tokens
# to register as "definitely possibly present" before BM25 + RRF runs.
#
# The bloom_hits == 0 branch (deja-vu / novel-territory fallback) is
# preserved unchanged at all sites that have it -- the new gate only kicks
# in for 1 <= bloom_hits < BLOOM_MIN_HITS, returning empty without emitting
# a deja-vu thought.
BLOOM_MIN_HITS: int = 2

# BM25 tuning parameters (passed through to popoto defaults)
BM25_K1 = 1.2  # Term frequency saturation. Higher = more weight to repeated terms.
BM25_B = 0.75  # Length normalization. 0 = no normalization, 1 = full normalization.

# Injection limits
MAX_THOUGHTS_PER_INJECTION = 3
INJECTION_WINDOW_SIZE = 3  # tool calls per window
INJECTION_BUFFER_SIZE = 9  # total tool calls in rolling buffer

# Latency budget for the user-facing prefetch path
# (.claude/hooks/hook_utils/memory_bridge.py::prefetch).
# Warn-level log is emitted when a single prefetch query exceeds this.
# The PostToolUse multi-cluster path uses a 15ms budget; prefetch is a
# single-call user-facing query that runs once per UserPromptSubmit, so
# a more generous budget is appropriate.
PREFETCH_LATENCY_WARN_MS = 200

# Deja vu thresholds -- control when vague recognition messages fire
# Shared between memory_bridge.py (hooks path) and agent/memory_hook.py (SDK path)
DEJA_VU_BLOOM_HIT_THRESHOLD = 3  # min bloom hits for "seen something related" thought
NOVEL_TERRITORY_KEYWORD_THRESHOLD = 7  # min unique keywords with zero bloom hits

# Dismissal tracking -- controls importance decay for chronically dismissed memories
DISMISSAL_DECAY_THRESHOLD = 3  # consecutive dismissals before importance decays
DISMISSAL_IMPORTANCE_DECAY = 0.7  # multiply importance by this on threshold breach
MIN_IMPORTANCE_FLOOR = 0.2  # never decay below this

# Outcome history -- how many outcome entries to keep per memory
MAX_OUTCOME_HISTORY = 10

# Orphaned-sidecar sweep (reflections/memory/memory_outcome_resolve.py).
# TTL-only gating: a session sidecar's mtime is refreshed on every recall
# injection (memory_bridge.py's _save_sidecar), so a live session keeps its
# sidecar fresh. This TTL must exceed the maximum plausible gap between
# recall injections in a live session (not just a single turn), with
# headroom -- a mis-estimate is harmless because "deferred" is a no-op
# outcome. Grain of salt: provisional/tunable, no empirical measurement of
# the real-world injection-gap distribution has been done yet.
INJECTION_RESOLVE_TTL = 6 * 60 * 60  # 6 hours, in seconds

# Per-run cap on how many stale sidecars the outcome-resolve sweep processes
# in a single invocation, bounding worst-case sweep latency/blast radius.
# Grain of salt: provisional/tunable -- picked to comfortably exceed normal
# crash volume per reflection tick without unbounded work on a backlog.
OUTCOME_RESOLVE_MAX_PER_RUN = 200

# Category recall weights -- post-fusion re-ranking multipliers for memory recall.
# After RRF fusion returns scored results, each result's effective score is
# multiplied by the weight for its category before re-sorting. Higher weight = more
# likely to surface. Set all to 1.0 to disable re-ranking (no-op).
CATEGORY_RECALL_WEIGHTS: dict[str, float] = {
    "correction": 1.5,
    "decision": 1.3,
    "pattern": 1.0,
    "surprise": 1.0,
    "default": 1.0,
}

# -----------------------------------------------------------------------------
# Distilled human ingest (Phase 3, docs/plans/memory-distilled-ingest.md).
#
# `.claude/hooks/hook_utils/memory_bridge.py::ingest()` used to persist human
# prompts verbatim at a flat importance=6.0. It now persists a PROVISIONAL
# record synchronously (cheap, no LLM call, deadline-safe) and a later
# out-of-band reflection distills it into a standalone fact with a
# content-derived importance.
# -----------------------------------------------------------------------------

# Provisional-insert importance for hook-path human prompts. Deliberately set
# ABOVE the bare MEMORY_WF_MIN_THRESHOLD (0.15) floor AND above
# MIN_IMPORTANCE_FLOOR (0.2, above): flooring the provisional record at
# exactly the write-filter floor would be a near-term recall regression -- a
# just-ingested record would rank far below a settled memory during the
# immediate-follow-up access pattern (the human refers back to what they just
# said before the backfill reflection has distilled it). 3.0 keeps the
# provisional record comfortably retrievable in the pre-distillation window
# while sitting below the settled distilled top band, so it is never mistaken
# for a high-value settled memory. It carries `metadata.distill_status ==
# "provisional"` so it is always distinguishable from a settled record. See
# spike-2b in the plan.
PROVISIONAL_INGEST_IMPORTANCE = 3.0

# Max distillation attempts before a provisional record is transitioned to the
# terminal `distill_abandoned` state (metadata.distill_status) and is never
# re-scanned again. Bounds a permanently-refusing record (LLM keeps
# timing out / refusing / returning unparseable output) from retrying forever
# and crowding out fresh provisional records in the backfill reflection's
# per-run queue. See spike-2b / Risk 1 in the plan.
MAX_DISTILL_ATTEMPTS = 5

# Maximum provisional records the memory-distill-backfill reflection processes
# per run. Bounds Haiku load per cycle -- mirrors the shape of
# MAX_BACKFILL_PER_RUN in reflections/memory/memory_embedding_backfill.py, but
# scaled down: a Haiku distillation call is a slower/more expensive network
# round-trip than a local embedding provider call, and this reflection runs at
# a 300s cadence (vs 86400s for the embedding backfill), so a much smaller
# per-run cap keeps steady-state load bounded. 50 drains a realistic
# ingest-rate backlog within a couple of cycles without saturating the shared
# Anthropic semaphore (agent.anthropic_client.semaphore_slot).
MAX_DISTILL_PER_RUN = 50

# Human-source prior for compute_ingest_importance() (Phase 3 distillation,
# docs/plans/memory-distilled-ingest.md). Every distillation-backfill target is
# a SOURCE_HUMAN record -- the only writer that seeds `distill_status:
# "provisional"` is `.claude/hooks/hook_utils/memory_bridge.py::ingest()`, which
# always saves `source=SOURCE_HUMAN` -- so a single constant covers every
# distillation caller; no per-source branching is needed here (contrast with
# `agent.memory_extraction.CATEGORY_IMPORTANCE`, which does vary per distilled
# category).
#
# Value: 2.0. Combined with CATEGORY_IMPORTANCE's 1.0-4.0 range (correction/
# decision=4.0, pattern/surprise=1.0), a settled distilled record's importance
# spans 3.0 (pattern/surprise) to 6.0 (correction/decision) -- comparable to
# the historical flat 6.0 verbatim value at the top end, with real spread
# below it, rather than every human record clustering at one point (the whole
# point of this feature -- see the plan's Problem statement).
DISTILL_SOURCE_WEIGHT = 2.0


def compute_ingest_importance(source_weight: float, content_value: float) -> float:
    """Combine a source-prior weight and a content-value score into an importance.

    Formula: ``importance = source_weight + content_value``, then floored at
    ``MEMORY_WF_MIN_THRESHOLD``.

    ``source_weight`` encodes the human>agent prior (a caller passes a larger
    weight for human-sourced content than agent-sourced content).
    ``content_value`` is the distillation-derived signal -- e.g. a lookup into
    ``agent.memory_extraction.CATEGORY_IMPORTANCE`` keyed by the distilled
    category (correction/decision/pattern/surprise). Addition (rather than a
    multiplier) keeps both terms legible and independently tunable: a
    high-value category on a low-weight source still contributes meaningfully,
    and vice versa.

    The floor is NOT cosmetic. ``WriteFilterMixin._check_write_filter()``
    (vendored popoto, see ``popoto/models/base.py``) runs on EVERY
    ``Memory.save()`` call -- including partial ``save(update_fields=[...])``
    UPDATEs -- before the update-fields branch, and silently drops (raises
    ``SkipSaveException``, ``save()`` returns ``False``) any record whose
    ``compute_filter_score()`` (== ``self.importance``, see
    ``models/memory.py``) is below ``Memory._wf_min_threshold`` (0.15). A
    distillation re-save with a below-floor computed importance would
    therefore vanish silently, leaving the record permanently stuck
    `distill_status=provisional` with no write ever landing. Clamping here,
    at construction, makes that failure mode structurally impossible rather
    than relying on every caller to remember the floor.
    """
    return max(source_weight + content_value, MEMORY_WF_MIN_THRESHOLD)


def apply_defaults() -> None:
    """Override popoto Defaults with memory-tuned values.

    Call this once before defining the Memory model. Safe to call multiple times.
    Also configures the embedding provider for EmbeddingField if available.
    """
    Defaults.DECAY_RATE = MEMORY_DECAY_RATE
    Defaults.WF_MIN_THRESHOLD = MEMORY_WF_MIN_THRESHOLD
    Defaults.WF_PRIORITY_THRESHOLD = MEMORY_WF_PRIORITY_THRESHOLD
    Defaults.INITIAL_CONFIDENCE = MEMORY_INITIAL_CONFIDENCE
    Defaults.ACTED_CONFIDENCE_SIGNAL = MEMORY_ACTED_SIGNAL
    Defaults.CONTRADICTED_CONFIDENCE_SIGNAL = MEMORY_CONTRADICTED_SIGNAL
    Defaults.DISMISSED_CYCLE_WEAKEN_FACTOR = MEMORY_DISMISSED_WEAKEN

    # Configure embedding provider (non-blocking, graceful degradation)
    try:
        from agent.embedding_provider import configure_embedding_provider

        configure_embedding_provider()
    except Exception:
        pass  # Embedding is optional; fail silently
