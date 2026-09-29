"""AI-judge eval (#3588): what a handoff session says to a human is plain words.

A real model receives a rendered handoff brief that genuinely needs a human
decision and writes the one message it would send. An independent judge grades
it: plain words, one named decision, no internal IDs, no shell commands. A
synthetic bad message proves the judge discriminates. Skipped without an
Anthropic key.
"""

import pytest

from reflections.agent_handoff import Finding, render_brief

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_api_key():
    from utils.api_keys import get_anthropic_api_key

    if not get_anthropic_api_key():
        pytest.skip("ANTHROPIC_API_KEY not configured")


BAD_MESSAGE = (
    "Job 7f3a9c2e-51d4-4b8e-9a16-2c0d8e4b1f77 expectation e-19 is orphaned. Run "
    "`python -m tools.job_tool expectation-remove --job 7f3a9c2e --id e-19` to discharge it."
)


def _finding() -> Finding:
    return Finding(
        source="expectation_reconciler",
        project={"slug": "proj", "working_directory": "/tmp"},
        room_id="proj|telegram:-100123",
        facts=[
            "The lane that owed 'deliver the billing export' is gone and the recovery budget "
            "(3 attempt(s)) is spent.",
            "The only visible work is a pull request that was closed without merging.",
        ],
        evidence={
            "job_id": "7f3a9c2e-51d4-4b8e-9a16-2c0d8e4b1f77",
            "expectation_id": "e-19",
            "shipped_kind": "closed_unmerged",
            "pr_number": 311,
        },
        suggested_action="Decide whether the work still matters; ask the human one plain question.",
        dedup_key="k",
    )


async def _agent_message() -> str:
    from agent.memory_extraction import _llm_call
    from config.models import MODEL_FAST

    prompt = (
        render_brief(_finding())
        + "\n\nAssume nothing you can check tells you the work still matters, so a human decision "
        "is truly needed. Write the single chat message you would send to the human. Output only "
        "the message."
    )
    return await _llm_call(
        model=MODEL_FAST, max_tokens=400, messages=[{"role": "user", "content": prompt}]
    )


async def _judge(message: str) -> str:
    from agent.memory_extraction import _llm_call
    from config.models import MODEL_FAST

    prompt = (
        "Grade a chat message an assistant sends to a non-technical project owner.\n\n"
        "PASS only if ALL hold: (1) it is written in plain words a non-engineer follows; "
        "(2) it names one concrete decision the owner is being asked to make; (3) it contains "
        "no UUIDs or internal identifiers (job ids, expectation ids, session ids) and no shell "
        "commands or CLI invocations.\n"
        "FAIL if any of those is violated.\n\n"
        f"Message:\n---\n{message}\n---\n\nReply with exactly one word: PASS or FAIL."
    )
    return (
        (
            await _llm_call(
                model=MODEL_FAST, max_tokens=5, messages=[{"role": "user", "content": prompt}]
            )
        )
        .strip()
        .upper()
    )


@pytest.mark.asyncio
async def test_judge_discriminates_bad_from_plain_message():
    assert (await _judge(BAD_MESSAGE)).startswith("FAIL")


@pytest.mark.asyncio
async def test_handoff_message_is_plain_words_with_a_named_decision():
    message = await _agent_message()
    assert message and message.strip(), "empty model reply is a failure, never a pass"
    verdict = await _judge(message)
    assert verdict.startswith("PASS"), f"judge said {verdict!r} for:\n{message}"
