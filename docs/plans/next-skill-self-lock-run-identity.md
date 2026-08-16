---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-14
tracking: https://github.com/tomcounsell/ai/issues/2766
last_comment_id: 5288838467
revision_applied: true
revision_applied_at: 2026-08-14T03:21:02Z
---

# next-skill takes the caller's run identity instead of guessing it

## Problem

A `/do-sdlc` supervisor mints a `run_id` with `sdlc-tool session-ensure`, takes the issue
lock, and then asks the router what to do next:

```
sdlc-tool next-skill --issue-number 2766
{"blocked": true, "reason": "ISSUE_LOCKED", "guard_id": "ISSUE_LOCK",
 "owner_run_id": "<the supervisor's own run_id>", "orphaned_lock": false}
```

`/do-sdlc`'s Step 2 three-way refusal table calls exactly that shape — `ISSUE_LOCKED` +
`orphaned_lock: false` — "the **only unconditional stop condition** of the three. A genuine
live foreign run owns the issue. Stop and report." A supervisor that follows the table
correctly stands down on its own lock, on its first router call, before doing any work.
The pipeline dies at the starting line and the human sees a false "another run owns this
issue" report.

**Current behavior:**

`tools/sdlc_next_skill.py:530-545` does not receive the caller's identity. It reconstructs
one:

```python
peek_run_id = None
try:
    issue_session = find_session_by_issue(issue_number)
    if issue_session is not None:
        peek_run_id = getattr(issue_session, "active_run_id", None)
except Exception:
    peek_run_id = None

lock_result = touch_issue_lock(issue_number, peek_run_id, session_id=session_id or "", peek=True)
```

When the lookup yields nothing, `peek_run_id` is `None`. `touch_issue_lock`'s peek branch
(`models/session_lifecycle.py:1235-1250`) only returns `acquired=True` on
`run_id and owner_run_id == run_id`; with `run_id=None` it falls through to the blocked
return and reports the lock — the caller's own — as a foreign holder.

`find_session_by_issue` (`tools/_sdlc_utils.py:331-345`) returns `None` for three
structurally ordinary callers:

1. the session has reached a terminal `status` (`failed`/`completed`/`killed`) — excluded by
   default, deliberately, since incident #1915;
2. the session is not `session_type == "eng"` — all three passes filter to eng;
3. any exception in the lookup — swallowed to `None`.

The bug is *conditional on the lookup*, not on any write. That is why it reproduces
intermittently: on a healthy run with a live `eng` session it never fires.

**Desired outcome:**

`next-skill` is told who is calling. `sdlc-tool next-skill --run-id {run_id}` peeks the lock
under the caller's stated identity, so a run can never be told to stand down for its own
lock. The blocked payload additionally carries **diagnostic-only** keys
(`peek_identity: "caller" | "session_mirror" | "unresolved"`, and `session_mirror_run_id`
on the caller-supplied path) so a human reading a block can tell "I could not determine the
caller's identity" apart from "a genuine live rival owns this issue" — and `/do-sdlc`'s
Step 2 table applies the same `owner_run_id` self-identity check to the `ISSUE_LOCKED` rows
that Step 3d.6 already applies. Neither key drives any automatic supervisor recovery
branch (see Critique Results, concern 4).

## Freshness Check

**Baseline commit:** `a27710d060e72e12c4869fc13466e3b9ce871372`
**Issue filed at:** 2026-08-13T07:12:06Z
**Disposition:** Minor drift — with a **corrected diagnosis**. The issue's stated root cause
is wrong; its *secondary* suggestion is the real fix. The symptom is real and reachable.

**File:line references re-verified:**

- `tools/sdlc_next_skill.py:473-505` — issue named this as the suspect peek site. **Drifted:**
  the peek block now lives at `tools/sdlc_next_skill.py:513-549`; `473-505` is
  `_recover_stage_states_from_durable_signals`. The claim about the peek holds at the new
  location.
- `tools/sdlc_session_ensure.py:583-597` — issue claimed `session-ensure` does not write the
  `active_run_id` mirror. **False.** The mirror IS written, with `active_run_id` and
  `owned_run_ids` both in `update_fields` (no partial-save trap), followed by a post-save
  readback at `:613-646` that **releases the lock** and returns `RUN_BIND_FAILED` if the
  record does not read back carrying the run_id. A successful `session-ensure` structurally
  cannot leave the mirror unwritten. `git log -L` dates the mirror write to `2f324bff6`
  (2026-07-11, #2003) and the readback to `250380451` (2026-07-14) — both predate the issue's
  filing by a month.
- `models/session_lifecycle.py:1235-1250` — `touch_issue_lock` peek branch: confirmed, blocks
  on `run_id=None` against any existing lock.
- `tools/_sdlc_utils.py:331-345` — `find_session_by_issue` eng-only + non-terminal filters:
  confirmed.

**Live read-only probe on this issue's own run (baseline commit):**

```
find_session_by_issue(2766) -> sdlc-local-2766  eng  running  active_run_id=27304a58...
touch_issue_lock(2766, "27304a58...", peek=True) -> acquired=True  orphaned_lock=False
```

The mirror is present and the peek succeeds. This run's first `next-skill` after a fresh
`session-ensure` did **not** block — exactly as the conditional diagnosis predicts, and
directly contradicting the issue's "the mirror is never written" claim.

**Cited sibling issues/PRs re-checked:**

- **#2803** (`ec585ceb9`, merged 2026-08-14T02:36Z) — "Anchor run-identity history on the
  ledger so a re-ensure rebinds instead of re-mints." Landed *after* filing, touches
  `tools/sdlc_session_ensure.py`. Adds the durable, issue-keyed `_run_identities` anchor
  (`agent/pipeline_ledger.py:397::read_run_identities`) and `owned_run_ids`. Does not fix this
  bug; it supplies a corroboration primitive the fix can reuse.
- **#2813** — "SDLC issue lock and session lookup are not repo-scoped, so two repos sharing an
  issue number collide." Open. Filed from the same investigation. Out of scope here (No-Gos).
- **#1915 / #1954** — the incident and the fix that made `find_session_by_issue` exclude
  terminal sessions. Still live rationale; this plan must not undo it.
- **#2003** — introduced run-identity ownership and the `active_run_id` mirror. Closed, shipped.
- **#2446 / #2451 / #2675** — self-recognition after lease lapse. Closed. The `owned_run_ids`
  and anchor machinery this plan corroborates against.

**Commits on main since issue was filed (touching referenced files):**

- `ec585ceb9` #2803 run-identity anchor — *changed the landscape favorably* (supplies
  `read_run_identities`); did not change the root cause.
- `e50eba258` #2735/#2718 lane identity — irrelevant to the peek path.
- `706fc4da0` #2740/#2767/#2769/#2790 verdict finalize + stale-verdict router row — irrelevant.
- `ac96dc210` #2784 lease heartbeat anchoring — adjacent (lease liveness), does not touch peek.
- `971ff1caf` #2660/#2747 maintenance writes and `updated_at` — irrelevant.

**Active plans in `docs/plans/` overlapping this area:**

- `ledger-integrity.md` (tracking #2730, status Ready) — dispatch-record durability. Adjacent
  substrate, different call path (`sdlc-tool dispatch record`, not `next-skill`'s peek). No
  file conflict expected in `tools/sdlc_next_skill.py`.
- `sdlc-progress-lane-discovery-branch-shape.md` (tracking #2755, status Ready) — lane slug
  discovery in the reflections path. No overlap.

Neither is a blocker. Both are surfaced so a builder who sees a conflicting edit in
`tools/` knows where it came from.

**Notes:** The issue body has been amended with a `## Recon Summary` carrying this evidence,
so the ISSUE→PLAN gate is grounded in the corrected diagnosis rather than the original one.

## Prior Art

- **#2003 / PR #2010** — "SDLC substrate: run_id ownership, live merge-predicate enforcement,
  PR-number single writer." Introduced `run_id` ownership, the `active_run_id` mirror, and the
  `--run-id` convention for state-mutating subcommands. Succeeded. It also established the
  rule this bug lives inside: read-only subcommands take *no* run-id, so `next-skill` was
  given an inference path instead of an argument. That design choice is the root cause.
- **#1954** — introduced the `next-skill` issue-lock pre-check itself, with `peek=True` so
  `next-skill` never claims or renews. Succeeded at its goal; the peek's *identity source* was
  the unexamined part.
- **#1915** — incident: a terminal session was resolved as the live issue owner and a second
  live session believed it also owned the issue. Fixed by excluding terminal statuses from
  `find_session_by_issue`. Succeeded. This plan must not weaken that filter.
- **#2446 / #2451 / PR #2076** — "Local /do-sdlc cannot finish a pipeline: supervisor mistakes
  its own orphaned session for a rival." Added `owned_run_ids` self-history and the
  self-identity check. Partially successful: it fixed self-recognition for `session-ensure`
  and `SUPERVISED_RUN_ACTIVE`, but not for `next-skill`'s peek — the same class of bug,
  unfixed in one more place.
- **#2675 / PR #2803** — "Step 3d.6 continuity re-ensure mints a new run_id after lease lapse."
  Added the durable issue-keyed `_run_identities` anchor because session-side history is
  erasable. Succeeded, merged 2026-08-14.
- **#2452** — "`/do-sdlc` skill body predates #2026 fork inheritance: `SUPERVISED_RUN_ACTIVE`
  hand-off is treated as an abort." Closed. Same *shape* of bug in the skill body: a designed
  self-signal misread as a stop condition. The fix was a skill-body correction, and this plan's
  secondary change is its direct sibling for the `ISSUE_LOCKED` rows.

## Research

No relevant external findings — proceeding with codebase context and training data. The work
is entirely internal to this repo's SDLC substrate: no external libraries, APIs, or ecosystem
patterns are involved. Phase 0.7 skipped per the skill's own skip criterion.

## Data Flow

1. **Entry point**: `/do-sdlc` (or `/sdlc`) runs `sdlc-tool session-ensure --issue-number N`.
   `tools/sdlc_session_ensure.py` mints `candidate` run_id, acquires
   `session:issuelock:{N}` with `run_id=candidate`, writes `session.active_run_id = candidate`
   plus `owned_run_ids` (`:583-597`), readback-verifies (`:613-646`), appends to the ledger's
   `_run_identities` anchor. Emits `run_id` to the supervisor.
2. **Supervisor**: carries `run_id` forward. Passes `--run-id` to every state *write*.
3. **Router call**: `sdlc-tool next-skill --issue-number N` — **today, carries no identity**.
4. **`tools/sdlc_next_skill.py::decide()`** (`:513-549`): reconstructs `peek_run_id` via
   `find_session_by_issue(N).active_run_id`. **This is the break.** Three ways to get `None`:
   terminal status, non-eng `session_type`, exception.
5. **`models/session_lifecycle.py::touch_issue_lock(..., peek=True)`** (`:1235-1250`): reads
   the lock payload. `if run_id and owner_run_id == run_id` → `acquired=True`. With
   `run_id=None` the guard short-circuits → `acquired=False`, `owner_run_id` = the caller's
   own id, `orphaned_lock = not _lock_owner_is_live(payload)` = `False` (the caller just
   renewed it).
6. **Back in `decide()`** (`:545-553`): returns `{"blocked": true, "reason": "ISSUE_LOCKED",
   "guard_id": "ISSUE_LOCK", "owner_run_id": <own>, "orphaned_lock": false}` before any
   G1-G8 guard runs.
7. **Output**: `/do-sdlc` Step 2/3a matches the "Foreign holder" row and stops the pipeline.

**After the fix**, step 3 carries `--run-id {run_id}`, step 4 uses it directly with no lookup,
step 5's guard passes, and step 6 never happens.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2003 / PR #2010 | Established run_id ownership; `--run-id` required on state-mutating subcommands only. Read-only subcommands (`stage-query`, `next-skill`, `verdict get`) take no run-id. | Conflated two different reasons a subcommand might want a run_id: **authorization to write** and **identity to compare against**. `next-skill` needs the second and was denied both, so it was given an inference path instead. |
| #1954 | Added the `next-skill` peek pre-check. | Correctly made the peek non-mutating, but sourced the peek identity from a lookup rather than from the caller — an unexamined dependency on `find_session_by_issue` succeeding. |
| #2446 / #2451 / PR #2076 | Added `owned_run_ids` and the `owner_run_id` self-identity check so a supervisor recognizes its own ghost. | Applied the check to `SUPERVISED_RUN_ACTIVE` in the skill body, not to the `ISSUE_LOCKED` rows, and not to `next-skill` at all. The self-recognition principle was established but not propagated to every site that can produce a self-lock. |
| #2675 / PR #2803 | Added the durable issue-keyed `_run_identities` anchor. | Solved durability of identity *history*; did not address a call site that never states an identity in the first place. |

**Root cause pattern:** every prior fix strengthened the *storage and corroboration* of run
identity while leaving one consumer **inferring** it. Inference has a failure mode — "not
found" — that is indistinguishable from "you are not the owner." The durable fix is to stop
inferring at the last consumer that still does, not to make the inference smarter. Making
`find_session_by_issue` more permissive would trade this bug for #1915.

## Architectural Impact

- **New dependencies**: none. `read_run_identities` (`agent/pipeline_ledger.py:397`) already
  exists and is already imported by `tools/sdlc_session_ensure.py`.
- **Interface changes**: `sdlc-tool next-skill` gains an **optional** `--run-id`.
  `tools/sdlc_next_skill.py::decide()` gains an optional `run_id` keyword. Both are additive
  and backward compatible: omitting `--run-id` preserves today's inference path exactly, with
  added diagnostic keys in the blocked payload. **Callers of `decide()` outside its own CLI
  `main()` are all in the test tree** — and there are three, two of them integration:
  `tests/integration/test_sdlc_dispatch.py:28` imports and drives `decide()` directly, `:321-350`
  shells out to `python -m tools.sdlc_next_skill` with an explicit argv list (the exact argparse
  surface being changed, so it is the argparse regression detector), and
  `tests/integration/test_sdlc_session_ensure_integration.py:451`/`:497` drive `decide()`
  end-to-end against real Redis after a real `session-ensure`. Production blast radius is a
  single file; test blast radius is unit **plus** those two integration suites, all listed in
  Test Impact.
- **Coupling**: *decreases*. `decide()`'s correctness stops depending on
  `find_session_by_issue` resolving, removing a hidden coupling between the router's lock
  pre-check and the session-lookup heuristics.
- **Data ownership**: unchanged. `next-skill` remains strictly read-only — `peek=True` on every
  path, no mint, no adopt, no renewal. `--run-id` is an *assertion to compare against*, never
  a value written anywhere.
- **Doc-contract change**: several docs currently state as a rule that `next-skill` takes no
  run-id (`docs/features/sdlc-issue-ownership-lock.md:68,247,308`,
  `.claude/skills/sdlc/SKILL.md:74`, `.claude/skills-global/do-sdlc/SKILL.md:122`, and the
  four `docs/sdlc/do-*.md` addenda). The rule must be restated precisely: **read-only
  subcommands still never mint or adopt; `next-skill` accepts an identity to compare against.**
  This is the largest surface of the change and is mostly editorial.
- **Reversibility**: high. Revert is a single-file code revert plus doc reverts; the flag is
  optional so no caller breaks either way.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The code change is one optional argument threaded through one function, plus a diagnostic key
and a doc-contract restatement. The care goes into (a) not weakening #1915's terminal filter,
(b) proving the bug red before proving it green, and (c) catching every doc that asserts the
old rule.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `sdlc-tool` on PATH | `command -v sdlc-tool` | The CLI under change |
| Redis reachable | `uv run python -c "from popoto.redis_db import POPOTO_REDIS_DB as R; R.ping()"` | Lock and session state live in Redis |
| On-pin venv | `uv run python -m tools.venv_health` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **Caller-stated identity**: `sdlc-tool next-skill` accepts an optional `--run-id`. When
  present it is the peek identity, full stop — no session lookup, no inference.
- **Preserved inference fallback**: when `--run-id` is absent, today's
  `find_session_by_issue(...).active_run_id` path runs unchanged, so every existing caller
  keeps working during and after the rollout.
- **Diagnostics on the blocked payload (no control flow)**: a `peek_identity` key records where
  the peek identity came from — `"caller"`, `"session_mirror"`, or `"unresolved"`. On the
  caller-supplied blocked path only, a `session_mirror_run_id` key additionally reports what
  `find_session_by_issue(...).active_run_id` currently says, so a supervisor whose `--run-id`
  went stale after an orphaned-lock re-ensure can see that its own id was superseded rather than
  displaced by a rival. **Both keys are diagnostic. Neither overrides a block, and neither
  triggers an automatic retry.** An `ISSUE_LOCKED` block is reported to the human as-is;
  `peek_identity: "unresolved"` is reported as *inconclusive* rather than as a confirmed rival.
  There is no supervisor recovery branch (critique concern 4 — resolves the former Open
  Question #1 in favor of keeping the cheap diagnostic and cutting the expensive control flow).
- **Self-identity belt in the skill body**: `/do-sdlc`'s Step 2 three-way table applies the
  `owner_run_id ∈ {run_ids this run has held}` check to the `ISSUE_LOCKED` rows, exactly as
  Step 3d.6 already does — so a supervisor can never be instructed to stand down for its own
  lock, even if both code paths fail.
- **Doc-contract restatement**: the "read-only subcommands take no run-id" rule is rewritten
  to distinguish *authorization to write* from *identity to compare against*.

### Flow

**Supervisor holds `run_id`** → runs `sdlc-tool next-skill --issue-number N --run-id {run_id}`
→ **peek under the caller's own identity** → lock payload's `run_id` matches → `acquired=True`
→ **G1-G8 guards evaluate normally** → dispatch decision returned.

Contrast, no `--run-id` supplied and lookup fails → **peek under `None`** → blocked →
payload carries `peek_identity: "unresolved"` → **supervisor reports the block to the human as
inconclusive rather than as a confirmed foreign holder, and stops.** No automatic re-ensure, no
automatic retry: `session-ensure` is a mutating call and an `unresolved` block is emitted on any
lookup miss, including one where a genuine rival really does hold the lock. Automatic recovery
there would turn a designed stop into repeated lock contests against a live foreign owner.

### Technical Approach

- **`tools/sdlc_next_skill.py`**
  - `main()`: add `--run-id` (optional, `metavar="ID"`, help text stating it is a read-only
    identity assertion — `next-skill` never mints, adopts, or renews). Thread into `decide()`.
  - `decide(issue_number, session_id, proposed_skill, run_id=None)`: in the pre-check block
    (currently `:530-545`), when `run_id` is truthy use it directly and set
    `peek_identity = "caller"`; skip `find_session_by_issue` entirely — one fewer linear scan
    of all eng sessions per router call, which is a small latency win too. Otherwise run the
    existing lookup, setting `peek_identity` to `"session_mirror"` on a hit and `"unresolved"`
    on a miss or exception.
  - Add `peek_identity` to the `ISSUE_LOCKED` blocked payload only. Do not add keys to the
    dispatch payload.
  - **Stale-self diagnostic (critique concern 3).** Making a supplied `--run-id` authoritative
    removes a self-heal that exists today: after an orphaned-lock re-ensure mints a NEW run_id
    (`.claude/skills-global/do-sdlc/SKILL.md:94`), a supervisor that forgot to rebind currently
    still passes the peek, because inference reads the session's *updated* `active_run_id`
    mirror. After this change it asserts its stale id, the mirror is never consulted, and the
    peek blocks. So: on the `--run-id`-supplied **blocked** path only, additionally resolve
    `find_session_by_issue(issue_number)` (default args — no `include_terminal`) and emit its
    `active_run_id` as `session_mirror_run_id`. This is **diagnostic only** and must never be
    used to override the block; overriding would violate the same boundary
    `tools/sdlc_session_ensure.py:326-334` draws for `read_run_identities`. Resolve it inside a
    `try/except` that leaves the key absent on failure — a diagnostic must never turn a block
    into an error.
  - **Do not opt into `include_terminal=True`** at this or any call site in this file. The flag
    exists (`tools/_sdlc_utils.py:255`, honored at `:340`/`:361`) and is a working one-keyword
    route back to #1915 that would leave `tools/_sdlc_utils.py` untouched and every other
    Success Criterion satisfied. There is a Verification row for it.
  - **Invariant to preserve and assert in tests**: `touch_issue_lock` is still called with
    `peek=True` on every path. The new argument changes *what identity is compared*, never
    *whether a lock is claimed or renewed*.
- **Corroboration belt (in code, optional second layer).** When the peek blocks and
  `--run-id` was supplied but did not match the live lock, leave the block as-is: a supplied
  identity that does not match a live lock is a genuine foreign holder and must stop. Do **not**
  consult `read_run_identities` to override a live lock — `tools/sdlc_session_ensure.py:326-334`
  documents why: the anchor is issue-keyed and can legitimately hold a foreign run's id, so it
  corroborates a claim only on a **free** lock and is never consulted to take over a live one.
  Respect that boundary; it is the difference between a fix and a new #1915.
- **`.claude/skills-global/do-sdlc/SKILL.md`**
  - Step 3a: pass `--run-id {run_id}` on the `next-skill` invocation; correct the "(Read-only —
    `next-skill` takes no `--run-id`.)" note at `:122`.
  - Step 2 table (`:93-95`): add the self-identity qualifier to the two `ISSUE_LOCKED` rows so
    the "Foreign holder" row reads as *foreign* only when `owner_run_id` is not one this run
    has held — matching the wording already at `:269-273`. **This file's `:95` is the site
    genuinely missing the qualifier** (`.claude/skills/sdlc/SKILL.md:202` already documents a
    "Self-owned continue path"; that one is an amendment, not an addition).
  - Document `peek_identity` and `session_mirror_run_id` as **diagnostics only**:
    `peek_identity: "unresolved"` means the block is *inconclusive* — report it to the human as
    such and stop. **Do not add an automatic re-ensure/retry branch.** `session-ensure` mutates,
    and `unresolved` is emitted on any lookup miss including a genuine rival's, so automatic
    recovery would contest a live foreign lock.
- **`.claude/skills/sdlc/SKILL.md`**
  - `:74`: restate the rule. Writes require `--run-id` (`RUN_ID_REQUIRED`); `next-skill`
    *accepts* `--run-id` as a read-only identity assertion; `stage-query`, `verdict get`, and
    `dispatch get` take none.
  - `:202`: amend the existing `ISSUE_LOCKED` paragraph — the "Self-owned continue path" wording
    is already there; add that `next-skill` now accepts `--run-id` and that `peek_identity` /
    `session_mirror_run_id` are diagnostics on the blocked payload.
  - `:239` and `:266`: add `--run-id {run_id}` to the sample invocations.
  - Check `tests/unit/test_sdlc_skill_md_parity.py` still passes — it asserts Step 4 references
    `sdlc-tool next-skill`, which an added flag does not break.
- **`docs/sdlc/do-plan.md:30`, `do-build.md:36`, `do-pr-review.md:60`,
  `do-plan-critique.md:36`** — the four addenda repeat "Read-only calls (`stage-query`,
  `verdict get`, `next-skill`) take no run-id." Update the `next-skill` clause in each.
- **`docs/features/sdlc-issue-ownership-lock.md`** — `:68`, `:247`, `:308` are the canonical
  statements of the old contract. Rewrite all three; add a short subsection explaining the
  inference-vs-assertion distinction and why widening `find_session_by_issue` is the wrong fix.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_next_skill.py:536-539` — the bare `except Exception: peek_run_id = None`
      around the session lookup. After the change this branch must set
      `peek_identity = "unresolved"` and emit a `logger.debug`. Add a test that forces
      `find_session_by_issue` to raise and asserts the blocked payload carries
      `peek_identity == "unresolved"` (observable behavior, not a silent swallow).
- [ ] `tools/sdlc_next_skill.py:605` — `decide()`'s outer `except Exception`: assert unchanged
      (still returns `{"error": ..., "dispatched": False}` and fails open toward existing
      behavior). *(The earlier citation of `:545` was wrong — `:545` is the `return {...}` of the
      blocked payload; the only local handler in that region is the `except Exception` at `:538`,
      already covered by the row above.)*
- [ ] The new `session_mirror_run_id` lookup must be wrapped so a raising
      `find_session_by_issue` leaves the key absent and the block intact — a diagnostic must
      never convert a block into an error. Add a test that forces the raise on the
      `--run-id`-supplied blocked path.
- [ ] No other exception handlers are in scope; `models/session_lifecycle.py` is not modified.

### Empty/Invalid Input Handling
- [ ] `--run-id ""` (empty string) must behave exactly as *omitted* — fall through to the
      inference path, never be compared as an identity. Add a test.
- [ ] `--run-id` with whitespace only — same disposition as empty. Strip before use.
- [ ] `--run-id` naming an id that is **not** the lock owner while the lock is live → still
      blocks with `peek_identity: "caller"`. This is the anti-regression test: the flag must
      not become a bypass.
- [ ] `next-skill` with no lock present at all and `--run-id` supplied → `acquired=True`,
      proceeds to guards. No block.

### Error State Rendering
- [ ] The blocked payload is the user-visible output. Test that `peek_identity` appears on
      `ISSUE_LOCKED` blocks and does **not** appear on dispatch results (no payload pollution).
- [ ] Exit code contract unchanged: `next-skill` exits 0 on both dispatch and block, 1 only on
      error (`docs/features/sdlc-tool-resolver.md:51`). Assert a block still exits 0.

## Test Impact

- [ ] `tests/unit/test_sdlc_next_skill.py::test_peek_never_acquires_or_renews` (around `:415`)
      — UPDATE: `lock_mock.assert_called_once_with(4002, None, session_id="sdlc-local-4002",
      peek=True)` at `:442` hard-codes `None` as the peek identity. Keep this case (it covers
      the no-`--run-id` inference path) but add the `peek_identity` assertion; the `peek=True`
      assertion must stay exactly as strict.
- [ ] `tests/unit/test_sdlc_next_skill.py` `ISSUE_LOCKED` payload assertions (around `:400-410`)
      — UPDATE: payloads now carry `peek_identity`. Assert the new key rather than asserting
      exact dict equality.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE if needed: it asserts Step 4 references
      `sdlc-tool next-skill` (`:70-76`) and that the hand-edited routing table is gone
      (`:84-97`). Adding a flag to the sample invocation should not break either; verify, do not
      assume.
- [ ] `tests/unit/test_sdlc_run_identity.py` — REVIEW: run-identity contract tests. If any
      assert that `next-skill` takes no run-id, UPDATE to the new contract.
- [ ] `tests/unit/test_sdlc_takeover_regression.py` — REVIEW and keep green: this is the
      #1915-class guard. Nothing in this plan may make it fail. If it does not already cover
      "a foreign run_id supplied via `--run-id` cannot take over a live lock," ADD that case.
- [ ] `tests/integration/test_sdlc_dispatch.py` — REVIEW: `:28` imports and drives `decide()`
      directly and `:57-64` calls it with mocked session lookup, so the added keyword must keep a
      default. `:321-350` shells out to `python -m tools.sdlc_next_skill` with an explicit argv
      list — **this is the argparse regression surface** for the new `--run-id`. Add a subprocess
      case exercising `--run-id` there.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — UPDATE: `:435-497` already
      runs a real `session-ensure` against real Redis and then calls `sdlc_next_skill.decide()`
      (`:451`, `:497`). **This is the honest home for the Risk 3 demonstrated-red proof.** A red
      produced only by `patch("tools._sdlc_utils.find_session_by_issue", return_value=None)`
      proves the mock, not the bug: it asserts that a function stubbed to return `None` causes a
      `None` peek identity, which is true by construction. The real reproduction drives a real
      lock plus a real session record forced terminal / non-`eng`.
- [ ] NEW `tests/unit/test_sdlc_next_skill.py::TestSelfLockPeekIdentity` — the red-first
      regression suite (see Success Criteria). Unit-level cases may use mocks for the branch
      matrix, but they do **not** satisfy the demonstrated-red requirement on their own.

## Rabbit Holes

- **Widening `find_session_by_issue` to see terminal or non-`eng` sessions — by editing it *or*
  by passing `include_terminal=True` at the peek call site.** It looks like the obvious one-line
  fix and it is the trap: that filter is #1915's fix, and loosening it lets a dead session be
  resolved as the live issue owner. Note that the opt-in already exists
  (`tools/_sdlc_utils.py:255`, honored at `:340`/`:361`, proven working by
  `tests/unit/test_sdlc_utils.py:309`), so this is a **live one-keyword bypass**, not a
  theoretical one: a builder could write `find_session_by_issue(n, include_terminal=True)` inside
  `tools/sdlc_next_skill.py`, reintroduce #1915, and leave `tools/_sdlc_utils.py` byte-for-byte
  unchanged — satisfying every other Success Criterion. The Verification table carries a row
  asserting the token never appears in `tools/sdlc_next_skill.py`. Fix the caller's *identity
  source*, not the lookup's *visibility*.
- **Making `next-skill` mint or adopt a run_id when it cannot resolve one.** That would make a
  read-only subcommand an identity source, breaking `session-ensure`'s exclusive-mint invariant
  and reintroducing the multi-owner class of bug wholesale.
- **Using `read_run_identities` to override a *live* lock.** The anchor is issue-keyed and can
  legitimately hold a foreign run's id (`tools/sdlc_session_ensure.py:326-334`). Consulting it
  against a live lock is a takeover primitive dressed as a self-check.
- **Repo-scoping the lock key while you are in here.** It is genuinely broken and genuinely
  tempting, and it is #2813 — a different blast radius (key format migration, every lock reader).
- **Auditing every SDLC doc for run-id statements.** Scope the doc pass to the six known sites
  listed in Technical Approach plus a single `grep` sweep. Do not turn this into a docs refactor.
- **Threading `--run-id` through `stage-query` / `verdict get` / `dispatch get` "for symmetry."**
  Only `next-skill` compares against a lock. The others have no identity comparison to make.

## Risks

### Risk 1: `--run-id` becomes an accidental lock bypass
**Impact:** If a supplied `--run-id` were treated as "trust the caller," any process could
assert an arbitrary id and peek past a genuine foreign holder, silently reintroducing
concurrent-owner corruption — the exact class #1915 and #2003 exist to prevent.
**Mitigation:** The flag changes only *which identity is compared*, never *whether the
comparison happens*. `touch_issue_lock(..., peek=True)` still returns `acquired=True` only on
`owner_run_id == run_id`. A mismatched `--run-id` against a live lock must still block. This is
an explicit test case in Failure Path Test Strategy and a Verification row, and
`tests/unit/test_sdlc_takeover_regression.py` must stay green.

### Risk 2: Doc-contract drift leaves half the skills asserting the old rule
**Impact:** A future agent reads `docs/sdlc/do-build.md:36` ("`next-skill` takes no run-id"),
omits the flag, and the bug reappears intermittently in exactly the hard-to-reproduce way it
did here.
**Mitigation:** Six named sites in Technical Approach plus a repo-wide `grep` sweep for the
phrase pattern, with a Verification row asserting zero remaining stale statements. Note the
`grep`-anti-criterion-counts-comments trap: the sweep must not match a comment that quotes the
old wording while explaining the change — paraphrase in any explanatory prose.

### Risk 3: The bug is only reachable under conditions the test suite does not naturally create
**Impact:** A patch that looks correct ships without ever having been demonstrated to fix
anything, because the happy-path suite was green before the change too. This is the
demonstrated-red requirement for guard-shaped work.
**Mitigation:** Write the regression test **first** and observe it fail on `main` before the
fix lands. The two reproducing conditions are cheap to construct with a session fixture:
`session_type != "eng"`, and terminal `status`. Paste the red output into the PR body.

### Risk 4 (dissolved by the revision): `peek_identity` as a control-flow surface
**Status:** DISSOLVED. `peek_identity` and `session_mirror_run_id` are now specified as pure
diagnostics on the `ISSUE_LOCKED` payload with no supervisor recovery branch (critique concern
4). There is no belt to fail to engage: the keys are read by a human reading a block, and by the
supervisor's existing `owned_run_ids` self-identity check, which was already in scope. The
expensive half — a `/do-sdlc` retry branch — is cut, which also removes the scope creep that put
`appetite: Small` under strain. The remaining cost is ~10 lines of code plus unit assertions.

## Race Conditions

### Race 1: Lock renewal between the caller's `session-ensure` and its first `next-skill`
**Location:** `tools/sdlc_next_skill.py:513-549` ↔ `models/session_lifecycle.py:1225-1250`
**Trigger:** `session-ensure` acquires the lock under `run_id=X`; a rival's TTL-expiry contest
takes it under `run_id=Y` before the caller's first `next-skill`.
**Data prerequisite:** the lock payload's `run_id` at peek time.
**State prerequisite:** the caller's `run_id` must be the live lock owner for the peek to pass.
**Mitigation:** Correct behavior is to **block** here — the caller genuinely lost the issue.
The fix must not paper over this: with `--run-id X` supplied and the lock held by `Y`, the peek
returns `acquired=False`, `owner_run_id=Y`, `peek_identity="caller"`, and the supervisor's
self-identity check correctly finds `Y ∉ {ids this run has held}` and stops. Explicit test case.

### Race 2: Session record goes terminal mid-run while the lock is still live
**Location:** `tools/_sdlc_utils.py:331-345` ↔ `tools/sdlc_next_skill.py:530-539`
**Trigger:** a stall advisory, a reaper, or an operator marks the session `failed`/`killed`
while its lock is still being renewed. This is the primary real-world reproduction path.
**Data prerequisite:** `session.status` at lookup time.
**State prerequisite:** none — the lock's liveness and the session's status are independent
signals and may legitimately disagree.
**Mitigation:** With `--run-id` supplied, `session.status` is never consulted for the peek, so
the disagreement cannot produce a self-block. The inference fallback still yields
`peek_identity: "unresolved"`, which the supervisor treats as inconclusive rather than as a
rival. Neither path widens the terminal filter.

### Race 3: Concurrent `next-skill` calls from supervisor and a stage fork
**Location:** `tools/sdlc_next_skill.py:513-549`
**Trigger:** two processes under the same `run_id` call `next-skill` simultaneously.
**Data prerequisite:** none beyond the lock payload.
**State prerequisite:** none.
**Mitigation:** Both peek, neither mutates. `peek=True` makes `next-skill` idempotent and
side-effect free by construction; concurrent peeks under the same identity both return
`acquired=True`. No change to this property is in scope, and it is asserted by the preserved
`test_peek_never_acquires_or_renews`.

### Race 4: Stale caller-supplied `--run-id` after an orphaned-lock re-ensure
**Location:** `tools/sdlc_next_skill.py:513-549` ↔ `.claude/skills-global/do-sdlc/SKILL.md:94`
**Trigger:** the supervisor hits the orphaned-lock row, re-ensures, and `session-ensure` mints a
**new** run_id. The supervisor fails to rebind and keeps passing its original `--run-id` to
`next-skill`.
**Data prerequisite:** the session's `active_run_id` mirror (fresh) vs. the caller's asserted id
(stale).
**State prerequisite:** the lock is live and owned by the *new* run_id, which belongs to the same
supervisor.
**Why this is new:** today this self-heals by accident — inference reads the *updated* mirror, so
the peek passes despite the supervisor's stale bookkeeping. Making `--run-id` authoritative
removes that accidental heal and turns it into a hard block. Race 1 does not cover this; Race 1
assumes a genuine rival.
**Mitigation:** the block is **correct** and stays — a run asserting an id it no longer holds
should be told so, not silently papered over. What the fix adds is legibility: on the
`--run-id`-supplied blocked path the payload carries `session_mirror_run_id`, so the supervisor's
existing `owned_run_ids` self-identity check has the data to recognize "this is my own successor
id" rather than "a rival". Diagnostic only — it never overrides the block. Explicit test case:
supply run_id `X`, lock held by `Y`, session mirror reads `Y` → blocked, `peek_identity:
"caller"`, `session_mirror_run_id: "Y"`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2813] Repo-scoping the issue-lock key (`session:issuelock:{issue_number}`)
  and `find_session_by_issue`'s `issue_url.endswith("/issues/{n}")` match. Confirmed latent
  during this investigation, already filed as #2813, and a materially different blast radius
  (key-format migration touching every lock reader and writer).
- [SEPARATE-SLUG #2813] Repo-scoping the deterministic `sdlc-local-{N}` session id.
  Same issue, same reason.

Everything else the investigation surfaced is in scope: the `--run-id` flag, the
`peek_identity` diagnostic, the `/do-sdlc` Step 2 table row, the `/sdlc` SKILL.md rule
restatement, all four `docs/sdlc/do-*.md` addenda, and
`docs/features/sdlc-issue-ownership-lock.md`.

## Update System

No update system changes required. This changes a CLI argument surface and skill/doc text
inside the repo; `/update` propagates it by the ordinary git sync it already performs.

Two notes for the builder:

- `.claude/skills-global/do-sdlc/SKILL.md` is a **global** skill, hardlinked to
  `~/.claude/skills/` by `/update` (`scripts/update/hardlinks.py`). Editing it in place is the
  correct and complete action; no new `RENAMED_REMOVALS` entry is needed because nothing moves
  between `skills-global/` and `skills/`.
- No new dependency, config file, secret, or migration. No Popoto model changes, so no entry in
  `scripts/update/migrations.py`.

## Agent Integration

No agent integration required. `sdlc-tool` is an existing bash dispatcher on PATH
(`/Users/valorengels/.local/bin/sdlc-tool`) that the agent already invokes via Bash; `next-skill`
is already in its `ALLOWED_SUBCOMMANDS` list. Adding an optional argument to an existing
subcommand needs no change to `pyproject.toml [project.scripts]`, no MCP surface, and no
`bridge/telegram_bridge.py` import.

The one integration-shaped requirement is that the *skill bodies* actually pass the new flag —
that is what makes the fix reachable in production, and it is covered by the
`.claude/skills-global/do-sdlc/SKILL.md` and `.claude/skills/sdlc/SKILL.md` edits in Technical
Approach, with a Verification row asserting the flag appears in the Step 3a invocation.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-issue-ownership-lock.md` — rewrite `:68` (read-only
      subcommands rule), `:247` (the `decide()` peek pre-check description, which explicitly
      says "not a caller-supplied `--run-id`"), and `:308` (the peek-sites table row). Add a
      subsection "Stated identity vs. inferred identity" covering the failure mode, the three
      ways `find_session_by_issue` returns `None`, `peek_identity`, and why widening the lookup
      is the wrong fix.
- [ ] Update `docs/features/sdlc-run-self-recognition.md` — add `next-skill`'s peek to the list
      of sites where a run recognizes its own identity.
- [ ] Verify `docs/features/README.md` index entries for the two files above still describe
      them accurately; update the one-liners if the scope statement drifted.

### SDLC Addenda
- [ ] Update the `next-skill` clause in `docs/sdlc/do-plan.md:30`, `docs/sdlc/do-build.md:36`,
      `docs/sdlc/do-pr-review.md:60`, `docs/sdlc/do-plan-critique.md:36`.

### Skill Bodies
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md` — Step 2 refusal table `ISSUE_LOCKED`
      rows (`:95` is the row missing the self-identity qualifier), Step 3a invocation and its
      `:122` parenthetical, plus a sentence describing `peek_identity` / `session_mirror_run_id`
      as diagnostics. **Do not add a retry or re-ensure branch.**
- [ ] Update `.claude/skills/sdlc/SKILL.md` — `:74` run-id rule, `:202` ISSUE_LOCKED paragraph
      (amendment — the self-owned continue path is already documented there), and the `:239` /
      `:266` sample invocations.

### Inline Documentation
- [ ] Docstring on `decide()` documenting the `run_id` parameter as a read-only identity
      assertion that is never minted, adopted, or written.
- [ ] Comment at the peek block explaining why a supplied `--run-id` skips the session lookup
      and why the lookup is *not* widened instead (cite #1915). **Do not write the literal token
      `peek=False` or `include_terminal` in this prose** — the anti-criterion sweeps below are
      substring greps and would match your own explanatory comment (the same trap Risk 2 warns
      about). Say "the peek argument is never inverted" and "the terminal filter is never opted
      out of" instead.
- [ ] Help text on `--run-id` in `main()` stating it is read-only.

## Success Criteria

- [x] A regression test reproduces the self-block on `main` **before** the fix: with the lock
      held under the caller's own `run_id` and the session invisible to `find_session_by_issue`
      (terminal status; and separately, non-`eng` `session_type`), `decide()` returns
      `ISSUE_LOCKED` with `owner_run_id` equal to the caller's own id and
      `orphaned_lock: false`. Red output pasted into the PR body. **The demonstrated-red case
      lives in `tests/integration/test_sdlc_session_ensure_integration.py`** driving a real
      `session-ensure` and a real session record forced terminal/non-`eng`; a red produced by
      patching `find_session_by_issue` to return `None` proves the mock, not the bug.
- [x] The same test passes after the fix when `--run-id` is supplied.
- [x] Control case: a **foreign** `run_id` supplied via `--run-id` against a live lock still
      blocks. The flag is not a bypass.
- [x] `--run-id ""` and whitespace-only behave exactly as omitted.
- [x] `touch_issue_lock` is still called with `peek=True` on every path
      (`test_peek_never_acquires_or_renews` green, unweakened).
- [x] `find_session_by_issue`'s eng-only and non-terminal filters are byte-for-byte unchanged,
      **and `include_terminal` is never passed from `tools/sdlc_next_skill.py`** (the one-keyword
      bypass).
- [x] `peek_identity` appears on `ISSUE_LOCKED` payloads and nowhere else. `session_mirror_run_id`
      appears only on `ISSUE_LOCKED` payloads produced on the `--run-id`-supplied path, and never
      overrides the block.
- [x] `.claude/skills-global/do-sdlc/SKILL.md` contains **no** automatic re-ensure/retry branch
      keyed on `peek_identity` — an `unresolved` block is reported to the human, not retried.
- [x] `.claude/skills-global/do-sdlc/SKILL.md` Step 3a passes `--run-id`, and its Step 2
      `ISSUE_LOCKED` rows carry the self-identity qualifier.
- [x] No doc or skill file still asserts that `next-skill` takes no run-id.
- [x] `tests/unit/test_sdlc_takeover_regression.py` green.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)
- [x] No xfail conversions required — the expected-failure search over `tests/` found no
      `pytest.mark.xfail` or runtime `pytest.xfail()` related to the issue lock, run identity,
      or `next-skill`.

## Team Orchestration

- **Builder (next-skill run identity)**
  - Name: `next-skill-builder`
  - Role: Thread the optional `--run-id` through `main()` → `decide()` → the peek, add the
    `peek_identity` diagnostic, preserve every read-only invariant.
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto data — the change sits on a distributed lock;
    paste the matching rules from `DOMAIN_FRAMING.md` into the assignment.
  - Resume: true

- **Test engineer (red-first regression)**
  - Name: `self-lock-test-engineer`
  - Role: Write the reproducing tests **first**, demonstrate red on `main`, then green after
    the fix. Own the bypass control case and the empty-string cases.
  - Agent Type: test-engineer
  - Resume: true

- **Builder (skill bodies and doc contract)**
  - Name: `contract-doc-builder`
  - Role: Update the six known doc/skill sites plus the `grep` sweep; keep the parity test green.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `self-lock-validator`
  - Role: Verify every Success Criteria row, especially that the terminal filter is unchanged
    and the flag is not a bypass.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Red-first regression tests
- **Task ID**: test-red-repro
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_next_skill.py`,
  `tests/integration/test_sdlc_session_ensure_integration.py`
- **Assigned To**: `self-lock-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestSelfLockPeekIdentity` to `tests/unit/test_sdlc_next_skill.py` covering: terminal-status
  session, non-`eng` session_type, and lookup-raises — each with the lock held under the caller's
  own run_id.
- **The demonstrated-red proof goes in `tests/integration/test_sdlc_session_ensure_integration.py`**
  alongside the existing `:435-497` block, which already runs a real `session-ensure` against real
  Redis and then calls `sdlc_next_skill.decide()`. Force the session record terminal (and,
  separately, non-`eng`) while the lock stays live, then call `decide()`. A red produced by
  `patch("tools._sdlc_utils.find_session_by_issue", return_value=None)` proves the mock, not the
  bug — it is not accepted as the red.
- Assert the failing shape: `reason == "ISSUE_LOCKED"`, `owner_run_id == <own>`,
  `orphaned_lock is False`.
- Run against `main`, capture the FAIL output verbatim for the PR body. This is the
  demonstrated-red proof; a fix that was never shown to turn a red test green is not accepted.
- Use `scripts/pytest-clean.sh`, never bare `pytest`.

### 2. Thread `--run-id` through next-skill
- **Task ID**: build-run-id-flag
- **Depends On**: test-red-repro
- **Validates**: `tests/unit/test_sdlc_next_skill.py`, `tests/unit/test_sdlc_run_identity.py`,
  `tests/unit/test_sdlc_takeover_regression.py`
- **Assigned To**: `next-skill-builder`
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data
- **Parallel**: false
- Add optional `--run-id` to `main()` in `tools/sdlc_next_skill.py`; strip whitespace and treat
  empty as absent.
- Add `run_id: str | None = None` to `decide()`; when truthy, use it as `peek_run_id` and skip
  `find_session_by_issue` entirely.
- Set `peek_identity` to `"caller"` / `"session_mirror"` / `"unresolved"`; include it in the
  `ISSUE_LOCKED` payload only.
- On the `--run-id`-supplied **blocked** path only, add `session_mirror_run_id` from
  `find_session_by_issue(issue_number).active_run_id`, wrapped in `try/except` so a failure
  leaves the key absent rather than turning the block into an error. Diagnostic only — it never
  overrides the block.
- Log a `logger.debug` on the `"unresolved"` branch — no silent swallow.
- Do **not** touch `find_session_by_issue`, `touch_issue_lock`, or any `peek=True` argument.
- Do **not** pass `include_terminal=True` anywhere in this file — it is a working one-keyword
  route back to #1915 that leaves `tools/_sdlc_utils.py` untouched.
- Do **not** consult `read_run_identities` against a live lock.

### 3. Green + control cases
- **Task ID**: test-green-and-control
- **Depends On**: build-run-id-flag
- **Validates**: `tests/unit/test_sdlc_next_skill.py`
- **Assigned To**: `self-lock-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Confirm the Task 1 cases pass when `--run-id` is supplied.
- Add the bypass control: foreign `run_id` + live lock → still blocked, `peek_identity: "caller"`.
- Add the Race 4 stale-self case: `--run-id X`, lock held by `Y`, session mirror reads `Y` →
  still blocked, `peek_identity: "caller"`, `session_mirror_run_id: "Y"`.
- Add the diagnostic-failure case: `find_session_by_issue` raises on the `--run-id`-supplied
  blocked path → key absent, block intact, no error payload.
- Add `--run-id ""` and whitespace-only cases → behave as omitted.
- Update `test_peek_never_acquires_or_renews`'s `assert_called_once_with` for the new signature
  while keeping the `peek=True` assertion exactly as strict.
- Add a subprocess case in `tests/integration/test_sdlc_dispatch.py` (near `:321-350`) exercising
  `python -m tools.sdlc_next_skill ... --run-id ...` — the argparse regression surface.

### 4. Skill bodies and doc contract
- **Task ID**: build-doc-contract
- **Depends On**: build-run-id-flag
- **Validates**: `tests/unit/test_sdlc_skill_md_parity.py`
- **Assigned To**: `contract-doc-builder`
- **Agent Type**: documentarian
- **Parallel**: true
- `.claude/skills-global/do-sdlc/SKILL.md`: Step 3a invocation + `:122` parenthetical; Step 2
  table `ISSUE_LOCKED` rows gain the self-identity qualifier (`:95` is the one actually missing
  it); document `peek_identity` and `session_mirror_run_id` as diagnostics. **Add no retry or
  re-ensure branch** — an `unresolved` block is reported to the human as inconclusive and stops.
- `.claude/skills/sdlc/SKILL.md`: `:74` rule, `:202` paragraph (amendment — the self-owned
  continue path is already documented there), `:239` / `:266` samples.
- `docs/sdlc/do-plan.md`, `do-build.md`, `do-pr-review.md`, `do-plan-critique.md`: the
  `next-skill` clause in each.
- Run a repo-wide `grep` sweep for the old assertion; paraphrase in any explanatory prose so the
  sweep does not match your own comment.

### 5. Feature documentation
- **Task ID**: document-feature
- **Depends On**: build-doc-contract, test-green-and-control
- **Assigned To**: `contract-doc-builder`
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/sdlc-issue-ownership-lock.md`: rewrite `:68`, `:247`, `:308`; add the
  "Stated identity vs. inferred identity" subsection.
- `docs/features/sdlc-run-self-recognition.md`: add the `next-skill` peek site.
- Verify `docs/features/README.md` index one-liners still describe both files accurately.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: test-green-and-control, build-doc-contract, document-feature
- **Assigned To**: `self-lock-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm `find_session_by_issue`'s filters are unchanged: `git diff main...HEAD --stat --
  tools/_sdlc_utils.py` empty (three-dot, not two-dot), **and** `include_terminal` absent from
  `tools/sdlc_next_skill.py`.
- Confirm the flag is not a bypass.
- Confirm no automatic retry/re-ensure branch was added to the `/do-sdlc` skill body.
- Report pass/fail per Success Criteria row.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Self-lock regression green | `scripts/pytest-clean.sh tests/unit/test_sdlc_next_skill.py -q` | exit code 0 |
| Takeover regression green | `scripts/pytest-clean.sh tests/unit/test_sdlc_takeover_regression.py -q` | exit code 0 |
| Run-identity contract green | `scripts/pytest-clean.sh tests/unit/test_sdlc_run_identity.py -q` | exit code 0 |
| SKILL.md parity green | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| `--run-id` exists on next-skill | `sdlc-tool next-skill --help` | output contains `--run-id` |
| do-sdlc Step 3a passes the flag | `grep -c -- '--run-id' .claude/skills-global/do-sdlc/SKILL.md` | output > 0 |
| Integration suites green | `scripts/pytest-clean.sh tests/integration/test_sdlc_dispatch.py tests/integration/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| Terminal filter untouched (three-dot, so unrelated main commits don't redden it) | `git diff main...HEAD --stat -- tools/_sdlc_utils.py` | empty output |
| No `include_terminal` bypass at the call site (anti-criterion) | `grep -c 'include_terminal' tools/sdlc_next_skill.py \|\| true` | `0` |
| Peek is still read-only (anti-criterion, comment lines excluded per the nit) | `grep -vE '^[[:space:]]*#' tools/sdlc_next_skill.py \| grep -c 'peek=False' \|\| true` | `0` |
| No lock-key repo-scoping crept in (anti-criterion for the #2813 No-Go) | `git diff main...HEAD -- models/session_lifecycle.py \| grep -c 'issuelock' \|\| true` | `0` |
| No doc or skill file still asserts `next-skill` takes no run-id | `grep -rn --include='*.md' -e 'take no run-id' -e 'takes no run-id' -e 'no run-id at all' .claude/ docs/ \| grep -- 'next-skill' \| grep -v 'docs/plans/' \| wc -l` | `0` |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

**Notes on the sweep rows.** The `grep -- 'next-skill'` filter on the doc-sweep row is
load-bearing: without it, `docs/sdlc/plan-revising-lock.md:24` and the `tools/sdlc_dispatch.py` /
`tools/sdlc_verdict.py` docstrings match on correct statements about *other* subcommands and the
row can never go green. The `grep -v 'docs/plans/'` exclusion is equally load-bearing: this plan
document itself quotes the old wording repeatedly. Every `grep -c` row carries `|| true` because
`grep -c` exits 1 on a zero count, which is the passing case here and would abort a `set -e`
script. The two `git diff` rows use three-dot `main...HEAD` (merge-base) rather than two-dot: the
files are high-traffic SDLC substrate — the Freshness Check above lists five commits touching
them in the days before this plan — and a two-dot diff would go red on someone else's unrelated
change, training everyone to ignore the guard.

## Critique Results

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | Anti-regression guard has a one-keyword bypass: `find_session_by_issue` already exposes an opt-in `include_terminal: bool = False` (`tools/_sdlc_utils.py:255`, honored at `:340`/`:361`). A builder can call `find_session_by_issue(n, include_terminal=True)` from inside `tools/sdlc_next_skill.py`, reintroduce #1915, and still satisfy every Success Criterion and every Verification row, because `tools/_sdlc_utils.py` stays untouched. | ✅ Rabbit Holes (widening entry, rewritten in terms of the call site); Technical Approach (explicit "do not pass `include_terminal=True`"); Task 2; Verification row `grep -c 'include_terminal' tools/sdlc_next_skill.py` == 0; Success Criteria | Add a Verification row `grep -c 'include_terminal' tools/sdlc_next_skill.py` expecting `0`, and restate the Rabbit Hole in terms of the *call site*: "Widening the lookup **or opting into `include_terminal=True` at the peek call site**". `tests/unit/test_sdlc_utils.py:309` already proves the flag works, so this is a live bypass, not a theoretical one. |
| CONCERN | Risk & Robustness + Scope & Value | The `peek_identity: "unresolved"` recovery contract is stated two incompatible ways: Solution/Flow says "re-ensure and retry rather than abort" (unbounded), Technical Approach says "re-ensure, adopt, retry once". `unresolved` is emitted on any lookup miss regardless of whether a genuine rival holds the lock, and `session-ensure` is a mutating call, so the unbounded reading turns a designed stop into repeated lock contests against a live foreign owner. | ✅ Flow + Technical Approach (both rewritten: report inconclusive, stop; no automatic re-ensure or retry); dissolved together with concern 4 | Pin one bounded contract in both places: at most ONE re-ensure + ONE retry of `next-skill`; if the second call still returns `ISSUE_LOCKED`, stop and report regardless of `peek_identity`. The re-ensure must be a bare `sdlc-tool session-ensure --issue-number N` (which itself refuses to steal a live lock), never a force/steal variant. Delete the unbounded phrasing from the Flow section so the two statements cannot drift. |
| CONCERN | Risk & Robustness | Making a supplied `--run-id` authoritative ("skip `find_session_by_issue` entirely") creates a NEW hard-block case that today self-heals. `.claude/skills-global/do-sdlc/SKILL.md:94` documents that an orphaned-lock re-ensure mints a new run_id and every downstream call must rebind. A supervisor that fails to rebind currently still passes the peek, because inference reads the session's *updated* `active_run_id` mirror; after this change it asserts its stale id, the mirror is never consulted, and the peek blocks. | ✅ Technical Approach (`session_mirror_run_id` diagnostic on the `--run-id`-supplied blocked path); new **Race 4**; Task 2; Task 3 test case | On the `--run-id`-supplied blocked path ONLY, additionally resolve `find_session_by_issue(...).active_run_id` and emit it as `session_mirror_run_id` — diagnostic only, never used to override the block (overriding would violate the `read_run_identities` boundary at `tools/sdlc_session_ensure.py:326-334`). The supervisor's `owned_run_ids` self-identity check then has the data to recognize a stale-self block. Add this as an explicit Race Conditions scenario; Race 1 does not cover it (it assumes a genuine rival). |
| CONCERN | Scope & Value | `peek_identity` plus its "inconclusive" handling is roughly half the surface of a plan whose `appetite: Small` is justified as "one optional argument threaded through one function": a payload key, a `/do-sdlc` skill-body paragraph, a new control-flow branch in the supervisor loop, and four Failure Path test rows. Its value is zero for any caller passing `--run-id`, which this same plan makes every caller do. Risk 4 concedes it may be dead on arrival and mitigates with more scope. Open Question #1 leaves it undecided. | ✅ Solution Key Elements + Flow + Technical Approach + Documentation + Task 4 (diagnostic keys kept, supervisor control-flow branch cut); Risk 4 marked DISSOLVED; Open Question #1 resolved | Keep the cheap half, cut the expensive half: retain `peek_identity` as a pure diagnostic key on the `ISSUE_LOCKED` payload with its unit assertions (~10 lines, no behavior change), and REMOVE the "re-ensure, adopt, retry once" branch from `.claude/skills-global/do-sdlc/SKILL.md`. Instead have the supervisor report an `unresolved` block to the human as inconclusive with no automatic recovery. This decides Open Question #1, eliminates the retry branch (concern 2), and dissolves Risk 4. |
| CONCERN | Scope & Value | Risk 2's mitigation promises "a Verification row asserting zero remaining stale statements" and Success Criteria carries "No doc or skill file still asserts that `next-skill` takes no run-id", but no such row exists in the Verification table — the plan's most drift-prone deliverable is unverifiable. The sweep as described also false-positives: `tests/unit/test_sdlc_dispatch.py:318` says "Read-only subcommands take no run-id", a CORRECT statement about `dispatch get`. | ✅ New Verification row with the `next-skill` filter and `docs/plans/` exclusion, plus the sweep-notes paragraph under the table | Add the row: `grep -rn --include='*.md' -e 'take no run-id' -e 'takes no run-id' -e 'no run-id at all' .claude/ docs/ ; grep -- 'next-skill' ; grep -v 'docs/plans/' ; wc -l` expecting `0`. The `next-skill` filter is load-bearing — without it `docs/sdlc/plan-revising-lock.md:24` and the `tools/sdlc_dispatch.py` / `tools/sdlc_verdict.py` docstrings match and stay red forever. The `docs/plans/` exclusion is also required: this plan quotes the old wording eight times. |
| CONCERN | History & Consistency | Architectural Impact asserts "`decide()` has no in-repo Python callers outside its own CLI `main()` (verified by grep)". False for the test tree, and the omission propagates into Test Impact (unit tests only): `tests/integration/test_sdlc_dispatch.py:28` imports and drives `decide()`, `:321-350` shells out to `python -m tools.sdlc_next_skill` with an argv list (the exact argparse surface being changed), and `tests/integration/test_sdlc_session_ensure_integration.py:435-497` drives `decide()` end-to-end after a real `session-ensure`. | ✅ Architectural Impact corrected (three test callers named); Test Impact gains both integration suites; Success Criteria + Task 1 relocate the demonstrated-red proof to the integration path | Add both suites to Test Impact: `tests/integration/test_sdlc_dispatch.py` (REVIEW — the subprocess CLI invocation at `:321-350` is the argparse regression surface) and `tests/integration/test_sdlc_session_ensure_integration.py` (UPDATE — `:435-497` already runs `session-ensure` then `decide()` against real Redis and is the natural home for the Risk 3 demonstrated-red case). A red produced only by `patch("tools._sdlc_utils.find_session_by_issue", return_value=None)` proves the mock, not the bug. |
| CONCERN | History & Consistency | The two anti-criterion Verification rows use two-dot `git diff main -- <path>`, which diffs the working tree against main's CURRENT tip. Once main advances with any unrelated commit touching `tools/_sdlc_utils.py` or `models/session_lifecycle.py` — both high-traffic SDLC substrate, and the plan's own Freshness Check lists five such commits from the preceding days — the rows go red on someone else's change and the guard gets trained to be ignored. | ✅ Both anti-criterion rows rewritten to `main...HEAD`; `|| true` added to every `grep -c` row; rationale in the sweep-notes paragraph; Task 6 updated | Use three-dot merge-base semantics: `git diff main...HEAD --stat -- tools/_sdlc_utils.py` expecting empty output, and `git diff main...HEAD -- models/session_lifecycle.py ; grep -c 'issuelock'` expecting `0`. Note the second row's `grep -c` exits 1 on a zero count, so under `set -e` it must be run with `\|\| true` or compared on stdout rather than exit status. |
| NIT | Scope & Value | The Verification row `grep -c 'peek=False' tools/sdlc_next_skill.py` expecting `0` is the same anti-criterion-counts-comments trap Risk 2 warns about, applied to a different sweep: the plan mandates (Documentation → Inline Documentation) a comment at the peek block explaining the change, and a natural phrasing of that comment trips the check. | ✅ Verification row now excludes comment lines; Inline Documentation checkbox forbids the literal tokens `peek=False` and `include_terminal` in prose | Either scope the grep to non-comment lines (`grep -vE '^[[:space:]]*#' tools/sdlc_next_skill.py ; grep -c 'peek=False'`) or instruct the Inline Documentation checkbox never to write the literal token `peek=False` in prose — say "the peek argument is never inverted" instead. |
| NIT | History & Consistency | Two citation slips. (1) Failure Path Test Strategy calls `tools/sdlc_next_skill.py:545` "the peek's own `except` path"; `:545` is the `return {...}` of the blocked payload, the only local handler in that region is the `except Exception` at `:538`, and the next enclosing one is `decide()`'s outer handler at `:605`. (2) Technical Approach lists `.claude/skills/sdlc/SKILL.md` sites as `:74`, `:239`, `:266`, while Documentation and Task 4 also list `:202`. | ✅ Failure Path checkbox restated as `tools/sdlc_next_skill.py:605` (`decide()`'s outer handler); `:202` added to the Technical Approach site list, flagged as an amendment; `.claude/skills-global/do-sdlc/SKILL.md:95` named as the row genuinely missing the qualifier | Restate the checkbox as "`tools/sdlc_next_skill.py:605` — `decide()`'s outer `except Exception`: assert unchanged (still fails open toward existing behavior)", and add `:202` to the Technical Approach site list so all three agree. Note for the builder: `:202` ALREADY documents the "Self-owned continue path" for `ISSUE_LOCKED`, so that edit is an amendment; the file genuinely missing the self-identity qualifier is `.claude/skills-global/do-sdlc/SKILL.md:95`. |

---

## Resolved Questions

Both former Open Questions are resolved by this revision pass. Nothing remains blocking.

1. **`peek_identity` on the wire — keep or drop?** *(was Open Question #1; resolved by critique
   concern 4.)* **Split the difference: keep the diagnostic, cut the control flow.**
   `peek_identity` stays as a key on the `ISSUE_LOCKED` payload (with `session_mirror_run_id`
   added per concern 3) because it costs roughly ten lines and no behavior change, and it makes a
   block legible to a human and to the supervisor's existing `owned_run_ids` self-identity check.
   The `/do-sdlc` "re-ensure, adopt, retry once" branch is **removed from scope**: its two
   statements had already drifted into incompatible unbounded and bounded readings, `unresolved`
   is emitted on any lookup miss including a genuine rival's, and `session-ensure` mutates — so
   the branch turned a designed stop into repeated lock contests against a live foreign owner.
   An `unresolved` block is now reported to the human as inconclusive, and stops. This is also
   what returns the plan to a defensible `appetite: Small`.

2. **Should `--run-id` be required rather than optional on `next-skill`?** **Optional, confirmed.**
   Required would break ad-hoc human inspection (`sdlc-tool next-skill --issue-number N`) and
   would be non-backward-compatible mid-flight for lanes already running under the old contract.
   The inference fallback is preserved unchanged, so no existing caller breaks; the skill-body
   edits are what make every production caller pass the flag, and the `peek_identity` diagnostic
   is what makes an inference-path block legible when one does not.
