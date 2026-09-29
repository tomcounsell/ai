# Run Identity & Lock Ownership

The ownership machinery behind Step 2 (`session-ensure`), Step 4/5a (`next-skill`), and Step 5d.6
(between-stage re-ensure). Read it whenever a `sdlc-tool` call returns a `blocked` payload, or when
a resumed turn has lost its `run_id`.

## The ownership contract

- **Every state-mutating call** (`dispatch record`, `stage-marker`, `verdict record`, `meta-set`)
  passes `--run-id {run_id}`. A missing flag is a named non-zero error (`RUN_ID_REQUIRED`); the
  call never mints or adopts an identity.
- **Pass `--issue-number` to every invocation.** It is the authoritative session selector.
- **`stage-query`, `verdict get`, and `dispatch get` take no `--run-id`.** `next-skill` accepts it
  as a read-only identity assertion for its issue-lock peek; always pass it there.
- **Do not export the session id as an env var**; env vars do not persist across bash blocks.

**Self-heal on resume.** If a resumed turn has lost its `run_id`, a state-mutating write
re-establishes the *same* run's identity and retries once instead of freezing the ledger. A foreign
live lease still hard-refuses. The heal is a safety net; still pass `--run-id`.

**Ledger anchor.** The run's tracking session is a non-executable ledger anchor that permanently
shows `status=running` and carries the run's `_meta` stage state. Never kill it; a running-looking
anchor is not evidence of a rogue pipeline.

## Three-way refusal decision table

`session-ensure` (Step 2 and the 5d.6 re-ensure alike) refuses with exactly three payload shapes.
Discriminate them; do not collapse all three to "stop".

| Refusal | Shape | Action |
|---|---|---|
| **Hand-off** | `{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id", "owner_run_id", "owner_session_id"}` | A designed hand-off that mints nothing. **Pass the self-identity check below first.** If it confirms your own signal, **inherit** `owner_run_id` (carry it as `run_id`, pass it via `--run-id`/`--reuse-run-id`) and continue. |
| **Orphaned lock** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": true}` | The prior owner died before renewing; the lock frees within its TTL. Wait, re-ensure, then **rebind `run_id` to whatever the re-ensure returns**: a post-TTL contest mints a NEW run_id, and a stale one silently orphans every downstream write. |
| **Foreign holder** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": false}` | Apply the self-identity check: if `owner_run_id` is one this run has held, it is your lock; inherit and continue. Only a genuinely foreign `owner_run_id` is the **unconditional stop**: report `reason` and `owner_run_id` and stop; do not loop or route around it. |

## Self-identity check before standing down

The decisive test is `owner_run_id ∈ {run_ids this run has held}`. `SUPERVISED_RUN_ACTIVE` fires
only on a live signal; a live signal carrying a run_id this run never held is a concurrent rival:
stop and report, even though it looks like a hand-off.

A matching `owner_session_id` is necessary but not sufficient: ledger anchors are keyed by issue,
so a second concurrent run on the same issue emits a byte-identical one. Compare `owner_run_id`
explicitly; do not substitute the sibling `run_id` field.

**Recovery after run_id loss** (compaction, restarted supervisor): re-run `session-ensure`. While the
old lock is live it returns `ISSUE_LOCKED`; after its TTL a fresh contest mints a new run_id. If you
still have the run_id, add `--reuse-run-id {run_id}` to recover immediately under the same identity;
the tool verifies the claim.

## `next-skill` lock blocks

`next-skill` checks the issue lock before any G-guard and short-circuits to
`{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock"}`.
Ownership is keyed by `run_id`, never by session or process identity. `orphaned_lock: true` keys on
renewal freshness, not process liveness: the lease payload's `pid` belongs to the short-lived CLI
that acquired it.

The payload may also carry `peek_identity` (`"caller"` | `"session_mirror"` | `"unresolved"`) and,
when `--run-id` did not match the live lock, `session_mirror_run_id`. Both are diagnostics only;
they never override the block. `peek_identity: "unresolved"` means the block is inconclusive: report
it as such and stop.

## Between-stage continuity (Step 5d.6)

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

Never wrap this in a stderr redirect, `|| true`, or anything else that discards the diagnostic; a
run that loses the payload continues under an identity it no longer holds. This is a continuity
proof (the held `run_id` checked against the live lock and the durable run-identity anchor), not a
keepalive; it does not extend the TTL.

- **Adopt the returned `run_id`.** On an unblocked payload, use *that* value for every later stage,
  prompt, and `--run-id`. A lapsed lease is rebound and a fresh contest mints a new identity; a
  stale copy writes markers nobody accepts.
- **Branch on the payload, not the exit code.** `session-ensure` exits 0 on every outcome it can
  report. A non-zero exit is a wrapper or usage error with **no payload**: stop and report. (This
  is per-tool: `stage-marker` does exit non-zero on an ownership refusal.)
- On a `blocked` payload, use the three-way table. Anything else (a broker error, a timeout, a
  payload with neither `run_id` nor `blocked`) is **transient**: surface it, retry, and continue.
  Never turn a transient error into a pipeline abort. Empty stdout is not transient; it is the
  usage error above.
