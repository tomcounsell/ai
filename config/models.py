"""
Centralized AI Model Configuration

This module defines all AI models used throughout the codebase.
Import model constants from here rather than hardcoding model strings.

When model versions change, update them in ONE place here.
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

# =============================================================================
# ANTHROPIC DIRECT API MODELS
# Format: model ID as used with Anthropic's API directly
# =============================================================================

# Haiku 4.5 - Fast and cheap, good for simple tasks
# Use cases: summarization, classification, health checks, simple extraction
# Strengths: Speed, cost-efficiency, good enough for most routine tasks
HAIKU = "claude-haiku-4-5-20251001"

# Sonnet 4.5 - Balanced reasoning and speed
# Use cases: code generation, complex analysis, documentation, test judgment
# Strengths: Better reasoning than Haiku, still relatively fast
SONNET = "claude-sonnet-4-5-20250929"

# Sonnet 4 (previous gen) - Use SONNET instead for new code
# Kept for reference during migration
SONNET_4 = "claude-sonnet-4-20250514"

# Opus 4.5 - Best reasoning, slowest and most expensive
# Use cases: Complex multi-step reasoning, nuanced analysis, creative tasks
# Strengths: Highest quality output, best at handling ambiguity
OPUS = "claude-opus-4-5-20251101"


# =============================================================================
# OPENROUTER API ENDPOINTS
# Override via environment variables for custom/proxy deployments
# =============================================================================

# Chat completions endpoint (used by summarizer, tools, scripts).
# Override: set OPENROUTER_URL env var to point at a custom or proxy endpoint.
OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")


# =============================================================================
# OPENROUTER MODELS
# Format: provider/model as used with OpenRouter API
# Use OpenRouter for: experimenting with new models, non-Anthropic providers
# =============================================================================

# Anthropic models via OpenRouter (fallback when no direct API key)
OPENROUTER_HAIKU = "anthropic/claude-haiku-4-5-20251001"
OPENROUTER_SONNET = "anthropic/claude-sonnet-4-5-20250929"
OPENROUTER_OPUS = "anthropic/claude-opus-4-5-20251101"

# -----------------------------------------------------------------------------
# VISION ANALYSIS ALTERNATIVES (via OpenRouter)
# For image understanding, OCR, diagram interpretation
# -----------------------------------------------------------------------------

# Google Gemini - Best overall vision, 1M+ token context, video understanding
OPENROUTER_GEMINI_VISION = "google/gemini-2.5-pro"

# Qwen3-VL - Strong open-source alternative, good multimodal reasoning
OPENROUTER_QWEN_VISION = "qwen/qwen3-vl-72b"

# Pixtral - Handles multiple images, native resolution, 128K context
OPENROUTER_PIXTRAL = "mistralai/pixtral-large"

# -----------------------------------------------------------------------------
# IMAGE GENERATION MODELS (via OpenRouter)
# For creating images from text prompts (Nano Banana style)
# -----------------------------------------------------------------------------

# Gemini 3 Pro - Native image generation with aspect ratio control (via OpenRouter)
OPENROUTER_GEMINI_IMAGE_GEN = "google/gemini-3-pro-image-preview"

# OpenAI gpt-image-1 - native image generation (via OpenAI Images API directly)
OPENAI_IMAGE_GEN = "gpt-image-1"

# Provider alias -> default model. Lets callers say `--provider openai` instead of
# memorizing model strings; Gemini stays the default so existing behavior is unchanged.
IMAGE_GEN_PROVIDERS = {
    "gemini": OPENROUTER_GEMINI_IMAGE_GEN,
    "openai": OPENAI_IMAGE_GEN,
}

# Image generation aspect ratios (width x height) — Gemini's native vocabulary.
IMAGE_ASPECT_RATIOS = {
    "1:1": (1024, 1024),  # Square
    "16:9": (1344, 768),  # Landscape wide
    "9:16": (768, 1344),  # Portrait tall (stories/reels)
    "4:3": (1184, 864),  # Classic landscape
    "3:4": (864, 1184),  # Classic portrait
    "3:2": (1248, 832),  # Photo landscape
    "2:3": (832, 1248),  # Photo portrait
    "21:9": (1536, 672),  # Ultrawide/cinematic
}

# gpt-image-1 only accepts a fixed size set (1024x1024, 1536x1024, 1024x1536).
# Map our richer aspect-ratio vocabulary onto the nearest supported size.
OPENAI_IMAGE_SIZES = {
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "4:3": "1536x1024",
    "3:2": "1536x1024",
    "21:9": "1536x1024",
    "9:16": "1024x1536",
    "3:4": "1024x1536",
    "2:3": "1024x1536",
}

# -----------------------------------------------------------------------------
# ULTRA-CHEAP EXPERIMENT MODELS (via OpenRouter)
# For autonomous prompt optimization — hypothesis generation at ~$0.001/call
# -----------------------------------------------------------------------------

# Kimi K2.5 - Strong reasoning at ultra-low cost
OPENROUTER_KIMI_K2_5 = "moonshotai/kimi-k2.5"

# Qwen3 32B - Good code understanding, ultra-cheap
OPENROUTER_QWEN3_32B = "qwen/qwen3-32b"

# Gemma 4 E2B - Free tier option, multimodal, 128K context
OPENROUTER_GEMMA4_FREE = "google/gemma-4-e2b:free"


# =============================================================================
# LOCAL OLLAMA MODELS
# Local Ollama steady state per machine:
#   - granite4.1:3b  — classification / structured-output (the model already
#     resident for the granite PTY operator; reused for bridge message routing,
#     memory-audit, and email triage). This is the single local classifier.
#   - nomic-embed-text — embeddings (out of scope here).
#   - gemma4:31b-mlx — OPTIONAL local generation on RAM-rich Apple-Silicon hosts
#     (otherwise generation goes to Ollama Cloud gemma4:31b-cloud). Selected by
#     `ollama_generation_model` (config/settings.py), not a constant here.
# Classification tasks read OLLAMA_CLASSIFIER_MODEL; generation tasks read
# settings.models.ollama_generation_model. gemma4:e2b is retired (superseded).
# =============================================================================

# The single local classifier model. Granite is already resident for the PTY
# operator, so reusing it for bridge routing / memory-audit / email triage costs
# zero extra GPU memory. Kept in sync with granite_classifier.DEFAULT_MODEL,
# which imports this constant (single source of truth).
OLLAMA_CLASSIFIER_MODEL = "granite4.1:3b"

# Minimum host RAM (GB) required to run a local gemma4:31b-mlx generation model.
# Hypothesis: the ~18-20 GB resident MLX 32B must coexist with granite (~2 GB),
# nomic-embed-text (~0.4 GB), and the OS. Below this, generation must use the
# cloud variant. The RAM guard lives inside ensure_generation_model() so even a
# hand-edited bad config on a small host degrades to cloud instead of pulling
# ~18 GB of weights.
MIN_LOCAL_GEN_RAM_GB = 48

# Models superseded by the granite/cloud split — cleaned up during /update.
# gemma4:e2b retired here: classification moved to granite, generation to the
# configured gemma4:31b (cloud or mlx).
OLLAMA_SUPERSEDED_MODELS = [
    "gemma2:3b",
    "gemma3:4b",
    "gemma3:12b-it-qat",
    "qwen3:1.7b",
    "qwen3:4b",
    "gemma4:e2b",
]


def _host_ram_gb() -> float:
    """Return total physical RAM in GB via ``sysctl -n hw.memsize`` (macOS).

    Returns ``0.0`` if sysctl is unavailable or unparseable, which makes the
    RAM guard conservatively treat the host as too small for a local mlx model
    (degrade to cloud) rather than risk an ~18-20 GB pull.
    """
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if out.returncode == 0 and out.stdout.strip():
            return int(out.stdout.strip()) / (1024**3)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return 0.0


def _is_cloud_tag(model: str) -> bool:
    """True if ``model`` is an Ollama Cloud tag.

    Ollama Cloud tags appear in two shapes: ``<model>:cloud`` (e.g.
    ``glm-5.1:cloud``) and ``<model>:<size>-cloud`` (e.g. ``gemma4:31b-cloud``).
    Both route to Ollama's hosted GPUs; treat either as cloud.
    """
    return model.endswith(":cloud") or model.endswith("-cloud")


def ensure_generation_model(model: str | None = None) -> tuple[bool, str]:
    """Detection helper for the free-text *generation* model (NOT a startup gate).

    Unlike ``ensure_granite_model`` (a hard worker precondition for the
    classifier), this is a config-layer availability probe shared by ``/setup``,
    ``/update``, and the memory title-generator. Each caller consumes the typed
    ``(model_available, detail)`` result per its own failure-cost profile:
    ``/update`` warns, the title-generator skips persistence, the ai-judge
    hard-fails. It never exits the worker or suppresses a service restart.

    Branches by tag:

    - ``:cloud`` tag → near-no-op. A signed-in cloud tag is always reported
      available; the only real check is cloud-signin, which callers surface as a
      warning. Does NOT pull (cloud tags are lightweight pointers).
    - local ``-mlx`` / other tag → **RAM-guard FIRST**: an ``-mlx`` tag on a
      host below ``MIN_LOCAL_GEN_RAM_GB`` returns ``(False, ...)`` WITHOUT
      pulling, so a misconfigured small host never triggers an ~18-20 GB pull.
      Above the threshold, probe→pull-once→re-probe (mirrors
      ``ensure_granite_model`` for the local branch only).

    Args:
        model: generation tag to check. When ``None``, reads
            ``settings.models.ollama_generation_model`` (lazy import to avoid a
            config import cycle).

    Returns:
        ``(model_available, detail)`` — ``detail`` is a human-readable reason
        suitable for a log line.
    """
    if model is None:
        from config.settings import settings  # lazy: avoid import cycle

        model = settings.models.ollama_generation_model

    # Cloud branch: a cloud tag is a lightweight hosted pointer. No pull; the
    # only real precondition is cloud-signin, surfaced as a warning by callers.
    # Ollama Cloud tags appear as both ``<model>:cloud`` (e.g. glm-5.1:cloud)
    # and ``<model>:<size>-cloud`` (e.g. gemma4:31b-cloud) — accept both.
    if _is_cloud_tag(model):
        return True, "cloud tag assumed available"

    # Local branch. RAM-guard BEFORE any pull for mlx tags.
    if "mlx" in model:
        ram_gb = _host_ram_gb()
        if ram_gb < MIN_LOCAL_GEN_RAM_GB:
            return (
                False,
                f"RAM too low for local mlx ({ram_gb:.0f}GB < "
                f"{MIN_LOCAL_GEN_RAM_GB}GB) — use cloud",
            )

    try:
        from ollama import chat as _ollama_chat  # noqa: F401
    except ImportError:
        return False, "ollama python client is not importable"
    if shutil.which("ollama") is None:
        return False, "ollama CLI not found on PATH"

    def _probe() -> bool:
        try:
            r = subprocess.run(
                ["ollama", "run", model, "reply with the single word: ready"],
                capture_output=True,
                text=True,
                timeout=60.0,
            )
        except subprocess.TimeoutExpired:
            return False
        return r.returncode == 0 and bool(r.stdout.strip())

    if _probe():
        return True, f"{model} responsive"

    logger.warning("generation model %s not responsive — attempting pull...", model)
    try:
        subprocess.run(
            ["ollama", "pull", model],
            check=True,
            capture_output=True,
            text=True,
            timeout=1800.0,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out pulling {model}"
    except subprocess.CalledProcessError as e:
        return False, f"failed to pull {model}: {(e.stderr or '').strip() or e}"

    if _probe():
        return True, f"{model} pulled and responsive"
    return False, f"{model} still not responsive after pull"


# =============================================================================
# USE-CASE SPECIFIC ALIASES
# These map semantic use cases to specific models.
# Change these to easily switch models for specific tasks.
# =============================================================================

# Fast, cheap tasks (summarization, classification, health checks)
MODEL_FAST = HAIKU

# Reasoning tasks (test judgment, complex analysis, documentation generation)
MODEL_REASONING = SONNET

# Vision/multimodal tasks via OpenRouter (image analysis, tagging)
MODEL_VISION = OPENROUTER_SONNET

# Vision analysis alternatives (for experimentation)
MODEL_VISION_ALT = OPENROUTER_GEMINI_VISION  # Best overall vision model

# Image generation (Nano Banana style)
MODEL_IMAGE_GEN = OPENROUTER_GEMINI_IMAGE_GEN

# Experiment hypothesis generation (ultra-cheap, ~$0.001/call)
MODEL_EXPERIMENT = OPENROUTER_KIMI_K2_5

# Highest quality tasks (rarely needed, expensive)
MODEL_BEST = OPUS


# =============================================================================
# MODEL METADATA
# Documentation and capabilities for tooling/introspection
# =============================================================================

MODEL_INFO = {
    HAIKU: {
        "name": "Claude Haiku 4.5",
        "tier": "fast",
        "vision": True,
        "context_window": 200_000,
        "strengths": [
            "Speed - fastest response times",
            "Cost efficiency - cheapest per token",
            "Good for simple, routine tasks",
        ],
        "use_cases": [
            "Response summarization",
            "Health checks and monitoring",
            "Simple classification",
            "Text extraction",
        ],
    },
    SONNET: {
        "name": "Claude Sonnet 4.5",
        "tier": "balanced",
        "vision": True,
        "context_window": 200_000,
        "strengths": [
            "Strong reasoning capabilities",
            "Good balance of speed and quality",
            "Handles complex instructions well",
        ],
        "use_cases": [
            "Code generation and review",
            "Test judgment and evaluation",
            "Documentation generation",
            "Complex analysis",
            "Image analysis and tagging",
        ],
    },
    OPUS: {
        "name": "Claude Opus 4.5",
        "tier": "best",
        "vision": True,
        "context_window": 200_000,
        "strengths": [
            "Highest quality reasoning",
            "Best at nuanced understanding",
            "Handles ambiguity well",
            "Most creative",
        ],
        "use_cases": [
            "Complex multi-step reasoning",
            "Nuanced analysis",
            "Creative writing",
            "Difficult edge cases",
        ],
    },
    OPENROUTER_KIMI_K2_5: {
        "name": "Kimi K2.5 (via OpenRouter)",
        "tier": "experiment",
        "vision": False,
        "context_window": 128_000,
        "strengths": [
            "Ultra-low cost (~$0.001/call)",
            "Strong reasoning for price point",
            "Good at prompt analysis and rewriting",
        ],
        "use_cases": [
            "Experiment hypothesis generation",
            "Prompt optimization proposals",
            "Cheap bulk evaluation",
        ],
    },
}


# =============================================================================
# MODEL ALIASES + LOOKUP HELPERS
# =============================================================================

# Short-alias to full model-id mapping, matching the ``--model`` values accepted
# by the Claude CLI and the per-session model cascade in
# ``agent/session_executor.py::_resolve_session_model``. Consumed by
# ``get_model_context_window`` so callers can pass either form.
_MODEL_ALIASES: dict[str, str] = {
    "haiku": HAIKU,
    "sonnet": SONNET,
    "opus": OPUS,
}


def get_model_context_window(model_name: str | None) -> int | None:
    """Return the context window (in tokens) for a registered model.

    Accepts either a short alias (``"opus"``/``"sonnet"``/``"haiku"``) or a
    full Anthropic model id (e.g. ``"claude-opus-4-5-20251101"``). Returns
    ``None`` when the model is not registered in ``MODEL_INFO`` — callers
    (e.g. ``agent/sdk_client.py::_log_context_usage_if_risky``) treat ``None``
    as "unknown model" and skip their percentage calculation.

    Added for issue #1099 Mode 2 so the SDK/harness observer is decoupled from
    the nested-dict layout of ``MODEL_INFO``.
    """
    if not model_name:
        return None
    key = _MODEL_ALIASES.get(model_name, model_name)
    entry = MODEL_INFO.get(key)
    if entry is None:
        return None
    value = entry.get("context_window")
    if isinstance(value, int) and value > 0:
        return value
    return None
