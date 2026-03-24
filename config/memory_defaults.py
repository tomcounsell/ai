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
        Priority memories get preferential treatment in ContextAssembler.

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

# ContextAssembler tuning
MEMORY_SURFACING_THRESHOLD = 0.4  # slightly lower than default 0.5 to surface more

# Injection limits
MAX_THOUGHTS_PER_INJECTION = 3
INJECTION_WINDOW_SIZE = 3  # tool calls per window
INJECTION_BUFFER_SIZE = 9  # total tool calls in rolling buffer


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
    Defaults.DEFAULT_SURFACING_THRESHOLD = MEMORY_SURFACING_THRESHOLD
