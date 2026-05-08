"""
reflections/pm_briefings/builder.py — Brief construction (Pass A + Pass B).

Implements the deterministic phases of /do-debrief: Categorize -> Pass A draft
(LLM via direct anthropic.Anthropic() call) -> Pass B word-count cut -> hard
"no numbers" guard. The Pass A prompt encodes ALL semantic constraints:
- forbids issue/PR numbers
- forbids forward-looking commitments ("we will", "I'll push", etc.)
- forbids bare 3+ digit integers ("363", "1195" stripped of their prefix)
- requires the first sentence to be a decision or heads-up, not setup

Post-LLM regex guard catches anything that slipped through:
- Layer 2: prefixed forms via `\\b(?:issue|pr|#)[\\s\\-_]*\\d{2,}\\b`
- Layer 3: bare 3+ digit integers via `(?<!\\$)(?<![\\d.])\\b\\d{3,}\\b`
  with lookahead excluding unit suffixes (users, ms, seconds, %, etc.)

Either layer matching raises BriefingNumbersDetectedError.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("reflections.pm_briefings.builder")


class BriefingNumbersDetectedError(RuntimeError):
    """Raised when the post-LLM regex guard finds a forbidden number form.

    Declared at module top so the regex guard never raises NameError before
    the import block resolves.
    """


class BriefingWordCountError(RuntimeError):
    """Raised when Pass A transcript is under the minimum word count.

    Declared at module top (per plan C1-R5) so the post-check guard never
    raises NameError. Signals a too-short Pass A regression to the dashboard
    last_status field.
    """


# Layer 2: prefixed forms ("issue 1197", "PR 1197", "pr-363", "issue_1197").
# Uses [\s\-_]* (whitespace, hyphen, underscore) per plan B1-R5 canonical spec
# so that hyphen/underscore-separated forms like `pr-363` and `issue_1197` are
# caught. NOTE: does NOT catch "#1197" because `#` is not a word char so the
# `\b` before it never anchors -- those are handled by Layer 3.
_NUMBERS_PREFIXED_RE = re.compile(r"\b(?:issue|pr|#)[\s\-_]*\d{2,}\b", re.IGNORECASE)

# Layer 3: bare 3+ digit integers ("363", "1197", "1195"). Uses lookbehind to
# exclude dollar amounts and decimal fractions, and lookahead to exclude common
# measurement units. Per plan B1-R5 canonical spec: catches bare 3+ digit
# issue/PR numbers like "363", "1195", "1197" that slip through Layer 2.
_NUMBERS_BARE_RE = re.compile(
    r"(?<!\$)(?<![\d.])\b\d{3,}\b(?!\s*(?:users?|requests?|lines?|ms|seconds?|minutes?|hours?|days?|%|percent))"
)

# Word-count target (Pass B).
_WORD_COUNT_MIN = 55
_WORD_COUNT_MAX = 80


def _check_numbers(transcript: str) -> None:
    """Raise BriefingNumbersDetectedError if either regex layer matches."""
    m2 = _NUMBERS_PREFIXED_RE.search(transcript)
    if m2:
        raise BriefingNumbersDetectedError(
            f"Numbers (prefixed) detected in transcript: {m2.group()!r}"
        )
    m3 = _NUMBERS_BARE_RE.search(transcript)
    if m3:
        raise BriefingNumbersDetectedError(
            f"Numbers (bare 3+ digit) detected in transcript: {m3.group()!r}"
        )


# --- Pass A prompt -----------------------------------------------------------


_PASS_A_SYSTEM = (
    "You are an executive briefing writer. You produce a 30-second spoken brief "
    "(target 55-80 words, ~30 seconds aloud) for a busy product manager.\n\n"
    "Hard rules (non-negotiable):\n"
    "- The first sentence must be a decision or heads-up — the most important "
    "shipped or queued item — not setup or context. Lead with the ask.\n"
    "- Never recite issue numbers, PR numbers, or hash-prefixed identifiers. Refer "
    "to work by substance, not by number ('the continuation-PM crash', not "
    "'issue 1195').\n"
    "- Do not recite any standalone integer of 3 or more digits — those are "
    "likely issue or PR numbers stripped of their prefix. Use words like 'a few' "
    "or 'several' instead.\n"
    "- Do not invent decisions, commitments, or future actions. Only narrate work "
    "that has already shipped or is queued. Never use phrases like 'we will', "
    "'I'll push', 'pushing to', or 'unless you want it sooner' — those are "
    "default-and-confirm phrases that imply a human review gate. This brief is "
    "auto-confirmed; you do not have a safety check.\n"
    "- Use contractions ('I'm', 'don't', 'we're') — written prose reads stiff "
    "aloud.\n"
    "- Stay under 80 words. Aim for 55-80.\n"
    "- Close with 'I've got the rest.'\n\n"
    "Shape: top item (10s) + second decision or heads-up (8s) + batched FYIs "
    "(8s, opening with 'A few quick FYIs:') + close. Drop slots with no content."
)


def _format_signals_for_prompt(raw_signals: dict[str, list[dict]]) -> str:
    """Render the collected signals as a plain-text bulletted block.

    Numbers are deliberately included in the prompt input so the LLM can SEE
    them (and refer to work by substance), but the prompt forbids reciting
    them.
    """
    lines: list[str] = []
    if not raw_signals:
        return "(no signals collected)"
    for cat, items in raw_signals.items():
        if not items:
            continue
        lines.append(f"## {cat}")
        for item in items:
            subject = item.get("subject") or item.get("title") or "(unknown)"
            num = item.get("pr_number") or item.get("number")
            if num:
                lines.append(f"- {subject} (#{num})")
            else:
                lines.append(f"- {subject}")
    if not lines:
        return "(no signals collected)"
    return "\n".join(lines)


def _draft_pass_a(raw_signals: dict[str, list[dict]]) -> str:
    """Run Pass A: ask Claude Haiku to draft the brief from raw signals.

    Mirrors the pattern in reflections/utils.py:run_llm_reflection. Returns
    the transcript string. Raises RuntimeError on hard failure (no anthropic
    package, no API key, or LLM error) so the caller surfaces it.
    """
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(f"anthropic package not installed: {e}") from e

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    from config.models import HAIKU

    user_content = (
        "Raw signals from the last 24 hours:\n\n"
        f"{_format_signals_for_prompt(raw_signals)}\n\n"
        "Write the brief now. Output only the spoken transcript, no preamble, "
        "no markdown, no quotation marks."
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=HAIKU,
        max_tokens=1024,
        system=_PASS_A_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    # Concatenate any text content blocks
    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


# --- Pass B (deterministic word-count cut) -----------------------------------


def _word_count(text: str) -> int:
    return len(text.split())


def _pass_b_cut(transcript: str) -> str:
    """Strict deterministic word-count enforcement.

    If transcript is over the max, drop trailing sentences until under cap.
    If under the min, leave alone — the caller will surface a Pass-B failure
    via post-check; this function does not invent words.
    """
    if not transcript:
        return ""
    wc = _word_count(transcript)
    if wc <= _WORD_COUNT_MAX:
        return transcript

    # Split into sentences (greedy split on '.', '!', '?').
    sentences = re.split(r"(?<=[.!?])\s+", transcript.strip())
    while sentences and _word_count(" ".join(sentences)) > _WORD_COUNT_MAX:
        # Always preserve the close ("I've got the rest.") if present as the
        # final sentence — drop the second-to-last instead.
        if len(sentences) >= 2 and "i've got the rest" in sentences[-1].lower():
            del sentences[-2]
        else:
            del sentences[-1]
    return " ".join(sentences).strip()


# --- Written follow-up -------------------------------------------------------


def _build_written_followup(raw_signals: dict[str, list[dict]], project: dict | None = None) -> str:
    """Build the markdown follow-up that mirrors the audio's structure.

    Sections: ## Decisions / ## Heads-up / ## FYIs. v1 is a flat dump of
    everything collected -- the audio is the curated cut, the followup is
    the full ledger with numbers + links.
    """
    if not raw_signals:
        return ""
    lines: list[str] = []
    has_any = False

    # Map category -> section heading
    for cat, heading in (
        ("merges", "Shipped"),
        ("open-bugs", "Open bugs"),
        ("upvote-queue", "Upvote queue"),
    ):
        items = raw_signals.get(cat) or []
        if not items:
            continue
        has_any = True
        lines.append(f"## {heading}")
        for item in items:
            subject = item.get("subject") or item.get("title") or "(unknown)"
            num = item.get("pr_number") or item.get("number")
            url = item.get("url")
            if url and num:
                lines.append(f"- [#{num}]({url}) — {subject}")
            elif num:
                lines.append(f"- #{num} — {subject}")
            else:
                lines.append(f"- {subject}")
        lines.append("")

    if not has_any:
        return ""
    return "\n".join(lines).rstrip() + "\n"


# --- Public API --------------------------------------------------------------


def build(
    raw_signals: dict[str, list[Any]],
    fallback_message: str,
    skip_when_empty: bool,
    project: dict | None = None,
) -> tuple[str, str]:
    """Compose the audio transcript and the written follow-up.

    Args:
        raw_signals: Output of collector.collect().
        fallback_message: String to use as the audio when no signals arrived
            and skip_when_empty is False.
        skip_when_empty: If True, returns ("", "") when no signals exist
            (the caller should treat that as a noop). If False, returns
            (fallback_message, "") instead.
        project: Optional project dict (currently unused; reserved for
            future per-project followup formatting).

    Returns:
        (audio_transcript, written_followup_markdown).

    Raises:
        BriefingNumbersDetectedError: If the post-LLM regex guard finds a
            forbidden number form.
        RuntimeError: If Pass A (LLM call) fails.
    """
    # Empty-signal handling (no LLM call needed).
    has_signals = any(items for items in (raw_signals or {}).values())
    if not has_signals:
        if skip_when_empty:
            return ("", "")
        # Use the fallback message as the audio. Still run the numbers guard
        # to catch a misconfigured fallback.
        _check_numbers(fallback_message)
        return (fallback_message, "")

    # Pass A: LLM draft.
    transcript = _draft_pass_a(raw_signals)
    if not transcript:
        raise RuntimeError("Pass A returned an empty transcript")

    # Pass B: deterministic word-count cut.
    transcript = _pass_b_cut(transcript)

    # Layer 2/3 hard guard -- raises BriefingNumbersDetectedError on match.
    _check_numbers(transcript)

    # Word-count post-check.
    wc = _word_count(transcript)
    if wc < _WORD_COUNT_MIN:
        raise BriefingWordCountError(
            f"Pass A transcript is under minimum word count: {wc} < {_WORD_COUNT_MIN}"
        )
    if wc > _WORD_COUNT_MAX:
        # Pass B should have cut this; if it still over, raise.
        raise RuntimeError(f"Pass B failed to cut transcript to <= {_WORD_COUNT_MAX} words ({wc})")

    followup = _build_written_followup(raw_signals, project=project)
    return (transcript, followup)
