---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-09-29
tracking: https://github.com/tomcounsell/ai/issues/3588
last_comment_id:
---

# Reflection Human Comms Through the Persona

## Problem

On 2026-09-29 at 09:21 UTC, Tom got this message in **Eng: Valor**:

```
[valor] Lane a2c69feea4c09332f (Job 4d7328d9411f4299aac1fd881d147eee) is gone but its work is visible: PR #3586 (merged) on session/a2c69feea4c09332f. Verify delivery of '...' and discharge the expectation: python -m tools.job_tool expectation-remove --job-id 4d7328d9... --expectation-id 88546e35c436
```

It was the expectation reconciler's to-do for a PM session, pasted unchanged into human chat. It included a Job UUID, an expectation ID, and a shell command Tom cannot run. `job_tool` refuses without `VALOR_SESSION_ID`. At 04:55 UTC the same group had received the "Improvement assumption digest", which ends "This is a status report. It asks nothing."

Both came through the same side door: `reflections/utilities.py::_send_telegram_transport` shells out to `valor-telegram send`, the human-operator CLI. That path skips the drafter and the redundancy filter. It also skips read-the-room, because `VALOR_SESSION_ID` is unset. Nine senders use this door or a copy of it.

**Current behavior:**
- Reflections post raw, machine-authored text (IDs, counts, shell commands, status digests) to `Eng:` groups through `valor-telegram send`.
- When the expectation reconciler finds a dead lane whose work is visible and cannot steer a PM, it pages humans with the PM's instruction. It "can't" steer because the holder is recorded as the literal `"pm"` and the owner lookup crashes on every tick.
- The reconciler's evidence and liveness reads are unreliable:
  - A closed, unmerged PR counts as "shipped".
  - `owner='dev'` probes the unrelated `session/dev` branch.
  - `_owner_rows` logs `Invalid filter parameters: agent_session_id` on every tick. There are 145 such lines in `logs/reflection_worker_error.log`.

**Desired outcome:**
- Every Telegram message a human sees in an `Eng:`/client group is written by an agent session in the Valor persona and passes the session outbound pipeline (`TelegramRelayOutputHandler` → drafter → redundancy filter → read-the-room → outbox).
- A reflection that finds something hands a **structured finding to an agent**. The agent decides whether to act, fix, stay silent, or ask a human. If it asks, it names the decision in plain words.
- Status-only output never reaches human chat. It goes to the operator surface: the reflection `summary` on the dashboard, and the logs.
- The reconciler's reads are truthful. The 2026-09-29 incident replays to *silence in human chat* plus an agent discharging the expectation.

## Freshness Check

**Baseline commit:** `8b95a838a82e5e8e7a327780fc31d49121e1b460`
**Issue filed at:** 2026-09-29T09:27:33Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `reflections/utilities.py:428` `_send_telegram_transport`: still holds (send at :440; `send_eng_telegram` :463, `send_host_eng_telegram` :511).
- `reflections/expectation_reconciler.py:558-571` (evidence message): still holds (steer text built at :558, escalated verbatim at :570).
- `reflections/expectation_reconciler.py:313-354` `_shipped_evidence`, `:360-383` `_live_pm_session`, `:230-245` `_owner_rows`: all still hold.
- `tools/valor_telegram.py:728-753` `_should_run_rtr`: still holds.
- `agent/session_health.py:905-939`: operator-surface-only alarm, still holds.
- `tools/send_message.py:230-275`: still requires a real AgentSession.
- `reflections/improvement_assumption_digest.py:53-56` `CLOSING_LINE`: still holds. It is pinned to **charter §11** (see Open Questions).
- `tools/improvement.py:254-270`: charter-amendment page via `send_eng_telegram`, still holds.

**Cited sibling issues/PRs re-checked:**
- #2192, #2334, #3072, #2708, #2862: all closed. Their resolutions are the premises this plan builds on.
- #2494: still open (durability refactor). This plan does not touch its open phases.
- PR #3191 (routing by resolved `Eng:` group): merged. It fixed *where* pages go. This plan changes *who writes them*.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=2026-09-29T09:27:33Z` over `reflections/ tools/ agent/session_health.py scripts/memory_consolidation.py docs/features/expectation-reconciler.md` is empty.

**Active plans in `docs/plans/` overlapping this area:** none. `durability-room-job-agentrun.md` defines the Room/Job primitives this plan consumes but does not change them.

**Notes:** Recon under-counted one defect. `reflections/agents/system_health_digest.py:141` calls `AgentSession.create_and_enqueue(...)`, but that method does not exist anywhere in `models/`. Its anomaly path raises `AttributeError`, which the outer `except Exception: logger.exception` swallows. `tests/unit/test_sustainability.py:826` mocks the whole class, so the test stays green. The health digest has silently never reported an anomaly. This plan replaces that call with the new handoff primitive.

## Prior Art

- **#2192**: Nightly regression and Sentry triage. Added dedup, human-readable output, and auto-triage via AgentSession. Same principle as this plan, applied to two reflections one at a time.
- **#2334**: Nightly regression attempts an autonomous fix before paging ("alert = last resort"). Same principle, one reflection. Its guard test (`tests/unit/test_nightly_regression_tests.py:884-897`) bans `valor-telegram` in that one module.
- **#3072 / PR #3191**: Routes reflection alerts by resolved `Eng:` group. Fixed destination, not authorship.
- **#2754 / PR #3077**: docs-auditor routes notifications by audited repo root. Destination only.
- **#2497 / PR #2627**: Chatless reflection-session output goes to the system Room sink (`models/room.py` maps `chat_id="0"` to `system`). This is the precedent for a sink with no human audience.
- **#2708 / #2862**: Expectation reconciler and blocked-state annotation. These are the ladder this plan rewires.
- **#2717 / PR #2721**: Upvote pickup announces in Telegram, then anchors a session to the announcement.

## Research

No relevant external findings. This work is internal: routing, the Popoto ORM, and existing session plumbing. Proceeding with codebase context from three code-read spikes.

## Spike Results

### spike-1: Can a session created from reflection code carry a finding to humans through the persona pipeline?
- **Assumption**: "A session enqueued with a real Telegram `chat_id` delivers through `TelegramRelayOutputHandler`, and the agent may stay silent."
- **Method**: code-read
- **Finding**: Half true.
  - Routing holds. The worker registers one `TelegramRelayOutputHandler` per project (`worker/__main__.py:755-758`). `_resolve_transport` returns `telegram` for a nonzero numeric chat (`agent/output_handler.py:537-570`). `send()` runs drafter → redundancy → read-the-room → outbox (`:750-970`).
  - **Silence is impossible today.** An empty `[/complete]` triggers the wrap-up guard (`agent/session_runner/router.py:419`, `runner.py:1125-1131`). If the agent is still silent, the runner sends a fixed "I wasn't able to produce a response…" message (`runner.py:268-270, 2056-2059`).
  - Chatless sessions (`chat_id="0"`) route to the system Room (`output_handler.py:572-611`). Nothing reads that Room: `bridge/room_inbox.py:1-15` says so, and the dashboard never renders it.
  - Read-the-room skips `is_sdlc` sessions (`bridge/read_the_room.py:487-497`). Its "suppress" verdict falls through to a real send when there is no anchor message (`output_handler.py:1204-1223`).
- **Confidence**: high
- **Impact on plan**: The handoff session is bound to a real Room chat, so it has authority and a voice. The plan adds a narrow **silent-completion** rule for reflection-handoff sessions (no human asked them anything, so an empty completion is legitimate). The system Room is not treated as an operator surface.

### spike-2: Disposition of every sender
- **Assumption**: "Most side-door senders are status-only."
- **Method**: code-read
- **Finding**: Confirmed. See the disposition table in Solution. Other findings:
  - `stall_advisory`'s Telegram path is already off in production (`stall_advisory_telegram_enabled: false`).
  - `sentry_triage` runs as a cloud routine where `valor-telegram` is absent, so its page silently fails.
  - `sdlc_upvote_lanes` uses the announcement's message id as the session's reply anchor.
  - `tools/improvement.py` charter-amendment is a genuine human decision; only Tom may authorize it (charter §12).
  - Raw `telegram:outbox:*` writers that fail the bar (`pm_briefings`, `email_cs._ping_human`) are split to #3589.
- **Confidence**: high
- **Impact on plan**: Every sender gets a recorded disposition. One shared primitive replaces `send_eng_telegram`/`send_host_eng_telegram`.

### spike-3: Why the reconciler cannot find a PM, and what its evidence really says
- **Assumption**: "The owner/holder records can resolve to a live PM bound to the Job."
- **Method**: code-read plus a read-only ORM inspection of live Jobs
- **Finding**:
  - `agent_session_id` is a property over `id` (`models/agent_session.py:1522-1532`), so Popoto rejects it as a filter. The correct lookup is `AgentSession.get_by_id` (`:1187`).
  - All 5 open outbound expectations were PM-authored through `job_tool expectation-add`. That tool never passes `holder`, so every holder is the literal `"pm"`. That makes `_live_pm_session` fall back to *the most recent live eng session in the project*, which is unrelated to the Job.
  - Owners are Agent-tool agentIds (17 hex characters) or the bare placeholder `dev`. An agentId resolves to its parent PM row through `AgentSession.dev_agent_id == "agent-" + owner` (`models/agent_session.py:315`, captured at `agent/session_runner/runner.py:2127-2146`). `dev` resolves to nothing.
  - `_shipped_evidence` requests only `number,state` and takes `prs[0]`. `gh` exposes `state` (OPEN/MERGED/CLOSED) and `closingIssuesReferences`.
  - AgentSession has no `job_id` field. Room binding is derived: `room_id_for_session` (`models/room.py:98-103`). `job_tool` is room-scoped at the tool layer (`tools/job_tool.py:68-92`), so only a session *in the Job's Room* can discharge.
  - Nothing in `/do-merge` or the lane flow reports completion to a Job.
- **Confidence**: high
- **Impact on plan**: Fixes owner resolution and holder recording. Scopes PM targeting to the Job's Room. Creates the handoff session **in the Job's Room**, so it holds the tool-layer authority to discharge. Returns typed evidence.

## Data Flow

**Today (the incident):**
1. The `expectation-reconciler` reflection ticks (every 30 minutes) and finds an open outbound expectation on Job `4d7328d9…` (room `valor|telegram:<Eng: Valor id>`).
2. `_owner_rows` → owner `a2c69feea4c09332f` → no rows (the `agent_session_id` filter raises; `session_id` and `slug` miss) → "gone".
3. `_shipped_evidence` → `PR #3586 (merged)`.
4. `_live_pm_session(project, holder="pm")` → no holder match → falls back to the newest live eng session, or none.
5. The steer fails or there is no PM → `_escalate_once` → `send_eng_telegram` → `valor-telegram send` → `telegram:outbox:cli-<epoch>` → relay → **Eng: Valor**. No persona, no drafter, no read-the-room.

**After:**
1. The reflection builds a `Finding` with these fields:
   - source reflection
   - project
   - target Room: the Job's `room_id`, or the project's `Eng:` Room for host-level findings
   - optional `job_id` / `expectation_id`
   - plain-language facts
   - typed evidence
   - the suggested action
   - dedup key
2. `reflections/agent_handoff.py::hand_off(finding)`:
   - a. **Steer**: a live, non-ledger session *whose Room is the target Room*. The Job's recorded holder is preferred. `steer_session` drains it at the next turn.
   - b. **Else create**: enqueue an eng session in the target Room's chat. It gets `extra_context={"origin": "reflection_handoff", "handoff_source": ..., "job_id": ...}` and a brief rendered from the finding.
   - c. **Else (worker/enqueue failure)**: operator surface only (warning log, reflection finding/summary). **Never** a human page.
3. The agent (a PM in that Room, so `job_tool` room scope passes) reads the evidence, then:
   - discharges (`job_tool expectation-remove`), re-owns (respawns the lane), fixes, or stays silent (the silent-completion rule), or
   - asks a human. That output goes through `TelegramRelayOutputHandler.send` → drafter → redundancy → read-the-room → outbox → relay → the Room's chat.
4. The reflection records `handed-off: <kind> <session_id>` in its findings/summary, which appears on the dashboard `output_summary`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2192, #2334 | Made one or two reflections human-readable or agent-first | Fixed per module. The shared side door (`send_*_telegram`) stayed available, so every new reflection reached for it. |
| PR #3191 (#3072) | Centralized *destination* resolution into `send_eng_telegram` | Made the side door **easier** to use correctly-addressed, and gave five modules one convenient raw pipe to humans. |
| #2708 reconciler escalation | "Escalate once" when no PM is steerable | Treated "no reachable agent" as a reason to page a human. Liveness reads were broken (holder `"pm"`, bad filter), so this fired on finished work. |

**Root cause pattern:** non-session code owns human-facing prose, and there is no sanctioned way for it to reach an agent. Each fix improved the prose or the address, but never removed the capability.

## Architectural Impact

- **New module**: `reflections/agent_handoff.py` is the one sanctioned exit from reflection code toward humans. It is agent-mediated, and nothing in it writes to a chat.
- **Removed interface**: `send_eng_telegram`, `send_host_eng_telegram`, `_send_telegram_transport` (`reflections/utilities.py`), `docs_auditor._send_telegram_notification`, the `valor-telegram` subprocess in `scripts/memory_consolidation.py` and `reflections/sdlc_upvote_lanes.py`, and `stall_advisory`'s Telegram branch together with its `stall_advisory_telegram_enabled` param. There is no flag and no parallel path.
- **Kept**: `resolve_eng_group` (the handoff uses it to find a project's `Eng:` Room). `resolve_host_eng_chat` / `FALLBACK_ENG_CHAT` are deleted if no caller remains after the cut (the handoff needs a numeric chat id, not a name).
- **Runner change**: sessions carrying `extra_context["origin"] == "reflection_handoff"` may complete silently. An empty `[/complete]` ends the run with no wrap-up turn and no fallback message, and the output is logged. Every other session is unchanged.
- **job_tool change**: outbound `expectation-add` records `holder` = the calling session's `agent_session_id`, and rejects reserved placeholder owners (`dev`, `pm`).
- **Coupling**: reflections stop depending on the `valor-telegram` binary and on `Eng:` chat naming. They start depending on the Room model (`models/room.py`), which the reconciler already uses.
- **Reversibility**: moderate. Deleted senders can be restored from git, but the no-parallel-path rule means there is no toggle.

## Appetite

TBD

## Prerequisites

TBD

## Solution

TBD

## Failure Path Test Strategy

TBD

## Test Impact

- [ ] `tests/unit/reflections/test_reflections_expectation_reconciler.py` — UPDATE: escalation/steer paths (skeleton; detailed list follows)

## Rabbit Holes

TBD

## Risks

TBD

## Race Conditions

TBD

## No-Gos (Out of Scope)

TBD

## Update System

TBD

## Agent Integration

TBD

## Documentation

- [ ] Update `docs/features/expectation-reconciler.md` (skeleton; detailed list follows)

## Success Criteria

TBD

## Team Orchestration

TBD

## Step by Step Tasks

TBD

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

TBD
