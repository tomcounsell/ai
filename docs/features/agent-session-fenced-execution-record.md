# AgentSession Fenced Execution Record

The schema-and-liveness half of the durability Room/Job/AgentSession refactor (plan [`docs/plans/durability-room-job-agentrun.md`](../plans/durability-room-job-agentrun.md)); the Room/Job model lands in later milestones.

## What it is

`AgentSession` records **one fenced execution record** for the process currently running a turn. The record is:

| Field | Type | Meaning |
|-------|------|---------|
| `exec_pid` | plain nullable `IntField` | OS pid of the live harness subprocess. `None` for in-process Task subagents (no OS subprocess). **Deliberately NOT an `IndexedField`.** |
| `pid_create_time` | `FloatField` | psutil `create_time()` of `exec_pid`, captured at spawn — the PID-reuse fence. |
| `exec_cwd` | `Field` | Absolute working dir the spawn ran in (resume is cwd-scoped). |
| `exec_harness` | `Field` | Harness enum string (`"claude"` today; future codex/opencode/pi need no schema change beyond this value). |
| `spawn_history` | `ListField` | Append-only `[{pid, create_time, cwd, harness, generation, agent_id, ts}, …]`. Newest entry is the live fence; a died-resumed-died-again timeline stays reconstructable for the session TTL, not bounded by count. |

Stamped by the runner's `_on_turn_spawn` via `AgentSession.stamp_execution_spawn(...)` **before** the turn-await blocks, so a worker crash mid-turn always leaves a reapable, fenced record. `AgentSession.live_fence` returns the newest `spawn_history` entry (falling back to the denormalized scalars for a partially-written record). A save failure inside `stamp_execution_spawn` logs at WARNING, not DEBUG: this save is the fence's single point of entry, so a silent failure there would degrade every downstream consumer (kill, reprieve, sweep, and ownership sites) to "no fence recorded" without anyone noticing.

### Removed fields

The model does not carry `claude_pid`, `pm_pid`, `harness_pid`, or the write-only `expectations` field, and there is no `notify_sdk_started` callback path. There is no back-compat shim in the model — Popoto ignores unknown hash fields on load, so pre-cutover records hydrate fine, and a one-shot migration (below) reclaims the orphaned hash entries.

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

Rows written before the fence existed carry a pid with no `create_time`, and a live process' `create_time` can also be unreadable (`AccessDenied`, psutil missing). One rule governs all of these cases, written into `agent/pid_fence.py`'s module docstring as the canonical source:

> An unreadable or absent `create_time` on **either** side means "unknown". Unknown never authorizes an irreversible kill, and never authorizes more force than the site already applied before the fence existed. Unknown may authorize an action the site was already taking — including a recoverable SIGTERM on a positive liveness probe.

Concretely, `fence_is_live` returns `False` for unknown, and callers must read that as "cannot claim ownership," not "this process is dead." Grade the action by two questions:

- **Is it recoverable?** Popping a stale handle, sweeping a row to terminal, falling back to a plain liveness probe, and SIGTERM (which a healthy process may trap, drain, and exit cleanly from) all are. SIGKILL is not — the target gets no say. Unknown may authorize the first group; only a positive fence match authorizes SIGKILL.
- **Is it an escalation?** Unknown is a licence to keep doing what the site already did, never to do more. A site that reached for SIGTERM before the fence existed may still reach for it on unknown; a site that did not must not start.

This matches upstream psutil practice: treat an unfetchable `create_time` as "unknown → fall back," never as "assume valid."

Applying the rule at each site: `_apply_recovery_transition`'s snapshot guard has no `_snap_ct is not None` clause, so unknown yields no pid to signal at all. The in-process orphan reap keeps the same legacy fallback `_sweep_dead_worker_sessions` has — a legacy row with no recorded `create_time` gets a SIGTERM on a bare liveness probe, but never earns SIGKILL escalation, because escalation requires a positive fence match. A *recycled* row (`create_time` recorded and mismatched) is not unknown at all — it is a positive "not ours" and gets no signal whatsoever.

## Consumer census

`fence_is_live`/`create_times_match` is applied at every site that reads a fenced pid. The two HIGH-severity sites fail in **opposite directions** when left unguarded:

| Site | Direction | What an unfenced/recycled pid does | What fencing it does |
|------|-----------|--------------------------------------------------|------------------------|
| `_tier2_reprieve_signal` (`agent/session_health.py:1953`) | Under-kills | An unfenced/recycled `exec_pid` probing as `"progressing"` grants a reprieve every tick — `pid is not None` at the trailing predicate is permanently true because the fence is never cleared, so it discriminates nothing on its own. A dead session is held alive indefinitely. | Nulling the pid yields `"unknown"`, which routes to the count-based escalation guard and can return `None`. Strictly kill-increasing. |
| `_has_progress` → `subprocess_hang_verdict` (`agent/session_health.py:1740`) | Over-kills | An unrelated process occupying a recycled `exec_pid` that probes as `"hung"` bypasses the sticky-field honor and prematurely releases a session with real progress to Tier-2 recovery. | Nulling the pid yields `"unknown"`, which `if _verdict != "hung":` treats identically to `"progressing"` — sticky fields still honored. Strictly kill-reducing. |

Both HIGH sites are guarded, along with the four LOWER-severity ones (`_pending_sigkill` staging/drain, `find_live_session_by_pid`'s ownership scan, `_owned_task_hang_check`, and the ownership lookups feeding the fast oneshot reaper and the forward orphan scan). The full list of guarded consumers (`tools/check_fence_census.py --list`):

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

A naive anti-criterion would be a threshold: `grep -c 'fence_is_live' agent/session_health.py | output > 11`. A count is satisfied by *any* fenced site — it cannot tell "the census grew" from "one guarded site was deleted and one unguarded one was added." A change that adds one unguarded consumer while removing one guarded site leaves the count unchanged and green.

`tools/check_fence_census.py` instead checks **per-site, function-scoped adjacency**: for every function body (and the module top level), it finds every read of `.get("pid")` off an expression rooted in `live_fence` — directly or through a local name bound to one — and requires that the *same function body* either calls `fence_is_live` / `create_times_match`, or reads `.get("create_time")` off that same fence (keeping both halves is the alternative to guarding directly; a function that forwards `create_time` onward to a real decision site, like `_session_to_pipeline` handing both values to `_check_process_alive`, is not in the defect class the checker guards against — the decision site is checked independently). Nested function/class scopes are checked as their own units, so a guard inside a closure does not vouch for a read in the enclosing function. Any site that satisfies neither is reported by exact `file:line`.

**How it is enforced.** There is no GitHub Actions job for it — `tests/unit/test_fence_census.py::test_repo_root_exits_zero` runs the checker against the repo root and asserts exit 0, so the census is enforced on every suite run, the same way `tests/unit/test_architectural_constraints.py` enforces import boundaries. The checker is also runnable by hand (`python tools/check_fence_census.py --list`) and is cwd-independent (`--root` defaults to its own repo, not the process cwd). Its red state is proved, not assumed: `tests/fixtures/fence_census_violator/` is a fixture tree the checker must exit 1 on, naming both violating functions with `file:line`.

Two sites read a fenced pid and drive nothing — `agent/session_health.py:3377` and `:3396`, both interpolating a pid into a `finalize_session` reason string or a `logger.warning` call. These carry the marker `# fence-census: log-only, not a decision consumer`, the sole exemption mechanism the checker honors. Any new exemption is a deliberate, reviewable annotation at the call site, not a silently rising threshold.

## Reverse pid lookup: forward scan over the status index

"Which live session owns OS pid X?" is resolved by `AgentSession.find_live_session_by_pid(pid, create_time=None)` — a **bounded forward scan** over the low-cardinality `status` index, not an indexed reverse lookup. Indexing a pid creates one Redis Set per distinct pid value (unbounded cardinality — the pid-index anti-pattern, and a contributor to the phantom-key floods).

The scan builds an in-process `{live_pid: session}` map from each non-terminal row's live fence and returns the owner by membership. Bounded — dozens of live rows, each reached via an indexed `status` lookup. Callers that need a hard time budget (the fast orphan reaper) wrap this in a thread + timeout and fail toward reapable; that contract lives at the call site.

`create_time` is the psutil-observed `create_time` the *caller* already read for `pid`. Matching is a strict **refinement** of the pid-only match, never a broadening:

- both sides have a `create_time` → they must agree within `CREATE_TIME_TOLERANCE_S`; a mismatch means the pid was recycled and this row does NOT own it;
- either side recorded nothing → fall back to a pid-only match, so legacy rows still resolve.

A fence-verified match always wins over a pid-only one, and a multi-match (more than one row claiming the same pid at the same confidence level) logs a WARNING naming every candidate instead of silently resolving to whichever the scan happened to visit first. The per-status query's blanket `except Exception` is narrowed to the specific lookup errors (`QueryException`, `ModelException`, `RedisError`, `AttributeError`, `TypeError`, `ValueError`); a poisoned cohort logs the WARNING and the scan continues to the remaining statuses rather than unprotecting every row in that status silently. The scan routes through `_filter_hydrated_sessions`, per that helper's stated contract.

`agent/index_drift.py` holds the at-rest index-drift safety check as a per-model spec (`ModelDriftSpec` / `reconcile_model_index`) driven by a forward scan, with `claude_pid` absent from the enumerated `AgentSession` `IndexedField` set.

## `_pending_sigkill` is fenced, not bare pids

The staged-SIGKILL escalation drain (`agent/session_health.py`) stages fenced `(pid, create_time)` tuples, never bare pids: an orphan gets a SIGTERM on one tick, and if it hasn't cleaned itself up by the next tick (300s later), the staged pid is SIGKILLed. Elapsed time is never a defence — the tick interval is 300s, the same order as the macOS pid-recycle window.

`_pending_sigkill` is `set[tuple[int, float | None]]`, staging `(pid, create_time)` alongside the reap that scheduled it, mirroring `_pending_sigkill_orphans` one screen away. The drain re-verifies with `fence_is_live` before signalling: a staged entry whose `create_time` no longer matches has been recycled to an unrelated process and is dropped unsignalled; a legacy entry staged with no recorded `create_time` is likewise dropped, per the legacy-row rule. Only a fence-matched reap stages the escalation in the first place — the legacy liveness-probe branch in the orphan reap (see above) sends SIGTERM but never stages SIGKILL, because it cannot prove the pid is still ours.

## `_has_progress` vs. `_tier2_reprieve_signal`: opposite failure directions

These two functions are the two HIGH-severity sites from the consumer census, and fencing does the opposite thing at each of them.

**`_has_progress` over-kills if unfenced.** A recycled `exec_pid` probing as `"hung"` bypasses the sticky-field honor (`turn_count`, `log_path`, `claude_session_uuid`) and prematurely releases a session with real progress to Tier-2 recovery. Fencing this site is strictly **kill-reducing**: nulling a non-matching pid moves the verdict to `"unknown"`, and `if _verdict != "hung":` treats `"unknown"` exactly like `"progressing"` — both honor the sticky fields. Because the guard can only ever *withhold* a hang verdict, never produce one, it is enforced unconditionally. A regression test pins the direction: patch `agent.pid_fence.proc_create_time` so a recycled pid would probe as hung, and assert `_has_progress` still returns `True` when the sticky fields show real progress.

**`_tier2_reprieve_signal` under-kills unfenced.** A recycled `exec_pid` probing as `"progressing"` returns a reprieve gate every tick, and the trailing `return "alive" if pid is not None else None` predicate is permanently true because the fence is never cleared between turns — on its own it discriminates nothing. A dead session with a recycled pid is held alive indefinitely. Fencing this site is strictly **kill-increasing**: withdrawing a reprieve that would otherwise be granted risks killing a session that was relying on it, which is a visible, user-facing failure if that session turns out to be legitimately alive by some other measure.

Both sites are fenced, but the guard is written differently at each, and the difference is the point:

- **`_has_progress` nulls any pid the fence cannot claim**, including a pre-fence row that never recorded a `create_time`. Unknown is safe there because it can only withhold a hang verdict.
- **`_tier2_reprieve_signal` nulls a pid only on a POSITIVE "not ours"** — a `create_time` was recorded and no longer verifies, either because the pid was recycled to an unrelated process or because the live reading fails (the process is gone, `AccessDenied`, psutil missing). A row that never recorded a `create_time` is *unknown*, not "not ours", and keeps its reprieve until it ages out with session TTL. That legacy fallback is spelled out at the call site rather than inherited from `fence_is_live`, which returns the same `False` for both conditions, because the canonical legacy-row rule in `agent/pid_fence.py` forbids unknown authorizing more force than a kill-increasing site already applied.

When the pid is nulled at `_tier2_reprieve_signal`, `subprocess_hang_verdict` returns `"unknown"`, so the count-based escalation guard (`reprieve_count >= MAX_NO_OUTPUT_REPRIEVES`) and the trailing `pid is None` return both regain their authority. That trailing predicate is fence-driven, not a "did this session ever spawn" test: on its own `pid is not None` discriminates nothing, because the fence is deliberately never cleared on exit.

Enforcement is unconditional. There is no config flag, no env var, and no `if ENFORCE_FENCE:` branch — a toggle would rot into a permanent fork in the logic, and `tests/unit/test_session_health_reprieve_fence.py` greps the live module to keep one out.

The same fence-then-null pattern (`pid, ct = fence.get("pid"), fence.get("create_time"); if pid is not None and not fence_is_live(pid, ct): pid = None`) is also applied at `_owned_task_hang_check` (`agent/agent_session_queue.py`), which drives the per-tool hang probe the same way `_has_progress` does, and is kill-reducing for the same reason.

## `/update` cutover: fenced stale-session cleanup

`scripts/update/run.py::_cleanup_stale_sessions` finalizes `running` sessions with no live process during every `/update` run. The fence is consulted first, ahead of recency: a session whose fence resolves live (`fence_is_live` on the recorded `(exec_pid, pid_create_time)`) is skipped **at any age**, and that skip is counted separately from a recency-based skip so an operator rolling `/update` across the fleet can see the fence gate acting rather than inferring it from a total. `_cleanup_stale_sessions` returns `(killed_count, skipped_recent, skipped_fence_live)`, and the run summary logs both skip counts on separate lines. Recency stays as the fallback for rows with no fence, and `created_at` age stays as the last resort for rows with neither a fence nor an `updated_at`. The reason string names which signal actually decided the finalization — `"stale cleanup (fence verified: recorded process not live)"` when the fence positively resolved the process as gone, versus `"stale cleanup (stale heartbeat, liveness unverified)"` when only recency lapsed and nothing checked the process itself.

`HEARTBEAT_WRITE_INTERVAL` is 60s on an independent asyncio loop, well inside the 30-minute recency window, so a genuinely live session is protected by recency; the fence check makes the cleanup's own claims accurate and makes an already-live session's protection independent of the heartbeat writer as well.

## Dashboard: fence-aware liveness probe

The dashboard's session modal and row-level ghost badge use a fence-aware probe, not a bare `os.kill(pid, 0)` check (which returns success for whatever process holds a recycled pid, not necessarily the one the session spawned).

`PipelineProgress` (`ui/data/sdlc.py`) carries a `pid_create_time: float | None` field alongside the existing `exec_pid`, threaded through `_session_to_pipeline` (which reads the fence dict) and into the dashboard JSON via `ui/app.py`. `_check_process_alive(pid, create_time=None)` returns a genuine three-state verdict:

- `True` — the recorded process is alive and confirmed to be the same process (`fence_is_live` matched).
- `False` — not live. Either the pid is gone entirely (ghost), or the pid is alive under a *different* `create_time` — recycled, belongs to something else.
- `None` (unknown) — `pid` is `None`/non-positive; or the pid exists but no `create_time` was ever recorded for it (legacy row — nothing to compare against); or the pid's identity itself could not be read (`PermissionError`/`OSError`/psutil unavailable).

A recycled pid renders as **not-live** (`False`), not as a green "alive" chip — the case a bare `os.kill(pid, 0)` probe cannot distinguish. A legacy row with no recorded `create_time` whose pid is still in the process table renders as **unknown**, not alive: the dashboard cannot vouch for it, so it says so rather than guessing. The ghost badge's CSS comment (`ui/static/style.css`) describes the fence rather than raw-probe semantics.

## Migration

`scripts/migrate_strip_pid_fields.py` reclaims the orphaned `claude_pid` / `pm_pid` / `harness_pid` / `expectations` hash entries from existing records:

- **ORM-safe** — no raw `hdel`/`hset`. For each stale record it queues `instance.delete()` + `Model.save(instance)` on ONE transactional Redis pipeline (MULTI/EXEC), atomically rewriting the record with only current model fields; a crash mid-migration can never lose a record.
- **Terminal-only, but terminal rows are not quiescent.** Only records whose `status` is in `TERMINAL_STATUSES` are rewritten. Terminal does not mean nobody writes them. `agent.session_health.cleanup_corrupted_agent_sessions` sweeps every hydrated record, terminal ones included, and `/update`, worker startup, and the `agent-session-cleanup` reflection all invoke it — that sweep writes no field value: it classifies with `is_valid()` plus a targeted `EXPIRE` keepalive, so it does not restamp `updated_at` on rows it is only inspecting. Terminal rows stay non-quiescent anyway, because other writers still reach them. The safety property is therefore atomicity, not quiescence: the delete+recreate is one transactional pipeline, so a crash or an interleaved writer can never lose a record. A concurrent write landing between the read and the pipeline is lost, which is why the scope stays terminal-only — those rows carry no in-flight state worth racing for.
- **Idempotent** — a re-run finds zero stale records and no-ops.
- **Deferred rows do not age out via TTL.** Every popoto `save()` re-issues `EXPIRE` with `Meta.ttl`, so the 30-day backstop only fires on a record nothing writes for 30 days. `is_ledger=True` rows are written on every stage dispatch while their pipeline is open, holding a perpetually-refreshed TTL; that run-lock bind is a two-field partial save, which is enough because popoto re-issues `EXPIRE` on the `update_fields` path too (`base.py:1186-1188`). The cleanup pass holds every healthy row's TTL at the ceiling as well, deliberately and without a field write, via `AgentSession.refresh_ttl()`. A deferred row keeps its stale fields until a later run of the migration finds it terminal.
- **Detection fails closed.** A failed `HKEYS` read propagates rather than returning an empty set, so the record is counted in `errors` and the run exits 1 without being recorded complete. Counting it as clean would make a detection failure indistinguishable from a genuinely clean record, and a transient Redis blip would manufacture the exact "proof of cleanliness" artifact the re-runs exist to produce.

**Output capture.** All three strip scripts call `logging.basicConfig` with `stream=sys.stdout`, which is load-bearing: Python's default `StreamHandler` writes to **stderr**, and a stdout-only capture would then record an empty string while looking healthy. Every subprocess-shaped migration helper in `scripts/update/migrations.py` runs through `_run_migration_script` **except `_migrate_purge_phantom_agent_sessions`**, which shells out on its own because it needs an exit-3 branch and its own time budget, and is therefore the one helper that discards its output on the success path and reports a stderr-only failure reason. The shared runner logs **both** captured streams line-by-line at INFO, prefixed `[migration:<label>]`, on the success path as well as on failure, and returns an error string carrying both tails. Logging on the success path is the point: a migration whose output is discarded has a failure mode indistinguishable from its success mode. The `MIGRATIONS: dict[str, tuple[callable, str]]` contract stays `str | None` — output goes to the logger rather than through the return value, so capturing it everywhere needs no contract widening.

**Shared strip engine.** `scripts/_strip_migration.py` holds the single copy of the scan, the terminal-only atomic delete+recreate, the zero-record guard, and the `clean_indexes()` sweep for all three "strip removed hash fields" migrations: `migrate_strip_pid_fields.py` (this one), `migrate_strip_pty_fields.py`, and `migrate_schema_diet_fields.py`. One engine keeps the guard, the sweep and the exit codes from drifting apart across three copies. Each script is a thin delegate keeping only its docstring and its own `STALE_FIELDS` set. `strip_pty_session_fields_v2` and `schema_diet_fields_v2` register the pty and schema-diet scripts under new `MIGRATIONS` names so every machine re-runs them once now that they carry the guard — the same rename-to-rerun mechanism `strip_pid_fields_v2` uses below.

**Zero-record fork.** When `total_records == 0` the engine runs a bounded, detection-only `SCAN` for raw `AgentSession:*` hashes and branches on the answer. Hashes present means the scan was **blinded** by the class-set window — `AgentSession.query.all()` reads `$Class:AgentSession`, which popoto's index rebuild deletes and re-adds in batches, returning 0 rows with no exception — so the engine exits 2 (distinct from the 1 used for per-record errors) and does not report success. An *exhaustive* SCAN returning zero means the keyspace is genuinely empty, so there is nothing to strip and the migration exits 0 and is recorded complete. A truncated SCAN or one that raises fails closed to exit 2, because neither proves emptiness.

The window is measured, not theoretical: driving the rebuild against a concurrent poller, 96.5% of scans saw zero at 150 rows (0.24s rebuild), 99.8% at 1000 rows (1.17s), and 91.8% at 4006 rows (**22.33s**). The rebuild deletes the class set outright and only re-adds members at each `batch_size=1000` flush, so the window is essentially the whole rebuild and grows with the keyspace. Without the guard, a run landing inside it exits 0 and is recorded permanently complete having stripped nothing.

`run_pending_migrations` only records a migration complete when its helper returns `None`, so a non-zero exit means the next `/update` retries automatically. Six registrations route through this engine (`strip_pty_session_fields`, `schema_diet_fields`, `strip_pid_fields`, and all three `_v2` names). **Read a recurring `FAIL:` line from any of them as a real signal**: a fresh install passes on the first `/update`, so a repeated failure means a genuinely blinded scan or a keyspace holding only phantom `AgentSession:*` bookkeeping hashes.

**Index sweep is `clean_indexes()`, never a rebuild.** After an apply run that stripped records, the migration calls `AgentSession.clean_indexes()` — the production-safe orphan-reference cleanup. This is a defensive sweep, not a functional requirement: the per-record `delete()` + `save()` pair already maintains indexes atomically on its own pipeline.

Neither the raw `rebuild_indexes()` nor the `repair_indexes()` wrapper around it is used here, and reaching for either is a regression. A rebuild tears down and re-adds every index, which does two unacceptable things in this context:

- it opens the **class-set window** — the rebuild deletes and re-adds `$Class:AgentSession` in batches, and `query.all()` landing inside that window returns 0 rows with no exception, which is the exact ambiguous observation the zero-record guard exists to fail closed on. A migration that triggers the window it is guarding against is self-defeating;
- it **fails outright** on pre-existing phantom index metadata with `unpack(b) received extra data` — to be investigated, not blind-purged.

The raw-rebuild identifier must not appear in `scripts/_strip_migration.py` or in any of the three delegate scripts, not even in a comment — so the engine's own comment describes the excluded paths without naming them. Two tests enforce it: `tests/unit/test_strip_migration_shared.py` covers the delegates and `tests/unit/test_migrate_strip_pid_fields.py` covers the engine.

**Re-run mechanism.** `run_pending_migrations` skips by name, so re-running a script requires a new registration rather than hand-editing `data/migrations_completed.json` (unauditable, and impossible on a machine nobody is sitting at). `strip_pid_fields_v2` registers the *same* script under a new name in `MIGRATIONS`, positioned after `strip_pid_fields` and before `purge_phantom_agent_sessions`. Every machine runs it exactly once on its next `/update`; the script's own idempotency makes a genuinely clean machine a fast no-op. Its value is provenance for future cutovers.

Cutover is **per-machine**: Redis is localhost on each machine, so there is no shared-state fleet hazard — each machine strips its own records when `/update` runs there.

## Recovery is keyed on session STATUS, not pid-absence

Interrupted-session recovery keys on `AgentSession.status` (the non-terminal statuses), not on whether a pid field is present or a process is alive. Pid-absence is not a recovery trigger; a session in a live status with a dead fence is recovered because its *status* says it should be running, and the fence tells the reaper the process is gone. This closes the silent-loss paths where a dead process left no status change for recovery to key on.

## At-rest owed-communication health check

`agent/session_health.py` runs an at-rest authorship check: a session with **no live fenced execution** (nothing running) that authored a reply but shows an activity anchor lagging the authorship anchor past a tolerance is flagged as owing communication. The activity anchor is the max of `last_stdout_at` / `last_tool_use_at` / `last_turn_at`, with a `session_events` authorship-scan fallback; the tolerance is a named, env-overridable constant seeded from a reference incident (activity-after-authorship gap of ~507s) and padded past the liveness-writer cooldown so a fresh activity write racing an authorship write does not false-positive. This detects death-after-authoring that leaves a reply un-delivered, independent of the pid fence.

## Key files

| File | Role |
|------|------|
| `agent/pid_fence.py` | `fence_is_live`, `create_times_match`, `proc_create_time`, `CREATE_TIME_TOLERANCE_S`, and the canonical legacy-row rule — the entire fence. |
| `models/agent_session.py` | Fence fields, `stamp_execution_spawn`, `live_fence`, `find_live_session_by_pid` (fenced ownership resolution). |
| `agent/session_runner/runner.py` | Retained child handle (`_TurnHandle`) — primary liveness; `_on_turn_spawn` stamps the fence. |
| `agent/session_health.py` | Status-keyed recovery, forward-scan orphan reaper, staged-SIGKILL fencing, `_has_progress`/`_tier2_reprieve_signal` fencing, at-rest owed-communication check. |
| `agent/agent_session_queue.py` | `_owned_task_hang_check` fencing. |
| `agent/index_drift.py` | Per-model index-drift reconciliation via forward scan. |
| `scripts/_strip_migration.py` | Shared engine for the three AgentSession field-strip migrations: scan, terminal-only atomic delete+recreate, zero-record guard, `clean_indexes()` sweep, exit-code contract. |
| `scripts/migrate_strip_pid_fields.py` | Terminal-only, ORM-safe, idempotent pid-field strip; thin delegate over `scripts/_strip_migration.py`; output-captured; wired into `/update` as `strip_pid_fields` and `strip_pid_fields_v2`. |
| `scripts/update/run.py` | `_cleanup_stale_sessions` fenced stale-session cleanup. |
| `ui/data/sdlc.py`, `ui/app.py` | `PipelineProgress.pid_create_time`, fence-aware `_check_process_alive`. |
| `tools/check_fence_census.py` | Anti-criterion: function-scoped adjacency check that every fenced-pid consumer is guarded. Enforced by `tests/unit/test_fence_census.py`, not by a CI workflow. |

## See also

- [Agent Session Model](agent-session-model.md) — full field catalog.
- [Agent Session Health Monitor](agent-session-health-monitor.md) — orphan reap and recovery detail.
- [AgentSession Liveness Field Authorship](agent-session-liveness-authorship.md) — who is authorized to write `updated_at` and the other liveness fields, and the `/update` reaper's full liveness ladder, of which this fence is the top rung.
- [PM Session Liveness](pm-session-liveness.md) — evidence-only liveness detection.
- [Redis Durability](redis-durability.md) — the broader durability posture this record feeds.
- [Session Recovery Mechanisms](session-recovery-mechanisms.md) — where the startup dead-worker sweep fits among the other recovery mechanisms.
