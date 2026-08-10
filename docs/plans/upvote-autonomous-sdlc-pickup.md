---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2717
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-10T11:49:20Z
---

# Autonomous SDLC pickup on `upvote`-labeled issues

## Problem

A human decides an issue is worth doing and marks it. Then nothing happens until that same human separately tells an Eng session to begin — by Telegram message, or by invoking `/sdlc` in a local session. Approval and kickoff are two different acts, and approved work sits idle not because anything is blocked but because nobody re-asked.

**Current behavior:** `reflections/sdlc_progress.py` is the *recovery* half of autonomous SDLC — it detects a stalled lane (open `session/sdlc-<N>` PR, no live run holding the issue lock) and steers, resumes, or creates an Eng session to unstick it. There is no counterpart that *starts* a lane. The pipeline heals itself but cannot begin by itself.

**Desired outcome:** A human adds the existing `upvote` label and does nothing further. Within the next scheduled tick the system announces in that project's `Eng: X` Telegram group that it is picking the issue up, creates an Eng session anchored to that announcement message, and lets `/sdlc` run the appropriate stage. Every subsequent agent message threads under the announcement, so the group reads as one conversation per issue instead of a stream of orphaned updates.

## Freshness Check

**Baseline commit:** `1e3fdd6f5` ("Migrate completed plan: sdlc-stall-auto-resume")
**Issue filed at:** 2026-08-10T09:52:01Z
**Disposition:** Minor drift (one premise in the issue was factually wrong and is corrected below; nothing in the repo moved)

**File:line references re-verified:**

| Reference | Issue's claim | Status |
|---|---|---|
| `reflections/sdlc_progress.py` | recovery-half sibling with `_lock_says_live` | Holds. Entrypoint `run_sdlc_progress_check()` at `:949`, `_lock_says_live` at `:302`, `create_session` call at `:720`, `_send_alert` at `:536`. |
| `reflections/utilities.py` | `run_per_project_audit`, `load_local_projects` | Holds. `:118` and `:94`; `machine_owns_project` at `:65`. |
| `agent/reflection_schedule.py` | `cron:` + inline `tz=` already supported | Holds. `_INLINE_TZ_RE` at `:45`, `_next_cron` at `:100`. |
| `tools/valor_session.py:639` | hardcoded `telegram_message_id=0` | Holds exactly at `:639`; `create_session` signature at `:452` has no such parameter. |
| `tools/valor_telegram.py` | "`send` returns `message_ids`" | **FALSE.** See Spike Results spike-1. `cmd_send` (`:798`) is a pure Redis-outbox producer returning an exit code. |
| `agent/pipeline_state.py` | reuse for derived stage | Partially. `derive_from_durable_signals` (`:1234`) keys the plan path off `session.slug` and runs subprocesses with **no `cwd`** (`:1463`) — unusable from a multi-project reflection. The issue-keyed `PipelineLedger` is the correct reuse. See spike-3. |
| `bridge/routing.py:561` | how `Eng:` grants the engineer persona | Holds — `:560-562` is the `chat_title.startswith("Eng:")` fallback inside `resolve_persona`. It is not a chat-id resolver; none exists for "the Eng group of project X". |

**Cited sibling issues/PRs re-checked:** #1191 (reply-to threading via `TELEGRAM_REPLY_TO`) — confirmed live at `agent/sdk_client.py:502-507`. #2696/#2710 (SDLC stall auto-resume) — merged 2026-08-10, the direct predecessor; it created `reflections/sdlc_progress.py` in its current shape, which this plan mirrors.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=<issue createdAt> -- reflections/ tools/valor_session.py agent/pipeline_state.py config/reflections.yaml` is empty.

**Active plans in `docs/plans/` overlapping this area:** none. The nearest neighbor, `sdlc-stall-auto-resume`, shipped and was migrated out on `1e3fdd6f5`. `ask-me-telegram-polls` touches Telegram group messaging but not the outbox/relay send path.

**Live-config re-verification (new, not in the issue):** 8 of 9 projects in `projects.json` have an `Eng: X` group; `royop` has none. The scope gate is load-bearing on real config, not hypothetical. The `upvote` label **already carries** the description "Pre-approved for autonomous SDLC pickup" — that half of AC 1 is done; only the `CLAUDE.md` table row remains. There are currently **zero** open `upvote` issues, so first production tick will be a no-op — the feature must be exercised by tests, not by waiting for it to fire.

## Prior Art

- **PR #2710 / issue #2696 — "SDLC stall auto-resume"** (merged 2026-08-10). Built `reflections/sdlc_progress.py`: per-project audit, machine-ownership gate, issue-lock liveness with a fail-closed unknown state, cooldowns, attempt counting, escalate-once. This plan is its mirror image and reuses its gate vocabulary verbatim. Succeeded; no known regressions.
- **Issue #1197 — "Daily PM audio briefing reflection"** (closed 2026-05-01). Built `reflections/pm_briefings/`, including `_collect_upvote_queue` (`collector.py:159`) — the repo already lists `upvote` issues per project. Succeeded. This plan reuses `_gh_issue_list` rather than writing a third `gh issue list` wrapper.
- **Issue #1191 — reply-to threading.** Established `AgentSession.telegram_message_id` → `TELEGRAM_REPLY_TO` → outbound `reply_to`. Succeeded, but only ever fed by bridge-originated messages; no programmatic producer has ever set it. That gap is what this plan closes.
- **Issue #1828 — reflections moved out of the worker event loop.** Established `python -m reflections` as its own launchd process. Relevant because it means the reflection has **no Telethon client**, which forecloses the obvious implementation.

No prior attempt at autonomous *start* exists. No "Why Previous Fixes Failed" section — this is greenfield in that respect.

## Research

Purely internal work: no new external libraries, APIs, or ecosystem patterns. `croniter` (already a dependency, already used by `agent/reflection_schedule.py`) is the only third-party surface and it is unchanged. No relevant external findings — proceeding with codebase context.

## Spike Results

### spike-1: Can the reflection capture the Telegram message id of its announcement?
- **Assumption**: "`tools/valor_telegram.py send` returns `message_ids`; capture the first" (issue, Solution §4).
- **Method**: code-read
- **Finding**: **The assumption is false.** `cmd_send` (`tools/valor_telegram.py:798`) resolves the chat, builds a payload, `RPUSH`es it to `telegram:outbox:{session_id}` (`:1110`, `session_id = f"cli-{int(time.time())}"` at `:889`), prints "Message queued", and returns an exit code. It never learns the id — its own docstring (`:800-804`) explains it deliberately avoids a Telethon client because the bridge holds the `data/valor_bridge` SQLite lock. The only `message_ids` in the codebase is `AwaitResult.message_ids` (`tools/valor_telegram_await.py:57`), populated exclusively from records where `direction == "in"` (`:146-149`) — i.e. **inbound replies**, not the sent message — and `--await-reply` hard-refuses any chat that is not a registered bot peer (`tools/valor_telegram.py:851-867`), which an `Eng: X` human group never is.
  The id *does* exist, but only inside the bridge relay: `bridge/telegram_relay.py:636-646` reads `getattr(sent, "id", None)` off `send_markdown`'s return, and `:978-984` hands it to `_record_sent_message(session_id, msg_id)` → `AgentSession.record_pm_message` → `pm_sent_message_ids`. **There is no ack channel back to the producer.** The single precedent for reading an id back (`agent/session_executor.py:1688-1714`) drain-polls `get_outbox_length(session_id)` then re-reads its own `AgentSession` — which requires the producer to already own a session with that exact `session_id`.
- **Confidence**: high
- **Impact on plan**: The plan must add a producer-readable ack. Chosen: a small addition in `process_outbox` writing the id to a short-TTL Redis list keyed by the payload's `session_id`. This is the only new infrastructure in the plan and it makes the anchor available to *any* programmatic producer, not just this reflection. It also dictates the resolution of open question 3 (see Technical Approach §D).

### spike-2: Which process runs reflections, and does it have a Telethon client?
- **Assumption**: "the reflection can send Telegram messages directly."
- **Method**: code-read
- **Finding**: Reflections run in a standalone launchd process (`com.valor.reflection-worker.plist` → `python -m reflections`; `reflections/__main__.py` names itself the sole scheduler owner per #1828). `grep -l telethon` over `reflections/` and `agent/` returns nothing. Sync reflection callables execute in `_reflection_pool`, a `ThreadPoolExecutor` (`agent/reflection_scheduler.py:453`), under an `asyncio.wait_for` that is **detection-only** for sync functions — a hung thread is not cancelled. Redis is the only cross-process channel available.
- **Confidence**: high
- **Impact on plan**: Direct Telethon is off the table (option C, rejected). All sends go through the outbox. Every blocking wait in the reflection must be tightly bounded, because the scheduler's timeout cannot actually kill it.

### spike-3: Can `agent/pipeline_state.py` be reused to derive per-project lane state?
- **Assumption**: "`agent/pipeline_state.py` already computes stage from exactly these artifacts and should be reused rather than reimplemented" (issue, Solution §3).
- **Method**: code-read
- **Finding**: Two problems with `derive_from_durable_signals` (`:1234`). (a) It computes `plan_path = f"docs/plans/{session.slug}.md"` from the session slug — for a lane on issue #2717 that is `docs/plans/sdlc-2717.md`, but this repo names plan docs from the issue **title** (`docs/plans/upvote-autonomous-sdlc-pickup.md`). Plan-doc existence keyed on `sdlc-{N}` is therefore always false; it is not a usable signal. (b) `_durable_run` (`:1463`) invokes `git`/`gh` with **no `cwd`**, so it inspects whichever repo the calling process sits in — structurally wrong for a reflection iterating nine projects.
  The correct reuse is the issue-keyed ledger: `agent.pipeline_ledger.PipelineLedger.get(target_repo, issue_number)` — read-only, never creates (per #2395), and `target_repo` is derivable per project from `projects.json` `github.org`/`github.repo`. Note `tools/sdlc_stage_query.query_stage_states(issue_number=N)` is *not* usable either: it resolves `target_repo` through a lease/env fallback (`_resolve_target_repo_for_read`) that has no meaning in a multi-project reflection process.
- **Confidence**: high
- **Impact on plan**: Drop plan-doc-existence from the derived-state table. Use `PipelineLedger.get(repo, N)` directly with an explicitly-derived repo slug. Also drop the reflection's stage *choice* entirely — see spike-4.

### spike-4: Does the reflection need to decide PLAN vs. BUILD?
- **Assumption**: the issue's decision table has the reflection "start a lane at PLAN" vs. "start a lane at BUILD".
- **Method**: code-read
- **Finding**: `/sdlc` is a single-stage router that assesses state and dispatches exactly one sub-skill (CLAUDE.md development principle 9; `.claude/skills/sdlc/SKILL.md`). `reflections/sdlc_progress.py` already exploits this — it creates an Eng session with a message and lets the router pick. A reflection that pre-selected the stage would be a second, weaker copy of the router's assessment logic, running in a process that cannot even read the right repo (spike-3).
- **Confidence**: high
- **Impact on plan**: The reflection's decision collapses from a 4-row stage table to a binary **start / skip**. This removes the largest chunk of proposed logic and eliminates a whole class of divergence bug.

### spike-5: What closes the two-consecutive-ticks window without a claim key?
- **Assumption**: "running the reflection twice in a row does not start a second lane" needs a debounce key.
- **Method**: code-read
- **Finding**: `create_session` → `_push_agent_session` (`agent/agent_session_queue.py:274`) writes the `AgentSession` row **synchronously, with `slug` set**, before returning. So immediately after tick 1 returns, a non-terminal `AgentSession` with `slug == f"sdlc-{N}"` exists and is queryable. That is a derived artifact, not a claim key — it cannot drift from reality because it *is* the reality. The issue lock, by contrast, is only taken once the worker actually starts the session, which leaves a gap of seconds-to-minutes.
- **Confidence**: high
- **Impact on plan**: Gate on "non-terminal `AgentSession` with this slug exists" as the primary already-started signal. No new Redis key, no TTL to tune, and the no-claim-key constraint is honored in spirit as well as letter.

## Data Flow

1. **Trigger**: `ReflectionScheduler` fires `sdlc-upvote-pickup` on its cron tick, resolves `reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes`, and runs it in `_reflection_pool`.
2. **Fan-out**: `run_per_project_audit(_pick_up_upvoted, name="sdlc-upvote-pickup")` iterates `load_local_projects()`.
3. **Per-project gates**: `machine_owns_project(slug)` → `resolve_eng_group(project)` (new) → `_project_repo(project)`. Any miss returns `status: "skipped"`.
4. **Candidate list**: `_gh_issue_list(repo, ["upvote"], cwd=working_directory, extra_args=["--search", "sort:created-asc"])` → open issues carrying `upvote`, **server-side** oldest-first, then sliced to `UPVOTE_CANDIDATE_SCAN_MAX`.
5. **Ceiling**: one `gh pr list --state open --json headRefName` per project counts live `session/sdlc-*` lanes; at or above the cap the project returns a finding and starts nothing.
6. **Per-candidate gates** (first survivor wins, then stop): non-terminal `AgentSession(slug=sdlc-N)` → recent terminal-FAILED `AgentSession(slug=sdlc-N)` inside `UPVOTE_FAILURE_BACKOFF_S` → `PipelineLedger.get(repo, N)` has recorded stages → `_lock_says_live(N)` is `True` or `None` → any PR (open **or merged**) on `session/sdlc-{N}`.
7. **Announce**: build the announcement payload with a fresh producer id `upvote-{project}-{N}-{ts}` **and `"ack_sent_id": True`**, `RPUSH telegram:outbox:{producer_id}`.
8. **Relay** (`bridge/telegram_relay.py::process_outbox`, other process): `LPOP` → `send_markdown` → `msg_id` → existing `_record_sent_message` bookkeeping **plus**, *only when the payload carries `ack_sent_id`*, `RPUSH telegram:sent:{producer_id} {msg_id}` with a TTL.
9. **Anchor readback**: reflection blocking-polls `telegram:sent:{producer_id}` with a bounded budget, then deletes the key.
10. **Re-gate and create**: re-read the two cheap liveness gates, then `create_session(message=..., role="eng", session_type="eng", slug=f"sdlc-{N}", project_key=..., chat_id=str(eng_chat_id), telegram_message_id=anchor_id)`.
11. **Output**: the worker starts the session; `agent/sdk_client.py:502` exports `TELEGRAM_REPLY_TO=anchor_id`; every outbound message from the lane threads under the announcement in the `Eng: X` group.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `create_session` gains two keyword parameters (`telegram_message_id: int = 0`, and `chat_id` becomes genuinely load-bearing rather than a `"0"` placeholder) — purely additive, every existing caller keeps today's behavior. `bridge/telegram_relay.py` gains one exported reader function.
- **Coupling**: slightly *decreased*. Today the sent-message id is reachable only by a caller that already owns a matching `AgentSession`. The ack key makes it a general producer-side capability, which is why `session_executor`'s bespoke drain-poll exists at all.
- **Data ownership**: unchanged. No new durable state; the ack key is ephemeral and single-consumer.
- **Reversibility**: high. Flip `enabled: false` on the reflection entry and the whole feature is inert; nothing else in the system reads its outputs.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm the concurrency cap and the announce-first trade-off land as decided)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `projects.json` reachable | `python -c "from reflections.utilities import load_local_projects; assert load_local_projects()"` | Per-project iteration source |
| `gh` authenticated | `gh auth status` | Issue and PR listing |
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Outbox, ack key, issue lock |
| At least one `Eng:` group configured | `python -c "import json,os;d=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json')));assert any(g.startswith('Eng:') for p in d['projects'].values() for g in ((p.get('telegram') or {}).get('groups') or {}))"` | Scope gate has something to match |

## Solution

### Key Elements

- **`reflections/sdlc_upvote_lanes.py`** — the start half of autonomous SDLC. Sibling to `sdlc_progress.py`, same shape, opposite direction: it starts lanes that do not exist rather than unsticking lanes that do.
- **`reflections/utilities.py::resolve_eng_group(project)`** — resolves a project's `Eng: X` group name and numeric `chat_id` by prefix scan. Today no such helper exists; `sdlc_progress` hardcodes `"Eng: Valor"` and `pm_briefings/delivery.py::_resolve_chat_id` requires the caller to already know the exact group name. This makes "the engineering room for project X" a first-class lookup.
- **Relay sent-id ack (opt-in per message)** — `bridge/telegram_relay.py` publishes a successfully sent message's id to `telegram:sent:{session_id}` with a short TTL, and exposes `await_sent_message_id(session_id, timeout_s)` for producers. Closes the gap spike-1 found. **The write is gated on the payload flag `ack_sent_id`** — `if msg_id is not None and message.get("ack_sent_id"):` — because an unconditional write would put two extra Redis ops on the send path for *every* outbound Telegram message system-wide and mint a key nobody reads for every ordinary session, to serve one consumer that fires at most once per project per two hours. Only the reflection's announcement payload sets the flag, so existing traffic is byte-for-byte unchanged and `tests/unit/test_telegram_relay_chat_log.py`'s current assertions hold without modification. The non-fatal `try/except` wrapper stays regardless: the relay must never crash on ack bookkeeping.
- **`create_session(telegram_message_id=...)`** — replaces the hardcoded `0` at `tools/valor_session.py:639`, letting a programmatic caller anchor a session's outbound thread.
- **Derived start/skip decision** — no claim key, no label mutation. Every gate reads an artifact that *is* the state.

### Flow

`Human adds `upvote` to issue #N` → **nothing else required** → `cron tick` → **Eng: X group** shows "Picking up issue #N: <title>" → `Eng session created, anchored to that message` → **same group, threaded under the announcement** → `/sdlc` runs one stage → ... → `PR merged, issue closed` → **issue drops out of the candidate list**

Failure branch: `announcement sent` → `create_session fails` → **threaded reply: "could not start, retrying next tick"** → `next tick re-evaluates from artifacts` (nothing to clean up, because nothing was claimed).

### Technical Approach

#### A. Ordering (resolves open question 1) — **oldest first, by issue creation time**

Candidates sort by `createdAt` ascending, tie-broken by issue `number` ascending. Rationale: one-per-tick makes ordering the drain order, so the rule must be starvation-free. Newest-first starves the oldest approved work indefinitely — precisely the failure this feature exists to fix. An explicit priority signal would mean minting a new label or parsing issue bodies, which is a second approval vocabulary layered on `upvote`; explicitly rejected (see No-Gos). A human who wants an issue jumped can remove and re-add `upvote`… which under oldest-first does nothing, so the honest escape hatch is: start it by hand, exactly as today. FIFO is the whole contract and it is legible from the issue list without reading any code.

**The sort must be server-side, not client-side.** `_gh_issue_list` (`reflections/pm_briefings/collector.py:95`) carries `limit: int = 20` and passes no sort flag, so `gh issue list` returns its default **newest-first** page. Sorting that already-truncated page by `createdAt` ascending in Python yields "the oldest of the newest 20" — above a 20-issue backlog the genuinely oldest approved issue is never a candidate, which is precisely the starvation this section exists to eliminate. Raising `--limit` is **not** an equivalent fix; it only moves the cliff.

So:

- `_gh_issue_list` gains `extra_args: list[str] | None = None`, spliced into the `gh` argv. Default `None` and default `limit=20` keep `_collect_open_bugs` / `_collect_upvote_queue` byte-for-byte unchanged.
- The reflection calls `_gh_issue_list(repo, ["upvote"], cwd=..., limit=<explicit>, extra_args=["--search", "sort:created-asc"])`. `gh` applies the `--search` sort **before** `--limit`, so the returned page is the oldest N. Verified live at plan time against `tomcounsell/ai` (returned #728 2026-04-06 first, ascending).
- `_gh_issue_list` also requests `createdAt` in its `--json` field list (additive; existing callers ignore unknown keys). The client-side `createdAt`-ascending, `number`-ascending sort stays as a deterministic tie-break over the already-correct page — it is a stabilizer, not the ordering mechanism.

The ordering test **must** use a fixture larger than the page size and assert the pick is the true oldest; a 3-item fixture cannot detect this defect. Both call shapes (default and `extra_args`) assert their constructed argv in `tests/unit/reflections/test_pm_briefings_collector.py`.

#### B. Concurrency ceiling (resolves open question 2) — **yes, cap live auto-started lanes per project**

One-per-tick throttles the *ramp* but not the *steady state*: nine ticks a day (06:00–22:00 every two hours) permits eight new lanes per project per day, indefinitely. Each lane is a `claude -p` subprocess plus a worktree plus eventually a PR that a human must review. Unbounded, this converts a queue problem into a review-backlog problem and a memory-pressure problem on a machine that already has documented xdist/worktree pressure.

- Constant `UPVOTE_LANE_MAX_LIVE`, default **3**, read via an `_env_int` helper mirroring `sdlc_progress._env_float`. Per the repo's provisional-magic-number convention this is a named, env-overridable constant carrying a grain-of-salt comment: 3 is a starting guess chosen so a single project cannot monopolize the worker, not a measured optimum.
- Live count = (open PRs whose `headRefName` matches `session/sdlc-*`) + (candidate `upvote` issues whose `_lock_says_live` is `True`). One extra `gh pr list --state open --json number,headRefName --limit 100` per project per tick; the lock reads are already being done per candidate.
- **Known undercount, accepted and documented**: a human-started lane that has not yet opened a PR and whose issue is not `upvote`-labeled is invisible to this count. The ceiling is a ramp brake, not admission control. Making it exact would require enumerating every non-terminal `AgentSession` per project, which is a Redis scan on a hot path for a guard that only needs to be approximately right.
- At or above the cap the project contributes a finding (`"[slug] lane ceiling reached (3/3), N upvote issues waiting"`) and starts nothing. Findings surface in the reflection report, so the backlog is visible rather than silent.

**Candidate scan cap — bounding aggregate per-tick work.** The ceiling bounds *starts*, not *inspection*. Gate 4 shells one `gh pr list` per candidate and the ceiling reads `_lock_says_live` per candidate, and both run for every issue that ends up skipped, across all nine projects, on top of a `UPVOTE_ANCHOR_WAIT_S` wait per pickup. Spike-2 established that nothing can cancel a wedged sync reflection, so an unbounded candidate list is an unbounded occupancy of a `_reflection_pool` slot.

- Immediately **after** the §A sort and **before** the gate loop, the candidate list is sliced to `UPVOTE_CANDIDATE_SCAN_MAX` (named, env-overridable, provisional default **10**, grain-of-salt comment). Because the sort is oldest-first and server-side, this truncates the *tail*, never the head — FIFO is preserved exactly, and the slice is the same set the `--limit` would have returned.
- The module docstring states the aggregate worst case explicitly: `projects × (UPVOTE_CANDIDATE_SCAN_MAX × gh_timeout + UPVOTE_ANCHOR_WAIT_S)`.
- Risk 4's budget test asserts the **whole** `run_sdlc_upvote_lanes()` call returns within that budget against a never-arriving ack — not a single project.

#### C. Skip gates, in evaluation order

Cheapest and most decisive first; **any** gate that cannot answer confidently skips (fail closed — a duplicate lane is strictly worse than a delayed one, and the next tick is only two hours away).

| # | Gate | Skip when | Source |
|---|---|---|---|
| 1 | Session exists | a non-terminal `AgentSession` with `slug == f"sdlc-{N}"` exists | Popoto ORM query (spike-5) |
| 1.5 | Recent failure | a **terminal-FAILED** `AgentSession` with `slug == f"sdlc-{N}"` exists whose `created_at` is newer than `UPVOTE_FAILURE_BACKOFF_S` | same query as gate 1, status + recency filter (§D backoff) |
| 2 | Ledger written | `PipelineLedger.get(f"{org}/{repo}", N)` carries any recorded stage state | `agent/pipeline_ledger.py` (spike-3) |
| 3 | Lock live | `_lock_says_live(N)` is `True` **or** `None` (unknown) | ported from `sdlc_progress.py:302` |
| 4 | Branch has a PR | `gh pr list --head session/sdlc-{N} --state all` is non-empty | `--state all`, deliberately |

Gate 4 uses `--state all`, not `--state open`. An **open** PR means `sdlc_progress` owns the lane (the issue's own table). A **merged** PR on an issue that is still open means the implementation PR body lacked `Closes #N` — the derived table in the issue would read that as "planned, not built" and restart the lane forever. Gate 4 stops it and emits a finding naming the issue and the merged PR so a human can close it. This is the concrete failure mode behind open question 4.

#### D. Announce-then-create atomicity (resolves open question 3) — **announce first, capture, create, retract on failure**

The three candidate orderings, and why:

- *Create then announce* is atomic in the good direction (no phantom promise) but **loses the anchor**, which is the feature's entire threading requirement. Rejected.
- *Create, announce, then patch the anchor onto the session* (mirroring `session_executor.py:1688`) keeps the anchor but opens a genuine race: the worker can claim the session and spawn the `claude -p` subprocess — which snapshots `TELEGRAM_REPLY_TO` from `agent/sdk_client.py:502` at spawn time — before the anchor is written. The window is seconds on both sides and there is no ordering primitive between the worker's claim and the relay's drain. Rejected as unfixable without a session-level "hold" that does not exist.
- **Announce, capture the id, then create** is chosen. The only failure mode is a phantom promise: announcement lands, `create_session` raises or returns `success=False`, and the group shows an unfulfilled statement.

That failure mode is handled explicitly rather than tolerated: on create failure the reflection posts a short reply **threaded under the announcement** naming the error and stating that the next tick will retry, and returns the failure as a finding. Because there is no claim key and no label mutation, there is genuinely nothing to roll back — the next tick re-derives from artifacts and tries again. A retract-by-delete was considered and rejected: `edit_message`/`delete_message` appear nowhere in the repo, adding them is a bridge capability well beyond this issue's scope, and a visible "that did not work" is better operator signal than a message that quietly vanishes.

**Failure backoff (gate 1.5) — retry, but not forever, and not every tick.** "Nothing was claimed, so the next tick retries" is the correct recovery story for a *transient* failure and an amplifier for a *deterministic* one. A candidate whose `create_session` fails the same way every time (missing checkout, worktree collision, bad `project_key`) would re-announce and re-retract in the `Eng: X` group on every tick — nine announcement/retraction pairs a day for one broken issue, which is worse for the group's signal quality than never having announced. The predecessor `sdlc_progress.py` carries cooldowns and attempt counting for exactly this; dropping them while claiming to reuse its gate vocabulary verbatim was an inconsistency.

Gate 1.5 (§C) closes it: skip candidate N when a **terminal-FAILED** `AgentSession` with `slug == f"sdlc-{N}"` exists whose `created_at` is newer than `UPVOTE_FAILURE_BACKOFF_S` (named, env-overridable, provisional). Gate 1 already runs `AgentSession.query.filter(slug=f"sdlc-{N}")`, so this is a status-and-recency filter over a query that is happening anyway — no new Redis key, no extra round trip, no new state.

**This is a backoff, not a claim, and the "No claim key" No-Go holds.** The distinction is not rhetorical: a claim key is *written by this reflection to assert ownership* and must be explicitly released or expired for correctness. Gate 1.5 writes nothing at all — it reads a record that `create_session` produced for its own reasons, and its effect expires by wall clock with no participation from anyone. An issue under backoff is not marked as taken; it is simply not re-attempted for a while. The Task 7 validator is directed here so it does not misread the gate as a No-Go violation.

Two bounded degradations:
- If the ack does not arrive inside the wait budget (`UPVOTE_ANCHOR_WAIT_S`, default **20s**, provisional), the reflection proceeds to create with `telegram_message_id=0`. The announcement already went out; refusing to start the work because threading is imperfect would be the wrong trade. A finding records the unanchored start.
- If the relay reports `DELIVERED_NO_ID`, treat it as the same case.

The wait is `time.sleep`-polled in ~250ms increments with a hard iteration cap. Per spike-2 the scheduler cannot cancel a wedged sync reflection, so the budget is a hard local invariant, not a hope.

#### E. Post-merge label residue (resolves open question 4) — **the closed-issue filter suffices; gate 4 covers its one hole**

`_gh_issue_list` already passes `--state open`. The normal terminal path is: implementation PR merges with `Closes #N` → issue closes → it stops appearing as a candidate on the very next tick, with the `upvote` label harmlessly still attached. No label mutation, no terminal-state record, nothing to reap. An issue closed as wontfix while still labeled behaves identically.

The one hole is the merged-PR-but-issue-still-open case above, which gate 4 closes. So: **the terminal state is "issue closed", and it is entirely sufficient given gate 4.** Explicitly *not* doing: removing the `upvote` label on pickup or on merge (the issue forbids label mutation, and the label is also useful post-hoc as a record of what was auto-approved).

#### F. Schedule and registration

```yaml
  - name: sdlc-upvote-pickup
    group: audits
    description: "Start half of autonomous SDLC: pick the oldest open `upvote`-labeled issue per project, announce it in that project's `Eng:` Telegram group, and create an Eng session anchored to the announcement. Derives start/skip from artifacts only — no claim key, no label mutation. Set SDLC_UPVOTE_PICKUP_ENABLED=false to disable without editing config."
    schedule: "cron: 0 6-22/2 * * *; tz=America/Los_Angeles"
    priority: low
    execution_type: function
    callable: "reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes"
    enabled: true
```

This will be the **first** `cron:` entry in `config/reflections.yaml` (every existing entry uses `every: Ns`). The grammar is supported and unit-tested (`tests/unit/test_reflection_schedule_grammar.py`), but the end-to-end YAML→`ReflectionEntry.validate`→`compute_next_due` path has never been exercised with a `cron:` value from that file. A registration test must cover it — this is the plan's most likely source of a "works in the unit test, inert in production" defect.

No `project_key:` key on the entry: the reflection is multi-project by design and gates ownership per project at runtime via `machine_owns_project`. (`project_key:` is an *install-time* filter that would disable the whole reflection on every machine but one.)

An `SDLC_UPVOTE_PICKUP_ENABLED` env kill switch mirrors `SDLC_STALL_RESUME_ENABLED`, checked at the top of the entrypoint, returning `status: "disabled"`.

#### G. `create_session` plumbing

```python
def create_session(
    *, message, role="eng", slug=None, project_key=None, parent_id=None,
    session_type="eng", chat_id="0", telegram_message_id=0,   # <- new
    model=None, requires_real_chrome=False,
) -> CreateResult:
```

`telegram_message_id` flows to `_push_agent_session` at `:639` in place of the literal `0`. Defaulting to `0` preserves every existing caller byte-for-byte. The CLI (`cmd_create`) gains a matching `--telegram-message-id` argument. Its justification is **operational, not programmatic** — the only production caller imports `create_session` directly — so it is kept explicitly as a documented debugging affordance: it is how a human reproduces an anchored start by hand against a test group when threading misbehaves (the manual path named in the human-visible Success Criterion). The flag's help text says so, so a future reader does not delete it as dead surface.

Note that `chat_id` and `telegram_message_id` are meaningful only together — a message id is scoped to its chat, and `chat_id="0"` routes output to the system Room sink (`agent/output_handler.py:466,678`) rather than Telegram. The reflection always passes both; `create_session` logs a warning if `telegram_message_id` is non-zero while `chat_id` is `"0"`, since that combination is always a caller bug.

#### H. Announcement and session messages

Announcement (to the `Eng: X` group):
> Picking up issue #N for SDLC: {title}
> {url}

Session message (must contain `issue #N` — `_derive_sdlc_metadata` at `tools/valor_session.py:616` parses it to set `issue_url`/`classification_type`, and slugless `eng` creation depends on it):
> Run the next SDLC stage for issue #N: {title}. This issue is labeled `upvote` (pre-approved for autonomous pickup). Invoke `/sdlc` and let the router choose the stage.

Retraction (threaded reply, create-failure only):
> Could not start the SDLC lane for issue #N: {error}. Retrying on the next tick.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every `except Exception` in `reflections/sdlc_upvote_lanes.py` must log and produce an observable outcome (a finding string, a `status`, or a declined-gate result) — no bare `pass`. Tests assert the finding text, not just that no exception escaped.
- [ ] `resolve_eng_group` on malformed `telegram.groups` (string instead of dict, missing `chat_id`, non-integer `chat_id`) returns `None` and the project is skipped — asserted, not assumed.
- [ ] `_lock_says_live` returning `None` (Redis down / malformed payload) must be asserted to **skip**, mirroring `sdlc_progress`. A test injects a raising Redis client.
- [ ] The relay ack addition must be non-fatal: a test asserts `process_outbox` still records `pm_sent_message_ids` and returns normally when the ack `RPUSH` raises.

### Empty/Invalid Input Handling
- [ ] Zero `upvote` issues → `status: "ok"`, empty findings, no session created (this is the current production reality — verified zero open `upvote` issues at plan time).
- [ ] `gh` returns non-zero or non-JSON → `_gh_issue_list` returns `[]` → project skipped with a finding, never a crash.
- [ ] Empty/whitespace issue title → announcement still well-formed; the `issue #N` token in the session message is present regardless.
- [ ] `load_local_projects()` returns `[]` → `run_per_project_audit` yields the documented `"no qualifying projects"` result.

### Error State Rendering
- [ ] Create-failure path: assert the retraction reply is enqueued to the outbox with `reply_to` equal to the announcement id, and that the failure appears as a finding.
- [ ] Anchor-timeout path: assert the session is still created (with `telegram_message_id=0`) **and** a finding records the unanchored start — the degradation must be visible, not silent.
- [ ] Ceiling-reached path: assert the finding names the count and the number of waiting issues.

## Test Impact

- [ ] `tests/unit/test_valor_session_create_core.py` — UPDATE: add coverage for the new `telegram_message_id` parameter reaching `_push_agent_session`; existing assertions on the default path must still pass unchanged (proving the additive-default claim).
- [ ] `tests/unit/test_valor_session_cli.py` — UPDATE: cover the new `--telegram-message-id` CLI argument and its default.
- [ ] `tests/unit/test_valor_session_sdlc_metadata.py` — UPDATE: no behavior change expected; re-run to confirm `_derive_sdlc_metadata` is untouched by the signature change.
- [ ] `tests/unit/test_telegram_relay_chat_log.py` — UPDATE: `process_outbox`'s success path gains an `ack_sent_id`-gated `RPUSH`. Because the write is opt-in, the existing assertions should hold **unmodified** (payloads there carry no flag); the update is purely additive coverage for the flagged and unflagged branches.
- [ ] `tests/unit/test_reflections_yaml_migration.py`, `tests/unit/test_update_reflections_yaml.py`, `tests/unit/test_reflection_register.py` — UPDATE: these assert over the full set of entries in `config/reflections.yaml`; adding an entry (and the file's first `cron:` schedule) will require updating expected counts/shapes.
- [ ] `tests/unit/test_reflection_machine_filter.py` — UPDATE: confirm an entry with no `project_key` is left enabled on every machine (the behavior this plan relies on).
- [ ] `tests/unit/reflections/test_pm_briefings_collector.py` — UPDATE: `_gh_issue_list` gains `createdAt` in its `--json` field list and an `extra_args` parameter; assert **both** built command shapes (default, and with `--search sort:created-asc`) and that `_collect_open_bugs` / `_collect_upvote_queue` are unaffected.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — no change expected; it is the structural template for the new test module and must keep passing to prove no shared-helper regression.
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py` — CREATE: the new module's coverage (gates, ordering, ceiling, anchor, failure paths).

## Rabbit Holes

- **Building a general "send and get the message id" RPC.** The ack is one `RPUSH` + `EXPIRE` next to an id the relay already has, plus one reader. Resist turning it into a request/response correlation framework, a pubsub channel, or a retrofit of `session_executor.py:1688`'s bespoke drain-poll onto the new primitive. That retrofit is worth doing — as a separate change, after this one proves the primitive.
- **Making the concurrency ceiling exact.** Enumerating every non-terminal `AgentSession` per project to get a true live-lane count is a Redis scan on a periodic path, for a guard whose job is "do not ramp to eight a day." Approximate is the requirement.
- **Reimplementing stage derivation.** Spike-3 and spike-4 both point one way: read the ledger, do not compute the stage, let `/sdlc` route. Any task bullet that starts computing PLAN-vs-BUILD is out of scope.
- **Message editing / retraction-by-delete.** `edit_message` does not exist anywhere in the repo. Adding Telethon edit support to the relay to make the failure path prettier is a bridge feature, not this issue.
- **A priority or ordering vocabulary.** Labels like `upvote-high`, numeric prefixes in titles, body-parsed priorities. FIFO is the decision; anything else is a new approval language.
- **Auto-closing issues whose PR merged without `Closes #N`.** Gate 4 detects and reports it. Having a reflection close GitHub issues is a materially larger trust grant than starting a session.

## Risks

### Risk 1: The first `cron:` entry in `config/reflections.yaml` fails to parse and the reflection is silently inert
**Impact:** The feature ships, tests pass, and nothing ever fires in production. The most likely way this plan fails invisibly.
**Mitigation:** A registration test loads the real `config/reflections.yaml`, constructs the `ReflectionEntry`, calls `validate()`, and asserts `compute_next_due()` returns a timestamp in the expected 06:00–22:00 America/Los_Angeles window — not a mocked schedule string. Plus a `## Verification` row.

### Risk 2: A duplicate lane on a live issue
**Impact:** Two Eng sessions on one issue: two worktrees on the same branch name, contending pushes, doubled review load. The worst outcome in this change.
**Mitigation:** Four independent skip gates (§C), all fail-closed on uncertainty, with gates 1 and 3 re-read immediately before `create_session` (mirroring `sdlc_progress.py:707-719`). A test drives two consecutive ticks against one issue and asserts exactly one creation.

### Risk 3: Two machines both pick up the same issue
**Impact:** Same as Risk 2, across machines, where no shared lock is consulted before the session record exists.
**Mitigation:** `machine_owns_project` is gate zero, before any `gh` call. `projects.<key>.machine` is the single source of truth (CLAUDE.md, single-machine ownership). A test asserts a non-owned project returns `status: "skipped"` and issues no subprocess calls at all.

### Risk 4: The reflection wedges on the anchor wait
**Impact:** Per spike-2, `asyncio.wait_for` cannot cancel a sync reflection thread. A wedged tick occupies a `_reflection_pool` slot indefinitely and can starve other reflections.
**Mitigation:** The wait is a bounded loop with both a deadline and a hard iteration cap; every `gh`/subprocess call carries an explicit `timeout`. Crucially the **candidate count** is bounded too, by `UPVOTE_CANDIDATE_SCAN_MAX` (§B) — without it the per-project cost scales with the open-`upvote` backlog and the per-call timeouts bound nothing in aggregate. The worst case is therefore closed-form, `projects × (UPVOTE_CANDIDATE_SCAN_MAX × gh_timeout + UPVOTE_ANCHOR_WAIT_S)`, stated in the module docstring and asserted by a test that stubs a never-arriving ack and asserts the **whole** `run_sdlc_upvote_lanes()` call returns within it.

### Risk 5: Phantom promise in the engineering chat
**Impact:** The group is told work is starting and nothing does; trust in autonomous announcements erodes faster than it builds.
**Mitigation:** The threaded retraction reply (§D), the failure surfacing as a reflection finding, and the fact that the next tick genuinely does retry because nothing was claimed.

### Risk 6: Announcement lands in the wrong chat
**Impact:** Internal engineering chatter posted into a client-facing group.
**Mitigation:** `resolve_eng_group` matches only keys with the literal `Eng:` prefix and requires an explicit numeric `chat_id`; there is no name-substring fallback and no default chat. A missing or malformed entry skips the project. Tested against fixtures including `royop` (no Eng group) and a group entry lacking `chat_id`.

## Race Conditions

### Race 1: Lane starts between candidate listing and session creation
**Location:** `reflections/sdlc_upvote_lanes.py`, between the gate sweep and `create_session`; announcement + anchor wait sit in the middle and can take ~20s.
**Trigger:** A human messages the Eng group, or `sdlc_progress` fires, during the anchor wait.
**Data prerequisite:** The issue lock and the `AgentSession` row must reflect the other actor's start.
**State prerequisite:** No non-terminal `AgentSession` with `slug=sdlc-{N}`.
**Mitigation:** Re-read gates 1 and 3 immediately before `create_session`; on a positive re-read, abandon the create and post the retraction (a benign outcome, distinguished from an error in the finding text). Directly mirrors `sdlc_progress.py:707-719`.

### Race 2: Ack key read before the relay writes it
**Location:** `bridge/telegram_relay.py::process_outbox` (writer) vs. the reflection's `await_sent_message_id` (reader), different processes.
**Trigger:** Always — the reader starts polling before the relay has even `LPOP`ed.
**Data prerequisite:** The relay's `msg_id`.
**State prerequisite:** The bridge process is running and draining.
**Mitigation:** Blocking bounded poll with a documented fallback to `telegram_message_id=0`. The key is `RPUSH`ed (list, not `SET`), so a slow reader cannot miss a fast writer; the reader deletes the key after reading.

### Race 3: Ack-key cross-talk between attempts
**Location:** `telegram:sent:{session_id}` namespace.
**Trigger:** Two announcements reusing one producer id, or a stale un-consumed entry from a prior failed tick.
**Data prerequisite:** One-to-one producer id ↔ announcement.
**State prerequisite:** Fresh key per attempt.
**Mitigation:** Producer id is `upvote-{project_key}-{issue}-{int(time.time())}` — unique per attempt; the key carries a short TTL; the reader deletes on consumption. A test asserts two concurrent attempts never read each other's ids.

### Race 4: Overlapping ticks
**Location:** `ReflectionScheduler` / `_reflection_pool`.
**Trigger:** A tick running longer than the two-hour interval (only possible under Risk 4).
**Data prerequisite:** none.
**State prerequisite:** none.
**Mitigation:** Every gate is derived from artifacts and re-read, so an overlapping tick converges on skip rather than duplicating. Risk 4's bounds make the overlap effectively impossible in the first place.

### Race 5: Worker claims the session before the anchor is set
**Location:** N/A — designed out.
**Mitigation:** The anchor is captured *before* `create_session`, so the session record is born with its final `telegram_message_id`. This is the specific reason the create-then-patch ordering was rejected in §D.

## No-Gos (Out of Scope)

**One deferral, recorded:** retrofitting `session_executor.py:1688`'s bespoke drain-poll onto the new ack primitive. It is worth doing and it is deliberately *not* done here — the primitive should prove itself on a single low-traffic consumer before a live session-execution path depends on it. File it as a follow-up issue at merge time; it is the one follow-up a reader of this plan would want recorded. (See `## Rabbit Holes`.)

Beyond that, nothing is deferred. Every acceptance criterion on issue #2717 (label documentation, the reflection, the scope gate, the one-per-tick cap, artifact-derived decisions, PR skip, the announcement, `create_session` plumbing, reply threading, idempotency, tests, and the feature doc) ships in this PR.

Explicitly *not built*, as design decisions rather than deferrals (each has a `## Verification` anti-criterion below):

- **No claim key.** No Redis key, file, or record is written to mark an issue as claimed. Every decision derives from artifacts that exist for their own reasons.
- **No label mutation.** The reflection never calls `gh issue edit --add-label` / `--remove-label`. `upvote` is a human-owned signal in both directions.
- **No reflection-side stage selection.** No PLAN-vs-BUILD branch; `/sdlc` routes (spike-4).
- **No issue closing.** Gate 4 reports the merged-PR-with-open-issue case as a finding; a human closes it.
- **No message editing or deletion.** The failure path replies; it does not rewrite history.

## Update System

- `config/reflections.yaml` gains an entry. `/update` regenerates each machine's filtered copy via `tools/reflection_machine_filter.filter_reflections_for_machine`; the new entry carries **no** `project_key`, so it stays enabled on every machine and gates ownership at runtime. Confirm the filtered copy on at least one non-`valor` machine retains it.
- No new dependencies, secrets, or config files.
- No migration: no Popoto model changes (the ack key is a plain ephemeral Redis list; `telegram_message_id` is an existing property over an existing `DictField`). The Popoto migration requirement in `docs/sdlc/do-plan.md` does not apply, and that must remain true — if the build finds itself adding a model field, the design has drifted.
- **Restart required**: the change touches `bridge/telegram_relay.py` (bridge) and the reflection worker. After merge, `/update` → `./scripts/valor-service.sh restart` plus a reflection-worker restart. Note the ack writer lives in the bridge and the reader in the reflection process — if only one restarts, anchoring silently degrades to `telegram_message_id=0`. Call this out in the feature doc.

## Agent Integration

- **No new MCP tool or `[project.scripts]` entry.** The reflection is invoked by the scheduler through its `callable:` dotted path, not by the agent.
- `tools/valor_session.py` already ships as the `valor-session` console script; the new `--telegram-message-id` argument is reachable from the agent's Bash tool with no `pyproject.toml` change.
- **The bridge does need a code change** — `bridge/telegram_relay.py::process_outbox` publishes the ack. This is bridge-internal; nothing new is imported into `bridge/telegram_bridge.py`.
- Integration coverage: a test drives the real `create_session` → `_push_agent_session` path and asserts the persisted `AgentSession.telegram_message_id` matches what was passed, so the agent-visible contract (`TELEGRAM_REPLY_TO` export at `agent/sdk_client.py:502`) is proven end to end rather than at the boundary.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/upvote-autonomous-sdlc-pickup.md` — the `upvote` contract, the schedule, the scope gate, the four skip gates and why each fails closed, the ordering and ceiling decisions with their rationale, the announce-then-create trade-off and its failure path, and the restart-both-processes note.
- [ ] Add a row to `docs/features/README.md`.
- [ ] Update `docs/features/eng-session-architecture.md` — Eng sessions now have a third origin (human message, `sdlc_progress` recovery, and now autonomous pickup).
- [ ] Cross-link from `docs/features/bridge-worker-architecture.md` where the outbox is described, documenting `telegram:sent:{session_id}` as a producer-readable ack.

### Repo Instructions
- [ ] Add an `upvote` row to the GitHub Issue Labels table in `CLAUDE.md`: "Pre-approved for autonomous SDLC pickup — a scheduled reflection may start a lane on this issue without further human input." (The GitHub label description itself already says this — verified at plan time — so this task is documentation only.)

### Inline Documentation
- [ ] Module docstring on `reflections/sdlc_upvote_lanes.py` stating the start/recovery split against `sdlc_progress.py`, the no-claim-key/no-label-mutation invariants, and the worst-case per-tick time budget.
- [ ] Docstring on the relay ack helper naming the key, its TTL, and the single-consumer/delete-on-read contract.
- [ ] Grain-of-salt comments on `UPVOTE_LANE_MAX_LIVE`, `UPVOTE_ANCHOR_WAIT_S`, `UPVOTE_CANDIDATE_SCAN_MAX`, and `UPVOTE_FAILURE_BACKOFF_S` marking them provisional and env-overridable.
- [ ] The module docstring states the closed-form aggregate per-tick budget `projects × (UPVOTE_CANDIDATE_SCAN_MAX × gh_timeout + UPVOTE_ANCHOR_WAIT_S)`, and states that gate 1.5 is a clock-expiring backoff rather than a claim (so the "No claim key" invariant is not misread as violated).

## Success Criteria

- [ ] `CLAUDE.md`'s labels table documents `upvote` (the GitHub label description already exists).
- [ ] `config/reflections.yaml` registers `sdlc-upvote-pickup` with `cron: 0 6-22/2 * * *; tz=America/Los_Angeles`, and a test proves that entry validates and computes a next-due time in the expected local window.
- [ ] Projects without an `Eng:` group are skipped; non-owned projects are skipped before any subprocess runs.
- [ ] At most one issue is picked per project per tick.
- [ ] No claim key is written and no label is mutated — asserted by anti-criteria, not just by review.
- [ ] An issue with an open PR is skipped; an issue with a *merged* PR is skipped and reported.
- [ ] On pickup, the announcement is enqueued to the `Eng: X` group's `chat_id` and its message id is captured from the relay ack.
- [ ] `create_session` accepts `telegram_message_id`, plumbs it in place of the `0` at `tools/valor_session.py:639`, and the created session's `telegram_message_id` equals the announcement id.
- [ ] Subsequent output threads under the announcement (proven via the persisted `telegram_message_id` → `TELEGRAM_REPLY_TO` contract).
- [ ] Two consecutive ticks start exactly one lane for the same issue.
- [ ] A candidate whose `create_session` failed within `UPVOTE_FAILURE_BACKOFF_S` is not re-announced on the following tick (no announce/retract loop in the `Eng: X` group).
- [ ] **Human-visible outcome:** after a pickup, a reader scrolling the `Eng: X` group sees one announcement with the lane's subsequent messages collapsed under it as replies, not a flat stream of orphaned updates. Verified by a human reading the group after the first real pickup (or a manual `valor-session create --telegram-message-id <id>` dry run into a test group), and recorded in the feature doc. This is the criterion the mechanical `telegram_message_id` → `TELEGRAM_REPLY_TO` rows are a proxy for; the proxy passing while this fails means the feature did not work.
- [ ] Create-failure posts a threaded retraction; anchor-timeout still starts the lane and records a finding.
- [ ] `docs/features/upvote-autonomous-sdlc-pickup.md` exists and is indexed in `docs/features/README.md`.
- [ ] Tests pass (`/do-test`); lint and format clean.
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (plumbing)** — Name: `plumbing-builder`; Role: `create_session` parameter + CLI flag, relay ack write/read, `resolve_eng_group`, `_gh_issue_list` `createdAt`; Agent Type: `builder`; Resume: true
- **Builder (reflection)** — Name: `reflection-builder`; Role: `reflections/sdlc_upvote_lanes.py` and its YAML registration; Agent Type: `builder`; Resume: true
- **Test engineer** — Name: `pickup-tester`; Role: the new test module plus updates to the impacted suites; Agent Type: `test-engineer`; Resume: true
- **Documentarian** — Name: `pickup-documentarian`; Role: feature doc, index, `CLAUDE.md` row, cross-links; Agent Type: `documentarian`; Resume: true
- **Validator** — Name: `pickup-validator`; Role: verify every success criterion and every anti-criterion; Agent Type: `validator`; Resume: true

## Step by Step Tasks

### 1. Plumbing: `create_session` anchor parameter
- **Task ID**: build-create-session-anchor
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session_create_core.py`, `tests/unit/test_valor_session_cli.py`, `tests/unit/test_valor_session_sdlc_metadata.py`
- **Informed By**: spike-1 (the anchor cannot come from `valor-telegram send`); recon (hardcoded `0` confirmed at `tools/valor_session.py:639`)
- **Assigned To**: plumbing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `telegram_message_id: int = 0` to `create_session` (`tools/valor_session.py:452`) and pass it to `_push_agent_session` in place of the literal `0` at `:639`.
- Add `--telegram-message-id` to the `create` subparser and thread it through `cmd_create`.
- Log a warning when `telegram_message_id` is non-zero while `chat_id == "0"` (always a caller bug — that chat id routes to the system Room sink).
- Do not change `_derive_sdlc_metadata` or any default behavior; existing callers must be byte-for-byte unaffected.

### 2. Plumbing: relay sent-message-id ack
- **Task ID**: build-relay-ack
- **Depends On**: none
- **Validates**: `tests/unit/test_telegram_relay_chat_log.py`
- **Informed By**: spike-1 (`msg_id` already exists at `bridge/telegram_relay.py:636-646` and is routed at `:978-984`; no producer-side channel)
- **Assigned To**: plumbing-builder
- **Agent Type**: builder
- **Parallel**: true
- In `process_outbox`, in the existing `if msg_id is not None:` success block, add an **opt-in** ack: `if msg_id is not None and message.get("ack_sent_id"):` → `RPUSH telegram:sent:{session_id} {msg_id}` + short `EXPIRE`. Wrap so a failure is logged and non-fatal — the relay must never crash on ack bookkeeping. The flag gate is required, not optional: an unconditional write puts two Redis ops on every outbound message system-wide for one consumer, and it is what keeps `tests/unit/test_telegram_relay_chat_log.py`'s existing assertions valid unmodified.
- Export `await_sent_message_id(session_id, timeout_s) -> int | None`: bounded blocking poll, deletes the key on read, returns `None` on timeout. Docstring states the single-consumer, delete-on-read contract and the TTL.
- Do not refactor `session_executor.py:1688`'s existing drain-poll onto this primitive (rabbit hole).

### 3. Plumbing: `Eng:` group resolution and `createdAt`
- **Task ID**: build-eng-group-resolver
- **Depends On**: none
- **Validates**: `tests/unit/reflections/test_pm_briefings_collector.py`, new fixtures in `tests/unit/reflections/test_sdlc_upvote_lanes.py`
- **Informed By**: recon (no prefix-based resolver exists; `sdlc_progress` hardcodes `"Eng: Valor"`); live config (8/9 projects have one, `royop` does not)
- **Assigned To**: plumbing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `resolve_eng_group(project) -> tuple[str, int] | None` to `reflections/utilities.py`: scan `project["telegram"]["groups"]` for keys with the literal `Eng:` prefix, return `(group_name, int(chat_id))`. Return `None` on absence, malformed entry, missing `chat_id`, or non-integer `chat_id`. No substring fallback, no default chat.
- Extend `_gh_issue_list` (`reflections/pm_briefings/collector.py:95`) in two additive ways: request `createdAt` in `--json`, and add `extra_args: list[str] | None = None` spliced into the `gh` argv. Keep `limit: int = 20` as the default. `_collect_open_bugs` / `_collect_upvote_queue` must be byte-for-byte unaffected — assert the default call's argv in `tests/unit/reflections/test_pm_briefings_collector.py`, and assert the `extra_args` call shape separately.
- Do **not** attempt to fix ordering by raising `limit`. The reflection passes `extra_args=["--search", "sort:created-asc"]` so `gh` sorts server-side before truncating (§A); a bigger page only moves the starvation cliff.

### 4. The reflection module
- **Task ID**: build-upvote-reflection
- **Depends On**: build-create-session-anchor, build-relay-ack, build-eng-group-resolver
- **Validates**: `tests/unit/reflections/test_sdlc_upvote_lanes.py`
- **Informed By**: spike-3 (use `PipelineLedger.get(repo, N)`, not `derive_from_durable_signals`); spike-4 (no stage selection); spike-5 (session-slug gate replaces a debounce key); spike-2 (bounded waits — the scheduler cannot cancel this thread)
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/sdlc_upvote_lanes.py` with `run_sdlc_upvote_lanes() -> dict` delegating to `run_per_project_audit(_pick_up_upvoted, name="sdlc-upvote-pickup")`.
- Honor `SDLC_UPVOTE_PICKUP_ENABLED=false` → `status: "disabled"`.
- Per-project gates in order: `machine_owns_project` → `resolve_eng_group` → `_project_repo`.
- Candidates via `_gh_issue_list(repo, ["upvote"], cwd, extra_args=["--search", "sort:created-asc"])` — **server-side** oldest-first — then a client-side `createdAt`/`number` ascending sort purely as a deterministic tie-break (§A).
- Slice the sorted list to `UPVOTE_CANDIDATE_SCAN_MAX` (default 10, env-overridable, grain-of-salt comment) **before** the gate loop, so aggregate per-tick work is bounded; oldest-first means this truncates the tail and FIFO survives (§B).
- Enforce `UPVOTE_LANE_MAX_LIVE` (default 3, env-overridable, grain-of-salt comment) from open `session/sdlc-*` PRs plus live locks on candidates (§B); emit a finding when at the ceiling.
- Per-candidate skip gates 1, 1.5, 2, 3, 4 in the documented order, all fail-closed (§C). Gate 1.5 is the failure backoff: terminal-FAILED `AgentSession(slug=sdlc-N)` newer than `UPVOTE_FAILURE_BACKOFF_S` (named, env-overridable, provisional) → skip, reusing gate 1's query with a status + recency filter. Gate 4 uses `--state all` and emits a finding on merged-PR-with-open-issue.
- Announce (payload carries `"ack_sent_id": True`) → `await_sent_message_id` bounded by `UPVOTE_ANCHOR_WAIT_S` (default 20s, provisional) → re-read gates 1 and 3 → `create_session(..., chat_id=str(eng_chat_id), telegram_message_id=anchor)`.
- Producer id `upvote-{project_key}-{issue}-{int(time.time())}` (§Race 3).
- On create failure or a positive re-read, enqueue the threaded retraction and return the outcome as a finding, distinguishing benign (someone else started it) from error.
- Register the entry in `config/reflections.yaml` exactly as specified in §F — no `project_key:` key.
- Every subprocess call carries an explicit timeout; the module docstring states the worst-case per-project budget.

### 5. Tests
- **Task ID**: test-upvote-pickup
- **Depends On**: build-upvote-reflection
- **Validates**: the full list in `## Test Impact`
- **Assigned To**: pickup-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/reflections/test_sdlc_upvote_lanes.py` modeled on `test_sdlc_progress_check.py`: each skip gate independently (including gate 1.5's failure backoff — a recent terminal-FAILED session skips, an expired one does not — and `_lock_says_live → None` skipping), ordering, one-per-tick, the ceiling, the `Eng:`-group scope gate (fixtures with no Eng group and with a malformed `chat_id`), the machine-ownership gate issuing zero subprocess calls, two consecutive ticks starting exactly one lane, the anchor happy path, anchor timeout, create failure + retraction, and ack cross-talk isolation.
- Add a registration test that loads the **real** `config/reflections.yaml`, builds the `ReflectionEntry`, calls `validate()`, and asserts `compute_next_due()` lands in the 06:00–22:00 America/Los_Angeles window (Risk 1 — this is the first `cron:` entry in that file).
- Update the impacted suites listed in `## Test Impact`.
- **Ordering test (must be able to fail):** the fixture is **larger than `_gh_issue_list`'s page size** and asserts the pick is the true oldest. A 3-item fixture passes against the defective client-side-sort implementation and therefore proves nothing. Additionally assert the constructed `gh` argv contains `--search sort:created-asc`, so the server-side sort is verified structurally and not only through a stub that happens to return sorted data.
- **Scan-cap test:** with more candidates than `UPVOTE_CANDIDATE_SCAN_MAX`, assert gate-loop subprocess calls are capped and that the retained slice is the oldest N.
- **Aggregate budget test (Risk 4):** stub a never-arriving ack and assert the whole `run_sdlc_upvote_lanes()` call returns within `projects × (UPVOTE_CANDIDATE_SCAN_MAX × gh_timeout + UPVOTE_ANCHOR_WAIT_S)` — the full run, not one project.
- **Ack opt-in test:** assert `process_outbox` performs **no** `telegram:sent:*` write for a payload without `ack_sent_id`, and does write for one with it.
- Add an integration test asserting the persisted `AgentSession.telegram_message_id` matches what `create_session` was given.
- Run via `scripts/pytest-clean.sh`, never bare `pytest`.

### 6. Documentation
- **Task ID**: document-upvote-pickup
- **Depends On**: test-upvote-pickup
- **Assigned To**: pickup-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Everything in the `## Documentation` section, including the restart-both-processes note (bridge writes the ack, reflection worker reads it).

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-upvote-pickup
- **Assigned To**: pickup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every `## Success Criteria` row and every `## Verification` row, including the anti-criteria.
- Confirm no Popoto model field was added (the design says none is needed; a field would mean drift).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/reflections/test_sdlc_upvote_lanes.py tests/unit/test_valor_session_create_core.py tests/unit/test_telegram_relay_chat_log.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Reflection registered | `grep -c 'sdlc-upvote-pickup' config/reflections.yaml` | output > 0 |
| Cron schedule present | `grep -A6 'name: sdlc-upvote-pickup' config/reflections.yaml \| grep -c '0 6-22/2 \* \* \*'` | output > 0 |
| Entry validates and schedules | `python -c "from agent.reflection_scheduler import load_reflections_config as L; e=[x for x in L() if x.name=='sdlc-upvote-pickup'][0]; e.validate(); from agent.reflection_schedule import compute_next_due; print(compute_next_due(e.schedule, None))"` | exit code 0 |
| `create_session` takes the anchor | `python -c "import inspect,tools.valor_session as m; assert 'telegram_message_id' in inspect.signature(m.create_session).parameters"` | exit code 0 |
| Hardcoded `0` is gone | `grep -n 'telegram_message_id=0' tools/valor_session.py` | exit code 1 |
| Feature doc exists | `test -f docs/features/upvote-autonomous-sdlc-pickup.md` | exit code 0 |
| Feature doc indexed | `grep -c 'upvote-autonomous-sdlc-pickup' docs/features/README.md` | output > 0 |
| `CLAUDE.md` documents the label | `grep -c '`upvote`' CLAUDE.md` | output > 0 |
| **Anti-criterion:** no label mutation | `! grep -qE 'issue edit.*--(add\|remove)-label' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no issue closing | `! grep -qE 'gh.*issue.*close' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no claim key | `! grep -qiE '(setnx\|set_nx\|nx\s*=\s*True)' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no stage selection | `! grep -qE '"(PLAN\|BUILD)"' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no raw Redis on Popoto keys | `! grep -qE '\.(delete\|srem\|sadd\|zrem)\(' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no Popoto model change | `git diff origin/main --stat -- models/` | output does not contain `models/` |

Every anti-criterion row is written as `! grep -q…` returning **exit code 0** on success, deliberately. `grep -c` exits **1** when it finds zero matches, so a validator running `grep -c …` as a pass/fail command reads every *satisfied* anti-criterion as a failure — the trap the earlier draft of this table walked into. The rows also match **code constructs**, never English prose: the claim-key row greps for `setnx` / `set_nx` / `nx=True`, not the word `claim`, because `## Documentation` mandates a module docstring that states the no-claim-key invariant in prose and a prose-matching row would be failed by its own required documentation.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness; History & Consistency | Technical Approach §A / Task 3 — the starvation-free FIFO contract is delegated to `_gh_issue_list`, which caps at `limit: int = 20` (`reflections/pm_briefings/collector.py:95`) and passes no sort flag, so `gh issue list` returns its default newest-first page. Sorting that truncated page client-side by `createdAt` ascending yields the oldest of the newest 20 — above a 20-issue backlog the genuinely oldest approved issue is never a candidate, which is exactly the starvation §A exists to eliminate. | §A, Task 3, Task 5 (2026-08-10) | Make the ordering server-side, not client-side. Extend `_gh_issue_list` with `extra_args: list[str] \| None = None` and have the reflection call it with `["--search", "sort:created-asc"]` (gh applies `--search` sort before `--limit`); keep `limit: int = 20` as the default so `_collect_open_bugs` / `_collect_upvote_queue` are byte-for-byte unaffected and the reflection passes its own explicit limit. A larger `--limit` alone is NOT equivalent — only the server-side sort is correct at any backlog size. The ordering test must use a fixture larger than the page size and assert the pick is the true oldest; a 3-item fixture cannot detect this defect. Assert the constructed argv for both call shapes in `tests/unit/reflections/test_pm_briefings_collector.py`. |
| CONCERN | Risk & Robustness | Risk 4 / §C / Task 4 — per-tick work is unbounded in the number of open `upvote` issues. Gate 4 shells one `gh pr list --head session/sdlc-{N} --state all` per candidate and §B's ceiling reads `_lock_says_live` per candidate; both run for every skipped issue, across all nine projects, on top of a 20s `UPVOTE_ANCHOR_WAIT_S` per pickup. Spike-2 establishes nothing can cancel a wedged sync reflection, yet only the per-project budget is bounded, never the aggregate. | §B (candidate scan cap), §C, Task 4, Task 5, Risk 4 (2026-08-10) | After the §A sort, slice the candidate list to a named env-overridable `UPVOTE_CANDIDATE_SCAN_MAX` (provisional default ~10, grain-of-salt comment) before the gate loop. Because the sort is oldest-first this truncates the tail, not the head, so FIFO is preserved. The module docstring must then state the aggregate ceiling `projects × (SCAN_MAX × gh_timeout + UPVOTE_ANCHOR_WAIT_S)`, and Risk 4's test must assert the whole `run_sdlc_upvote_lanes()` call — not one project — returns within budget against a never-arriving ack. |
| CONCERN | Risk & Robustness | §D / Risk 5 — there is no failure backoff. A candidate whose `create_session` fails deterministically (missing checkout, worktree collision, bad `project_key`) re-announces and re-retracts in the `Eng: X` group every tick, indefinitely — nine announcement/retraction pairs a day for one broken issue. Risk 5's mitigation ("the next tick genuinely does retry because nothing was claimed") is the amplifier, not the fix. The predecessor `sdlc_progress.py` carries cooldowns and attempt counting for exactly this, and the plan drops them while claiming to reuse its gate vocabulary verbatim. | §C gate 1.5, §D (failure backoff), Task 4, Task 5, Success Criteria (2026-08-10) | Add gate 0.5 before the announcement: skip candidate N when a terminal-FAILED `AgentSession` with `slug == f"sdlc-{N}"` exists whose `created_at` is newer than `UPVOTE_FAILURE_BACKOFF_S` (named, env-overridable, provisional). Gate 1 already runs `AgentSession.query.filter(slug=f"sdlc-{N}")`, so this is a status + recency filter on an existing query — no new Redis key, no extra round trip. It is a backoff, not a claim (it never marks the issue taken and it expires by clock), so the "No claim key" No-Go holds; §D must state that explicitly or the Task 7 validator will flag it as a violation. |
| CONCERN | Scope & Value | Solution → Key Elements / Task 2 — the ack is specified as an unconditional write inside `process_outbox`'s success block, i.e. two extra Redis ops on the send path for every outbound Telegram message system-wide, minting a `telegram:sent:{session_id}` key nobody reads for every ordinary session, to serve one consumer that fires at most once per project per two hours. Architectural Impact calls the key "single-consumer", which is true of the reflection's key but not of the write. | Key Elements, Data Flow 7-8, Task 2, Test Impact, Task 5 (2026-08-10) | Guard the write on an explicit producer opt-in: `if msg_id is not None and message.get("ack_sent_id"):` then `RPUSH` + `EXPIRE`. The reflection sets `"ack_sent_id": True` in the announcement payload it `RPUSH`es to `telegram:outbox:{producer_id}`; no other producer sets it, so the send path is byte-for-byte unchanged for existing traffic and `tests/unit/test_telegram_relay_chat_log.py`'s current assertions hold without modification. Keep the non-fatal `try/except` wrapper either way. |
| CONCERN | History & Consistency | `## Verification` anti-criteria vs. `## Documentation` — two contradictions. (a) The claim-key row greps for the prose token `claim` in `reflections/sdlc_upvote_lanes.py` expecting zero matches, but `## Documentation` mandates a module docstring stating the "no-claim-key/no-label-mutation invariants" — the required documentation makes the anti-criterion fail. (b) All five anti-criterion rows use `grep -c` with expected "match count == 0", but `grep -c` exits 1 on zero matches, so a Task 7 validator running them as pass/fail reads every satisfied anti-criterion as a failure. | ## Verification (all five rows rewritten + convention note) (2026-08-10) | Rewrite each anti-criterion row as `! grep -qE '<pattern>' <file>` with expected exit code 0 — the negation makes exit 0 mean "pattern absent", which is what the row asserts, and removes the `grep -c` exit-code trap. For the claim-key row, match constructs rather than English prose: `! grep -qiE '(setnx\|set_nx\|nx\s*=\s*True)' reflections/sdlc_upvote_lanes.py`. The label-mutation and issue-closing rows are already construct-shaped and need only the exit-code fix. |
| NIT | Scope & Value | Success Criteria — every criterion is mechanical (parameter present, id equal, grep count). The stated user outcome, that the group reads as one conversation per issue, has no criterion checking what a human sees in the `Eng: X` group; the nearest row proves the `telegram_message_id` → `TELEGRAM_REPLY_TO` mechanism, not the outcome. | Success Criteria — human-visible outcome row added (2026-08-10) | Accepted. A human-read criterion now sits alongside the mechanical proxies and names the proxies as proxies. |
| NIT | Scope & Value | §G / Task 1 — `--telegram-message-id` is added "for parity and manual testing", but the only programmatic caller imports `create_session` directly. It is new surface justified by a use it does not have; worth keeping as a documented debugging affordance rather than an unused flag a future reader deletes. | §G, Task 1 (2026-08-10) | Accepted. Kept, and rejustified in §G as the manual-reproduction path for the human-visible criterion; the flag's help text says so. |
| NIT | History & Consistency | `## No-Gos` vs. `## Rabbit Holes` — No-Gos opens "Nothing deferred — every relevant item is in scope", but Rabbit Holes defers the `session_executor.py:1688` drain-poll retrofit to "a separate change, after this one proves the primitive". That is a deferral, and it is the one follow-up a reader would want recorded. | ## No-Gos (2026-08-10) | Accepted. No-Gos now opens by recording the `session_executor.py:1688` retrofit as the one deferral, to be filed as a follow-up issue at merge. |

---

## Open Questions

All four open questions carried by issue #2717 are resolved in this plan and are **not** open:

1. **Ordering** → oldest-first by `createdAt`, tie-broken by issue number (Technical Approach §A).
2. **Concurrency ceiling** → yes, `UPVOTE_LANE_MAX_LIVE` default 3 per project, counted approximately from open `session/sdlc-*` PRs plus live locks (§B).
3. **Announce-then-create atomicity** → announce first, capture the id via a new relay ack, then create; threaded retraction on failure; bounded degradation to an unanchored start on ack timeout (§D).
4. **Post-merge `upvote` residue** → the `--state open` filter is sufficient; terminal state is "issue closed"; the one hole (merged PR, issue still open) is closed by skip gate 4 using `--state all` (§E).

No questions remain for the supervisor.
