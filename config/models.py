"""
Centralized AI Model Configuration

This module defines all AI models used throughout the codebase.
Import model constants from here rather than hardcoding model strings.

When model versions change, update them in ONE place here.
"""

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
SONNET = "claude-sonnet-4-5-20250514"

# Sonnet 4 (previous gen) - Use SONNET instead for new code
# Kept for reference during migration
SONNET_4 = "claude-sonnet-4-20250514"

# Opus 4.5 - Best reasoning, slowest and most expensive
# Use cases: Complex multi-step reasoning, nuanced analysis, creative tasks
# Strengths: Highest quality output, best at handling ambiguity
OPUS = "claude-opus-4-5-20251101"


# =============================================================================
# OPENROUTER MODELS
# Format: provider/model as used with OpenRouter API
# =============================================================================

# Vision-capable models for image analysis
# Note: Use these when calling OpenRouter API for multimodal tasks
OPENROUTER_HAIKU = "anthropic/claude-haiku-4-5-20251001"
OPENROUTER_SONNET = "anthropic/claude-sonnet-4-5-20250514"
OPENROUTER_OPUS = "anthropic/claude-opus-4-5-20251101"


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
}
