#!/usr/bin/env python3
"""Granite TUI PTY Spike — post-run analyzer.

Walks /tmp/granite-pty-spike/stdlib/ and /tmp/granite-pty-spike/pexpect/ for
scenario-{1..8}.bin transcripts. Each transcript ends with a footer block
(written by the spike scripts) listing pass/fail, parse_failures, drain
iters, per-turn latencies, observed state, exit code, and total bytes.

The analyzer is a thin table-renderer — it emits a Markdown table and a
verdict block. The full prose report is hand-edited in
docs/plans/granite-tui-pty-spike-report.md after the runs complete, citing
the transcripts and this analyzer's table.

Verdict rubric (per the plan's scenario contract):
  - If any of the minimum-set scenarios {1, 2, 4, 5} fails for BOTH libraries
    -> "not drivable, here's why".
  - If any of the minimum-set scenarios fails for ONE library and passes for
    the other -> "drivable with caveats: use {winning}, not {losing}".
  - If all of {1, 2, 4, 5} pass for at least one library -> "drivable".
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TRANSCRIPT_ROOT = Path("/tmp/granite-pty-spike")
LIBRARIES = ("stdlib", "pexpect")
SCENARIOS = list(range(1, 9))  # 1..8
MINIMUM_SET = {1, 2, 4, 5}  # load-bearing for the verdict

# Footer marker may be preceded by ANSI escape sequences (stdlib) or be at
# line start (pexpect). Allow either form.
FOOTER_START_RE = re.compile(rb"--- scenario-(\d+) footer ---", re.MULTILINE)
# Key:value lines may be at line start (pexpect) or preceded by ANSI (stdlib).
# Allow optional ANSI prefix (escape sequences) at the start of each line.
KEY_VAL_RE = re.compile(rb"^(?:\x1b\[[0-9;]*[a-zA-Z])*([a-z_]+):\s*(.*)$", re.MULTILINE)
LIST_VAL_RE = re.compile(rb"\[([^\]]*)\]")


@dataclass
class ScenarioResult:
    scenario: int
    pass_: bool = False
    parse_failures: int = 0
    buf_drain_iters_max: int = 0
    latency_turns_ms: list[int] = field(default_factory=list)
    observed_state: str = ""
    exit_code: int = -1
    total_bytes: int = 0
    transcript_path: Path | None = None
    parse_error: str = ""

    @property
    def pass_label(self) -> str:
        return "✅ pass" if self.pass_ else "❌ fail"


def parse_footer(transcript: Path) -> ScenarioResult:
    """Parse the footer block from a transcript file."""
    raw = transcript.read_bytes()
    m = FOOTER_START_RE.search(raw)
    if not m:
        return ScenarioResult(
            scenario=int(transcript.stem.split("-")[-1]),
            parse_error="footer marker not found",
            transcript_path=transcript,
            total_bytes=len(raw),
        )
    footer = raw[m.start() :]
    result = ScenarioResult(
        scenario=int(transcript.stem.split("-")[-1]),
        transcript_path=transcript,
        total_bytes=len(raw),
    )

    # Iterate key: value lines until EOF or non-kv line
    for line_m in KEY_VAL_RE.finditer(footer):
        key = line_m.group(1).decode("utf-8", errors="replace")
        val = line_m.group(2).decode("utf-8", errors="replace").strip()
        if key == "pass":
            result.pass_ = val.lower() in ("true", "yes", "1")
        elif key == "parse_failures":
            try:
                result.parse_failures = int(val)
            except ValueError:
                result.parse_error = f"parse_failures not int: {val!r}"
        elif key == "buf_drain_iters_max":
            try:
                result.buf_drain_iters_max = int(val)
            except ValueError:
                result.parse_error = f"buf_drain_iters_max not int: {val!r}"
        elif key == "latency_turns_ms":
            list_m = LIST_VAL_RE.search(val.encode("utf-8"))
            if list_m:
                inner = list_m.group(1).decode("utf-8", errors="replace").strip()
                if inner:
                    try:
                        result.latency_turns_ms = [
                            int(x.strip()) for x in inner.split(",") if x.strip()
                        ]
                    except ValueError as e:
                        result.parse_error = f"latency_turns_ms parse error: {e}"
        elif key == "observed_state":
            # Strip ANSI escape sequences and surrounding quotes
            val = (
                re.sub(
                    rb"\x1b\[[0-9;]*[a-zA-Z]",
                    b"",
                    val.encode("utf-8", errors="replace"),
                )
                .decode("utf-8", errors="replace")
                .strip()
            )
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            result.observed_state = val
        elif key == "exit_code":
            try:
                result.exit_code = int(val)
            except ValueError:
                result.parse_error = f"exit_code not int: {val!r}"
        elif key == "total_bytes":
            try:
                result.total_bytes = int(val)
            except ValueError:
                pass  # already set from file size
    return result


def load_library(library: str) -> dict[int, ScenarioResult]:
    """Load all 8 scenario results for a given library."""
    lib_dir = TRANSCRIPT_ROOT / library
    results: dict[int, ScenarioResult] = {}
    for n in SCENARIOS:
        path = lib_dir / f"scenario-{n}.bin"
        if not path.exists():
            results[n] = ScenarioResult(
                scenario=n,
                parse_error=f"transcript missing: {path}",
            )
        else:
            results[n] = parse_footer(path)
    return results


def render_table(by_lib: dict[str, dict[int, ScenarioResult]]) -> str:
    """Render the per-scenario, per-library pass/fail table."""
    header = (
        "| # | stdlib | pexpect | stdlib observed | pexpect observed "
        "| stdlib bytes | pexpect bytes |"
    )
    sep = "|---|---|---|---|---|---|---|"
    rows = [header, sep]
    for n in SCENARIOS:
        s = by_lib["stdlib"].get(n)
        p = by_lib["pexpect"].get(n)
        s_label = s.pass_label if s else "—"
        p_label = p.pass_label if p else "—"
        s_obs = (s.observed_state or s.parse_error)[:80] if s else "—"
        p_obs = (p.observed_state or p.parse_error)[:80] if p else "—"
        s_bytes = str(s.total_bytes) if s else "—"
        p_bytes = str(p.total_bytes) if p else "—"
        rows.append(f"| {n} | {s_label} | {p_label} | {s_obs} | {p_obs} | {s_bytes} | {p_bytes} |")
    return "\n".join(rows)


def render_latency(by_lib: dict[str, dict[int, ScenarioResult]]) -> str:
    """Render per-turn latency observations per scenario per library."""
    lines = [
        "| # | stdlib turn ms (p50/max) | pexpect turn ms (p50/max) | stdlib drain iters |",
        "|---|---|---|---|",
    ]
    for n in SCENARIOS:
        s = by_lib["stdlib"].get(n)
        p = by_lib["pexpect"].get(n)
        s_lat = s.latency_turns_ms if s else []
        p_lat = p.latency_turns_ms if p else []
        s_drain = s.buf_drain_iters_max if s else 0

        def stats(lat: list[int]) -> str:
            if not lat:
                return "—"
            sorted_lat = sorted(lat)
            p50 = sorted_lat[len(sorted_lat) // 2]
            mx = sorted_lat[-1]
            return f"p50={p50} max={mx} n={len(lat)}"

        lines.append(f"| {n} | {stats(s_lat)} | {stats(p_lat)} | {s_drain} |")
    return "\n".join(lines)


def compute_verdict(by_lib: dict[str, dict[int, ScenarioResult]]) -> tuple[str, list[str]]:
    """Apply the verdict rubric and return (verdict, rationale_lines)."""
    rationale: list[str] = []
    failing_min_both: list[int] = []
    failing_min_one: list[tuple[int, str, str]] = []  # (scenario, winner, loser)
    all_min_pass_one: bool = True

    for n in sorted(MINIMUM_SET):
        s = by_lib["stdlib"].get(n)
        p = by_lib["pexpect"].get(n)
        s_pass = s and s.pass_
        p_pass = p and p.pass_
        if not s_pass and not p_pass:
            failing_min_both.append(n)
            rationale.append(
                f"- Scenario {n} FAILED in BOTH libraries — load-bearing "
                f"affordance cannot be detected."
            )
            all_min_pass_one = False
        elif s_pass and not p_pass:
            failing_min_one.append((n, "stdlib", "pexpect"))
            rationale.append(f"- Scenario {n} passed stdlib, failed pexpect.")
        elif p_pass and not s_pass:
            failing_min_one.append((n, "pexpect", "stdlib"))
            rationale.append(f"- Scenario {n} passed pexpect, failed stdlib.")
        else:
            rationale.append(f"- Scenario {n} passed in BOTH libraries.")

    if failing_min_both:
        verdict = "**not drivable, here's why**"
        rationale.insert(
            0,
            f"**Verdict: {verdict}** — minimum-set scenarios "
            f"{failing_min_both} failed in both libraries.",
        )
    elif failing_min_one:
        # Recommend the winning library by which had fewer min-set failures
        stdlib_fails = sum(1 for _, w, _ in failing_min_one if w == "pexpect")
        pexpect_fails = sum(1 for _, w, _ in failing_min_one if w == "stdlib")
        if stdlib_fails > pexpect_fails:
            winner, loser = "pexpect", "stdlib"
        elif pexpect_fails > stdlib_fails:
            winner, loser = "stdlib", "pexpect"
        else:
            winner, loser = "stdlib", "pexpect"  # tiebreak to stdlib (already imported in agent/)
        verdict = f"**drivable with caveats: use {winner}, not {loser}**"
        rationale.insert(
            0,
            f"**Verdict: {verdict}** — minimum-set scenarios "
            f"{sorted(MINIMUM_SET)} pass in at least one library; mixed results "
            f"on {[s for s, _, _ in failing_min_one]}.",
        )
    elif all_min_pass_one:
        verdict = "**drivable**"
        rationale.insert(
            0,
            f"**Verdict: {verdict}** — all minimum-set scenarios "
            f"{sorted(MINIMUM_SET)} pass in at least one library.",
        )
    else:
        verdict = "**inconclusive**"
        rationale.insert(0, f"**Verdict: {verdict}** — review rationale below.")

    return verdict, rationale


def main() -> int:
    by_lib: dict[str, dict[int, ScenarioResult]] = {}
    for lib in LIBRARIES:
        by_lib[lib] = load_library(lib)

    print("# Granite TUI PTY Spike — Analyzer Output")
    print()
    print("## Per-Scenario Pass/Fail")
    print()
    print(render_table(by_lib))
    print()
    print("## Per-Scenario Latency & Drain")
    print()
    print(render_latency(by_lib))
    print()
    print("## Verdict")
    print()
    verdict, rationale = compute_verdict(by_lib)
    print(verdict)
    print()
    for line in rationale:
        print(line)
    print()
    print("## Transcript Paths")
    print()
    for lib in LIBRARIES:
        for n in SCENARIOS:
            r = by_lib[lib].get(n)
            if r and r.transcript_path:
                print(f"- `{r.transcript_path}`")

    # Also emit machine-readable JSON for downstream tooling
    print()
    print("## Raw Results (JSON)")
    print()
    print("```json")
    print(
        json.dumps(
            {
                lib: {
                    str(n): {
                        "pass": r.pass_,
                        "parse_failures": r.parse_failures,
                        "buf_drain_iters_max": r.buf_drain_iters_max,
                        "latency_turns_ms": r.latency_turns_ms,
                        "observed_state": r.observed_state,
                        "exit_code": r.exit_code,
                        "total_bytes": r.total_bytes,
                        "parse_error": r.parse_error,
                        "transcript_path": str(r.transcript_path) if r.transcript_path else None,
                    }
                    for n, r in by_lib[lib].items()
                }
                for lib in LIBRARIES
            },
            indent=2,
        )
    )
    print("```")
    return 0


if __name__ == "__main__":
    sys.exit(main())
