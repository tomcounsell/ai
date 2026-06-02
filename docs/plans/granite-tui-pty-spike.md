---
status: Planning
type: feature
appetite: Medium
last_comment_id:
tracking: https://github.com/tomcounsell/ai/issues/1547
parent: https://github.com/tomcounsell/ai/issues/1546
---

# Granite TUI PTY Spike — Interactive Claude Code Drivability

## Problem

Issue #1546 (PoC: granite operator drives a REAL interactive Claude Code session
via PTY, no `claude -p`) rests on a load-bearing, untested assumption: **the
interactive Claude Code TUI can be driven programmatically through a pseudo-terminal
by an automated agent.** No code in the repo has ever tested this.

Every existing path to Claude — `agent/sdk_client.py:2186` and the prior
PoC's `agent/claude_session.py:104` `_build_cmd` — runs `claude -p` (headless).
Headless mode is the *one mode* that hides every hard part: ANSI redraws,
alt-screen rendering, prompt detection, two-stage ctrl-c interject, the
`claude --resume <uuid>` exit hint, terminal capability negotiation, and persona
priming via first-message text vs. slash command.

The prior PoC's docs (`docs/features/granite-agent-loop.md:290-299`) characterize
the TUI affordances the spike must exercise. These are the spec — not a
validated reality. We need a falsifiable test that either confirms the kernel
is buildable or reports the specific failure mode that makes it not.

## Goal (one sentence)

Produce a single report — `docs/plans/granite-tui-pty-spike-report.md` —
recording, with evidence, every claim the prior-PoC docs make about TUI
affordances and the #1546 kernel, plus any newly-discovered failure modes,
with a falsifiable verdict on whether the interactive TUI can be driven
reliably enough to underpin the #1546 PoC.

## Freshness Check

**Baseline commit:** current `main` at plan time (no SHA drift — references are
to characteristically-stable line numbers, and re-verified below).

**File:line references cited in the issue body — re-verified:**

| Reference | Verified | Notes |
|---|---|---|
| `agent/claude_session.py:104` (`_build_cmd`) | ✅ unchanged | Confirmed `claude -p` hard-wired; not modified by this spike |
| `agent/claude_session.py:28` (`import select`) | ✅ unchanged | Stdlib `select` is already imported; the spike's library #1 reuses the same primitive |
| `docs/features/granite-agent-loop.md:290-299` (TUI affordances) | ✅ unchanged | The spec the spike must exercise |
| `agent/granite_router.py:276` (event shape) | ✅ unchanged | Confirmed `list[dict]` consumer; spike output is downstream of this |
| `agent/claude_session.py:50-51` (`_UUID_RE`) | ✅ unchanged | Regex the spike's resume-UUID scrape must match |
| `.claude/commands/` does not exist | ✅ unchanged | Verified: directory absent (not just empty) |

**Sibling issues / PRs — re-verified:**

| Ref | Status | Relevance |
|---|---|---|
| #1546 (parent PoC) | OPEN, no PR | Spike is the precondition; #1546 should not start planning until the report lands |
| #1542 (granite_root_session_runner, cancelled) | CANCELLED | The cancelled cutover plan; cited in issue body. Reason: PoC was non-interactive. Confirmed the cancellation narrative |
| #1486 / PR #1487 (prior PoC, closed) | CLOSED | The prior interactive-PoC work; spike builds on its docs but not its code |
| #1542 critique at `docs/plans/critiques/` | exists, multiple revisions | Provides "what was tried" context for the kernel question |

**Active plans overlapping this area:**

- `docs/plans/granite_root_session_runner.md` — **status: Cancelled**. Touches the
  same granite/granite-loop domain but was a *production cutover* plan, not a
  TUI-feasibility spike. The cancellation makes this spike the correct next move.
  No coordination needed.

**Disposition:** Unchanged — issue claims still hold; the kernel question
(PTY → TUI → bytes) is unaddressed by any in-flight or shipped work.

## Prior Art

| Source | What it did | Why it didn't answer the spike's question |
|---|---|---|
| `agent/claude_session.py` (the headless wrapper) | Runs `claude -p --input-format stream-json`, drives it via stdin/stdout lines | Headless only. Sidesteps every TUI affordance the spike tests |
| `agent/sdk_client.py:2186` (the production path) | Same headless `-p` mode | Same blindspot |
| `docs/features/granite-agent-loop.md:290-299` | Describes TUI affordances (ctrl-c stages, resume hint, numbered menus) | Pure prose spec, never tested against a real interactive session |
| `tests/integration/test_claude_session_resume.py` | Verifies the headless-mode `--resume` round-trip | Headless, not TUI. Confirms UUID capture from stream-json, not from terminal bytes |
| `scripts/granite_questions_game.py` | Live operator benchmark against a `claude -p` session | Uses the headless wrapper; never spawns a TUI |
| `docs/plans/granite_root_session_runner.md` | Production cutover plan for the granite operator | Cancelled — the *cutover* was rejected, not the substrate experiment |

**Why previous fixes failed (or didn't address this):** None of the prior work
attempted the interactive-TUI substrate. The closest — the cancelled
`granite_root_session_runner.md` plan — was a *production cutover* that
inherited `claude_session.py`'s headless-only design without challenging it.
The spike exists because no one tested the kernel.

## Research

No relevant external findings — the substrate is the *specific* `claude` CLI
binary on the operator's machine, not a public library. Web research on
"pexpect vs. pty in Python" or "Claude Code TUI architecture" would produce
generic PTY-automation material that doesn't constrain the spike's design.
Proceeding with codebase context and the two candidate libraries (stdlib
`pty`+`select`, `pexpect`).

## Data Flow

```
scripts/granite_tui_pty_spike.py        scripts/granite_tui_pty_spike_pexpect.py
  (stdlib path)                           (pexpect path)
        │                                          │
        │  pty.fork() / pty.spawn()                │  pexpect.spawn()
        ▼                                          ▼
   ┌─────────────────────────────────────────────────────┐
   │  real `claude` subprocess attached to a PTY         │
   │  (no -p, no --input-format stream-json)            │
   │  inherits env with ANTHROPIC_API_KEY=""            │
   └─────────────────────────────────────────────────────┘
        │                                          │
        │  select() on master fd                   │  expect() pattern matching
        ▼                                          ▼
   raw bytes → /tmp/granite-pty-spike/        raw bytes → /tmp/granite-pty-spike/
   stdlib/{scenario-N}.bin                    pexpect/{scenario-N}.bin
        │                                          │
        └──────────────┬───────────────────────────┘
                       ▼
            scripts/granite_tui_pty_spike_report.py
              (post-run analyzer; reads transcripts,
              computes per-scenario pass/fail + latency,
              renders the report markdown)
                       │
                       ▼
            docs/plans/granite-tui-pty-spike-report.md
              (committed; the spike's deliverable)
```

The post-run analyzer is a separate script (not inline in the spike) so the
report can be re-rendered without re-running the scenarios — useful for
comparing transcripts against a later TUI version without paying the
latency cost of a re-run.

## Architectural Impact

- **Coupling:** none added. The spike writes `scripts/granite_tui_pty_spike*.py`
  and `docs/plans/granite-tui-pty-spike-report.md`. It does not import from
  `agent/`, does not modify `claude_session.py` or `sdk_client.py`, does not
  add to `pyproject.toml`'s runtime dependencies (only `[dependency-groups]
  dev` for `pexpect`).
- **Data ownership:** the report is the spike's deliverable. No agent state,
  no Redis keys, no env vars are written.
- **Module surface:** zero production impact. The scripts are run-once
  experiments.

## Appetite

**Medium.** 1-2 days of focused implementation, as the issue specifies.
Two library implementations (8 scenarios × 2 libraries = 16 runs), a
post-run analyzer, and the report.

## Prerequisites

- Interactive `claude` binary on PATH (already in use by the prior PoC).
- A scratch directory for raw byte transcripts: `/tmp/granite-pty-spike/`
  (created at script start, gitignored implicitly — not under repo).
- `pexpect` 4.9.0 (already installed system-wide; will be added to
  `[dependency-groups] dev` in `pyproject.toml` for the second candidate
  library to make the spike reproducible).
- **Not required:** `granite4.1:3b`, ollama, `GraniteRouter`. The spike
  tests the substrate, not the operator.

## Solution

Three deliverables, all under the spike's lane (no production paths touched):

### 1. `scripts/granite_tui_pty_spike.py` (stdlib path)

- Imports: `os`, `pty`, `select`, `subprocess`, `time`, `sys`, `signal`,
  `pathlib.Path`, `json`, `uuid`.
- For each of the 8 scenarios:
  - Open a transcript file at
    `/tmp/granite-pty-spike/stdlib/scenario-{N}.bin` (binary write).
  - `pid, fd = pty.fork()`; in the child, `os.execvp("claude", ["claude",
    "--model", "sonnet", "--permission-mode", "bypassPermissions"])`.
    `ANTHROPIC_API_KEY=""` in the child env (delete or blank the inherited
    key, same pattern as `agent/claude_session.py:_build_env`).
  - In the parent: `select.select([fd], [], [], timeout)` per turn; read
    available bytes, write verbatim to the transcript, and apply the
    scenario's expected prompts (e.g., send "hello\n" for scenario 2,
    "\x03" for scenario 4, etc.).
  - Per-scenario pass/fail determined by an explicit assertion on the
    observed terminal state (e.g., "saw the `>` prompt within 30s" for
    scenario 1; "saw the two-stage interject prompt" for scenario 4).
  - Latency measured per turn via `time.monotonic()` deltas.
  - Parse-failure counter: incremented each time a scenario's expected
    prompt was not detected within its timeout.
  - On exit, `os.close(fd)`, `os.waitpid(pid, 0)`.
- All 8 scenarios run sequentially in a single process for stdlib (the
  pexpect version gets its own process per scenario for cleaner teardown,
  per the issue's "no respawns" requirement for scenario 7).

### 2. `scripts/granite_tui_pty_spike_pexpect.py` (pexpect path)

- Same 8 scenarios, same assertions, same transcript format, same env
  stripping — but the driver is `pexpect.spawn("claude", ["--model",
  "sonnet", "--permission-mode", "bypassPermissions"], env={...,
  "ANTHROPIC_API_KEY": ""}, echo=False, encoding="utf-8")`.
- Uses `child.expect(pattern, timeout=...)` for prompt detection
  (patterns include the `>` prompt regex, the `Interrupted · What should
  Claude do instead?` string for scenario 4, the `claude --resume <uuid>`
  regex for scenario 5, etc.).
- Per-scenario pass/fail is the `expect()` return value (matched → pass;
  `pexpect.TIMEOUT` / `pexpect.EOF` → fail with diagnostic).

### 3. `scripts/granite_tui_pty_spike_report.py` (post-run analyzer)

- Walks `/tmp/granite-pty-spike/stdlib/` and `/tmp/granite-pty-spike/pexpect/`.
- For each scenario, emits:
  - Pass/fail per library.
  - Latency per turn (mean, p50, p95 across the two libraries).
  - Parse-failure count per library.
  - Path to the raw byte transcript.
  - First 200 bytes of transcript for quick eyeballing (ANSI escapes
    escaped for markdown readability).
- Renders the verdict section: **drivable** / **not drivable, here's why** /
  **drivable with these specific caveats** — derived from a hard-coded
  rubric: if any scenario that the prior-PoC docs claim is detectable
  (scenarios 1, 2, 3, 4, 5) fails for *both* libraries, the verdict is
  "not drivable." If it fails for one library but the other passes, the
  verdict is "drivable with caveats: use {winning library}, not {losing
  library}." If both pass, the verdict is "drivable."
- Writes `docs/plans/granite-tui-pty-spike-report.md` (committed by the
  spike's run, not by the spike scripts themselves — the scripts only
  write to `/tmp/`; the report is the human's commit at the end of the
  spike).

### 4. The report itself (`docs/plans/granite-tui-pty-spike-report.md`)

Required content per the issue's Acceptance Criteria:
- Per-scenario pass/fail for both libraries, with raw byte transcripts
  linked (relative paths from repo root into `/tmp/granite-pty-spike/`).
- Latency per turn, parse-failure counts, byte-loss observations.
- Side-by-side library comparison (stdlib vs. pexpect) with a
  recommendation.
- Falsifiable verdict on the kill-or-proceed gate.
- An honest "what's still unknown after the spike" section.
- Explicit reference to which of #1546's open questions it resolves
  (resolves: #1 PTY library, #2 TUI drivable, partial #5 resume UUID)
  and which it does not (deferred: #3 persona priming, #4 event-bridge
  shape).
- Explicit non-recommendation on persona priming, event-bridge shape,
  and orchestration.

## Failure Path Test Strategy

The spike is itself an experiment — its "test strategy" is the scenario
rubric. Each scenario has a hard pass/fail criterion; the report records
both the verdict and the evidence. Failure modes the spike must document
when they occur:

- **PTY desync:** observed prompt content diverges from the expected
  string (e.g., mid-redraw, escaped `>` inside a code block, alt-screen
  residue). Record the divergence and the raw bytes.
- **Byte loss:** a prompt expected at turn N is never observed; subsequent
  sends hit a desynced state. Record the gap.
- **Latency cliff:** any single turn exceeds 5x the median turn latency
  in the same scenario. Record the cliff turn.
- **Resume UUID mismatch:** the UUID captured from the on-exit hint does
  not match the regex `_UUID_RE` in `agent/claude_session.py:49-51` (i.e.,
  the prior PoC's parser would fail to extract it). Record the actual
  format.

Negative control (scenario 8) has no pass criterion — its only requirement
is to record the failure mode honestly so the report can say "and here's
what happens when the operator runtime forgets to set up a PTY."

## Test Impact

No existing tests affected — this is a greenfield investigation. Two new
scripts (`scripts/granite_tui_pty_spike*.py`, `scripts/granite_tui_pty_spike_report.py`)
are added under `scripts/`, but they are run-once experiments, not test
files. The post-run analyzer's report is the deliverable, not a pytest
target. `pexpect` and `ptyprocess` get added to `[dependency-groups] dev`
in `pyproject.toml` so the second candidate library is reproducible
(Issue Scope: spike should be re-runnable; not a runtime dep).

## Rabbit Holes

- **Don't wire the spike into any production path.** The spike is a
  standalone experiment. If a temptation arises to "just use this code
  in `claude_session.py`," resist — the spike's job is to answer a
  question, not to refactor.
- **Don't build a GraniteRouter replacement.** The "operator" in the
  spike is a Python stub (if X then send Y). Real operator logic is
  #1546's problem.
- **Don't re-test the headless path.** The issue's Recon Summary is
  explicit: the prior PoC already verified headless `--resume` and
  stream-json UUID capture. The spike tests the *interactive* path only.
- **Don't validate `--input-format stream-json` against the TUI.** It's
  not a TUI flag; the prior PoC's documentation already covers this.
- **Don't use `claude-agent-sdk`.** The issue is explicit. If the
  spike's environment has it installed, the scripts must not import it.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `claude` TUI version differs from what the prior PoC's docs describe, breaking the spec the spike must exercise | Medium | Medium | The report's "what's still unknown" section absorbs version-drift findings; the spike exercises the spec *as documented*, and any spec deviation becomes evidence |
| `pexpect` 4.9.0 has an incompatibility with the local `claude` build (PTY child setup, signal forwarding) | Low | Low | The stdlib path is the fallback. If `pexpect` fails on a plumbing issue, the spike still produces a verdict on stdlib |
| TUI detection is fundamentally hard (e.g., the `>` prompt regex is ambiguous with code-block `>` in Claude's output) | Medium | High | This is a finding the spike exists to discover. The report's verdict will say "drivable with caveats: prompt boundary detection is non-trivial; see scenario-X evidence" |
| Running 8 scenarios × 2 libraries × ~60s each pushes the spike past 1-2 days | Low | Low | Scenarios are time-bounded (each has a 30s or 60s hard timeout). Total runtime < 30 minutes wall-clock |
| Transcript disk usage (raw bytes per scenario per library) | Low | Low | Hard cap: 1 MiB per transcript, 16 MiB total. Truncate with a `[truncated]` marker if exceeded |

## Race Conditions

- **Spawn race:** the stdlib path uses `pty.fork()` which is atomic;
  pexpect's `spawn()` is single-threaded. No race.
- **Output ordering:** the spike reads bytes in `select()` order
  (FIFO). TUI redraws can interleave writes; the spike captures
  verbatim, in receive order. Order is *not* preserved across a prompt
  boundary in the TUI's semantic sense — only in byte order. The
  report makes this explicit so the reader doesn't misread the
  transcripts.
- **Resume UUID timing:** scenario 5 captures the UUID from the
  on-exit hint. If the TUI prints the hint during shutdown and the
  reader exits before the bytes are flushed, the UUID is lost. The
  spike waits 2s after the second ctrl-c (scenario 4) before
  closing the PTY to let the hint print.

## No-Gos (Out of Scope)

- Operator intelligence (granite, ollama, GraniteRouter) — #1546 only.
- Persona priming mechanism — #1546 only.
- Slash commands in `.claude/commands/` — #1546 only.
- Multi-session orchestration (PM + Dev, dual-resume UI) — explicitly
  deferred per #1546 ("trivial in comparison").
- Replacing `sdk_client.py` or `claude_session.py` — the spike writes
  *new* code in a new path; existing headless harness is untouched.
- Any `claude-agent-sdk` import.
- Use of `claude -p`, `--input-format stream-json` (headless flags).
- Recommendation on persona priming, event-bridge shape, or
  orchestration in the report.
- Production test coverage (the spike is an experiment, not a test).

## Update System

No update system changes required — this feature is purely internal.
The spike scripts live under `scripts/`, the report under
`docs/plans/`, and the optional `pexpect` addition is a dev-only
`[dependency-groups]` entry. Nothing needs to be propagated to other
machines via `/update`.

## Agent Integration

No agent integration required — this is a research spike, not an
agent-invocable tool. The scripts are invoked manually by the human
(or by `/do-build` if the spike is itself built by a Dev session).
No new CLI entry point, no MCP server, no bridge change. The spike's
deliverable is a markdown report the human reads; it is not
agent-reachable code.

## Documentation

- [ ] Create `docs/plans/granite-tui-pty-spike-report.md` (the spike's
      deliverable) when the spike completes. Required content per the
      issue's Acceptance Criteria (per-scenario pass/fail for both
      libraries, latencies, side-by-side comparison, falsifiable
      verdict, "still unknown" section, which #1546 questions it
      resolves, which it does not).
- [ ] Add a one-paragraph summary to `docs/features/granite-agent-loop.md`
      under a new `## TUI PTY Spike` section, linking the report. This
      becomes the durable breadcrumb so a future reader of the granite
      docs sees the substrate feasibility result alongside the loop
      description.

## Success Criteria

- [ ] `scripts/granite_tui_pty_spike.py` exists and runs all 8 scenarios
      against stdlib `pty`+`select`.
- [ ] `scripts/granite_tui_pty_spike_pexpect.py` exists and runs all 8
      scenarios against `pexpect`.
- [ ] Raw byte transcripts persisted at
      `/tmp/granite-pty-spike/stdlib/scenario-{1..8}.bin` and
      `/tmp/granite-pty-spike/pexpect/scenario-{1..8}.bin`.
- [ ] `scripts/granite_tui_pty_spike_report.py` exists, reads the
      transcripts, and renders the report.
- [ ] `docs/plans/granite-tui-pty-spike-report.md` exists with the
      content required by the issue.
- [ ] The report's verdict is one of the three allowed values
      (drivable / not drivable, here's why / drivable with caveats).
- [ ] The report explicitly states which of #1546's open questions
      are resolved and which are deferred.
- [ ] No code in the spike imports `claude-agent-sdk` or uses
      `claude -p` / `--input-format stream-json`.
- [ ] `pexpect` and `ptyprocess` are added to `[dependency-groups] dev`
      in `pyproject.toml` (not `[project.dependencies]`).
- [ ] Spike runtime under 30 minutes wall-clock for all 16 runs.

## Spike Results

*(filled after the spike runs)*

## Open Questions

None for the spike itself. The spike's report will answer #1546's
open questions #1 (PTY library) and #2 (TUI drivable) and partially
#5 (resume UUID); the remaining #1546 questions are out of scope per
the issue.
