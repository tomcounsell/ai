"""Persona-toolbelt baseline report from the session telemetry stream (plan #3081).

Publishes the pre-activation measurements the −40% context / −25% turns
targets are judged against: tool-surface tokens, per-tool attributed cost,
tool-call turns, and PreToolUse denial counts — optionally normalized per
merged PR. Reads ONLY the per-session telemetry JSONL files
(``logs/session_telemetry/``); it never queries Redis or GitHub, so the same
stream that produced a number can always reproduce it.

Event types consumed (see ``agent/session_telemetry.py`` for the schema):

* ``tool_cost``           — per-tool attributed tokens + tool_call_count +
                            initial_context_tokens + tool_surface_size, one
                            event per harness subprocess
                            (method ``assistant-usage-delta/v1`` — a ranking
                            aid, not billing; see agent/tool_cost_attribution.py)
* ``token_usage``         — raw per-turn usage + total_cost_usd
* ``pre_tool_use_denial`` — one event per PreToolUse deny, tagged by cause

Denial counts split belt-relevant vs belt-irrelevant (see
``BELT_IRRELEVANT_CAUSES``). ``denials_belt_relevant`` is the field task 4
consumes as the escalation-ceiling baseline; a belt cannot prevent a denial
whose cause it does not control, and counting those would inflate the ceiling.

The measurement contract from the plan's Failure Path section: an empty or
missing stream is reported AS an absence of measurement, never as a zero
baseline — zeros here would make the −40% target trivially "met".

Per-PR normalization needs the merged-PR count for the same window; pass it
explicitly with ``--merged-pr-count`` (e.g. from
``gh pr list --state merged --search "merged:>=DATE" --json number --jq length``).
Without it the report prints window totals and says how to normalize.

Exit codes (stable):
    0 — report produced from at least one measured event
    1 — the telemetry directory could not be read
    2 — usage error (argparse)
    3 — no measurements in the window (explicit empty state; NOT a zero baseline)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent.session_telemetry import _get_telemetry_dir

EXIT_OK = 0
EXIT_UNREADABLE = 1
EXIT_NO_DATA = 3

#: Per-tool rows shown before ``--full`` is needed.
_COMPACT_ROW_LIMIT = 15

#: Event types this report folds. Anything else in the stream is ignored.
_CONSUMED_TYPES = ("tool_cost", "token_usage", "pre_tool_use_denial")

#: Denial causes a persona belt cannot affect (plan #3081 Risk 1).
#:
#: Task 4 arms the escalation rollback gate against
#: ``max(ESCALATION_CEILING_MULTIPLIER x belt-relevant denial baseline,
#: ESCALATION_CEILING_FLOOR)``. Counting causes a belt does not control would
#: inflate that ceiling and mask a belt that is genuinely cut too tight, so the
#: denominator excludes them. ``sensitive_path`` and ``teammate_write`` are the
#: two the plan names; ``tool_budget`` (a per-session spend cap) and
#: ``foreground_guard`` (a subagent-backgrounding guard) are excluded on the
#: same principle — both are orthogonal to which tools a belt offers.
#:
#: ``teammate_write`` leaves this set in Lane B, once ``valor-docs-write``
#: replaces the hook branch and the restriction becomes belt-expressible.
BELT_IRRELEVANT_CAUSES = frozenset(
    {"sensitive_path", "teammate_write", "tool_budget", "foreground_guard"}
)


def _int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _parse_ts(raw: object) -> datetime | None:
    """Parse a telemetry ``ts`` (ISO 8601 ending in 'Z'). None when unusable."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_since(spec: str) -> timedelta | None:
    """Parse ``30d`` / ``12h`` / ``90m`` into a timedelta. None when unparseable."""
    spec = (spec or "").strip().lower()
    if not spec:
        return None
    units = {"d": "days", "h": "hours", "m": "minutes"}
    if spec[-1] not in units:
        return None
    try:
        return timedelta(**{units[spec[-1]]: float(spec[:-1])})
    except ValueError:
        return None


def collect_baseline(telemetry_dir: Path, cutoff: datetime | None = None) -> dict:
    """Fold every consumed event across all session files into one aggregate.

    Sessions are independent files, so one unreadable or half-written file
    must not hide the rest — each is read defensively and skipped on error,
    matching ``read_session_timeline``'s malformed-line tolerance.
    """
    per_tool: dict[str, dict[str, int]] = {}
    denials: Counter[str] = Counter()
    sessions_measured: set[str] = set()
    harness_turns = 0
    tool_calls = 0
    dropped_samples = 0
    total_cost_usd = 0.0
    initial_context_samples: list[int] = []
    tool_surface_sizes: list[int] = []

    for path in sorted(telemetry_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    etype = event.get("type")
                    if etype not in _CONSUMED_TYPES:
                        continue
                    if cutoff is not None:
                        ts = _parse_ts(event.get("ts"))
                        if ts is not None and ts < cutoff:
                            continue

                    session_id = str(event.get("session_id") or path.stem)
                    sessions_measured.add(session_id)

                    if etype == "tool_cost":
                        harness_turns += 1
                        tool_calls += _int(event.get("tool_call_count"))
                        dropped_samples += _int(event.get("dropped_samples"))
                        if event.get("initial_context_tokens") is not None:
                            initial_context_samples.append(
                                _int(event.get("initial_context_tokens"))
                            )
                        if event.get("tool_surface_size") is not None:
                            tool_surface_sizes.append(_int(event.get("tool_surface_size")))
                        for name, row in (event.get("per_tool") or {}).items():
                            if not isinstance(row, dict):
                                continue
                            slot = per_tool.setdefault(
                                str(name),
                                {
                                    "calls": 0,
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "total_tokens": 0,
                                },
                            )
                            for key in slot:
                                slot[key] += _int(row.get(key))
                    elif etype == "token_usage":
                        raw_cost = event.get("total_cost_usd")
                        if isinstance(raw_cost, (int, float)):
                            total_cost_usd += float(raw_cost)
                    elif etype == "pre_tool_use_denial":
                        denials[str(event.get("cause") or "<unknown>")] += 1
        except OSError:
            continue

    return {
        "sessions_measured": len(sessions_measured),
        "harness_turns": harness_turns,
        "tool_calls": tool_calls,
        "dropped_samples": dropped_samples,
        "total_cost_usd": round(total_cost_usd, 4),
        "per_tool": dict(sorted(per_tool.items(), key=lambda kv: -kv[1]["total_tokens"])),
        "attributed_tokens": sum(r["total_tokens"] for r in per_tool.values()),
        "initial_context_tokens_avg": (
            round(sum(initial_context_samples) / len(initial_context_samples))
            if initial_context_samples
            else None
        ),
        "tool_surface_size_max": max(tool_surface_sizes, default=None),
        "denials": dict(denials.most_common()),
        "denials_total": sum(denials.values()),
        # Split out as its own field: a later lane consumes `denials_belt_relevant`
        # directly as the escalation-ceiling baseline (plan Risk 1, task 4).
        "denials_belt_relevant": sum(
            n for cause, n in denials.items() if cause not in BELT_IRRELEVANT_CAUSES
        ),
        "denials_belt_irrelevant": sum(
            n for cause, n in denials.items() if cause in BELT_IRRELEVANT_CAUSES
        ),
    }


def _per_pr(value: float, pr_count: int) -> str:
    return f"{value / pr_count:,.1f}"


def _render(summary: dict, telemetry_dir: Path, args: argparse.Namespace) -> str:
    window = f" in the last {args.since}" if args.since else ""
    lines = [f"Toolbelt baseline — {telemetry_dir}{window}", ""]

    lines += [
        f"  sessions measured   {summary['sessions_measured']}",
        f"  harness turns       {summary['harness_turns']}",
        f"  tool calls          {summary['tool_calls']}",
        f"  attributed tokens   {summary['attributed_tokens']:,}"
        "  (assistant-usage-delta/v1 — ranking aid, not billing)",
        f"  total cost (USD)    {summary['total_cost_usd']}",
    ]
    if summary["initial_context_tokens_avg"] is not None:
        lines.append(
            f"  initial context     {summary['initial_context_tokens_avg']:,} tokens avg/turn"
            "  (tool-definition-bearing prefix — the surface --tools shrinks)"
        )
    if summary["tool_surface_size_max"] is not None:
        lines.append(f"  tool surface        {summary['tool_surface_size_max']} tools offered")
    if summary["dropped_samples"]:
        lines.append(
            f"  dropped samples     {summary['dropped_samples']}"
            "  (attribution errors — stream partially degraded)"
        )

    rows = list(summary["per_tool"].items())
    shown = rows if args.full else rows[:_COMPACT_ROW_LIMIT]
    if rows:
        lines += ["", f"Per tool ({len(shown)} of {len(rows)}, by total tokens):"]
        lines.append(f"  {'tool':<28} {'calls':>6} {'result-ctx':>11} {'output':>8} {'total':>10}")
        for name, row in shown:
            lines.append(
                f"  {name:<28} {row['calls']:>6} {row['input_tokens']:>11,}"
                f" {row['output_tokens']:>8,} {row['total_tokens']:>10,}"
            )
        if len(shown) < len(rows):
            lines.append(f"  … {len(rows) - len(shown)} more — pass --full to see every tool.")

    lines += [
        "",
        f"PreToolUse denials: {summary['denials_total']}",
        f"  belt-relevant     {summary['denials_belt_relevant']:>6}"
        "   <- task 4's escalation-ceiling baseline",
        f"  belt-irrelevant   {summary['denials_belt_irrelevant']:>6}"
        "   (causes a belt cannot affect)",
    ]
    lines += [
        f"    {cause:<26} {count}"
        + ("" if cause not in BELT_IRRELEVANT_CAUSES else "  [irrelevant]")
        for cause, count in summary["denials"].items()
    ]
    if not summary["denials"]:
        lines.append(
            "  none recorded — denial telemetry only counts instrumented causes "
            "(tool_budget today), so zero here means no instrumented denials, "
            "not necessarily no denials."
        )

    if args.merged_pr_count:
        n = args.merged_pr_count
        lines += [
            "",
            f"Per merged PR (over {n} PRs):",
            f"  tool calls / PR          {_per_pr(summary['tool_calls'], n)}",
            f"  attributed tokens / PR   {_per_pr(summary['attributed_tokens'], n)}",
            f"  denials / PR             {_per_pr(summary['denials_total'], n)}",
            f"  belt-relevant denials/PR {_per_pr(summary['denials_belt_relevant'], n)}",
        ]
    else:
        lines += [
            "",
            "Per-PR normalization skipped — pass --merged-pr-count N for the same "
            "window (e.g. gh pr list --state merged "
            '--search "merged:>=DATE" --json number --jq length).',
        ]

    lines += [
        "",
        "Next step: publish this report before flipping TOOLBELTS_ENFORCE "
        "(plan #3081 task 4) — the −40% context / −25% turns targets are "
        "unfalsifiable without it. Re-run post-rollout with the same window "
        "flags for the before/after.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.belt_baseline",
        description=(
            "Toolbelt baseline from session telemetry: tool-surface tokens, "
            "per-tool attributed cost, tool-call turns, and PreToolUse denial "
            "counts, optionally normalized per merged PR."
        ),
        epilog=(
            "Exit codes: 0 report produced, 1 telemetry unreadable, "
            "3 no measurements in the window (explicit empty state, never a "
            "zero baseline)."
        ),
    )
    parser.add_argument(
        "--telemetry-dir",
        type=Path,
        default=None,
        help="Session telemetry directory (default: the repo's logs/session_telemetry).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Only count events newer than this window, e.g. 30d / 12h / 90m.",
    )
    parser.add_argument(
        "--merged-pr-count",
        type=int,
        default=None,
        help="Merged-PR count for the same window; enables the per-PR normalization.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"List every tool instead of the top {_COMPACT_ROW_LIMIT}.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    args = parser.parse_args(argv)

    if args.merged_pr_count is not None and args.merged_pr_count <= 0:
        print(
            json.dumps({"error": "bad_merged_pr_count", "value": args.merged_pr_count}),
            file=sys.stderr,
        )
        return EXIT_UNREADABLE

    cutoff = None
    if args.since:
        delta = _parse_since(args.since)
        if delta is None:
            print(
                json.dumps({"error": "bad_since", "value": args.since, "expected": "30d|12h|90m"}),
                file=sys.stderr,
            )
            return EXIT_UNREADABLE
        cutoff = datetime.now(UTC) - delta

    telemetry_dir = args.telemetry_dir or _get_telemetry_dir()
    if not telemetry_dir.is_dir():
        print(
            json.dumps({"error": "telemetry_dir_missing", "path": str(telemetry_dir)}),
            file=sys.stderr,
        )
        return EXIT_UNREADABLE

    summary = collect_baseline(telemetry_dir, cutoff)

    # The empty-stream contract: absence of measurement is stated as such and
    # exits distinctly. Printing zeros here would publish a fake baseline that
    # makes the −40% target trivially satisfiable.
    if summary["harness_turns"] == 0 and summary["denials_total"] == 0:
        message = (
            f"NO MEASUREMENTS FOUND in {telemetry_dir}{' for ' + args.since if args.since else ''}."
            "\n\nThe telemetry stream holds no tool_cost, token_usage, or "
            "pre_tool_use_denial events in this window — nothing was measured, "
            "so there is NO baseline to publish (this is an absence of data, "
            "not a zero baseline).\n\n"
            "Next step: confirm instrumented sessions have run since the Lane A "
            "attribution landed, or widen/drop --since."
        )
        if args.json:
            print(json.dumps({"telemetry_dir": str(telemetry_dir), "measured": False}))
        else:
            print(message)
        return EXIT_NO_DATA

    if args.json:
        print(
            json.dumps({"telemetry_dir": str(telemetry_dir), "measured": True, **summary}, indent=2)
        )
    else:
        print(_render(summary, telemetry_dir, args))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
