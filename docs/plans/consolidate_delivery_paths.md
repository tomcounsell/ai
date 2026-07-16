---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1370
last_comment_id: 4875179886
revision_applied: true
---

# Consolidate Agent-Message-Delivery Send Paths

## Problem

The 2026-05-10 daily integration audit (issue #1370) found multiple doors into
the outbound-message outbox, each with a different post-processing pipeline,
and no document declaring which path is canonical for which caller. Since
filing, the divergence has both *narrowed* (PR #1382 routed the stop-hook
tool-call path through the canonical handler; PR #1685 turned the drafter into
a verbatim pass-through + validation filter) and *widened* (PR #1738 added
health-checker recovery notices; issues #1730/#1794/#1797 added a synchronous
terminal-status flush that writes raw payloads to the outbox).

**Current behavior** (re-verified against `main` @ `2201e7d15`, 2026-07-08 —
see Freshness Check): agent-authored text can reach the user through paths
with three different filter postures. `tools/send_telegram.py` still runs the
drafter but **ignores its `needs_self_draft` verdict**, skips the redundancy
filter and RTR, and raw-rpushes to `telegram:outbox`. A wire-format violation
or empty promise that the canonical handler would bounce back to the agent for
self-draft sails straight to Telegram on this path — that defect is real and
unchanged. **Correction from the original filing:** the claim that
`tools/send_telegram.py` is "the tool the eng/PM system prompt teaches for
proactive sends" no longer describes production eng traffic. The granite
two-PTY architecture that prompt taught was torn down (issue #1924, PR #1930,
`e8351e4ca`) and replaced by the headless `agent/session_runner/`; today's
eng/PM sessions communicate via the `[/user]`/`[/complete]` token convention
(`.claude/commands/roles/prime-pm-role.md`), not a taught CLI tool, and the
`agent/hooks/stop.py` "delivery review gate" that used to present
`send_message.py`/`send_telegram.py` as options is dead code on that path (see
Freshness Check). The one prompt surface that still actively teaches
`send_telegram.py` to any session (session_runner or otherwise) reachable via
normal skill-matching is `.claude/skills/telegram/SKILL.md` — this is now the
load-bearing reference to fix, not the `sdk_client.py`/`engineer.md` prompt
blocks (those sit behind a `ValorAgent` class nothing instantiates anymore).
The underlying design problem — no declared canonical pipeline, no bypass
policy, undocumented gate vocabulary/failure-modes — still stands.

**Desired outcome:** One declared canonical pipeline for agent-authored text;
one grep-able, documented seam for the only sanctioned bypass class
(system-authored canned notices); `tools/send_telegram.py` deleted with its
unique capabilities migrated; a delivery-path registry in
`docs/features/agent-message-delivery.md` naming every remaining path, its
caller, and its filters; canonical vocabulary; failure-mode documentation; and
a contract test per delivery path asserting input → outbox payload with the
real handler.

## Freshness Check

**Original baseline:** `e7a7f987c3a05992acbe5d3e246b1879d505eace` (2026-07-03)
**Re-check baseline:** `2201e7d156b3bc50fb3114432c5945662dffe250` (2026-07-08)
**Issue filed at:** 2026-05-10 (re-scoped to consolidation plan by comment on 2026-06-24)
**Disposition:** **Major drift**, discovered while planning a sibling issue (#1955). `e7a7f987c` — this plan's original freshness-check baseline — is a git ancestor of `e8351e4ca`, the "Granite PTY teardown: headless `claude -p` session runner cutover" (PR #1930, issue #1924), which landed AFTER the original check and replaced the two-PTY `agent/granite_container/` architecture with the current `agent/session_runner/`. The original check's file:line citations for the *code-level* delivery pipeline (drafter, output_handler, redundancy filter, RTR) all still hold — those are unaffected by the teardown. What broke is the *framing* of how an agent reaches for a send tool in the first place. See the corrected Data Flow and Problem sections below; the Solution's code-level scope is largely unaffected (detailed per-item in Technical Approach).

**Original file:line references (still accurate, unaffected by the teardown):**

- `tools/send_message.py:71-145` — issue claimed "no drafter, no RTR, no redundancy filter" — **DRIFTED / already fixed**: PR #1382 (commit `97a6cd8f`) rewrote the tool to route through `TelegramRelayOutputHandler.send`. Today it runs linkify + promise gate CLI-side (`tools/send_message.py:212-225`), then delegates to the handler at `tools/send_message.py:257-270` (telegram) and `:348-360` (email). Full pipeline applies.
- `tools/send_telegram.py:71-99` — claim "drafter only" — **still holds** (`_draft_text` at `tools/send_telegram.py:71-99`; raw rpush at `:211-217`). Additional finding: `_draft_text` returns the *original* text when `draft.text` is empty (the drafter's blocking `needs_self_draft` signal), so validation verdicts are silently discarded on this path.
- `agent/output_handler.py:301-552` — **drifted to** `TelegramRelayOutputHandler.send` at `agent/output_handler.py:346-790`: drafter (hoisted, once, `:403-462`) → self-draft steering (`:434-441`) → redundancy filter (`:513-616`, SDLC sessions) → RTR (`:618-724`, env-gated) → transport branch + outbox rpush (`:739-790`). Claims hold.
- `bridge/email_bridge.py:575-577` — **drifted to** `EmailOutputHandler.send` at `bridge/email_bridge.py:810+`; drafter call at `:923-927`. Runs the drafter only — no self-draft steering, no redundancy filter, no RTR — and sends via direct SMTP (not the outbox).
- `bridge/telegram_bridge.py:2673` — **drifted to** `:2873-2940`: the bridge's registered send callback wraps `handler.send` (adds `filter_tool_logs`, PM self-messaging bypass, `<<FILE:>>` extraction).
- `config/personas/project-manager.md:221` — **gone** (persona renamed); the erstwhile live references were `config/personas/engineer.md:476` and three prompt blocks in `agent/sdk_client.py` (~`:3520-3645`) — **now confirmed dead** per the 2026-07-08 re-check below.
- `agent/session_health.py::_deliver_tool_timeout_degraded_notice` (PR #1738, now merged) — the issue comment claimed it "bypasses everything." **Partially stale**: it resolves the send callback via `_resolve_callbacks` (`agent/agent_session_queue.py:1266`), which in the worker process returns `TelegramRelayOutputHandler.send` (registered at `worker/__main__.py:892-901`) — so the canned notice *does* traverse the full filter stack; only the no-callback fallback (`FileOutputHandler`) bypasses it. Two sibling call sites share the pattern. **Line numbers refreshed 2026-07-08** (file shrank 5120→4944 lines across 8 commits since the original check): `_deliver_tool_timeout_degraded_notice` now at `agent/session_health.py:1726`; `_deliver_deferred_self_draft_fallback` now at `:1967` (its `_resolve_callbacks` call at `:2048-2050`); fan-out completion site's `_resolve_callbacks` now at `:3673-3679`. The three-call-site shape the plan describes is unchanged — only the line numbers moved.
- **New since original filing:** `flush_deferred_self_draft_sync` (issues #1794/#1797) is a genuinely raw outbox writer — synchronous `rpush` with no drafter/redundancy/RTR, justified by running in a no-event-loop context. It builds the telegram payload inline (the email branch reuses `build_email_outbox_payload`).
- Pattern 4's claim "no doc for `bridge/redundancy_filter.py`" — **stale**: `docs/features/drafter-redundancy-suppression.md` exists and is indexed in `docs/features/README.md:58`.

**2026-07-08 re-check findings (post-teardown, the Major Drift items):**

- **`agent/sdk_client.py:3527, 3596, 3634` — stale line numbers AND dead code.** The three "teach `send_telegram.py`" blocks now sit around `agent/sdk_client.py:3616-3623` and `:3685-3730` (file grew 3838→3927 lines from PR #1930/#1938). All three are inside `class ValorAgent` (`agent/sdk_client.py:1511`). Grepping every live caller (`agent/session_executor.py`, `agent/agent_session_queue.py`, `agent/health_check.py`, `agent/session_completion.py`, `bridge/routing.py`, `worker/idle_sweeper.py`, `worker/__main__.py`) — **none import `ValorAgent`**; only free functions (`get_response_via_harness`, `get_turn_count`, `get_stop_reason`, `load_principal_context`). No live code instantiates `ValorAgent` anymore.
- **`config/personas/engineer.md:476` — content unchanged, but unreachable for session_runner.** Only reachable via `load_persona_prompt`/`compose_system_prompt`/`load_eng_system_prompt` (`agent/sdk_client.py:960,1046,1228`), themselves only called from `ValorAgent` methods and `scripts/capture_persona_baseline.py`. `agent/session_runner/role_driver.py` primes via `.claude/commands/roles/prime-{pm,dev,teammate}-role.md` (slash-command path, `_PRIME_SLASH_BY_ROLE`/`_PRIME_FILE_BY_ROLE` at `role_driver.py:58-67`), never `config/personas/`. **Dead for session_runner.**
- **`.claude/skills/telegram/SKILL.md:18-36` — content matches, and this IS live-reachable.** A generic Claude Code skill, not gated by role/persona — session-matching can surface it to a session_runner turn. This is the one prompt-teaching citation that is stale-in-guidance but not dead-in-reachability, and is now the highest-priority sweep target.
- **`agent/hooks/stop.py:56-60` (`_LEGACY_SEND_TELEGRAM_PATTERN`) and `:163-190` (`_build_review_prompt`, the "review gate")** — content matches (still teaches `send_message.py`/`react_with_emoji.py`, still has the legacy regex), but **confirmed dead for session_runner**: `agent/session_runner/hook_edge.py::generate_hook_settings` wires the Stop hook only to `hook_forwarder.py`, never to `agent/hooks/stop.py`. Corroborated independently in the sibling-issue (#1955) investigation.
- **`tools/send_telegram.py`** — file still exists (14.8KB), still referenced by ~20 files. Every *prompt-teaching* reference to it now sits on a dead or minority path (per above); the *code* references (relay/bridge dedup recording, hook classification pattern) are still live regardless of whether anyone invokes the tool, and Decision C's retirement work is unaffected.
- **What DOES an eng/session_runner system prompt teach for a proactive mid-turn send, today?** Traced `role_driver.py:329-343` (`_prime_args`) → `/roles:prime-pm-role` (or `prime-dev-role`/`prime-teammate-role`) prepended to the first message; `runner.py:445` confirms role `"pm"` for default `session_type="eng"`. Reading `.claude/commands/roles/prime-pm-role.md` and `prime-dev-role.md` in full: **neither mentions `tools/send_telegram.py` or `tools/send_message.py` at all.** The PM's only taught communication mechanism is the `[/user]`/`[/complete]` token convention on final turn text (`prime-pm-role.md:29-33`); the Dev role's final message is forwarded verbatim to the PM, never sent directly. So proactive mid-turn tool-based sends are **not** taught to session_runner/eng sessions today — all communication flows through the end-of-turn text/token → automatic `send_cb` path (Data Flow path 3, corrected below).
- **Does session_runner have its own review gate for proactive sends?** No. `hook_edge.py`'s `generate_hook_settings` wires only `hook_forwarder.py`; there is no pre-send gate for a hypothetical mid-turn CLI invocation — it would go straight through the CLI's own internal pipeline (linkify/promise-gate/handler), the same sole checkpoint as the automatic path.

**2026-07-16 re-anchor (critique-driven, baseline `de5cdbd6c`):** the 8-day
Planning stall since the 2026-07-08 check crossed another architecture-level
landing, exactly the situation the #1924 lesson warns about. **Issue #2039
deleted `class ValorAgent` and its three `send_telegram.py` prompt blocks.**
Verified directly against `de5cdbd6c`:
- `grep -rn "class ValorAgent" agent/` → **empty** (the class is gone repo-wide).
- `wc -l agent/sdk_client.py` → **1522 lines** (not the 3927 the 2026-07-08 pass
  recorded); the cited blocks at `:3616-3623` / `:3685-3730` are hundreds of
  lines past EOF and no longer exist.
- `agent/session_executor.py:1810` documents the removal: "deleted ValorAgent
  path (main sdk_client.py ~line 3930) … issue #2039".
- The **only** surviving `send_telegram` reference in `agent/sdk_client.py` is a
  load-bearing prose comment at `:503` ("# telegram_message_id so
  tools/send_telegram.py can reply to the …") — it must be reworded (not
  blank-deleted) in the retirement sweep or the Verification "No live
  references" grep gate false-FAILs.

**Consequence for the plan:** the `agent/sdk_client.py` prompt-block *deletion*
scope (Technical Approach step 4, Agent Integration bullet 3, Risk 2, Race 2) is
now a **no-op on a stale model** and is struck. Retirement re-anchors on the two
genuinely-live teaching surfaces confirmed present at `de5cdbd6c`:
`.claude/skills/telegram/SKILL.md:18-36` (the load-bearing fix) and
`config/personas/engineer.md:476`. All other core surfaces re-verified present:
`tools/send_telegram.py` exists; `_LEGACY_SEND_TELEGRAM_PATTERN` in
`agent/hooks/stop.py` (2 occurrences); `TelegramRelayOutputHandler.send`
returns `None`; `build_email_outbox_payload` already extracted at
`agent/output_handler.py:131` (email branch already reuses it — only the
telegram inline payload near `:750-761` remains to extract); `DeliveryOutcome`
and `deliver_system_notice` do **not** yet exist (still to build). **Drifted
`session_health.py` line numbers refreshed:** `_deliver_tool_timeout_degraded_notice`
`1726`→**`1851`**; `_deliver_deferred_self_draft_fallback` `1967`→**`2092`**;
`flush_deferred_self_draft_sync` at **`1940`**; the three `_resolve_callbacks`
sites `1726/2048/3673`→**`1826/1828`, `2173/2175`, `3905/3911`**. Issue #1955
(local-file-path drafter violation) MERGED but the self-draft-steering block it
shares is intact at `agent/output_handler.py:423-441` — no conflicting edit.

**Cited sibling issues/PRs re-checked:**
- #1369 — CLOSED, fixed by PR #1382 (path 1 consolidation). Its fix is the template this plan extends.
- **#2039 — MERGED.** Deleted `class ValorAgent` + its three `send_telegram` prompt blocks; root cause of this 2026-07-16 re-anchor (see above).
- **#1955 — MERGED** (`0bce7800b`). Adds a local-file-path `Violation`; shares `agent/output_handler.py`'s self-draft-steering block (`:423-441`) but does not conflict with this plan's scope.
- PR #1685 / #1680 — MERGED (`513d8eac`): drafter is verbatim pass-through + validation; `needs_self_draft` routes a steering nudge instead of a rewrite.
- PR #1738 — MERGED: degraded-notice path exists as described above.
- PR #1415 — MERGED: `build_teammate_instructions()` now uses TOOL POSTURE / OPERATIONAL WORK ENCOURAGED / WHEN BLOCKED blocks; `tests/unit/test_qa_handler.py` already asserts them, and its DELIVERY REVIEW section assertions are unaffected.
- **#1924 / PR #1930 (`e8351e4ca`) — MERGED, 2026-07-06/07.** The granite-PTY teardown. Root cause of this re-check; see above.

**Active plans in `docs/plans/` overlapping this area:** GitHub issue **#1955** ("Message drafter has no local-file-path awareness") is being planned concurrently and touches the same `bridge/message_drafter.py` / `agent/output_handler.py` self-draft-steering machinery. That plan adds a new `Violation` type (local file-path references) and fixes the fact that violations are never surfaced at all for session_runner/eng sessions (since the only consumer, `agent/hooks/stop.py`, is dead there) — it is scoped narrower than this plan (detection + surfacing of one new violation class, not path consolidation) and does not need to block on this plan landing first, though both should avoid touching `agent/output_handler.py`'s self-draft-steering block (`:429-441`) in conflicting ways in the same review cycle.

**Notes:** Because path 1 is already consolidated, this plan's code scope shrinks to: retiring `send_telegram.py`, naming/centralizing the system-notice bypass seam, vocabulary + docs, and the contract-test suite. The re-check does not change this code scope (see Technical Approach for the two adjusted items) — it corrects the plan's prose framing of *why* `send_telegram.py` still gets called and *which* prompt surface is the load-bearing fix target.

## Prior Art

- **Issue #641 / `docs/plans/unify-telegram-send.md` (Done)** — unified the earlier `send_telegram.py` vs `valor-telegram send` confusion by giving the PM tool `--file` support instead of teaching two tools. Lessons carried forward: (1) prompt-surface audits are load-bearing — the agent invents hybrid syntax when prompts conflict; (2) the Redis queue path is load-bearing for `has_pm_messages()` tracking; (3) `valor-telegram send` is the *operator* CLI, deliberately not an agent delivery path.
- **Issue #1369 / PR #1382** — routed `tools/send_message.py` through the canonical handler. Proves the wrapper pattern this plan finishes: CLI keeps env validation, linkify, and promise gate; handler owns everything else.
- **Issue #1680 / PR #1685 (`513d8eac`)** — drafter rewrite deleted `_draft_with_haiku`/`_draft_with_openrouter`; `draft_message()` is verbatim pass-through + validation. This removes the historical reason for `send_telegram.py --no-draft` (there is no LLM rewrite to skip anymore).
- **Issue #1205** — redundancy filter; **#1193** — RTR; **#1730/#1794/#1797** — deferred self-draft persistence and terminal flushes. These define the filter stack the registry must document.
- **PR #1738** — tool-timeout degraded notice; its issue comment explicitly asked this plan to answer "when is it correct to bypass the filter stack?"
- **Issue #1924 / PR #1930 (`e8351e4ca`)** — the granite-PTY teardown, discovered mid-planning (2026-07-08) to have landed after this plan's original freshness check and invalidated its framing of `agent/hooks/stop.py` as the live "delivery review gate" for eng sessions. See the corrected Freshness Check and Data Flow sections. Lesson for future plans: a Medium-appetite plan sitting in `Planning` status for 5+ days should re-run its freshness check if any architecture-level PR lands in the interim, not just check for direct file-line drift on its own citations.

## Research

No relevant external findings — this is purely internal consolidation of repo-owned delivery plumbing; no external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

Current outbound flows for agent-authored text (re-verified 2026-07-08 against
session_runner reality — see Freshness Check for the correction history):

1. **End-of-turn text/token path (dominant for eng/session_runner):** the PM/Dev's final turn text (classified via the `[/user]`/`[/complete]` token convention, `.claude/commands/roles/prime-pm-role.md:29-33`) is forwarded to the worker's registered `send_cb` = `TelegramRelayOutputHandler.send` → drafter validation → self-draft steering → redundancy filter → RTR → `telegram:outbox:{sid}` / `email:outbox:{sid}` → relay (Telethon / SMTP relay) → user. Relay records `pm_sent_message_ids` and `recent_sent_drafts` post-send (`bridge/telegram_relay.py:846-866`). This is the SAME code as path 3 below — session_runner sessions have no separate "proactive send" tool call; all communication is end-of-turn text. No pre-send review gate exists on this path (`agent/hooks/stop.py` is dead for session_runner turns — see Freshness Check).
2. **`send_message.py` CLI path (reachable, minority):** an agent invokes `python tools/send_message.py "text"` directly (e.g., a Teammate session, or any session that reaches for the CLI mid-turn rather than relying on end-of-turn forwarding) → linkify → promise gate → Popoto session lookup (fail-closed) → `TelegramRelayOutputHandler.send` (same handler as path 1). Full pipeline applies. This is the tool this plan wants to be the *sole* agent-facing CLI (Decision A).
3. **Proactive `send_telegram.py` path (to be retired):** an agent invokes `python tools/send_telegram.py "text"` mid-session → `_draft_text` (validation verdicts discarded) → linkify → 4096-char truncate → promise gate → raw `rpush telegram:outbox:{sid}` → relay → user. Reachability today is via `.claude/skills/telegram/SKILL.md`'s guidance (the only live-reachable prompt surface still teaching this tool — see Freshness Check); the `agent/sdk_client.py`/`config/personas/engineer.md` teaching surfaces are dead (`ValorAgent` is never instantiated by live code).
4. **Health-checker/recovery notices:** `agent/session_health.py` composes a canned string → `_resolve_callbacks` → `handler.send` in the worker (full stack) or `FileOutputHandler` fallback; plus `flush_deferred_self_draft_sync` writing raw payloads synchronously at the `finalize_session` chokepoint.

Target flow after this plan: paths 1 and 2 unchanged (declared canonical — both already funnel through `TelegramRelayOutputHandler.send`); path 3 deleted (its one live teaching surface, `.claude/skills/telegram/SKILL.md`, is repointed to path 2's tool); path 4's async call sites route through one named helper (`deliver_system_notice`) and the sync flush reuses one shared payload builder, with both declared in the registry.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #527 / #641 (send_telegram evolution) | Made `send_telegram.py` the PM tool, later added `--file` | Solved tool *confusion* but entrenched a second pipeline; filters added later (#1205, #1193, #1685 steering) landed only in the handler, so the PM path silently fell behind |
| PR #1382 (#1369) | Routed `tools/send_message.py` through the canonical handler | Fixed one of the divergent paths but left `send_telegram.py` untouched and still taught by the system prompt; no policy stopped new paths from appearing (PR #1738 added one weeks later) |
| stop.py "legacy" labeling | Marked `send_telegram.py` legacy in the classifier | A label without a migration: the prompt surfaces kept teaching the tool, so "legacy" accrued new callers |

**Root cause pattern:** filters are added at the handler, but nothing forces callers *through* the handler, and no written policy says who may bypass it. Consolidation without a declared policy re-diverges — this plan ships the policy (registry + one bypass seam) alongside the code change.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `TelegramRelayOutputHandler.send` gains a return value (`DeliveryOutcome` enum: `sent | suppressed_redundant | suppressed_rtr | deferred_self_draft | dropped_empty`) — currently returns `None`, so all existing callers remain valid (additive). `tools/send_telegram.py` is deleted; its `--emoji` capability moves to `tools/react_with_emoji.py --standalone`. New module-level helper `deliver_system_notice(entry, message)` plus shared `build_telegram_outbox_payload(...)` in `agent/output_handler.py`.
- **Coupling:** decreases — `agent/session_health.py` loses three hand-rolled callback-resolution blocks; `agent/sdk_client.py` prompt blocks reference one tool instead of two.
- **Data ownership:** unchanged — relay still owns sends and post-send recording; outbox payload shape unchanged (contract-tested).
- **Reversibility:** moderate — deleting `send_telegram.py` is a hard cutover (per NO LEGACY CODE TOLERANCE); reverting means restoring the file and prompt blocks from git history.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm the two Open Questions)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies; everything runs against repo-local code, Redis, and the existing test harness.

## Solution

### Key Elements

Four decisions, then the mechanical work that follows from them:

- **Decision A — canonical path.** `TelegramRelayOutputHandler.send` is the single queue-side pipeline for agent-authored outbound text (both transports); `tools/send_message.py` is the single agent-facing CLI wrapper. This ratifies what PR #1382 built and extends it to every remaining caller.
- **Decision B — bypass rule.** *Agent-authored content always traverses the canonical handler. Only system-authored canned notices — fixed strings composed by infrastructure code, containing no agent-generated text — may be delivered outside the CLI wrapper, and only via the named helper `deliver_system_notice()`.* The helper wraps today's `_resolve_callbacks` + `FileOutputHandler` fallback + telemetry pattern, making every bypass enumerable by a single grep. (In the worker the resolved callback is still `handler.send`, so notices retain outbox delivery; the *policy* point is that no new code hand-rolls callback resolution.) `flush_deferred_self_draft_sync` is declared the one sanctioned synchronous outbox writer (no event loop at the `finalize_session` chokepoint) and switches to the shared payload builder so the wire shape is defined once.
- **Decision C — retire `tools/send_telegram.py`.** Full cutover, no transition shims: delete the file; the load-bearing prompt-surface fix is `.claude/skills/telegram/SKILL.md` (the only teaching surface still reachable by a live session — see Freshness Check), not the `agent/sdk_client.py` prompt blocks or `config/personas/engineer.md:476` (both dead: gated behind `ValorAgent`, which no live code instantiates post-#1924 teardown — delete those blocks as dead-code cleanup, not as a "re-teaching" migration); migrate `--emoji` (standalone custom-emoji message) to `tools/react_with_emoji.py --standalone`; drop `--react` (already owned by `react_with_emoji.py`) and `--no-draft` (obsolete — the drafter no longer rewrites; verbatim system notices use `deliver_system_notice`); remove `_LEGACY_SEND_TELEGRAM_PATTERN` from `agent/hooks/stop.py` (dead-code cleanup — `stop.py` itself is unreachable for session_runner, but it's still imported/tested code that should not keep referencing a deleted tool). `has_pm_messages()` / `recent_sent_drafts` tracking is unaffected: the relay records both from the outbox payload regardless of which tool queued it (`bridge/telegram_relay.py:846-866`).
- **Decision D — canonical vocabulary.** Gate concept: **"delivery review gate"** (already the module docstring; the UI label "DELIVERY REVIEW" and doc heading update to match). Outcome verbs: the classifier's four — **send / react / silent / continue** — plus the new `DeliveryOutcome` values for handler results; retire "send as-is" / "edit and send" as distinct terms (both are `send`).

- **Delivery-path registry**: a "Delivery paths" section in `docs/features/agent-message-delivery.md` — one table naming each remaining path, its caller, which filters apply, and *why* (including the two declared intentional divergences: `EmailOutputHandler.send`'s drafter-only direct-SMTP posture for worker email sessions, and `valor-telegram send` as the human-operator CLI that is not an agent delivery path).
- **`DeliveryOutcome` surfacing**: `handler.send` returns the outcome; `tools/send_message.py` prints it instead of an unconditional "Queued" (today a redundancy- or RTR-suppressed CLI send prints "Queued (N chars)" — misleading to the agent that called it).
- **Failure-modes documentation + tests**: a "Failure modes" section in the feature doc covering drafter exception in first stop, worker restart between stops (`_review_state` is process-local — gate re-presents; accepted behavior), malformed transcript tail, and simultaneous tool-call + continued work.
- **Contract tests**: per-path tests asserting input → outbox payload with the real handler (see Failure Path Test Strategy).

### Flow

Agent needs to message the user (any moment, any transport) → `python tools/send_message.py "text" [--file ...]` → canonical handler pipeline → outbox → relay → user. Infrastructure needs to deliver a canned notice → `deliver_system_notice(entry, message)` → resolved callback (handler in worker) or file fallback. There is no third door.

### Technical Approach

1. **`agent/output_handler.py`**: add `DeliveryOutcome` (`enum.StrEnum`); return it from `send()` at each exit (suppression exits, defer exit, outbox write). Extract `build_telegram_outbox_payload(chat_id, text, reply_to, session_id, file_paths)` from the inline payload dict (`:750-758`) and reuse it in `flush_deferred_self_draft_sync`. Add `async def deliver_system_notice(entry, message, *, telemetry_key: str | None = None)` encapsulating the `_resolve_callbacks` + `FileOutputHandler` fallback + WARNING-and-swallow contract currently duplicated at `agent/session_health.py:1851` (`_deliver_tool_timeout_degraded_notice`), `:2092`/`:2173-2175` (`_deliver_deferred_self_draft_fallback`), and `:3905-3911` (fan-out completion site) — **line numbers refreshed 2026-07-16** (re-verify at build time with `grep -n "_resolve_callbacks" agent/session_health.py`); the fan-out site keeps its own completion-runner logic and uses the helper only for callback resolution if extraction is clean; otherwise leave it and document it in the registry — do not force-fit. **Per critique (CONCERN):** the registry entry + `grep -c "_resolve_callbacks" agent/session_health.py` enumerability is the hard deliverable; the 3-site extraction is optional cleanup, not a blocking Success Criterion — if any site resists clean extraction, leave it and document it.
2. **`tools/send_message.py`**: print the returned `DeliveryOutcome` (exit 0 for suppress/defer — they are pipeline verdicts, not errors; the message tells the agent what happened).
3. **`tools/react_with_emoji.py`**: add `--standalone` (port of `send_emoji` from `send_telegram.py:314-383`, payload `type: custom_emoji_message` unchanged so the relay needs no changes).
4. **Delete `tools/send_telegram.py`**; re-derive the sweep list from a fresh `grep -rn "send_telegram\.py" agent/ tools/ bridge/ config/ .claude/skills/ tests/ docs/features/ | grep -v docs/plans` at build time (the list below was re-anchored 2026-07-16), highest-value first: `.claude/skills/telegram/SKILL.md:18-36` (the one live-reachable teaching surface — fix this even if nothing else in this step ships), `config/personas/engineer.md:476` (the second live-reachable teaching surface — reword to name `send_message.py`), `agent/hooks/stop.py` `_LEGACY_SEND_TELEGRAM_PATTERN` + `classify_delivery_outcome` (dead-code cleanup, still worth doing per NO LEGACY CODE TOLERANCE), `agent/sdk_client.py:503` (a load-bearing prose comment — **reword** so it no longer names the deleted tool; do NOT blank-delete; this is the ONLY remaining `sdk_client.py` reference now that #2039 removed `class ValorAgent` and its prompt blocks — the plan's former `:3616-3623`/`:3685-3730` deletion scope is struck as a no-op), `docs/tools-reference.md`, `docs/features/emoji-embedding-reactions.md`, `docs/features/README.md:62`, comment-level references in `agent/output_handler.py` / `agent/session_executor.py` / `bridge/promise_gate.py` / `bridge/telegram_relay.py` / `bridge/telegram_bridge.py`.
5. **Vocabulary sweep** (Decision D): `agent/hooks/stop.py` docstrings and `_build_review_prompt` header, `agent/teammate_handler.py` DELIVERY REVIEW section wording, `docs/features/agent-message-delivery.md` headings, test names/docstrings touched anyway by the retirement.
6. **Docs**: registry + failure-modes sections in `docs/features/agent-message-delivery.md`; cross-links to `bridge-worker-architecture.md`, `read-the-room.md`, `drafter-redundancy-suppression.md`, `promise-gate.md`, `session-steering.md` (closing Pattern 4).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `deliver_system_notice`: callback raises → logged WARNING, swallowed, file-fallback attempted — test asserts the log record and that no exception propagates (mirrors the existing never-raises contract at `agent/session_health.py:1810`)
- [ ] `handler.send` drafter exception → falls through to raw text and returns `DeliveryOutcome.sent` — existing behavior (`agent/output_handler.py:463-470`), new assertion on the return value
- [ ] Stop hook `_generate_draft` drafter exception → truncated raw tail used as draft (`agent/hooks/stop.py:158-160`) — test asserts gate still presents
- [ ] `tools/send_message.py` Popoto lookup failure → fail-closed exit 1 (existing tests keep covering this)

### Empty/Invalid Input Handling
- [ ] `classify_delivery_outcome` on malformed/garbage transcript tail → `silent`, never raises (extend `TestClassifyDeliveryOutcome` with binary-ish garbage input)
- [ ] `deliver_system_notice` with empty message → no send, debug log
- [ ] `react_with_emoji --standalone` with empty feeling → exit 1 (port of `send_telegram.py:346-348` behavior)

### Error State Rendering
- [ ] CLI suppression/defer verdicts print the outcome name to stdout (not a false "Queued") — test each `DeliveryOutcome` branch
- [ ] Worker-restart-between-stops: simulate by clearing `_review_state` between two stop invocations → gate re-presents (documented accepted behavior; the test pins it so a future change is deliberate)

### Contract tests (Pattern 2 — one per delivery path, real handler, fake Redis)
- [ ] CLI telegram: `send_message.py` main → `telegram:outbox:{sid}` payload shape (chat_id, reply_to, text, session_id, timestamp, file_paths)
- [ ] CLI email: → `email:outbox:{sid}` payload (reply-all `to`, subject, threading headers)
- [ ] Worker silent path: registered callback → same payload shape (extends `TestToolCallHandlerRouting`)
- [ ] System notice: `deliver_system_notice` with registered handler → outbox payload; with no registration → `FileOutputHandler` write
- [ ] Sync flush: `flush_deferred_self_draft_sync` telegram branch → payload built by `build_telegram_outbox_payload` (identical shape to handler writes)

## Test Impact

- [ ] `tests/unit/test_send_telegram.py` (entire file, ~30 tests) — DELETE with the tool; REPLACE coverage: queueing/validation/file/album behavior is already covered for the canonical tool in `tests/unit/test_tool_call_delivery.py::TestToolCallHandlerRouting` and the new contract suite; reaction tests (`TestSendTelegramReaction`) move to `tests/unit/test_react_with_emoji.py` alongside new `--standalone` tests
- [ ] `tests/unit/test_stop_hook_review.py::TestClassifyDeliveryOutcome::test_legacy_send_telegram` — DELETE: legacy pattern removed
- [ ] `tests/unit/test_stop_hook_review.py::TestBuildReviewPrompt` — UPDATE: prompt header/vocabulary assertions if wording changes in the sweep
- [ ] `tests/unit/test_tool_call_delivery.py::TestClassifyDeliveryOutcome::test_legacy_send_telegram_still_classifies_as_send` — DELETE: legacy pattern removed
- [ ] `tests/unit/test_tool_call_delivery.py::TestToolCallHandlerRouting` — UPDATE: extend into the per-path contract suite; assert `DeliveryOutcome` return values
- [ ] `tests/unit/test_output_handler.py` — UPDATE: `send()` return-value assertions on existing cases; payload-shape docstrings that cite `tools/send_telegram.py` (`:191`, `:212`) re-point to `build_telegram_outbox_payload`; add `deliver_system_notice` tests
- [ ] `tests/unit/test_qa_handler.py::test_no_send_telegram_instruction` — UPDATE: keeps passing (asserts absence), but re-point its docstring; the TOOL POSTURE / OPERATIONAL WORK ENCOURAGED / WHEN BLOCKED and DELIVERY REVIEW marker assertions from PR #1415 are unaffected unless the vocabulary sweep renames the DELIVERY REVIEW header — if so, UPDATE the marker string in the same commit
- [ ] `tests/unit/test_duplicate_delivery.py` — no change: it covers bridge catchup dedup and auto-continue guards, none of which touch the send tools or handler signature
- [ ] `tests/e2e/test_message_pipeline.py` — no change: it exercises the bridge router classifier (routing/mention/response decisions), not the delivery review gate or send tools
- [ ] `tests/unit/test_nightly_regression_tests.py` — no change: its `send_telegram` is `scripts/nightly_regression_tests.py`'s own function, unrelated to the tool
- [ ] `tests/integration/test_session_spawning.py:151` / `tests/unit/test_promise_gate_session_events.py:14` — UPDATE: comment/docstring references to `tools/send_telegram.py` re-point to `tools/send_message.py`

## Rabbit Holes

- **Unifying `EmailOutputHandler.send` (direct SMTP) with the email-outbox relay path.** Two email mechanisms genuinely exist (worker-registered SMTP handler vs. `email:outbox` + relay). Reconciling them touches retry/DLQ semantics and the email bridge lifecycle — a different blast radius entirely. This plan *documents* the divergence in the registry as intentional; it does not touch email transport code.
- **Making RTR/redundancy apply to email or to system notices.** The redundancy filter is SDLC-session-scoped and RTR is chat-snapshot-based by design; "filter parity everywhere" is not the goal — *declared* filter posture per path is.
- **Rewriting the review gate's `_review_state` to survive worker restarts** (Redis-backed state). The restart behavior (gate re-presents) is acceptable; document and pin it with a test instead of building persistence.
- **Touching `tools/valor_telegram.py`.** It is the operator/teammate CLI (Path B, `owner_agent_session_id`), deliberately outside the agent delivery pipeline since #641. Registry entry only.
- **Refactoring the bridge's `_make_send_cb` wrapper** (`bridge/telegram_bridge.py:2873`). Its extra layers (filter_tool_logs, PM bypass, file extraction) are bridge-process concerns; consolidating them into the handler is a separate design question. Registry entry only.

## Risks

### Risk 1: Proactive PM sends inherit new suppression behavior
**Impact:** After retirement, mid-session sends that used `send_telegram.py` route through the full pipeline — the redundancy filter or self-draft steering could suppress/defer a message the PM expected to land, and (pre-`DeliveryOutcome`) the agent would not know.
**Mitigation:** `DeliveryOutcome` surfacing in the CLI output is part of this plan precisely so the agent sees `suppressed_redundant` / `deferred_self_draft` and can react. The drafter is verbatim pass-through, so no text is altered. Contract tests assert each verdict's CLI output.

### Risk 2: Missed reference to the deleted tool breaks a prompt or hook at runtime
**Impact:** A stale `send_telegram.py` mention in a prompt teaches the agent a nonexistent tool (exactly the #641 failure class); a stale pattern in stop.py misclassifies delivery. **Updated 2026-07-16:** the two live-reachable teaching surfaces are `.claude/skills/telegram/SKILL.md` and `config/personas/engineer.md:476` — both must be reworded. The former `agent/sdk_client.py` prompt-block surfaces no longer exist (issue #2039 removed `class ValorAgent`); only the `:503` prose comment remains and carries no runtime teaching risk but must be reworded so the grep gate passes.
**Mitigation:** Verification table includes a repo-wide grep gate (`match count == 0` outside `docs/plans/`); the sweep list in Technical Approach step 4 was built from a live grep at plan time (refreshed 2026-07-08).

### Risk 3: `DeliveryOutcome` return value breaks a caller that treated `send()` as fire-and-forget
**Impact:** None expected — adding a return value to a previously-`None` coroutine is compatible with every `await ... send(...)` call site.
**Mitigation:** Grep-verify no call site does `assert result is None`; contract tests cover the worker callback, CLI, and bridge wrapper call shapes.

## Race Conditions

### Race 1: Terminal flush vs. async fallback double-send
**Location:** `agent/session_health.py:1876-1990` (sync flush) and `:2040-2136` (async fallback)
**Trigger:** Session with a pending deferred self-draft reaches a terminal status while the health checker also fires
**Data prerequisite:** `deferred_self_draft_pending` persisted in `extra_context`
**State prerequisite:** Existing transport/status gates (`flush` owns telegram + email-completed; async owns email failed/abandoned) plus distinct SETNX dedup keys
**Mitigation:** This plan does not change the gating or dedup keys — the refactor swaps only the payload construction (shared builder) and callback resolution (named helper). Contract tests assert the dedup keys are still consulted before any write.

### Race 2: Prompt-surface cutover vs. in-flight sessions
**Location:** `.claude/skills/telegram/SKILL.md` and `config/personas/engineer.md:476` (the two live teaching surfaces, per the 2026-07-16 re-anchor — the former `agent/sdk_client.py` prompt blocks no longer exist post-#2039 and cannot race); running sessions spawned pre-deploy
**Trigger:** A session whose skill-matching already surfaced the old `send_telegram.py` guidance invokes it mid-turn after the file is deleted
**Data prerequisite:** none
**State prerequisite:** Long-running session spanning the deploy
**Mitigation:** Bash returns a clear "No such file" error. Unlike the original (pre-teardown) framing, there is no stop.py review gate to offer `send_message.py` as a fallback on the next turn for session_runner sessions — the agent must recover by reasoning from the Bash error alone. Acceptable for a hard cutover given the narrow window and low frequency of mid-deploy sessions; noted in the PR description.

## No-Gos (Out of Scope)

- Email transport unification (direct-SMTP `EmailOutputHandler` vs. `email:outbox` relay) — declared an intentional divergence in the delivery-path registry with its rationale recorded there; changing email delivery mechanics is not part of this consolidation.
- `tools/valor_telegram.py` — remains the human-operator/teammate CLI by design (boundary set in #641 and reaffirmed here); documented in the registry, no code change.
- Bridge `_make_send_cb` wrapper layers (filter_tool_logs, PM bypass) — bridge-process concerns documented in the registry; restructuring them is not required to close #1370's design questions.
- Review-gate state persistence across worker restarts — the re-present behavior is documented and test-pinned as accepted; no Redis-backed `_review_state`.

## Update System

No update system changes required — this work modifies repo Python, prompts, and docs that propagate via normal `git pull` in `/update`. No new dependencies, no config files, no Popoto schema changes (no model fields added; `DeliveryOutcome` is an in-process enum), therefore no `scripts/update/migrations.py` entry. The deleted `tools/send_telegram.py` has no `pyproject.toml [project.scripts]` entry to remove (it was invoked as `python tools/send_telegram.py`).

## Agent Integration

This plan *is* agent-integration work: it changes which CLI the agent is taught for outbound messages.

- No new `pyproject.toml [project.scripts]` entry and no MCP server changes — `tools/send_message.py` and `tools/react_with_emoji.py` remain `python tools/...` Bash invocations. The review gate at `agent/hooks/stop.py:163-190` still presents them for whatever (if anything) still runs the pre-teardown `ValorAgent` path, but per the 2026-07-08 re-check that path is dead in production — **do not rely on stop.py as the load-bearing agent-integration wiring.**
- `.claude/skills/telegram/SKILL.md` is the **load-bearing** prompt surface (2026-07-08 correction, superseding the original plan) — it is the only teaching surface still reachable by a live session (session_runner or otherwise) via normal skill-matching. Its PM-tool guidance updates to name `send_message.py` and keep the `valor-telegram`-is-for-operators warning; a tool this skill does not teach is invisible to any session that reaches for Telegram-sending guidance.
- The `agent/sdk_client.py` prompt blocks are **already gone** (issue #2039 deleted `class ValorAgent`; the file is 1522 lines — re-anchored 2026-07-16, see Freshness Check), so there is no block to delete — a no-op. The one surviving `agent/sdk_client.py:503` reference is a load-bearing prose comment that is **reworded** (not deleted). `config/personas/engineer.md:476` IS still live-reachable and is reworded to name `send_message.py`.
- Integration test: extend `tests/unit/test_tool_call_delivery.py` contract suite to run the real CLI `main()` against fake Redis, proving the agent-invokable entry point produces the canonical outbox payload for both transports.

## Documentation

- [ ] Update `docs/features/agent-message-delivery.md`: add **"Delivery paths"** registry table (every path, caller, filters, rationale — including declared intentional divergences), add **"Failure modes"** section (drafter exception in first stop; worker restart between stops; malformed transcript tail; simultaneous tool-call + continued work), apply canonical vocabulary ("delivery review gate"; outcomes send/react/silent/continue + `DeliveryOutcome` values), and add cross-links to `bridge-worker-architecture.md`, `read-the-room.md`, `drafter-redundancy-suppression.md`, `promise-gate.md`, `session-steering.md`
- [ ] Update `docs/features/message-drafter.md`: note the `DeliveryOutcome` return surface and that `send_telegram.py`'s drafter call is gone
- [ ] Update `docs/features/emoji-embedding-reactions.md` and `docs/tools-reference.md`: `--emoji` examples move to `react_with_emoji.py --standalone`
- [ ] Update `docs/features/README.md` index rows that mention `send_telegram` (`:62`)
- [ ] Update `.claude/skills/telegram/SKILL.md` PM-tool table and examples

## Success Criteria

- [ ] `tools/send_telegram.py` deleted; repo-wide grep for `send_telegram.py` finds zero live references outside `docs/plans/`
- [ ] `agent/hooks/stop.py` has no `_LEGACY_SEND_TELEGRAM_PATTERN`; `classify_delivery_outcome` classifies on `send_message.py` / `react_with_emoji.py` only
- [ ] `deliver_system_notice` exists and is the only call path for health-checker notice delivery (`_deliver_tool_timeout_degraded_notice`, `_deliver_deferred_self_draft_fallback` refactored onto it); `grep -n "_resolve_callbacks" agent/session_health.py` shows at most the fan-out completion site
- [ ] `TelegramRelayOutputHandler.send` returns `DeliveryOutcome`; `tools/send_message.py` prints the outcome (no unconditional "Queued")
- [ ] `flush_deferred_self_draft_sync` telegram branch uses `build_telegram_outbox_payload`
- [ ] `tools/react_with_emoji.py --standalone` sends a `custom_emoji_message` payload identical in shape to the old `send_emoji`
- [ ] Delivery-path registry and failure-modes sections exist in `docs/features/agent-message-delivery.md`
- [ ] Contract tests pass for all five paths listed in Failure Path Test Strategy
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (handler+notice)**
  - Name: handler-builder
  - Role: `DeliveryOutcome`, `build_telegram_outbox_payload`, `deliver_system_notice`, session_health refactor
  - Agent Type: builder
  - Resume: true
- **Builder (retirement)**
  - Name: retirement-builder
  - Role: delete send_telegram.py, migrate `--standalone`, sweep prompts/personas/skills/stop.py
  - Agent Type: builder
  - Resume: true
- **Test Engineer (contracts)**
  - Name: contract-tester
  - Role: per-path contract tests, failure-mode tests, Test Impact dispositions
  - Agent Type: test-engineer
  - Resume: true
- **Documentarian**
  - Name: delivery-docs
  - Role: registry, failure modes, vocabulary sweep in docs
  - Agent Type: documentarian
  - Resume: true
- **Validator (final)**
  - Name: final-validator
  - Role: run Verification table, grep gates, success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Handler outcome + system-notice seam
- **Task ID**: build-handler
- **Depends On**: none
- **Validates**: tests/unit/test_output_handler.py
- **Assigned To**: handler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `DeliveryOutcome` enum; return it from every exit of `TelegramRelayOutputHandler.send`
- Extract `build_telegram_outbox_payload`; reuse in `flush_deferred_self_draft_sync` (telegram branch)
- Add `deliver_system_notice`; refactor `_deliver_tool_timeout_degraded_notice` and `_deliver_deferred_self_draft_fallback` onto it (preserve SETNX dedup, transport gates, telemetry counters, never-raises contract exactly)
- `tools/send_message.py` prints the returned outcome

### 2. Retire send_telegram.py
- **Task ID**: build-retirement
- **Depends On**: build-handler
- **Validates**: grep gates in Verification; tests/unit/test_react_with_emoji.py
- **Assigned To**: retirement-builder
- **Agent Type**: builder
- **Parallel**: false
- Port `send_emoji` to `tools/react_with_emoji.py --standalone`; delete `tools/send_telegram.py`
- Sweep: `agent/sdk_client.py` prompt blocks, `config/personas/engineer.md`, `.claude/skills/telegram/SKILL.md`, `agent/hooks/stop.py` (pattern + docstrings), comment-level references (Technical Approach step 4 list)
- Vocabulary sweep (Decision D) across stop.py, teammate_handler.py

### 3. Contract + failure-mode tests
- **Task ID**: build-tests
- **Depends On**: build-retirement
- **Validates**: tests/unit/test_tool_call_delivery.py, tests/unit/test_output_handler.py, tests/unit/test_stop_hook_review.py
- **Assigned To**: contract-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Implement the five contract tests and the failure-mode tests from Failure Path Test Strategy
- Apply every Test Impact disposition (deletes, updates, docstring re-points)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: delivery-docs
- **Agent Type**: documentarian
- **Parallel**: false
- All items in the Documentation section

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; verify all Success Criteria including anti-criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_tool_call_delivery.py tests/unit/test_output_handler.py tests/unit/test_stop_hook_review.py tests/unit/test_react_with_emoji.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/ tools/ bridge/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ tools/ bridge/` | exit code 0 |
| Tool deleted | `ls tools/send_telegram.py` | exit code != 0 |
| No live references | `grep -rn "send_telegram\.py" agent/ tools/ bridge/ config/ .claude/skills/ tests/ docs/features/ \| grep -cv "docs/plans"` | match count == 0 |
| Legacy pattern gone | `grep -c "_LEGACY_SEND_TELEGRAM_PATTERN" agent/hooks/stop.py` | match count == 0 |
| Notice seam exists | `grep -c "def deliver_system_notice" agent/output_handler.py` | output > 0 |
| session_health uses seam | `grep -c "deliver_system_notice" agent/session_health.py` | output > 0 |
| Outcome surfaced | `grep -c "DeliveryOutcome" tools/send_message.py` | output > 0 |
| Registry documented | `grep -c "Delivery paths" docs/features/agent-message-delivery.md` | output > 0 |
| Failure modes documented | `grep -c "Failure modes" docs/features/agent-message-delivery.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-07-16 (FULL, 3 critics). Verdict: NEEDS REVISION (1 blocker). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency (+ Risk & Robustness) | Freshness self-violation: issue #2039 deleted `class ValorAgent` and its three `sdk_client.py` prompt blocks after the 2026-07-08 re-check. Technical Approach step 4, Agent Integration bullet 3, Risk 2, and Race 2 still instruct deleting `agent/sdk_client.py:3616-3623` / `:3685-3730` (past EOF — file is now 1522 lines, not 3927; `ValorAgent` gone). That work is a no-op on a stale model, and the surviving `:503` comment then trips the grep gate. | **Freshness Check 2026-07-16 re-anchor** + struck sdk_client.py deletion scope in Tech Approach step 4, Agent Integration bullet 3, Risk 2, Race 2; `:503` reworded in sweep | Verify `grep -rn "class ValorAgent" agent/` (empty) and `wc -l agent/sdk_client.py` (1522). `agent/session_executor.py:1810` confirms "deleted ValorAgent path … issue #2039". Strike the sdk_client.py deletion scope; re-anchor retirement on the two live surfaces (`.claude/skills/telegram/SKILL.md`, `config/personas/engineer.md:476`). |
| CONCERN | Risk & Robustness (+ History & Consistency) | Step-4 sweep list omits the live comment `agent/sdk_client.py:503` ("# telegram_message_id so tools/send_telegram.py can reply to the"); the Verification "No live references" grep (line 389, expected `== 0`) will match it and false-FAIL a plan that otherwise met its goal. | Tech Approach step 4 now re-derives the sweep at build time and rewords `:503` (not blank-delete) | Re-derive the sweep list from a fresh `grep -rn "send_telegram\.py" agent/ tools/ bridge/ config/ .claude/skills/ tests/ docs/features/ | grep -v docs/plans` at build time; the `:503` hit is load-bearing prose — reword rather than blank-delete. |
| CONCERN | Scope & Value | `DeliveryOutcome` enum + `send()` return threading + CLI surfacing is a separable enhancement: it fixes a pre-existing "Queued" mislabel bug unrelated to #1370 and mitigates a risk that barely applies now that session_runner sessions make no proactive CLI sends. #1370's design questions are answered by the registry + bypass policy, not the enum. | Retained as an explicitly opportunistic UX fix (also the Risk 1 mitigation once `send_telegram.py` is retired); labeled so it can be dropped if appetite tightens — not the load-bearing #1370 deliverable | `agent/output_handler.py:346` `send()` returns `-> None`; threading a value touches every exit path + `test_output_handler.py`. Registry/policy deliverables do not depend on it — excise cleanly into a follow-up, or explicitly label it an opportunistic UX fix so it can be dropped if appetite tightens. |
| CONCERN | Scope & Value | `deliver_system_notice` extraction + 3-site `session_health.py` refactor is the largest code item yet is not required to close #1370; the "no bypass policy" root cause is closed by the registry table + grep gate. The plan itself hedges the refactor ("if extraction is clean; otherwise leave it"), an admission it is speculative. | Tech Approach step 1 amended: registry entry + `grep -c "_resolve_callbacks"` enumerability is the hard deliverable; 3-site extraction demoted to optional cleanup (not a blocking Success Criterion) | Sites are `agent/session_health.py:1826/1828, 2173/2175, 3905/3911` (drifted from the plan's cited 1726/2048/3673), each with divergent SETNX keys, transport gates, telemetry, never-raises contract. Make the registry entry + `grep -c "_resolve_callbacks" agent/session_health.py` the enumerability deliverable; treat extraction as optional cleanup, not a hard Success Criterion. |
| NIT | Scope & Value | `build_telegram_outbox_payload` extraction (2 call sites) is a builder-owned micro-decision over-specified as a plan-level element with its own Success Criterion. | Kept as builder guidance; the "Sync flush" contract test is the real gate | The "Sync flush" contract test already asserts identical wire shape whether or not payload construction is physically shared — the named-helper criterion is redundant with the test. |
| NIT | Structural | `_resolve_callbacks` line citations in Technical Approach step 1 (1726 / 2048-2050 / 3673-3679) have drifted to 1826/1828, 2173/2175, 3905/3911; the 3-site shape holds. | Line numbers refreshed in Tech Approach step 1 and the Freshness Check re-anchor | Refresh line numbers during the revision pass; symptom of the same 8-day freshness gap as the BLOCKER. |

---

## Open Questions

1. **Should mid-session proactive sends be exempt from the redundancy filter?** The filter is SDLC-session-scoped, and a PM deliberately re-sending a status after an edit could be suppressed. Default answer in this plan: no exemption — `DeliveryOutcome` visibility lets the agent rephrase and resend. Confirm.
2. **Vocabulary final call:** "delivery review gate" is proposed as the canonical gate term (matching the module docstring and the `── DELIVERY REVIEW ──` UI label). Confirm, or pick "review gate" and the sweep inverts.
