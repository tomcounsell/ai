"""Granite4.1:3b smoke test (gate task for the granite-agent-loop PoC).

Runs 20 scripted operator decision scenarios against granite4.1:3b via ollama
and verifies that the model produces well-formed tool calls reliably enough to
act as the session operator.

Kill criteria: parse error rate > 20% (i.e. < 80% of scenarios produce a
non-empty `tool_calls` whose function name is in the expected set).
A stricter informal target of >= 95% is logged but does not gate exit code.

Usage:
    python scripts/granite_smoke_test.py

Exit codes:
    0 -- error rate <= 20% (gate passes)
    1 -- error rate > 20% (kill signal -- abandon granite routing)
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any

try:
    import ollama
except ImportError:
    print("ERROR: ollama Python package not installed (pip install ollama)", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Operator tool schema (subset used by the smoke test)
# ---------------------------------------------------------------------------

OPERATOR_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "extract_dev_prompt",
            "description": (
                "Extract the next instruction the Dev session should receive, "
                "based on what the PM session just produced."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dev_prompt": {
                        "type": "string",
                        "description": "The full instruction text to send to Dev.",
                    }
                },
                "required": ["dev_prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_for_pm",
            "description": (
                "Summarize the Dev session output so the PM can evaluate progress "
                "without seeing every raw tool call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "A short summary of what Dev did and produced.",
                    }
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handle_choice",
            "description": (
                "Respond to a multiple-choice prompt issued by a Claude Code session. "
                "Pick one of the numbered options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "choice": {
                        "type": "string",
                        "description": "The chosen option, e.g. '1' or '2'.",
                    }
                },
                "required": ["choice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "probe_session",
            "description": (
                "Send a probe message to a session that has gone silent, asking "
                "whether it is still working or has wrapped up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the session is being probed.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "signal_done",
            "description": (
                "Signal that the overall task is complete because the PM session "
                "explicitly indicated finished work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "result_summary": {
                        "type": "string",
                        "description": "Final summary of what was accomplished.",
                    }
                },
                "required": ["result_summary"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in OPERATOR_TOOLS}


# ---------------------------------------------------------------------------
# 20 scripted scenarios
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    user_prompt: str
    expected_tool: str


SYSTEM_PROMPT = (
    "You are the operator routing messages between two Claude Code sessions "
    "(PM = Opus and Dev = Sonnet). For every situation, choose exactly ONE "
    "tool from the available tools that best handles it. Do not reply with "
    "free-form text -- always call a tool."
)


def build_scenarios() -> list[Scenario]:
    return [
        # extract_dev_prompt (4)
        Scenario(
            "pm_gives_dev_instructions_1",
            "PM just said: 'Have Dev create a file hello.py that prints Hello World.' "
            "What do you do next?",
            "extract_dev_prompt",
        ),
        Scenario(
            "pm_gives_dev_instructions_2",
            "PM result: 'Dev, please run the test suite and report failures.' Forward this to Dev.",
            "extract_dev_prompt",
        ),
        Scenario(
            "pm_gives_dev_instructions_3",
            "PM said: 'Next, implement the granite router class.' Route to Dev.",
            "extract_dev_prompt",
        ),
        Scenario(
            "pm_gives_dev_instructions_4",
            "PM result text: 'Tell Dev to write unit tests for the parser.'",
            "extract_dev_prompt",
        ),
        # summarize_for_pm (4)
        Scenario(
            "dev_finished_summarize_1",
            "Dev emitted 50 lines of tool calls editing 3 files and finished with "
            "'created hello.py and committed it'. Summarize for PM.",
            "summarize_for_pm",
        ),
        Scenario(
            "dev_finished_summarize_2",
            "Dev ran pytest and reported 'All 42 tests passed'. Pass this back to PM.",
            "summarize_for_pm",
        ),
        Scenario(
            "dev_finished_summarize_3",
            "Dev produced a long stream of bash and edit operations, final result "
            "was 'refactored module agent/foo.py'. Summarize.",
            "summarize_for_pm",
        ),
        Scenario(
            "dev_finished_summarize_4",
            "Dev session output: 'Wrote 3 functions, added docstrings, ran format.' "
            "Summarize for the PM session.",
            "summarize_for_pm",
        ),
        # handle_choice (4)
        Scenario(
            "multi_choice_1",
            "The Dev session printed:\n"
            "Do you want to proceed?\n  1) Yes\n  2) No\n"
            "How do you respond?",
            "handle_choice",
        ),
        Scenario(
            "multi_choice_2",
            "Claude printed a multiple-choice prompt:\n"
            "Pick a strategy:\n  1) fast\n  2) safe\n  3) skip\n"
            "Respond with the first option.",
            "handle_choice",
        ),
        Scenario(
            "multi_choice_3",
            "Session asks: 'Should I overwrite the file?\n  1) yes\n  2) cancel'. "
            "Choose to overwrite.",
            "handle_choice",
        ),
        Scenario(
            "multi_choice_4",
            "A numbered prompt appeared:\n  1) commit\n  2) discard\n  3) review\nPick option 1.",
            "handle_choice",
        ),
        # probe_session (4)
        Scenario(
            "probe_silent_1",
            "The Dev session has been silent for 130 seconds with no output. What do you do?",
            "probe_session",
        ),
        Scenario(
            "probe_silent_2",
            "PM session produced no output for two minutes. Decide.",
            "probe_session",
        ),
        Scenario(
            "probe_silent_3",
            "Dev appeared to hang -- last event was 3 minutes ago. Take action.",
            "probe_session",
        ),
        Scenario(
            "probe_silent_4",
            "No stream activity from Dev for over 120s; nothing else is happening.",
            "probe_session",
        ),
        # signal_done (4)
        Scenario(
            "done_1",
            "PM said: 'The task is complete. The hello.py file is committed and "
            "tests pass.' What do you do?",
            "signal_done",
        ),
        Scenario(
            "done_2",
            "PM result: 'All done -- nothing more to do.' Wrap up.",
            "signal_done",
        ),
        Scenario(
            "done_3",
            "PM finished with: 'Final result delivered. Closing out.'",
            "signal_done",
        ),
        Scenario(
            "done_4",
            "PM confirmed completion: 'The user's request has been fully satisfied.'",
            "signal_done",
        ),
    ]


# ---------------------------------------------------------------------------
# Smoke test runner
# ---------------------------------------------------------------------------


def run_scenario(scenario: Scenario) -> tuple[bool, str, float]:
    """Return (parse_ok, observed_tool_name_or_error, duration_seconds)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": scenario.user_prompt},
    ]
    started = time.monotonic()
    try:
        response = ollama.chat(
            model="granite4.1:3b",
            messages=messages,
            tools=OPERATOR_TOOLS,
        )
    except Exception as exc:  # noqa: BLE001 -- want every failure to surface
        return False, f"exception: {type(exc).__name__}: {exc}", time.monotonic() - started
    duration = time.monotonic() - started

    msg = getattr(response, "message", None) or response.get("message", {})
    tool_calls = getattr(msg, "tool_calls", None) or (
        msg.get("tool_calls") if isinstance(msg, dict) else None
    )
    if not tool_calls:
        content_preview = (
            (msg.content if hasattr(msg, "content") else msg.get("content", ""))[:80] if msg else ""
        )
        return False, f"no tool_calls (content='{content_preview}')", duration

    first = tool_calls[0]
    fn = getattr(first, "function", None) or first.get("function", {})
    name = getattr(fn, "name", None) or fn.get("name") if fn else None
    if not name:
        return False, "tool_calls present but no function name", duration
    if name not in TOOL_NAMES:
        return False, f"unknown tool name: {name}", duration
    return True, name, duration


def main() -> int:
    scenarios = build_scenarios()
    total = len(scenarios)
    results: list[dict[str, Any]] = []
    parse_ok = 0
    tool_correct = 0
    durations: list[float] = []

    print(f"Running granite4.1:3b smoke test ({total} scenarios)\n")
    for scen in scenarios:
        ok, observed, dur = run_scenario(scen)
        durations.append(dur)
        if ok:
            parse_ok += 1
            if observed == scen.expected_tool:
                tool_correct += 1
        results.append(
            {
                "name": scen.name,
                "expected": scen.expected_tool,
                "observed": observed,
                "parse_ok": ok,
                "tool_match": ok and observed == scen.expected_tool,
                "duration_s": round(dur, 3),
            }
        )
        marker = "OK " if ok else "ERR"
        match = "match" if (ok and observed == scen.expected_tool) else "miss "
        print(f"  [{marker}] [{match}] {scen.name} -> {observed} ({dur:.2f}s)")

    error_rate = 1 - parse_ok / total
    print()
    print(f"Total scenarios:    {total}")
    print(f"Parse-valid:        {parse_ok} / {total} ({(parse_ok / total) * 100:.1f}%)")
    print(f"Tool-name correct:  {tool_correct} / {total} ({(tool_correct / total) * 100:.1f}%)")
    print(f"Parse error rate:   {error_rate * 100:.1f}%")
    print(f"Mean turn latency:  {sum(durations) / len(durations):.2f}s")
    print(f"Max turn latency:   {max(durations):.2f}s")

    out_path = "logs/granite_smoke_results.json"
    try:
        import os

        os.makedirs("logs", exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(
                {
                    "total": total,
                    "parse_ok": parse_ok,
                    "tool_correct": tool_correct,
                    "parse_error_rate": error_rate,
                    "results": results,
                },
                fh,
                indent=2,
            )
        print(f"\nDetailed results written to {out_path}")
    except OSError as exc:
        print(f"\nFailed to write {out_path}: {exc}", file=sys.stderr)

    if error_rate > 0.20:
        print("\nKILL SIGNAL: abandon granite routing", file=sys.stderr)
        return 1
    if parse_ok / total < 0.95:
        print(
            "\nWarning: parse-valid rate below informal 95% target; gate still passes.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
