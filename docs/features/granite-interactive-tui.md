# Granite Operator: Interactive TUI Session Runner

**Status:** Production. The granite interactive-TUI container is the
execution path for bridge-originated sessions under the standalone worker.
It drives a PTY-backed interactive TUI rather than the headless `-p`
substrate. The headless harness (`agent/sdk_client.py`) remains in place
alongside it; this container lives
at module path `agent/granite_container/`. (Historical origin: the
container began as the #1546 PoC and was cut over to production in
#1572 / #1612.)

## Architecture (3 layers)

```
Bridge → Container → Granite + PM/Dev
```

- **Bridge** originates the sessions this container runs. The worker drives
  the container; user-address output is routed back to the bridge.
- **Container** (`agent/granite_container/container.py`) owns two PTYs and the
  steady-state loop. Per-turn trace + exit reason are serialized to a
  results JSON callers can render.
- **Granite + PM/Dev**: the operator is `granite4.1:3b` (local, via
  ollama). PM and Dev are real `claude` sessions under PTY, with the user
  message passed as `$ARGUMENTS` to a persona-priming slash command.

## 10 invariants (from issue #1546)

1. **Substrate is the TUI** — never `claude -p`. The container spawns
   `claude` interactively (`--permission-mode bypassPermissions`).
2. **Max OAuth path** — `ANTHROPIC_API_KEY=""` is set on the subprocess env
   (mirroring the auth setup in `agent/sdk_client.py`).
3. **Resume UUID** — the on-exit hint is environment-gated (C3). Resume
   acceptance tests run only in a model-reachable env.
4. **Persona priming** — `.claude/commands/granite/prime-{pm,dev}-role.md`
   prime the two roles via the TUI's slash-command mechanism (F1).
5. **Fresh context per turn** — granite's classifier is stateless; no
   `HISTORY_KEEP_LAST_N` knob.
6. **No `send_to_dev` tool on PM** — routing goes through granite's
   classifier, not a custom tool.
7. **No `reply_to_user` tool on PM** — user-address text is a regular PM
   output, classified by granite.
8. **Event-bridge shape** — PTY text is wrapped as
   `[{"type": "pm_output" | "dev_output", "text": <tail>}]` at the
   container's boundary. Granite consumes the same list shape
   `agent/granite_router.py:276` consumes today.
9. **Idempotent teardown** — every PTY's `close(force=True)` runs in a
   `try/finally`; on exit `_close_pair_and_reap()` reaps each self-spawned
   PTY's process group via `os.killpg(getpgid(pid), SIGTERM→SIGKILL)`.
   Pool-owned pairs are skipped (the pool owns their lifecycle). The
   machine-wide `pkill -f "claude --permission-mode bypassPermissions"`
   fallback was removed in #1816 because it could kill bystander `claude`
   sessions — see [Worker Fault Containment](worker-fault-containment.md).
10. **Two-PTY coordination is the early risk** — the container's loop is
    single-threaded; reads from both PTYs are not interleaved within a
    single tick. The `await_idle(pty)` invariant: the container only
    writes to a PTY that is in the idle state.

## Persona-priming flow (F1-F4)

The PM and Dev personas are primed at TUI layer (F1) by the slash
commands under `.claude/commands/granite/`. The body of each slash
command is invisible to the operator (F4); the only substrate signal is
"did the model respond?" (F2 substitution). Multi-word args are preserved
as a single `$ARGUMENTS` string (F3).

The PM persona body instructs PM to begin every output with one of three
literal prefix tokens on a line of its own:

- `[/dev]` — followed by the developer instruction (Dev-address). Optionally
  takes a `:<harness>` suffix to select the builder: `[/dev:pi]` routes to
  `PiSubprocessBuilder`; `[/dev:claude]` or bare `[/dev]` route to
  `PtyClaudeBuilder`. See [Pluggable Builder Harness](pluggable-builder-harness.md)
  for the full task-shape rubric the PM uses to choose.
- `[/user]` — followed by the user-facing message
- `[/complete]` — followed by a one-sentence completion summary

The Dev persona body instructs Dev to wait for the PM and report
naturally; Dev's final assistant message each turn is forwarded to PM
**verbatim** (read from the JSONL transcript), not summarized.

## Granite classification taxonomy (zero-LLM shuttle)

As of #1681, the granite PTY operator is a **zero-LLM shuttle** on the
PM↔Dev channel: it classifies by deterministic regex and moves the
sessions' own authored text between them, doing no LLM rewriting. The
two former ollama "translation" calls (`extract_dev_prompt`,
`summarize_for_pm`) and their tool schemas are deleted.

`agent/granite_container/granite_classifier.py` now ships a single
routing tool:

| Tool | Caller | Type | Purpose |
|------|--------|------|---------|
| `classify_pm_prefix` | container | deterministic regex | Parse PM's first non-empty line for the `[/dev]/[/user]/[/complete]` convention, including the optional `:<harness>` suffix. **Not** an LLM call. |

The classifier accepts the optional harness suffix on `[/dev]`:
`^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$` (strict) and the
equivalent fallback form for mid-line tolerance. `ClassificationResult` carries a
`harness: str | None` field; `None` or `"claude"` default to `PtyClaudeBuilder`.

- **PM→Dev:** the verbatim text after `[/dev]` (`classification.payload`)
  is written directly to Dev (or passed to the selected builder). No rewrite.
- **Dev→PM:** `last_assistant_text()` (`agent/granite_container/transcript_tailer.py`)
  reads Dev's final authored assistant message from the JSONL transcript
  and writes it to PM verbatim. No summary. A content-identity freshness
  baseline (count of text-bearing assistant entries) ensures the current
  turn — not a stale prior turn — is forwarded; the deterministic
  hook-driven fix is followup #1688.

`ensure_granite_model` and its worker-startup gate are retained — granite
remains required for the separate **classification** role
(`OLLAMA_CLASSIFIER_MODEL`), independent of this now-zero-LLM routing role.

The 3 judgment tools from the earlier granite-agent-loop (`handle_choice`,
`probe_session`, `signal_done`) are dropped. That earlier results
doc at `docs/plans/completed/granite-agent-loop-poc-results.md` shows
those tools were validated by synthetic smoke tests only, not in a
live 4-turn run. Routing judgment calls to PM (a real Claude
session) is the right level of abstraction; granite is a
shuttle, not a judge.

## Steady-state loop

The container's main loop is single-threaded and processes one
PM→granite→Dev→granite→PM cycle per tick:

```
1. await_idle(pm_pty)         # glyph + bar + content-floor (C5)
2. classify_pm_prefix(pm_buf) # regex parse on first non-empty line
3. branch on destination:
   - complete: emit turn record, exit
   - user:     emit turn record, continue (PM may have more to say)
   - dev:      await_idle(dev_pty), then
                 write(dev_pty, classification.payload)  # verbatim, \r terminator
                 baseline = text_bearing_count(dev_transcript)
                 await_idle(dev_pty)         # wait for Dev's response
                 dev_text = last_assistant_text(dev_transcript, baseline)  # verbatim from JSONL
                 await_idle(pm_pty)          # PM must be idle
                 write(pm_pty, dev_text + PM_TURN_CONTRACT_REMINDER)  # reminder on every handoff (#1719)
   - unknown:  compliance miss; log + continue
4. loop until destination == "complete" or max_turns reached
```

## Exit reasons

| `exit_reason` | Description | Anomaly? |
|---|---|---|
| `pm_complete` | PM emitted `[/complete]` | No |
| `pm_user` | PM emitted `[/user]` | No |
| `pm_max_turns` | Steady-state loop exhausted `max_turns` | No |
| `pm_floor_delivered` | Wrap-up guard delivered PM's non-empty but prefix-less last message directly (issue #1719) | No |
| `pm_no_user_message` | Wrap-up guard exhausted; PM produced no text; `OPERATOR_TERMINAL_MESSAGE` sent | Yes |
| `pm_hang` | PM did not reach idle within `CYCLE_IDLE_TIMEOUT_S` | Yes |
| `dev_hang` | Dev did not reach idle within `CYCLE_IDLE_TIMEOUT_S` | Yes |
| `startup_unresolved` | Neither PTY settled within `STARTUP_HARD_CEILING_S` | Yes |
| `exception` | Unhandled Python exception in the container loop | Yes |

"Anomaly" means `BridgeAdapter._maybe_publish_exit_anomaly` writes an
`exit_anomaly` event to `session_events` and logs at ERROR.

## PM feedback strings — compliance vs. wrap-up

The container writes two distinct feedback strings to the PM PTY; they serve
different purposes and must not be confused:

| Constant | Written when | Purpose |
|---|---|---|
| `PM_COMPLIANCE_NUDGE` | PM produces output with no recognized prefix token (`unknown` classification) | Re-prompt PM to follow the `[/dev]/[/user]/[/complete]` convention on its next turn |
| `PM_WRAPUP_PROMPT` | Wrap-up guard fires — run exiting but no user-facing message delivered yet | Seed PM with the Dev's final report and instruct it to produce a `[/user]` or `[/complete]` summary for the human |

`PM_COMPLIANCE_NUDGE` fires inside the steady-state loop and does not
consume a `max_turns` slot. `PM_WRAPUP_PROMPT` fires post-loop, is capped at
`MAX_WRAPUP_ATTEMPTS = 1`. The wrap-up guard then:
1. If PM produces a **non-empty but prefix-less** response: delivers it directly
   via `on_user_payload` with `exit_reason = "pm_floor_delivered"` (relaxed
   floor, issue #1719). Bypasses `_route_pm_classification` so no
   `PM_COMPLIANCE_NUDGE` is written to a PTY being torn down.
2. If PM is genuinely silent (empty transcript after all attempts): delivers
   the canned `OPERATOR_TERMINAL_MESSAGE` directly with
   `exit_reason = "pm_no_user_message"`.

The human always receives at least `OPERATOR_TERMINAL_MESSAGE`; the relaxed
floor means a real PM message is preferred when one exists.

## Cross-references

- Substrate driver: [`pty-driver.md`](pty-driver.md). C1-C5 substrate facts
  live there.
- Spike report: `docs/research/spikes/granite-tui-pty-spike.md` (v7, closed
  2026-06-03). C1-C5 + F1-F4 substrate facts are load-bearing inputs.
- Probe: `scripts/probe_slash_arguments.py`. F1-F4 persona-priming
  findings (model-side `$ARGUMENTS` substitution, slash-command layer
  parsing, multi-line message support).
- Originating plan + verdict: `docs/plans/completed/granite-interactive-tui-poc-results.md`
  (the historical results doc from the originating effort).
- Earlier granite-agent-loop PoC: deleted (superseded; source files removed in PR #1664). This doc is the source of truth for the live runner.
- Production cutover + bounded slot pool: [`granite-pty-production.md`](granite-pty-production.md)
  — the production wiring this container runs under (PRs #1572 / #1612).

## Out of scope (No-Gos)

- `AgentSession` schema change to store two `claude_session_uuid` fields
  (PM UUID + Dev UUID).
- Resume-UUID capture spike (#1552). The container exercises resume inside
  itself in a model-reachable env; #1552's findings are a corroborating
  reference, not a prerequisite.
- Cross-turn history accumulation in granite. Fresh context per turn is
  the default; a 1-line structured handoff field is a follow-on
  optimization.
- Model-per-role config at the runner level. The container hardcodes
  `claude --model <auto-pick> --permission-mode bypassPermissions`.
