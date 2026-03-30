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

# Default project key used when VALOR_PROJECT_KEY env var is not set
DEFAULT_PROJECT_KEY = "dm"

# Reciprocal Rank Fusion (RRF) tuning
# RRF_K controls blending uniformity: higher values give more weight to lower-ranked
# items across lists. k=60 is the standard default from the original RRF paper
# (Cormack et al., 2009). Lower k (e.g., 20) favors top-ranked items more aggressively.
RRF_K = 60

# Injection limits
MAX_THOUGHTS_PER_INJECTION = 3
INJECTION_WINDOW_SIZE = 3  # tool calls per window
INJECTION_BUFFER_SIZE = 9  # total tool calls in rolling buffer

# Deja vu thresholds -- control when vague recognition messages fire
# Shared between memory_bridge.py (hooks path) and agent/memory_hook.py (SDK path)
DEJA_VU_BLOOM_HIT_THRESHOLD = 3  # min bloom hits for "seen something related" thought
NOVEL_TERRITORY_KEYWORD_THRESHOLD = 7  # min unique keywords with zero bloom hits

# Dismissal tracking -- controls importance decay for chronically dismissed memories
DISMISSAL_DECAY_THRESHOLD = 3  # consecutive dismissals before importance decays
DISMISSAL_IMPORTANCE_DECAY = 0.7  # multiply importance by this on threshold breach
MIN_IMPORTANCE_FLOOR = 0.2  # never decay below this

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


def apply_defaults() -> None:
    """Override popoto Defaults with memory-tuned values.

    Call this once before defining the Memory model. Safe to call multiple times.
    """
    Defaults.DECAY_RATE = MEMORY_DECAY_RATE
    Defaults.WF_MIN_THRESHOLD = MEMORY_WF_MIN_THRESHOLD
    Defaults.WF_PRIORITY_THRESHOLD = MEMORY_WF_PRIORITY_THRESHOLD
    Defaults.INITIAL_CONFIDENCE = MEMORY_INITIAL_CONFIDENCE
    Defaults.ACTED_CONFIDENCE_SIGNAL = MEMORY_ACTED_SIGNAL
    Defaults.CONTRADICTED_CONFIDENCE_SIGNAL = MEMORY_CONTRADICTED_SIGNAL
    Defaults.DISMISSED_CYCLE_WEAKEN_FACTOR = MEMORY_DISMISSED_WEAKEN
