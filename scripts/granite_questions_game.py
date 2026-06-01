"""Live questions-game harness for the granite-agent-loop PoC.

Measures how well granite4.1:3b performs as the *operator* when a real Claude
Code session asks multiple-choice questions -- i.e. can granite recognize a
numbered prompt and enter a valid answer via the `handle_choice` tool?

This exercises the single peculiarity that the offline emulator
(`tests/unit/granite_session_emulator.py`) can only fake: a genuine Claude
session, in headless `-p --output-format stream-json` mode, emitting numbered
options as assistant/result text and waiting for an answer.

Flow
----
1. Spawn ONE `ClaudeSession` (no PM/Dev split needed for the game).
2. Ask it to run an N-question multiple-choice quiz, one question per turn.
3. Each turn: read the session's events, hand them to the real `GraniteRouter`,
   and require a `handle_choice` decision whose payload is an in-range option
   number. Send that number back to the session.
4. Stop on `QUIZ COMPLETE` or after a turn cap.

Metrics reported (and written to logs/granite_questions_game.json):
  * questions_seen
  * handle_choice_rate  -- fraction of question turns where granite chose handle_choice
  * in_range_rate       -- fraction where the chosen number was a valid option
  * mean_router_latency_s
  * final score line echoed by Claude, if any

Prereqs (same as the PoC): `claude` on PATH logged in via OAuth, ollama running
with `granite4.1:3b` pulled. Run:

    python scripts/granite_questions_game.py --questions 5 --model haiku
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field

# Allow running as a bare script (python scripts/granite_questions_game.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.claude_session import ClaudeSession, ClaudeSessionConfig  # noqa: E402
from agent.granite_router import GraniteRouter, GraniteRoutingError  # noqa: E402

OPTION_RE = re.compile(r"^\s*[❯>*\-]*\s*(\d+)[.)]\s+\S")
RESULT_DONE_MARKER = "QUIZ COMPLETE"


def _quiz_prompt(n: int) -> str:
    return (
        f"Let's play a multiple-choice quiz. Ask me exactly {n} general-knowledge "
        "questions, ONE at a time. For each question, print the question text on its "
        "own line, then the answer options each on their own line numbered like "
        "'1. ', '2. ', '3. ', '4. '. Then stop and wait for my numeric answer. "
        "After I reply with a number, tell me whether it was correct in one line and "
        "immediately ask the next question. After the final question is answered, "
        f"print '{RESULT_DONE_MARKER}' followed by my score as 'X/{n}'. "
        "Do not ask anything other than the quiz questions. Begin with question 1 now."
    )


def _result_text(events: list[dict]) -> str:
    for ev in events:
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            return ev["result"]
    # fall back to concatenated assistant text
    chunks: list[str] = []
    for ev in events:
        if ev.get("type") == "assistant":
            for part in ev.get("message", {}).get("content", []) or []:
                if isinstance(part, dict) and part.get("type") == "text":
                    chunks.append(part.get("text", ""))
    return "\n".join(chunks)


def _valid_option_numbers(text: str) -> set[str]:
    return {m.group(1) for line in text.splitlines() if (m := OPTION_RE.match(line))}


@dataclass
class TurnRecord:
    turn: int
    options: list[str]
    granite_tool: str | None
    chosen: str
    in_range: bool
    router_latency_s: float


@dataclass
class GameReport:
    model: str
    questions_requested: int
    questions_seen: int = 0
    handle_choice_count: int = 0
    in_range_count: int = 0
    final_line: str = ""
    status: str = "incomplete"
    turns: list[TurnRecord] = field(default_factory=list)

    @property
    def handle_choice_rate(self) -> float:
        return self.handle_choice_count / self.questions_seen if self.questions_seen else 0.0

    @property
    def in_range_rate(self) -> float:
        return self.in_range_count / self.questions_seen if self.questions_seen else 0.0

    @property
    def mean_router_latency_s(self) -> float:
        lat = [t.router_latency_s for t in self.turns]
        return sum(lat) / len(lat) if lat else 0.0


def play(questions: int, model: str, turn_cap: int | None = None) -> GameReport:
    turn_cap = turn_cap or (questions * 3 + 3)
    report = GameReport(model=model, questions_requested=questions)
    router = GraniteRouter()
    session = ClaudeSession(ClaudeSessionConfig(model=model, cwd=os.getcwd()))
    session.start()
    try:
        session.send_message(_quiz_prompt(questions))
        for turn in range(1, turn_cap + 1):
            events = session.read_until_result(timeout=120)
            text = _result_text(events)
            if RESULT_DONE_MARKER in text:
                report.status = "done"
                for line in text.splitlines():
                    if RESULT_DONE_MARKER in line or re.search(r"\b\d+\s*/\s*\d+\b", line):
                        report.final_line = line.strip()
                break

            options = sorted(_valid_option_numbers(text))
            if not options:
                # Not a question turn (e.g. Claude acknowledged the answer without
                # immediately posing the next). Nudge it forward.
                session.send_message("continue")
                continue

            report.questions_seen += 1
            t0 = time.monotonic()
            try:
                decision = router.route(dev_events=events)
                err = None
            except GraniteRoutingError as exc:
                decision = None
                err = str(exc)
            latency = time.monotonic() - t0

            tool = decision.tool_name if decision else None
            chosen = decision.payload.strip() if (decision and decision.payload) else ""
            is_choice = tool == "handle_choice"
            in_range = is_choice and chosen in set(options)
            if is_choice:
                report.handle_choice_count += 1
            if in_range:
                report.in_range_count += 1

            report.turns.append(
                TurnRecord(
                    turn=turn,
                    options=options,
                    granite_tool=tool or (f"ERROR:{err}" if err else None),
                    chosen=chosen,
                    in_range=in_range,
                    router_latency_s=round(latency, 3),
                )
            )

            # Send the answer back. If granite misfired, default to option 1 so
            # the game can continue and we still measure the miss.
            answer = chosen if in_range else options[0]
            session.send_message(answer)
        else:
            report.status = "turn_cap_reached"
    finally:
        session.stop()
    return report


def _print_report(r: GameReport) -> None:
    print(f"\nModel:               {r.model}")
    print(f"Status:              {r.status}")
    print(f"Questions seen:      {r.questions_seen} / {r.questions_requested}")
    print(
        f"handle_choice rate:  {r.handle_choice_count}/{r.questions_seen} "
        f"({r.handle_choice_rate * 100:.0f}%)"
    )
    print(
        f"In-range answer:     {r.in_range_count}/{r.questions_seen} ({r.in_range_rate * 100:.0f}%)"
    )
    print(f"Mean router latency: {r.mean_router_latency_s:.2f}s")
    if r.final_line:
        print(f"Claude's score line: {r.final_line}")
    print("\nPer-turn:")
    for t in r.turns:
        mark = "OK " if t.in_range else "MISS"
        print(
            f"  [{mark}] turn {t.turn}: tool={t.granite_tool} chose={t.chosen!r} "
            f"opts={t.options} ({t.router_latency_s:.2f}s)"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Granite operator multiple-choice questions game")
    ap.add_argument("--questions", type=int, default=5)
    ap.add_argument("--model", default="haiku", help="Claude model alias (haiku/sonnet/opus)")
    ap.add_argument("--turn-cap", type=int, default=None)
    args = ap.parse_args()

    if shutil.which("claude") is None:
        print("ERROR: `claude` not found on PATH", file=sys.stderr)
        return 2
    try:
        import ollama  # noqa: F401
    except ImportError:
        print("ERROR: ollama Python package not installed", file=sys.stderr)
        return 2

    report = play(args.questions, args.model, args.turn_cap)
    _print_report(report)

    os.makedirs("logs", exist_ok=True)
    out = "logs/granite_questions_game.json"
    with open(out, "w", encoding="utf-8") as fh:
        payload = asdict(report)
        payload["handle_choice_rate"] = report.handle_choice_rate
        payload["in_range_rate"] = report.in_range_rate
        payload["mean_router_latency_s"] = report.mean_router_latency_s
        json.dump(payload, fh, indent=2)
    print(f"\nDetailed results written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
