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
- Status-only output never reaches human chat. It goes to the operator surface: the reflection `summary` on the dashboard, and the logs. The one named exception is the charter §11 assumption digest (see the disposition table and Open Question 1), which only Tom can move.
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
   - a. **Ownership gate**: `machine_owns_project(finding.project.get("slug"))` is False → `unreachable("not-owner")`.
   - b. **Steer**: a live session in the target Room that is either the Job's recorded holder or itself a handoff session (`extra_context.origin == "reflection_handoff"`). Never an arbitrary human conversation or SDLC lane PM. `steer_session` drains it at the next turn.
   - c. **Else create**: enqueue an eng session in the target Room's chat through `_push_agent_session(idempotency_key=...)`. It gets `extra_context={"origin": "reflection_handoff", "handoff_source": ..., "job_id": ...}` and a brief rendered from the finding.
   - d. **Else (not-owner, rate-capped, worker/enqueue failure)**: operator surface only (warning log, reflection finding/summary). **Never** a human page.
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
- **Runner change**: sessions carrying `extra_context["origin"] == "reflection_handoff"` may complete silently. An empty final reply ends the run under a new clean `ExitReason.HANDOFF_SILENT` with no wrap-up turn and no fallback message, and it finalizes `completed`. Every other session is unchanged.
- **job_tool change**: outbound `expectation-add` records `holder` = the calling session's `agent_session_id`, and rejects reserved placeholder owners (`dev`, `pm`).
- **Coupling**: reflections stop depending on the `valor-telegram` binary and on `Eng:` chat naming. They start depending on the Room model (`models/room.py`), which the reconciler already uses.
- **Reversibility**: moderate. Deleted senders can be restored from git, but the no-parallel-path rule means there is no toggle.

## Appetite

**Size:** Large

**Team:** Solo dev (builder lanes), PM, code reviewer

**Interactions:**
- PM check-ins: 1-2. Two decisions need Tom: the charter §11 digest disposition, and the open questions below.
- Review rounds: 1-2. The change crosses reflections, runner, `job_tool`, and PM priming.

## Prerequisites

No prerequisites. All of this work uses existing Redis, the Popoto models, and the worker. It needs no new secrets or services.

## Solution

### Key Elements

- **Agent handoff primitive** (`reflections/agent_handoff.py`): the only way reflection-side code gets anything in front of a human. It takes a structured `Finding` and reaches an agent in the right Room. It steers a live session bound to that Room if there is one; otherwise it creates one. It never writes to a chat itself. If it cannot reach an agent, it reports that on the operator surface and never pages a human.
- **Silent completion for handoff sessions**: a session created by a handoff has no human question to answer, so completing with nothing to say is a valid outcome. For these sessions the runner skips the wrap-up nag and the "I wasn't able to produce a response" fallback.
- **Truthful reconciler reads**:
  - Owner resolution uses `get_by_id`, `session_id`, `slug`, and the `dev_agent_id` parent link.
  - PM targeting is scoped to the Job's Room.
  - `_shipped_evidence` returns typed evidence: merged / open / closed-unmerged / branch-only, plus the issues each PR closes.
- **Expectation records that stay resolvable**: `job_tool expectation-add --direction outbound` records the calling session as `holder` and rejects the placeholder owners `dev` and `pm`. PM priming tells the PM to record the agentId the Agent tool returns.
- **Sender cut-over**: every sender below moves to its recorded disposition in the same PR, and the old senders are deleted.
- **Guard test**: an AST scan fails on any `valor-telegram` / `tools.valor_telegram` subprocess or `send_*_telegram` call under `reflections/`, `scripts/`, or `tools/improvement.py`. It is proven red against the baseline commit.

### Sender dispositions (AC: each remaining sender has a recorded disposition)

| Sender | Today | Disposition | After |
|---|---|---|---|
| `expectation_reconciler` T2 (shipped evidence, no PM) | PM instruction pasted to humans | **agent-handled** | `hand_off` to the Job's Room with typed evidence; the agent discharges or re-owns |
| `expectation_reconciler` T1 (attempts exhausted) / T3 (no PM, no slug) | "Needs a human." with IDs | **agent-handled** | `hand_off` stating "recovery budget spent" / "no respawnable slug". The agent asks the human, in the persona, only if a decision is really needed |
| `sdlc_progress` "auto-resume disabled" | page | **operator surface** | finding + summary only (it is config state) |
| `sdlc_progress` "budget exhausted" / "resume failed" | page with raw error | **agent-handled** | `hand_off` to the lane project's `Eng:` Room with the lane, PR, issue and error as evidence |
| `sentry_triage` digest | host page (fails silently in the cloud routine) | **operator surface** | summary/log only; Class C/D items already become GitHub issues |
| `stall_advisory` | host page (already disabled) | **operator surface** | delete the Telegram branch and the `stall_advisory_telegram_enabled` param; dashboard events stay |
| `improvement_assumption_digest` | host page, "asks nothing" | **persona path, verbatim (ships unless Tom answers Open Question 1 otherwise)** | `hand_off` of the digest to the valor `Eng:` Room. The brief carries the rendered digest as verbatim text the session must deliver unchanged as a status report per charter §11; no rewording, no silence. The full text also goes in the reflection summary/log. This is the one named exception to "status-only never reaches human chat", held because charter §11 mandates it and only Tom can amend it |
| `tools/improvement.py` charter amendment | raw page | **persona path** | `hand_off` of the amendment request; the agent asks Tom plainly for authorization (charter §12) |
| `docs_auditor` zero-diff / withheld fixes | page | **operator surface** | summary only; the withheld fixes are already filed issues |
| `docs_auditor` PR opened | page "Review required" | **agent-handled** | `hand_off` to the audited repo's `Eng:` Room; the agent reviews and escalates only if a human merge decision is needed |
| `memory_consolidation._flag_contradiction` | raw memory IDs to chat | **operator surface** | keep the `logs/memory-contradictions.log` write (it becomes the primary path) and add a count to the reflection summary |
| `sdlc_upvote_lanes` announce | raw `--no-read-the-room` post, anchor for the session | **agent-handled** | create the eng session in the `Eng:` Room first, with no anchor; its brief says to announce the pickup in its first message, which goes through the persona path |
| `sdlc_upvote_lanes` retractions | raw post with raw error | **operator surface** | finding + summary |
| `agents/system_health_digest` | calls nonexistent `AgentSession.create_and_enqueue`; the brief forces "send via valor-telegram to Eng: Valor" | **agent-handled** | `hand_off` of the anomalies to the host `Eng:` Room; drop the forced send and the chat name |
| `pm_briefings`, `email_cs._ping_human` (raw outbox) | raw outbox | out of scope | #3589 |

### Operator surface: who reads it

"Operator surface" means the reflection's `summary` and findings, rendered in the reflections panel of the dashboard (`localhost:8500`, `dashboard.json`), plus the reflection's log. Its reader is the operator (Tom or an ops agent session) checking system state on demand; nothing pushes it. Rows that move there are ones where no decision exists: config state, already-filed issues, and log-only records.

### Behavior Tom will notice

- The assumption digest arrives written by an agent session in the Valor persona instead of the raw host page. The text, including the closing line, is unchanged.
- No more memory-contradiction pings with raw memory IDs. Contradictions are in `logs/memory-contradictions.log` and counted in the consolidation summary.
- No more "auto-resume disabled" pings. That state is on the dashboard.
- No more Sentry-triage digest pings (they were already failing silently in the cloud routine) and no docs-auditor "zero-diff" / "fixes withheld" pings.
- The upvote pickup announcement becomes the lane session's first message, written in the persona, instead of a raw post; later replies are no longer threaded under a separate announcement.
- Reconciler messages with Job UUIDs and shell commands stop. When a human decision is needed, an agent asks it in plain words.
- Projects with no `Eng:` group get no human-facing handoff at all; their findings are dashboard-only (Open Question 4).

### Flow

**Reflection finds something** → `hand_off(Finding)` → **live session in the target Room?** → steer it → the agent decides at its next turn. If there is no live session: create a handoff session in the Room → the agent acts / discharges / stays silent → **only if a human decision is needed** → the persona message goes through `TelegramRelayOutputHandler` → the human sees a plain-words ask.

### Technical Approach

- **`Finding` and `hand_off`** (`reflections/agent_handoff.py`):
  - `Finding` is a frozen dataclass with these fields: `source`, `project` (the dict), `room_id`, `facts: list[str]`, `evidence: dict`, `suggested_action: str`, `job_id: str | None`, `expectation_id: str | None`, `holder: str | None`, `dedup_key: str`.
  - `hand_off(finding) -> HandoffResult(kind: "steered" | "created" | "unreachable", session_id: str | None, reason: str | None)`.
  - `HandoffResult.kind` also carries `"rate-capped"` for the per-source cap below. Only `steered` and `created` count as delivered.
  - It never raises. It renders the brief with a fixed framing: this came from a scheduled reflection; no human asked; judge the evidence; act, fix, or stay silent; if a human decision is needed, say which decision in plain words, with no internal IDs or commands. The facts and evidence follow as data.
- **Ownership gate** (first thing `hand_off` does): `if not machine_owns_project(finding.project.get("slug")): return HandoffResult("unreachable", None, "not-owner")`. The project dict's key is `slug` (the reconciler reads `project.get("slug")` at `reflections/expectation_reconciler.py:223,436`). Host-level findings (digest, health anomalies, charter amendment) target the valor project, so they inherit valor's owning machine and only one machine ever creates them. This closes the gap for `docs_auditor`, `system_health_digest`, and `tools/improvement.py`, which do not gate on ownership today.
- **Room targeting**:
  - A Job-scoped finding targets `job.room_id`.
  - A project-level finding targets `room_id(project_key, "telegram:<chat_id>")`, with the id from `resolve_eng_group(project)`.
  - A `system` or non-Telegram Room, or a project with no `Eng:` group, cannot carry a human-facing ask. `hand_off` returns `unreachable("no-human-room")` and the caller records an operator finding. Email Rooms stay unsupported; the reconciler only sees Telegram Rooms today.
- **Steer target** (`_live_session_in_room`):
  - Candidates are non-terminal, non-ledger eng AgentSessions with `room_id_for_session(r) == room_id` **and** either `r.id == holder_id` (the Job's recorded holder, matched by `get_by_id` or `session_id`) or `(r.extra_context or {}).get("origin") == "reflection_handoff"`.
  - A live human conversation or an SDLC lane PM that merely shares the Room is never a candidate. When no candidate exists, `hand_off` creates, so a machine finding never becomes the next reply in someone else's conversation.
  - Preference order: the holder; then the newest handoff-origin session.
  - No project-wide fallback, which meets the AC "never steers a session unrelated to the Job".
  - Silent completion is claimed only for handoff-origin sessions. A steered holder is the PM that authored the expectation and is mid-work on it; the steer text tells it the finding needs no human-facing mention unless a decision is required, and its ordinary turn-end rules apply.
- **Create**:
  - Enqueue through `agent.agent_session_queue._push_agent_session` (the same core `tools/valor_session.create_session` uses), with the chat id parsed from the Room, `session_type="eng"`, `priority="low"`, `telegram_message_id=0`, `extra_context_overrides={"origin": "reflection_handoff", "handoff_source": source, "job_id": ..., "expectation_id": ...}`, and `idempotency_key=f"handoff:{source}:{room_id}:{dedup_key}"`.
  - The idempotency key is single-winner (`agent/agent_session_queue.py:246,428-445`; `agent/enqueue_idempotency.py` binds with a 24h TTL). Every racing caller gets the bound session id back, and a winner that died before the row save is covered by the loser creating under the same preallocated id. No raw-Redis claim is added.
  - Low priority shares the per-chat queue (`agent_session_queue.py:253-254`). The builder confirms a pending low-priority handoff session does not hold a human's new message behind it in that chat; if it does, the human message's normal priority must win the ordering, and a test asserts it.
  - The builder checks whether the executor's synthetic slug for slugless eng sessions (`agent/session_executor.py:1413-1417`) provisions a worktree for these sessions. A handoff session that only reads evidence and runs `job_tool` must not cost a worktree. If it does, gate the synthetic slug on `origin != "reflection_handoff"`.
- **Dedup and rate cap**:
  - `Finding.dedup_key` is required (non-empty) for every caller, including the new ones (`docs_auditor`: repo root + PR number; `system_health_digest`: sorted anomaly kinds + date; `tools/improvement.py`: amendment id; `sdlc_progress`: slug + head SHA; digest: digest date). Through the idempotency key it caps each distinct finding at one created session per 24h.
  - Per-source cap: `hand_off` counts live handoff-origin sessions for `finding.source` through the ORM (`AgentSession.query.filter(project_key=...)`, then a client-side `extra_context` check) and returns `rate-capped` when the count reaches `HANDOFF_MAX_LIVE_PER_SOURCE` (named, env-overridable module constant, provisional default with a grain-of-salt comment). A rate-capped finding goes to the operator surface.
  - The reconciler keeps its `(job, eid)` cooldown and attempts keys. Its sentinel (~:215) now means "handed off once at the exhausted rung" and is written **only** when `HandoffResult.kind in ("steered", "created")`. An `unreachable` or `rate-capped` result leaves the sentinel unset and relies on the cooldown key, so a transient failure retries on a later tick instead of being suppressed forever, and a persistent failure cannot retry every tick.
- **Silent completion** (`agent/session_runner/router.py`, `agent/session_runner/runner.py`):
  - Add `ExitReason.HANDOFF_SILENT = ("handoff_silent", True, False, False)` (clean, not wrap-up eligible, not an anomaly) to the table at `router.py:~419-440`.
  - A fully empty reply today is `ExitReason.PM_EMPTY_TURN` (`runner.py:1082-1088`), which is non-clean and finalizes `failed` (`agent/session_executor.py:144-156`); an empty `[/complete]` is a different path. Both must be covered. At the PM_EMPTY_TURN branch and at `wrapup_trigger` (`runner.py:1125-1131`), when `(self._agent_session.extra_context or {}).get("origin") == "reflection_handoff"`, set `summary.exit_reason = ExitReason.HANDOFF_SILENT` and skip the wrap-up guard and the fallback text (`runner.py:268-270, 2056-2059`). An empty `[/complete]` in a handoff session maps to the same reason.
  - The #2420 fail-closed downgrade (`runner.py:~1155-1165`) still applies: `HANDOFF_SILENT` is clean, so a silent exit with a subagent in flight is rewritten to `PM_USER_SUBAGENT_LIVE` like any clean exit.
  - One INFO log `handoff-silent <source> <session_id>` per silent completion. The rule keys on the explicit origin marker and nothing else.
  - Tests assert a silent handoff session finalizes `completed`, and a non-handoff empty turn still finalizes as today.
- **Reconciler rewrite** (`reflections/expectation_reconciler.py`):
  - `_owner_rows` uses `get_by_id(owner)`, `session_id`, `slug`, and `dev_agent_id == "agent-" + owner`. `dev_agent_id` is a plain Field (`models/agent_session.py:315`), so the query always pairs `dev_agent_id=` with `project_key=` (Popoto's client-side filter needs an indexed param alongside it). For an Agent-tool subagent, the parent PM row stands in as the owner's liveness claim.
  - `_lane_slug` returns `None` for the reserved placeholders `dev` and `pm`, so there is no `session/dev` probe and no respawn with slug `dev`.
  - `_shipped_evidence` returns a `ShippedEvidence(kind, pr_number, closes_issues, branch)` dataclass or `None`, from `gh pr list --head <branch> --state all --json number,state,closingIssuesReferences`. `kind` is picked in the order merged > open > closed-unmerged > branch-only. `closed_unmerged` does **not** count as shipped and does not block respawn by itself. It goes into the handoff as evidence ("PR #N was closed without merging") and the agent decides; no auto-respawn over a deliberately closed PR.
  - `_live_pm_session` is replaced by `agent_handoff._live_session_in_room(job.room_id, holder)`.
  - Every former `_escalate_once` site becomes a `hand_off`.
  - The `attempts_exhausted` annotation order is kept (hand off first, then annotate), with the same rationale: a handoff that failed must not be masked.
- **`job_tool`** (`tools/job_tool.py`):
  - Outbound `expectation-add` passes `holder=<caller agent_session_id>` into `Job.add_expectation`.
  - It refuses `--owner` values in `{"dev", "pm"}` with a loud `JobToolError` that says to record the lane slug or the agentId the Agent tool returned.
  - `.claude/commands/roles/prime-pm-role.md:70` is updated to match.
- **Removals**:
  - `send_eng_telegram`, `send_host_eng_telegram`, `_send_telegram_transport`
  - `resolve_host_eng_chat` and `FALLBACK_ENG_CHAT`, if unused after the cut. `docs_auditor._resolve_notify_chat` then resolves a project dict instead.
  - `docs_auditor._send_telegram_notification`
  - the `memory_consolidation` subprocess
  - the `sdlc_upvote_lanes` `_run_send` / `_announce` / `_retract` and `telegram_message_id` anchoring
  - the `stall_advisory` Telegram branch
- **Existing-row repair**: none. Existing rows with holder `"pm"` and owner `dev` are handled at read time. Holder `"pm"` matches no row, so targeting falls to the Room's newest live session or creates one. Owner `dev` means no slug, so no respawn, and the finding is handed off with "owner unrecorded" evidence. Popoto schemas do not change (no new fields), so no migration.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `hand_off` never raises. Each boundary (Room resolution, session query, `steer_session`, `_push_agent_session`) is tested to return `unreachable` with a `reason` and a `logger.warning`. Tests assert the warning and the `reason`, not just "didn't crash".
- [ ] The reconciler's per-expectation `except Exception` keeps its test. A test also asserts that an `unreachable` handoff leaves an operator finding (`handoff-unreachable: <eid> <reason>`) and that no chat write happens.
- [ ] `_shipped_evidence` `gh` failure: the test asserts `None` plus a warning, and that the reconciler then does **not** respawn. A failed evidence read must not look like "no evidence".

### Empty/Invalid Input Handling
- [ ] `Finding` with empty `facts`, or `room_id` of `system` / an email Room / an unparseable value → `unreachable("no-human-room")`.
- [ ] `job_tool expectation-add --owner dev|pm|""` → loud refusal.
- [ ] A handoff session completing with an empty `[/complete]` → silent completion, no fallback text. A **non**-handoff session with the same empty completion still gets the wrap-up and fallback (regression guard).

### Error State Rendering
- [ ] When the handoff agent decides a human is needed, the message reaches the human through `TelegramRelayOutputHandler.send`. An AI judge (not keywords) scores it on human readability, a named decision, and the absence of UUIDs and shell commands.
- [ ] When no agent can be reached, the reflection `summary` (visible on the dashboard) says so in words. It is not silently equivalent to "handed off".

## Test Impact

- [ ] `tests/unit/reflections/test_reflections_expectation_reconciler.py`: REPLACE the escalation-shaped tests (`test_successful_steer_never_escalates`, `test_attempt_cap_escalates_once_then_stops`, the no-`Eng:`-group suppression test, and the `_escalate_once` monkeypatches) with `hand_off` assertions. ADD owner resolution by agent-session id, `dev_agent_id`, and reserved placeholders; typed `_shipped_evidence` kinds; Room-scoped targeting; and the incident replay.
- [ ] `tests/unit/reflections/test_reflections_utilities_eng_chat.py`: DELETE the `send_eng_telegram` / `send_host_eng_telegram` / `_send_telegram_transport` cases. UPDATE any `resolve_host_eng_chat` cases (DELETE if the function is removed).
- [ ] `tests/unit/reflections/test_reflections_utilities_resolve_eng_group.py`: no change; `resolve_eng_group` stays.
- [ ] `tests/unit/reflections/test_reflections_progress_check.py` and `tests/integration/test_sdlc_stall_auto_resume_e2e.py`: UPDATE the `_send_alert` / `send_eng_telegram` assertions to `hand_off` (budget exhausted / resume failed) and to the operator finding (disabled).
- [ ] `tests/unit/test_sentry_triage_apply.py`: UPDATE by removing the Telegram-notification assertions and asserting the summary carries the digest.
- [ ] `tests/unit/reflections/test_stall_advisory_reflection.py` (lines ~179-222) and `tests/integration/test_stall_advisory_e2e.py`: DELETE the `stall_advisory_telegram_enabled` cases. UPDATE to assert no Telegram path exists.
- [ ] `tests/unit/test_reflection_scheduler.py:1425-1479`: UPDATE the param-passthrough fixtures to a neutral param name (they use `stall_advisory_telegram_enabled` only as an example).
- [ ] `tests/unit/test_improvement_assumption_digest.py`: UPDATE the default-sender assertions to the `hand_off` path. `CLOSING_LINE` stays pinned.
- [ ] `tests/unit/test_improvement_investigations.py` (~:360, propose-amendment): UPDATE "Tom notified" to the handoff result.
- [ ] `tests/unit/test_docs_auditor_substrate.py` and `tests/unit/reflections/test_reflections_docs_auditor_git_surface.py`: UPDATE the `_send_telegram_notification` assertions to the summary (zero-diff) or `hand_off` (PR opened).
- [ ] `tests/unit/test_memory_consolidation.py`: UPDATE the `_flag_contradiction` tests. The log write is now the primary path and no subprocess runs.
- [ ] `tests/unit/reflections/test_reflections_upvote_lanes.py`: REPLACE the announce/anchor/retract tests with "session created in the `Eng:` Room with an announce-first brief, no anchor, retraction as a finding".
- [ ] `tests/unit/test_sustainability.py:826-907`: REPLACE the `create_and_enqueue` mock (the method does not exist) with a `hand_off` assertion. ADD a test that fails if the anomaly path calls an attribute the model lacks (use the real class, not a mock).
- [ ] `tests/unit/test_job_tool.py`: UPDATE `expectation-add` for the recorded holder. ADD the reserved-owner refusal.
- [ ] `tests/unit/session_runner/test_runner_turns.py`: ADD silent completion for `origin=reflection_handoff`, plus the unchanged-behavior regression for other sessions.
- [ ] `tests/unit/test_nightly_regression_tests.py:884-897`: no change. The new guard generalizes it and both stay.

## Rabbit Holes

- **Rebuilding the dashboard to render the system Room inbox.** The operator surface for status-only output is the reflection `summary` and the logs, which the dashboard already shows. A Room-inbox viewer is a separate feature.
- **Making read-the-room suppress without an anchor, or stop skipping SDLC sessions.** Both gaps are real (spike-1), but silent completion makes the handoff session's silence explicit rather than relying on read-the-room.
- **Reworking mechanical discharge.** The durability model's "nothing mechanical discharges" rule stays (Open Question 2). This plan only guarantees that an agent is always reachable to author the discharge.
- **Auto-discovering the Job for Agent-tool subagents through hooks.** Recording the agentId at `expectation-add` time plus the `dev_agent_id` link is enough; a SubagentStart hook that writes expectations is a larger design.
- **Per-project `sentry_triage` / `stall_advisory` redesigns.** They go to the operator surface as they are.

## Risks

### Risk 1: Handoff sessions spam the Eng group
**Impact:** Swapping raw pages for many agent posts is still noise.
**Mitigation:**
- Silent completion is the default outcome the brief asks for.
- Every finding carries a required `dedup_key`; the enqueue idempotency key caps each distinct finding at one created session per 24h.
- `HANDOFF_MAX_LIVE_PER_SOURCE` caps concurrent live handoff sessions per source.
- Caller dedup keys stay: the reconciler's per-(job, eid) cooldown and sentinel, and `sdlc_progress`'s per-(slug, sha) sentinel.
- The drafter's redundancy filter applies.
- The reflection summary carries `handoff-steered` / `handoff-created` / `handoff-silent` / `handoff-unreachable` counts, so session cost is visible on the dashboard.
- The incident-replay test asserts zero human-chat writes for a delivered-work case.

### Risk 2: A created handoff session collides with a human conversation in the same Room
**Impact:** Two sessions answer in one chat.
**Mitigation:** A live human conversation is never a steer target (only the Job's holder or a handoff-origin session is). A created handoff session runs at low priority and completes silently unless a decision is needed; when it does speak, the drafter's redundancy filter and read-the-room apply. The builder confirms and tests that the low-priority session does not delay a human's message in the same chat.

### Risk 3: Silent completion masks a genuinely broken handoff session
**Impact:** A session that crashed or produced nothing looks like a deliberate silence.
**Mitigation:**
- Silent completion requires a clean `[/complete]` routing. A harness failure still takes the existing failure path.
- Each silent completion logs `handoff-silent <source> <session_id>`, which is countable on the operator surface.

### Risk 4: Upvote pickup loses its announcement anchor
**Impact:** The lane's replies are no longer threaded under a pickup message.
**Mitigation:** The session's first persona message *is* the announcement, and later replies follow the session's normal reply chain. The test asserts the brief requires announcing first.

### Risk 5: Charter §11 conflict
**Impact:** Removing the digest from Telegram would amend the charter without Tom's authorization (charter §12: only Tom authorizes amendments).
**Mitigation:** Default to the persona path with verbatim delivery, so the digest stays in Telegram with its charter-pinned `CLOSING_LINE` intact. A test asserts `CLOSING_LINE` appears byte-exact in the outbox payload. If the drafter rewrites it, the build must make the drafter pass the digest through unchanged for this origin, or stop and ask; it must not ship a reworded digest. A silent completion of a digest handoff is recorded as an operator failure (`handoff-digest-undelivered`), not success. The deviation from the issue's AC4 is named and raised as Open Question 1 rather than decided by fiat.

## Race Conditions

### Race 1: PM discharges while the reconciler hands off
**Location:** `reflections/expectation_reconciler.py`, from the per-expectation pass to `hand_off`
**Trigger:** The PM runs `expectation-remove` between the scan and the handoff.
**Data prerequisite:** The expectation must still be open when the handoff is issued.
**State prerequisite:** The Job must be re-fetched immediately before acting.
**Mitigation:** Keep the existing Race-3 re-fetch (`Job.query.get` plus the open check) directly before `hand_off`. A stale handoff is benign: the agent re-reads the Job with `job_tool show`, and the brief tells it to do that first.

### Race 2: Two ticks or two reflections create two sessions in one Room
**Location:** `agent_handoff.hand_off` create rung
**Trigger:** The reconciler and `sdlc_progress` both find the Room empty in the same instant.
**Data prerequisite:** none
**State prerequisite:** At most one handoff session per Room should be created per short window.
**Mitigation:** No new claim. `_push_agent_session(idempotency_key=f"handoff:{source}:{room_id}:{dedup_key}")` is single-winner: the second caller with the same key gets the bound session id back instead of creating a second row, including when the winner died between binding and saving the row (the loser creates under the same preallocated id). Two *different* findings for one Room may each create a session; that is intended, since they are different work, and the per-source cap bounds it. A later caller can also steer an existing handoff-origin session through `_live_session_in_room`, because pending is non-terminal.

### Race 3: The steer target turns terminal between selection and `steer_session`
**Location:** `hand_off` steer rung
**Trigger:** The session completes in between.
**Mitigation:** `steer_session` already rejects terminal sessions. On rejection, `hand_off` falls through to the create rung within the same call.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3589] Raw `telegram:outbox:*` writers that fail the same bar (`reflections/pm_briefings/*`, `tools/email_cs/handler.py::_ping_human`, `tools/send_message.py::_legacy_telegram_rpush`). #3588's recon dropped them. The new guard targets the `valor-telegram` side door, and #3589 widens it to raw outbox writes.
- [EXTERNAL] Amending charter §11 to take the assumption digest out of Telegram. Only Tom can authorize charter changes (charter §12). The plan defaults to the persona path and asks.

## Update System

- No new dependencies, services, secrets, or launchd plists.
- `config/reflections.yaml` and the vault copy `~/Desktop/Valor/reflections.yaml`: remove the `stall_advisory_telegram_enabled` param from the `stall-advisory` entry. `/update`'s `sync_reflections_yaml` (`scripts/update/env_sync.py:146`) copies the vault file into `config/`. The vault edit is a [ORDERED] step: first the code change that stops reading the param, then the vault edit. A stale key is ignored by the loader, so the order is safe either way.
- No Popoto schema change, so no migration. The worker must be restarted after deploy (`./scripts/valor-service.sh restart`) so the runner picks up silent completion and the reflections pick up `hand_off`. `/update` already does this.

## Agent Integration

- No new CLI or MCP tool. `hand_off` is an internal Python API that the reflections call inside the worker process.
- `tools/job_tool.py expectation-add` changes behavior: it records the caller as holder and rejects `--owner dev|pm`. `.claude/commands/roles/prime-pm-role.md` (~:70) must say to record the lane slug or the agentId returned by the Agent tool. The PM prime is injected by the headless runner, so no other registration is needed.
- Handoff sessions are ordinary eng sessions with `extra_context.origin = "reflection_handoff"`. They use the existing tools (`job_tool`, `gh`, `valor-session`). Their brief comes from `reflections/agent_handoff.py`.
- Integration test: `tests/integration/test_reflection_agent_handoff.py` (create) enqueues a real handoff session against a `test-` project key on the test Redis db. It asserts that the session row carries the origin marker and the Room's chat id, and that no `telegram:outbox:*` write happened at enqueue time.

## Documentation

- [ ] Create `docs/features/reflection-agent-handoff.md`: the `Finding` contract, the steer/create/unreachable ladder, Room targeting, silent completion, and the disposition table. Add it to `docs/features/README.md`.
- [ ] Update `docs/features/expectation-reconciler.md`: handoff instead of escalation, owner resolution, typed shipped evidence, Room-scoped targeting, and the reserved-owner rejection.
- [ ] Rewrite `docs/features/reflection-telegram-routing.md`. It describes the `send_eng_telegram` consumers being deleted, so it becomes a short page stating the rule "reflections never write to a human chat; see reflection-agent-handoff" plus the `resolve_eng_group` Room resolution. If nothing else remains, delete it and fold the rule into the new page, updating inbound links.
- [ ] Update `docs/features/message-drafter.md`: handoff sessions complete silently; the persona path is the only human-facing path.
- [ ] Update `docs/features/docs-auditor.md`: notification dispositions (summary vs. handoff).
- [ ] Update `docs/features/stall-advisory-classifier.md` and `docs/features/reflections.md`: remove the `stall_advisory_telegram_enabled` param.
- [ ] Update `.claude/commands/roles/prime-pm-role.md`: expectation-add owner/holder guidance.

## Success Criteria

- [ ] Incident replay: an outbound expectation owned by a placeholder `dev`, whose issue has a merged PR, produces **zero** human-chat writes from the reconciler. A handoff session in the Job's Room receives typed merged-PR evidence. (Maps to AC1 and AC2.)
- [ ] `_owner_rows` resolves an agent-session id, a `session_id`, a slug, and an Agent-tool agentId through `dev_agent_id`. `dev` and `pm` resolve to nothing and never spawn `session/dev`. (AC3)
- [ ] `_shipped_evidence` reports merged / open / closed-unmerged / branch-only correctly. Closed-unmerged is never treated as shipped. (AC3)
- [ ] Reconciler PM targeting never selects a session outside the Job's Room. (AC3)
- [ ] Every sender in the disposition table is moved, and the table is recorded in `docs/features/reflection-agent-handoff.md`. (AC4)
- [ ] `grep -rn "send_eng_telegram\|send_host_eng_telegram\|_send_telegram_transport" reflections/ scripts/ tools/` returns nothing.
- [ ] The guard test fails against baseline `8b95a838a` and passes on the branch. The red run is recorded in the PR body. (AC5)
- [ ] AI-judge test: a handoff-originated human-facing message scores as plain-words, names a decision, and contains no UUIDs or shell commands. (AC2)
- [ ] Handoff sessions complete silently. Non-handoff sessions keep the wrap-up nag and fallback message.
- [ ] `AgentSession.create_and_enqueue` has no remaining caller, and `system_health_digest` anomalies reach `hand_off`.
- [ ] `tests/unit/` and `tests/integration/` pass via `scripts/pytest-clean.sh`. Ruff is clean.
- [ ] Documentation updated (see Documentation).

## Team Orchestration

The lead orchestrates and never builds directly.

### Team Members

- **Builder (handoff)**
  - Name: handoff-builder
  - Role: `reflections/agent_handoff.py`, runner silent completion, handoff Redis claim
  - Agent Type: builder
  - Resume: true
- **Builder (reconciler)**
  - Name: reconciler-builder
  - Role: reconciler reads plus handoff wiring, `job_tool` holder/owner, PM prime
  - Agent Type: builder
  - Resume: true
- **Builder (senders)**
  - Name: senders-builder
  - Role: the sender cut-over in the disposition table, deletions, yaml param removal
  - Agent Type: builder
  - Resume: true
- **Test engineer (guards)**
  - Name: guard-tester
  - Role: AST guard test (proven red against baseline), incident replay, AI-judge test
  - Agent Type: test-engineer
  - Resume: true
- **Validator**
  - Name: plan-validator
  - Role: verifies every Success Criterion and Verification row
  - Agent Type: validator
  - Resume: true
- **Documentarian**
  - Name: handoff-docs
  - Role: the Documentation list
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Handoff primitive
- **Task ID**: build-handoff
- **Depends On**: none
- **Validates**: tests/unit/reflections/test_agent_handoff.py (create), tests/unit/session_runner/test_runner_turns.py, tests/integration/test_reflection_agent_handoff.py (create)
- **Informed By**: spike-1 (read-the-room skips SDLC; an unanchored suppress falls through), spike-2 (system Room is write-only)
- **Assigned To**: handoff-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data, async/concurrency
- **Parallel**: true
- Write `Finding`, `HandoffResult`, `hand_off`, `_live_session_in_room`, and the `handoff:create:{room_id}` claim.
- Enqueue through `_push_agent_session` with the `reflection_handoff` origin. Check the synthetic-slug worktree behavior (`agent/session_executor.py:1413-1417`) and gate it if needed.
- Add silent completion in `agent/session_runner/runner.py`, keyed on the origin marker.

### 2. Reconciler and job_tool
- **Task ID**: build-reconciler
- **Depends On**: build-handoff
- **Validates**: tests/unit/reflections/test_reflections_expectation_reconciler.py, tests/unit/test_job_tool.py
- **Informed By**: spike-3 (the owner/holder values in the incident rows)
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: true (with build-senders once build-handoff lands)
- Rewrite `_owner_rows`, `_lane_slug`, and `_shipped_evidence` (typed). Replace `_live_pm_session` and `_escalate_once` with `hand_off`.
- `job_tool expectation-add`: record the holder and reject reserved owners. Update `prime-pm-role.md`.

### 3. Sender cut-over
- **Task ID**: build-senders
- **Depends On**: build-handoff
- **Validates**: every test file listed under Test Impact except the reconciler and job_tool ones
- **Assigned To**: senders-builder
- **Agent Type**: builder
- **Parallel**: true
- Apply each row of the disposition table.
- Delete the `send_*_telegram` helpers, `_send_telegram_transport`, `docs_auditor._send_telegram_notification`, the memory_consolidation subprocess, the upvote-lanes send/anchor, and the stall_advisory Telegram branch plus its param. Remove `resolve_host_eng_chat` / `FALLBACK_ENG_CHAT` if no caller remains.
- Fix `system_health_digest` to call `hand_off`.
- Remove the param from `config/reflections.yaml`.

### 4. Guard and judge tests
- **Task ID**: build-guards
- **Depends On**: build-reconciler, build-senders
- **Validates**: tests/unit/test_no_reflection_telegram_side_door.py (create), tests/integration/test_reconciler_incident_replay.py (create), tests/integration/test_handoff_message_judge.py (create)
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- The AST guard over `reflections/`, `scripts/`, and `tools/improvement.py`. Run it against a checkout of `8b95a838a` to prove it red, and record the output.
- The incident replay and the AI-judge test.

### 5. Validate
- **Task ID**: validate-all
- **Depends On**: build-guards
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and check each Success Criterion.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: handoff-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Complete the Documentation checklist.

### 7. Final validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run Verification. Confirm there are no stale doc references (`grep -rn "send_eng_telegram" docs/features` is empty).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Reflection unit tests | `scripts/pytest-clean.sh tests/unit/reflections/ -q` | exit code 0 |
| Touched unit tests | `scripts/pytest-clean.sh tests/unit/test_job_tool.py tests/unit/test_sustainability.py tests/unit/test_memory_consolidation.py tests/unit/test_docs_auditor_substrate.py tests/unit/test_sentry_triage_apply.py tests/unit/test_improvement_assumption_digest.py tests/unit/test_improvement_investigations.py tests/unit/test_reflection_scheduler.py tests/unit/session_runner/test_runner_turns.py -q` | exit code 0 |
| Guard test | `scripts/pytest-clean.sh tests/unit/test_no_reflection_telegram_side_door.py -q` | exit code 0 |
| Integration | `scripts/pytest-clean.sh tests/integration/test_reflection_agent_handoff.py tests/integration/test_reconciler_incident_replay.py tests/integration/test_handoff_message_judge.py tests/integration/test_sdlc_stall_auto_resume_e2e.py tests/integration/test_stall_advisory_e2e.py -q` | exit code 0 |
| No side-door senders | `grep -rn "send_eng_telegram\|send_host_eng_telegram\|_send_telegram_transport" reflections/ scripts/ tools/` | exit code 1 |
| No valor-telegram in reflections | `grep -rn "valor-telegram\|tools.valor_telegram" reflections/ scripts/memory_consolidation.py tools/improvement.py` | exit code 1 |
| Dead method gone | `grep -rn "create_and_enqueue" reflections/ agent/ models/` | exit code 1 |
| Param removed | `grep -rn "stall_advisory_telegram_enabled" reflections/ config/ docs/features/` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Silent completion keys only on the origin marker of *created* sessions. The preferred rung (steering a live session) carries no marker, so a steered session that judges the finding needs no reply still gets the wrap-up nag and the "I wasn't able to produce a response" fallback (`runner.py:268-270`, `:1125-1131`). A fully empty reply is `ExitReason.PM_EMPTY_TURN` (`runner.py:1084-1088`), not an empty `[/complete]`, and the plan names no clean ExitReason, so a silent session would finalize "failed" (`session_executor.py:144-156`). | pending | Add a new `ExitReason` in `agent/session_runner/router.py` (~:419-440) with clean=True, wrapup=False, anomaly=False. Gate both the PM_EMPTY_TURN branch (`runner.py:1084`) and `wrapup_trigger` (`:1125`) on `(self._agent_session.extra_context or {}).get("origin") == "reflection_handoff"`. Cover the #2420 downgrade scope (`runner.py:1161`). For steers, either narrow steering to handoff-origin sessions (see the steer-target row) or drop silent-completion claims for steers. Test that a silent handoff session finalizes "completed", not "failed". |
| CONCERN | Risk & Robustness, History & Consistency | The raw-Redis `handoff:create:{room_id}` SET NX claim has holes. A loser arriving between claim and row save finds neither a live session nor a free claim, and its behavior is undefined. A claim followed by a failed enqueue blocks the Room for the TTL. The reconciler's "handed off once" sentinel is written even when the result is `unreachable`, so a transient failure suppresses the finding permanently. `_push_agent_session` already has single-winner `idempotency_key` (`agent/agent_session_queue.py:246`, `:428-431`). | pending | Drop the raw claim. Pass `idempotency_key=f"handoff:{source}:{room_id}:{dedup_key}"` to `_push_agent_session`; every caller gets the bound session id back. In `expectation_reconciler.py` set the sentinel (~:215) only when `HandoffResult.kind in ("steered", "created")`, and keep the cooldown key so a failing handoff cannot retry every tick. Rewrite Race 2 accordingly. |
| CONCERN | Risk & Robustness | `_live_session_in_room` accepts any non-terminal, non-ledger eng session in the Room, including one carrying a live human conversation or an SDLC lane PM. The machine finding then surfaces as that session's next reply to the human, which undoes the plan's core promise and the Risk 2 mitigation. A low-priority handoff session also shares the per-chat queue (`agent_session_queue.py:253-254`), so it can hold up a real human message in that chat. | pending | Filter steer candidates to `(r.extra_context or {}).get("origin") == "reflection_handoff"` or `r.id == holder_id` (the Job's recorded holder). Otherwise create. `dev_agent_id` is a plain Field (`models/agent_session.py:315`), so always pair `dev_agent_id=` with `project_key=` in `_owner_rows` (client-side filter needs an indexed param). Add `handoff-steered` / `handoff-created` / `handoff-silent` counts to the reflection summary. |
| CONCERN | History & Consistency | `hand_off` never gates on machine ownership. Reflections that act on a project call `machine_owns_project` (`reflections/expectation_reconciler.py:448`, `sdlc_upvote_lanes.py:438`, `sdlc_progress.py:1098`), but `docs_auditor`, `system_health_digest` and `tools/improvement.py` do not. After the cut-over, a non-owning machine could enqueue a session for another machine's project, or two machines could each create one for the same host finding. | pending | At the top of `hand_off`, before the steer rung: `if not machine_owns_project(finding.project.get("_key") or finding.project.get("key")): return HandoffResult("unreachable", None, "not-owner")` (confirm the project-dict key name), and record an operator finding. Host-level findings target the valor project, so they inherit valor's owning machine. Add a boundary test. |
| CONCERN | History & Consistency | Two verification checks cannot pass as written. The "No valor-telegram in reflections" grep uses the unescaped `tools.valor_telegram` and matches kept, out-of-scope text in `reflections/pm_briefings/delivery.py:6,84` and the `resolve_eng_group` docstring at `reflections/utilities.py:378`. Step 7's `grep -rn "send_eng_telegram" docs/features` still matches `improvement-research-cycle.md:520`, `pm-session-liveness.md:321`, `sentry-triage.md:124` and `README.md`, none of which the Documentation checklist covers. | pending | Use `grep -rnE '"valor-telegram"\|"tools\.valor_telegram"' reflections/ scripts/memory_consolidation.py tools/improvement.py`, which matches quoted string literals only. Add `docs/features/{sentry-triage,improvement-research-cycle,pm-session-liveness}.md` and the `README.md` row to the Documentation task. |
| CONCERN | Scope & Value | The default disposition for the assumption digest sends status-only output to human chat through an LLM session. That contradicts the plan's own "Status-only output never reaches human chat" and AC4. A persona rewrite or a silent completion can reword or drop the byte-pinned `CLOSING_LINE` (`reflections/improvement_assumption_digest.py:53-56`), which charter §11 depends on. The Success Criteria never say which outcome ships if Tom has not answered Open Question 1. | pending | If the persona path stays, the brief must carry the rendered digest as verbatim text to deliver. A test asserts `CLOSING_LINE` appears byte-exact in the outbox payload, and a silent completion of a digest handoff is recorded as an operator failure, not success. Otherwise default to a verbatim, tested exception. Add a Success Criterion naming the shipped digest disposition. |
| CONCERN | Scope & Value | The plan does not show the sessions are worth their cost, and it silently drops signals. Every agent-handled row spawns an eng session whose expected outcome is silence. `docs_auditor` "PR opened" and health anomalies are notifications, not decisions. Memory contradictions, "auto-resume disabled", and projects with no `Eng:` group move to an operator surface that no one is named as reading. The plan has no list of messages Tom will stop seeing, and the Success Criteria omit AC2's live observation. | pending | Add a per-source rate cap in `hand_off` keyed on `dedup_key` with a TTL, and require every new caller (docs_auditor, system_health_digest) to define a dedup key. Add a "Behavior Tom will notice" list: the digest voice, no memory-contradiction pings, no auto-resume-disabled ping, and the upvote announcement becoming the session's first message. Add a Success Criterion for one live observation of a handed-off reflection in Eng: Valor. |

---

## Open Questions

1. **Charter §11 vs. AC4 (assumption digest).** The charter keeps the three-day digest "as a status report" in Telegram, and only you can amend it. The plan defaults to keeping it in Telegram, delivered by a handoff agent in the persona rather than a raw page. Alternative: amend §11 and move the digest to the operator surface only. Which do you want?
2. **Mechanical discharge.** The reconciler still never discharges an expectation on its own, even with merged-PR evidence; it hands the evidence to an agent, which discharges. Keep that rule (recommended), or allow automatic discharge when the evidence is `merged` and the PR closes the Job's issue?
3. **Completion reporting.** Recording outbound work on the Job is fixed here, but lanes and `/do-merge` still never report completion back to the Job, which is why an expectation stays open after a merge. Should that be a separate follow-up issue (recommended), or folded into this plan?
4. **Handoff target for project-level findings.** The plan targets the project's `Eng:` Room. If a project has no `Eng:` group, the finding becomes operator-only. Is that acceptable, or should those go to the host valor `Eng:` Room?
