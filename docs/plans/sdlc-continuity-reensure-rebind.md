---
status: Needs Revision
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2675
last_comment_id: 5277292773
---

# Make the between-stage continuity re-ensure rebind, and make marker refusals loud

## Problem

Issue #2675 reports that `/do-sdlc` Step 3d.6's between-stage continuity
re-ensure "mints a NEW run_id after the lease lapsed, recreating the ledger
anchor and wiping previously recorded stage markers."

**The forensics refute the second half of that sentence, and the correction is
the whole plan.** The `PipelineLedger` is keyed on `(target_repo, issue_number)`
(`agent/pipeline_ledger.py:122-134`, `:171-175`). Nothing in the run_id mint path
touches it. A fresh run_id cannot "recreate the ledger anchor" because the anchor
does not depend on run_id at all — that was the entire point of issue #2012.

What actually happens is worse, because it is invisible:

1. The lease lapses. A re-ensure mints a **fresh** run_id and wins the now-free
   lock (mechanism confirmed below).
2. Every subsequent `sdlc-tool stage-marker` call that still carries the **old**
   run_id is **refused** — `LEASE_ABSENT` or `ISSUE_LOCKED` — at
   `tools/sdlc_stage_marker.py:534-556`. The refusal returns exit code 1 and
   prints a diagnostic to stderr.
3. `.claude/skills-global/do-sdlc/SKILL.md:219` and the marker invocations in
   `docs/sdlc/do-plan.md:12-13` end in **`2>/dev/null || true`**. The exit code is
   discarded and the diagnostic is routed to `/dev/null`. The supervisor sees
   nothing.
4. Markers are therefore never written. Writers that *do* carry the winning
   identity (the verdict recorder, invoked by the critique subagent) succeed.

The end state is a ledger holding **only** whatever the winning identity wrote.
That is observationally identical to a wipe, which is why #2675 was filed as one.

### Live evidence — issue #2735, current batch

`sdlc-tool stage-query --issue-number 2735` returns a `stages` blob containing
exactly one key:

```json
{"_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD",
  "recorded_at": "2026-08-13T09:34:31.074015+00:00",
  "artifact_hash": "sha256:2bde9098..."}}}
```

201 bytes. No `ISSUE`/`PLAN`/`CRITIQUE` stage keys, no `_sdlc_dispatches`. For
contrast, `tomcounsell/ai:2740` (5602 bytes) and `tomcounsell/ai:2744` (2576
bytes) each carry all nine stage keys plus `_sdlc_dispatches`, `_stage_skips`,
`_patch_cycle_count`, `_critique_cycle_count`, `_verdicts`. Same batch, same day.

**This shape cannot be produced by a wipe-then-repopulate.** `_save()`
(`agent/pipeline_state.py:520-555`) unconditionally backfills every member of
`ALL_STAGES` to `"pending"` before serializing (`:524-527`), so *any* successful
marker write leaves all nine stage keys behind. Their total absence proves no
marker write ever landed on this ledger — not that one landed and was later
erased.

### The refusals were never even counted

`tools/_sdlc_marker_telemetry.py` maintains per-`(issue, run_id)` ok/fail
counters with a 24-hour TTL that is refreshed on every increment (`:38-44`,
`:96-106`). Issue #2735 ran today, so the counters are live. For its only owned
run_id:

```
sdlc-tool run-health --issue-number 2735 --run-id c4a2eb9025714156a5e644f7a3e32cd6 --verbose
{"ok_writes": 0, "fail_writes": 0, "last_failed_stage": null,
 "trail_complete": true, "disposition": "clean"}
```

Zero ok writes, zero fail writes — and `disposition: "clean"`. A run that wrote
nothing at all is reported as healthy. `tools/sdlc_run_health.py:83` sets
`DISPOSITION_CLEAN` whenever `fail_writes == 0`, and its own module docstring
(`:9`) spells out the hole: *"`clean` -- zero fail writes (nothing went wrong),
**or no counters at all**."* The observability layer built by #2451 to make
exactly this failure visible cannot distinguish "wrote everything successfully"
from "never wrote anything."

### The skill body contradicts itself

Step 3d.6 already instructs the supervisor to handle a refusal:

> The re-ensure can itself return a refusal — route its payload through the
> **same** three-way table and `owner_run_id` self-identity check from Step 2

That instruction is **unfollowable as written**, because the command on the line
above it destroys the payload it tells you to route. This is not a missing
feature; it is a live contradiction inside one section of one file.

## Freshness Check

Verified 2026-08-13 against the current `main` (`fd2edd66d`).

- `grep -rlE "reuse-run-id|_validated_reuse_candidate|marker_writes|3d\.6" docs/plans/*.md`
  returns **zero** open plans. No in-flight plan touches Step 3d.6, the reuse
  validator, or the marker counters.
- Issue #2675 is still `OPEN`, labels `bug`, `skills`, one comment.
- Issue #2714 (lease heartbeat anchored to supervisor lifetime) **shipped hours
  ago** via PR #2784, merge `ac96dc2`. Its plan is already migrated to
  `docs/plans/completed/`. This plan is written against the post-#2714 world and
  reasons about it explicitly (see Research).
- The issue's cited relatives are all resolved or superseded: #2659/PR #2667
  (supervised-run signal renewal), #2648 (issue-lock liveness), #2657
  (supervisor fork turn-chaining).
- `tools/sdlc_session_ensure.py` is **contested** — lane #2735 (round-6 critique)
  will add an idempotent slug-mint there, and #2766 is deferred against the same
  file. See Architectural Impact.

## Prior Art

| Issue | What it built | Why it did not close this |
|---|---|---|
| #2012 | Moved the ledger off `AgentSession.stage_states` onto the issue-keyed `PipelineLedger` | Made the ledger immune to run_id churn — which is precisely why #2675's stated mechanism is impossible. It did not touch **write authorization**. |
| #2003 | `--run-id` on every mutating `sdlc-tool` call; `_validated_reuse_candidate` | Introduced verified reuse, but corroboration reads the `AgentSession`, the most fragile record in the system. |
| #2395 / #2397 | `load()`-based existence check + SETNX create-lock in `get_or_create` | Closed the create()-clobber race on a populated ledger. Confirms the ledger layer is already hardened against overwrite-with-empty. |
| #2446 / #2451 | `owned_run_ids` history; per-run marker ok/fail counters | The history proof shipped (so "add owned_run_ids" is **not** the gap). The counters exist but `run_health` reads them fail-open. |
| #2452 | Step 3d.6 itself | Added the re-ensure **and** the `2>/dev/null || true` that blinds it. |
| #2554 | Routed `STATE_MACHINE_REJECTED` from `logger.debug` to `print(..., stderr)` | Fixed the same class of blindness one layer down. The fix is undone at the call site by `2>/dev/null`. |
| #2714 | Heartbeat anchored to supervisor pid; active release on supervisor death | Changes the *statistics* of a free lock (see Research), raising the stakes on the free-lock branch this plan touches. |

The through-line: every layer below the skill body has been taught to fail
loudly. The skill body then discards the message.

## Research

### The mint mechanism is real, and hypothesis-3 is confirmed

`ensure_session`'s documented resolution order (`tools/sdlc_session_ensure.py:641-680`)
ends in **"3. Create: Fall through to creating a new sdlc-local-{N} session."**
That branch calls `_acquire_run_lock_and_bind(issue_number, session, reuse_run_id=...)`
at `:916` with a **brand-new** session object.

`_acquire_run_lock_and_bind:481-482` does:

```python
if reuse_run_id:
    candidate = _validated_reuse_candidate(issue_number, session, reuse_run_id) or candidate
```

Every proof inside `_validated_reuse_candidate` (`:284-347`) is evaluated against
that `session`:

- `:321` live-lock-owner match — independent of the record, but a lapsed lease
  means the lock is free, so this branch does not fire.
- `:333-335` lapsed-then-re-minted — requires `_read_owned_run_ids(session)`.
- `:344-346` free lock — requires `session.active_run_id` **or**
  `_read_owned_run_ids(session)`.

A freshly created session has `active_run_id = None` and an empty
`owned_run_ids`. Both corroborating branches are structurally unreachable, the
helper returns `None`, and `or candidate` falls through to the fresh uuid.

Confirmed live: `sdlc-local-2735` was created `2026-08-13 09:02:55` and its
`owned_run_ids` is `["c4a2eb9025714156a5e644f7a3e32cd6"]` — a single entry, no
history. The continuity record the reuse check consults is more fragile than the
record it is protecting. **Hypothesis 3 holds as the mint mechanism.**

But the mint is the *trigger*, not the *damage*. The damage is step 2-4 of the
Problem section.

### What #2714 changed underneath this issue

PR #2784 anchored the lease heartbeat to its supervising `claude` process. Three
consequences bear directly on this plan:

1. **A free lock is now earlier and more common.** On confirmed supervisor death
   the heartbeat *actively releases* within ~2 minutes; with no resolvable
   supervisor it stops renewing at 90 minutes; otherwise the 4h ceiling applies.
   The free-lock branch of `_validated_reuse_candidate` (`:344-346`) therefore
   carries far more traffic than when it was written against a bare TTL lapse.
2. **A free lock now more often means "the prior supervisor is genuinely dead."**
   My #2714 finding was "processes died on schedule, the leases did not." Now
   they do. Re-ensure after a lapse is now much more likely to be a *legitimate*
   takeover by the same logical pipeline than a spurious TTL blip.
3. **Some releases are now deliberate.** `/do-sdlc` Step 5 exit calls
   `sdlc-tool session-release`, and the MERGE leg of `_write_marker_impl` calls
   `_release_run_best_effort` → `release_run` (`tools/sdlc_stage_marker.py:494-508`).

Point 3 raises the discriminator question the task poses: should re-ensure rebind
across a *deliberate* release?

**Discriminator chosen: none is needed, because of where the two releases sit.**
Both deliberate releases are terminal by construction. The MERGE leg fires only
after a MERGE-completed marker persists, and the Step 5 release fires only as
`/do-sdlc` exits its loop. Step 3d.6 is a **between-stage** step inside that
loop — it cannot run after either. A re-ensure reaching a free lock is therefore
always reaching an *accidental* lapse, never a deliberate handback. Adding a
"was this release deliberate?" flag would be a mechanism guarding a state that
cannot occur, and would rot. This is recorded as Risk 4 so a future change that
moves either release site is forced to revisit it.

### Why the ledger layer is the wrong place to defend

The task asks whether the durable fix is run_id-centric ("never mint while an
anchor with recorded markers exists") or ledger-centric ("a populated stage-marker
blob is never overwritten with an empty one").

**The evidence says: neither, and the ledger-centric defense would be a no-op.**

- Nothing overwrites the blob with an empty one. `get_or_create` already refuses
  to clobber a populated record and logs a WARNING when it nearly does
  (`agent/pipeline_ledger.py:271-281`). `_save()` merges unowned `_*` keys back
  from the live store before writing (`agent/pipeline_state.py:557-585`).
  `update_stage_states` is read-modify-write with optimistic retry and post-save
  verification (`tools/stage_states_helpers.py:209-239`). There is **no**
  `PipelineLedger` deleter anywhere in production code — verified by grepping
  `tools/ agent/ scripts/ reflections/ models/`.
- A "never overwrite populated with empty" guard added today would never fire,
  because the ledger for #2735 was never populated in the first place. It would
  ship as dead code that *looks* like a fix, which is the worst possible outcome
  for an issue whose defining property is that failures are invisible.

The real invariant to protect is one layer up: **a stage marker the supervisor
believes it wrote either landed, or the supervisor knows it did not.** That is an
authorization-and-observability invariant, not a storage invariant.

## Spike Results

### spike-1: Can a fresh run_id wipe or recreate the ledger anchor?

**No.** `PipelineLedger` is keyed `f"{target_repo}:{issue_number}"`
(`agent/pipeline_ledger.py:134`). Enumerating the ORM shows issue 2735 has
exactly **one** record, `ledger_key='tomcounsell/ai:2735'`,
`target_repo='tomcounsell/ai'` — no sibling under a `None` target_repo. 151
ledger records survive in total, including `tomcounsell/ai:209` and
`tomcounsell/ai:1249`, confirming the model's no-TTL claim (`PipelineLedger._meta.ttl`
is `None`). The issue's stated mechanism is refuted.

### spike-2: Could the blob have been emptied and then repopulated?

**No.** `_save()` backfills all nine `ALL_STAGES` keys to `"pending"` before
serializing (`agent/pipeline_state.py:524-527`). Any landed marker write leaves
all nine keys. #2735's blob has zero. The markers never landed.

### spike-3: Is the verdict writer authorized more permissively than the marker writer?

**No — they are identical.** `record_verdict`'s CLI path
(`tools/sdlc_verdict.py:731-773`) and `_write_marker_impl`
(`tools/sdlc_stage_marker.py:534-556`) both call `resolve_ledger_lease` then
`revalidate_ledger_lease`. There is no asymmetry to exploit. The verdict landed
because it was invoked carrying the *winning* identity; the markers did not
because they carried a losing one — and nothing reported the difference.

### spike-4: Are marker refusals counted?

**Only when both `issue_number` and `run_id` are truthy.**
`record_marker_write` early-returns on either being falsy
(`tools/_sdlc_marker_telemetry.py:95-97`). A marker invoked without `--run-id`
is refused by the lease check *and* records neither an ok nor a fail — the
refusal is invisible at both the call site and the telemetry layer
simultaneously.

### spike-5: Does `run-health` distinguish "wrote everything" from "wrote nothing"?

**No.** `tools/sdlc_run_health.py:79-83`:

```python
disposition = (
    DISPOSITION_TRANSIENT_RECOVERED if trail_complete else DISPOSITION_NEVER_LANDED
)
...
disposition = DISPOSITION_CLEAN
```

`DISPOSITION_CLEAN` is the `else` of "any fail writes". `ok_writes` is never
consulted. Measured against the real #2735 run: `{"ok_writes": 0,
"fail_writes": 0, "trail_complete": true, "disposition": "clean"}`.

### spike-6: Does the plan doc mask the damage in `_meta`?

**Yes, partially — and this is why the loss stayed unnoticed.**
`stage-query`'s `_meta.revision_applied` / `revision_applied_at` are parsed from
the **plan file frontmatter**, not the ledger (`tools/sdlc_stage_query.py:378-414`,
`:544-549`). So #2735's `_meta` cheerfully reports
`revision_applied_at: "2026-08-13T12:04:11Z"` while the `stages` blob is empty.
A supervisor reading `_meta` sees a healthy pipeline. Any fix must not rely on
`_meta` as a health signal.

## Data Flow

Today, after a lapse (each `→` is a process boundary):

```
/do-sdlc loop iteration N
  └─ stage subagent runs, carries run_id=OLD
       └─ sdlc-tool stage-marker --run-id OLD  → resolve_ledger_lease
            → lock FREE (heartbeat released it, #2714)  → LEASE_ABSENT
            → exit 1 + stderr diagnostic
            → 2>/dev/null || true   ← DIAGNOSTIC DESTROYED
            → telemetry: fail+1 (or nothing at all if run_id was empty)
  └─ Step 3d.6: session-ensure --reuse-run-id OLD
       → ensure_session falls through to Create (fresh session)
       → _validated_reuse_candidate: no active_run_id, no owned_run_ids → None
       → fresh mint NEW, wins the free lock
       → 2>/dev/null || true   ← THE NEW run_id IS NEVER READ BACK
  └─ loop iteration N+1 still carries run_id=OLD
```

The final line is the compounding failure: the supervisor keeps the stale
`{run_id}` in its own context because it never read the tool's stdout. Every
subsequent marker in the run is refused for the same reason, forever.

After this plan:

```
  └─ sdlc-tool stage-marker --run-id OLD  → exit 1, stderr VISIBLE
       → supervisor sees LEASE_ABSENT, does not proceed as if written
  └─ Step 3d.6: session-ensure --reuse-run-id OLD   (stderr visible, exit checked)
       → _validated_reuse_candidate consults the DURABLE issue-keyed anchor
       → OLD is in the ledger's run-identity history → rebind, re-acquire under OLD
       → stdout parsed; supervisor adopts the returned run_id for iteration N+1
  └─ if rebinding is genuinely impossible → loud named refusal, loop stops
```

## Architectural Impact

### The contested-file question — answered explicitly

**NO: this plan does not require editing `tools/sdlc_session_ensure.py::ensure_session`.**

`ensure_session`'s body is untouched. Its resolution order, its env short-circuit,
its adopt-ownerless branch, and its create fall-through all stay exactly as they
are. Task 3 modifies **only `_validated_reuse_candidate`** — a leaf helper at
`:284-347` with a single call site (`:482`) — by adding one more corroborating
source alongside the three it already consults. Nothing else in the file moves.

That said, the file **is** contested at file granularity: lane #2735 (round-6
critique) will add an idempotent slug-mint, and #2766 is deferred against it.
Tasks 1, 2 and 4 of this plan touch it **not at all**, so they can land in any
order relative to those lanes. Task 3 is the only point of contact and is
sequenced last precisely so it can be rebased onto whatever those lanes land.
Recorded as Risk 1.

### Where the durable anchor lives

The new corroboration record is a `_run_identities` key inside the ledger's own
`stage_states_json` blob, written through `update_stage_states` like every other
`_*` metadata key. This is deliberate:

- It is issue-keyed, so it survives every `AgentSession` lifecycle event — the
  exact durability property #2012 established and the exact property
  `owned_run_ids` lacks.
- It requires **no** Popoto schema change, therefore **no** entry in
  `scripts/update/migrations.py`. An absent key reads as an empty list on an old
  record, which degrades to today's behavior.
- `_save()` already preserves unowned `_*` keys across merges
  (`agent/pipeline_state.py:557-585`), so it is protected by machinery that
  already exists and is already tested.

### Fail-open vs fail-closed, decided per new decision point

| New decision point | Direction | Why |
|---|---|---|
| Step 3d.6 exit-code check | **fail-closed** | A re-ensure that cannot establish identity means every subsequent marker in the run is refused. Continuing is guaranteed silent data loss. |
| Reading the new `_run_identities` anchor | **fail-open** | A read error degrades to today's three proofs. A Redis hiccup must never block a legitimate rebind. Mirrors `touch_issue_lock`'s peek default. |
| Writing `_run_identities` on bind | **fail-open** | Best-effort observability metadata; a write failure must not fail a lock acquisition that already succeeded. Mirrors `_record_marker_telemetry`. |
| `run-health` zero-write disposition | **fail-closed** | This is the detector. A detector that fails open is the bug being fixed. |

## Appetite

**Medium.** Four tasks. Two are skill-body/prose edits with no Python. One is a
~15-line disposition change plus tests. One is a scoped ~25-line change to a
single leaf helper. No schema change, no migration, no new CLI entry point.

The appetite is Medium rather than Small only because Task 3 touches a contested
file and needs demonstrated-red tests against real Redis.

## Solution

### Key Elements

1. **Stop discarding the diagnostic (Task 1).** Replace `2>/dev/null || true` at
   Step 3d.6 with a form that keeps stderr and branches on exit code, and make
   the supervisor **adopt the run_id the tool actually returned** rather than
   keeping its own stale copy. This alone converts the failure from silent to
   loud and stops the compounding described in Data Flow.

2. **Make the zero-write run visible (Task 2).** Add a
   `never_wrote` disposition to `run-health`: `ok_writes == 0 and fail_writes == 0`
   is not `clean`. This is the detector that would have caught #2735 at the first
   between-stage check.

3. **Give reuse a durable place to look (Task 3).** Record each bound run_id into
   an issue-keyed `_run_identities` list on the ledger, and teach
   `_validated_reuse_candidate` to accept it as a fourth proof. A re-ensure after
   a lapse then rebinds to the existing identity even when the `AgentSession` was
   just created and knows nothing.

4. **Close the same hole in the marker call sites (Task 4).** `docs/sdlc/do-plan.md:12-13`
   carries the identical `2>/dev/null || true` on both PLAN marker writes. Fix
   the addendum the same way so the fix is not undone one directory over.

### Flow

Tasks 1, 2 and 4 are independent of each other and of Task 3, and independent of
the `sdlc_session_ensure.py` lanes. Task 3 lands last.

### Technical Approach

**Task 1** — `.claude/skills-global/do-sdlc/SKILL.md` Step 3d.6. The command
becomes a form that captures stdout, lets stderr through, and checks status:

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

with prose instructing the supervisor to (a) treat a non-zero exit as a stop
condition routed through the Step 2 three-way table, and (b) parse `run_id` out
of the JSON payload and **use that value** for every subsequent stage in this
iteration. The existing "route its payload through the same three-way table"
sentence stops being a contradiction and becomes an instruction that can be
followed.

**Task 2** — `tools/sdlc_run_health.py`. New module constant
`DISPOSITION_NEVER_WROTE = "never_wrote"`, returned when
`ok_writes == 0 and fail_writes == 0`. The existing `never_landed` /
`transient_recovered` / `clean` branches are unchanged for every run that wrote
anything. `tools/sdlc_review_finalize.py` already asserts `>= 1` confirmed ok
write before selfcheck passes; this makes the same signal available to the
supervisor mid-run instead of only at REVIEW.

**Task 3** — the durable anchor. On successful bind in
`_acquire_run_lock_and_bind`, append the bound run_id to `_run_identities` on the
ledger via `update_stage_states(ledger, ..., field="stage_states_json")`, capped
at `SDLC_RUN_IDENTITY_HISTORY_MAX` entries (see Rabbit Holes for why capped).
Then add a fourth proof to `_validated_reuse_candidate`, placed **after** the
three existing ones so no current behavior changes:

- free lock **and** the claim appears in the ledger's `_run_identities` → return
  the claim (rebind and re-acquire under it);
- lapsed-then-re-minted, where both the claim and the live owner appear in
  `_run_identities` → return the **live owner** id, mirroring `:333-335`.

The no-adopt invariant is preserved exactly: the helper still only ever *echoes
back a claim the caller already carried*, never reads an identity out of the lock
or the ledger and hands it to a caller who did not already hold it. `_run_identities`
is self-written history, exactly like `owned_run_ids` — it is just stored
somewhere that survives.

Magic number, per repo convention:

```python
# Cap on the issue-keyed run-identity history. GRAIN OF SALT: 20 is
# PROVISIONAL/TUNABLE -- generous enough to cover a long pipeline with
# repeated lease lapses, bounded so the ledger blob cannot grow without
# limit over an issue's lifetime. Override via SDLC_RUN_IDENTITY_HISTORY_MAX.
SDLC_RUN_IDENTITY_HISTORY_MAX = int(
    os.environ.get("SDLC_RUN_IDENTITY_HISTORY_MAX", "20")
)
```

**Task 4** — `docs/sdlc/do-plan.md:12-13`, same treatment as Task 1.

## Failure Path Test Strategy

### Exception Handling Coverage

- `_run_identities` read raises → `_validated_reuse_candidate` returns the
  pre-#2675 result (fail-open); assert no exception escapes and the three legacy
  proofs still work.
- `_run_identities` write raises → the bind still succeeds and returns a run_id
  (fail-open); assert the lock is held afterwards.
- `update_stage_states` returns `False` (retries exhausted) on the identity
  append → bind unaffected.

### Empty/Invalid Input Handling

- Ledger with no `_run_identities` key at all (every pre-existing record) →
  reads as `[]`, behavior identical to today. This is the backward-compat proof
  that no migration is needed.
- `_run_identities` present but malformed (a string, a dict, `null`) → treated as
  `[]`, never raises.
- `run-health` with `run_id=None` or `issue_number=None` → unchanged empty
  counters, and must NOT report `never_wrote` (there is no run to judge).

### Error State Rendering

- A refused marker must still print its `LEASE_ABSENT` / `ISSUE_LOCKED`
  diagnostic to stderr and exit 1 — assert this is unchanged, since Task 1's
  value depends entirely on it.
- `never_wrote` must render as a distinct string in the `run-health` JSON, not
  collapse into `clean`.

## Test Impact

- [ ] `tests/unit/test_sdlc_run_health.py` — UPDATE: existing cases asserting
      `disposition == "clean"` for a zero-counter run now expect `"never_wrote"`.
      This is the demonstrated-red case: the test must fail on current `main`.
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: add the fourth-proof
      cases; assert the three existing proofs are unchanged (control).
- [ ] `tests/integration/test_sdlc_run_identity_resume.py` — UPDATE: extend the
      real-Redis resume scenario to cover create-fall-through + lapsed lease,
      asserting rebind to the original run_id rather than a fresh mint.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — no change expected; used as the
      control that refusal semantics and exit codes are untouched.
- [ ] `tests/unit/test_pipeline_state_machine.py` — no change expected; used as
      the control that `_save()` still preserves unowned `_*` keys, now
      including `_run_identities`.

## Rabbit Holes

- **Rewriting `ensure_session`'s resolution order.** The create fall-through is
  correct behavior for a genuinely new session. Making it "look harder" for a
  prior session invites the #1671 cross-issue contamination class. Fix the
  corroboration source, not the resolver.
- **A "never mint while an anchor with recorded markers exists" rule.** This is
  the issue's own suggestion and it is wrong twice: it would have permitted the
  #2735 mint (the anchor had no recorded markers), and it inverts the no-adopt
  invariant by letting ledger contents veto a lock contest.
- **A ledger-layer "never overwrite populated with empty" guard.** Argued at
  length in Research. Zero production writers do this; it would ship as dead code.
- **Unbounded `_run_identities` growth.** A long-lived issue could accumulate
  identities indefinitely and bloat a blob that is read on every router poll.
  Capped at `SDLC_RUN_IDENTITY_HISTORY_MAX`, newest-last.
- **Making Step 3d.6 renew the lease.** It is a continuity *proof*, not a
  keepalive — renewal is #2714's heartbeat's job, and #2714 just shipped. Do not
  blur the two.
- **Adding a deliberate-vs-accidental release flag.** Argued in Research: both
  deliberate release sites are terminal and unreachable from Step 3d.6.

## Risks

### Risk 1: `tools/sdlc_session_ensure.py` is contested by two other lanes

Lane #2735 (round-6 critique) adds an idempotent slug-mint; #2766 is deferred
against the same file. **Mitigation:** Task 3 is the only point of contact, it is
sequenced last, and it modifies a single leaf helper (`_validated_reuse_candidate`,
`:284-347`) with one call site. `ensure_session` itself is not edited — see
Architectural Impact. Tasks 1, 2 and 4 deliver the loudness fix independently and
can land while those lanes are in flight. **This plan should still stop for
cross-lane ordering confirmation before Task 3 begins.**

### Risk 2: Making Step 3d.6 loud turns silent degradation into pipeline stops

Runs that were previously "succeeding" while writing no markers will now halt.
That is the intent, but it will look like a regression in throughput on first
contact. **Mitigation:** the refusal message must name the run_id mismatch and
the remedy explicitly, and Task 3 (rebind) removes the most common cause of the
stop. Land Task 1 and Task 3 in the same release where practical.

### Risk 3: The fourth proof widens what counts as "self"

A longer identity history means more claims corroborate. **Mitigation:**
`_run_identities` is written only from a successful self-bind, never populated
from a foreign lock payload or a foreign record — identical provenance to
`owned_run_ids`, which #2446 already established as safe. The cap bounds it. A
foreign live holder still yields `ISSUE_LOCKED` because the free-lock branch
requires `not peek.acquired` to be false.

### Risk 4: A future refactor moves a deliberate release into the loop

Research concludes no deliberate-release discriminator is needed **because both
deliberate release sites are terminal**. If `release_run` is ever called from a
non-terminal position, a re-ensure could rebind across an intentional handback.
**Mitigation:** an anti-criterion asserts `release_run` appears in
`tools/sdlc_stage_marker.py` only on the MERGE leg, so moving it fails
Verification.

### Risk 5: `_meta` continues to mask an empty ledger

Per spike-6, `_meta.revision_applied_at` comes from plan frontmatter and looks
healthy on a gutted ledger. This plan does not change that. **Mitigation:**
out of scope, but Task 2's `never_wrote` gives the supervisor a signal that does
not depend on `_meta` at all. Noted in No-Gos.

## Race Conditions

### Race 1: Two re-ensures for the same issue append identities concurrently

Both call `update_stage_states` with optimistic retry and post-save verification
(`tools/stage_states_helpers.py:209-239`), so the loser reloads and re-applies.
The update function must be **idempotent** — appending an id already present is a
no-op — which the helper's docstring explicitly requires of every `update_fn`
(`:173-176`).

### Race 2: Identity append lands after a concurrent `_save()`

`_save()` reloads and merges unowned `_*` keys immediately before writing
(`agent/pipeline_state.py:557-585`, `:544-548`). `_run_identities` is unowned by
the state machine, so it is merged forward rather than dropped. This is the same
protection `_verdicts` and `_sdlc_dispatches` already rely on.

### Race 3: The lock is taken between the fourth-proof read and the re-acquire

`_acquire_run_lock_and_bind` already re-contests via `touch_issue_lock` after
`_validated_reuse_candidate` returns a candidate, and a foreign holder yields
`ISSUE_LOCKED`. The fourth proof only chooses *which id to attempt with*; it
never bypasses the contest.

### Race 4: The heartbeat releases the lease between the proof and the bind

Post-#2714 this is more likely than before. The outcome is a free lock at bind
time, which `touch_issue_lock`'s `SET NX` acquires under the rebound identity —
the desired result. No additional guard needed.

## No-Gos (Out of Scope)

- Editing `tools/sdlc_session_ensure.py::ensure_session` itself.
- Any `PipelineLedger` schema change, and therefore any
  `scripts/update/migrations.py` entry.
- Any change to `PipelineLedger.get_or_create`'s create-race hardening
  (#2395/#2397) — verified correct and unrelated.
- Any change to lease TTLs, the heartbeat, or renewal cadence (#2714 territory,
  shipped hours ago).
- Fixing `_meta`'s plan-frontmatter-derived fields masking an empty ledger
  (Risk 5) — real, but a separate issue.
- Backfilling `_run_identities` onto the 151 existing ledger records. An absent
  key reads as `[]` and degrades to today's behavior; a backfill would have no
  truthful source to draw from.
- Reconstructing #2735's or #2643's lost markers. Manual, one-off, not a code
  change.

## Update System

No update-system changes required. This work adds no dependency, no config file,
no CLI entry point, and no Popoto schema change. `.claude/skills-global/do-sdlc/SKILL.md`
is already hardlinked to `~/.claude/skills/` by `scripts/update/hardlinks.py`, so
the Task 1 edit propagates on the next `/update` with no wiring. `docs/sdlc/do-plan.md`
is read from the repo at runtime and needs no propagation step.

## Agent Integration

No agent-integration changes required. `sdlc-tool session-ensure` and
`sdlc-tool run-health` are already registered subcommands reachable via the
agent's Bash tool; this plan changes their output and semantics, not their
surface. No new `pyproject.toml [project.scripts]` entry, no new bridge import.

## Documentation

- [ ] Update `docs/features/sdlc-issue-ownership-lock.md` with the durable
      issue-keyed `_run_identities` corroboration source and the fourth reuse
      proof, replacing (not appending to) the current description of how a reuse
      claim is validated.
- [ ] Update `docs/sdlc/do-sdlc.md` to describe Step 3d.6's new loud contract and
      the run_id-adoption requirement.
- [ ] Update `docs/sdlc/do-plan.md` marker invocations (this is Task 4 — it is
      both the code change and the doc change).
- [ ] Add a `never_wrote` row to the disposition table in
      `docs/features/sdlc-run-health.md` if that file exists; otherwise document
      the disposition in `tools/sdlc_run_health.py`'s module docstring, which is
      the current source of truth for the disposition vocabulary.

## Success Criteria

1. A re-ensure after a lapsed lease, against a freshly created `AgentSession`,
   rebinds to the **existing** run_id instead of minting a fresh one — proven by
   an integration test against real Redis.
2. When rebinding is genuinely impossible, Step 3d.6 fails **loudly**: non-zero
   exit, diagnostic on stderr, supervisor stops rather than continuing with a
   stale identity.
3. `sdlc-tool run-health` reports `never_wrote` — not `clean` — for a run with
   zero ok and zero fail marker writes. Demonstrated red against current `main`.
4. No `2>/dev/null || true` remains on any `session-ensure` or `stage-marker`
   invocation in `.claude/skills-global/do-sdlc/SKILL.md` or `docs/sdlc/do-plan.md`.
5. The three pre-existing reuse proofs behave identically (control tests pass
   unchanged).
6. `tools/sdlc_session_ensure.py::ensure_session` is byte-identical to `main`.

## Step by Step Tasks

### 1. Make Step 3d.6 loud and identity-adopting

- Edit `.claude/skills-global/do-sdlc/SKILL.md` Step 3d.6: remove
  `2>/dev/null || true`; add prose requiring an exit-code branch and requiring
  the supervisor to adopt the returned `run_id` for subsequent stages.
- Resolve the self-contradiction: the existing "route its payload through the
  same three-way table" sentence must now be executable.
- No Python. No tests (skill-body prose), verified by Verification greps.

### 2. `never_wrote` disposition in run-health

- Add `DISPOSITION_NEVER_WROTE = "never_wrote"` to `tools/sdlc_run_health.py`.
- Return it when `ok_writes == 0 and fail_writes == 0` and a real
  `(issue_number, run_id)` pair was supplied.
- Update the module docstring's disposition list — replace the current
  `clean -- zero fail writes ..., or no counters at all` wording entirely; do not
  leave the old sentence alongside the new one.
- Tests in `tests/unit/test_sdlc_run_health.py`: demonstrated red (a zero-counter
  run currently returns `clean`), plus a control that a run with ok writes and no
  fails still returns `clean`.

### 3. Durable issue-keyed run-identity anchor (touches the contested file — STOP for ordering first)

- **Before starting: confirm cross-lane ordering with the #2735 and #2766 lanes.**
- Add `SDLC_RUN_IDENTITY_HISTORY_MAX` with the GRAIN OF SALT comment.
- On successful bind in `_acquire_run_lock_and_bind`, append the bound run_id to
  `_run_identities` on the ledger via `update_stage_states(..., field="stage_states_json")`.
  Idempotent append, capped, fail-open.
- Add the fourth proof to `_validated_reuse_candidate`, placed after the three
  existing proofs. Do not reorder or modify them.
- Do not touch `ensure_session`.
- Tests: `tests/unit/test_sdlc_session_ensure.py` (fourth proof + the three
  controls), `tests/integration/test_sdlc_run_identity_resume.py` (real-Redis
  create-fall-through + lapsed lease → rebind).

### 4. Close the same hole in the do-plan addendum

- Edit `docs/sdlc/do-plan.md:12-13`: remove `2>/dev/null || true` from both PLAN
  marker invocations, and state that a non-zero exit is a stop condition.

## Verification

Every row must fail by **exit code**. No `grep -c` anywhere: it prints
`path:count` per file and exits `1` on zero matches, which aborts a `set -e`
harness on the *success* case.

| Check | Command | Expected |
|-------|---------|----------|
| Run-health tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_run_health.py -q` | exit code 0 |
| Session-ensure tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Resume integration passes | `scripts/pytest-clean.sh tests/integration/test_sdlc_run_identity_resume.py -q` | exit code 0 |
| Marker semantics unchanged (control) | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_marker.py -q` | exit code 0 |
| State-machine merge unchanged (control) | `scripts/pytest-clean.sh tests/unit/test_pipeline_state_machine.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New disposition exists | `grep -q "never_wrote" tools/sdlc_run_health.py` | exit code 0 |
| Disposition reachable via CLI | `sdlc-tool run-health --help` | exit code 0 |
| Identity cap is env-overridable | `grep -q "SDLC_RUN_IDENTITY_HISTORY_MAX" tools/sdlc_session_ensure.py` | exit code 0 |
| Cap carries the grain-of-salt marker | `grep -q "GRAIN OF SALT" tools/sdlc_session_ensure.py` | exit code 0 |
| Durable anchor written to the ledger | `grep -q "_run_identities" tools/sdlc_session_ensure.py` | exit code 0 |
| Ownership-lock docs updated | `grep -q "_run_identities" docs/features/sdlc-issue-ownership-lock.md` | exit code 0 |
| Step 3d.6 docs updated | `grep -q "reuse-run-id" docs/sdlc/do-sdlc.md` | exit code 0 |

**Anti-criteria** — each must exit 0. These are anchored to actual code *use*
rather than to bare identifiers, so a check cannot be satisfied only by deleting
the comment that explains the invariant.

| Anti-criterion | Command | Expected |
|---|---|---|
| No ledger deleter introduced | `! grep -q 'ledger\.delete(' tools/sdlc_session_ensure.py` | exit code 0 |
| No Popoto schema field added | `! grep -q 'run_identities = ' agent/pipeline_ledger.py` | exit code 0 |
| No migration entry added for this work | `! grep -q 'run_identities' scripts/update/migrations.py` | exit code 0 |
| Create-race hardening untouched | `grep -q '_acquire_create_lock' agent/pipeline_ledger.py` | exit code 0 |

Four checks contain a literal `|` and are written **outside** the table on
purpose. A literal `|` inside a markdown table cell must be escaped as `\|`, and
copying that escape into a shell is precisely how a check that can never fail
gets written: in a BRE, `\|` is *alternation*, so `grep -q '2>/dev/null \|\| true'`
parses as "empty-or-empty-or-` true`" and **errors with exit 2** — measured, not
assumed. Run these verbatim; each must exit 0:

```bash
! grep -qF '2>/dev/null || true' .claude/skills-global/do-sdlc/SKILL.md
! grep -qF '2>/dev/null || true' docs/sdlc/do-plan.md
! grep -rqE 'never mint while|anchor with recorded markers' tools/ agent/
! grep -qE 'release_run\(|_release_run_best_effort\(' tools/sdlc_session_ensure.py
```

The first two use `-F` (fixed string), the only form that matches this token
correctly; both currently exit 0 *without* the `!`, so they are demonstrated-red
against `main` and become the direct proof that Tasks 1 and 4 landed.

The third asserts the rejected run_id-centric rule was not implemented anywhere.
The fourth asserts no release path leaked into the ensure module (Risk 4's
guard); `release_run` legitimately appears in `tools/sdlc_stage_marker.py` on the
MERGE leg — verified present there as a control — and is anchored by the `(` so a
prose mention can neither satisfy nor break it.

Finally, the `ensure_session` fence, which is the single most important
invariant in this plan:

```bash
git diff main -- tools/sdlc_session_ensure.py | grep -qE '^[+-].*def ensure_session' && exit 1 || exit 0
```
