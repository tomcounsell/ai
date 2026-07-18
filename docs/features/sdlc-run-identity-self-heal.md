# SDLC Run Identity Self-Heal (issue #2144)

**Status:** Shipped

State-mutating `sdlc-tool` writes (`stage-marker`, `verdict record`,
`meta-set`, `dispatch record`) **self-heal** their run identity when it is
absent after a resume, re-establishing the *same* run's `run_id` and retrying
the write once — instead of silently no-op'ing and freezing the ledger.

## The bug

Every state-mutating `sdlc-tool` subcommand requires a **run identity**: a
`run_id` minted exactly once by `sdlc-tool session-ensure`, pinned on a
TTL-bounded Redis issue lock (`session:issuelock:{issue}`), mirrored to
`AgentSession.active_run_id`, and passed back via `--run-id` on every write. A
missing flag is a hard, named refusal `RUN_ID_REQUIRED`; an expired or foreign
lease surfaces `LEASE_ABSENT` / `ISSUE_LOCKED`. These refusals are intentionally
loud at the tool boundary (issues #2003 / #2012 — no session to fall back to).

But the `run_id` lives only in the driving turn's conversation context. When a
PM turn is killed mid-pipeline (worker restart — #2141/#2143) and the session
resumes from transcript, the resumed turn continues pipeline work **without**
the `run_id`. Compounding this, the SDLC skill convention wraps every
best-effort marker write `2>/dev/null || true`, so the refusal is **doubly
silent**: stderr is discarded and the non-zero exit is swallowed. The ledger
freezes (`BUILD: ready` / `TEST: pending`) while real work — commits, PRs, CI —
proceeds. Observed live on issue #2133: the ledger stayed frozen for ~2 hours
of wall-clock while the fix was committed and the PR opened green.

The confirmed root cause: **marker writes work fine once run identity is
re-established; the silent-failure window is exactly the resumed turns that
continue pipeline work without re-ensuring.** All week the manual workaround
was `sdlc-tool session-ensure --reuse-run-id <id>` after lease expiry — humans
doing by hand what the tool should self-heal.

## The fix

The healing lives in the **tool**, not in skill prose, so it is deterministic
and cannot be forgotten by a resumed LLM turn that does not know it was
resumed. Two wiring points in each of the four state-mutating CLIs, both
backed by one shared helper (`tools/_sdlc_run_identity.py`):

### Front-gate heal (missing `--run-id`)

The old unconditional `RUN_ID_REQUIRED` hard-exit is replaced: when a write
carries no `--run-id` but has a resolvable `--issue-number`, call
`heal_missing_run_id(issue_number, subcommand)`. If it returns a `run_id`, the
write proceeds under it (exit 0, so `|| true` is a no-op and the write lands).
Only a genuinely unhealable state (foreign live lease, or no issue-number to
key on) still emits `RUN_ID_REQUIRED`.

### Post-write heal (stale `--run-id` → `LEASE_ABSENT`)

When a write refuses for a run-identity reason (`LEASE_ABSENT`, or a stale
`ISSUE_LOCKED` echo) after running under a stale `--run-id`, call
`maybe_heal_after_write(...)`; on a healed `run_id`, retry the write **exactly
once** under it. `sdlc_verdict` routes this through its `OwnershipError`
handler; the other three inspect the result dict directly.

### `reestablish_run_id` — how identity is re-established

`reestablish_run_id(issue_number, prior_run_id)` re-establishes the *same*
run's identity via the already-built primitives, in precedence order:

1. **Supervised-inherit first, directly.** If a live supervised-run signal
   exists for the issue, return its `run_id` (do NOT route it through
   `ensure_session(reuse_run_id=…)` — the inherit branch there is gated
   `if not reuse_run_id`, so passing a reuse id would bypass inheritance and
   either refuse or mint a competitor).
2. **Else reuse an environment-corroborated candidate:** the supervised signal
   / worktree `.sdlc-run` file → `AgentSession.active_run_id` (the record
   mirror `_resume_active` never touches — the only carrier that survives a
   resume for a bridge-originated PM pipeline, the #2133 shape) → the
   caller-supplied `prior_run_id` (possibly stale, lowest precedence).
3. `ensure_session(reuse_run_id=candidate)` re-acquires on a free/expired lock
   (verified reuse) or mints fresh on a genuinely free lease.

Returns the healed `run_id`, or `None` when identity cannot be safely
re-established (foreign live lease, no issue-number, a terminally-done pipeline,
or any error).

## Invariants (load-bearing)

- **No-adopt.** Self-heal never adopts a *foreign* live lease. A foreign live
  holder yields `ISSUE_LOCKED` → `ensure_session` returns no `run_id` →
  `reestablish_run_id` returns `None` → the refusal stands. Healing only ever
  reuses/echoes an environment-corroborated candidate, or inherits a genuinely
  live supervisor's id.
- **Minting exclusivity.** `session-ensure` remains the exclusive minting site.
  The helper performs no writes of its own beyond what `ensure_session` does
  under its lock contract (plus a terminal-guard `release_issue_lock` cleanup).
- **At-most-once retry / no loop.** Each CLI retries the write exactly once
  under the healed id and never re-enters the heal path (a front-gate heal sets
  `healed_at_gate`, which gates out the post-write heal). `maybe_heal_after_write`
  returns the healed id **even when it equals the prior id** — that equal-id
  case means the SAME run's lapsed lease was re-acquired (the lock is held
  again), which is the stale-`--run-id` + lapsed-lease resume, the bug's most
  common real manifestation (the `session-ensure --reuse-run-id` pattern). The
  single-retry structure guarantees a same-id return cannot loop.
- **Terminal guard.** A *fresh* mint (candidate not corroborated) on a
  `MERGE == completed` pipeline would resurrect a finished run's lease — so it
  is declined (release the just-acquired lock, return `None`). It cannot
  misfire on the normal reuse path, where the healed id equals the candidate.
- **Best-effort / fail-open.** Every helper path swallows exceptions to `None`
  and never raises into the calling tool. A healed write exits 0; a genuinely
  unhealable refusal keeps the loud named error.

## Visibility sink

Because both stderr and the exit code are swallowed by the skill convention,
every self-heal attempt (healed or not) is recorded to a durable sink so a
frozen ledger becomes diagnosable and a healed one shows the recovery:

- **`logs/sdlc_run_identity.log`** — one JSON line per event
  (`{ts, issue, subcommand, reason, healed, old_run_id, new_run_id}`), written
  at the **git-common-dir root** so all worktrees converge on one
  operator-tailable file (a worktree-relative `logs/` would be invisible).
- **`sdlc:run_identity:refusals:{issue}`** — a raw-Redis rolling
  counter/last-event hash (attempts / healed / unhealed + last event fields).
  This is a **new, non-Popoto observability key** written via the same raw-Redis
  idiom as the issue lock; it is deliberately outside the gated ledger path
  (writing to the Popoto-managed `PipelineLedger` would itself need identity —
  chicken-and-egg). Both sinks are fail-open.

> **Known follow-up:** the `sdlc:run_identity:refusals:{issue}` key currently
> has no TTL, so one key per issue accumulates indefinitely. Volume is low
> (only on the resume edge), but a bounded `expire` (a few days) would keep the
> keyspace self-cleaning — tracked as an open question in the plan.

## Frozen-ledger repair (decision)

There is **no** artifact-inference `--reconcile`. The SDLC invariant — "never
infer stage completion from artifacts; completion is exclusively determined by
stored state" — is load-bearing (`.claude/skills/sdlc/SKILL.md`). Instead:

- **Forward repair is automatic:** with self-heal, the next state-mutating
  write after a resume re-establishes identity and lands, so the ledger
  self-repairs going forward without inference.
- **Back-fill of the freeze window is a bounded manual operation** using the
  now-self-healing `stage-marker` / `verdict record` writes — an operator (or
  the resumed pipeline itself) issues the missed completion markers explicitly.
  Every ledger mutation stays grounded in an explicit, authorized `run_id`
  write, never in inference.

## Files

- `tools/_sdlc_run_identity.py` — `reestablish_run_id`, `heal_run_identity`,
  `heal_missing_run_id`, `maybe_heal_after_write`, `classify_refusal`, the
  visibility sink (`log_run_identity_event`, `_record_refusal_redis`,
  `_log_path`), and the terminal guard (`_pipeline_is_terminal`).
- `tools/sdlc_stage_marker.py`, `tools/sdlc_verdict.py`,
  `tools/sdlc_meta_set.py`, `tools/sdlc_dispatch.py` — the front-gate +
  post-write wiring.
- `tests/unit/test_sdlc_run_identity.py` — helper + CLI-level self-heal unit
  coverage.
- `tests/integration/test_sdlc_run_identity_resume.py` — end-to-end resume:
  mint identity, lapse the lease + signal, then land a marker with no manual
  `session-ensure` (no `--run-id`, and the stale-`--run-id` variant).

## See also

- [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md) — the `run_id`
  issue lock this rides.
- [SDLC Issue-Keyed Stage Ledger](sdlc-issue-keyed-stage-ledger.md) — the
  durable ledger the markers write into.
- [SDLC Pipeline State](sdlc-pipeline-state.md) — `session-ensure` and the
  run-identity model.
