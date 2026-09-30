"""Real AI-judge integration test for the LLM refusal-detector complement.

Issue #1829: the closed-vocabulary ``_REFUSAL_PATTERNS`` check in
``agent/memory_extraction.py`` is a finite substring list — a novel Haiku
refusal phrasing that isn't a listed substring escapes and gets persisted as
noise. The optional ``_looks_like_refusal_llm`` complement generalizes beyond
the enumerated phrasings via one extra Haiku yes/no call, gated behind
``MEMORY_REFUSAL_LLM_ENABLED`` (default OFF).

This test makes REAL Anthropic API calls — no mocking of ``_llm_call``,
``AsyncAnthropic``, or the judge call. Two live-Haiku roles are exercised:

  1. The detector itself (``_looks_like_refusal_llm``), driven directly
     against a fixture set of novel refusal phrasings (deliberately NOT
     substrings of any ``_REFUSAL_PATTERNS`` entry) and genuine observation
     texts.
  2. An independent AI judge (a separate ``_llm_call`` invocation) that
     grades whether each detector verdict was correct, per this repo's
     testing philosophy of using AI judges rather than keyword matching
     (see root ``CLAUDE.md``: "Intelligence validation - Use AI judges, not
     keyword matching").

Skipped entirely when ``ANTHROPIC_API_KEY`` is not configured.
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_api_key():
    from utils.api_keys import get_anthropic_api_key

    if not get_anthropic_api_key():
        pytest.skip("ANTHROPIC_API_KEY not configured — skipping live refusal-detector test")


# -----------------------------------------------------------------------------
# Fixture set.
#
# Novel refusal phrasings: each conveys "there's nothing here to extract" /
# meta-commentary about absence of content, but is deliberately phrased
# differently from every entry in `_REFUSAL_PATTERNS` (agent/memory_extraction.py)
# so the closed-vocab check would NOT catch it — only the LLM complement can.
# -----------------------------------------------------------------------------
NOVEL_REFUSAL_PHRASINGS: list[str] = [
    (
        "I looked through the transcript and honestly there just isn't "
        "anything worth writing down here — it's all routine tool output "
        "with nothing new to learn from."
    ),
    (
        "Nothing noteworthy happened in this exchange. The conversation was "
        "purely administrative housekeeping and doesn't warrant a memory entry."
    ),
    (
        "This turn consisted entirely of the assistant acknowledging a "
        "prior instruction; there is no decision, correction, pattern, or "
        "surprise for me to surface here."
    ),
    (
        "Skipping — the input I was given is just boilerplate scaffolding "
        "text with zero informational content to distill."
    ),
]

# Genuine observation texts: realistic session-summary-style content, in the
# voice of real extracted observations. Varied topics/categories so the
# fixture isn't a single narrow shape.
GENUINE_OBSERVATION_TEXTS: list[str] = [
    (
        "Fixed the race condition in the session-lock cleanup by adding a "
        "mutex around the file-open call; deployed and verified in production."
    ),
    (
        "Chose to route all outbound Slack messages through the existing "
        "webhook relay instead of standing up a new bot token, since the "
        "relay already handles retries and rate limiting."
    ),
    (
        "Discovered that the nightly rollup job double-counts sessions that "
        "span midnight UTC because the date filter uses created_at instead "
        "of a session-scoped window; filed issue #2044 to fix the boundary."
    ),
    (
        "User corrected an earlier assumption: the /update script must run "
        "from the repo root, not from inside a worktree, because it resolves "
        "sibling paths relative to cwd."
    ),
]


async def _judge_verdict(text: str, detector_said_refusal: bool, intended_label: str) -> str:
    """Ask an independent Haiku judge to grade the detector's verdict.

    Returns the judge's raw reply (expected to start with CORRECT or
    INCORRECT). This is a SEPARATE ``_llm_call`` invocation from the
    detector under test — the judge only sees the fixture text and the
    detector's verdict, and grades correctness on its own reasoning, not on
    a keyword match against the fixture text.
    """
    from agent.memory_extraction import _llm_call
    from config.models import MODEL_FAST

    detector_label = "REFUSAL" if detector_said_refusal else "CONTENT"
    prompt = (
        "A refusal-detector was asked whether a piece of text is a REFUSAL "
        "(meta-commentary about the absence of content — e.g. saying there's "
        "nothing worth extracting) or CONTENT (a genuine observation about a "
        "real event, decision, correction, or pattern).\n\n"
        f"The text was:\n---\n{text}\n---\n\n"
        f"The detector's verdict was: {detector_label}\n"
        f"The text was designed to be: {intended_label}\n\n"
        "Was the detector's verdict CORRECT? Reply with exactly one word: "
        "CORRECT or INCORRECT."
    )
    return await _llm_call(
        model=MODEL_FAST,
        max_tokens=5,
        messages=[{"role": "user", "content": prompt}],
    )


class TestRefusalLLMComplementLiveJudge:
    """Drives the real flag-ON detector against live Haiku, then grades each
    drop/keep decision with an independent live-Haiku judge.

    No mocking anywhere in this class — every assertion is backed by a real
    API round trip. Findings from a genuine flaky/incorrect verdict on a
    borderline case are reported, not papered over with retries or softened
    assertions.
    """

    @pytest.mark.asyncio
    async def test_novel_refusal_phrasings_are_caught_and_judged_correct(self, monkeypatch):
        """The complement should flag each novel refusal phrasing as REFUSAL,
        and an independent judge should agree the verdict was correct."""
        from agent.memory_extraction import _REFUSAL_PATTERNS, _looks_like_refusal_llm

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        # Guard against fixture rot: these phrasings must NOT already be
        # caught by the closed-vocab check, or this test would stop
        # exercising the LLM complement at all.
        for phrasing in NOVEL_REFUSAL_PHRASINGS:
            lowered = phrasing.lower()
            for pattern in _REFUSAL_PATTERNS:
                assert pattern not in lowered, (
                    f"Fixture phrasing accidentally overlaps _REFUSAL_PATTERNS "
                    f"entry {pattern!r} — rewrite the fixture so it only tests "
                    f"the LLM complement, not the closed-vocab check."
                )

        results: list[tuple[str, bool, str]] = []
        for phrasing in NOVEL_REFUSAL_PHRASINGS:
            verdict = await _looks_like_refusal_llm(phrasing)
            judge_reply = await _judge_verdict(phrasing, verdict, "REFUSAL")
            results.append((phrasing, verdict, judge_reply))

        failures = [
            (text, verdict, reply)
            for text, verdict, reply in results
            if not reply.strip().upper().startswith("CORRECT")
        ]
        assert not failures, (
            "AI judge reported INCORRECT (or unparseable) for the following "
            "novel-refusal fixture items — either the detector missed a "
            "refusal or the judge disagreed with a correct verdict:\n"
            + "\n".join(
                f"  text={text!r} detector_verdict={'REFUSAL' if v else 'CONTENT'} "
                f"judge_reply={r!r}"
                for text, v, r in failures
            )
        )

    @pytest.mark.asyncio
    async def test_genuine_observations_are_kept_and_judged_correct(self, monkeypatch):
        """The complement should NOT flag genuine observations as REFUSAL
        (no false drops), and an independent judge should agree."""
        from agent.memory_extraction import _looks_like_refusal_llm

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        results: list[tuple[str, bool, str]] = []
        for text in GENUINE_OBSERVATION_TEXTS:
            verdict = await _looks_like_refusal_llm(text)
            judge_reply = await _judge_verdict(text, verdict, "CONTENT")
            results.append((text, verdict, judge_reply))

        failures = [
            (text, verdict, reply)
            for text, verdict, reply in results
            if not reply.strip().upper().startswith("CORRECT")
        ]
        assert not failures, (
            "AI judge reported INCORRECT (or unparseable) for the following "
            "genuine-observation fixture items — a false drop would silently "
            "discard legitimate Memory content:\n"
            + "\n".join(
                f"  text={text!r} detector_verdict={'REFUSAL' if v else 'CONTENT'} "
                f"judge_reply={r!r}"
                for text, v, r in failures
            )
        )

    @pytest.mark.asyncio
    async def test_end_to_end_extraction_keeps_genuine_observations(self, monkeypatch):
        """Drives the genuine-observation fixtures through the full
        extract_observations_async save path with the flag ON, confirming
        the complement doesn't accidentally suppress real extraction output
        (in addition to the direct-detector check above)."""
        from agent.memory_extraction import extract_observations_async
        from models.memory import Memory

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        saved_ids: list[str] = []
        try:
            for i, text in enumerate(GENUINE_OBSERVATION_TEXTS):
                # Build a session-response-shaped payload long enough to
                # clear the pre-LLM guards (50-char minimum, non-whitespace
                # ratio) and pass it through real end-to-end extraction.
                response_text = (
                    f"Session summary: {text} This was verified working "
                    "end to end before the session concluded."
                )
                result = await extract_observations_async(
                    session_id=f"test-refusal-llm-e2e-{i}",
                    response_text=response_text,
                    project_key="test-refusal-llm",
                )
                saved_ids.extend(item["memory_id"] for item in result if item.get("memory_id"))
        finally:
            for memory_id in saved_ids:
                try:
                    memory = Memory.query.filter(memory_id=memory_id).first()
                    if memory:
                        memory.delete()
                except Exception:
                    pass

        assert saved_ids, (
            "Expected at least one genuine observation to survive the "
            "flag-ON end-to-end extraction path — the LLM refusal complement "
            "may be suppressing legitimate content (false drop)."
        )
