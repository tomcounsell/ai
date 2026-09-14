---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2124
last_comment_id:
---

# SDLC Fork Artifact-Grounding Guards

## Problem

A forked SDLC stage can complete and hand a structurally-valid verdict back to
the supervising pipeline **without ever having produced the verifiable artifact
that gives the verdict its meaning**. Three live incidents in the same failure
family:

- **#2124 / PR #2121 (CRITIQUE):** The `plan-reviewer` fork returned a complete-
  looking critique that was entirely fabricated — it reviewed a *different,
  nonexistent plan* and made zero grounded reads of the real plan. The
  supervising dev only caught it by manually checking tool-call counts. A
  fabricated `READY TO BUILD` (or a fabricated blocker) would have steered the
  pipeline on a lie.
- **#2112 / PR #2134 (REVIEW):** The forked `/do-pr-review` returned **twice**,
  both times ending with "judges are still running, I will aggregate/post/record
  once they return" — while no `## Review:` comment was ever posted and
  `sdlc-tool verdict get --stage REVIEW` was empty. The fork exited while its
  background judge subagents were still in flight; the children died with the
  fork, so nothing ever landed.
- **#2026 latest (MERGE, 2026-07-16 / PR #2125):** After a Claude Code process
  restart, a lane's worktree HEAD was left detached at a PR branch head. The
  subsequent docs-cascade `git push` to main therefore carried the PR branch
  ancestry, and GitHub registered it as the PR merge (merge commit `f08bd7bf`) —
  no `gh pr merge --squash` ever ran. Benign this time (all gates were green
  before the push), but the mechanism is a merge-gate bypass class distinct from
  the known GitHub squash-UI bypass.

**Current behavior:** Grounding is enforced only by whichever human/supervisor
happens to inspect tool-call counts or notice an empty verdict store. The gates
trust the fork's self-report.

**Desired outcome:** Each stage fork's verdict/completion is refused
(fail-closed, with a named error) unless the fork produced its required
verifiable artifact — a plan-grounded critique, a readable CRITIQUE verdict, a
posted+recorded REVIEW artifact — and a push to main cannot silently register an
open PR's ancestry as its merge.

## Freshness Check

**Baseline commit:** `eef4ad5b6` (origin/main HEAD at plan time)
**Issue filed at:** #2124 — 2026-07-16T10:38:53Z; #2026 latest comment 2026-07-16.
**Disposition:** Unchanged

**File:line references re-verified (against live main):**
- `.claude/agents/plan-reviewer.md` — tool list is `read,grep,find,ls`; body does
  not force a read of the plan path. Still holds. Critics are additionally
  dispatched by the critique SKILL with plan text passed *inline* (Step 1.5
  SOURCE_FILES), so "zero file reads by the critic fork" is a by-design
  possibility, not proof of fabrication — the real grounding signal is
  plan-citation evidence, not tool-call count.
- `tools/critique_roster_check.py::evaluate()` — reads each `{name}.result.md`
  and checks a terminal two-line fence. Confirmed; this is the natural home for
  a grounding sub-check.
- `tools/sdlc_stage_marker.py::_review_verdict_readable()` (WS3c / #2062) — the
  marker-completed ⇒ verdict-readable precedent for REVIEW. Confirmed present;
  CRITIQUE has no analogue yet.
- `tools/sdlc_verdict.py` — CRITIQUE verdict already stores an `artifact_hash`
  of the plan body (`compute_plan_body_hash`). Confirmed. This ties the verdict
  to the plan bytes but does not prove the *critics* read it.
- `.claude/hooks/validators/validate_merge_guard.py` — fires only on
  `gh pr merge` commands (`_MERGE_CMD_RE`). Confirmed: a plain `git push` to main
  is NOT covered, so #2026's push-ancestry bypass is genuinely unguarded.
- `tools/doctor.py::install_pre_push_hook()` — an opt-in pre-push hook exists
  (runs `doctor --quick`). Confirmed; WS-E extends this path.

**Cited sibling issues/PRs re-checked:**
- PR #2076 (umbrella #2026 5-workstream hardening) — MERGED 2026-07-14. Its WS3c
  and WS5 guards are the precedents this plan extends; NOT to be redone.
- PR #2121 (#2120 lane) — surfaced the fabricated critique. PR #2134 (#2112) —
  surfaced the in-flight-judge REVIEW miss. Both merged; the incidents are
  post-merge observations, not open regressions.

**Commits on main since issues filed (touching referenced files):** none touching
`tools/critique_roster_check.py`, `tools/sdlc_stage_marker.py`, the critique/review
skills, or the merge guard. Baseline `eef4ad5b6` is only a dependency bump.

**Active plans in `docs/plans/` overlapping this area:** none open. The
`sdlc-fork-supervision-hardening` plan (PR #2076) is in `completed/`.

**Notes:** The umbrella #2026 work is done; this plan addresses only the NEW
open #2026 comment (the merge-by-push class) plus #2124.

## Prior Art

- **PR #2076 / #2026 umbrella**: Shipped WS1-WS5 fork/supervisor hardening —
  single-owner lease, in-turn synchronous mandate, verdict-gated routing,
  revision-latch fix, and the docs-fork zero-tool-call guard. **This plan extends
  two of its patterns** (WS3c readability invariant, WS5 zero-tool/artifact
  guard) to CRITIQUE and to the push path; it does not touch the shipped legs.
- **#2062 (WS3c)**: `sdlc_stage_marker._review_verdict_readable` — REVIEW
  `completed` refused (named `REVIEW_VERDICT_MISSING`, exit 1, fail-closed) when
  no substrate verdict is readable. Direct template for WS-C's CRITIQUE analogue.
- **#2022 (WS5)**: do-build Step 3.5 "tool-availability mismatch" — a child whose
  final message is a bare shell command with zero tool calls is re-dispatched
  once on a Bash-capable type, then failed loudly. Template for the
  fork-produced-no-artifact posture WS-A and WS-D adopt.
- **#1690**: the artifact-based critique roster barrier (`critique-roster-check`,
  `_roster.json`, terminal two-line fence). WS-A adds a grounding leg to this
  existing gate rather than inventing a parallel one.
- **#1932 / #1897**: REVIEW verdict-gated routing — merge-ready requires a
  recorded APPROVED verdict, not just `REVIEW==completed`. WS-D's artifact-
  presence check composes with, and does not replace, this.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #2076 WS3c (#2062) | Refuse REVIEW `completed` without a readable *verdict* | Covers the verdict *record*, not the posted GitHub review artifact, and does not cover CRITIQUE at all. #2112's fork exited before recording the verdict — WS3c's refusal fired correctly (fail-closed), but the fork still wasted a full cycle because nothing stopped it from *spawning un-awaited background judges* in the first place. |
| PR #2076 WS5 (#2022) | Zero-tool-call guard on the DOCS fork | Scoped to a bare-shell-command final message; a *fabricated* critique produces rich prose, not a bare command, so the WS5 heuristic does not catch it. Grounding needs a plan-citation check, not a shell-command check. |
| Merge guard (#2003) | Block `gh pr merge` unless the predicate passes | Only pattern-matches `gh pr merge`. A `git push` to main carrying PR ancestry never invokes `gh pr merge`, so the guard never runs — the #2026 bypass. |

**Root cause pattern:** each stage gate trusts the fork's *report of completion*
rather than independently verifying the *artifact* the completion is supposed to
have produced. The fix is to make each completion contingent on a fail-closed,
externally-verifiable artifact check.

## Architectural Impact

- **New dependencies:** none. All checks use `git`, `gh`, and existing substrate
  (`PipelineLedger`, `sdlc_verdict`, `critique_roster_check`).
- **Interface changes:**
  - `tools/critique_roster_check.py::evaluate()` gains an optional
    `plan_path`/`plan_text` parameter and a per-member grounding leg. Callers
    that pass no plan get today's behavior (backward compatible).
  - `tools/sdlc_stage_marker.py` gains a `_critique_verdict_readable` probe
    mirroring `_review_verdict_readable`, gating the CRITIQUE `completed` write.
  - New CLI `sdlc-push-guard` (`tools/push_ancestry_guard.py`) for WS-E.
- **Coupling:** WS-A keeps the grounding check inside the existing roster gate
  (no new gate surface). WS-E adds one skill-agnostic guard reused by the
  pre-push hook AND the docs-cascade push step (defense in depth).
- **Data ownership:** unchanged — verdicts/markers still owned by the
  issue-keyed `PipelineLedger`; the push guard reads only `gh`/`git`.
- **Reversibility:** each workstream is independently revertible; all are
  additive gates that fail toward "refuse + redirect", never toward silent pass.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (plan sign-off before build; PR before merge)
- Review rounds: 1 (SDLC `/do-pr-review` gate)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo `gh` auth | `gh auth status` | WS-D/WS-E query PR state |
| Python venv | `python -c "import tools.critique_roster_check"` | Modules import |

No external secrets required — all guards run against local git/gh and Redis
substrate already present.

## Solution

### Key Elements

- **WS-A — CRITIQUE grounding leg** (core of #2124): each critic result file
  must carry a verbatim plan citation that actually appears in the plan text;
  `critique-roster-check` verifies it. A result with zero verifiable citations is
  treated exactly like a missing critic — bounded re-dispatch, then a loud
  `MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP.
- **WS-B — worktree-cwd path resolution**: resolve `PLAN_PATH` to an absolute
  path before the existence check and before it is passed to critics/SOURCE_FILES,
  so a repo-root-relative plan path is never unresolvable from a
  `.claude/worktrees/agent-*` cwd (the "improvise instead of fail loudly" root
  cause).
- **WS-C — CRITIQUE verdict/marker readability invariant**: mirror WS3c — refuse
  a CRITIQUE `completed` marker (named `CRITIQUE_VERDICT_MISSING`, exit 1,
  fail-closed) when no readable CRITIQUE substrate verdict exists.
- **WS-D — REVIEW no-un-awaited-children + artifact presence**: the review skill
  must not spawn background judges it does not await before returning, and REVIEW
  `completed` is refused unless a posted review artifact (GitHub review or
  `## Review:` comment) is verifiable — mirroring WS5's artifact-presence side and
  composing with WS3c's verdict-readable leg.
- **WS-E — push-ancestry merge-bypass guard** (#2026): a new `sdlc-push-guard`
  CLI refuses a push to `main` when HEAD is at or descended from the head of any
  OPEN PR branch unless a merge lease/verdict authorizes it; wired into both the
  installed pre-push hook body and the docs-cascade push step.

### Flow

CRITIQUE fork → critics write result files → **roster gate now also checks each
file cites the real plan** → missing OR ungrounded critic → bounded re-dispatch →
still bad → `MAJOR REWORK (CRITIQUE INCOMPLETE)` → router re-plans. On grounded
completion → verdict recorded → **CRITIQUE `completed` marker refused unless that
verdict reads back** → marker written.

REVIEW fork → judges run **in the foreground / awaited in-turn** → verdict
recorded AND review artifact posted → **REVIEW `completed` refused unless BOTH the
verdict reads back (WS3c) AND a posted review artifact exists (WS-D)**.

Any `git push origin main` → **`sdlc-push-guard` checks HEAD ancestry vs open PR
heads** → detached-at/descended-from an open PR head with no merge authorization
→ refuse (exit 1) → operator must `gh pr merge` through the gate.

### Technical Approach

- **WS-A grounding definition (deliberately conservative to avoid false
  refusals):** a critic result file is "grounded" iff it contains at least one
  normalized substring of length ≥ N (provisional `MIN_GROUNDING_QUOTE_LEN`,
  env-overridable) that appears verbatim in the normalized plan text, OR cites a
  section header that exists in the plan. Normalization: collapse runs of
  whitespace, casefold. Rationale: a genuine critic quotes/section-refs the plan
  it read; a fork that reviewed a *nonexistent* plan cannot produce a substring
  that collides with the real plan bytes. The `LOCATION:`/`SUGGESTION:` critic
  format already nudges critics to cite; WS-A's CRITICS.md edit makes a verbatim
  citation a hard contract, and the check is the enforcement. `No findings.` is
  still valid but must be accompanied by at least one grounding citation line
  (the critic asserts what it read).
- **WS-A wiring:** extend `critique_roster_check.evaluate()` with an optional
  `plan_path`. When provided, a member is "complete" iff it passes the terminal
  fence AND the grounding check. The CLI gains `--plan-path`; the addendum's
  `critique-roster-check --run-dir ...` invocation passes `--plan-path
  "$PLAN_PATH"`. When `--plan-path` is omitted, behavior is byte-identical to
  today (generic/foreign-repo safety).
- **WS-B:** in do-plan-critique Plan Resolution, after resolving `PLAN_PATH`,
  canonicalize to an absolute path rooted at the repo (git rev-parse
  --show-toplevel), assert existence there, and pass the absolute path into
  SOURCE_FILES and every critic prompt. Keep the loud `exit 1` on a missing file.
- **WS-C:** add `_critique_verdict_readable(issue_number)` to
  `sdlc_stage_marker.py` (structural twin of `_review_verdict_readable`, reading
  `get_verdict(record, "CRITIQUE")`), and gate the `CRITIQUE` + `completed`
  branch of `write_marker` on it with a named `CRITIQUE_VERDICT_MISSING` refusal
  (exit 1). Fails closed on any probe error. The idempotent already-completed
  path stays exit 0.
- **WS-D:** two parts. (1) Skill/contract: do-pr-review SKILL + the multi-judge
  `outcome-contract.md`/context addendum state explicitly that judge forks run in
  the foreground and MUST be awaited in-turn before the parent aggregates/posts/
  records — never `run_in_background` without an in-turn await (mirrors the WS2
  in-turn mandate). (2) Mechanism: extend the REVIEW `completed` refusal in
  `sdlc_stage_marker.py` so it requires BOTH a readable verdict (existing WS3c)
  AND a verifiable posted review artifact — implemented via a
  `_review_artifact_posted(issue_number, pr_number)` probe that queries
  `gh pr view <pr> --json reviews` / review comments for a `## Review:` marker.
  Fail-closed; the WS3b recovery row already owns the resulting no-artifact state
  (re-dispatch `/do-pr-review`). Reconcile with do-pr-review's existing
  "verify posting" step so the two agree on the artifact definition.
- **WS-E:** `tools/push_ancestry_guard.py` exposing `sdlc-push-guard`:
  1. Read the push target ref(s) from argv or stdin (git pre-push hook protocol
     passes `<local ref> <local sha> <remote ref> <remote sha>` on stdin). Only
     act when the remote ref is `refs/heads/main`.
  2. `gh pr list --state open --json number,headRefName,headRefOid` for the repo.
  3. For each open PR head oid: if `git merge-base --is-ancestor <pr_head> HEAD`
     succeeds (HEAD contains that PR's head) AND the pushed commit is not itself a
     squash-merge authored through the gate, refuse with a named
     `PUSH_CARRIES_OPEN_PR_ANCESTRY` error unless a merge authorization is present
     (the break-glass `data/merge_authorized_{pr}` override, same file the merge
     guard honors, or a live merge lease/verdict).
  4. Fail-closed on ancestry match; fail-open only when `gh` is unreachable
     (log + allow) so an offline machine is not bricked — but detached-HEAD +
     open-PR-head ancestry is a purely local `git` check that still fires without
     `gh` (a detached HEAD whose commit equals a known local PR branch tip).
  5. Wire into `doctor.install_pre_push_hook()` hook body (call
     `sdlc-push-guard` before/after `doctor --quick`) and into the do-docs
     cascade push step (explicit `sdlc-push-guard` call before `git push`), so
     the guard runs even where the hook is not installed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `critique_roster_check`: the grounding check must never raise into a false
  "complete" — a plan-read failure yields `grounded: false` (refusal direction),
  asserted by a test that points at a missing plan path.
- [ ] `sdlc_stage_marker._critique_verdict_readable` and
  `_review_artifact_posted`: both wrapped so any exception returns False
  (fail-closed), asserted by a test that forces a probe exception.
- [ ] `push_ancestry_guard`: `gh` failure is caught → local-only ancestry check
  still runs; a caught exception in the local check refuses (fail-closed) rather
  than allowing.

### Empty/Invalid Input Handling
- [ ] Empty plan text / empty result file → ungrounded (refusal), tested.
- [ ] `sdlc-push-guard` with no open PRs → allow (exit 0), tested.
- [ ] `--plan-path` omitted → grounding leg skipped, roster gate byte-identical
  to today, tested against the existing barrier fixtures.

### Error State Rendering
- [ ] Every refusal prints a NAMED error to stderr (`CRITIQUE_VERDICT_MISSING`,
  `PUSH_CARRIES_OPEN_PR_ANCESTRY`, grounding-missing critic names) and a non-zero
  exit — asserted per guard. No silent swallow.

## Test Impact

- [ ] `tests/unit/test_do_plan_critique_barrier.py` — UPDATE: add grounding-leg
  cases (grounded pass, ungrounded-but-fenced fail, `--plan-path` omitted =
  legacy behavior). Existing fence cases must still pass unchanged.
- [ ] `tests/unit/test_critique_roster_check.py` (if present; else add) — UPDATE/
  ADD: unit cases for `evaluate(run_dir, plan_path=...)` grounding leg.
- [ ] `tests/unit/test_sdlc_stage_marker.py` (or the module's existing test) —
  UPDATE: add `CRITIQUE` `completed` refusal-without-readable-verdict case,
  mirroring the existing `REVIEW_VERDICT_MISSING` case; add REVIEW artifact-
  presence case for WS-D.
- [ ] `tests/unit/test_merge_predicate.py` / merge-guard tests — no change
  (WS-E is a new push-path guard, disjoint from `gh pr merge`); add a NEW
  `tests/unit/test_push_ancestry_guard.py`.
- [ ] do-plan-critique / do-pr-review skill-body changes are prose; covered by
  the barrier/marker unit tests above plus a lint pass. No skill-runner test
  harness exists to update.

No existing REVIEW verdict-gate tests are deleted — WS-C/WS-D compose additively
with the #1932/#2062 gates.

## Rabbit Holes

- **Do NOT** try to count the fork's actual tool calls from the parent — the
  Agent tool does not surface a reliable per-fork tool-call ledger to the driver,
  and the critique design deliberately passes plan text inline. Grounding via
  plan-citation substring match is the robust, testable signal. (This is the
  trap the #2124 supervisor's manual check hinted at but which does not
  generalize.)
- **Do NOT** rewrite the multi-judge consensus machinery for WS-D — the fix is an
  in-turn-await contract + an artifact-presence gate, not a consensus redesign.
- **Do NOT** make `sdlc-push-guard` a universal pre-push hook that blocks all
  branches — scope strictly to `refs/heads/main` pushes; feature-branch pushes
  must stay unimpeded.
- **Do NOT** widen the grounding substring length so far that legitimate short
  section-header citations are rejected — tune `MIN_GROUNDING_QUOTE_LEN` with a
  grain-of-salt comment; bias toward accepting real critiques.

## Risks

### Risk 1: Grounding check false-refuses a legitimate critique
**Impact:** A real critic that paraphrases rather than quotes gets treated as
ungrounded, forcing a needless re-dispatch or a false `CRITIQUE INCOMPLETE`.
**Mitigation:** Make the contract explicit in CRITICS.md (critics MUST include at
least one verbatim citation line), keep the bar low (one citation), accept
section-header matches, and bound re-dispatch (the existing cap) so worst case is
one extra round, not a loop. Provisional env-overridable `MIN_GROUNDING_QUOTE_LEN`.

### Risk 2: WS-E push guard bricks a legitimate docs-on-main push
**Impact:** A normal docs-cascade push from a clean main checkout is refused.
**Mitigation:** The guard only fires when HEAD is at/descended-from an OPEN PR
head — a clean main checkout is never descended from an unmerged PR branch. Add a
test proving a normal main push passes. Fail-open on `gh` outage for the remote
query; local detached-HEAD check stays conservative and named.

### Risk 3: WS-C/WS-D marker refusals deadlock the pipeline
**Impact:** A refused CRITIQUE/REVIEW `completed` leaves the stage stuck.
**Mitigation:** Both refusals redirect to an existing router recovery row
(re-dispatch the stage), exactly as WS3c/WS3b do — the failure direction is
"re-run the stage", never a terminal stall. Assert the named error is emitted.

## Race Conditions

### Race 1: Judge forks still writing while REVIEW artifact-presence probe runs
**Location:** do-pr-review parent, `sdlc_stage_marker` REVIEW `completed` branch.
**Trigger:** Parent probes for the posted `## Review:` artifact before the (now
mandated foreground/awaited) judges have posted.
**Data prerequisite:** All judge forks awaited in-turn; aggregate `## Review:`
comment posted BEFORE the verdict record and completion marker.
**State prerequisite:** WS-D's in-turn-await contract holds (no background judge
outlives the parent).
**Mitigation:** WS-D mandates in-turn await; the artifact-presence probe is the
mechanical backstop that refuses `completed` if the ordering was violated.

### Race 2: Concurrent push and merge on the same PR
**Location:** `push_ancestry_guard` vs `gh pr merge`.
**Trigger:** A legitimate squash-merge lands the PR to main between the guard's
`gh pr list --state open` read and the push.
**Data prerequisite:** The PR must still be OPEN for the guard to fire.
**State prerequisite:** A merged PR drops out of `--state open`, so the guard no
longer treats its ancestry as a bypass.
**Mitigation:** The break-glass `data/merge_authorized_{pr}` override (same file
the merge guard honors) authorizes the intended path; the guard reads open PRs
live at push time, minimizing the window. Benign direction: worst case is a
refused push that the operator retries after the merge registers.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2026] The umbrella #2026 5-workstream hardening (single-owner
  lease, in-turn mandate, verdict-gated routing, revision-latch, docs-fork
  zero-tool guard) is already SHIPPED via PR #2076 and verified live — this plan
  does NOT redo any of it; it only adds the NEW merge-by-push guard (WS-E) tracked
  by the latest #2026 comment.
- Nothing else deferred — WS-A through WS-E are all in scope for this plan.

## Update System

- **`scripts/update/hardlinks.py`**: WS-A/WS-B/WS-D edit `skills-global/` skill
  bodies (`do-plan-critique/{SKILL.md,CRITICS.md}`, `do-pr-review/`), which are
  already hardlink-synced to `~/.claude/skills/` on every machine — no new sync
  wiring, but confirm the edited files fall under the existing
  `sync_claude_dirs()` sweep (they do; no `RENAMED_REMOVALS` entry needed since
  nothing is renamed/moved between `skills/` and `skills-global/`).
- **WS-E pre-push hook**: `tools/doctor.py::install_pre_push_hook()` gains the
  `sdlc-push-guard` call. If `/update` should install/refresh the hook on every
  machine, add a step to `scripts/update/run.py` (or note it stays opt-in via
  `doctor --install-hook`). Decision recorded in the plan; default: keep the
  hook opt-in but ALSO wire the guard into the docs-cascade push step so
  protection does not depend on hook installation.
- **`pyproject.toml [project.scripts]`**: add `sdlc-push-guard =
  "tools.push_ancestry_guard:main"` — a new console entry point propagated by the
  standard `pip install -e` in the update flow.
- No Popoto model changes → no `scripts/update/migrations.py` entry.

## Agent Integration

- **New CLI entry point (WS-E):** `sdlc-push-guard` in `pyproject.toml
  [project.scripts]` — invokable via the agent's Bash tool and by the git
  pre-push hook. No MCP surface needed (it is a git-lifecycle guard, not a
  conversational tool).
- **WS-A CLI change:** `critique-roster-check` gains `--plan-path`; already a
  console entry, no new registration.
- **Bridge:** no `bridge/telegram_bridge.py` import changes — all guards are
  invoked by SDLC skills (Bash) and git hooks, not by the bridge directly.
- **Integration test:** a test invoking `sdlc-push-guard` via subprocess against a
  temp git repo with a simulated open-PR head confirms the agent-invocable path
  refuses/allows correctly (not just the Python API).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-fork-artifact-grounding.md` describing the four
  grounding guards (CRITIQUE grounding leg, CRITIQUE verdict-readability, REVIEW
  artifact-presence + in-turn-await, push-ancestry guard), the fail-closed
  posture, and how each redirects rather than stalls.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-pipeline.md` "Fork/Supervisor Hardening" section
  to reference the new guards alongside the #2076 legs.

### Repo SDLC Addenda
- [ ] `docs/sdlc/do-plan-critique.md` — document the `--plan-path` grounding leg
  on `critique-roster-check` and the absolute-path resolution (WS-A/WS-B).
- [ ] `docs/sdlc/do-pr-review.md` — document the REVIEW artifact-presence gate and
  the in-turn-await mandate for judges (WS-D).
- [ ] `docs/features/config-timeout-catalog.md` or a nearby catalog — record
  `MIN_GROUNDING_QUOTE_LEN` as a provisional/tunable env-overridable constant.

### Inline Documentation
- [ ] Docstrings on the new `_critique_verdict_readable`, `_review_artifact_posted`,
  the grounding helper, and `push_ancestry_guard.main` explaining the fail-closed
  contract.

## Success Criteria

- [ ] A critique result file that cites a nonexistent/fabricated plan is refused
  by `critique-roster-check --plan-path ...` (treated as an incomplete member),
  proven by a red-state test.
- [ ] A CRITIQUE `completed` marker write is refused (`CRITIQUE_VERDICT_MISSING`,
  exit 1) when no readable CRITIQUE verdict exists.
- [ ] A REVIEW `completed` marker write is refused when either the verdict is
  unreadable (WS3c) OR no posted review artifact is verifiable (WS-D).
- [ ] `PLAN_PATH` resolves to an absolute path in do-plan-critique so a
  `.claude/worktrees/agent-*` cwd never yields an unresolvable plan (WS-B).
- [ ] `sdlc-push-guard` refuses a push to main whose HEAD is descended from an
  OPEN PR head without authorization, and allows a normal main push — both proven
  by tests.
- [ ] Tests pass (`/do-test` on the touched unit files).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep` confirms `pyproject.toml` references `tools.push_ancestry_guard:main`
  and the pre-push hook body / do-docs push step reference `sdlc-push-guard`.

## Team Orchestration

### Team Members

- **Builder (critique-grounding)** — Name: `critique-builder` — Role: WS-A + WS-B
  (`critique_roster_check.py`, CRITICS.md, do-plan-critique SKILL/addendum) —
  Agent Type: builder — Resume: true
- **Builder (marker-invariants)** — Name: `marker-builder` — Role: WS-C + WS-D
  (`sdlc_stage_marker.py` probes, do-pr-review skill/addendum) — Agent Type:
  builder — Resume: true
- **Builder (push-guard)** — Name: `push-builder` — Role: WS-E
  (`push_ancestry_guard.py`, `pyproject.toml`, `doctor.py` hook, do-docs push
  step) — Agent Type: builder — Resume: true
- **Documentarian** — Name: `docs-writer` — Role: feature + addenda docs — Agent
  Type: documentarian — Resume: true
- **Validator** — Name: `grounding-validator` — Role: verify all success criteria
  + Verification table — Agent Type: validator — Resume: true

The three builders touch disjoint file sets (critique tools+skill vs stage_marker
vs push guard) so they can run in parallel within the one slug worktree without
commit interleaving. Docs and final validation run after.

### Available Agent Types

Standard tiers apply (builder, validator, documentarian).

## Step by Step Tasks

### 1. WS-A: CRITIQUE grounding leg
- **Task ID**: build-ws-a
- **Depends On**: none
- **Validates**: `tests/unit/test_do_plan_critique_barrier.py`, `tests/unit/test_critique_roster_check.py` (create if absent)
- **Assigned To**: critique-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `critique_roster_check.evaluate()` with optional `plan_path`; add the
  grounding helper (normalized verbatim substring / section-header match).
- Add `--plan-path` to the CLI; pass it from the addendum invocation.
- Edit `CRITICS.md`: critics MUST include ≥1 verbatim plan-citation line.
- Edit `do-plan-critique/SKILL.md` + `docs/sdlc/do-plan-critique.md` Step 3.5 to
  document the grounding leg and its bounded-re-dispatch → `MAJOR REWORK
  (CRITIQUE INCOMPLETE)` STOP path.
- Add `MIN_GROUNDING_QUOTE_LEN` as an env-overridable constant with a
  provisional/tunable comment.

### 2. WS-B: worktree-cwd absolute plan path
- **Task ID**: build-ws-b
- **Depends On**: none
- **Validates**: `tests/unit/test_do_plan_critique_barrier.py` (path-resolution case), manual worktree-cwd repro note
- **Assigned To**: critique-builder
- **Agent Type**: builder
- **Parallel**: true
- In do-plan-critique Plan Resolution, canonicalize `PLAN_PATH` to an absolute
  path rooted at `git rev-parse --show-toplevel`, assert existence, and pass the
  absolute path into SOURCE_FILES and every critic prompt. Keep the loud exit 1.

### 3. WS-C: CRITIQUE verdict-readability marker gate
- **Task ID**: build-ws-c
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_stage_marker.py`
- **Assigned To**: marker-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_critique_verdict_readable(issue_number)` to `sdlc_stage_marker.py`
  (twin of `_review_verdict_readable`); gate the `CRITIQUE` + `completed` branch
  of `write_marker` with a named `CRITIQUE_VERDICT_MISSING` refusal (exit 1,
  fail-closed). Idempotent already-completed path stays exit 0.
- Add the error sentinel to `_DIAGNOSED_ERRORS`.

### 4. WS-D: REVIEW artifact presence + in-turn-await contract
- **Task ID**: build-ws-d
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_stage_marker.py` (REVIEW artifact case)
- **Assigned To**: marker-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_review_artifact_posted(issue_number, pr_number)` probe; extend the REVIEW
  `completed` refusal to require BOTH readable verdict AND posted artifact.
- Edit `do-pr-review/SKILL.md` + `sub-skills/outcome-contract.md` +
  `docs/sdlc/do-pr-review.md`: judges run foreground/awaited in-turn; the parent
  posts the aggregate `## Review:` comment and records the verdict BEFORE
  returning — never exit with background judges in flight.

### 5. WS-E: push-ancestry merge-bypass guard
- **Task ID**: build-ws-e
- **Depends On**: none
- **Validates**: `tests/unit/test_push_ancestry_guard.py` (create), integration subprocess test
- **Assigned To**: push-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/push_ancestry_guard.py` (`main`) implementing the git pre-push
  stdin protocol, `refs/heads/main`-only scope, open-PR-head ancestry check via
  `git merge-base --is-ancestor`, named `PUSH_CARRIES_OPEN_PR_ANCESTRY` refusal,
  break-glass override, fail-open on `gh` outage / fail-closed on ancestry match.
- Register `sdlc-push-guard` in `pyproject.toml [project.scripts]`.
- Wire into `doctor.install_pre_push_hook()` hook body and the do-docs cascade
  push step.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-ws-a, build-ws-b, build-ws-c, build-ws-d, build-ws-e
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-fork-artifact-grounding.md`; update
  `docs/features/README.md`, `docs/features/sdlc-pipeline.md`, both SDLC addenda,
  and the timeout/constant catalog entry.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: all above
- **Assigned To**: grounding-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every success criterion; report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Critique barrier tests | `pytest tests/unit/test_do_plan_critique_barrier.py -q` | exit code 0 |
| Roster-check grounding tests | `pytest tests/unit/test_critique_roster_check.py -q` | exit code 0 |
| Stage-marker tests | `pytest tests/unit/test_sdlc_stage_marker.py -q` | exit code 0 |
| Push-guard tests | `pytest tests/unit/test_push_ancestry_guard.py -q` | exit code 0 |
| Push guard registered | `grep -c "tools.push_ancestry_guard:main" pyproject.toml` | output > 0 |
| CRITIQUE readability probe exists | `grep -c "_critique_verdict_readable" tools/sdlc_stage_marker.py` | output > 0 |
| REVIEW artifact probe exists | `grep -c "_review_artifact_posted" tools/sdlc_stage_marker.py` | output > 0 |
| Grounding leg wired | `grep -c "plan_path" tools/critique_roster_check.py` | output > 0 |
| Push guard in docs-cascade push | `grep -c "sdlc-push-guard" .claude/skills-global/do-docs/SKILL.md` | output > 0 |
| Lint clean | `python -m ruff check tools/ .claude/hooks/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **WS-E hook deployment posture:** keep `sdlc-push-guard` opt-in (only when a
   machine runs `doctor --install-hook`) with the do-docs push-step call as the
   always-on backstop, OR have `/update` install/refresh the pre-push hook on
   every machine? Default assumption in the plan: opt-in hook + always-on
   docs-cascade call. Confirm.
2. **WS-A grounding strictness:** is a single verbatim citation (≥
   `MIN_GROUNDING_QUOTE_LEN` chars) or a section-header match a low-enough bar to
   avoid false refusals of paraphrasing critics, or should the contract require a
   `LOCATION:` line that resolves to a real plan section specifically? Default:
   one verbatim-substring OR section-header match.
3. **Scope confirmation:** proceed with all five workstreams in one PR (one lane,
   same family), or split WS-E (#2026 push guard) into its own PR since it touches
   a disjoint file set (git hook path vs critique/review substrate)? Default:
   one PR, one lane, as routed.
