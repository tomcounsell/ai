---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2717
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-10T13:01:42Z
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

**Live-config re-verification (new, not in the issue):** 8 of 9 projects in `projects.json` have an `Eng: X` group; `royop` has none. The scope gate is load-bearing on real config, not hypothetical. The `upvote` label **already carries** the description "Pre-approved for autonomous SDLC pickup" — that half of AC 1 is done; only the `CLAUDE.md` table row remains. As of this writing, one `upvote` issue is open (#2716), but it already has `PLAN`/`CRITIQUE` progress recorded in `PipelineLedger`, so gate 2 skips it — the first production tick will be a no-op because the gates work, not because the queue is empty. The feature must be exercised by tests, not by waiting for a real pickup.

## Prior Art

- **PR #2710 / issue #2696 — "SDLC stall auto-resume"** (merged 2026-08-10). Built `reflections/sdlc_progress.py`: per-project audit, machine-ownership gate, issue-lock liveness with a fail-closed unknown state, cooldowns, attempt counting, escalate-once. This plan is its mirror image and reuses its gate vocabulary verbatim. Succeeded; no known regressions.
- **Issue #1197 — "Daily PM audio briefing reflection"** (closed 2026-05-01). Built `reflections/pm_briefings/`, including `_collect_upvote_queue` (`collector.py:159`) — the repo already lists `upvote` issues per project. Succeeded. This plan reuses `_gh_issue_list` rather than writing a third `gh issue list` wrapper.
- **Issue #1191 — reply-to threading.** Established `AgentSession.telegram_message_id` → `TELEGRAM_REPLY_TO` → outbound `reply_to`. Succeeded, but only ever fed by bridge-originated messages; no programmatic producer has ever set it. That gap is what this plan closes.
- **Issue #1828 — reflections moved out of the worker event loop.** Established `python -m reflections` as its own launchd process. Relevant because it means the reflection has **no Telethon client**, which forecloses the obvious implementation.
- **Issue #2566 (OPEN) — "critique-resume-probe / critique-roster-check console shims crash with `ModuleNotFoundError: No module named 'tools'` — same wrong-interpreter class as #2536"** (#2536 closed). This is *live*, not historical: `which valor-telegram` on this machine resolves to `~/Library/Python/3.12/bin/valor-telegram`, a stale shim whose shebang is the system 3.12 framework interpreter, and it crashes on import. Any `subprocess.run(["valor-telegram", …])` in this plan would therefore work only inside the reflection worker (whose `com.valor.reflection-worker.plist` happens to pin `.venv/bin` early in `PATH`) and break in every test, operator repro, and non-launchd invocation. That is why §I invokes the module (`[sys.executable, "-m", "tools.valor_telegram", …]`), never the console script — the failure class is open and unfixed, so the plan must not add a new instance of it.

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
- **Finding**: Two problems with `derive_from_durable_signals` (`:1234`). (a) It computes `plan_path = f"docs/plans/{session.slug}.md"` from the session slug — for a lane on issue #2717 that is `docs/plans/sdlc-2717.md`, but this repo names plan docs from the issue **title** (`docs/plans/upvote-autonomous-sdlc-pickup.md`). Plan-doc existence keyed on `sdlc-{N}` is therefore always false; it is not a usable signal. (b) `_durable_run` (`:1463`) invokes `git`/`gh` with **no `cwd`**, so it inspects whichever repo the calling process sits in — structurally wrong for a reflection iterating every project in `load_local_projects()`.
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
- **Impact on plan**: Gate on "non-terminal `AgentSession` with this slug **and this project's `project_key`** exists" as the primary already-started signal (the `project_key` half is required because `slug` is a global `KeyField` and issue numbers repeat across repos — §C). No new Redis key, no TTL to tune, and the no-claim-key constraint is honored in spirit as well as letter.

## Data Flow

1. **Trigger**: `ReflectionScheduler` fires `sdlc-upvote-pickup` on its cron tick, resolves `reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes`, and runs it in `_reflection_pool`.
2. **Fan-out**: `run_per_project_audit(_pick_up_upvoted, name="sdlc-upvote-pickup")` iterates `load_local_projects()` — the projects in `projects.json` whose `working_directory` **exists on disk**, which is `len(load_local_projects())` and was **5** (of 9 configured) when measured at revision time. Every budget expression below uses `len(load_local_projects())`, never a literal, because that number is neither stable nor equal to the config count.
3. **Per-project gates**: `machine_owns_project(slug)` → `resolve_eng_group(project)` (new) → `_project_repo(project)`. Any miss returns `status: "skipped"`.
4. **Candidate list**: `_gh_issue_list(repo, ["upvote"], cwd=working_directory, extra_args=["--search", "sort:created-asc"])` → open issues carrying `upvote`, **server-side** oldest-first, then sliced to `UPVOTE_CANDIDATE_SCAN_MAX`.
5. **Ceiling**: one `gh pr list --state open --json headRefName` per project counts live `session/sdlc-*` lanes; at or above the cap the project returns a finding and starts nothing.
6. **Per-candidate gates** (first survivor wins, then stop): non-terminal `AgentSession(slug=sdlc-N)` → recent terminal-FAILED `AgentSession(slug=sdlc-N)` inside `UPVOTE_FAILURE_BACKOFF_S` → `PipelineLedger.get(repo, N)` has recorded stages → `_lock_says_live(N)` is `True` or `None` → any PR (open **or merged**) on `session/sdlc-{N}`.
6b. **Budget admission**: the surviving candidate is picked up only if the run's remaining wall-clock budget covers a whole worst-case pickup (`UPVOTE_PICKUP_WORST_CASE_S`, §B). Otherwise the project declines with a finding and nothing is announced — declining is free, because nothing was claimed and the next tick is two hours away.
7. **Announce**: the reflection **shells out to the existing sender, by module path** — `[sys.executable, "-m", "tools.valor_telegram", "send", "--chat", str(eng_chat_id), "--session-id", producer_id, "--ack-sent-id", "--no-read-the-room", text]` with an explicit subprocess timeout and a **scrubbed env** (`VALOR_SESSION_ID`, `TELEGRAM_REPLY_TO`, `AGENT_SESSION_ID` removed — §I). Never the bare `valor-telegram` console script (Prior Art #2566). It does **not** build an outbox payload of its own (§I). `cmd_send` applies `_linkify_text`, the 4096-char guard, and `bridge.promise_gate.cli_check_or_exit`, then `RPUSH`es `telegram:outbox:{producer_id}` with `"ack_sent_id": True` in the payload. Producer id is `upvote-{project}-{N}-{ts}`.
8. **Relay** (`bridge/telegram_relay.py::process_outbox`, other process): `LPOP` → `send_markdown` → `msg_id` → existing `_record_sent_message` bookkeeping **plus**, *only when the payload carries `ack_sent_id`*, `bridge.outbox_ack.publish_sent_message_id(session_id, msg_id)` → `RPUSH telegram:sent:{producer_id} {msg_id}` with a TTL.
9. **Anchor readback**: reflection blocking-polls via `bridge.outbox_ack.await_sent_message_id(producer_id, timeout_s)` with a bounded budget, then deletes the key. `bridge/outbox_ack.py` is a leaf module — it imports the Redis client and nothing else, so the reflection process never imports Telethon (§I).
10. **Re-gate and create**: re-read the two cheap liveness gates, then `create_session(message=..., role="eng", session_type="eng", slug=f"sdlc-{N}", project_key=..., chat_id=str(eng_chat_id), telegram_message_id=anchor_id)`. On `CreateResult(success=False)` the reflection writes its own failure-backoff key (§D).
11. **Output**: the worker starts the session; `agent/sdk_client.py:502` exports `TELEGRAM_REPLY_TO=anchor_id`; every outbound message from the lane threads under the announcement in the `Eng: X` group.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `create_session` gains `telegram_message_id: int = 0` (and `chat_id` becomes genuinely load-bearing rather than a `"0"` placeholder) — purely additive, every existing caller keeps today's behavior. `valor-telegram send` gains `--session-id` and `--ack-sent-id`. `scripts/update/reflection_register.register_reflection` gains optional `cron` / `cron_tz` / `timeout` keyword arguments (its two existing callers pass `cadence=` and are unaffected). One new leaf module, `bridge/outbox_ack.py`.
- **Coupling**: slightly *decreased*. Today the sent-message id is reachable only by a caller that already owns a matching `AgentSession`. The ack key makes it a general producer-side capability, which is why `session_executor`'s bespoke drain-poll exists at all. The reflection depends on `bridge/outbox_ack.py` (a leaf) and on the `valor-telegram` console script — not on the relay module or on Telethon.
- **Data ownership**: unchanged. No new durable state. Two ephemeral, clock-expiring Redis keys, neither Popoto-managed: the single-consumer ack list `telegram:sent:{producer_id}` and the failure-backoff key `upvote:pickup:failed:{repo}:{N}` (§D).
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
- **Sent-id ack in a neutral leaf module (`bridge/outbox_ack.py`, opt-in per message)** — owns the `telegram:sent:{session_id}` key constant, `publish_sent_message_id(session_id, msg_id)` (called by the relay) and `await_sent_message_id(session_id, timeout_s)` (called by producers). Closes the gap spike-1 found. It lives in its own module rather than in `bridge/telegram_relay.py` because that module opens with `import redis` **and** `from telethon.errors import FloodWaitError`; a reader importing it would drag Telethon into the reflection process that spike-2 established has no Telegram client. `bridge/outbox_ack.py` imports the Redis client and nothing else. **The write is gated on the payload flag `ack_sent_id`** — `if msg_id is not None and message.get("ack_sent_id"):` — because an unconditional write would put two extra Redis ops on the send path for *every* outbound Telegram message system-wide and mint a key nobody reads for every ordinary session, to serve one consumer that fires at most once per project per two hours. Only the announcement payload sets the flag, so existing traffic is byte-for-byte unchanged and `tests/unit/test_telegram_relay_chat_log.py`'s current assertions hold without modification. The non-fatal `try/except` wrapper stays regardless: the relay must never crash on ack bookkeeping.
- **One sender, not a third payload producer** — the announcement and the retraction go out through `tools.valor_telegram`'s `send`, invoked as `[sys.executable, "-m", "tools.valor_telegram", …]` rather than by console-script name (§I, Prior Art #2566), so `_linkify_text`, the 4096-char guard and the promise gate stay on the one send path that already owns them and no stale PATH shim can intercept the call.
- **Update-time registration into the vault registry** — `config/reflections.yaml` is gitignored and clobbered from the vault on every `/update`, so the entry is shipped by tracked code in `scripts/update/reflection_register.py` (§F), not by a hand edit.
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
- The reflection calls `_gh_issue_list(repo, ["upvote"], cwd=..., limit=UPVOTE_CANDIDATE_SCAN_MAX, extra_args=["--search", "sort:created-asc"])`. `gh` applies the `--search` sort **before** `--limit`, so the returned page is the oldest N. Verified live at plan time against `tomcounsell/ai` (returned #728 2026-04-06 first, ascending).
- **`limit` and the scan cap are one knob, not two.** An earlier draft passed an independent `limit` *and* re-sliced the result to `UPVOTE_CANDIDATE_SCAN_MAX` after the sort. Because the sort is server-side oldest-first, both cut from the same end and only the smaller ever bound: with the inherited `limit=20` and `SCAN_MAX=10`, ten issues were fetched and discarded unread, and a maintainer raising `SCAN_MAX` would get no effect at all until they also found `limit` at a different call site in a different file. So `UPVOTE_CANDIDATE_SCAN_MAX` **is** the `limit`, and there is no post-sort slice — only the deterministic `createdAt`/`number` ascending tie-break stays. This does not reopen "raising `--limit` is not an equivalent fix" above: that was about ordering under a *client-side* sort, and it still holds.
- `_gh_issue_list` also requests `createdAt` in its `--json` field list (additive; existing callers ignore unknown keys). The client-side `createdAt`-ascending, `number`-ascending sort stays as a deterministic tie-break over the already-correct page — it is a stabilizer, not the ordering mechanism.

The ordering test **must** use a fixture larger than the page size and assert the pick is the true oldest; a 3-item fixture cannot detect this defect. Both call shapes (default and `extra_args`) assert their constructed argv in `tests/unit/reflections/test_pm_briefings_collector.py`.

#### B. Concurrency ceiling (resolves open question 2) — **yes, cap live auto-started lanes per project**

One-per-tick throttles the *ramp* but not the *steady state*: nine ticks a day (06:00–22:00 every two hours) permits eight new lanes per project per day, indefinitely. Each lane is a `claude -p` subprocess plus a worktree plus eventually a PR that a human must review. Unbounded, this converts a queue problem into a review-backlog problem and a memory-pressure problem on a machine that already has documented xdist/worktree pressure.

- Constant `UPVOTE_LANE_MAX_LIVE`, default **3**, read via an `_env_int` helper mirroring `sdlc_progress._env_float`. Per the repo's provisional-magic-number convention this is a named, env-overridable constant carrying a grain-of-salt comment: 3 is a starting guess chosen so a single project cannot monopolize the worker, not a measured optimum.
- Live count = open PRs whose `headRefName` matches `session/sdlc-*` — PR-only, not lock-inclusive (see `## No-Gos` item 0's fourth substitution). One extra `gh pr list --state open --json number,headRefName --limit 100` per project per tick.
- **Known undercount, accepted and documented**: a human-started lane that has not yet opened a PR and whose issue is not `upvote`-labeled is invisible to this count. The ceiling is a ramp brake, not admission control. Making it exact would require enumerating every non-terminal `AgentSession` per project, which is a Redis scan on a hot path for a guard that only needs to be approximately right.
- At or above the cap the project contributes a finding (`"[slug] lane ceiling reached (3/3), N upvote issues waiting"`) and starts nothing. Findings surface in the reflection report, so the backlog is visible rather than silent.

**The per-project cap implies a machine-wide number, and that number is the one that hurts.** `UPVOTE_LANE_MAX_LIVE` is evaluated per project, so with `len(load_local_projects()) == 5` (measured at revision time) the permitted steady state is up to **15** concurrent auto-started lanes on one machine — each a `claude -p` subprocess plus a worktree plus a worktree-local `.venv`. The per-project rationale above ("a single project cannot monopolize the worker") never confronts that aggregate, and 15 is not a number this machine should reach silently. So there is a second, machine-wide ceiling:

- `UPVOTE_LANE_MAX_LIVE_MACHINE`, named and env-overridable, provisional default **5**, grain-of-salt comment. Same provenance as its per-project sibling: a starting guess sized so the machine-wide count cannot exceed the project count by much, not a measured optimum.
- **It costs zero extra subprocess calls.** Each project's live-lane count is already computed for the per-project cap; the run-scoped state object (see the deadline discussion below) accumulates those counts as the sweep proceeds, and a start is refused when `machine_live_total >= UPVOTE_LANE_MAX_LIVE_MACHINE`.
- **Known undercount, accepted and documented, same class as the per-project one**: projects not yet visited in this sweep contribute 0, so the running total is a lower bound that tightens as the sweep proceeds. This biases admission toward projects early in `load_local_projects()` order. That bias is acceptable because FIFO is a *within-project* contract (§A) and was never a cross-project one, and because at most one start per project per tick bounds the total error at `len(load_local_projects())`. Making it exact would mean a full pre-pass of `gh pr list` across every project before starting any — doubling the tick's `gh` cost for a ramp brake.
- At the machine ceiling the project emits `"[slug] machine-wide lane ceiling reached (5/5), N upvote issues waiting"` and starts nothing.

**Candidate scan cap — bounding aggregate per-tick work.** The ceiling bounds *starts*, not *inspection*. Gate 4 shells one `gh pr list` per candidate and the ceiling reads `_lock_says_live` per candidate, and both run for every issue that ends up skipped, across every project in `load_local_projects()`, on top of a `UPVOTE_ANCHOR_WAIT_S` wait per pickup. Spike-2 established that nothing can cancel a wedged sync reflection, so an unbounded candidate list is an unbounded occupancy of a `_reflection_pool` slot.

- `UPVOTE_CANDIDATE_SCAN_MAX` (named, env-overridable, provisional default **10**, grain-of-salt comment) is passed **as `_gh_issue_list`'s `limit`** — it is the single truncation knob, not a second slice layered on one (§A). Because the sort is oldest-first and server-side, `gh` truncates the *tail*, never the head, so FIFO is preserved exactly and nothing is fetched that the gate loop will not read.
**`create_session` is the most expensive call on the path, and an earlier draft of this plan left it out of every budget expression.** `create_session` → `get_or_create_worktree` → `create_worktree` calls `provision_worktree_venv(worktree_dir)` unconditionally (`agent/worktree_manager.py:1341`), which runs `uv sync --all-extras` at `settings.timeouts.uv_sync_s` = **600.0s**, on top of `git worktree add` at `settings.timeouts.git_subprocess_s` = **60.0s**. Every pickup is a cold worktree — the slug is `sdlc-{N}`, unique per issue, so `get_or_create_worktree` never finds an existing one to reuse. One pickup is therefore worth ~660s, and a deadline checked only at loop tops cannot fire mid-create. A budget that omits this term is not a bound.

So the cost model names it, and the run refuses pickups it cannot afford:

- **`UPVOTE_CREATE_WORST_CASE_S` is derived, not a literal**: `settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s` (660.0 with today's defaults). Deriving it means a `TIMEOUTS__UV_SYNC_S` override cannot silently invalidate the plan's arithmetic.
- **`UPVOTE_PICKUP_WORST_CASE_S` is the whole uninterruptible tail of one pickup**: `2 × UPVOTE_GH_TIMEOUT_S + UPVOTE_ANCHOR_WAIT_S + UPVOTE_CREATE_WORST_CASE_S` (announce send, anchor wait, create, possible retraction send) = **740s** with defaults.
- **Admission check, not a mid-flight abort.** Immediately before announcing, the reflection checks `deadline - time.monotonic() >= UPVOTE_PICKUP_WORST_CASE_S`. If it does not fit, the project declines with a finding (`"[slug] insufficient run budget for a pickup; deferring issue #N to the next tick"`) and announces nothing. Declining is free: nothing was claimed, the announcement never went out, and the next tick is two hours away. This is what makes the deadline an actual bound rather than a hope — the expensive call is gated *before* it starts, because there is no way to interrupt it once it has.
- **The consequence, stated plainly:** at 1200s of budget and 740s per pickup, a single tick realistically admits **one** cold pickup, occasionally two. That is an accepted rate limit, not a defect — nine ticks a day still permits up to nine new lanes machine-wide, well above the machine ceiling this section just imposed. If pickups need to be faster, the lever is `UPVOTE_RUN_BUDGET_S` (and the entry timeout that must stay above it), not removing the admission check.

**Aggregate worst case, corrected.** The module docstring states it as `len(load_local_projects()) × (UPVOTE_CANDIDATE_SCAN_MAX × UPVOTE_GH_TIMEOUT_S) + UPVOTE_PICKUP_WORST_CASE_S` — inspection cost scales with the project count; pickup cost does not, because the admission check permits at most one pickup to straddle the deadline. The multiplier is the on-disk project count (**5** when measured at revision time), never the nine entries in `projects.json`.

**Reconciling the budget with the scheduler's own timeout.** The inspection term alone is not self-limiting: with `SCAN_MAX=10` and `UPVOTE_GH_TIMEOUT_S=30` it is ~300s per project, so at 5 on-disk projects the sweep's ceiling is ~1500s and at 9 it would be ~2700s — past `ReflectionEntry.effective_timeout()`'s `DEFAULT_FUNCTION_TIMEOUT = 1800` (`agent/reflection_scheduler.py:40`), which the §F entry previously left unset. Spike-2 established that timeout cannot cancel a sync callable, so an overrun holds a `_reflection_pool` slot past the point the scheduler gave up and `reap_stale_running` can fire against a still-running thread. Multiplying a per-project figure by a growing project count is the wrong shape of fix — it goes stale the moment a project is cloned. So:

- **A wall-clock deadline bounds the whole run.** `UPVOTE_RUN_BUDGET_S` (named, env-overridable, provisional default **1200**) is captured once in `run_sdlc_upvote_lanes()` as `deadline = time.monotonic() + UPVOTE_RUN_BUDGET_S`, before the sweep starts. This is the invariant that stays true as `load_local_projects()` grows.
- **The deadline is enforced by early return, not by breaking the loop — because the reflection does not own the loop.** `run_per_project_audit(audit_one, *, skip_if=None, name)` (`reflections/utilities.py:118`) owns it. The reflection supplies only a per-project callable, which cannot `break`, does not know its iteration index, and cannot abort by raising (each project is wrapped in its own `try/except`, so a raise is swallowed into that project's `error` record). Therefore: `run_sdlc_upvote_lanes()` builds a run-scoped state object holding `deadline` and the machine-wide live-lane accumulator, and passes `functools.partial(_pick_up_upvoted, state=state)` to `run_per_project_audit`. On an expired deadline `_pick_up_upvoted` returns `{"status": "skipped", "findings": ["budget exhausted; project not scanned"], …}` immediately. The sweep still visits every remaining project, but each visit is a cheap early return costing no subprocess call. The deadline is also re-checked at the top of each gate-loop iteration within a project, by the same early-return mechanism.
- **No `{k}/{n}` phrasing.** `run_per_project_audit` exposes no iteration index, and the aggregate report already prefixes each finding with `[slug]`, so the set of budget-skipped projects is legible from the findings list without the reflection inventing a counter it cannot ground.
- **The declared timeout derives from the budget.** The §F entry sets an explicit `timeout:` of `UPVOTE_ENTRY_TIMEOUT_S = 1500`. Both constants live in `reflections/sdlc_upvote_lanes.py`; `scripts/update/reflection_register.py` imports the entry timeout rather than repeating the number, so the YAML and the code cannot drift.
- **Two invariants, both asserted by tests**, because each catches a different way the numbers can go wrong:
  1. `UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S < UPVOTE_ENTRY_TIMEOUT_S` (1230 < 1500). Headroom for the one in-flight `gh` call that may straddle the final deadline check, so the scheduler never gives up on a thread that is still running.
  2. `UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S` (740 < 1200). Without this the admission check could never pass and the feature would ship **silently inert** — the same class of failure as Risk 1, arrived at by arithmetic instead of by config.
- **The `gh` timeout is a named constant**, `UPVOTE_GH_TIMEOUT_S` (default **30**), so the worst-case expression is checkable rather than prose. For the closed form to keep describing the code, `_gh_issue_list` must accept it too: today it exposes no timeout parameter and delegates to `_run(cmd, cwd, timeout: int = 30)` (`reflections/pm_briefings/collector.py:24`), a hardcoded default that merely *happens* to equal the constant, so overriding `UPVOTE_GH_TIMEOUT_S` would leave the candidate-list call at 30s. Task 3 therefore adds an optional `timeout: int | None = None` passthrough alongside `extra_args` (defaulting to today's 30 when absent, so both existing callers are unchanged) and the reflection passes `UPVOTE_GH_TIMEOUT_S` explicitly.

#### C. Skip gates, in evaluation order

Cheapest and most decisive first; **any** gate that cannot answer confidently skips (fail closed — a duplicate lane is strictly worse than a delayed one, and the next tick is only two hours away).

| # | Gate | Skip when | Source |
|---|---|---|---|
| 1 | Session exists | a non-terminal `AgentSession` with `slug == f"sdlc-{N}"` **and `project_key == project["slug"]`** exists | Popoto ORM query (spike-5) |
| 1.5 | Recent create failure | the key `upvote:pickup:failed:{repo}:{N}` exists — written by **this reflection** with `SETEX` on its own observation of `CreateResult(success=False)` | §D backoff |
| 1.6 | Lane started then died | a **terminal-FAILED** `AgentSession` with `slug == f"sdlc-{N}"` **and this project's `project_key`** exists whose `created_at` is newer than `UPVOTE_FAILURE_BACKOFF_S` | same query as gate 1, status + recency filter |
| 2 | Ledger written | `PipelineLedger.get(f"{org}/{repo}", N)` carries any recorded stage state | `agent/pipeline_ledger.py` (spike-3) |
| 3 | Lock live | `_lock_says_live(N)` is `True` **or** `None` (unknown) | **imported**, not copied, from the shared home in `reflections/utilities.py` (§J) |
| 4 | Branch has a PR | `gh pr list --head session/sdlc-{N} --state all` is non-empty | `--state all`, deliberately |

**Gate 1 must match on two fields, because the slug is not project-scoped.** The lane slug is `sdlc-{N}` with no project component — kept that way deliberately, since it matches `sdlc_progress` and the `session/sdlc-{N}` branch convention — but `AgentSession.slug` is a global `KeyField` (`models/agent_session.py:388-390`) while this reflection is multi-project, and two repos routinely share an issue number. A bare `query.filter(slug="sdlc-42")` therefore matches a live lane on *any* project's #42: the candidate would be skipped indefinitely while an unrelated lane lives. That is fail-closed (no duplicate lane) but silently blocking, and it is the same collision the backoff key already guards against by keying on `{org}/{repo}` (§D).

So gate 1 iterates `AgentSession.query.filter(slug=f"sdlc-{N}")` and skips only when a non-terminal row **also** carries `project_key == project["slug"]`. A non-terminal row with a *different* `project_key` is a cross-project collision, not a reason to skip: the reflection emits a finding naming both projects and proceeds, because the per-project and machine-wide ceilings (§B) already bound concurrency and `worker_key`'s own docstring (`models/agent_session.py:662-664`) documents that two same-slug lanes merely serialize on one worker loop rather than corrupting each other. `project_key` is set on every row `create_session` enqueues, so no new plumbing is required. Gate 1.6 applies the same two-field match, for the same reason.

Gate 4 uses `--state all`, not `--state open`. An **open** PR means `sdlc_progress` owns the lane (the issue's own table). A **merged** PR on an issue that is still open means the implementation PR body lacked `Closes #N` — the derived table in the issue would read that as "planned, not built" and restart the lane forever. Gate 4 stops it and emits a finding naming the issue and the merged PR so a human can close it. This is the concrete failure mode behind open question 4.

#### D. Announce-then-create atomicity (resolves open question 3) — **announce first, capture, create, retract on failure**

The three candidate orderings, and why:

- *Create then announce* is atomic in the good direction (no phantom promise) but **loses the anchor**, which is the feature's entire threading requirement. Rejected.
- *Create, announce, then patch the anchor onto the session* (mirroring `session_executor.py:1688`) keeps the anchor but opens a genuine race: the worker can claim the session and spawn the `claude -p` subprocess — which snapshots `TELEGRAM_REPLY_TO` from `agent/sdk_client.py:502` at spawn time — before the anchor is written. The window is seconds on both sides and there is no ordering primitive between the worker's claim and the relay's drain. Rejected as unfixable without a session-level "hold" that does not exist.
- **Announce, capture the id, then create** is chosen. The only failure mode is a phantom promise: announcement lands, `create_session` raises or returns `success=False`, and the group shows an unfulfilled statement.

That failure mode is handled explicitly rather than tolerated: on create failure the reflection posts a short reply **threaded under the announcement** naming the error and stating that the next tick will retry, and returns the failure as a finding. Because there is no claim key and no label mutation, there is genuinely nothing to roll back — the next tick re-derives from artifacts and tries again. A retract-by-delete was considered and rejected: `edit_message`/`delete_message` appear nowhere in the repo, adding them is a bridge capability well beyond this issue's scope, and a visible "that did not work" is better operator signal than a message that quietly vanishes.

**Failure backoff (gate 1.5) — retry, but not forever, and not every tick.** "Nothing was claimed, so the next tick retries" is the correct recovery story for a *transient* failure and an amplifier for a *deterministic* one. A candidate whose `create_session` fails the same way every time would re-announce and re-retract in the `Eng: X` group on every tick — nine announcement/retraction pairs a day for one broken issue, which is worse for the group's signal quality than never having announced. The predecessor `sdlc_progress.py` carries cooldowns and attempt counting for exactly this.

**The backoff must be keyed on the reflection's own observation, because the failure class leaves no record behind.** The failures that produce the loop — missing checkout, worktree collision, bad `project_key` — all raise *inside* `create_session` **before** any row is written: `_resolve_project_working_directory`, `_validate_slug` and `get_or_create_worktree` all precede the `_create()` enqueue at `tools/valor_session.py:639`, and the wrapping `except Exception` returns `CreateResult(success=False)`. No `AgentSession` exists, so a query for a terminal-FAILED `AgentSession(slug=sdlc-N)` finds nothing and the loop stays open. An earlier draft of this plan used exactly that query as gate 1.5 and would have shipped an inert backoff.

So the reflection records its own failure:

- On `CreateResult(success=False)` (and on an exception out of `create_session`), immediately before enqueueing the retraction: `SETEX upvote:pickup:failed:{repo}:{N} UPVOTE_FAILURE_BACKOFF_S "<truncated error>"`, via the same raw client `sdlc_progress` uses for its cooldown keys (`_get_redis()` → `POPOTO_REDIS_DB`).

**Why a raw `SETEX`/`GET` here does not violate the repo's raw-Redis rule.** The rule (CLAUDE.md, "Manual Testing Hygiene"; enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`) is scoped precisely: *never raw Redis on **Popoto-managed** keys*. It exists because Popoto maintains secondary index sets alongside each model hash, and a raw write or delete desynchronizes them. `upvote:pickup:failed:{repo}:{N}` is not such a key: no Popoto model declares that namespace, it holds a bare string rather than a model hash, it has no index sets, and it is created and reaped entirely by wall clock. The established precedent is `reflections/sdlc_progress.py:353-364` — `_action_cooldown_set` does `_get_redis().set(_COOLDOWN_KEY.format(...), "1", nx=True, ex=…)` on `"sdlc:stall:resume:cooldown:{slug}:{sha}"` (`:93`), a plain non-Popoto string key, through the same client, in the merged predecessor to this plan. The rule and this usage do not overlap, and the Task 7 validator should not read one as the other.
- Gate 1.5 is a plain `EXISTS`/`GET` on that key. `{repo}` is the `org/repo` slug so two projects' issue numbers cannot collide.
- Gate 1.6 keeps the terminal-FAILED-`AgentSession` check as a *second, different* signal: it covers the case where a lane genuinely started and then died, which gate 1.5 cannot see because `create_session` succeeded.

**This is a backoff, not a claim, and the "No claim key" No-Go holds.** A claim key is written on *success* to assert ownership and must be explicitly released for correctness; while it is held the issue reads as taken, and a crash between claim and start strands it. This key is written only on *failure*, never marks the issue as taken (any other actor — a human, `sdlc_progress` — is entirely free to start the lane while it is set), requires no release step, and disappears by wall clock. Losing it entirely costs one extra announce/retract pair, not a stranded issue: it is a politeness timer over a chat channel, not a correctness primitive. It is also written with `SETEX`, which does not trip the `! grep -qiE '(setnx|set_nx|nx\s*=\s*True)'` anti-criterion — deliberately, because that anti-criterion greps for the *claim* construct (an atomic set-if-absent), and this is not one. The Task 7 validator is directed here so it does not misread the gate as a No-Go violation.

Two bounded degradations:
- If the ack does not arrive inside the wait budget (`UPVOTE_ANCHOR_WAIT_S`, default **20s**, provisional), the reflection proceeds to create with `telegram_message_id=0`. **A missing ack means delivery is unconfirmed, not merely unthreaded** — the send was enqueued (exit 0 says only that), but the bridge may be down, or the payload may not have been a message at all (§I). The reflection still starts the lane: the work is approved and a two-hour wait for a confirmation that may never come is the worse trade. The finding therefore reads as an unconfirmed delivery with an unanchored start, not as a cosmetic threading miss, so an operator reading the report knows to check whether the announcement actually landed.
- If the relay reports `DELIVERED_NO_ID`, treat it as the same case.

The wait is `time.sleep`-polled in ~250ms increments with a hard iteration cap. Per spike-2 the scheduler cannot cancel a wedged sync reflection, so the budget is a hard local invariant, not a hope.

#### E. Post-merge label residue (resolves open question 4) — **the closed-issue filter suffices; gate 4 covers its one hole**

`_gh_issue_list` already passes `--state open`. The normal terminal path is: implementation PR merges with `Closes #N` → issue closes → it stops appearing as a candidate on the very next tick, with the `upvote` label harmlessly still attached. No label mutation, no terminal-state record, nothing to reap. An issue closed as wontfix while still labeled behaves identically.

The one hole is the merged-PR-but-issue-still-open case above, which gate 4 closes. So: **the terminal state is "issue closed", and it is entirely sufficient given gate 4.** Explicitly *not* doing: removing the `upvote` label on pickup or on merge (the issue forbids label mutation, and the label is also useful post-hoc as a record of what was auto-approved).

#### F. Schedule and registration — **via `scripts/update/reflection_register.py`, not by editing `config/reflections.yaml`**

`config/reflections.yaml` is **gitignored and untracked** (`.gitignore:8`; `git ls-files config/reflections.yaml` is empty). It is a per-machine install-time copy of the vault source of truth `~/Desktop/Valor/reflections.yaml`, refreshed by `scripts/update/env_sync.sync_reflections_yaml()` (Step 1.66) on every `/update`, and the scheduler reads the vault file first at runtime (`agent/reflection_scheduler._resolve_registry_path`: `REFLECTIONS_YAML` env → vault → `config/`). An entry hand-added to the in-repo copy cannot be committed, never reaches another machine, and is clobbered on the next `/update` — the feature would ship inert, which is precisely the Risk 1 failure. An earlier draft of this plan aimed every registration, test and verification hook at that file.

The repo already solved this. `scripts/update/reflection_register.py` (issue #1917, generalized in #2004) is the tracked, committed code path whose whole purpose is "a reflection whose callable ships in the repo gets appended to the **vault** registry at update time": idempotent, atomic (temp file + `os.replace`), re-parse-validated, guarded on the vault file existing and on this machine owning the `valor` project, and run from `scripts/update/run.py:833` **before** Step 1.66's vault→config copy so the entry propagates into every machine's `config/reflections.yaml` on the same cycle. `tests/unit/test_reflection_register.py` is its test harness and builds a temp vault via `REFLECTIONS_YAML`.

So registration is a code change, not a config edit:

- Add `UPVOTE_PICKUP_NAME = "sdlc-upvote-pickup"` / `UPVOTE_PICKUP_CALLABLE = "reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes"` and a `register_sdlc_upvote_pickup(project_dir)` wrapper to `scripts/update/reflection_register.py`, and add it to `main()`'s register tuple and to `scripts/update/run.py`'s registration block (alongside `register_crash_recovery` / `register_memory_distill_backfill`, with the same `RegisterResult` logging).
- **`register_reflection` must learn two things it cannot express today.** `_build_entry_block` hardcodes `every: {cadence}` and emits no `timeout:`, but this entry needs a cron schedule *and* an explicit timeout (§B). Extend it additively: optional `cron: str | None`, `cron_tz: str | None`, `timeout: int | None`; exactly one of `cadence` / `cron` must be supplied (`ValueError` otherwise); `timeout:` is emitted only when not `None`. **The keywords must thread through all three layers** — `register_reflection` → `_append_entry` (`:225-232`, which hard-declares `cadence: str` as required and re-passes it at `:256-261`) → `_build_entry_block`. Skipping the middle layer raises `TypeError` at the two `entry_kwargs` splat sites (`:455`, `:482`), and passing `cadence=""` to dodge it emits an empty `every:` line that `load_registry` silently skips — the Risk 1 failure by another route. Task 4b carries the exact signatures. The two existing callers pass `cadence=` and their emitted block is byte-for-byte unchanged — asserted by the existing tests in `tests/unit/test_reflection_register.py`, which must keep passing untouched.

The block the step appends:

```yaml
  - name: sdlc-upvote-pickup
    description: "Start half of autonomous SDLC: pick the oldest open `upvote`-labeled issue per project, announce it in that project's `Eng:` Telegram group, and create an Eng session anchored to the announcement. Derives start/skip from artifacts only — no claim key, no label mutation. Set SDLC_UPVOTE_PICKUP_ENABLED=false to disable without editing config."
    cron: 0 6-22/2 * * *
    cron_tz: America/Los_Angeles
    priority: low
    execution_type: function
    callable: "reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes"
    timeout: 1500
    enabled: true
```

`cron:` + `cron_tz:` are the file's idiomatic form; `load_registry` composes them into exactly the schedule string the cycle-1 fix verified — `"cron: 0 6-22/2 * * *; tz=America/Los_Angeles"` (`agent/reflection_scheduler.py:268-272`) — so the grammar decision is unchanged, only the surface that expresses it. `timeout: 1500` is `UPVOTE_ENTRY_TIMEOUT_S`, imported from `reflections/sdlc_upvote_lanes.py` by the register step rather than repeated as a literal (§B).

This is still the **first** cron-scheduled entry in the registry (every existing one uses `every:`). The grammar is unit-tested (`tests/unit/test_reflection_schedule_grammar.py`) but the end-to-end registry-file→`ReflectionEntry.validate`→`compute_next_due` path has never carried a cron value, so the registration test must drive it through a real temp registry file, not a constructed entry — this remains the plan's most likely source of a "works in the unit test, inert in production" defect. Note the failure mode is silent by design: `load_registry` logs a warning and **skips** an invalid entry (`:299-306`), so a malformed registration simply vanishes from the loaded list.

No `project_key:` on the entry: the reflection is multi-project by design and gates ownership per project at runtime via `machine_owns_project`. (`project_key:` is an *install-time* filter that would disable the whole reflection on every machine but one.) Registration itself is guarded on this machine owning `valor`; non-owning machines receive the entry through the vault's iCloud sync plus Step 1.66 — the same propagation path `crash-recovery` uses.

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

**`valor-session` is not a shipped command, and the manual repro must name the invocation that actually resolves.** `pyproject.toml` `[project.scripts]` ships `valor-telegram` (`:78`) and `valor-session-archive` (`:98`); there is no `valor-session` entry, nothing by that name in `.venv/bin`, and `which valor-session` resolves to nothing. The name survives only in `tools/valor_session.py`'s own docstring examples and in two CLAUDE.md bullets — a pre-existing repo gap, not one this plan created. The operator invocation named in §G and in the human-visible Success Criterion is therefore the module form, which does resolve (`tools/valor_session.py:2178` carries the `__main__` guard):

```bash
.venv/bin/python -m tools.valor_session create --telegram-message-id <id> --chat-id <eng chat id> --role eng --message "…"
```

Adding `valor-session = "tools.valor_session:main"` to `[project.scripts]` is the right fix for the underlying gap and is deliberately **not** done here: it would make CLAUDE.md's two bullets true and is a one-line change, but it is a repo-wide entrypoint decision with its own stale-shim exposure (Prior Art #2566) and belongs in its own issue. Recorded as a follow-up in `## No-Gos`.

Note that `chat_id` and `telegram_message_id` are meaningful only together — a message id is scoped to its chat, and `chat_id="0"` routes output to the system Room sink (`agent/output_handler.py:466,678`) rather than Telegram. The reflection always passes both; `create_session` logs a warning if `telegram_message_id` is non-zero while `chat_id` is `"0"`, since that combination is always a caller bug.

#### H. Announcement and session messages

Announcement (to the `Eng: X` group):
> Picking up issue #N for SDLC: {title}
> {url}

Session message (must contain `issue #N` — `_derive_sdlc_metadata` at `tools/valor_session.py:616` parses it to set `issue_url`/`classification_type`, and slugless `eng` creation depends on it):
> Run the next SDLC stage for issue #N: {title}. This issue is labeled `upvote` (pre-approved for autonomous pickup). Invoke `/sdlc` and let the router choose the stage.

Retraction (threaded reply, create-failure only):
> Could not start the SDLC lane for issue #N: {error}. Retrying on the next tick.

#### I. Sending: reuse the `valor-telegram send` implementation (as a module), do not become a third outbox producer

An earlier draft had the reflection hand-build the `telegram:outbox:*` payload. That would make it the third independent producer of that schema (after `tools/send_message.py` and `cmd_send`), and `cmd_send` is not a thin wrapper — en route to the same `RPUSH` it applies `_linkify_text`, the 4096-char truncation guard, and `bridge.promise_gate.cli_check_or_exit`. "Picking up issue #N" is about as literal a promise as outbound text gets; routing it around the promise gate silently would be a real (and unremarked) loss.

**Decision: the reflection shells out to the existing `valor-telegram send` implementation, invoked as a module** — `subprocess.run([sys.executable, "-m", "tools.valor_telegram", "send", …], timeout=UPVOTE_GH_TIMEOUT_S)`, once per announcement and once per retraction.

**Never the bare `valor-telegram` console script.** A bare PATH lookup is the exact failure class of open issue #2566 (Prior Art): on this machine `which valor-telegram` resolves to the stale `~/Library/Python/3.12/bin/valor-telegram` shim, whose shebang is the system framework interpreter, and it dies on import. Production would survive only because `com.valor.reflection-worker.plist` pins `/Users/valorengels/src/ai/.venv/bin` early in `PATH` — an undocumented dependency of this feature on a plist env var, and one that holds for exactly zero of the tests, operator repros, and non-launchd invocations. `sys.executable` inside the reflection worker *is* `.venv/bin/python`, so the module form is interpreter-pinned by construction and the plist dependency disappears. `tools/valor_telegram.py:1478` carries the `__main__` guard and `python -m tools.valor_telegram --help` was verified working at revision time. The same rule applies to any other console script this reflection would otherwise shell out to.

`cmd_send` already accepts a bare numeric chat id (`if args.chat.lstrip("-").isdigit(): chat_id = args.chat`, `tools/valor_telegram.py:836`), so `resolve_eng_group`'s numeric `chat_id` goes straight through. Two additive flags are required:

- `--session-id` — overrides the hardcoded `session_id = f"cli-{int(time.time())}"` (`:889`) so the outbox key and the ack key both carry the reflection's producer id. Default unchanged when the flag is absent. **Its help text must state the attribution consequence**, because the flag is public surface and is not validated: passing a *real* bridge `session_id` makes the relay attribute the send to that live session — `_record_sent_message` appends to its `pm_sent_message_ids` and `_append_outbound_chat_log` writes into its `chat_message_log`. That is a legitimate use, but it is not what a caller reaching for "give my message a stable id" expects, so the help text says: "Overrides the synthetic `cli-<epoch>` outbox id. Passing a live AgentSession id attributes the send to that session's message log; use a unique producer id for standalone sends."
- `--ack-sent-id` — sets `"ack_sent_id": True` in the payload, which is what arms the relay-side ack write (Key Elements).

`--reply-to` already exists (`:1362`) and carries the retraction's threading.

**Exit 0 from `cmd_send` does not by itself mean "the announcement was published", and two inherited env vars can silently redirect it.** Three concrete paths, all on this exact invocation:

- **Read-the-Room suppression.** `_should_run_rtr` (`tools/valor_telegram.py:729-755`) returns True whenever `VALOR_SESSION_ID` is set and non-empty — true for every invocation made from inside an Eng session, which is how this plan's own manual operator repro runs, and true for any nested shell that inherited it. On a `suppress` verdict `cmd_send` RPUSHes a **reaction** payload instead of the message (`:1113-1118`) and still returns **0** (`:1199-1200`). The reflection would read exit 0, wait the full `UPVOTE_ANCHOR_WAIT_S` for an ack that a reaction payload can never produce, record "unanchored start", and create a lane having announced nothing.
- **Implicit reply anchor.** With `--reply-to` absent, `cmd_send` defaults `reply_to` from the inherited `TELEGRAM_REPLY_TO` env var (`:1056-1062`), so an announcement launched from inside a session threads under an unrelated message in a different chat.
- **Misattributed ownership.** `AGENT_SESSION_ID` injects `owner_agent_session_id` into the payload (`:1103-1105`).

So the send helper carries two guards, and both are structural rather than advisory:

1. **`--no-read-the-room` is always on the argv.** The flag exists and short-circuits at `:751-752` before any LLM call, which additionally removes an unbounded-latency model call from inside the `UPVOTE_GH_TIMEOUT_S` subprocess budget. An autonomous announcement is not a conversational interjection and has no room to read.
2. **The subprocess env is built explicitly, never inherited wholesale.** `env = {k: v for k, v in os.environ.items() if k not in ("VALOR_SESSION_ID", "TELEGRAM_REPLY_TO", "AGENT_SESSION_ID")}`, passed as `subprocess.run(..., env=env)`. The retraction's explicit `--reply-to <anchor>` still wins, because the explicit flag is applied before the env fallback at `:1056`.

Even with both guards, **exit 0 means "enqueued", not "delivered"** — bridge-down prints "delivery requires the bridge relay to be running" and still exits 0. The ack is the only delivery confirmation, which is why the ack-timeout degradation (§D) is worded as *delivery unconfirmed* rather than as a threading imperfection.

Consequences, all accepted deliberately:

- **The promise gate now runs on the announcement.** A BLOCK exits `cmd_send` non-zero, the reflection sees the non-zero exit, emits a finding, and starts nothing — no announcement, no session, no phantom promise. Fail-closed and correct.
- **Subprocess, not an in-process import.** `cli_check_or_exit` calls `sys.exit` on BLOCK; a `SystemExit` raised inside a `_reflection_pool` worker thread is exactly the kind of half-unwound state spike-2 warns about. A subprocess turns it into an exit code. It also keeps Telethon and the whole Telegram CLI import graph out of the reflection process. Invoking it as `-m` rather than as a console script changes none of that — same process boundary, same exit code, one fewer stale-shim dependency.
- `_record_sent_message` will log one benign "session not found" warning for the producer id, identically to today's `cli-{epoch}` ids — verified at `bridge/telegram_relay.py:655-677`, which is non-fatal by construction.

#### J. `_lock_says_live` is shared, not ported

Gate 3's liveness rule is not copied. `_lock_says_live`'s own docstring states the invariant it exists to protect — classification is delegated to `_lock_owner_is_live` "so this module never forks the liveness rule" — and `_lane_is_live` adds a second warning against a secondary signal. A copy into a sibling reflection *is* that fork, and silently duplicates the fail-closed `except → None` semantics both gates depend on.

**Decision: move `_lock_says_live` and `_LOCK_KEY` into `reflections/utilities.py`** (which grows its own `_get_redis` helper) and import them from there in both reflections. `reflections/sdlc_progress.py` re-exports **both** names — `from reflections.utilities import _LOCK_KEY, _lock_says_live` — so its call sites at `:711`/`:737` are untouched and `sdlc_progress._LOCK_KEY` still resolves. `sdlc_progress` keeps its own `_get_redis` for its cooldown/escalation/attempts keys; only the lock read and its key template move.

**The move costs `tests/unit/reflections/test_sdlc_progress_check.py` exactly one fixture line, and no test body or assertion changes.** An earlier draft of this section claimed the suite passes *unmodified* and instructed the builder that any needed edit meant reworking the move. That claim was mechanically false and the instruction would have steered the builder into a stall or into keeping the local copy this section exists to eliminate. The suite's `fake_redis` fixture (`:230-235`) patches `sdlc_progress._get_redis`, and twelve tests (`:311`-`:379`) drive `_lock_says_live` / `_lane_is_live` through it; post-move the function body resolves `reflections.utilities._get_redis`, which that patch does not reach. So the fixture patches **both** modules:

```python
@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(sdlc_progress, "_get_redis", lambda: r)
    monkeypatch.setattr(reflections.utilities, "_get_redis", lambda: r)
    return r
```

The existing `sdlc_progress` patch stays: `_action_cooldown_set` / `_action_cooldown_release` and the attempts counter remain in that module and still resolve `_get_redis` there. `:191-192`'s `sdlc_progress._LOCK_KEY.format(...)` keeps working via the re-export, so it needs no edit. The one-line fixture change is the *whole* diff to that suite, and that bounded diff — not "zero diff" — is the no-regression proof.

The zero-diff alternative (`def _lock_says_live(issue_number, *, redis_client=None)`, threading a client through every call site) was considered and rejected: it reintroduces the per-module client seam the move exists to remove, and it is a worse shape than one line in a fixture.

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
- [ ] Create-failure path: assert `valor-telegram send` is invoked with `--reply-to <announcement id>` for the retraction, that `upvote:pickup:failed:{repo}:{N}` is set, and that the failure appears as a finding.
- [ ] Announcement send failure (non-zero exit from `valor-telegram send`, including a promise-gate BLOCK): assert no session is created and a finding names the failure.
- [ ] Anchor-timeout path: assert the session is still created (with `telegram_message_id=0`) **and** a finding records it as an *unconfirmed delivery* with an unanchored start (§D) — the degradation must be visible, not silent, and must not read as a cosmetic threading miss.
- [ ] Send-guard path: assert every send argv (announcement and retraction) carries `--no-read-the-room`, and that the subprocess `env` passed to `subprocess.run` contains none of `VALOR_SESSION_ID`, `TELEGRAM_REPLY_TO`, `AGENT_SESSION_ID` (§I). Without these, an inherited session env silently converts the announcement into a reaction or threads it into the wrong chat while still exiting 0.
- [ ] Ceiling-reached path: assert the finding names the count and the number of waiting issues.

## Test Impact

- [ ] `tests/unit/test_valor_session_create_core.py` — UPDATE: add coverage for the new `telegram_message_id` parameter reaching `_push_agent_session`; existing assertions on the default path must still pass unchanged (proving the additive-default claim).
- [ ] `tests/unit/test_valor_session_cli.py` — UPDATE: cover the new `--telegram-message-id` CLI argument and its default.
- [ ] `tests/unit/test_valor_session_sdlc_metadata.py` — UPDATE: no behavior change expected; re-run to confirm `_derive_sdlc_metadata` is untouched by the signature change.
- [ ] `tests/unit/test_telegram_relay_chat_log.py` — UPDATE: `process_outbox`'s success path gains an `ack_sent_id`-gated `publish_sent_message_id` call. Because the write is opt-in, the existing assertions should hold **unmodified** (payloads there carry no flag); the update is purely additive coverage for the flagged and unflagged branches.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — UPDATE: exactly one line, in the `fake_redis` fixture (`:230-235`), patching `reflections.utilities._get_redis` alongside the existing `sdlc_progress._get_redis` patch, because `_lock_says_live`'s body now resolves the client in the new module (§J). No test body or assertion changes; `sdlc_progress._LOCK_KEY` at `:191-192` keeps working via the re-export. A diff larger than that one line means the move drifted.
- [ ] `tests/unit/test_valor_telegram*.py` (whichever cover `cmd_send`'s payload build) — UPDATE: cover `--session-id` overriding the `cli-{epoch}` default and `--ack-sent-id` setting the payload flag; assert the no-flag default payload is byte-for-byte unchanged.
- [ ] `tests/unit/test_reflection_register.py` — UPDATE: add coverage for `register_sdlc_upvote_pickup` and for `register_reflection`'s new `cron` / `cron_tz` / `timeout` arguments, including the load-survives-validate assertion (Risk 1) and the idempotence no-op. The existing `crash-recovery` / `memory-distill-backfill` assertions must pass **unmodified** — that is the proof the extension is additive.
- [ ] `tests/unit/test_reflections_yaml_migration.py`, `tests/unit/test_update_reflections_yaml.py` — UPDATE only if they assert over a fixed entry set; the new entry is appended at update time to the vault registry, not committed to `config/reflections.yaml`, so a suite that reads the in-repo file on a checkout is unaffected. Verify which of the two is which before touching either.
- [ ] `tests/unit/test_reflection_machine_filter.py` — UPDATE: confirm an entry with no `project_key` is left enabled on every machine (the behavior this plan relies on).
- [ ] `tests/unit/reflections/test_pm_briefings_collector.py` — UPDATE: `_gh_issue_list` gains `createdAt` in its `--json` field list and an `extra_args` parameter; assert **both** built command shapes (default, and with `--search sort:created-asc`) and that `_collect_open_bugs` / `_collect_upvote_queue` are unaffected.
- [ ] `tests/unit/reflections/test_sdlc_upvote_lanes.py` — CREATE: the new module's coverage (gates, ordering, ceiling, anchor, failure paths).

## Rabbit Holes

- **Building a general "send and get the message id" RPC.** The ack is one `RPUSH` + `EXPIRE` next to an id the relay already has, plus one reader. Resist turning it into a request/response correlation framework, a pubsub channel, or a retrofit of `session_executor.py:1688`'s bespoke drain-poll onto the new primitive. That retrofit is worth doing — as a separate change, after this one proves the primitive.
- **Making the concurrency ceiling exact.** Enumerating every non-terminal `AgentSession` per project to get a true live-lane count is a Redis scan on a periodic path, for a guard whose job is "do not ramp to eight a day." Approximate is the requirement.
- **Reimplementing stage derivation.** Spike-3 and spike-4 both point one way: read the ledger, do not compute the stage, let `/sdlc` route. Any task bullet that starts computing PLAN-vs-BUILD is out of scope.
- **Message editing / retraction-by-delete.** `edit_message` does not exist anywhere in the repo. Adding Telethon edit support to the relay to make the failure path prettier is a bridge feature, not this issue.
- **A priority or ordering vocabulary.** Labels like `upvote-high`, numeric prefixes in titles, body-parsed priorities. FIFO is the decision; anything else is a new approval language.
- **Auto-closing issues whose PR merged without `Closes #N`.** Gate 4 detects and reports it. Having a reflection close GitHub issues is a materially larger trust grant than starting a session.

## Risks

### Risk 1: The registration never reaches a running scheduler and the reflection is silently inert
**Impact:** The feature ships, tests pass, and nothing ever fires in production. The most likely way this plan fails invisibly. Two independent causes: (a) the entry is written to the gitignored `config/reflections.yaml` and is clobbered on the next `/update` (§F — the reason registration goes through `scripts/update/reflection_register.py`), and (b) the first cron-scheduled entry in the registry fails `validate()`, which `load_registry` handles by logging a warning and **skipping** it (`agent/reflection_scheduler.py:299-306`) — no crash, no signal.
**Mitigation:** A registration test in `tests/unit/test_reflection_register.py` that (1) points `REFLECTIONS_YAML` at a temp registry exactly as the existing tests do, (2) runs `register_sdlc_upvote_pickup`, (3) loads the result with `agent.reflection_scheduler.load_registry(path=<tmp>)`, (4) asserts the entry **survives the load** (its absence is the whole Risk-1 failure, and `[0]`-indexing an empty match list is the assertion that catches it), (5) asserts `entry.validate() == []` — `validate()` returns `list[str]` and never raises, so a bare call asserts nothing — and (6) asserts `compute_next_due(entry.schedule, None)` lands in the expected 06:00–22:00 America/Los_Angeles window. Real file, real loader, real cron string. Plus a `## Verification` row that runs exactly this test, and a post-`/update` operator check in `## Update System`.

### Risk 2: A duplicate lane on a live issue
**Impact:** Two Eng sessions on one issue: two worktrees on the same branch name, contending pushes, doubled review load. The worst outcome in this change.
**Mitigation:** Four independent skip gates (§C), all fail-closed on uncertainty, with gates 1 and 3 re-read immediately before `create_session` (mirroring `sdlc_progress.py:707-719`). A test drives two consecutive ticks against one issue and asserts exactly one creation.

### Risk 3: Two machines both pick up the same issue
**Impact:** Same as Risk 2, across machines, where no shared lock is consulted before the session record exists.
**Mitigation:** `machine_owns_project` is gate zero, before any `gh` call. `projects.<key>.machine` is the single source of truth (CLAUDE.md, single-machine ownership). A test asserts a non-owned project returns `status: "skipped"` and issues no subprocess calls at all.

### Risk 4: The reflection wedges on the anchor wait
**Impact:** Per spike-2, `asyncio.wait_for` cannot cancel a sync reflection thread. A wedged tick occupies a `_reflection_pool` slot indefinitely and can starve other reflections.
**Mitigation:** Four layers, in increasing authority.

1. **Per-call timeouts.** The anchor wait is a bounded loop with both a deadline and a hard iteration cap, and every `gh` / `python -m tools.valor_telegram` subprocess call carries an explicit `UPVOTE_GH_TIMEOUT_S`.
2. **Bounded inspection.** `UPVOTE_CANDIDATE_SCAN_MAX` (§B) caps the candidate count — without it the per-project cost scales with the open-`upvote` backlog and the per-call timeouts bound nothing in aggregate.
3. **Bounded pickup, gated before it starts.** `create_session` is uninterruptible and worth up to `UPVOTE_CREATE_WORST_CASE_S` = `settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s` = 660s, because every pickup provisions a cold worktree venv (`agent/worktree_manager.py:1341`). No deadline check placed at a loop top can fire inside it. So the pickup is admitted only when the remaining budget covers `UPVOTE_PICKUP_WORST_CASE_S` (740s) in full; otherwise the project defers with a finding and never announces (§B).
4. **The wall-clock run deadline.** `UPVOTE_RUN_BUDGET_S`, captured in `run_sdlc_upvote_lanes()` and enforced by early return from the per-project callable (`run_per_project_audit` owns the loop and neither `break` nor `raise` is available to the callable — §B), degrading to a `"budget exhausted; project not scanned"` finding per remaining project rather than an overrun. Held strictly below the entry's declared `timeout:` so the scheduler never gives up on a thread that is still running — spike-2 established it cannot cancel one, and `reap_stale_running` would otherwise fire against it.

**Tests.** Both invariants (`UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S < UPVOTE_ENTRY_TIMEOUT_S`, and `UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S`) are asserted directly. The behavioral budget test must exercise the **create** path, not just the ack path: stub `create_session` with a `time.sleep` longer than the remaining budget and assert the whole `run_sdlc_upvote_lanes()` call still returns inside `UPVOTE_ENTRY_TIMEOUT_S`. A never-arriving-ack fixture never enters the create path at all, so it passes against the defective design — the cannot-fail-test pattern this plan flags twice elsewhere. A separate test drives an already-near-expiry deadline and asserts the admission check defers the pickup with a finding and issues **zero** send subprocess calls.

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
**Location:** `bridge/telegram_relay.py::process_outbox` → `bridge.outbox_ack.publish_sent_message_id` (writer) vs. the reflection's `bridge.outbox_ack.await_sent_message_id` (reader), different processes.
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

**Three merge-time follow-ups, recorded:**

0. **Amend three of issue #2717's acceptance criteria, and record one further plan-internal deviation.** Each substitution below is argued in this plan and each is implemented as argued; the issue's checklist still names the pre-substitution text for the first three, so a future reader would misread all three as unmet. Post a comment on #2717 at merge naming the three AC substitutions (the fourth item is plan-vs-plan, not plan-vs-issue, so it needs no issue comment — it is recorded here purely so a future reader of §B is not sent looking for a lock term that was consciously dropped). Not a follow-up issue, just a comment. (The first three bullets were independently recorded on `main` at `dbf761ed4`, which landed while this branch's own plan-doc copy had already diverged from `main`; reconciled here so the branch's plan doc is not permanently missing them.)
   - **AC 2** reads "A new reflection is registered in `config/reflections.yaml`". §F deliberately does the opposite — the vault registry via `scripts/update/reflection_register.py` — because that file is gitignored and clobbered on every `/update`.
   - **AC 5** names "plan-doc existence" and "reusing `agent/pipeline_state.py`". Spike-3 rejects both: `derive_from_durable_signals` keys off `docs/plans/{session.slug}.md` = `docs/plans/sdlc-2717.md`, which never exists (this repo's plan docs are named from the issue title, not `sdlc-{N}`), and `agent/pipeline_state.py:1463`'s `_durable_run` runs with no `cwd`, so it always inspects whatever repo the calling process happens to sit in — wrong for a multi-project reflection. §C uses `PipelineLedger.get(f"{org}/{repo}", N)` instead.
   - **AC 11** names "the derived-state decision table (each row)". Spike-4 collapses the reflection's decision to a binary start/skip — no PLAN-vs-BUILD branch, since `/sdlc` already routes stages and a reflection-side stage choice would duplicate that logic (development principle 9). §C's six skip gates are the tested unit instead of a 4-row stage table.
   - **§B's live-count formula** was drafted as "(open PRs whose `headRefName` matches `session/sdlc-*`) + (candidate `upvote` issues whose `_lock_says_live` is `True`)" but shipped PR-only — `_count_live_lanes` (`reflections/sdlc_upvote_lanes.py`) counts open PRs alone. Lock-inclusive counting would double-count a candidate that already has a PR (the lock is held for the PR's whole lifetime) without a de-dupe step the code never had, and it costs an extra `_lock_says_live` read per candidate on every tick for a ramp brake whose own §B text calls "approximately right" sufficient. The feature doc (`docs/features/upvote-autonomous-sdlc-pickup.md`) already states PR-only; this plan's §B is now updated to match.

**Three deferrals, all to be filed as follow-up issues at merge time:**

1. **Retrofitting `session_executor.py:1688`'s bespoke drain-poll onto the new ack primitive.** Worth doing, deliberately not here — the primitive should prove itself on a single low-traffic consumer before a live session-execution path depends on it. (See `## Rabbit Holes`.)
2. **Adding `valor-session = "tools.valor_session:main"` to `pyproject.toml [project.scripts]`.** The command does not exist (§G) even though CLAUDE.md's two `valor-session` bullets and the module's own docstring examples assume it does. That is a pre-existing repo gap this plan works around by naming `.venv/bin/python -m tools.valor_session` everywhere the manual path is referenced. Shipping a new entrypoint is a repo-wide decision with its own stale-shim exposure (#2566) and does not belong inside this feature's PR.
3. **Post-merge scratch-issue dry run (Task 7), on the `valor`-owning machine, after both restarts.** The human-visible Success Criterion (a real pickup threads correctly in `Eng: X`) is not verifiable pre-merge: `bridge/outbox_ack.py` and the `telegram_relay.py` ack-write hunk are both absent from `origin/main`, and the ack write only executes inside the already-running production `bridge/telegram_bridge.py` process, which runs `main`'s code until this PR merges and both the bridge and the reflection worker restart onto it. Deferred with a machine-ownership check already performed and a concrete unblock path recorded in `docs/features/upvote-autonomous-sdlc-pickup.md`'s `## Scratch-issue dry run` section (its 7-step post-merge procedure is the body for the filed follow-up issue). This is the shortest-fused of the three deferrals: skipping the dry run leaves the anchoring path unexercised end-to-end, and its failure mode degrades silently — `telegram_message_id` stays `0` with no visible error — so nobody notices without this deliberate check.

Beyond those, nothing is deferred. Every acceptance criterion on issue #2717 except the three amended above (label documentation, the reflection, the scope gate, the one-per-tick cap, artifact-derived decisions, PR skip, the announcement, `create_session` plumbing, reply threading, idempotency, tests, and the feature doc) ships in this PR as written; ACs 2, 5, and 11 ship as the argued substitutions above.

Explicitly *not built*, as design decisions rather than deferrals (each has a `## Verification` anti-criterion below):

- **No claim key.** No Redis key, file, or record is written to mark an issue as claimed. Every decision derives from artifacts that exist for their own reasons.
- **No label mutation.** The reflection never calls `gh issue edit --add-label` / `--remove-label`. `upvote` is a human-owned signal in both directions.
- **No reflection-side stage selection.** No PLAN-vs-BUILD branch; `/sdlc` routes (spike-4).
- **No issue closing.** Gate 4 reports the merged-PR-with-open-issue case as a finding; a human closes it.
- **No message editing or deletion.** The failure path replies; it does not rewrite history.

## Update System

- **The update system is the registration mechanism, not an afterthought.** `config/reflections.yaml` is gitignored and clobbered from the vault on every `/update`, so the entry ships as tracked code: `scripts/update/reflection_register.py` gains `register_sdlc_upvote_pickup`, wired into its `main()` and into `scripts/update/run.py`'s registration block at `:833` — which already runs *before* Step 1.66's `env_sync.sync_reflections_yaml()` vault→config copy, so the appended entry propagates into the per-machine `config/reflections.yaml` on the same cycle. See §F.
- **Propagation across machines** is the same path `crash-recovery` uses: the register step's machine-ownership guard means only the `valor`-owning machine writes the shared iCloud vault file; every other machine picks the entry up via iCloud sync plus its own Step 1.66 copy. The entry carries **no** `project_key`, so `tools/reflection_machine_filter.filter_reflections_for_machine` leaves it enabled everywhere and ownership is gated per project at runtime.
- **No manual vault edit is required or wanted.** Hand-adding the entry to `~/Desktop/Valor/reflections.yaml` would work once and would not be reproducible on a fresh machine; the register step is idempotent and is the shipped mechanism. (It is a no-op when the entry is already present.)
- **Post-`/update` operator check** (run on any machine after the first `/update` that carries this change):
  ```bash
  python -c "from agent.reflection_scheduler import load_registry; e=[x for x in load_registry() if x.name=='sdlc-upvote-pickup']; assert e, 'entry missing or skipped as invalid'; assert not e[0].validate(), e[0].validate(); print(e[0].schedule, e[0].effective_timeout())"
  ```
  Expected: `cron: 0 6-22/2 * * *; tz=America/Los_Angeles 1500`. An empty list means the entry was either never registered or was silently skipped as invalid — the Risk 1 failure.
- No new dependencies, secrets, or config files.
- No migration: no Popoto model changes (the ack key is a plain ephemeral Redis list; `telegram_message_id` is an existing property over an existing `DictField`). The Popoto migration requirement in `docs/sdlc/do-plan.md` does not apply, and that must remain true — if the build finds itself adding a model field, the design has drifted.
- **Restart required**: the change touches `bridge/telegram_relay.py` + the new `bridge/outbox_ack.py` (bridge) and the reflection worker. After merge, `/update` → `./scripts/valor-service.sh restart` plus a reflection-worker restart. Note the ack writer lives in the bridge and the reader in the reflection process — if only one restarts, anchoring silently degrades to `telegram_message_id=0`. Call this out in the feature doc.

## Agent Integration

- **No new MCP tool or `[project.scripts]` entry.** The reflection is invoked by the scheduler through its `callable:` dotted path, not by the agent.
- **`valor-session` is not a console script** — `pyproject.toml [project.scripts]` has no such entry, despite CLAUDE.md and `tools/valor_session.py`'s own docstring examples assuming one (§G; a pre-existing gap, deferred as a follow-up in `## No-Gos`). The new `--telegram-message-id` argument is nonetheless reachable from the agent's Bash tool with no `pyproject.toml` change, via `.venv/bin/python -m tools.valor_session create …` (`tools/valor_session.py:2178` has the `__main__` guard). Every reference in this plan and in the feature doc uses that form.
- `valor-telegram` **is** a shipped console script (`pyproject.toml:78`), so its new `--session-id` / `--ack-sent-id` flags (§I) are agent-reachable today. The reflection still invokes it as `python -m tools.valor_telegram` rather than by name, because the on-PATH shim is stale on this machine (#2566); an operator reproducing an announcement by hand should use the same module form for the same reason.
- **The bridge does need a code change** — `process_outbox` calls `bridge.outbox_ack.publish_sent_message_id` on flagged payloads. This is bridge-internal; nothing new is imported into `bridge/telegram_bridge.py`, and `bridge/outbox_ack.py` is a leaf module with no Telethon import so the reflection process can read it without pulling in a Telegram client.
- Integration coverage: a test drives the real `create_session` → `_push_agent_session` path and asserts the persisted `AgentSession.telegram_message_id` matches what was passed, so the agent-visible contract (`TELEGRAM_REPLY_TO` export at `agent/sdk_client.py:502`) is proven end to end rather than at the boundary.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/upvote-autonomous-sdlc-pickup.md` — the `upvote` contract, the schedule, the scope gate, the four skip gates and why each fails closed, the ordering and ceiling decisions with their rationale, the announce-then-create trade-off and its failure path, and the restart-both-processes note.
- [ ] Add a row to `docs/features/README.md`.
- [ ] Update `docs/features/eng-session-architecture.md` — Eng sessions now have a third origin (human message, `sdlc_progress` recovery, and now autonomous pickup).
- [ ] Cross-link from `docs/features/bridge-worker-architecture.md` where the outbox is described, documenting `telegram:sent:{session_id}` as a producer-readable ack (`bridge/outbox_ack.py`) armed by the `ack_sent_id` payload flag / `valor-telegram send --ack-sent-id`.
- [ ] The feature doc states that registration lives in `scripts/update/reflection_register.py` and that `config/reflections.yaml` must never be hand-edited for it, with the post-`/update` operator check from `## Update System`.

### Repo Instructions
- [ ] Add an `upvote` row to the GitHub Issue Labels table in `CLAUDE.md`: "Pre-approved for autonomous SDLC pickup — a scheduled reflection may start a lane on this issue without further human input." (The GitHub label description itself already says this — verified at plan time — so this task is documentation only.)

### Inline Documentation
- [ ] Module docstring on `reflections/sdlc_upvote_lanes.py` stating the start/recovery split against `sdlc_progress.py`, the no-claim-key/no-label-mutation invariants, and the worst-case per-tick time budget.
- [ ] Module docstring on `bridge/outbox_ack.py` naming the key, its TTL, the single-consumer/delete-on-read contract, and why it is a leaf module (the reflection process must not import Telethon).
- [ ] Grain-of-salt comments on `UPVOTE_LANE_MAX_LIVE`, `UPVOTE_LANE_MAX_LIVE_MACHINE`, `UPVOTE_ANCHOR_WAIT_S`, `UPVOTE_CANDIDATE_SCAN_MAX`, `UPVOTE_FAILURE_BACKOFF_S`, `UPVOTE_GH_TIMEOUT_S`, `UPVOTE_RUN_BUDGET_S`, and `UPVOTE_ENTRY_TIMEOUT_S` marking them provisional and env-overridable. `UPVOTE_CREATE_WORST_CASE_S` and `UPVOTE_PICKUP_WORST_CASE_S` instead carry a comment stating they are **derived** from `settings.timeouts` and must never be replaced by literals.
- [ ] The module docstring states the closed-form aggregate per-tick budget `len(load_local_projects()) × (UPVOTE_CANDIDATE_SCAN_MAX × UPVOTE_GH_TIMEOUT_S) + UPVOTE_PICKUP_WORST_CASE_S`, names the cold-worktree `uv sync` inside `create_session` as the dominant term and the admission check as what bounds it, states both budget invariants, explains that the deadline is enforced by early return because `run_per_project_audit` owns the loop, and states that gate 1.5 is a clock-expiring failure backoff on a non-Popoto key rather than a claim (so neither the "No claim key" invariant nor the raw-Redis rule is misread as violated).
- [ ] The feature doc records the machine-wide implication of the two ceilings (`UPVOTE_LANE_MAX_LIVE` × projects, capped by `UPVOTE_LANE_MAX_LIVE_MACHINE`) and the practical pickup rate the run budget permits (~one cold pickup per tick), so an operator tuning either number knows what they are trading.
- [ ] Docstring on `register_sdlc_upvote_pickup` explaining why registration is code and not a `config/reflections.yaml` edit, matching the module's existing rationale.

## Success Criteria

- [x] `CLAUDE.md`'s labels table documents `upvote` (the GitHub label description already exists).
- [x] `scripts/update/reflection_register.py` registers `sdlc-upvote-pickup` into the **vault** registry with `cron: 0 6-22/2 * * *` + `cron_tz: America/Los_Angeles` and an explicit `timeout:`, is wired into `scripts/update/run.py` before the vault→config copy, and a test proves the registered entry survives `load_registry`, validates clean, and computes a next-due time in the expected local window. No commit touches the gitignored `config/reflections.yaml`.
- [x] The whole run is bounded by `UPVOTE_RUN_BUDGET_S`, including the uninterruptible `create_session` call: a pickup is admitted only when the remaining budget covers `UPVOTE_PICKUP_WORST_CASE_S` (which derives from `settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s`, not from a literal). Both invariants — `UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S < UPVOTE_ENTRY_TIMEOUT_S` and `UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S` — are asserted by tests, and the behavioral budget test stubs a slow `create_session` rather than only a never-arriving ack.
- [x] Budget expiry is enforced by early return from the per-project callable (`run_per_project_audit` owns the loop), not by `break` or by raising.
- [x] Projects without an `Eng:` group are skipped; non-owned projects are skipped before any subprocess runs.
- [x] At most one issue is picked per project per tick, and the machine-wide accumulated live-lane count never exceeds `UPVOTE_LANE_MAX_LIVE_MACHINE`.
- [x] No claim key is written and no label is mutated — asserted by anti-criteria, not just by review.
- [x] An issue with an open PR is skipped; an issue with a *merged* PR is skipped and reported.
- [x] On pickup, the announcement is enqueued to the `Eng: X` group's `chat_id` and its message id is captured from the relay ack.
- [x] `create_session` accepts `telegram_message_id`, plumbs it in place of the `0` at `tools/valor_session.py:639`, and the created session's `telegram_message_id` equals the announcement id.
- [x] Subsequent output threads under the announcement (proven via the persisted `telegram_message_id` → `TELEGRAM_REPLY_TO` contract).
- [x] Two consecutive ticks start exactly one lane for the same issue.
- [x] A candidate whose `create_session` returned `success=False` **without writing any `AgentSession` row** is not re-announced within `UPVOTE_FAILURE_BACKOFF_S` (no announce/retract loop in the `Eng: X` group) — the backoff is keyed on the reflection's own observation, not on a session record that does not exist.
- [x] The announcement and retraction go out through `tools.valor_telegram`'s `send`, invoked as `[sys.executable, "-m", "tools.valor_telegram", …]` and never as the bare `valor-telegram` console script (#2566), so the promise gate, linkify and length guard apply and no stale PATH shim can intercept it; the reflection writes no `telegram:outbox:*` payload of its own.
- [x] Every send carries `--no-read-the-room` and a subprocess env scrubbed of `VALOR_SESSION_ID`, `TELEGRAM_REPLY_TO` and `AGENT_SESSION_ID` (§I), so an inherited session env cannot turn the announcement into a reaction or thread it into the wrong chat while still exiting 0. Exit 0 is treated as "enqueued", never "delivered"; a missing ack is reported as an unconfirmed delivery.
- [x] Gates 1 and 1.6 match on `slug` **and** `project_key`, so a live lane on another project's identically-numbered issue does not silently block a pickup; the cross-project collision is reported as a finding (§C).
- [x] `UPVOTE_CANDIDATE_SCAN_MAX` is the single candidate-truncation knob, passed as `_gh_issue_list`'s `limit`, with no second post-sort slice (§A).
- [ ] **Human-visible outcome:** after a pickup, a reader scrolling the `Eng: X` group sees one announcement with the lane's subsequent messages collapsed under it as replies, not a flat stream of orphaned updates. **Verified at merge time by Task 7's scratch-issue dry run** — a throwaway `upvote`-labeled issue, both processes restarted, `run_sdlc_upvote_lanes()` invoked directly, and the resulting thread read by a human — not by waiting for a real pickup, because the one currently-open `upvote` issue (#2716) is skipped by gate 2 on its `PipelineLedger` progress, not because the queue is empty (Freshness Check). The outcome and the observed message id are recorded in the feature doc. This is the criterion the mechanical `telegram_message_id` → `TELEGRAM_REPLY_TO` rows are a proxy for; the proxy passing while this fails means the feature did not work. (The `python -m tools.valor_session create --telegram-message-id …` invocation from §G remains the *operator debugging* path for the plumbing alone — it is not a substitute for this run, since it exercises neither the reflection, nor `resolve_eng_group`, nor the send path, nor the relay ack.)
- [x] Create-failure posts a threaded retraction; anchor-timeout still starts the lane and records a finding.
- [x] `docs/features/upvote-autonomous-sdlc-pickup.md` exists and is indexed in `docs/features/README.md`.
- [x] Tests pass (`/do-test`); lint and format clean.
- [x] Documentation updated (`/do-docs`).

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
- Create `bridge/outbox_ack.py` — a **leaf** module (Redis client import only; no Telethon, no relay import) owning the `telegram:sent:{session_id}` key constant, its TTL, `publish_sent_message_id(session_id, msg_id)` and `await_sent_message_id(session_id, timeout_s) -> int | None`. The reader is a bounded blocking poll that deletes the key on read and returns `None` on timeout; its docstring states the single-consumer, delete-on-read contract and the TTL. Siting it here rather than in `bridge/telegram_relay.py` is required: that module imports `telethon.errors`, and the reader runs in the reflection process, which has no Telegram client (spike-2, §I).
- In `process_outbox`, in the existing `if msg_id is not None:` success block, add an **opt-in** ack: `if msg_id is not None and message.get("ack_sent_id"):` → `publish_sent_message_id(...)`. Wrap so a failure is logged and non-fatal — the relay must never crash on ack bookkeeping. The flag gate is required, not optional: an unconditional write puts two Redis ops on every outbound message system-wide for one consumer, and it is what keeps `tests/unit/test_telegram_relay_chat_log.py`'s existing assertions valid unmodified.
- Add `--session-id` and `--ack-sent-id` to `valor-telegram send` (§I): `--session-id` overrides the hardcoded `session_id = f"cli-{int(time.time())}"` at `tools/valor_telegram.py:889`; `--ack-sent-id` sets `"ack_sent_id": True` in the payload. Both default to today's behavior when absent, so no existing invocation changes. `--session-id`'s help text must state the attribution consequence verbatim as written in §I — the flag is unvalidated public surface, and passing a live `AgentSession` id routes the send into that session's `pm_sent_message_ids` and `chat_message_log`.
- Do not refactor `session_executor.py:1688`'s existing drain-poll onto this primitive (rabbit hole).

### 3. Plumbing: `Eng:` group resolution and `createdAt`
- **Task ID**: build-eng-group-resolver
- **Depends On**: none
- **Validates**: `tests/unit/reflections/test_pm_briefings_collector.py`, new fixtures in `tests/unit/reflections/test_sdlc_upvote_lanes.py`
- **Informed By**: recon (no prefix-based resolver exists; `sdlc_progress` hardcodes `"Eng: Valor"`); live config (8/9 projects have one, `royop` does not)
- **Assigned To**: plumbing-builder
- **Agent Type**: builder
- **Parallel**: true
- Move `_lock_says_live` and `_LOCK_KEY` from `reflections/sdlc_progress.py` into `reflections/utilities.py` (adding a local `_get_redis` there), and re-export **both** names from `sdlc_progress` (`from reflections.utilities import _LOCK_KEY, _lock_says_live`) so its call sites at `:711`/`:737` and the test's `sdlc_progress._LOCK_KEY` reference are untouched (§J). Do not copy the function into the new reflection.
- Then add **one line** to `tests/unit/reflections/test_sdlc_progress_check.py`'s `fake_redis` fixture (`:230-235`): `monkeypatch.setattr(reflections.utilities, "_get_redis", lambda: r)` alongside the existing `sdlc_progress` patch (which stays — the cooldown and attempts helpers still resolve `_get_redis` in that module). Twelve tests at `:311`-`:379` drive the moved function through that fixture and would otherwise hit an unpatched client. **No test body and no assertion in that suite changes**; that bounded one-line diff is the no-regression proof (§J). If the diff grows beyond it, the move drifted — rework the move, not the assertions.
- Add `resolve_eng_group(project) -> tuple[str, int] | None` to `reflections/utilities.py`: scan `project["telegram"]["groups"]` for keys with the literal `Eng:` prefix, return `(group_name, int(chat_id))`. Return `None` on absence, malformed entry, missing `chat_id`, or non-integer `chat_id`. No substring fallback, no default chat.
- Extend `_gh_issue_list` (`reflections/pm_briefings/collector.py:95`) in three additive ways: request `createdAt` in `--json`; add `extra_args: list[str] | None = None` spliced into the `gh` argv; and add `timeout: int | None = None` forwarded to `_run` (`:24`), which today hardcodes `timeout: int = 30` so the reflection's `UPVOTE_GH_TIMEOUT_S` would not reach this one call (§B). Keep `limit: int = 20` as the default. `_collect_open_bugs` / `_collect_upvote_queue` must be byte-for-byte unaffected — assert the default call's argv in `tests/unit/reflections/test_pm_briefings_collector.py`, and assert the `extra_args` + `timeout` call shape separately.
- Do **not** attempt to fix ordering by raising `limit`. The reflection passes `extra_args=["--search", "sort:created-asc"]` so `gh` sorts server-side before truncating (§A); a bigger page only moves the starvation cliff.

### 4. The reflection module
- **Task ID**: build-upvote-reflection
- **Depends On**: build-create-session-anchor, build-relay-ack, build-eng-group-resolver
- **Validates**: `tests/unit/reflections/test_sdlc_upvote_lanes.py`
- **Informed By**: spike-3 (use `PipelineLedger.get(repo, N)`, not `derive_from_durable_signals`); spike-4 (no stage selection); spike-5 (session-slug gate replaces a debounce key); spike-2 (bounded waits — the scheduler cannot cancel this thread)
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/sdlc_upvote_lanes.py` with `run_sdlc_upvote_lanes() -> dict` that builds a **run-scoped state object** (holding `deadline = time.monotonic() + UPVOTE_RUN_BUDGET_S` and the machine-wide live-lane accumulator) and delegates to `run_per_project_audit(functools.partial(_pick_up_upvoted, state=state), name="sdlc-upvote-pickup")`. `run_per_project_audit` (`reflections/utilities.py:118`) owns the loop, so the per-project callable can neither `break` nor abort by raising (it wraps each project in its own `try/except`) — budget enforcement is an **early return** of `{"status": "skipped", "findings": ["budget exhausted; project not scanned"], …}`, never loop control (§B). Do not emit `{k}/{n}` phrasing; no iteration index is available.
- Honor `SDLC_UPVOTE_PICKUP_ENABLED=false` → `status: "disabled"`.
- Per-project gates in order: `machine_owns_project` → `resolve_eng_group` → `_project_repo`.
- Candidates via `_gh_issue_list(repo, ["upvote"], cwd, limit=UPVOTE_CANDIDATE_SCAN_MAX, extra_args=["--search", "sort:created-asc"])` — **server-side** oldest-first, truncated server-side to the scan cap — then a client-side `createdAt`/`number` ascending sort purely as a deterministic tie-break (§A).
- **`UPVOTE_CANDIDATE_SCAN_MAX` is the `limit`; do not also slice the result.** One knob bounds aggregate per-tick work (default 10, env-overridable, grain-of-salt comment). A second post-sort slice would cut from the same end, make the smaller of the two silently binding, and leave a maintainer raising one of them with no effect (§A/§B).
- Enforce `UPVOTE_LANE_MAX_LIVE` (default 3, env-overridable, grain-of-salt comment) from open `session/sdlc-*` PRs plus live locks on candidates (§B); emit a finding when at the ceiling.
- Enforce the machine-wide companion `UPVOTE_LANE_MAX_LIVE_MACHINE` (default 5, env-overridable, grain-of-salt comment) by accumulating each project's already-computed live count into the run-scoped state as the sweep proceeds — **no additional `gh` calls** — and refusing a start when the running total is at the ceiling (§B). Emit a distinct finding naming the machine-wide count so it is not confused with the per-project one.
- Import `_lock_says_live` from `reflections.utilities` (Task 3). Do **not** define a local copy (§J).
- Per-candidate skip gates 1, 1.5, 1.6, 2, 3, 4 in the documented order, all fail-closed (§C). **Gates 1 and 1.6 match on two fields** — `slug == f"sdlc-{N}"` **and** `project_key == project["slug"]` — because `AgentSession.slug` is a global `KeyField` and two repos routinely share an issue number (§C). A non-terminal row with a different `project_key` is a cross-project collision: emit a finding naming both projects and proceed, do not skip. **Gate 1.5 reads `upvote:pickup:failed:{repo}:{N}`**, the key this reflection itself writes with `SETEX` on `CreateResult(success=False)` — the failure class that produces the announce/retract loop raises *before* `_push_agent_session` and leaves no `AgentSession` behind, so a session-row query cannot detect it (§D). Gate 1.6 keeps the terminal-FAILED-`AgentSession` check as the separate started-then-died signal. Gate 4 uses `--state all` and emits a finding on merged-PR-with-open-issue.
- **Admission check before announcing** (§B): proceed only if `deadline - time.monotonic() >= UPVOTE_PICKUP_WORST_CASE_S`; otherwise return a `"insufficient run budget for a pickup; deferring issue #N to the next tick"` finding and send nothing. This is what bounds the uninterruptible `create_session` call, which no loop-top deadline check can reach.
- Announce via `subprocess.run([sys.executable, "-m", "tools.valor_telegram", "send", "--chat", str(eng_chat_id), "--session-id", producer_id, "--ack-sent-id", "--no-read-the-room", text], timeout=UPVOTE_GH_TIMEOUT_S, env=scrubbed_env)` (§I) — module path, **never** the bare `valor-telegram` console script (Prior Art #2566: the shim on PATH resolves to a stale 3.12 interpreter and crashes on import outside the launchd-pinned PATH). Do **not** build an outbox payload here. A non-zero exit (including a promise-gate BLOCK) means no announcement: emit a finding and start nothing.
- **Both send guards are mandatory on every invocation, announcement and retraction alike (§I).** `--no-read-the-room` is always on the argv (without it, an inherited `VALOR_SESSION_ID` arms the RTR pass, whose `suppress` verdict enqueues a *reaction* instead of the message and still exits 0). And the env is built explicitly — `scrubbed_env = {k: v for k, v in os.environ.items() if k not in ("VALOR_SESSION_ID", "TELEGRAM_REPLY_TO", "AGENT_SESSION_ID")}` — never inherited wholesale, because an inherited `TELEGRAM_REPLY_TO` would thread the announcement under an unrelated message in another chat (`tools/valor_telegram.py:1056-1062`) and `AGENT_SESSION_ID` would misattribute ownership (`:1103-1105`). Treat exit 0 as "enqueued", never as "delivered"; only the ack confirms delivery.
- Then `bridge.outbox_ack.await_sent_message_id(producer_id, UPVOTE_ANCHOR_WAIT_S)` (default 20s, provisional) → re-read gates 1 and 3 → `create_session(..., chat_id=str(eng_chat_id), telegram_message_id=anchor)`.
- Producer id `upvote-{project_key}-{issue}-{int(time.time())}` (§Race 3).
- On create failure: `SETEX upvote:pickup:failed:{repo}:{N} UPVOTE_FAILURE_BACKOFF_S <truncated error>` (§D — a plain non-Popoto string key, precedent `reflections/sdlc_progress.py:353-364`), then send the threaded retraction (same `-m tools.valor_telegram send` invocation plus `--reply-to <anchor>`) and return the outcome as a finding. On a positive re-read (someone else started it) send the retraction but write **no** backoff key — that is a benign outcome, distinguished from an error in the finding text.
- Define the constants `UPVOTE_LANE_MAX_LIVE`, `UPVOTE_LANE_MAX_LIVE_MACHINE`, `UPVOTE_CANDIDATE_SCAN_MAX`, `UPVOTE_ANCHOR_WAIT_S`, `UPVOTE_FAILURE_BACKOFF_S`, `UPVOTE_GH_TIMEOUT_S`, `UPVOTE_RUN_BUDGET_S`, `UPVOTE_ENTRY_TIMEOUT_S` here, all named/env-overridable with grain-of-salt comments. Derive `UPVOTE_CREATE_WORST_CASE_S = settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s` and `UPVOTE_PICKUP_WORST_CASE_S = 2 * UPVOTE_GH_TIMEOUT_S + UPVOTE_ANCHOR_WAIT_S + UPVOTE_CREATE_WORST_CASE_S` — **derived, never fresh literals**, so a `TIMEOUTS__UV_SYNC_S` override cannot invalidate the budget arithmetic. Check the deadline by early return at the top of every per-project and per-candidate iteration (§B).
- Every subprocess call carries an explicit `UPVOTE_GH_TIMEOUT_S`; the module docstring states the worst case as `len(load_local_projects()) × (UPVOTE_CANDIDATE_SCAN_MAX × UPVOTE_GH_TIMEOUT_S) + UPVOTE_PICKUP_WORST_CASE_S`, names `create_session`'s cold-worktree `uv sync` as the dominant term, and states both invariants: `UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S < UPVOTE_ENTRY_TIMEOUT_S` and `UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S`.
- Registration is **not** done here — it is Task 4b. Do not edit `config/reflections.yaml`.

### 4b. Update-system registration
- **Task ID**: build-reflection-registration
- **Depends On**: build-upvote-reflection
- **Validates**: `tests/unit/test_reflection_register.py`
- **Informed By**: cycle-2 BLOCKER (`config/reflections.yaml` is gitignored, untracked, and clobbered from the vault on every `/update`); `scripts/update/reflection_register.py` module docstring; `scripts/update/run.py:833`
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `scripts/update/reflection_register.py` by threading the optional keywords through all **three** layers, not two. `_append_entry` (`:225-232`) sits between `register_reflection` and `_build_entry_block` and hard-declares `cadence: str` as a required keyword-only parameter before re-passing it at `:256-261`; `register_reflection` builds a fixed `entry_kwargs` dict containing `cadence` (`:448-453`) and splats it into `_append_entry` at both call sites (`:455` vault target, `:482` repo copy). Touching only two layers raises `TypeError: _append_entry() got an unexpected keyword argument 'cron'` — or gets "fixed" by passing `cadence=""`, which emits an empty `every:` line that `load_registry` skips as invalid: exactly the silent Risk 1 failure.
  - `_build_entry_block(dash_indent, *, name, callable_path, description, priority, cadence=None, cron=None, cron_tz=None, timeout=None)` — emits `every: {cadence}` XOR `cron: {cron}` (plus a following `cron_tz:` line when set), and a `timeout:` line only when `timeout is not None`.
  - `_append_entry` mirrors the same signature (`cadence: str | None = None` plus the three new keywords) and forwards them.
  - `register_reflection` raises `ValueError` unless exactly one of `cadence` / `cron` is truthy, and builds `entry_kwargs` accordingly so both `_append_entry` call sites stay a single splat.
  - Leave the `_append_entry` re-parse validation at `:266-283` untouched — it is what turns a malformed emitted block into an `"invalid"` verdict instead of a corrupted vault file.
  - The two existing callers pass `cadence=` and take that branch, so their emitted block is byte-for-byte unchanged.
- Add `UPVOTE_PICKUP_NAME` / `UPVOTE_PICKUP_CALLABLE` and a `register_sdlc_upvote_pickup(project_dir)` wrapper emitting exactly the block in §F, importing `UPVOTE_ENTRY_TIMEOUT_S` from `reflections.sdlc_upvote_lanes` rather than repeating `1500`.
- Wire it into `reflection_register.main()`'s register tuple and into `scripts/update/run.py`'s registration block (`:833`, alongside `register_crash_recovery` / `register_memory_distill_backfill`, same `RegisterResult` logging). It must stay **before** Step 1.66's `env_sync.sync_reflections_yaml()`.
- Never write `config/reflections.yaml` directly; the vault-first `_resolve_target()` already handles it, and the existing best-effort repo-copy append is enough.

### 5. Tests
- **Task ID**: test-upvote-pickup
- **Depends On**: build-upvote-reflection, build-reflection-registration
- **Validates**: the full list in `## Test Impact`
- **Assigned To**: pickup-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/reflections/test_sdlc_upvote_lanes.py` modeled on `test_sdlc_progress_check.py`: each skip gate independently, ordering, one-per-tick, the ceiling, the `Eng:`-group scope gate (fixtures with no Eng group and with a malformed `chat_id`), the machine-ownership gate issuing zero subprocess calls, two consecutive ticks starting exactly one lane, the anchor happy path, anchor timeout, create failure + retraction, and ack cross-talk isolation.
- **Backoff test — must be able to fail (cycle-2 blocker).** Drive `create_session` returning `CreateResult(success=False)` with **no `AgentSession` row written at all** (that is what a pre-`_push_agent_session` failure looks like), assert `upvote:pickup:failed:{repo}:{N}` was set, and assert the *second* tick does not re-announce (zero `valor-telegram send` invocations for that issue). A test that pre-creates a terminal-FAILED `AgentSession` passes against the defective design and proves nothing — assert gate 1.6 separately with its own fixture, and assert that an expired backoff key does allow a retry.
- **`_lock_says_live` is imported, not copied:** assert `reflections.sdlc_upvote_lanes._lock_says_live is reflections.utilities._lock_says_live` and that `reflections.sdlc_progress._lock_says_live` is the same object (§J), so a future copy-paste regresses a test. The identity assertion is compatible with the chosen re-export form — it rules out only the wrapper form, which §J rejected for its own reasons.
- **The `_lock_says_live` move's test-side cost is one fixture line, not zero.** Add `monkeypatch.setattr(reflections.utilities, "_get_redis", lambda: r)` to `test_sdlc_progress_check.py`'s `fake_redis` fixture (keeping the existing `sdlc_progress` patch) and change nothing else in that suite; then run it and confirm all twelve `_lock_says_live` / `_lane_is_live` tests at `:311`-`:379` pass (§J).
- Add a registration test in `tests/unit/test_reflection_register.py` following that module's existing `REFLECTIONS_YAML`-temp-file pattern: run `register_sdlc_upvote_pickup` against a temp vault registry, then `load_registry(path=<tmp>)`, assert the entry **survives the load** (a skipped-as-invalid entry is the Risk 1 failure and shows up as an empty match list), assert `entry.validate() == []` (it returns `list[str]` and never raises), assert `entry.effective_timeout() == UPVOTE_ENTRY_TIMEOUT_S`, and assert `compute_next_due(entry.schedule, None)` lands in the 06:00–22:00 America/Los_Angeles window. Also assert idempotence (a second call is a `noop`) and that the two existing registrations' emitted blocks are unchanged.
- **Budget invariants (both, directly):** `UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S < UPVOTE_ENTRY_TIMEOUT_S`, and `UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S` (violating the second makes the admission check unsatisfiable and ships the feature silently inert). Also assert `UPVOTE_CREATE_WORST_CASE_S` tracks `settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s` rather than being a literal, and that the registered `timeout:` derives from `UPVOTE_ENTRY_TIMEOUT_S` (§B).
- Update the impacted suites listed in `## Test Impact`.
- **Ordering test (must be able to fail):** the fixture is **larger than `_gh_issue_list`'s page size** and asserts the pick is the true oldest. A 3-item fixture passes against the defective client-side-sort implementation and therefore proves nothing. Additionally assert the constructed `gh` argv contains `--search sort:created-asc`, so the server-side sort is verified structurally and not only through a stub that happens to return sorted data.
- **Scan-cap test:** assert the `_gh_issue_list` argv carries `--limit <UPVOTE_CANDIDATE_SCAN_MAX>` (the single knob — §A/§B) and that with a stubbed over-length page the gate loop issues at most that many per-candidate subprocess calls. Asserting the argv is stronger than counting calls after a client-side slice, because it proves the truncation happens where the ordering guarantee lives.
- **Cross-project slug-collision test (§C):** two projects whose candidate issues share an issue number, with a live non-terminal `AgentSession(slug="sdlc-42")` belonging to the first. Assert the second project still gets its pickup and that a finding names both projects. A one-project fixture passes against the single-field gate and proves nothing.
- **Aggregate budget test (Risk 4) — must exercise the create path.** Stub `create_session` with a `time.sleep` longer than the remaining budget and assert the **whole** `run_sdlc_upvote_lanes()` call returns inside `UPVOTE_ENTRY_TIMEOUT_S`. A never-arriving-ack fixture never enters the create path and therefore passes against the defective design — it proves nothing and must not be the only budget test. Keep the ack-timeout fixture as a separate case for the anchor-wait bound.
- **Admission-check test:** with the run-scoped deadline set so `deadline - now < UPVOTE_PICKUP_WORST_CASE_S`, assert the pickup is deferred with the documented finding, **zero** send subprocess calls are made, and `create_session` is never called.
- **Budget early-return test:** with an already-expired deadline, assert every project returns `status: "skipped"` with the `"budget exhausted; project not scanned"` finding, that the sweep still visits every project (`run_per_project_audit` owns the loop and cannot be broken), and that no subprocess call is issued.
- **Machine-wide ceiling test:** fixtures where each project is individually under `UPVOTE_LANE_MAX_LIVE` but the accumulated total reaches `UPVOTE_LANE_MAX_LIVE_MACHINE`; assert later projects in the sweep are refused with the machine-wide finding and that no extra `gh pr list` call was added to achieve it.
- **Ack opt-in test:** assert `process_outbox` performs **no** `telegram:sent:*` write for a payload without `ack_sent_id`, and does write for one with it.
- **Send-path test:** assert the reflection's send argv begins `[sys.executable, "-m", "tools.valor_telegram", "send", …]` — explicitly assert `argv[0] == sys.executable` and that `"valor-telegram"` is **not** `argv[0]`, so a regression to the stale-shim form (#2566) fails a test — and contains `--session-id <producer_id>`, `--ack-sent-id`, and `--no-read-the-room`; assert the reflection never `RPUSH`es `telegram:outbox:*` itself (§I); assert a non-zero exit from that subprocess yields a finding and **no** `create_session` call.
- **Send-env scrub test (§I):** with `VALOR_SESSION_ID`, `TELEGRAM_REPLY_TO` and `AGENT_SESSION_ID` all set in `os.environ` via `monkeypatch`, assert the `env=` kwarg passed to `subprocess.run` for **both** the announcement and the retraction contains none of the three, and that `--no-read-the-room` is on both argvs. This test must be able to fail: an implementation that omits `env=` inherits all three and passes every other send-path assertion.
- Add an integration test asserting the persisted `AgentSession.telegram_message_id` matches what `create_session` was given.
- Run via `scripts/pytest-clean.sh`, never bare `pytest`.

### 6. Documentation
- **Task ID**: document-upvote-pickup
- **Depends On**: test-upvote-pickup
- **Validates**: `## Verification` rows "Feature doc exists" (`test -f docs/features/upvote-autonomous-sdlc-pickup.md`), "Feature doc indexed" (`grep -c 'upvote-autonomous-sdlc-pickup' docs/features/README.md`), and "`CLAUDE.md` documents the label"
- **Informed By**: `## Documentation`; §B (the budget and both ceilings must be documented as operator-tunable); §G (the manual repro names `.venv/bin/python -m tools.valor_session`, not a `valor-session` command)
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
- **Scratch-issue end-to-end dry run — this is what makes the human-visible criterion verifiable at merge time.** The Freshness Check found one open `upvote` issue (#2716), but gate 2 skips it on its `PipelineLedger` progress, so "verified after the first real pickup" cannot happen at merge, and the manual `python -m tools.valor_session create` fallback exercises only the Task 1 plumbing — it invokes neither `run_sdlc_upvote_lanes`, nor `resolve_eng_group`, nor the send path, nor the relay ack, so it cannot detect the integration failures that actually break threading. So, on the machine owning `valor` and **after both restarts** (bridge and reflection worker — `## Update System` flags that anchoring degrades silently to `telegram_message_id=0` if only one restarts, and this run is what catches it):
  1. Open a throwaway issue in this repo and label it `upvote`.
  2. Set `SDLC_UPVOTE_PICKUP_ENABLED=false` in the reflection worker's environment for the duration of this run, so the scheduled tick cannot pick the scratch issue up concurrently with the manual invocation below. Run `.venv/bin/python -c "from reflections.sdlc_upvote_lanes import run_sdlc_upvote_lanes; print(run_sdlc_upvote_lanes())"` in a foreground shell where the var is unset (the manual run must exercise the real gates, only the scheduler is disarmed).
  3. Assert by eye and by record: the announcement appears in `Eng: Valor`; the created session's persisted `telegram_message_id` is non-zero and equals that message's id; the lane's first outbound message renders as a **reply under** the announcement.
  4. Remove the `upvote` label and close the scratch issue **before** killing the created session, so no window exists in which the issue is both labeled and lane-free (kill-first would re-open the scratch issue as a live candidate for the next scheduled tick). This is the one place the "no label mutation" No-Go does not apply — that invariant constrains the reflection's own code, not the human running this validation. Record the outcome (including the observed message id) in the feature doc.
- The human-visible Success Criterion is gated on **this** run, not on "the first real pickup".

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/reflections/test_sdlc_upvote_lanes.py tests/unit/test_valor_session_create_core.py tests/unit/test_telegram_relay_chat_log.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Registration ships as tracked code | `grep -c 'sdlc-upvote-pickup' scripts/update/reflection_register.py` | output > 0 |
| Registration is wired into `/update` | `grep -c 'register_sdlc_upvote_pickup' scripts/update/run.py` | output > 0 |
| Registration → load → validate → schedule | `scripts/pytest-clean.sh tests/unit/test_reflection_register.py -q` | exit code 0 — includes the Risk 1 test that registers into a temp registry, reloads with `load_registry`, asserts the entry survived and that `entry.validate() == []`, and asserts the next-due window |
| Cron string parses | `python -c "from agent.reflection_schedule import compute_next_due; print(compute_next_due('cron: 0 6-22/2 * * *; tz=America/Los_Angeles', None))"` | exit code 0, prints an epoch |
| **Anti-criterion:** no hand edit of the gitignored config copy | `! git diff origin/main --name-only \| grep -q '^config/reflections.yaml$'` | exit code 0 (the file is gitignored; a diff entry means someone force-added it) |
| `create_session` takes the anchor | `python -c "import inspect,tools.valor_session as m; assert 'telegram_message_id' in inspect.signature(m.create_session).parameters"` | exit code 0 |
| Hardcoded `0` is gone | `grep -n 'telegram_message_id=0' tools/valor_session.py` | exit code 1 |
| Feature doc exists | `test -f docs/features/upvote-autonomous-sdlc-pickup.md` | exit code 0 |
| Feature doc indexed | `grep -c 'upvote-autonomous-sdlc-pickup' docs/features/README.md` | output > 0 |
| `CLAUDE.md` documents the label | `grep -c '`upvote`' CLAUDE.md` | output > 0 |
| **Anti-criterion:** no label mutation | `! grep -qE 'issue edit.*--(add\|remove)-label' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no issue closing | `! grep -qE 'gh.*issue.*close' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no claim key | `! grep -qiE '(setnx\|set_nx\|nx\s*=\s*True)' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no stage selection | `! grep -qE '"(PLAN\|BUILD)"' reflections/sdlc_upvote_lanes.py` | exit code 0 |
| **Anti-criterion:** no raw Redis on Popoto keys | `! grep -qE '\.(delete\|srem\|sadd\|zrem)\(' reflections/sdlc_upvote_lanes.py` | exit code 0 — the backoff key uses `SETEX`/`GET` on a non-Popoto namespace (§D) |
| **Anti-criterion:** the reflection is not a second outbox producer | `! grep -q 'telegram:outbox' reflections/sdlc_upvote_lanes.py` | exit code 0 (§I — sends go through `tools.valor_telegram`) |
| **Anti-criterion:** no bare console-script PATH lookup | `! grep -qE '"valor-(telegram\|session)"' reflections/sdlc_upvote_lanes.py` | exit code 0 (§I / #2566 — the send is `[sys.executable, "-m", "tools.valor_telegram", …]`) |
| The send is interpreter-pinned | `grep -c 'sys.executable' reflections/sdlc_upvote_lanes.py` | output > 0 |
| Sends disarm Read-the-Room | `grep -c 'no-read-the-room' reflections/sdlc_upvote_lanes.py` | output > 0 (§I — an inherited `VALOR_SESSION_ID` otherwise arms RTR, whose `suppress` verdict enqueues a reaction and still exits 0) |
| Sends scrub the inherited session env | `grep -c 'TELEGRAM_REPLY_TO' reflections/sdlc_upvote_lanes.py` | output > 0 (§I — the key must appear in the subprocess env exclusion set) |
| Budget arithmetic is derived, not literal | `python -c "import reflections.sdlc_upvote_lanes as m; from config.settings import settings; assert m.UPVOTE_CREATE_WORST_CASE_S == settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s; assert m.UPVOTE_PICKUP_WORST_CASE_S < m.UPVOTE_RUN_BUDGET_S; assert m.UPVOTE_RUN_BUDGET_S + m.UPVOTE_GH_TIMEOUT_S < m.UPVOTE_ENTRY_TIMEOUT_S"` | exit code 0 |
| **Anti-criterion:** no Telethon in the reflection's import graph | `! grep -q 'telegram_relay' reflections/sdlc_upvote_lanes.py` | exit code 0 (the ack reader lives in the leaf `bridge/outbox_ack.py`) |
| **Anti-criterion:** the liveness rule is not forked | `! grep -q 'def _lock_says_live' reflections/sdlc_upvote_lanes.py` | exit code 0 (§J — imported from `reflections/utilities.py`) |
| **Anti-criterion:** no Popoto model change | `git diff origin/main --name-only -- models/ \| grep -c 'models/'` | match count == 0 |

Every anti-criterion row is written as `! grep -q…` returning **exit code 0** on success, deliberately. `grep -c` exits **1** when it finds zero matches, so a validator running `grep -c …` as a pass/fail command reads every *satisfied* anti-criterion as a failure — the trap the earlier draft of this table walked into. The rows also match **code constructs**, never English prose: the claim-key row greps for `setnx` / `set_nx` / `nx=True`, not the word `claim`, because `## Documentation` mandates a module docstring that states the no-claim-key invariant in prose and a prose-matching row would be failed by its own required documentation. The Popoto-model-change row's `Expected` cell reads exactly `match count == 0` and nothing else: `agent/verification_parser.py:232` matches that phrase with exact string equality, not a prefix or a regex, so any appended text drops the row past that branch into the positive `exit code N` fall-through, where `grep -c`'s exit status 1 on zero matches reads as a failure. A clean branch emits the literal digit `0` on stdout with exit code 1 -- non-empty stdout, so the empty-stdout gate does not reject it, and `match count == 0` reads that `0` as a pass.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | NON-BLOCKING. §D ("Two bounded degradations") / Task 4 / `## Error State Rendering` — the ack-timeout degradation and the create-failure retraction can co-occur, and the plan never says what the retraction does when there is no anchor. §D permits `await_sent_message_id` to return `None` (or `DELIVERED_NO_ID`) and then creates with `telegram_message_id=0`; if that create fails, Task 4 instructs the builder to send the retraction "plus `--reply-to <anchor>`" against a `None` anchor. `--reply-to` is `type=int` at `tools/valor_telegram.py:1362-1363`, so the builder either passes `"None"` (argparse exit 2, retraction silently lost) or `0`, which `cmd_send` treats as falsy at `:1056` and falls through to the env fallback §I just scrubbed. The one path with both degradations is the one most in need of a visible retraction and the least likely to post one. | pending | Build the retraction argv unconditionally and splice the anchor in only when truthy: `argv = [sys.executable, "-m", "tools.valor_telegram", "send", "--chat", str(eng_chat_id), "--session-id", f"{producer_id}-retract", "--no-read-the-room", text]`, then `if anchor: argv[-1:-1] = ["--reply-to", str(anchor)]`. Guard on truthiness, not `is not None`, because both the timeout path and `DELIVERED_NO_ID` can yield `0`. Use a distinct `-retract` producer id: reusing the announcement's id would `RPUSH` onto `telegram:outbox:{producer_id}` while that id's ack list may still be un-consumed, the exact cross-talk Race 3 exists to prevent. Assert it: with `await_sent_message_id` stubbed to `None` and `create_session` returning `success=False`, a retraction subprocess is still invoked and `--reply-to` is absent from its argv. |
| CONCERN | Risk & Robustness | NON-BLOCKING. Task 7 (scratch-issue dry run) / `## Success Criteria` (human-visible row) — Task 7 opens a throwaway `upvote`-labeled issue on the real `tomcounsell/ai` and runs `run_sdlc_upvote_lanes()` by hand "after both restarts", but nothing takes the *scheduled* entry out of play for the duration. The restart that Task 7 demands is the natural consequence of running `/update`, which is also what registers the entry, so the scratch issue is simultaneously a live candidate for the scheduled tick: a second unattended lane (real worktree, real `uv sync`, real branch) can start on a throwaway issue. Step 4 explicitly kills the manual session, which re-opens the candidate for the next tick. | resolved | Two ordered additions to Task 7. (a) Run the dry run with `SDLC_UPVOTE_PICKUP_ENABLED=false` in the reflection worker's environment (the entrypoint kill switch §F already specifies) while invoking `run_sdlc_upvote_lanes()` in a foreground shell where the var is unset. (b) In step 4, remove the `upvote` label and close the scratch issue BEFORE killing the created session, so no window exists in which the issue is both labeled and lane-free. This is the one place the "no label mutation" No-Go does not apply: that invariant constrains the reflection's code (`! grep -qE 'issue edit.*--(add\\|remove)-label'`), not the human running the validation. |
| CONCERN | History & Consistency | NON-BLOCKING. `## No-Gos` (closing paragraph and item 0) vs. spike-3 / spike-4 vs. issue #2717 ACs 5 and 11 — `## No-Gos` asserts "Every acceptance criterion on issue #2717 ... ships in this PR" and records exactly one deviation (item 0, AC 2's `config/reflections.yaml`). Two further ACs are also deliberately not satisfiable as written and neither is recorded. AC 5 names "plan-doc existence" and "reusing `agent/pipeline_state.py`", both of which spike-3 rejects (`derive_from_durable_signals` keys `docs/plans/{session.slug}.md` = `sdlc-2717.md`, which never exists; `_durable_run` at `agent/pipeline_state.py:1463` runs with no `cwd`) in favor of `PipelineLedger.get`. AC 11 names "the derived-state decision table (each row)", which spike-4 collapses to a binary start/skip. Both substitutions are right; the contradiction is the full-coverage claim, which will make `/do-pr-review` and `/do-merge` read ACs 5 and 11 as unmet. | resolved | Plan-text and merge-comment change only; no task, test, or verification row moves. Extend merge-time follow-up 0 from one substitution to three — AC 2 `config/reflections.yaml` → vault registry via `scripts/update/reflection_register.py`; AC 5 plan-doc existence + `agent/pipeline_state.py` → `PipelineLedger.get(f"{org}/{repo}", N)` per spike-3; AC 11 "each row of the derived-state decision table" → the six skip gates of §C per spike-4 — and soften the closing sentence to "every acceptance criterion except the three amended at merge time." |
| NIT | Risk & Robustness | NON-BLOCKING. §I / Risk 4 layer 1 / Task 4 — `--no-read-the-room` is justified partly as removing "an unbounded-latency model call from inside the `UPVOTE_GH_TIMEOUT_S` subprocess budget", but `cmd_send` still runs `bridge.promise_gate.cli_check_or_exit`, which is LLM-first (`bridge/promise_gate.py:11-17`) with no per-call bypass. One unbounded-latency model call therefore remains on the send path by design, so `UPVOTE_GH_TIMEOUT_S` (default 30) simultaneously bounds a local `gh` invocation and an interpreter start plus a Haiku round-trip. | pending | Either name a separate `UPVOTE_SEND_TIMEOUT_S` for the two send subprocesses or keep one knob and say in the module docstring that the send budget covers a promise-gate LLM round-trip. Either way catch `subprocess.TimeoutExpired` explicitly — it is not a subclass of `CalledProcessError` — and treat it exactly like a non-zero exit (finding, no `create_session`). Safe because `cli_check_or_exit` runs at `tools/valor_telegram.py:893`, before the outbox `RPUSH` at `:1113`, so a killed send never leaves a half-published announcement. |
| NIT | History & Consistency | NON-BLOCKING. §G (the `create_session` signature block, `chat_id="0", telegram_message_id=0,   # <- new`) vs. `## Verification` row "Hardcoded `0` is gone" — that row runs `grep -n 'telegram_message_id=0' tools/valor_session.py` and expects exit 1, but §G's own illustrative signature writes the new parameter unannotated as `telegram_message_id=0`, so a builder copying that block verbatim fails the plan's own check on the parameter default rather than on the `_push_agent_session` literal it targets. | pending | `grep 'telegram_message_id=0'` does not match `telegram_message_id: int = 0` (annotation plus surrounding spaces), and the surrounding file already writes annotated defaults (`chat_id: str = "0"`, `tools/valor_session.py:452-462`), so annotating the §G signature line is sufficient and the verification row needs no change. Verified against the file as it stands: `grep -n telegram_message_id tools/valor_session.py` returns exactly one line, `:639`. |
| NIT | History & Consistency | NON-BLOCKING. §B ("Aggregate worst case, corrected.") and `## Documentation` → Inline Documentation vs. §B invariant 1 — the mandated module-docstring closed form `len(load_local_projects()) × (UPVOTE_CANDIDATE_SCAN_MAX × UPVOTE_GH_TIMEOUT_S) + UPVOTE_PICKUP_WORST_CASE_S` evaluates to 5×(10×30)+740 = 2240s with the plan's own measured project count and defaults, which is above the entry `timeout:` of 1500 the same section derives. The run cannot reach it (the deadline plus one straddling `gh` call caps at ~1230, and the admission check forbids a pickup starting after t=460), so the docstring would document a worst case the plan elsewhere proves impossible. | pending | Docstring wording only — no constant and no test changes. State the bound as `UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S` (the invariant already asserted by tests) and keep the per-project inspection product as an explanatory term for why the deadline exists rather than as the bound itself. |
| NIT | Scope & Value | NON-BLOCKING. Whole document / `## Appetite` ("Size: Medium") — the plan is 802 lines for one reflection callable, one additive `create_session` parameter, two additive CLI flags, and a ~30-line leaf module. Large stretches dictate implementation the builder owns rather than decisions the builder cannot make (the exact env dict comprehension, the exact `fake_redis` fixture line stated three separate times, the exact argparse signatures, the exact argv splice order). Correctness is not the issue; the issue is that four revision cycles have now been spent at this granularity and each restatement is a place the plan and the code can disagree at review time. | pending | No code change. Do not revise further for this — treat the remaining fine-grained prescriptions as builder guidance rather than contract. If a future revision touches this plan for another reason, collapse the three restatements of the one-line `fake_redis` fixture edit (§J, Task 3 bullet 2, Task 5 bullet 3) into one. |
| NIT | Scope & Value | NON-BLOCKING. §G / Task 1 / `## Agent Integration` / Task 7 — Task 1 adds `--telegram-message-id` to a module the plan establishes has no console-script entry, justified solely as "the manual path named in the human-visible Success Criterion". Task 7 then retires that justification: the manual path "exercises neither the reflection, nor `resolve_eng_group`, nor the send path, nor the relay ack", and the criterion is gated on the scratch-issue dry run instead. | pending | Builder-level either way and it blocks nothing. Keep the flag (one argparse line, genuinely useful for isolating the plumbing) but stop describing it as what verifies the human-visible criterion. If dropped instead, only the new `tests/unit/test_valor_session_cli.py` case in `## Test Impact` goes with it — the production path imports `create_session` directly. |

---

## Open Questions

All four open questions carried by issue #2717 are resolved in this plan and are **not** open:

1. **Ordering** → oldest-first by `createdAt`, tie-broken by issue number (Technical Approach §A).
2. **Concurrency ceiling** → yes, `UPVOTE_LANE_MAX_LIVE` default 3 per project, counted approximately from open `session/sdlc-*` PRs plus live locks (§B).
3. **Announce-then-create atomicity** → announce first, capture the id via a new relay ack, then create; threaded retraction on failure; bounded degradation to an unanchored start on ack timeout (§D).
4. **Post-merge `upvote` residue** → the `--state open` filter is sufficient; terminal state is "issue closed"; the one hole (merged PR, issue still open) is closed by skip gate 4 using `--state all` (§E).

No questions remain for the supervisor.
