# Run Identity & Lock Ownership

The ownership machinery behind Step 2 (`session-ensure`), Step 4/5a (`next-skill`), and Step 5d.6
(between-stage continuity re-ensure). Read this whenever a `sdlc-tool` call returns a `blocked`
payload, or when a resumed turn has lost its `run_id`.

## The ownership contract

- **Every state-mutating call** (`dispatch record`, `stage-marker`, `verdict record`, `meta-set`)
  **MUST pass `--run-id {run_id}` explicitly.** A missing flag is a named non-zero error
  (`RUN_ID_REQUIRED`) — the call never mints or adopts an identity.
- **Pass `--issue-number` to every invocation.** It is the authoritative session selector.
- **`stage-query`, `verdict get`, and `dispatch get` accept no `--run-id`** — they have no lock to
  compare against. `next-skill` sits in neither bucket: it *accepts* `--run-id` as a read-only
  identity assertion for its issue-lock peek. Always pass it there.
- **Do NOT export the session id as an env var** — env vars do not persist across bash blocks.

**Self-heal on resume.** State-mutating writes self-heal their run identity: if a resumed turn has
lost the `run_id` from context, the write re-establishes the *same* run's identity and retries
once, instead of refusing and freezing the ledger. A foreign live lease still hard-refuses. Still
pass `--run-id` on the happy path; the heal is a safety net for the resume edge, not a license to
omit it.

**Ledger-anchor rule.** The run's tracking session is a non-executable ledger anchor, not a live
executable session. It permanently shows `status=running` and carries the run's `_meta` stage
state — it must **not** be killed, and a running-looking anchor is not evidence of a rogue
pipeline.

## Three-way refusal decision table

`session-ensure` — the initial one in Step 2 and the between-stage re-ensure in Step 5d.6 alike —
can refuse with exactly three payload shapes. Discriminate them; do not collapse all three to
"stop".

| Refusal | Shape | Action |
|---|---|---|
| **Hand-off** | `{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id", "owner_run_id", "owner_session_id"}` | A **designed hand-off**, not a block. It mints nothing — the supervisor already owns the run. **Pass the self-identity check below first.** If it confirms your own signal: **inherit** `owner_run_id` (carry it forward as `run_id`, pass it back via `--run-id`/`--reuse-run-id`) and **continue** — never stop for a confirmed-own signal. |
| **Orphaned lock** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": true}` | The prior owner died before renewing; the lock frees within its TTL. Wait, re-ensure, then **rebind `run_id` to whatever the re-ensure returns** before continuing — a post-TTL fresh contest mints a NEW run_id, and every downstream `--run-id` call must use the rebound value or it silently orphans. |
| **Foreign holder** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": false}` | Apply the same self-identity check as the hand-off row: `owner_run_id ∈ {run_ids this run has held}` means this is your own lock, not a foreign one — **inherit and continue**, never stop. Only when `owner_run_id` is genuinely foreign is this the **unconditional stop condition**: a live foreign run owns the issue. Stop and report. |

## Self-identity check before standing down

`SUPERVISED_RUN_ACTIVE` fires only on a LIVE signal — a stale one falls through to the
orphaned-lock/foreign-holder rows instead. The **decisive** term is `owner_run_id ∈ {run_ids this
run has held}`: a live signal carrying a run_id this run never held is a genuine concurrent rival
— **stop and report**, even though the payload shape looks like a hand-off.

A matching `owner_session_id` is *necessary-but-not-sufficient*: ledger anchors are keyed by issue
number, not by run, so a second concurrent supervision run on the same issue emits a
byte-identical one. Compare `owner_run_id` explicitly; do not substitute the sibling `run_id`
field for it.

**Recovery after run_id loss** (context compaction, restarted supervisor): re-run `session-ensure`.
While the old lock is live it returns `ISSUE_LOCKED`; the lock frees within its TTL and a fresh
contest then mints a new run_id. **If you still have the run_id**, add `--reuse-run-id {run_id}`
to recover immediately under the same identity — the tool verifies the claim and never adopts an
unverified one.

## ISSUE_LOCKED is not a G-guard

`next-skill` checks the issue-level ownership lock *before* evaluating G1–G9 and short-circuits to
`{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": ..., "owner_session_id": ...,
"orphaned_lock": ...}` when a foreign run holds it. Ownership is keyed by `run_id` (minted only by
`session-ensure`, carried via `--run-id`), never by session or process identity.

`orphaned_lock: true` means the owning run died before its next renewal. The signal is **renewal
freshness, not process liveness** — the lease payload's `pid` belongs to the ephemeral CLI that
acquired it and is dead within seconds, so liveness keys on the renewal stamp instead. The lock
frees itself within the lease TTL.

**Self-owned continue path:** if `owner_run_id` is a `run_id` this run has already held, the lock
is YOURS — continue the stage under it. Only a FOREIGN `owner_run_id` is a hard block: surface the
`reason` and `owner_run_id` to the human and stop; do not loop, do not route around it.

On a `next-skill` block the payload also carries `peek_identity` (`"caller"` | `"session_mirror"` |
`"unresolved"`) and, only when `--run-id` was supplied and did not match the live lock,
`session_mirror_run_id`. Both are **diagnostics only** — they never override the block and nothing
retries on them. `peek_identity: "unresolved"` means the block is *inconclusive*; report it as such
and stop, exactly like any other block.

## Between-stage continuity (Step 5d.6)

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

Never wrap this in a stderr redirect, a trailing `|| true`, or any other form that discards the
diagnostic. The whole point of the step is the payload; a form that destroys it lets the run
continue under an identity it has silently lost.

This is a **continuity proof** — the tool verifies the held `run_id` against the live lock and the
durable run-identity anchor — not a lease keepalive; it does not renew or extend the TTL.

**Adopt the returned `run_id`.** When the payload carries no `blocked` flag, read `run_id` out of
it and use **that** value for every subsequent stage, prompt, and `--run-id` flag. It may differ
from the one you carried in: a lapsed lease is rebound, and a fresh contest mints a new identity.
Keeping a stale copy is precisely how a run ends up writing markers nobody accepts.

**Branch on the payload, not the exit code.** `session-ensure` exits 0 on every outcome it can
report and signals refusal as `{"blocked": true, "reason": ...}` on stdout. A non-zero exit is a
wrapper or usage error, emits **no payload at all**, and is not recoverable: stop and report. The
disposition is per-tool and does not generalize — `stage-marker` genuinely does exit non-zero on an
ownership refusal.

On a `blocked` payload, route it through the three-way table above, plus one extra class:

- **Transient** — a broker error, a timeout, or any payload that is none of the three rows:
  **surface it and retry**, then continue. Never convert a transient error into a pipeline abort;
  halting a healthy run on a broker blip is worse than the bug this step catches. A payload that
  parses but carries neither `run_id` nor `blocked` belongs here too. **Empty stdout is not
  transient** — that is the wrapper/usage error above, and retrying it forever is how a broken
  install turns into an identity-less run.
