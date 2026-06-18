---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-06-18
tracking: https://github.com/tomcounsell/ai/issues/1725
last_comment_id:
---

# Pluggable Builder Harness — Pi as Builder #1

## Problem

The granite container (`agent/granite_container/`) runs a two-PTY driver↔builder loop: an Opus **PM** session (a `claude` TUI under pexpect) routes work to a **Dev** session (a second `claude` TUI) and relays between them. The Dev side — the *builder* — is hardwired to `claude` in five places, so the only coding harness granite can ever drive is Claude Code.

This is more rigid than it needs to be. The PM/driver is the smart, model-heavy layer; the builder is an executor whose contract is thin ("given an instruction, do the work, produce a final report"). There is no reason the builder must be the same harness as the driver. Driving a *different* builder (Pi, and later others) is granite's genuine differentiator over plain subagents: the Opus driver can pick the right builder per task, cheaply, without the whole system being one vendor's TUI.

**Current behavior:**
- The Dev is spawned as `pexpect.spawn("claude", ...)` (`pty_driver.py:390`), primed via the `/granite:prime-dev-role` slash command, detected idle via claude's TUI bottom-bar/glyph heuristic, and its reply is read verbatim from claude's JSONL transcript (`last_assistant_text` with a `text_bearing_count` baseline) — the dev branch of `Container._route_pm_classification` (`container.py:1308-1359`).
- The routing classifier only knows `[/dev]`, `[/user]`, `[/complete]` (`granite_classifier.py`, `^\[/(dev|user|complete)\]\s*$`). There is no way for the PM to express *which* builder a task should go to.
- Adding any non-claude builder today means forking five coupled code paths with no seam to plug into.

**Desired outcome:**
- A `BuilderHarness` protocol is the single seam the container talks to for builder turns. The existing claude path becomes one implementation (`PtyClaudeBuilder`) behind it with **zero behavior change**.
- A second implementation, `PiSubprocessBuilder`, drives the Pi coding agent (`pi -p --mode json`) as a builder. Pi is a subprocess, not a PTY — no startup parser, no idle heuristic, structured response extraction.
- The Opus PM selects the builder per task by emitting `[/dev:pi]` / `[/dev:claude]` (bare `[/dev]` → default claude). This is also a hardening of the routing sentinel.
- End-to-end proof: a real task routed to `[/dev:pi]` is executed by Pi and its result relayed back to the user through the normal granite flow.

## Freshness Check

**Baseline commit:** `91289fc3`
**Issue filed at:** N/A — plan initiated from a live design discussion on 2026-06-18 (no pre-existing issue; tracking issue created with this plan).
**Disposition:** Overlap

**File:line references re-verified (read directly during the design discussion at baseline):**
- `agent/granite_container/pty_driver.py:390` — `pexpect.spawn("claude", args, ...)` with `--permission-mode bypassPermissions`, optional `--session-id`/`--model` — confirmed present.
- `agent/granite_container/container.py:1308-1359` — dev branch of `_route_pm_classification`: `_cycle_idle(dev)` → `dev_pty.write(dev_prompt)` → `text_bearing_count` baseline → `_cycle_idle(dev)` → `last_assistant_text(dev_transcript, baseline)` → write to PM PTY — confirmed present.
- `granite_classifier.py` `classify_pm_prefix` regex `^\[/(dev|user|complete)\]\s*$` — confirmed present.
- `.claude/commands/granite/prime-pm-role.md:15` — explicitly forbids PM custom tools; `:25-30` — prefix-token output contract — confirmed present.

**Active plans in `docs/plans/` overlapping this area:** `granite_lossless_checkpoint_resume.md` (#1721, status: Ready). It touches the **same** `Container` dev-shuttle area but for an orthogonal concern (lossless checkpoint/resume of granite sessions). No goal conflict, but both modify the dev-relay path — **coordination note:** whichever merges second rebases onto the other; the `BuilderHarness` extraction should preserve the transcript-cursor surfaces #1721 relies on. See Risks.

**Notes:** This plan introduces a seam that does not exist yet; there is no stale premise to correct. The Pi interface facts below were validated by live spikes (see Spike Results), not assumed.

## Prior Art

No prior issues or merged PRs found for pluggable/multi-harness builders (`gh issue list --state closed --search "pluggable builder harness pi codex"` and `gh pr list --state merged --search "builder harness abstraction granite"` both empty). This is a greenfield concept for the granite system. Relevant context:

- **#1681** — zero-LLM shuttle: removed the `summarize_for_pm`/`extract_dev_prompt` ollama calls, making the Dev→PM relay verbatim. The `BuilderHarness.run_turn` contract inherits this: it returns the builder's final text verbatim.
- **#1647** — wrap-up guard: the completion safety net. Unchanged by this work, but the guard's `_last_dev_report` seed must keep working when the last turn ran on a non-claude builder.
- **#1692** — persona priming moved out of `PairSpawnSpec` into slash-command priming at spawn. The Pi builder needs an equivalent priming mechanism that is *not* a slash command (see Technical Approach).
- **#1721** — granite lossless checkpoint resume (active, overlapping — see Freshness Check).

## Research

External research (WebSearch) plus live CLI spikes against the installed `pi` (v0.67.68).

**Queries used:**
- "Pi CLI AI coding assistant --print --mode json rpc non-interactive automation"

**Key findings:**
- **Pi is `@mariozechner/pi-coding-agent`** (repos: `badlogic/pi-mono`, `earendil-works/pi`; site `pi.dev`). It exposes four modes: interactive, **print/json**, **RPC**, and an SDK. Non-interactive modes (`-p`, `--mode json`, `--mode rpc`) **do not show a trust prompt** — confirming no startup-parser equivalent is needed for a Pi builder. (Source: npmjs.com/package/@mariozechner/pi-coding-agent, pi.dev, github.com/badlogic/pi-mono)
- **Print/JSON mode** (`pi -p --mode json "prompt"`): sends one message, streams an NDJSON event stream to stdout, exits. This is the simplest builder interface and the basis for the PoC. (Source: github.com/badlogic/pi-mono/.../coding-agent/README.md)
- **RPC mode** (`pi --mode rpc`): a long-lived process speaking **LF-delimited JSONL** over stdin/stdout, with commands `prompt`, `steer`, `follow-up`, `abort`, `get-state`, `compact` and streaming events. This is the natural **multi-turn persistent builder** interface — and `steer`/`follow-up` map directly onto granite's existing steering inbox. Documented at github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md. **This supersedes the earlier `--session`-subprocess-per-turn idea for multi-turn** — RPC is the better fast-follow path.

**How it informs the plan:** PoC builds on `-p --mode json` (single-turn, stateless, trivially testable). The multi-turn fast-follow is `--mode rpc`, not subprocess-per-turn — noted in No-Gos as a separate slug so the PoC stays small but the right long-term shape is recorded.

## Spike Results

### spike-1: Pi CLI surface and non-interactive modes
- **Assumption**: "Pi has a non-interactive mode that avoids the PTY idle-scraping problem."
- **Method**: code-read (`pi --help`) + web-research.
- **Finding**: Confirmed. `--print, -p` (non-interactive, process-and-exit), `--mode text|json|rpc`, `--session <path>`/`--continue`/`--resume` (session persistence), `--append-system-prompt <text|@file>` (priming via flag), `--skill <path>` (skill loading), `--tools read,bash,edit,write,grep,find,ls`, `--provider`/`--model`. Pi's flags map ~1:1 onto the five claude coupling points.
- **Confidence**: high
- **Impact on plan**: The Pi builder is a `subprocess`, not a PTY. Startup parser and idle heuristic are eliminated for this builder; priming is a flag, not a slash command.

### spike-2: Pi `--mode json` response envelope
- **Assumption**: "Pi's structured output gives a clean, deterministic way to extract the final assistant text."
- **Method**: prototype — ran `pi -p --mode json --no-tools --no-session --thinking off "Reply with exactly: PONG"`.
- **Finding**: Confirmed. Output is an **NDJSON event stream** (`session`, `agent_start`, `turn_start`, `message_start/update/end`, `text_start/delta/end`, `thinking_*`, `turn_end`, `agent_end`). The terminal **`agent_end`** event carries the full `messages` array; the final assistant message's `content[]` contains entries of `type:"text"` (the answer) and `type:"thinking"` (reasoning). Usage/cost ride in each assistant message's `usage` field. Extraction: parse NDJSON, take the final assistant message, concatenate `type=="text"` content, drop `type=="thinking"`.
- **Confidence**: high
- **Impact on plan**: Response extraction is a small deterministic NDJSON parser, unit-testable against a captured fixture. No ANSI scraping, no `text_bearing_count` baseline, no transcript tailing.

### spike-3: Pi default model resolution
- **Assumption**: "`--provider google` selects a Google model."
- **Method**: prototype (observed `provider`/`model` in the spike-2 envelope).
- **Finding**: **Surprise.** Despite the default `--provider google`, the run resolved to **local `ollama/gemma4:31b`** (the machine's configured default). Cost fields were all zero (local).
- **Confidence**: high
- **Impact on plan**: The adapter **must pass explicit `--provider` and `--model`** for the cloud demo; it cannot rely on Pi's effective default. Tests intentionally use local ollama (free); the demo uses an explicit cloud model.

## Data Flow

1. **Entry point**: User message → bridge → worker → `session_executor` constructs `BridgeAdapter` → `Container.run()` steady-state loop.
2. **PM turn**: PM TUI emits a turn; `_cycle_idle(pm)` → `last_assistant_text(pm_transcript)` → `classify_pm_prefix(pm_text)`.
3. **Classification** (changed): regex now yields `(destination, harness, payload)`. For `[/dev:pi]`, `destination="dev"`, `harness="pi"`.
4. **Builder dispatch** (changed): `_route_pm_classification` resolves a `BuilderHarness` for `harness` (default `claude`) and calls `builder.run_turn(payload)` instead of inlining the PTY+transcript logic.
   - `PtyClaudeBuilder.run_turn` = today's `_cycle_idle(dev)` → write → baseline → `_cycle_idle(dev)` → `last_assistant_text`.
   - `PiSubprocessBuilder.run_turn` = `subprocess.run(["pi","-p","--mode","json","--append-system-prompt","@<persona>","--provider",P,"--model",M,"--tools","read,bash,edit,write"], cwd=worktree, input=payload)` → parse NDJSON → final assistant text.
5. **Relay back**: builder's returned text written to PM PTY (unchanged); captured as `_last_dev_report` for the wrap-up guard (unchanged).
6. **Output**: PM eventually emits `[/user]`/`[/complete]` → delivered to the user via the existing callbacks (unchanged).

## Architectural Impact

- **New dependencies**: `pi` CLI (already installed, v0.67.68) as a runtime dependency for the Pi builder path. No new Python packages (stdlib `subprocess`/`json`).
- **Interface changes**: new `BuilderHarness` protocol; `classify_pm_prefix` return shape gains a `harness` field (additive — default `None`/`"claude"`); `_route_pm_classification` dev branch delegates to a builder instance.
- **Coupling**: **decreases** container↔claude coupling — the container no longer hardcodes the claude PTY for dev turns; it talks to an abstraction.
- **Data ownership**: unchanged. Transcripts, session events, and the wrap-up guard stay owned by the container.
- **Reversibility**: high. `PtyClaudeBuilder` is behavior-preserving; bare `[/dev]` keeps the exact current path. Removing the Pi builder is deleting one class + one persona file + the harness-token branch.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment on the protocol shape; confirm PoC-vs-fast-follow boundary)
- Review rounds: 1 (the `PtyClaudeBuilder` extraction must be reviewed for behavior preservation)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `pi` CLI installed | `command -v pi` | Pi builder subprocess target |
| Pi version ≥ 0.67 | `pi --version` | `--mode json` NDJSON envelope shape |
| Local ollama model for tests | `ollama list \| grep -q gemma4:31b && echo ok` | Free model for unit/integration tests |
| Cloud key for demo (explicit) | `python -c "from dotenv import dotenv_values; v=dotenv_values('.env'); assert v.get('ANTHROPIC_API_KEY') or v.get('GEMINI_API_KEY')"` | Real model for end-to-end demo |

Run all checks: `python scripts/check_prerequisites.py docs/plans/pluggable_builder_harness_pi.md`

## Solution

### Key Elements

- **`BuilderHarness` protocol** (`agent/granite_container/builder.py`): the single seam the container uses for builder turns. Minimal contract: `name`, `prepare(spec)` (idempotent setup/priming), `run_turn(prompt: str) -> str` (do the work, return final text verbatim), `close()`.
- **`PtyClaudeBuilder`**: wraps the existing dev-PTY + JSONL logic behind the protocol. Pure extraction — no behavior change. Owns the dev `PTYDriver`, the `_cycle_idle` cadence, and `last_assistant_text` baseline reads.
- **`PiSubprocessBuilder`**: runs Pi as a one-shot subprocess (`-p --mode json`) in the session worktree, primed via `--append-system-prompt @<persona-file>`, with an explicit `--provider/--model`. Parses the NDJSON envelope to the final assistant text.
- **Harness-aware classifier**: `classify_pm_prefix` parses `[/dev:<harness>]` (and plain `[/dev]`), returning the selected harness.
- **Builder registry / resolver**: maps a harness name → `BuilderHarness` instance, with `claude` as the default and a clear error for an unknown harness (routed back to PM as a compliance nudge, not a crash).
- **Pi dev persona file** (`config/personas/granite/pi_dev_rails.md` or similar): the rails + dev-persona text Pi receives via `--append-system-prompt`, since it cannot run the `/granite:prime-dev-role` slash command. v1 is a faithful translation of the claude Dev rails (no-push-to-main, worktree discipline, narrow tests, report in natural language).

### Flow

PM turn → `classify_pm_prefix` returns `(dev, "pi", payload)` → resolver returns `PiSubprocessBuilder` → `builder.run_turn(payload)` runs `pi -p --mode json` in the worktree → NDJSON parsed to final text → text written to PM PTY → PM continues → `[/user]`/`[/complete]` → user.

### Technical Approach

- **Extraction first, additively.** Land `BuilderHarness` + `PtyClaudeBuilder` as a behavior-preserving refactor in its own commit, fully covered by regression tests, *before* adding Pi. This keeps the risky change (claude path) separable from the new feature (Pi).
- **Classifier change is additive.** Regex becomes `^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`; the harness group defaults to `None` → treated as `claude`. Existing `[/dev]` callers and all current tests are unaffected.
- **Pi priming via flag, not slash.** `--append-system-prompt @<persona-file>` on every invocation (stateless priming is fine for single-turn). The persona file is the "skill translation" deliverable.
- **Pi runs in the worktree.** `cwd=working_dir` (the same session worktree the claude Dev uses) so Pi's `bash/edit/write` tools obey the existing filesystem isolation. `--tools read,bash,edit,write` only.
- **Explicit model.** Adapter passes `--provider`/`--model` from config; tests pass the local ollama model, the demo passes a cloud model. Never rely on Pi's default (spike-3).
- **NDJSON parser is the only fragile surface.** Implement it as a small pure function (`parse_pi_final_text(stream: str) -> str`) with a captured real envelope as a fixture; handle: no `agent_end` (timeout/crash → return `""` so the existing `DEV_REPORT_UNAVAILABLE` fallback fires), multiple text blocks (concatenate), thinking-only output (return `""`).
- **Reuse the existing fallbacks.** When `run_turn` returns empty, the container already increments `transcript_fallback_count` and writes `DEV_REPORT_UNAVAILABLE` — the Pi builder rides those paths unchanged.
- **Driver verification is load-bearing.** A non-claude builder cannot be trusted on rails compliance the way slash-priming implies. For the PoC, the PM persona is instructed to re-read the diff after a `[/dev:pi]` turn before reporting `[/complete]`. Full adversarial verification is a fast-follow.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `PiSubprocessBuilder.run_turn` must not swallow subprocess failures silently: a non-zero exit, timeout (`subprocess.TimeoutExpired`), or unparseable stream must log `logger.warning` and return `""` (triggering the container's existing `DEV_REPORT_UNAVAILABLE` path). Test asserts the log + empty return, not a swallow.
- [ ] The builder resolver's unknown-harness branch must route a compliance nudge back to PM (observable), not raise. Test asserts the nudge path.
- [ ] No new bare `except Exception: pass` introduced; the extraction preserves existing handlers in the dev branch verbatim.

### Empty/Invalid Input Handling
- [ ] `parse_pi_final_text("")`, a stream with no `agent_end`, and a thinking-only final message each return `""` (not None, not a crash). Unit tests for each.
- [ ] Empty `[/dev:pi]` payload follows the existing empty-`[/dev]` compliance-nudge path (no subprocess spawned).
- [ ] Empty builder output does not trigger a silent loop — it writes `DEV_REPORT_UNAVAILABLE` to PM and continues, same as the claude path on empty transcript.

### Error State Rendering
- [ ] When Pi fails entirely, the user still gets a message via the wrap-up guard / `DEV_REPORT_UNAVAILABLE` seed — test the end-to-end "Pi exits non-zero" path delivers *something* to the user callback.
- [ ] Pi `stderr` on failure is captured into the log line, not dropped.

## Test Impact

- [ ] `tests/unit/test_granite_classifier.py` (or equivalent) — UPDATE: add cases for `[/dev:pi]`, `[/dev:claude]`, `[/dev]` (default), and `[/dev:unknown]`; assert the new `harness` field and that existing bare-token cases still classify identically.
- [ ] Tests asserting `_route_pm_classification` dev-branch behavior — UPDATE: re-point at the `BuilderHarness` seam; assert `PtyClaudeBuilder.run_turn` produces byte-identical relay behavior to the pre-refactor path (regression guard).
- [ ] `tests/.../test_container*.py` two-PTY/loop tests — UPDATE only if they construct the dev relay directly; the goal is no behavioral diff for the claude path.
- [ ] New: `tests/unit/test_pi_builder.py` — `parse_pi_final_text` against the captured fixture + edge cases; `PiSubprocessBuilder` with a mocked subprocess and a real-envelope fixture.
- [ ] New: `tests/integration/test_pi_builder_e2e.py` — real `pi -p --mode json` against local ollama in a temp worktree, asserting a file write happens and the final text is relayed. Marked appropriately so CI runs it on the local model only.

If any current granite test asserts the literal absence of a `harness` concept, that assertion is updated to allow the additive field.

## Rabbit Holes

- **Porting `/do-*` skills to Pi.** The PoC Dev works with native `read/bash/edit/write`. Do not attempt to recreate the full SDLC skill suite inside Pi — that is a large separate effort and unnecessary to prove the seam.
- **Multi-turn / RPC mode now.** `--mode rpc` is the right long-term multi-turn interface, but standing up the persistent RPC process, lifecycle, and steering wiring is its own slug. Single-turn `-p` proves the abstraction.
- **Generalizing the protocol to N harnesses up front.** Design `BuilderHarness` against exactly two implementations (claude, pi). Do not speculatively add hooks for Codex/others until a second non-PTY harness actually lands.
- **PTYPool changes for Pi.** Pi is a subprocess with no PTY slot; do not entangle it with `PTYPool`'s pair lifecycle. The pool stays claude-only; the Pi builder spawns/reaps its own process.
- **Rewriting the wrap-up guard.** It already handles empty dev reports. Leave it; just make sure the Pi path feeds it the same `_last_dev_report` surface.

## Risks

### Risk 1: `PtyClaudeBuilder` extraction changes claude behavior
**Impact:** Every existing granite session regresses — the highest-blast-radius part of this work.
**Mitigation:** Extraction lands as its own commit with regression tests asserting byte-identical relay for the claude path; bare `[/dev]` exercises the exact pre-refactor code path; code-review round dedicated to behavior preservation.

### Risk 2: Pi rails non-compliance (pushes to main, escapes worktree, weak output)
**Impact:** A non-claude builder may ignore rails, producing unsafe or low-quality changes.
**Mitigation:** `cwd=worktree` + `--tools read,bash,edit,write` bound the filesystem surface; the Pi persona file restates the rails; the PM is instructed to re-read the diff before completing a Pi turn (driver verification). Cloud-model demo only after the local plumbing is proven.

### Risk 3: NDJSON envelope shape drifts across Pi versions
**Impact:** `parse_pi_final_text` breaks on a Pi upgrade.
**Mitigation:** Parser keys on stable event names (`agent_end`, `type:"text"`) with a no-`agent_end` → `""` fallback (degrades to `DEV_REPORT_UNAVAILABLE`, never crashes); a prerequisite pins Pi ≥ 0.67; the fixture documents the validated shape.

### Risk 4: Merge collision with #1721 (lossless resume)
**Impact:** Both edit the Container dev-relay; second-to-merge rebases.
**Mitigation:** Keep the `BuilderHarness` extraction surgical and preserve the transcript-cursor/`last_assistant_text` surfaces #1721 depends on; coordinate merge order with the #1721 owner; the extraction is mechanical enough to rebase cleanly.

## Race Conditions

### Race 1: Pi subprocess outlives its turn / orphan on container teardown
**Location:** `PiSubprocessBuilder.run_turn` / `close`
**Trigger:** Container exits (hang watchdog, exception) while a Pi subprocess is mid-run.
**Data prerequisite:** The subprocess handle must be tracked so it can be killed on `close()`.
**State prerequisite:** No second Pi turn starts for the same builder before the first returns (the container loop is single-threaded, so this holds).
**Mitigation:** `subprocess.run(..., timeout=CYCLE_IDLE_TIMEOUT_S)` bounds each turn; `close()` kills any live child; teardown mirrors the existing `pkill` orphan-reaping pattern in `pty_driver.close`.

### Race 2: Pi `bash/edit/write` racing the claude Dev in the same worktree
**Location:** worktree filesystem
**Trigger:** Only if both builders ran concurrently in one session — they do not (single-threaded loop, one builder per `[/dev*]` turn).
**Data prerequisite:** N/A — serialized by the loop.
**State prerequisite:** One active builder turn at a time.
**Mitigation:** The container loop already serializes dev turns; document the invariant. No concurrent-builder support in scope.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Multi-turn persistent Pi builder via `--mode rpc` (steer/follow-up/get-state). The right long-term interface, but a distinct effort; the PoC is single-turn `-p`. *(Tracking issue to be filed as a fast-follow once the seam lands; not promised as done here.)*
- [SEPARATE-SLUG] Adversarial output verification of builder results by the driver (beyond the PoC's "PM re-reads the diff"). Filed as a fast-follow.
- [SEPARATE-SLUG] Additional harnesses (Codex, etc.). The protocol is designed against claude+pi only; a third harness is future work.
- [SEPARATE-SLUG] Porting the `/do-*` SDLC skill suite to Pi. Out of scope; PoC uses native Pi tools.

## Update System

- **`pi` CLI propagation:** Pi is installed via npm (`@mariozechner/pi-coding-agent`) under nvm, **not** currently managed by `/update`. The Pi builder makes Pi a runtime dependency on any machine that runs granite with `[/dev:pi]`. Add a check (and optional install) for `pi` to `scripts/update/verify.py` (verify-only: warn if absent, like the existing `gws` ladder) and document it as a soft prerequisite. Do **not** hard-gate the bridge/worker on Pi — bare `[/dev]`/claude must keep working without Pi installed.
- **New config file:** the Pi dev persona file (`config/personas/granite/pi_dev_rails.md`) is committed to the repo, so it propagates via `git pull` automatically — no extra sync wiring.
- **No new secrets** beyond the already-present provider keys; the cloud demo uses existing `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`.
- Migration for existing installs: none — the feature is additive and dormant unless the PM emits `[/dev:pi]`.

## Agent Integration

- **No new MCP server or `.mcp.json` change.** This is entirely inside the granite container's builder path, which the worker already drives. The agent does not invoke the builder via a tool; the PM persona selects it by emitting a routing token.
- **Persona change is the integration surface:** `prime-pm-role.md` is updated to teach the PM the `[/dev:<harness>]` selector and when to choose `pi` vs `claude`. This is how the running agent "reaches" the new capability.
- **Bridge:** no change — the bridge has no SDLC/builder awareness (per architecture).
- **Integration test** verifies the end-to-end agent path: a PM turn emitting `[/dev:pi]` results in Pi executing and the result relayed (the e2e test under Test Impact covers this at the container level).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pluggable-builder-harness.md` — the `BuilderHarness` seam, the two implementations, the `[/dev:<harness>]` selector, the Pi NDJSON envelope, model policy, and the RPC fast-follow.
- [ ] Update `docs/features/granite-pty-production.md` — note that the dev relay now goes through `BuilderHarness`; bare `[/dev]` is unchanged.
- [ ] Update `docs/features/granite-interactive-tui.md` — classification taxonomy gains the optional harness selector.
- [ ] Update `docs/features/pty-driver.md` — clarify it is the claude builder's substrate, not the only builder substrate; **reconcile the README "PoC" vs header "Production" status mismatch** (`docs/features/README.md:125`).
- [ ] Add an entry to `docs/features/README.md` index for the new feature doc.

### Inline Documentation
- [ ] Docstrings on `BuilderHarness`, both implementations, and `parse_pi_final_text` (document the NDJSON contract + fallbacks).
- [ ] Comment the classifier regex change explaining the optional harness group and default.

## Success Criteria

- [ ] `BuilderHarness` protocol exists; `PtyClaudeBuilder` is the claude implementation; the container dev branch delegates to it.
- [ ] Regression tests prove the claude path is behavior-identical to pre-refactor (byte-identical relay on representative turns).
- [ ] `classify_pm_prefix` parses `[/dev:pi]`/`[/dev:claude]`/`[/dev]` correctly; unknown harness routes a compliance nudge, not a crash.
- [ ] `PiSubprocessBuilder` executes a task via `pi -p --mode json` in the worktree and relays the final text; `parse_pi_final_text` unit-tested against a real envelope fixture + edge cases.
- [ ] **Demo:** a real task routed `[/dev:pi]` on a cloud model produces a committed change in the worktree and a user-facing result through normal granite flow (proof artifact: transcript + diff).
- [ ] Failure paths covered: Pi non-zero exit / timeout / empty output each degrade to `DEV_REPORT_UNAVAILABLE` and still deliver a user message.
- [ ] `prime-pm-role.md` teaches the selector; `pi_dev_rails.md` persona file committed.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`), including the README status reconciliation.
- [ ] grep confirms `_route_pm_classification` references the `BuilderHarness` resolver (Agent Integration: "container calls builder seam").

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (harness-seam)**
  - Name: `seam-builder`
  - Role: Extract `BuilderHarness` + `PtyClaudeBuilder` (behavior-preserving) and the harness-aware classifier.
  - Agent Type: builder
  - Resume: true

- **Builder (pi-adapter)**
  - Name: `pi-builder`
  - Role: Implement `PiSubprocessBuilder` + `parse_pi_final_text` + Pi persona file + resolver wiring.
  - Agent Type: builder
  - Resume: true

- **Validator (seam)**
  - Name: `seam-validator`
  - Role: Verify claude-path behavior preservation + classifier correctness.
  - Agent Type: validator
  - Resume: true

- **Test engineer (pi)**
  - Name: `pi-tester`
  - Role: Unit + integration (local-ollama e2e) tests for the Pi builder.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `harness-doc`
  - Role: Feature doc + granite doc updates + README reconciliation.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types
(standard roster — builder, validator, test-engineer, documentarian)

## Step by Step Tasks

### 1. Extract BuilderHarness seam + PtyClaudeBuilder
- **Task ID**: build-seam
- **Depends On**: none
- **Validates**: existing granite container/classifier tests (must stay green), new `tests/unit/test_granite_classifier.py` harness cases
- **Informed By**: spike-1, spike-2
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/granite_container/builder.py` with the `BuilderHarness` protocol.
- Move the dev-branch PTY+JSONL logic into `PtyClaudeBuilder` with no behavior change.
- Extend `classify_pm_prefix` to parse optional `[/dev:<harness>]`; default → claude.
- Wire `_route_pm_classification` to resolve and call the builder seam.

### 2. Validate seam behavior preservation
- **Task ID**: validate-seam
- **Depends On**: build-seam
- **Assigned To**: seam-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert claude relay is byte-identical to pre-refactor on representative turns.
- Assert classifier cases (pi/claude/default/unknown) behave per spec.

### 3. Implement Pi builder
- **Task ID**: build-pi
- **Depends On**: build-seam
- **Validates**: `tests/unit/test_pi_builder.py`
- **Informed By**: spike-2 (NDJSON envelope), spike-3 (explicit model required)
- **Assigned To**: pi-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `PiSubprocessBuilder.run_turn` (subprocess, `-p --mode json`, `cwd=worktree`, explicit provider/model, timeout).
- Implement `parse_pi_final_text` with fallbacks (no `agent_end` → `""`).
- Add the Pi dev persona file and resolver registration (`pi` → builder).
- Update `prime-pm-role.md` with the `[/dev:<harness>]` selector guidance.

### 4. Test Pi builder (unit + e2e on local ollama)
- **Task ID**: test-pi
- **Depends On**: build-pi
- **Assigned To**: pi-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit-test `parse_pi_final_text` against a captured fixture + edge cases.
- Integration-test real `pi -p --mode json` (local ollama) in a temp worktree: assert a file write + relayed text.
- Cover failure paths (non-zero exit, timeout, empty output → `DEV_REPORT_UNAVAILABLE`).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-seam, test-pi
- **Assigned To**: harness-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pluggable-builder-harness.md`; update granite docs + README index; reconcile the PTY Driver status mismatch.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-seam, test-pi, document-feature
- **Assigned To**: seam-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands; confirm success criteria including the cloud demo proof artifact.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Classifier knows harness | `python -c "from agent.granite_container.granite_classifier import classify_pm_prefix as c; r=c('[/dev:pi]\nbuild it'); print(getattr(r,'harness',None))"` | output contains `pi` |
| Default harness preserved | `python -c "from agent.granite_container.granite_classifier import classify_pm_prefix as c; r=c('[/dev]\nbuild it'); print(r.destination, getattr(r,'harness',None) or 'claude')"` | output contains `dev claude` |
| Pi parser importable | `python -c "from agent.granite_container.builder import parse_pi_final_text; print(parse_pi_final_text(''))"` | exit code 0 |
| Pi available | `command -v pi` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Pi persona file location/format:** `config/personas/granite/pi_dev_rails.md` vs reusing/transcluding the existing `_prime-rails.md` content. Preference for a single source of rails truth shared between claude and Pi, or an independent Pi-tuned file?
2. **Cloud demo model:** which explicit `--provider/--model` for the end-to-end demo — anthropic claude (apples-to-apples vs the claude builder) or google gemini (proves genuine cross-vendor)? Default assumption: anthropic for the demo, document the gemini path.
3. **`/update` Pi handling:** verify-only warning (recommended, matches the `gws` ladder) vs. auto-install `@mariozechner/pi-coding-agent` on machines that run granite. Default assumption: verify-only warning.
