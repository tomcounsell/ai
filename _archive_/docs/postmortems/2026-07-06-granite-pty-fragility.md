# Postmortem + Architecture Case: Granite PTY Session Handling Is Too Fragile

**Date**: 2026-07-06
**Severity**: High — two SDLC runs produced zero work across two attempts; the worker sat in `granite degraded`, a state that fails *every* eng session, not just these two.
**Author**: Valor (from a first-hand incident while re-running issues #1915 and #1916)
**Verdict**: Retire the interactive-TUI-over-PTY substrate. Move eng/PM/teammate session execution to the headless SDK / `claude -p stream-json` transport that already exists in-tree. Delete the PTY complexity rather than keep patching it.

---

## 1. What Happened (first-hand)

Two bug issues were dispatched to SDLC: **#1915** and **#1916**. Both failed the first time. We raised a timeout, restarted the substrate, and **retried both while watching**. Both failed again, in a *different* way than the first time — and that difference is the whole point of this document.

### First run (earlier 2026-07-06)
- **#1915** → `failed`. Telemetry: `tool-wedge: Bash (default tier) older than 300s`. It at least degraded gracefully, returning a persona-safe apology to Telegram.
- **#1916** → marked `completed`, but `pm_sent_message_ids: None`, empty chat log, no PR. A **silent false success**: a CEO command vanished with no reply and no work.

We diagnosed a "300s guard" and, as a stopgap, raised `TOOL_TIMEOUT_DEFAULT_SEC` from `300` to `3000` (`agent/session_health.py:403`), fixed the true override source (`~/Desktop/Valor/.env:324` plus both launchd plists — a plain restart does *not* reload plist env; it took a `launchctl bootout`/`bootstrap` to make it live), and verified `3000` on the running PIDs.

### Retry (watched live, 06:18–06:35 UTC)
Both re-ran. **Neither did any work. Both issues are still OPEN. No PRs. Nothing to Telegram.** The failure signature changed entirely:

```
06:21:38  container: priming PM
06:27:47  prime post-write wait saw_idle=False buffer_len=17100 elapsed_ms=360456   ← 360s, never idle
06:27:47  container: PM prime done          ← "done" only because the 360s wait EXPIRED
06:27:47  container: priming Dev
06:33:55  prime post-write wait saw_idle=False elapsed_ms=360452                     ← another 360s, never idle
06:34:02  startup cycle=0 pm_idle=False dev_idle=False response=None
   …      (cycles 1–9, all identical no-progress)
06:35:02  ERROR startup plateau detected … bailing early
06:35:02  exit_reason=startup_unresolved
```

- **#1915** → `never_started past grace`: its container never emitted a single progress signal. Bounced pending↔running twice, then failed.
- **#1916** → container spawned, **both** PM and Dev prime steps burned their full 360s ceiling with `saw_idle=False`, the startup loop plateaued after 10 identical no-progress cycles, and it bailed as `startup_unresolved`. The session was then **auto-marked "completed"** anyway — the same false success — and branch cleanup failed because the worktree was still attached.

The worker's own startup line said it plainly: `0 ENG session(s) deferred (granite degraded)`.

### The baseline we regressed from
This is the sharpest evidence in the whole document: **in May 2026 this worked wonderfully** — reliable session execution, no prime plateaus, no login-frame babysitting — **but that was the single-session model, without the two-session PM↔Dev interaction.** The fragility did not arrive with headless Claude execution; it arrived with the **two live TUIs paired mouth-to-ear over PTYs.** Every failure in this incident is a pairing/startup failure: PM prime never idles, Dev prime never idles, the startup loop plateaus waiting for *both* terminals to settle. The thing that broke is precisely the thing we added after the known-good baseline. Removing granite + PTY and coordinating PM and Dev by message-passing (Section 5) is therefore not a leap into the unknown — it is a **return to the May-2026 known-good execution model**, keeping the two-role split but dropping the two-live-terminal mechanism that made it fragile.

### The lesson from the incident
Our stopgap was aimed at the wrong layer. **Nothing ever got far enough to run a Bash tool.** The first-run symptom (`Bash tool-wedge older than 300s`) and the retry symptom (`prime never idle` / `never_started`) are two faces of **one root cause: the interactive Claude TUI never reaches a ready/idle state, and everything downstream is us trying to *infer* that state by scraping the terminal screen.** We can raise timeouts forever; we are timing out on a signal that isn't coming.

---

## 2. Why This Keeps Happening: We Are Screen-Scraping a Moving Target

The granite substrate drives the **real interactive `claude` TUI** through a pseudo-terminal and reconstructs session state by **parsing what the TUI paints on screen**. That means our correctness depends on:

- Detecting "the model is idle" from **screen quiescence** (no new paint for N seconds).
- Detecting a **trust dialog** and dismissing it.
- Detecting a **login frame** and re-authenticating.
- Detecting **prime completion** from a persona-ack pattern in the buffer.
- Detecting a **wedge** vs. a slow extended-thinking turn from paint cadence.
- Distinguishing **startup plateau** from legitimately slow startup.

Every one of these is a heuristic reading of a **product UI that Anthropic ships changes to constantly.** A spinner style change, a new first-run prompt, a reflowed status line, a different idle glyph, an added confirmation step — any of these silently breaks a detector, and the failure mode is not a clean error. It is exactly what we saw today: `saw_idle=False` forever, then a plateau, then a fabricated "completed." **The TUI is a human-facing surface with no stability contract. We built a machine on top of it as if it had one.**

---

## 3. The Complexity This Fragility Costs Us

This is not a small wrapper. Measured today:

| Surface | Size / count | What it is |
|---|---|---|
| `agent/granite_container/` | **9,392 LOC** | The PTY substrate |
| `container.py` | 3,020 LOC | Prime + startup + turn orchestration |
| `bridge_adapter.py` | 1,585 LOC | Wiring PTY sessions to the bridge |
| `pty_pool.py` / `pty_driver.py` | 840 / 781 LOC | Slot management + terminal I/O |
| **`byob_relogin.py`** | **725 LOC** | **A full browser-automation OAuth bot** that drives real Chrome through the Claude Code login consent, *solely because the TUI sometimes paints a login screen mid-run* |
| `transcript_tailer.py` / `startup_parser.py` / `role_driver.py` / `hook_edge.py` / `hook_forwarder.py` | ~1,760 LOC | Screen/transcript parsing + hook plumbing |
| PTY timing knobs | **~30 env-tunable constants** | `PRIME_POST_WRITE_TIMEOUT_S`, `PRIME_TRUST_DISMISS_TIMEOUT_S`, `STARTUP_PLATEAU_CYCLES`, `QUIESCENCE_S`, `IDLE_BAR`, `MID_RUN_QUIESCENCE_SECS`, `NUDGE_WEDGE_THRESHOLD_S`, … |
| Cross-codebase leakage | **~30 files under `agent/`** reference PTY/idle/wedge/prime concepts | `session_health`, `session_stall_classifier`, `crash_signature`, `tui_interaction_capture`, `cold_start_metrics`, `steering`, … |
| Docs | **6** `granite-*.md` feature docs + a dedicated **failure-simulation harness** | We wrote a whole harness just to *reproduce* the failure modes |

Two of these entries deserve to be read as evidence, not line items:

- **`byob_relogin.py` exists at all.** We have a 725-line, LLM-free browser robot whose job is to complete an OAuth consent in a real Chrome window because a terminal UI occasionally decides to show a login prompt. Its own docstring warns of "SPIKE-4 GOTCHAS — encoded throughout, do NOT re-litigate" (React hydration races, CDP execution-context detachment, click-timing). That is a monument to accidental complexity. None of it would exist if we authenticated a headless client once instead of re-scraping a login screen.
- **The failure-simulation harness exists at all.** You build a simulator for a system whose real failures are too frequent and too hard to reproduce to debug live. That is a tell.

The ~30 timing knobs are the deeper problem. Each is a number we picked to separate "slow but fine" from "wedged." Today proved the exercise is unwinnable: the retry didn't trip *any* tool timeout, it starved on an idle signal that never arrived. **You cannot tune your way out of parsing an unstable UI.**

---

## 4. The Case for Switching: The Exit Already Exists

We do not need to invent an alternative. **It is already in the tree and already partially wired:**

1. **`agent/sdk_client.py` (3,838 LOC)** already ships two headless paths:
   - `ClaudeSDKClient` (`claude_code_sdk`), configured for our use case with native `pi /login` **subscription auth** and `--mode rpc`.
   - `get_response_via_harness()` — `claude -p stream-json`, parsing structured stream-json events (not screen paint) for the result, usage, and cost.
   Both emit **machine-readable** `usage` / `total_cost_usd` / `result` events. There is no idle-detection heuristic because the protocol *tells you* when a turn ends.

2. **A transport seam already exists.** `session_executor.py` reads `GRANITE__PM_TRANSPORT` / `GRANITE__DEV_TRANSPORT`, and there is already handling around `transport.pm=headless` (`session_executor.py:1686`). The abstraction for "PTY vs. headless" is present; PTY is just the current default.

3. **The SDK-native turn mechanism is already documented in-repo:** `docs/features/granite-hook-driven-turn-returns.md`. We have already started replacing "guess the turn ended from the screen" with "the harness/hooks tell us the turn ended." This document is arguing to finish that migration and delete the loser, not to start a new one.

### Rebutting the original reason we chose PTY (honestly)

The interactive-TUI-over-PTY approach was a deliberate choice, not an accident. The PoC thesis was explicitly that we must drive the **real** interactive TUI, not headless `claude -p`; an earlier attempt (#1542) was cancelled for using headless mode. The reasons were real at the time:

- **Subscription auth**, not metered API billing.
- **Mid-run interactivity / steering** of a live session.
- Full **slash-command / skills / hooks** behavior of the real harness.
- The real **permission UX**.

Every one of those has since become reachable *without* a PTY:

- **Subscription auth** — not a hypothesis to validate; it ships every day. The headless harness (`get_response_via_harness`) strips `ANTHROPIC_API_KEY` so the CLI falls back to the OAuth/subscription token (`sdk_client.py:1605-1607`, `:2469`), and that token is our long-lived (~1-year) `CLAUDE_CODE_OAUTH_TOKEN` setup-token — the same credential granite already injects. This is the exact machinery the production message-drafter runs daily. **It also deletes the entire `/login` re-auth objection**: `byob_relogin.py` exists only to babysit the *interactive TUI's* login prompt, which headless never paints.
- **Mid-run steering** — we now have the Redis steering list drained at turn boundaries (`agent/steering.py`), and hooks (`Stop → decision:block`, `AskUserQuestion`, `PermissionRequest`) are a sanctioned, PTY-free control surface over a live session. Steering no longer needs a terminal to type into.
- **Slash commands / skills / hooks** — these run under `claude -p` and the SDK just as they do in the TUI; they are harness features, not TUI-render features.
- **Permission UX** — handled by hooks and settings, not by us dismissing a painted dialog.

The capabilities we bought PTY for are now available through the structured interface. What remains uniquely "PTY" is the **cost**: the screen-scraping, the login bot, the 30 knobs, the cross-codebase leakage, and incidents like today.

---

## 5. Recommendation: Remove granite + PTY Entirely

This is **not** a proposal to abstract a transport and keep the PTY path warm behind a flag. A dual-run migration would leave us maintaining *both* the fragile system and its replacement, and this repo's rule is explicit: no parallel-run migrations, no historical artifacts, fully cut over and describe only the new status quo. So the recommendation is a **single, total cutover**:

> **Delete `agent/granite_container/` and run PM, Dev, teammate, and eng sessions entirely through `claude -p` headless, authenticated by the long-lived OAuth token, checkpointed by persisted per-role `claude_session_id`s.**

The reason this can be a clean removal rather than a years-long refactor is that **the replacement is already load-bearing in production and the two hard objections are already solved:**

- **Auth is solved and shipping daily.** `claude -p` on the ~1-year `CLAUDE_CODE_OAUTH_TOKEN` is exactly what the message-drafter runs every day (`sdk_client.py:1605-1607`, `:2469`). No `/login` frames, no token-rotation-mid-run, so **`byob_relogin.py` (725 LOC of browser-driving OAuth robot) is deleted, not ported.**
- **Resume is solved; only persistence is missing.** `HeadlessRoleDriver` already captures a per-role `claude_session_id` and exposes a `resume_handle` (`role_driver.py:95, 178-183`), and already chains turns with `--resume` (`:353`). Dev already runs this way (`container.py:799`). With the long-lived token plus persisted session IDs, we can run and resume PM and Dev **as many times as we want, across restarts**. Persisting those handles onto the `AgentSession` is issue **#1721**; this cutover forces and delivers it.

### The end-state architecture (what remains after deletion)

There is no container "pair," no PTY pool, no prime phase, no startup loop, no plateau detector, no idle scraper. A session is:

- **PM and Dev are headless role-runners.** Each turn is a `claude -p stream-json` subprocess, primed on turn 1 via its `/granite:prime-*-role` skill, continued on later turns via `--resume <claude_session_id>`. Turn-end comes from the **Stop-hook envelope / stream-json `result` event** (`docs/features/granite-hook-driven-turn-returns.md`), not from screen quiescence.
- **Coordination stays where it already is.** The PM→Dev handoff is message-passing the executor already mediates; it does not need two live terminals wired mouth-to-ear. This keeps the two-role split that has product value while dropping the two-live-PTY mechanism that was absent from the wonderfully-working May-2026 baseline and is the source of every failure in this incident. What little of `container.py`'s loop is genuinely orchestration (relay, per-turn progress hook, exit classification) collapses onto the headless turn dispatch that the Dev leg already implements.
- **Checkpoint = every turn.** A fresh subprocess per turn carries no ghost liveness state, so the entire class of "stale PTY snapshot / `last_pty_activity_at` says alive but isn't" bug disappears by construction — including the false-success we hit on #1916.
- **Steering lands at turn boundaries.** This is the one real behavioral change: a one-shot `claude -p` turn can't be ctrl-c interrupted mid-turn the way a PTY can. It is a non-issue in practice — the worker already drains the steering list at turn boundaries (`agent/steering.py`), and PM turns are short routing/classification turns. We accept boundary-granularity steering and delete the two-stage ctrl-c interject path.

### What gets deleted (the point of the exercise)

Per the no-legacy-code rule, this is removal, not deprecation:

- **All of `agent/granite_container/`** — the 9,392 LOC substrate: `pty_pool.py`, `pty_driver.py`, `transcript_tailer.py`, `startup_parser.py`, `hook_edge.py`, `hook_forwarder.py`, the prime/startup/plateau machinery in `container.py`, **`byob_relogin.py` in full**, and the PTY branch of `bridge_adapter.py`. The genuinely reusable pieces (`HeadlessRoleDriver`, the classifier, cost accounting) graduate out into a small `agent/` module.
- **The ~30 PTY timing knobs** — `PRIME_POST_WRITE_TIMEOUT_S`, `PRIME_TRUST_DISMISS_TIMEOUT_S`, `STARTUP_PLATEAU_CYCLES`, `QUIESCENCE_S`, `IDLE_BAR`, `MID_RUN_QUIESCENCE_SECS`, and the rest — collapse to a couple of honest subprocess timeouts on a protocol that reports its own turn boundaries.
- **The PTY leakage across ~30 `agent/` files** — the idle/wedge/prime branches in `session_health`, `session_stall_classifier`, `crash_signature`, `tui_interaction_capture`, `cold_start_metrics` reduce to subprocess-liveness + hook-edge signals.
- **The failure-simulation harness and the transport abstraction itself** — with only one transport there is no `GRANITE__*_TRANSPORT` to validate, no PTY failure modes to simulate.
- **The `TOOL_TIMEOUT_DEFAULT_SEC 3000` stopgap** reverts to `300`; it was aimed at a symptom this removal deletes.

### What this subsumes

- **#1918 workstream 2** (scraped-marker drift) becomes structurally impossible — there are no scraped markers left to drift.
- **#1721** (persist resume handles / lossless checkpoint resume) is delivered as a required part of the cutover, not a follow-up.
- The **false-success class** (#1916, both runs) is closed by construction: a headless turn either returns a `result` or the subprocess errors; there is no "plateau, then auto-mark completed."

### Why now

Today we spent a full investigation cycle, a stopgap deploy, a launchd reload, and two watched retries, and shipped **zero** SDLC work on two issues because a terminal UI would not paint an idle glyph — and then *reported success*. That is the most expensive failure mode there is. Meanwhile the replacement authenticates with a token we already hold, resumes with plumbing we already wrote, and runs the same harness features (skills, hooks, slash commands) that the PTY was chosen to preserve. We are carrying ~9.4k LOC of UI-scraping and a browser-driving OAuth robot to avoid using a structured interface sitting in the same package that we already run in production every day.

**The Claude TUI is a fast-moving human product with no stability contract. Stop consuming it as if it were an API. Delete granite + PTY, run `claude -p`, and keep the ~200 lines that were ever really ours.**

---

## Appendix: Evidence Index (all first-hand today)

- Failure telemetry: `valor-session telemetry --id 0_1783318706129` (#1915, `never_started past grace`), `--id 0_1783318711857` (#1916, `startup_unresolved` → auto-`completed`).
- Worker log 06:21–06:35 UTC: PM/Dev prime `saw_idle=False elapsed_ms=360xxx`, `startup plateau detected … bailing early`, `granite degraded`.
- Complexity: `wc -l agent/granite_container/*.py` → 9,392 total; `byob_relogin.py` 725 LOC; ~30 PTY timing constants via `grep -oE '[A-Z_]*(TIMEOUT|GRACE|QUIESCEN|WEDGE|PLATEAU|PRIME|IDLE|HEARTBEAT)[A-Z_]*'`.
- Existing exit: `agent/sdk_client.py` (`ClaudeSDKClient`, `get_response_via_harness`, `claude -p stream-json`, `pi /login` subscription auth); `GRANITE__PM_TRANSPORT=headless` seam at `session_executor.py:1686`; `docs/features/granite-hook-driven-turn-returns.md`.
- Stopgap change: `agent/session_health.py:403`, `~/Desktop/Valor/.env:324`, `com.valor.{worker,bridge}.plist` (commit `4f9f929e`).
