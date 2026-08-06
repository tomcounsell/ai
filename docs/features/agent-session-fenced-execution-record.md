# AgentSession Fenced Execution Record

Milestone 1 of the durability Room/Job/AgentSession refactor ([#2494](https://github.com/tomcounsell/ai/issues/2494); plan [`docs/plans/durability-room-job-agentrun.md`](../plans/durability-room-job-agentrun.md)). This is the schema-and-liveness half of that plan; the Room/Job model lands in later milestones. Issue [#2518](https://github.com/tomcounsell/ai/issues/2518) closed out the fence-application gaps a post-merge canary run found; those fixes are folded into this doc rather than kept as a separate changelog.

## What it is

`AgentSession` records **one fenced execution record** for the process currently running a turn, replacing a per-turn trio of raw process-id fields. The record is:

| Field | Type | Meaning |
|-------|------|---------|
| `exec_pid` | plain nullable `IntField` | OS pid of the live harness subprocess. `None` for in-process Task subagents (no OS subprocess). **Deliberately NOT an `IndexedField`.** |
| `pid_create_time` | `FloatField` | psutil `create_time()` of `exec_pid`, captured at spawn — the PID-reuse fence. |
| `exec_cwd` | `Field` | Absolute working dir the spawn ran in (resume is cwd-scoped). |
| `exec_harness` | `Field` | Harness enum string (`"claude"` today; future codex/opencode/pi need no schema change beyond this value). |
| `spawn_history` | `ListField` | Append-only `[{pid, create_time, cwd, harness, generation, agent_id, ts}, …]`. Newest entry is the live fence; a died-resumed-died-again timeline stays reconstructable for the session TTL, not bounded by count. |

Stamped by the runner's `_on_turn_spawn` via `AgentSession.stamp_execution_spawn(...)` **before** the turn-await blocks, so a worker crash mid-turn always leaves a reapable, fenced record. `AgentSession.live_fence` returns the newest `spawn_history` entry (falling back to the denormalized scalars for a partially-written record). A save failure inside `stamp_execution_spawn` logs at WARNING, not DEBUG: this save is the fence's single point of entry, so a silent failure there would degrade every downstream consumer (kill, reprieve, sweep, and ownership sites) to "no fence recorded" without anyone noticing.

### What it replaces

The former per-turn pid trio (`claude_pid`, `pm_pid`, `harness_pid`) and the write-only `expectations` field are **deleted** from the model, along with the dead `notify_sdk_started` callback path. There is no back-compat shim in the model — Popoto ignores unknown hash fields on load, so pre-cutover records hydrate fine, and a one-shot migration (below) reclaims the orphaned hash entries.

## The fence is DETECTION, not a guarantee

`agent/pid_fence.py` is the whole fence:

- `proc_create_time(pid)` — live `create_time` via psutil, or `None` if the pid is gone/unreadable.
- `fence_is_live(pid, recorded_create_time)` — `True` iff the pid is alive **and** still the same process we recorded, comparing the re-read `create_time` to the recorded one within `CREATE_TIME_TOLERANCE_S` (`1e-3`). A dead pid, a recycled pid (alive but different `create_time`), or a missing fence (`recorded_create_time is None`) all read `False`.
- `create_times_match(recorded, observed)` — the tolerance compare, factored out of `fence_is_live` so every consumer shares one definition of "same process." Two callers need it with different inputs: `fence_is_live` re-reads `create_time` live from the pid; `AgentSession.find_live_session_by_pid` compares a recorded time against one its caller already observed for the process it is holding, and must not re-read psutil a second time for the same pid.

There is an irreducible TOCTOU window between reading a live process' `create_time` and acting on its pid: the OS can recycle the pid in between. The race-free answer is Linux `pidfd`; **macOS has no pidfd**, and this system runs on darwin, so `create_time()` is the ceiling available here. A future reader must NOT mistake `(pid, create_time)` for a safety guarantee (see <https://lwn.net/Articles/784997/>).

For processes the runner spawns itself, the **retained child handle** (`_TurnHandle` in `agent/session_runner/runner.py`) is the primary liveness mechanism; the fence is the backstop for cross-process reads (another worker, the orphan reaper, the dashboard) that never held the handle.

### Staleness-by-compare, not null-between-turns

The fence is **not nulled between turns**. A stale fence pointing at a dead or recycled pid is recoverable — `fence_is_live` rejects it by comparing `create_time`. A *missing* fence is not recoverable, so the record is never cleared to reclaim it. Staleness is detected by comparison, never by absence.

## The legacy-row rule

Rows written before the fence existed carry a pid with no `create_time`, and a live process' `create_time` can also be unreadable (`AccessDenied`, psutil missing). The first version of this fence (PR #2516) left three different behaviors for that one condition:

- `_sweep_dead_worker_sessions` fell back to a bare `_pid_is_alive` liveness probe when `recorded_create_time is None`;
- `_apply_recovery_transition`'s pre-cancel snapshot skipped the fence check entirely when `_snap_ct is None`, so an unfenced pid fed straight into a real SIGTERM→SIGKILL — fail-open;
- the in-process orphan reap refused to signal at all on an unfenced row, so a genuine orphan with a legacy row leaked for the life of the machine — fail-closed, never reaps.

Issue #2518 replaced all three with one rule, written into `agent/pid_fence.py`'s module docstring as the canonical source:

> An unreadable or absent `create_time` on **either** side means "unknown". Unknown never authorizes an irreversible kill, and never authorizes more force than the site already applied before the fence existed. Unknown may authorize an action the site was already taking — including a recoverable SIGTERM on a positive liveness probe.

Concretely, `fence_is_live` returns `False` for unknown, and callers must read that as "cannot claim ownership," not "this process is dead." Grade the action by two questions:

- **Is it recoverable?** Popping a stale handle, sweeping a row to terminal, falling back to a plain liveness probe, and SIGTERM (which a healthy process may trap, drain, and exit cleanly from) all are. SIGKILL is not — the target gets no say. Unknown may authorize the first group; only a positive fence match authorizes SIGKILL.
- **Is it an escalation?** Unknown is a licence to keep doing what the site already did, never to do more. A site that reached for SIGTERM before the fence existed may still reach for it on unknown; a site that did not must not start.

This matches upstream psutil practice: treat an unfetchable `create_time` as "unknown → fall back," never as "assume valid."

Applying the rule closed both asymmetric sites. `_apply_recovery_transition`'s snapshot guard dropped its `_snap_ct is not None` clause: that site's pre-fence path fed an unfenced pid straight into a real SIGTERM→SIGKILL, an escalation unknown cannot authorize, so unknown now yields no pid to signal at all. The in-process orphan reap gained the same legacy fallback `_sweep_dead_worker_sessions` already had — the worked example of the rule's second clause. A legacy row with no recorded `create_time` gets a SIGTERM on a bare liveness probe, which is what that site did pre-fence and which refusing entirely turned into the never-reaps gap; it never earns SIGKILL escalation, because escalation requires a positive fence match. A *recycled* row (`create_time` recorded and mismatched) is not unknown at all — it is a positive "not ours" and gets no signal whatsoever. `_sweep_dead_worker_sessions` itself was left as-is; it was already the reference behavior.

## Consumer census

`fence_is_live`/`create_times_match` was applied at nine sites by PR #2516. Issue #2518's review of the change found six more sites that read `fence.get("pid")` and discarded the `create_time` sitting in the same dict — the pid source was rebound to the new fenced field, but nothing compared it. Two of the six were HIGH severity, and they failed in **opposite directions**:

| Site | Direction | What an unfenced/recycled pid did before #2518 | What fencing it does |
|------|-----------|--------------------------------------------------|------------------------|
| `_tier2_reprieve_signal` (`agent/session_health.py:1953`) | Under-kills | A recycled `exec_pid` probing as `"progressing"` grants a reprieve every tick, forever — `pid is not None` at the trailing predicate is permanently true since #2494 stopped clearing the fence, so it no longer discriminates anything. A dead session is held alive indefinitely. | Nulling the pid yields `"unknown"`, which routes to the count-based escalation guard and can return `None`. Strictly kill-increasing. |
| `_has_progress` → `subprocess_hang_verdict` (`agent/session_health.py:1740`) | Over-kills | An unrelated process occupying a recycled `exec_pid` that probes as `"hung"` bypasses the sticky-field honor and prematurely releases a session with real progress to Tier-2 recovery. | Nulling the pid yields `"unknown"`, which `if _verdict != "hung":` treats identically to `"progressing"` — sticky fields still honored. Strictly kill-reducing. |

Both HIGH sites are now guarded, along with the four LOWER-severity ones (`_pending_sigkill` staging/drain, `find_live_session_by_pid`'s ownership scan, `_owned_task_hang_check`, and the ownership lookups feeding the fast oneshot reaper and the forward orphan scan). The full list of guarded consumers, as of this write-up (`tools/check_fence_census.py --list`):

```
agent/agent_session_queue.py:2000  in _owned_task_hang_check()
agent/session_health.py:114        in _terminate_detached_harness()
agent/session_health.py:795        in _session_has_live_fence()
agent/session_health.py:1227       in _sweep_dead_worker_sessions()
agent/session_health.py:1740       in _has_progress()
agent/session_health.py:1953       in _tier2_reprieve_signal()
agent/session_health.py:3106       in _apply_recovery_transition()
agent/session_health.py:4520       in _agent_session_health_check()
models/agent_session.py:1344       in find_live_session_by_pid()
scripts/update/run.py:265          in _cleanup_stale_sessions()
ui/data/sdlc.py:1097               in _session_to_pipeline()
```

### Why this is an adjacency check, not a count

The original plan for an anti-criterion was a threshold: `grep -c 'fence_is_live' agent/session_health.py | output > 11`. A count is satisfied by *any* fenced site — it cannot tell "the census grew" from "one guarded site was deleted and one unguarded one was added." That is the PR #2516 failure mode exactly: a change that adds one unguarded consumer while removing one guarded site leaves the count unchanged and green.

`tools/check_fence_census.py` instead checks **per-site, function-scoped adjacency**: for every function body (and the module top level), it finds every read of `.get("pid")` off an expression rooted in `live_fence` — directly or through a local name bound to one — and requires that the *same function body* either calls `fence_is_live` / `create_times_match`, or reads `.get("create_time")` off that same fence (keeping both halves is the alternative to guarding directly; a function that forwards `create_time` onward to a real decision site, like `_session_to_pipeline` handing both values to `_check_process_alive`, is not in the defect class the checker guards against — the decision site is checked independently). Nested function/class scopes are checked as their own units, so a guard inside a closure does not vouch for a read in the enclosing function. Any site that satisfies neither is reported by exact `file:line`.

**How it is enforced.** There is no GitHub Actions job for it — `tests/unit/test_fence_census.py::test_repo_root_exits_zero` runs the checker against the repo root and asserts exit 0, so the census is enforced on every suite run, the same way `tests/unit/test_architectural_constraints.py` enforces import boundaries. The checker is also runnable by hand (`python tools/check_fence_census.py --list`) and is cwd-independent (`--root` defaults to its own repo, not the process cwd). Its red state is proved, not assumed: `tests/fixtures/fence_census_violator/` is a fixture tree the checker must exit 1 on, naming both violating functions with `file:line`.

Two sites read a fenced pid and drive nothing — `agent/session_health.py:3377` and `:3396`, both interpolating a pid into a `finalize_session` reason string or a `logger.warning` call. These carry the marker `# fence-census: log-only, not a decision consumer`, the sole exemption mechanism the checker honors. Any new exemption is a deliberate, reviewable annotation at the call site, not a silently rising threshold.

## Reverse pid lookup: forward scan over the status index

"Which live session owns OS pid X?" was previously an indexed reverse lookup (`find_by_claude_pid`) backed by an `IndexedField` on the pid. Indexing a pid creates one Redis Set per distinct pid value (unbounded cardinality — the #1271 pid-index anti-pattern, and a contributor to the phantom-key floods).

`AgentSession.find_live_session_by_pid(pid, create_time=None)` resolves ownership by a **bounded forward scan** over the low-cardinality `status` index: it builds an in-process `{live_pid: session}` map from each non-terminal row's live fence and returns the owner by membership. Bounded — dozens of live rows, each reached via an indexed `status` lookup. Callers that need a hard time budget (the fast orphan reaper) wrap this in a thread + timeout and fail toward reapable; that contract lives at the call site.

`create_time` is the psutil-observed `create_time` the *caller* already read for `pid`. Matching is a strict **refinement** of the pid-only match, never a broadening:

- both sides have a `create_time` → they must agree within `CREATE_TIME_TOLERANCE_S`; a mismatch means the pid was recycled and this row does NOT own it;
- either side recorded nothing → fall back to a pid-only match, the pre-#2518 behavior, so legacy rows still resolve.

Before this change, ownership answered on the bare pid, so a stale dormant row and a live running row both carrying `exec_pid=P` resolved by `frozenset` iteration order over `NON_TERMINAL_STATUSES` — nondeterministic across restarts under hash randomization. A fence-verified match now always wins over a pid-only one, and a multi-match (more than one row claiming the same pid at the same confidence level) logs a WARNING naming every candidate instead of silently resolving to whichever the scan happened to visit first. The per-status query's blanket `except Exception` was also narrowed to the specific lookup errors (`QueryException`, `ModelException`, `RedisError`, `AttributeError`, `TypeError`, `ValueError`); a poisoned cohort still logs the existing WARNING and the scan continues to the remaining statuses rather than unprotecting every row in that status silently. The scan also now routes through `_filter_hydrated_sessions`, per that helper's stated contract.

`agent/index_drift.py` was generalized in the same milestone: the at-rest index-drift safety check is now a per-model spec (`ModelDriftSpec` / `reconcile_model_index`) driven by a forward scan, with `claude_pid` removed from the enumerated `AgentSession` `IndexedField` set.

## `_pending_sigkill` is fenced, not bare pids

The staged-SIGKILL escalation drain (`agent/session_health.py`, issue #1218) used to stage bare `pid: int` values between health-check ticks: an orphan gets a SIGTERM on one tick, and if it hasn't cleaned itself up by the next tick (300s later), the staged pid is SIGKILLed unconditionally. The comment justifying "one tick is safe" cited "macOS recycles PIDs in ~5 minutes" — but the tick interval is 300s, the *same order* as that window, so elapsed time was never a defence.

`_pending_sigkill` is now `set[tuple[int, float | None]]`, staging `(pid, create_time)` alongside the reap that scheduled it, mirroring `_pending_sigkill_orphans` one screen away. The drain re-verifies with `fence_is_live` before signalling: a staged entry whose `create_time` no longer matches has been recycled to an unrelated process and is dropped unsignalled; a legacy entry staged with no recorded `create_time` is likewise dropped, per the legacy-row rule. Only a fence-matched reap stages the escalation in the first place — the legacy liveness-probe branch in the orphan reap (see above) sends SIGTERM but never stages SIGKILL, because it cannot prove the pid is still ours.

## `_has_progress` vs. `_tier2_reprieve_signal`: opposite failure directions

These two functions are the two HIGH-severity sites from the consumer census, and they are the single easiest thing to garble about this change, because fencing does the opposite thing at each of them.

**`_has_progress` over-kills today.** A recycled `exec_pid` probing as `"hung"` bypasses the sticky-field honor (`turn_count`, `log_path`, `claude_session_uuid`) and prematurely releases a session with real progress to Tier-2 recovery. Fencing this site is strictly **kill-reducing**: nulling a non-matching pid moves the verdict to `"unknown"`, and `if _verdict != "hung":` treats `"unknown"` exactly like `"progressing"` — both honor the sticky fields. Because the change can only ever *withhold* a hang verdict, never produce one, it shipped **enforcing, with no shadow phase**. A regression test pins the direction: patch `agent.pid_fence.proc_create_time` so a recycled pid would probe as hung, and assert `_has_progress` still returns `True` when the sticky fields show real progress.

**`_tier2_reprieve_signal` under-kills unfenced.** A recycled `exec_pid` probing as `"progressing"` returns a reprieve gate every tick, and the trailing `return "alive" if pid is not None else None` predicate has been permanently true since #2494 stopped clearing the fence between turns — on its own it discriminates nothing. A dead session with a recycled pid is held alive indefinitely. Fencing this site is strictly **kill-increasing**: withdrawing a reprieve that would otherwise be granted risks killing a session that was relying on it, which is a visible, user-facing failure if that session turns out to be legitimately alive by some other measure.

Both sites are now fenced, but the guard is written differently at each, and the difference is the point:

- **`_has_progress` nulls any pid the fence cannot claim**, including a pre-fence row that never recorded a `create_time`. Unknown is safe there because it can only withhold a hang verdict.
- **`_tier2_reprieve_signal` nulls a pid only on a POSITIVE "not ours"** — a `create_time` was recorded and no longer verifies, either because the pid was recycled to an unrelated process or because the live reading fails (the process is gone, `AccessDenied`, psutil missing). A row that never recorded a `create_time` is *unknown*, not "not ours", and keeps its reprieve until it ages out with session TTL. That legacy fallback is spelled out at the call site rather than inherited from `fence_is_live`, which returns the same `False` for both conditions, because the canonical legacy-row rule in `agent/pid_fence.py` forbids unknown authorizing more force than a kill-increasing site already applied.

When the pid is nulled at `_tier2_reprieve_signal`, `subprocess_hang_verdict` returns `"unknown"`, so the count-based escalation guard (`reprieve_count >= MAX_NO_OUTPUT_REPRIEVES`) and the trailing `pid is None` return both regain their authority. That trailing predicate is fence-driven, not a "did this session ever spawn" test: on its own `pid is not None` discriminates nothing, because #2494 deliberately stopped clearing the fence on exit.

Enforcement is unconditional. There is no config flag, no env var, and no `if ENFORCE_FENCE:` branch — a toggle would rot into a permanent fork in the logic, and `tests/unit/test_session_health_reprieve_fence.py` greps the live module to keep one out.

The same fence-then-null pattern (`pid, ct = fence.get("pid"), fence.get("create_time"); if pid is not None and not fence_is_live(pid, ct): pid = None`) is also applied at `_owned_task_hang_check` (`agent/agent_session_queue.py`), which drives the per-tool hang probe the same way `_has_progress` does, and is kill-reducing for the same reason.

## `/update` cutover: fenced stale-session cleanup

`scripts/update/run.py::_cleanup_stale_sessions` finalizes `running` sessions with no live process during every `/update` run. Before #2518 it decided liveness purely on `updated_at` recency (a 30-minute `RECENT_ACTIVITY_WINDOW`) and, when that was stale, `created_at` age — and its reason string unconditionally claimed `"stale cleanup (no live process)"` regardless of whether anything had actually verified a process was gone.

The fence is now consulted first, ahead of recency: a session whose fence resolves live (`fence_is_live` on the recorded `(exec_pid, pid_create_time)`) is skipped **at any age**, and that skip is counted separately from a recency-based skip so an operator rolling `/update` across the fleet can see the fence gate acting rather than inferring it from a total. `_cleanup_stale_sessions` now returns `(killed_count, skipped_recent, skipped_fence_live)`, and the run summary logs both skip counts on separate lines. Recency stays as the fallback for rows with no fence, and `created_at` age stays as the last resort for rows with neither a fence nor an `updated_at`. The reason string now names which signal actually decided the finalization — `"stale cleanup (fence verified: recorded process not live)"` when the fence positively resolved the process as gone, versus `"stale cleanup (stale heartbeat, liveness unverified)"` when only recency lapsed and nothing checked the process itself.

This is a correct refinement of a truthful reason string, not a rescue from an active fleet-rollout hazard. `HEARTBEAT_WRITE_INTERVAL` is 60s on an independent asyncio loop, well inside the 30-minute recency window, so a genuinely live session was already protected by recency before this change; the fence check makes the cleanup's own claims accurate and makes an already-live session's protection independent of the heartbeat writer as well.

## Dashboard: fence-aware liveness probe

The dashboard's session modal and row-level ghost badge previously asked "does this PID exist?" via a bare `os.kill(pid, 0)` probe — which returns success for whatever process now holds a recycled pid, not necessarily the one the session spawned.

`PipelineProgress` (`ui/data/sdlc.py`) gained a `pid_create_time: float | None` field alongside the existing `exec_pid`, threaded through `_session_to_pipeline` (which already read the fence dict and previously discarded `create_time`) and into the dashboard JSON via `ui/app.py`. `_check_process_alive(pid, create_time=None)` now returns a genuine three-state verdict:

- `True` — the recorded process is alive and confirmed to be the same process (`fence_is_live` matched).
- `False` — not live. Either the pid is gone entirely (ghost), or the pid is alive under a *different* `create_time` — recycled, belongs to something else.
- `None` (unknown) — `pid` is `None`/non-positive; or the pid exists but no `create_time` was ever recorded for it (legacy row — nothing to compare against); or the pid's identity itself could not be read (`PermissionError`/`OSError`/psutil unavailable).

A recycled pid renders as **not-live** (`False`), not as a green "alive" chip — the case the old `os.kill(pid, 0)` probe could never distinguish. A legacy row with no recorded `create_time` whose pid is still in the process table renders as **unknown**, not alive: the dashboard cannot vouch for it, so it says so rather than guessing. The ghost badge's CSS comment (`ui/static/style.css`) is updated to describe the fence rather than the old raw-probe semantics.

## Migration, cutover, and its observability fix

`scripts/migrate_strip_pid_fields.py` reclaims the orphaned `claude_pid` / `pm_pid` / `harness_pid` / `expectations` hash entries from existing records:

- **ORM-safe** — no raw `hdel`/`hset`. For each stale record it queues `instance.delete()` + `Model.save(instance)` on ONE transactional Redis pipeline (MULTI/EXEC), atomically rewriting the record with only current model fields; a crash mid-migration can never lose a record.
- **Terminal-only, but terminal rows are not quiescent.** Only records whose `status` is in `TERMINAL_STATUSES` are rewritten. Terminal does not mean nobody writes them: `agent.session_health.cleanup_corrupted_agent_sessions` re-saves every hydrated record, terminal ones included, as its no-op corruption probe, and `/update`, worker startup, and the `agent-session-cleanup` reflection all invoke it, restamping `updated_at` on terminal rows in the process. The safety property is therefore atomicity, not quiescence: the delete+recreate is one transactional pipeline, so a crash or an interleaved writer can never lose a record. A concurrent write landing between the read and the pipeline is lost, which is why the scope stays terminal-only — those rows carry no in-flight state worth racing for.
- **Idempotent** — a re-run finds zero stale records and no-ops.
- **Deferred rows do not age out via TTL.** Every popoto `save()` re-issues `EXPIRE` with `Meta.ttl`, so the 30-day backstop only fires on a record nothing writes for 30 days. `is_ledger=True` rows are re-saved continuously while their pipeline is open, holding a perpetually-refreshed TTL. A deferred row keeps its stale fields until a later run of the migration finds it terminal.
- **Detection fails closed.** A failed `HKEYS` read propagates rather than returning an empty set, so the record is counted in `errors` and the run exits 1 without being recorded complete. Counting it as clean would make a detection failure indistinguishable from a genuinely clean record, and a transient Redis blip would manufacture the exact "proof of cleanliness" artifact the re-runs exist to produce.

**Output capture.** All three strip scripts call `logging.basicConfig` with `stream=sys.stdout`, which is load-bearing: Python's default `StreamHandler` writes to **stderr**, and a stdout-only capture would then record an empty string while looking healthy. Every subprocess-shaped migration helper in `scripts/update/migrations.py` runs through `_run_migration_script` **except `_migrate_purge_phantom_agent_sessions`**, which shells out on its own because it needs an exit-3 branch and its own time budget, and is therefore the one helper that still discards its output on the success path and reports a stderr-only failure reason. The shared runner logs **both** captured streams line-by-line at INFO, prefixed `[migration:<label>]`, on the success path as well as on failure, and returns an error string carrying both tails. Logging on the success path is the point: a migration whose output is discarded has a failure mode indistinguishable from its success mode. The `MIGRATIONS: dict[str, tuple[callable, str]]` contract stays `str | None` — output goes to the logger rather than through the return value, so capturing it everywhere needs no contract widening.

**Shared strip engine.** `scripts/_strip_migration.py` holds the single copy of the scan, the terminal-only atomic delete+recreate, the zero-record guard, and the `clean_indexes()` sweep for all three "strip removed hash fields" migrations: `migrate_strip_pid_fields.py` (this one), `migrate_strip_pty_fields.py` (plan #1924 task 5), and `migrate_schema_diet_fields.py` (plan #1927). Consolidating onto one engine is what keeps the guard, the sweep and the exit codes from drifting apart across three copies. Each script is now a thin delegate keeping only its docstring and its own `STALE_FIELDS` set. `strip_pty_session_fields_v2` and `schema_diet_fields_v2` register the pty and schema-diet scripts under new `MIGRATIONS` names (#2524) so every machine re-runs them once now that they carry the guard — the same rename-to-rerun mechanism `strip_pid_fields_v2` used below.

**Zero-record guard.** The engine exits non-zero (code 2, distinct from the 1 used for per-record errors) with its own message when `total_records == 0`, and does not report success. This is insurance against the documented #1720 class-set window — `AgentSession.query.all()` reads `$Class:AgentSession`, which popoto's index rebuild deletes and re-adds in batches, and a scan landing inside that window returns 0 rows with no exception — not a fix for a proven cause; nothing establishes that window actually fired on any machine. `run_pending_migrations` only records a migration complete when its helper returns `None`, so a non-zero exit here means the next `/update` retries automatically. On a machine whose `AgentSession` keyspace is legitimately empty (a fresh install), this guard fails on **every** `/update`, indefinitely — that recurring `FAIL:` line is expected output on such a machine, not a live regression. Distinguishing a genuinely empty keyspace from a blinded scan is possible — a detection-only `SCAN` for `AgentSession:*` key names would do it — and is tracked as [#2543](https://github.com/tomcounsell/ai/issues/2543). Until that lands the retry is unbounded, and three migrations route through this guard, so a fresh install emits three recurring `FAIL:` lines.

**Index sweep is `clean_indexes()`, never a rebuild.** After an apply run that stripped records, the migration calls `AgentSession.clean_indexes()` — the production-safe orphan-reference cleanup. This is a defensive sweep, not a functional requirement: the per-record `delete()` + `save()` pair already maintains indexes atomically on its own pipeline.

Neither the raw `rebuild_indexes()` nor the `repair_indexes()` wrapper around it is used here, and reaching for either is a regression. A rebuild tears down and re-adds every index, which does two unacceptable things in this context:

- it opens the **#1720 class-set window** — the rebuild deletes and re-adds `$Class:AgentSession` in batches, and `query.all()` landing inside that window returns 0 rows with no exception, which is the exact ambiguous observation the zero-record guard exists to fail closed on. A migration that triggers the window it is guarding against is self-defeating;
- it currently **fails outright** on pre-existing phantom index metadata with `unpack(b) received extra data`, tracked as [#2536](https://github.com/tomcounsell/ai/issues/2536) — to be investigated, not blind-purged.

The raw-rebuild identifier must not appear in `scripts/_strip_migration.py` or in any of the three delegate scripts, not even in a comment — so the engine's own comment describes the excluded paths without naming them. Two tests enforce it: `tests/unit/test_strip_migration_shared.py` covers the delegates and `tests/unit/test_migrate_strip_pid_fields.py` covers the engine.

**Re-run mechanism.** `strip_pid_fields` was already recorded complete on every machine that had run `/update` post-cutover, and `run_pending_migrations` skips by name — so re-running the corrected script needed a new registration rather than an instruction to hand-edit `data/migrations_completed.json` (unauditable, and impossible on a machine nobody is sitting at). `strip_pid_fields_v2` registers the *same* script under a new name in `MIGRATIONS`, positioned after `strip_pid_fields` and before `purge_phantom_agent_sessions`. Every machine runs it exactly once on its next `/update`; the script's own idempotency makes a genuinely clean machine a fast no-op. Its value is provenance for future cutovers, not a retroactive discriminator of what the first run did (see Canary findings, below).

Cutover is **per-machine**: Redis is localhost on each machine, so there is no shared-state fleet hazard — each machine strips its own records when `/update` runs there.

## Recovery is keyed on session STATUS, not pid-absence

Interrupted-session recovery keys on `AgentSession.status` (the non-terminal statuses), not on whether a pid field is present or a process is alive. Pid-absence is not a recovery trigger; a session in a live status with a dead fence is recovered because its *status* says it should be running, and the fence tells the reaper the process is gone. This closes the silent-loss paths where a dead process left no status change for recovery to key on.

## At-rest owed-communication health check

`agent/session_health.py` adds an at-rest authorship check: a session with **no live fenced execution** (nothing running) that authored a reply but shows an activity anchor lagging the authorship anchor past a tolerance is flagged as owing communication. The activity anchor is the max of `last_stdout_at` / `last_tool_use_at` / `last_turn_at`, with a `session_events` authorship-scan fallback; the tolerance is a named, env-overridable constant seeded from a reference incident (activity-after-authorship gap of ~507s) and padded past the liveness-writer cooldown so a fresh activity write racing an authorship write does not false-positive. This detects death-after-authoring that leaves a reply un-delivered, independent of the pid fence.

## Canary findings

Issue [#2518](https://github.com/tomcounsell/ai/issues/2518) validated this milestone on one machine (a MacBook Air, worker-only, no bridge activated) before rolling `/update` to the fleet. The findings, kept honest including where an earlier diagnosis was wrong:

**The fence-application gaps.** The consumer census above — six unfenced consumers, two HIGH, opposite directions — is the substantive finding. It is why the change described in this doc exists as more than the original PR #2516.

**The migration episode.** A dry run on the canary machine found 23 records still carrying stale `claude_pid`/`pm_pid`/`harness_pid`/`expectations` hash fields (20 terminal, 3 deferred ledger rows), despite `strip_pid_fields` having been recorded complete. The first diagnosis was **"the migration self-certified — it recorded itself complete having stripped nothing." That diagnosis is retracted and was wrong.** The corrected account: the migration wrapper genuinely runs in apply mode (there is no code path that records completion without executing); post-cutover `AgentSession` defines none of the stale fields, so nothing in the running system could have written them; and all 23 stale records' `updated_at` postdates the migration's completion.

That last fact does not prove re-contamination, though, because a normal popoto `save()` writes fields via `HSET` and does **not** delete orphaned hash fields sitting alongside them — that is precisely why this migration needs delete+recreate rather than a plain save. So two hypotheses predict the *identical* observation: re-contamination by a writer that still defines the old fields (a stale record's `updated_at` moves after the strip, fields present), or a blinded scan that observed zero rows during an index-rebuild window and self-recorded complete having stripped nothing (a later writer bumps `updated_at` on a record that was never actually touched, fields present). Nothing currently observable on this machine discriminates the two — the root cause is **closed as permanently unresolvable here**, not left open as a to-do. What running `strip_pid_fields_v2` buys is provenance for the *next* cutover, via the output-capture fix above, not a retroactive answer to this one.

**Accepted condition: five pre-cutover worktrees.** `.worktrees/{sdlc-2138,sdlc-2140,sdlc-2144,sdlc-2146,simplify-merge-gate}` each hold a checkout whose `models/agent_session.py` still defines `claude_pid`, and all share `localhost:6379` with the canary machine's live Redis. No such process was running at inspection time, so this is a demonstrated *capability* to re-add stale fields, not an observed cause. The user's decision was to leave them in place. While they exist, a stale-field reappearance on this machine is an **expected outcome**, not evidence of a migration defect, and it pre-excuses any future non-clean `strip_pid_fields_v2` dry run from being read as a regression.

**Severity.** LOW throughout the migration findings — orphaned hash fields are ignored by Popoto on load; nothing in this episode is a crash hazard or a data-loss risk.

**The zero-record guard's accepted condition.** On a machine with a legitimately empty `AgentSession` keyspace — a fresh install, not either machine involved in this canary — the zero-record guard added above makes `strip_pid_fields_v2` exit non-zero on every `/update`, forever, showing a recurring `FAIL:` line. That is expected output on such a machine, not a live regression; see the migration section above for why it is not bounded.

**Live-job results.** The two live canary jobs — a multi-turn steered session (fence persists and re-stamps across turns; steering drain unaffected) and a short SDLC job (lifecycle renders; no owed-communication false positive) — both passed. Their per-job evidence, including the observed pids and `create_time`s across turn boundaries, is recorded in [`docs/plans/durability-m1-fence-canary.md`](../plans/durability-m1-fence-canary.md) and is not restated here; that plan is the record.

**Coverage limits.** Three, stated plainly rather than implied closed:

- **Bridge-side session intake was not exercised.** This canary machine is worker-only, with no bridge activated. Both live jobs were driven by creating `AgentSession` rows directly through `agent.agent_session_queue._push_agent_session` (a disposable `test-`-prefixed `project_key` has no `projects.json` entry, which the `valor-session` CLI's `--project-key` resolution requires), carrying the real project's `working_dir`/`project_config` so execution behaved identically to a real session. Execution and fence behavior are therefore covered; the bridge's own intake path is not.
- **The at-rest owed-communication check was only weakly exercised.** It scans non-terminal sessions once per health-check tick (`AGENT_SESSION_HEALTH_CHECK_INTERVAL`, 300s). The SDLC job finished inside a single tick, so the check had little or no opportunity to evaluate it before the row went terminal. No `[at-rest-owed]` line fired, which is the correct outcome for a session that owes nothing — but it is a weak negative, not a demonstration that the check fires when it should.
- **The SDLC dashboard render path** likewise depends on a bridge-running host.

The fleet rollout to a bridge-running machine happens with these gaps known, and stays a human-gated step.

## Key files

| File | Role |
|------|------|
| `agent/pid_fence.py` | `fence_is_live`, `create_times_match`, `proc_create_time`, `CREATE_TIME_TOLERANCE_S`, and the canonical legacy-row rule — the entire fence. |
| `models/agent_session.py` | Fence fields, `stamp_execution_spawn`, `live_fence`, `find_live_session_by_pid` (fenced ownership resolution). |
| `agent/session_runner/runner.py` | Retained child handle (`_TurnHandle`) — primary liveness; `_on_turn_spawn` stamps the fence. |
| `agent/session_health.py` | Status-keyed recovery, forward-scan orphan reaper, staged-SIGKILL fencing, `_has_progress`/`_tier2_reprieve_signal` fencing, at-rest owed-communication check. |
| `agent/agent_session_queue.py` | `_owned_task_hang_check` fencing. |
| `agent/index_drift.py` | Generalized per-model index-drift reconciliation via forward scan. |
| `scripts/_strip_migration.py` | Shared engine for the three AgentSession field-strip migrations: scan, terminal-only atomic delete+recreate, zero-record guard, `clean_indexes()` sweep, exit-code contract. |
| `scripts/migrate_strip_pid_fields.py` | Terminal-only, ORM-safe, idempotent pid-field strip; thin delegate over `scripts/_strip_migration.py`; output-captured; wired into `/update` as `strip_pid_fields` and `strip_pid_fields_v2`. |
| `scripts/update/run.py` | `_cleanup_stale_sessions` fenced stale-session cleanup. |
| `ui/data/sdlc.py`, `ui/app.py` | `PipelineProgress.pid_create_time`, fence-aware `_check_process_alive`. |
| `tools/check_fence_census.py` | Anti-criterion: function-scoped adjacency check that every fenced-pid consumer is guarded. Enforced by `tests/unit/test_fence_census.py`, not by a CI workflow. |

## See also

- [Agent Session Model](agent-session-model.md) — full field catalog.
- [Agent Session Health Monitor](agent-session-health-monitor.md) — orphan reap and recovery detail.
- [PM Session Liveness](pm-session-liveness.md) — evidence-only liveness detection.
- [Redis Durability](redis-durability.md) — the broader durability posture this milestone feeds.
- [Session Recovery Mechanisms](session-recovery-mechanisms.md) — where the startup dead-worker sweep fits among the other recovery mechanisms.
