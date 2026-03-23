#!/usr/bin/env python3
"""
Issue Dedup Engine - LLM-based similarity scoring for GitHub issues.

Compares a new issue's title and body against existing open issues
using Claude Haiku for semantic similarity assessment.

Classifications:
- duplicate (score >= 0.8): Issue is likely a duplicate
- related (0.5 <= score < 0.8): Issue is related but distinct
- unique (score < 0.5): Issue is new/unique

See docs/features/issue-poller.md for full documentation.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path for standalone execution
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Similarity thresholds
DUPLICATE_THRESHOLD = 0.8
RELATED_THRESHOLD = 0.5

# Claude model for dedup scoring
DEDUP_MODEL = "claude-haiku-4-20250514"


def _get_anthropic_client() -> anthropic.Anthropic:
    """Get an Anthropic client, raising if unavailable."""
    if anthropic is None:
        raise RuntimeError("anthropic package not installed")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try loading from .env
        try:
            from dotenv import dotenv_values

            env_path = Path(_project_root) / ".env"
            if env_path.exists():
                env = dotenv_values(env_path)
                api_key = env.get("ANTHROPIC_API_KEY")
            if not api_key:
                valor_env = Path.home() / "Desktop" / "Valor" / ".env"
                if valor_env.exists():
                    env = dotenv_values(valor_env)
                    api_key = env.get("ANTHROPIC_API_KEY")
        except ImportError:
            pass

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not found")

    return anthropic.Anthropic(api_key=api_key)


def score_similarity(
    title_a: str,
    body_a: str,
    title_b: str,
    body_b: str,
) -> float:
    """Score the semantic similarity between two issues using Claude Haiku.

    Returns a float between 0.0 (completely different) and 1.0 (identical).
    """
    client = _get_anthropic_client()

    prompt = f"""Compare these two GitHub issues and rate their semantic similarity.

Issue A:
Title: {title_a}
Body: {body_a[:1000]}

Issue B:
Title: {title_b}
Body: {body_b[:1000]}

Rate the similarity from 0.0 to 1.0 where:
- 0.0 = completely unrelated topics
- 0.5 = related topic but different specific request
- 0.8 = very similar, likely covering the same work
- 1.0 = identical request

Respond with ONLY a JSON object: {{"score": <float>, "reason": "<brief explanation>"}}"""

    try:
        response = client.messages.create(
            model=DEDUP_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Parse JSON from response
        result = json.loads(text)
        score = float(result.get("score", 0.0))
        return max(0.0, min(1.0, score))

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse dedup score: {e}")
        return 0.0
    except Exception as e:
        logger.warning(f"Dedup API call failed: {e}")
        raise


def classify_similarity(score: float) -> str:
    """Classify a similarity score into duplicate/related/unique."""
    if score >= DUPLICATE_THRESHOLD:
        return "duplicate"
    elif score >= RELATED_THRESHOLD:
        return "related"
    return "unique"


def compare_issues(
    title: str,
    body: str,
    existing_issues: list[dict],
) -> dict | None:
    """Compare a new issue against existing open issues.

    Returns the best match result dict or None if unique.

    Result dict keys:
        - classification: 'duplicate', 'related', or 'unique'
        - match_number: issue number of the best match
        - match_title: title of the best match
        - score: similarity score
    """
    if not existing_issues:
        return None

    best_match = None
    best_score = 0.0

    for existing in existing_issues:
        ex_title = existing.get("title", "")
        ex_body = existing.get("body", "") or ""
        ex_number = existing.get("number", 0)

        try:
            score = score_similarity(title, body, ex_title, ex_body)
        except Exception:
            # Skip this comparison on API failure
            continue

        if score > best_score:
            best_score = score
            best_match = {
                "match_number": ex_number,
                "match_title": ex_title,
                "score": score,
            }

    if best_match is None:
        return None

    classification = classify_similarity(best_score)
    if classification == "unique":
        return None

    best_match["classification"] = classification
    return best_match
