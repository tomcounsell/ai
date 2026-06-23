"""
Cross-vendor review judge for the SDLC /do-pr-review pipeline (issue #1626).

Fetches a PR diff, sends it to a non-Claude model (default: gpt-4o) using the
OpenAI Chat Completions API, and emits a single JSON envelope to stdout.

Output shapes:
  {"status": "ok",      "judge": {...}}
  {"status": "skipped", "reason": "...", "meta": {...}}

Judge dict fields (status=ok only):
  judge_id, verdict, blockers, tech_debt, confidence, reasoning_summary, meta
"""

import argparse
import json
import logging
import subprocess
import sys

import tiktoken

from config.settings import settings

logger = logging.getLogger(__name__)

# Named constant — disjoint from "code-quality" and "risk" judge IDs.
CROSS_VENDOR_JUDGE_ID = "cross-vendor"

# ---------------------------------------------------------------------------
# Rubric / system prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a senior software engineer performing a code review. Analyse the given
diff carefully and respond with ONLY a valid JSON object — no prose, no markdown
fences, just the raw JSON.

Review rubric:
1. Correctness — logic errors, off-by-ones, data loss
2. Regression risk — breaks existing behaviour
3. Security — injection, auth bypass, exposed secrets
4. Error handling — uncaught exceptions, silent failures
5. Test coverage — missing tests for new behaviour

Required JSON schema (respond with exactly this structure):
{
  "verdict": "APPROVED or CHANGES REQUESTED",
  "blockers": <integer count of blocking issues>,
  "tech_debt": <integer count of tech-debt items>,
  "confidence": <float 0.0-1.0>,
  "reasoning_summary": "<brief rationale>"
}

Rules:
- "verdict" must be exactly "APPROVED" or "CHANGES REQUESTED".
- "blockers" and "tech_debt" must be non-negative integers (not booleans).
- "confidence" must be a float between 0.0 and 1.0.
- "reasoning_summary" must be a non-empty string.
- If the diff is empty or trivially small, set confidence low (≤0.3).
"""

_USER_TEMPLATE = "Review the following diff and return the JSON object:\n\n{diff}"

# ---------------------------------------------------------------------------
# Token counting helpers
# ---------------------------------------------------------------------------


def _count_tokens(text: str, model: str) -> int:
    """Estimate token count for text using tiktoken (best-effort)."""
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def _truncate_diff(diff: str, model: str, max_tokens: int) -> tuple[str, bool]:
    """
    Truncate diff to max_tokens if necessary.

    Returns (truncated_diff, was_truncated).
    """
    if _count_tokens(diff, model) <= max_tokens:
        return diff, False
    # Binary search or simple character-based approximation.
    # ~4 chars/token is a reasonable heuristic.
    approx_chars = max_tokens * 4
    truncated = diff[:approx_chars]
    marker = "\n\n[DIFF TRUNCATED — token limit reached; confidence reduced]\n"
    return truncated + marker, True


# ---------------------------------------------------------------------------
# Core judge logic
# ---------------------------------------------------------------------------


def _emit_skipped(reason: str, meta: dict) -> dict:
    envelope = {"status": "skipped", "reason": reason, "meta": meta}
    print(json.dumps(envelope))
    logger.warning("cross_vendor_judge skipped: %s", reason)
    return envelope


def _coerce_judge_fields(raw: dict, model: str) -> tuple[dict | None, str | None]:
    """
    Coerce and validate the raw model response dict.

    Returns (coerced_dict, error_reason).
    error_reason is None on success.
    """
    # verdict
    verdict = raw.get("verdict", "")
    if not isinstance(verdict, str):
        verdict = str(verdict)
    verdict = verdict.strip().upper()
    if verdict not in ("APPROVED", "CHANGES REQUESTED"):
        # Normalise common variants
        if "APPROVED" in verdict:
            verdict = "APPROVED"
        else:
            verdict = "CHANGES REQUESTED"

    # blockers — must be int, reject bools
    blockers_raw = raw.get("blockers", 0)
    if isinstance(blockers_raw, bool):
        return None, f"blockers field is bool ({blockers_raw!r}), expected int"
    try:
        blockers = int(blockers_raw)
    except (TypeError, ValueError):
        return None, f"blockers field is not numeric: {blockers_raw!r}"

    # tech_debt — must be int, reject bools
    tech_debt_raw = raw.get("tech_debt", 0)
    if isinstance(tech_debt_raw, bool):
        tech_debt = 0  # degrade gracefully for tech_debt
    else:
        try:
            tech_debt = int(tech_debt_raw)
        except (TypeError, ValueError):
            tech_debt = 0

    # confidence — float clamped 0.0-1.0
    try:
        confidence = float(raw.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    # reasoning_summary
    reasoning_summary = raw.get("reasoning_summary", "")
    if not isinstance(reasoning_summary, str):
        reasoning_summary = str(reasoning_summary)
    reasoning_summary = reasoning_summary.strip() or "No summary provided."

    coerced = {
        "judge_id": CROSS_VENDOR_JUDGE_ID,
        "verdict": verdict,
        "blockers": blockers,
        "tech_debt": tech_debt,
        "confidence": confidence,
        "reasoning_summary": reasoning_summary,
        "meta": {"model": model},
    }
    return coerced, None


def run_judge(diff: str, pr_number: int | None = None) -> dict:
    """
    Run the cross-vendor judge against a diff string.

    Returns the full envelope dict (status=ok or status=skipped).
    Prints the envelope to stdout as a side effect.
    """
    model = settings.sdlc_review_cross_vendor_model
    max_tokens = settings.sdlc_review_cross_vendor_max_diff_tokens

    base_meta: dict = {"model": model, "pr_number": pr_number}

    # Empty diff — return low-confidence APPROVED rather than crash.
    if not diff or not diff.strip():
        judge = {
            "judge_id": CROSS_VENDOR_JUDGE_ID,
            "verdict": "APPROVED",
            "blockers": 0,
            "tech_debt": 0,
            "confidence": 0.3,
            "reasoning_summary": "Empty diff — nothing to review.",
            "meta": {**base_meta, "prompt_tokens": 0, "completion_tokens": 0},
        }
        envelope = {"status": "ok", "judge": judge}
        print(json.dumps(envelope))
        logger.info(
            "cross_vendor_judge ran (empty diff): model=%s prompt_tokens=0 completion_tokens=0",
            model,
        )
        return envelope

    # Truncate if needed.
    diff_for_prompt, was_truncated = _truncate_diff(diff, model, max_tokens)

    user_message = _USER_TEMPLATE.format(diff=diff_for_prompt)

    # Build OpenAI request.
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.api.openai_api_key)

        response = client.chat.completions.create(
            model=model,
            temperature=0,
            seed=42,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )

        raw_content = response.choices[0].message.content or "{}"
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0

    except Exception as exc:
        # Catches openai.OpenAIError (BadRequest, NotFound, RateLimit, Timeout,
        # Auth, Connection) and any other unexpected errors.
        import openai

        if isinstance(exc, openai.OpenAIError):
            reason = f"OpenAI API error: {type(exc).__name__}: {exc}"
        else:
            reason = f"Unexpected error calling OpenAI: {type(exc).__name__}: {exc}"

        return _emit_skipped(reason, base_meta)

    # Parse JSON from model response.
    try:
        raw = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        return _emit_skipped(f"Model returned non-JSON content: {exc}", base_meta)

    if not isinstance(raw, dict):
        return _emit_skipped(f"Model returned unexpected type: {type(raw).__name__}", base_meta)

    # Coerce and validate fields.
    coerced, error_reason = _coerce_judge_fields(raw, model)
    if error_reason:
        return _emit_skipped(error_reason, {**base_meta, "raw": raw_content[:500]})

    # Reduce confidence if diff was truncated.
    if was_truncated:
        coerced["confidence"] = max(0.0, coerced["confidence"] - 0.2)
        coerced["reasoning_summary"] = "[Diff truncated] " + coerced["reasoning_summary"]

    coerced["meta"]["prompt_tokens"] = prompt_tokens
    coerced["meta"]["completion_tokens"] = completion_tokens

    envelope = {"status": "ok", "judge": coerced}
    print(json.dumps(envelope))
    logger.info(
        "cross_vendor_judge ran: model=%s prompt_tokens=%d completion_tokens=%d",
        model,
        prompt_tokens,
        completion_tokens,
    )
    return envelope


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fetch_pr_diff(pr_number: int) -> str | None:
    """Fetch PR diff via gh CLI. Returns diff string or None on failure."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "gh pr diff %d failed (exit %d): %s",
            pr_number,
            result.returncode,
            result.stderr.strip(),
        )
        return None
    return result.stdout


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    parser = argparse.ArgumentParser(
        description="Cross-vendor (OpenAI) code review judge for SDLC pipeline."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--pr", type=int, metavar="N", help="PR number to review")
    source_group.add_argument(
        "--diff-file",
        metavar="PATH",
        help="Path to a diff file (for testing without a live PR)",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Always emits JSON to stdout (no-op flag for pipeline compat).",
    )
    args = parser.parse_args()

    if args.pr is None and args.diff_file is None:
        parser.error("Provide --pr N or --diff-file PATH")

    if args.diff_file:
        try:
            with open(args.diff_file) as fh:
                diff = fh.read()
        except OSError as exc:
            envelope = {
                "status": "skipped",
                "reason": f"Cannot read diff file: {exc}",
                "meta": {"diff_file": args.diff_file},
            }
            print(json.dumps(envelope))
            logger.warning("cross_vendor_judge skipped: %s", envelope["reason"])
            sys.exit(0)
        pr_number = None
    else:
        diff = _fetch_pr_diff(args.pr)
        if diff is None:
            envelope = {
                "status": "skipped",
                "reason": f"gh pr diff {args.pr} failed",
                "meta": {"pr_number": args.pr},
            }
            print(json.dumps(envelope))
            logger.warning("cross_vendor_judge skipped: %s", envelope["reason"])
            sys.exit(0)
        pr_number = args.pr

    run_judge(diff, pr_number=pr_number)


if __name__ == "__main__":
    main()
