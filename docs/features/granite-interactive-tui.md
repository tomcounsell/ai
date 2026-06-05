# Granite Operator: Interactive TUI PoC (issue #1546)

**Status:** Kernel validation PoC. Replaces the prior PoC's `-p`-driven substrate
with a PTY-driven interactive TUI. The headless harness baseline
(`agent/claude_session.py`, `agent/sdk_client.py`) is untouched; the PoC is
additive (new module path `agent/granite_container/`).

## Architecture (3 layers)

```
Bridge → Container → Granite + PM/Dev
```

- **Bridge** is *out of scope* for this PoC. User-address output is written
  to a results log; the production wiring is a follow-on issue.
- **Container** (`agent/granite_container/container.py`) owns two PTYs and the
  steady-state loop. Per-turn trace + exit reason are serialized to a
  results JSON the results doc renders.
- **Granite + PM/Dev**: the operator is `granite4.1:3b` (local, via
  ollama). PM and Dev are real `claude` sessions under PTY, with the user
  message passed as `$ARGUMENTS` to a persona-priming slash command.

## 10 invariants (from issue #1546)

1. **Substrate is the TUI** — never `claude -p`. The container spawns
   `claude` interactively (`--permission-mode bypassPermissions`).
2. **Max OAuth path** — `ANTHROPIC_API_KEY=""` is set on the subprocess env
   (mirroring `agent/claude_session.py:90-101`).
3. **Resume UUID** — the on-exit hint is environment-gated (C3). Resume
   acceptance tests run only in a model-reachable env.
4. **Persona priming** — `.claude/commands/granite-poc/prime-{pm,dev}-role.md`
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
   `try/finally`; a `pkill -f "claude --permission-mode bypassPermissions"`
   fallback runs on exit.
10. **Two-PTY coordination is the early risk** — the container's loop is
    single-threaded; reads from both PTYs are not interleaved within a
    single tick. The `await_idle(pty)` invariant: the container only
    writes to a PTY that is in the idle state.

## Persona-priming flow (F1-F4)

The PM and Dev personas are primed at TUI layer (F1) by the slash
commands under `.claude/commands/granite-poc/`. The body of each slash
command is invisible to the operator (F4); the only substrate signal is
"did the model respond?" (F2 substitution). Multi-word args are preserved
as a single `$ARGUMENTS` string (F3).

The PM persona body instructs PM to begin every output with one of three
literal prefix tokens on a line of its own:

- `[/dev]` — followed by the developer instruction (Dev-address)
- `[/user]` — followed by the user-facing message
- `[/complete]` — followed by a one-sentence completion summary

The Dev persona body instructs Dev to wait for the PM and report
naturally; Dev's output is summarized by granite, not classified.

## Granite classification + translation taxonomy

The new `agent/granite_container/granite_classifier.py` ships 3 tools (down
from 5 in the prior PoC):

| Tool | Caller | Type | Purpose |
|------|--------|------|---------|
| `classify_pm_prefix` | container | deterministic regex | Parse PM's first non-empty line for the `[/dev]/[/user]/[/complete]` convention. **Not** an LLM call. |
| `extract_dev_prompt` | container | ollama | Translate PM's tail into a developer instruction. |
| `summarize_for_pm` | container | ollama | Summarize Dev's output for PM's next turn. |

The classification is a parse, not an LLM call (per Q6 disposition). The
two translation calls remain LLM calls because the translation quality
is what granite adds.

The 3 judgment tools from the prior PoC (`handle_choice`,
`probe_session`, `signal_done`) are dropped. The prior PoC's results
doc at `docs/plans/completed/granite-agent-loop-poc-results.md` shows
those tools were validated by synthetic smoke tests only, not in a
live 4-turn run. Routing judgment calls to PM (a real Claude
session) is the right level of abstraction; granite is a
translator, not a judge.

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
                 extract_dev_prompt(pm_buf)  # ollama
                 write(dev_pty, dev_prompt)   # \r terminator
                 await_idle(dev_pty)         # wait for Dev's response
                 summarize_for_pm(dev_buf)   # ollama
                 await_idle(pm_pty)          # PM must be idle
                 write(pm_pty, summary)      # \r terminator
   - unknown:  compliance miss; log + continue
4. loop until destination == "complete" or max_turns reached
```

## Cross-references

- Substrate driver: [`pty-driver.md`](pty-driver.md). C1-C5 substrate facts
  live there.
- Spike report: `docs/research/spikes/granite-tui-pty-spike.md` (v7, closed
  2026-06-03). C1-C5 + F1-F4 substrate facts are load-bearing inputs.
- Probe: `scripts/probe_slash_arguments.py`. F1-F4 persona-priming
  findings (model-side `$ARGUMENTS` substitution, slash-command layer
  parsing, multi-line message support).
- Plan: `docs/plans/granite_interactive_tui_poc.md`.
- Results doc: `docs/plans/granite_interactive_tui_poc-results.md` (the
  PoC's verdict).
- Prior PoC: `docs/features/granite-agent-loop.md` (now historical; the
  new PoC is the source of truth).

## Out of scope (No-Gos)

- Production cutover (replacing `agent/sdk_client.py` as the unbypassable
  root session runner). The cancelled issue #1542 was replaced by this
  PoC; the production cutover is a follow-on.
- Bridge integration (Telegram wiring, dashboard, dual-resume UI). The
  PoC writes user-address output to a results log.
- `AgentSession` schema change to store two `claude_session_uuid` fields
  (PM UUID + Dev UUID).
- Resume-UUID capture spike (#1552). The PoC exercises resume inside
  itself in a model-reachable env; #1552's findings are a corroborating
  reference, not a prerequisite.
- Cross-turn history accumulation in granite. Fresh context per turn is
  the PoC's default; a 1-line structured handoff field is a follow-on
  optimization.
- Model-per-role config at the runner level. The PoC hardcodes
  `claude --model <auto-pick> --permission-mode bypassPermissions`.
