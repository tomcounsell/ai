---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2739
last_comment_id: none
---

# Docs Auditor Review Gate

## Problem

An automated writer generates documentation rewrites and commits them itself, in the
same function call, with nothing between generation and the permanent record.

In #2711 the auditor invented a module rename that never happened
(`agent/session_logs.py` → `agent/agent_sessions.py`), applied it in five places
across two docs, and committed it as `d7bf3ad99`. A human running `ls` caught it.
PR #2728 fixes that particular generator bug. It does not touch the reason a
generator bug reached `main` at all: there is no review gate in front of any write
the auditor makes, so the next generator bug takes exactly the same path.

**Current behavior:**

- **Cascade (Caller B).** `audit(scope_mode="pr-changed-files")` calls
  `_commit_current_branch`, which runs `git add <touched>` + `git commit`, both
  `check=False` with errors swallowed to a log line. The `/do-docs` skill is
  explicitly instructed not to review it: *"Do not re-commit the substrate's
  changes — it commits them itself"*
  (`.claude/skill-context/do-docs.md:152`, `.claude/skills-global/do-docs/SKILL.md:220-221`).
- **Rotation (Caller A).** `_push_branch_and_pr` runs `git add -A`, staging the
  **entire working tree** of the shared main checkout into a docs PR, which
  `run_docs_branch_sweeper` can then auto-merge with no human involvement.
- **Withheld fixes rot.** PR #2728's existence invariant stamps
  `WITHHELD_PR_MARKER` into the PR body. `_pr_is_auto_merge_eligible` refuses that
  PR forever — nothing rewrites the body — so the sweeper stale-closes it at
  `STALE_PR_AGE_DAYS = 14` with `--delete-branch`, discarding the fixes that *did*
  pass the invariant. Nobody is told.
- **Rotation can wedge the shared checkout and report success.** The daily-cap and
  open-PR guards in `_push_branch_and_pr` fire *after* the substrate has already
  written to disk, and rotation never commits (that path is gated to
  `pr-changed-files`). The edits sit uncommitted in `/Users/valorengels/src/ai`.
  `run_docs_auditor` then stamps the rotation hash, sends a "N files, M fixes"
  Telegram, and writes liveness `status="ok"`. Every subsequent run trips the
  dirty-tree guard and skips. The auditor is permanently disabled until a human
  notices the dirt by hand.

**Desired outcome:**

Every write the docs auditor makes passes a named review gate before it becomes a
permanent record; the auditor never leaves the repository in a state it did not
intend; and when it declines to do something, a human finds out through a channel
somebody actually reads.

## Freshness Check

**Baseline commit:** `48feedf318a768f6f38d364dc998a67a69f16027` (main)
**Issue filed at:** 2026-08-13T03:27:43Z
**Disposition:** Minor drift — one issue claim is materially **wrong in our favour** (see Spike Results, spike-1). All other claims hold.

**File:line references re-verified.** All line numbers in issue #2739 are against
`main`. This plan builds on `origin/session/docs-auditor-rename-guard` (PR #2728),
so every reference below is re-anchored to **that branch's** version of
`reflections/docs_auditor.py`, read via
`git show origin/session/docs-auditor-rename-guard:reflections/docs_auditor.py`.

| Issue claim (main line) | Branch line | Status |
|---|---|---|
| `_commit_current_branch` call site `:1057-1058` | `:1175-1176` | Holds |
| `_commit_current_branch` def `:1072` | `:1192` | Holds |
| `_push_branch_and_pr` def `:1218` | `:1338` | Holds |
| `git add -A` `:1246` | `:1373` | Holds |
| Early-return guards `:1229-1236` | `:1355-1362` | Holds; both still return before `checkout -b` |
| Unguarded `finally` restore `:1294-1301` | `:1436-1443` | Holds; no `check=`, no reset, no stash |
| `_pr_is_auto_merge_eligible` `:1689` | `:1872` | Holds; presence of review/comment still disqualifies (`:1923`) |
| Sweeper stale-close `~:1918-1931` | `:2127-2139` | Holds; `gh pr close --delete-branch` |
| Dirty-tree guard `:1587` | `:1737` | Holds |
| Liveness `status="ok"` `:1653-1655` | `:1822-1829` | Holds |
| `_send_telegram_notification` `:904` | `:1018` | Holds; one production call site (`:1814`), success-path only |
| Scheduler reads only `projects` `agent/reflection_scheduler.py:509` | `:510-515` | **Confirmed** — `findings` and `summary` are discarded |
| `_detect_renamed_symbol_fixes` `renames[0][1]` `:417-431` | `:447-461` | Present, but **unreachable** — see spike-1 |
| `_detect_readme_broken_entries` frame mismatch `:434` | `:464-494` | Present, but unreachable |
| `_detect_renamed_link_fixes` frame mismatch `:387/:412` | `:417-444` | Present, but unreachable |

**Cited sibling issues/PRs re-checked:**

- **#2726, #2725, #2729** — all still OPEN. This plan closes all three.
- **#2711, #2713** — still OPEN; closed by PR #2728, not by this work.
- **PR #2728** — still OPEN on `session/docs-auditor-rename-guard`, base `main`,
  head `90e8b6651`, at the merge gate. **Hard dependency:** build on this plan
  starts only after #2728 merges. It introduces `fixes_withheld`, the `withheld`
  result list, `WITHHELD_PR_MARKER`, `_absent_new_path_refs`, and the existence
  invariant, all of which item 3 depends on.

**Commits on main since the issue was filed** (touching `reflections/docs_auditor.py`,
`agent/reflection_scheduler.py`, `config/reflections.yaml`, `.claude/skill-context/do-docs.md`):
**none.** The issue was filed ~7 hours before planning and nothing relevant moved.

**Active plans overlapping this area:**

- `docs/plans/docs-auditor-rename-guard.md` — the plan behind PR #2728. **Not a
  blocker; a sequencing dependency.** That plan explicitly deferred this work:
  its No-Gos list `[SEPARATE-SLUG #2726] The auditor is its own committer` and its
  Rabbit Holes list *"Removing the auditor's self-commit."* One coordination point:
  it has an open checklist item to change `.claude/skill-context/do-docs.md`'s
  `status: "ok"` handling, and this plan rewrites the adjacent lines of the same
  file. Sequencing after merge resolves the collision.
- No other active plan touches `reflections/docs_auditor.py`.

**Bug reproduction:** items 1, 3 and 4 were re-read on the #2728 branch and the
defective code paths are all present and unchanged. Item 2 was reproduced
**and disproved** — see spike-1.

## Prior Art

- **PR #2728** (open, `session/docs-auditor-rename-guard`): word-anchors stale terms
  and adds the path-existence invariant. Closes #2711/#2713. Fixes the *generator*
  bug that produced `d7bf3ad99`; deliberately leaves the *structural* absence of a
  review gate to this slug.
- **#2726 / #2725 / #2729**: the three consolidated issues. Filed separately during
  #2728's scoping precisely because each needed an owner ruling rather than a
  mechanical fix.
- **`docs/plans/docs-auditor-rename-guard.md`**: records the reasoning for deferring
  — *"a workflow decision about the `/do-docs` contract, not a bug fix, and it would
  change how every cascade behaves."* This plan makes that decision.
- **`reflections/merged_branch_cleanup.py` + `tests/unit/reflections/test_merged_branch_cleanup.py`**:
  the closest in-repo precedent for the test strategy this plan needs — a real git
  repo on disk, a `gh` dispatcher as `subprocess.run` side-effect, and
  `git status --porcelain` asserted empty to prove the code committed its own work.
- No prior attempt to remove the self-commit exists. This is the first pass, so
  there is no **Why Previous Fixes Failed** section.

## Research

Purely internal work: no new libraries, no external APIs, no ecosystem patterns.
The one external-behavior question (git's `--follow` semantics) was settled by
direct experiment against real git rather than by search, because the answer is
version- and repo-specific and the experiment is cheap and authoritative.

No relevant external findings — proceeding with codebase context and the spike
results below.

## Spike Results

### spike-1: Are the three rename detectors actually capable of producing a fix?

- **Assumption being tested:** issue #2739 item 2 asserts the detectors "pick the
  wrong target" — `renames[0][1]`, the newest rename hop. That presumes they
  produce a target at all.
- **Method:** prototype — real git repos, both a purpose-built temp repo and the
  actual `ai` repo history. git 2.50.1 (Apple Git-155).
- **Finding: the detectors are structurally incapable of producing any fix.
  `renames[0][1]` is unreachable code.**

  `_git_log_follow_renames` runs `git log --follow --diff-filter=R --name-status
  --format= -- <old_path>`. **`git log --follow` only follows a path that exists at
  HEAD.** All three detectors call it *exclusively* for paths they have just
  confirmed do **not** exist — each one `continue`s when
  `(repo_root / path).exists()`. So the query is only ever handed inputs for which
  it returns zero `R` lines.

  Temp repo, `pkg/alpha.py → pkg/beta.py → pkg/gamma.py`:

  ```
  $ git log --follow --diff-filter=R --name-status --format= -- pkg/alpha.py
  (empty)
  $ git log --follow --diff-filter=R --name-status --format= -- pkg/gamma.py
  R100  pkg/beta.py   pkg/gamma.py
  R100  pkg/alpha.py  pkg/beta.py
  ```

  Only the path present at HEAD returns anything. Adding `-M`, `--find-renames=40%`,
  or dropping `--follow` while keeping the pathspec all still return empty for the
  absent path.

  Reproduced against real repo history with a rename that landed days ago:

  ```
  $ git log --diff-filter=R --name-status --format= -M -50 | head -1
  R100  docs/plans/upvote-autonomous-sdlc-pickup.md  docs/plans/completed/upvote-autonomous-sdlc-pickup.md
  $ git log --follow --diff-filter=R --name-status --format= -- docs/plans/upvote-autonomous-sdlc-pickup.md
  (empty)
  ```

  Corollary: the bad rename in #2711 did **not** come from these detectors. It came
  from the `STALE_TERMS` channel (`_detect_stale_term_fixes`), which is exactly what
  PR #2728 word-anchors.

- **Confidence:** high. Two independent repos, an explicit positive control (the
  HEAD-present path *does* return rename data), and three query variants ruled out.
- **Impact on plan:** inverts Q6. Neither "walk the chain" nor "the existence
  invariant already handles it" is the right answer — the code is dead. This plan
  deletes it (see Q6 in Technical Approach) and files the rebuild question as
  **#2741**.

### spike-2: Would a working query make chain-walking necessary?

- **Assumption:** "newest rename commit, not the final hop" (issue framing) — i.e.
  that `renames[0][1]` picks an intermediate node.
- **Method:** prototype, same temp repo.
- **Finding:** the issue's framing is backwards for a live chain, and would become
  correct only under a repaired query. `git log` emits newest-first, so for
  `A → B → C` queried on the HEAD-present path, `renames[0]` is `(B, C)` and
  `renames[0][1]` is `C` — the **final** hop. But the working query for an absent
  path is a pathspec-free scan matched on the rename *source*:

  ```
  $ git log --diff-filter=R -M --name-status --format= | awk -F'\t' '$1 ~ /^R/ && $2=="pkg/alpha.py" {print $3}'
  pkg/beta.py
  ```

  which yields the **first** hop. So chain-walking is genuinely required, but only
  *after* the query is repaired — it is not a fix that can be applied to today's code.
- **Confidence:** high.
- **Impact on plan:** confirms the rebuild is a feature, not a bug fix. Recorded in
  #2741 as prerequisite 2 of 3.

### spike-3: Does PR #2728's existence invariant catch the doc-relative frame mismatch?

- **Assumption (issue Q6):** the existence invariant may already reduce the rename
  defects to acceptable "declines to write" behavior.
- **Method:** code-read of `_absent_new_path_refs` (`:537-547`) against
  `_detect_renamed_link_fixes` (`:417-444`).
- **Finding: no — the invariant is blind to the frame mismatch.** For a doc at
  `docs/features/x.md` linking `(./old.md)`, the detector resolves against the repo
  root and substitutes `docs/features/new.md` into a doc-relative link, producing a
  link that resolves to `docs/features/docs/features/new.md`. `_absent_new_path_refs`
  validates the raw string `docs/features/new.md` with `(repo_root / ref).exists()`,
  which is **True**, so the invariant passes and a broken link is written. The
  invariant only catches targets absent at the repo root; a frame-mismatched target
  is present at the repo root by construction.
- **Confidence:** high.
- **Impact on plan:** removes the "invariant already handles it" option from Q6 —
  it would not have. Reinforces deletion over repair, and is recorded as
  prerequisite 3 of 3 in #2741.

### spike-4: Which reporting channel does a function reflection actually have?

- **Assumption (issue Q5):** `findings` and `summary` reach no human, so any
  escalation built on them is a no-op.
- **Method:** code-read of `agent/reflection_scheduler.py`, `models/reflection.py`,
  `ui/data/reflections.py`, and every `reflections/*.py` escalation site.
- **Finding: confirmed, plus a cheap repair nobody noticed.**
  - `agent/reflection_scheduler.py:510-515` reads only `result.get("projects")`;
    `findings` and `summary` are discarded.
  - **But `mark_completed` already accepts `output_summary`**
    (`models/reflection.py:186`, stored `:221`), and the dashboard already renders it
    (`ui/data/reflections.py:286` and `:139` as `last_run_summary`). The scheduler
    simply never passes it. Wiring `summary` → `output_summary` is a one-line change
    that makes every function reflection's summary visible.
  - `_write_liveness`'s two Redis keys (`docs_audit:last_completed_run_ts`,
    `docs_audit:last_completed_run_summary`) have **zero readers** repo-wide.
    Confirmed: no `r.get` for either key anywhere, nothing in `ui/`.
  - `_send_telegram_notification` is hardcoded to chat `"Eng: Valor"`, has exactly
    one production call site (`:1814`) on the **success path only**, and is silent
    on failure (`check=False`, output dropped).
  - `run_docs_auditor` catches its own exceptions and returns
    `{"status": "error", ...}`, so it **never raises to the scheduler** and never
    trips the consecutive-failure counter. Every run records as `success`.
  - The durable, most-used escalation across `reflections/` is `gh issue create`
    (six modules). The docs auditor already owns one — `_file_issue_if_new`, with
    30-day Redis dedup and a cross-machine `_open_issue_exists` pre-check.
- **Confidence:** high.
- **Impact on plan:** Q5 resolves to "reuse `_file_issue_if_new`" rather than
  inventing a channel, plus the one-line scheduler wiring as a bonus fix.

### spike-5: Is `files_touched` the complete set of the auditor's writes?

- **Assumption (issue Q3):** `git add -A` → stage only `files_touched` is
  unconditionally correct; confirm nothing depends on the sweep.
- **Method:** code-read — grep for every filesystem write in the module.
- **Finding: confirmed.** `reflections/docs_auditor.py` contains exactly **one**
  filesystem write: `full.write_text(new_text)` at `_apply_fixes_to_file:634`, whose
  path is precisely what lands in `files_touched`. `_run_vault_drift_detection` only
  files GitHub issues; it writes no files. Nothing in the module depends on the
  `add -A` sweep, and the sweep can only ever capture *other* processes' work in the
  shared checkout.
- **Confidence:** high.
- **Impact on plan:** Q3 is a mechanical, zero-risk narrowing.

## Data Flow

**Caller B (cascade) — after this change:**

1. **Entry point:** `/do-docs` skill Step 2d runs the substrate via the bash block
   in `.claude/skill-context/do-docs.md`.
2. **`audit(scope_mode="pr-changed-files")`:** detectors run, `_apply_fixes_to_file`
   writes to the working tree, `withheld` accumulates invariant rejections.
3. **Substrate returns** `files_touched` + `fixes_withheld` + `withheld`. **No git.**
   The working tree is dirty and stays dirty.
4. **`refresh_docs_in_memory(touched)`** fires on the applied (not committed) set.
5. **`/do-docs` Step 4 — the review gate:** the agent runs `git diff --name-only`,
   reconciles it against the Step 2 task list **plus the substrate's returned
   `files_touched`**, reads `git diff`, and reverts anything it cannot justify.
6. **Output:** `git add -A && git commit` by the skill, on the feature branch, with
   a human-reviewable diff that an agent has actually read.

**Caller A (rotation) — after this change:**

1. **Entry point:** scheduler invokes `run_docs_auditor` daily.
2. **Preflight (new position):** auth → lock → dirty-tree guard → **daily-PR cap
   guard** → rotation pick → **open-PR-for-slug guard**. All guards now fire
   **before** any write.
3. **`audit(scope_mode="rotation")`** writes fixes to the shared main checkout.
4. **Zero-diff gate** unchanged.
5. **`_push_branch_and_pr(slug, root, files_touched, withheld)`:** records the
   starting ref, `checkout -b`, stages **only `files_touched`**, commits, pushes,
   opens the PR.
6. **Restore, verified:** `finally` returns to the recorded starting ref and asserts
   the tree is clean. A failed restore is a hard error, not a discarded result.
7. **Outcome routing:** PR created → `status="ok"`. Guard fired pre-write →
   `status="skipped"`, nothing written. Anything else → `status="error"`, escalated.
8. **The review gate:** the PR. Nothing merges it automatically.
9. **Output:** `summary` → scheduler → `output_summary` → dashboard; withheld →
   deduped GitHub issue.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - `_commit_current_branch` — **deleted**. `audit()`'s return contract is unchanged;
    only its side effects shrink.
  - `_push_branch_and_pr` gains a required `files_touched: list[str]` parameter.
  - `_pr_is_auto_merge_eligible` — **deleted**.
  - `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`,
    `_git_log_follow_renames` — **deleted**.
  - `agent/reflection_scheduler.py` passes `output_summary` to `mark_completed`.
    This is a generic change affecting **every** function reflection, all
    beneficially (a summary that was discarded now renders).
- **Coupling:** decreases. The substrate stops owning git for Caller B; the skill
  that already owns git for the rest of the docs stage owns it for all of it.
- **Data ownership:** the commit decision moves from the substrate to each caller.
- **Reversibility:** high. Every change is a deletion or a narrowing, and the
  deployed code only changes on the next `/update` service restart.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (Q2 and Q6 are owner-visible rulings; both are settled in this
  plan with a recommendation, so the check-in is a confirm, not a design session)
- Review rounds: 2 (this touches a live scheduled reflection that writes to the
  shared main checkout)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #2728 merged to main | `gh pr view 2728 --json state -q .state` → `MERGED` | Build depends on `fixes_withheld`, `withheld`, `WITHHELD_PR_MARKER`, `_absent_new_path_refs` |
| No rotation in flight | `python -c "from reflections.docs_auditor import _get_redis, REDIS_RUNNING_KEY; print(bool(_get_redis().exists(REDIS_RUNNING_KEY)))"` → `False` | Changing commit behavior mid-rotation could interleave with a live `checkout -b` in the shared checkout |
| Shared main checkout clean | `git -C /Users/valorengels/src/ai status --porcelain` → empty | A pre-existing dirty tree is the wedged state item 4 describes; must be cleared and its cause understood before landing |
| No open docs-audit PR mid-flight | `gh pr list --state open --json headRefName -q '[.[] \| select(.headRefName \| startswith("docs-audit/"))] \| length'` → `0` | An in-flight PR opened by the old code carries old-format staging |
| `gh` authenticated | `gh auth status` | Sweeper and PR tests reason about real `gh` JSON shapes |

## Solution

### Key Elements

- **A named gate per caller.** Cascade's gate is the `/do-docs` agent reading the
  diff. Rotation's gate is the pull request, reviewed by a human or `/do-pr-review`.
  Neither caller can produce a commit that no reviewer saw.
- **The substrate stops being a committer.** `audit()` writes files and returns what
  it wrote. It never runs `git commit`.
- **Rotation stages an explicit list.** The only files that enter a docs PR are the
  files the auditor wrote.
- **Rotation is transactional against the shared checkout.** Guards run before
  writes; the restore is verified; a failed restore is reported as an error.
- **Declining is loud.** Withheld fixes become a deduped GitHub issue, and every
  function reflection's `summary` reaches the dashboard.
- **Dead rename detection is deleted, not repaired.**

### Flow

**Cascade:** `/do-docs` Step 2d → substrate applies fixes, returns `files_touched`,
commits nothing → **Step 4 review gate**: agent reads `git diff`, reconciles against
task list + `files_touched`, reverts the unjustifiable → agent commits → feature-PR
review sees the docs diff alongside the code diff.

**Rotation:** scheduler → preflight guards (all pre-write) → substrate applies fixes
→ stage exactly `files_touched` → commit → push → **PR opened = review gate** →
human or `/do-pr-review` approves → `/do-merge`. No auto-merge. Withheld → deduped
issue → human.

### Technical Approach

The six open questions from the issue's Solution sketch, resolved.

---

**Q1 — Cascade: who commits? → The `/do-docs` skill. Delete `_commit_current_branch`.**

Delete `_commit_current_branch` and its call site at `audit():1175-1176`; keep the
`refresh_docs_in_memory` hook firing there (it operates on applied paths and does
not need a commit to be meaningful). `audit()` in `pr-changed-files` mode leaves a
dirty tree and returns `files_touched`.

The skill can carry the contract: `.claude/skills-global/do-docs/SKILL.md` Step 4
**already** contains the full sequence — `git diff --name-only`, "every file in this
list should appear in the Step 2 task list", `git diff`, "each change should be a
targeted update, not a rewrite", then `git add -A && git commit`. Only three edits
are needed:

1. Step 2d (`:220-221`): replace *"do not re-commit changes the substrate commits
   itself"* with an instruction to carry the substrate's `files_touched` into Step 4.
2. Step 4 (`:252`): reconcile a latent conflict the recon surfaced —
   *"If there are unexpected files, revert them"* would today condemn every
   substrate-touched file, since they are not in the Step 2 task list. The expected
   set becomes **Step 2 task list ∪ substrate `files_touched`**.
3. `.claude/skill-context/do-docs.md:143-152`: rewrite the descriptive claim
   ("commits them to the current branch") and delete the imperative
   ("Do not re-commit the substrate's changes — it commits them itself").

No other Caller B site is left writing to a tree nobody commits. Recon enumerated
all four `audit()` callers:

| Caller | Location | Passes `repo_root`? | Own git? | Disposition |
|---|---|---|---|---|
| Rotation | `docs_auditor.py:1763` | yes | yes | Handled by Q2/Q3/Q4 |
| `__main__` CLI | `docs_auditor.py:2156-2168` | no | no | Correct after the change — it prints JSON and leaves the tree for its caller. Its docstring is updated to say so. |
| Skill-context bash block | `.claude/skill-context/do-docs.md:129-140` | no | no | Contract rewritten (above) |
| Cheatsheet | `docs/features/docs-auditor.md:274-276` | no | no | Documentation only; updated |

There is **no** MCP tool and **no** `pyproject.toml [project.scripts]` entry for the
auditor, so no other surface exists.

---

**Q2 — Rotation's review gate → the PR itself. Conservative unreviewed auto-merge does not survive.**

Delete `_pr_is_auto_merge_eligible` entirely and remove the auto-merge branch from
`run_docs_branch_sweeper` (`:2104-2125`).

Justification. Rotation is headless, so it cannot stop committing — the question is
who reviews, and the only available reviewer is whoever reads the PR. That makes the
PR the gate, and auto-merge is precisely the mechanism that bypasses it: it is how
an unreviewed auditor write reaches `main` without any human, which is the whole
defect this issue names.

The issue observes the predicate is inverted, and it is: `:1923` returns `False` when
`reviews`, `reviewRequests`, **or** `comments` are present. So today it is strictly a
*"nobody has looked at this"* detector — it merges exactly the PRs that had no
review, and refuses exactly the ones that did. Repairing the inversion means
requiring a positive approval, at which point the predicate reduces to "merge
approved PRs", which is what `/do-merge` and the human already do. So the repaired
predicate is either redundant or unsafe; there is no version of it worth keeping.
Deleting it is the Principle-1 outcome and removes ~80 lines.

The sweeper keeps its two legitimate jobs: deleting stale `docs-audit/*` branches
whose PRs are already closed, and closing stale PRs (modified per Q5).

Volume objection, addressed: without auto-merge, docs PRs accumulate. They do not —
`_daily_pr_cap_reached` bounds rotation to **1 PR per calendar day**, and the sweeper
still closes non-withheld PRs at 14 days.

---

**Q3 — Staging → `git add --` with the explicit `files_touched` list.**

`_push_branch_and_pr` gains a required `files_touched: list[str]` parameter and
stages `["git", "add", "--", *files_touched]`. `git add -A` is removed from the
module.

Confirmed nothing depends on the sweep (spike-5): the module contains exactly one
filesystem write, at `_apply_fixes_to_file:634`, and its path is exactly what lands
in `files_touched`. `_run_vault_drift_detection` files issues and writes no files.
The sweep could therefore only ever capture *other* processes' work in the shared
checkout, which is the bug.

---

**Q4 — Rotation must not corrupt the shared checkout → both hoist the guards *and* verify the restore.**

The issue offers these as alternatives. They are not: hoisting fixes the common case,
verification fixes the rest.

1. **Hoist both guards into `run_docs_auditor`, before `audit()` runs.**
   `_daily_pr_cap_reached(PROJECT_ROOT)` needs nothing but Redis and can move to the
   preflight block. `_has_open_pr_for_slug(slug, PROJECT_ROOT)` needs `slug`, which
   is computed at step 4b — still before the substrate at step 5. A fired guard now
   returns `status="skipped"` with **zero writes**, which is the clean fix.
2. **A verified restore still matters,** because `checkout -b`, `add`, `commit`,
   `push`, and `gh pr create` can all fail after the substrate has written.
   `_push_branch_and_pr` records the starting ref via
   `git rev-parse --abbrev-ref HEAD` at entry, and on every exit path:
   `git checkout -f <starting ref>` with the return code **checked**, delete the
   created branch if it exists, then assert `not _git_dirty(repo_root)`.
3. **A failed restore is a hard error.** If the tree is still dirty or HEAD is not on
   the starting ref, `_push_branch_and_pr` signals failure and `run_docs_auditor`
   returns `status="error"` and escalates via Q5's channel. It must not write
   liveness `"ok"`, must not stamp the rotation hash, and must not send a success
   Telegram.
4. **Outcome routing in `run_docs_auditor`.** Because the guards moved,
   `pr_url is None` after `audit()` now unambiguously means failure, not "a guard
   fired". The three outcomes become distinct: `ok` (PR created), `skipped`
   (guard fired, nothing written), `error` (write happened, PR did not).

Note the restore must use `checkout -f`. A plain `git checkout main` is exactly what
fails today when local edits conflict, and the current code discards that failure.

---

**Q5 — Withheld must escalate → a deduped GitHub issue, plus never deleting the branch that carries the fixes. Also wire `summary` to the dashboard.**

The issue is right that any escalation built on `findings`/`summary` is a no-op
today — but spike-4 found the repair is one line, so this plan does both.

1. **Escalate withheld via the module's existing `_file_issue_if_new`.** Do not
   invent a channel. `gh issue create` is the most-used escalation across
   `reflections/` (six modules), it is durable rather than ephemeral, and the docs
   auditor already owns a deduped wrapper for it with 30-day Redis dedup and a
   cross-machine `_open_issue_exists` pre-check. A run with `fixes_withheld > 0`
   files one issue naming the doc, the attempted rewrite, and the PR.
2. **The sweeper never closes a PR carrying `WITHHELD_PR_MARKER`.** On encountering
   one at stale age it files (or refreshes, via dedup) the escalation issue and
   leaves both PR and branch untouched. This is what stops the
   propose → withhold → close → re-propose loop: the loop today is silent, and an
   open issue is not. Non-marker stale PRs keep their existing
   `gh pr close --delete-branch` behavior — no withheld fixes are at risk there.
3. **Wire `summary` → `output_summary`** in `agent/reflection_scheduler.py:515`:
   `state.mark_completed(duration, projects=projects_list, output_summary=summary)`
   where `summary = result.get("summary") if isinstance(result, dict) else None`.
   The field already exists on the model (`models/reflection.py:186`, `:221`) and the
   dashboard already renders it (`ui/data/reflections.py:139`, `:286`). This makes
   every function reflection's summary visible, not just the auditor's.

**Deliberately not doing:** repairing `_write_liveness`. Its two Redis keys have zero
readers and this plan gives the same information a real surface (the dashboard, via
`output_summary`). Adding a reader for a redundant channel is the parallel-run
migration Principle 1 forbids. Its removal is scoped in No-Gos as **#2743**.

---

**Q6 — Rename targets → delete all three detectors. The bug is unreachable; the code is dead.**

This contradicts the issue's framing, which offers only "walk the chain" or "the
existence invariant already handles it". spike-1 shows neither applies, and spike-3
shows the invariant would *not* have handled it.

`_git_log_follow_renames` uses `git log --follow`, which only follows a path that
exists at HEAD. All three detectors call it exclusively for paths they have just
confirmed are absent. The query therefore returns zero `R` lines for every input it
is ever given, in the temp-repo control and against real repo history alike.
`renames[0][1]` has never executed in production.

Deleting:

- `_detect_renamed_link_fixes` (`:417-444`) — entirely rename-driven. Deleted.
- `_detect_renamed_symbol_fixes` (`:447-461`) — entirely rename-driven. Deleted.
- `_git_log_follow_renames` (`:372-414`), `GIT_LOG_FOLLOW_CAP`, the
  `_RENAME_QUERY_COUNT` global and its per-run reset in `audit():1064-1065`. Deleted.
- `_detect_readme_broken_entries` (`:464-494`) — **kept**, with only its rename
  branch removed. A broken index entry unconditionally becomes the existing
  line-delete fix `(line, "")`. Since `renames` is always empty today, this is
  provably a **zero-behavior-change** edit.

Why deletion rather than repair. Repair needs three independent fixes together — a
working query, chain-walking with cycle protection, and frame-correct
re-relativization — which is a feature, not a bug fix, and it would introduce a new
class of automated write at exactly the moment this plan is building a review gate
around automated writes. The capability is not silently lost: the
`_detect_deleted_target_issues` file-as-issue detector already reports broken
references to a human, which is the conservative direction this whole plan moves in.
And #2725 is genuinely closed — a wrong-target bug cannot occur in deleted code.
The rebuild question, with all three prerequisites written up and the spike evidence
attached, is filed as **#2741**.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_push_branch_and_pr`'s `except subprocess.CalledProcessError` / `except Exception`
      (`:1430-1435`) — test that each leaves the checkout on the starting ref with a
      clean tree, and that the failure is observable in the return value (not only a
      `logger.warning`).
- [ ] The `finally` restore (`:1436-1443`) — test the case where `checkout` itself
      fails; assert the function reports failure rather than discarding the result.
- [ ] `run_docs_auditor`'s blanket `except Exception` (`:1855-1861`) — assert it
      returns `status="error"` **and** that the tree was restored, so a mid-run
      exception cannot wedge the checkout.
- [ ] `_file_issue_if_new` failure during withheld escalation must not crash the
      rotation; assert the run still completes and the failure is in `findings`.
- [ ] `_send_telegram_notification` remains best-effort; assert a send failure does
      not change the returned `status`.

### Empty/Invalid Input Handling

- [ ] `_push_branch_and_pr` with `files_touched == []` — must not create a branch and
      must not run `git commit`. (Today `add -A` would happily commit unrelated work.)
- [ ] `_push_branch_and_pr` with a `files_touched` entry that no longer exists on
      disk — `git add --` fails; assert the restore runs and the error is reported.
- [ ] `audit(scope_mode="pr-changed-files")` with an empty changed-file set — returns
      `ok`, writes nothing, commits nothing.
- [ ] Sweeper with an empty `gh pr list` response and with malformed JSON — neither
      may delete a branch.

### Error State Rendering

- [ ] A rotation run that fails after writing must surface `status="error"` in the
      returned dict **and** through `output_summary` on the dashboard — assert the
      scheduler passes it through.
- [ ] A run with `fixes_withheld > 0` must produce a GitHub issue; assert the issue
      body names the doc and the attempted rewrite.
- [ ] Assert `_write_liveness` is **not** called with `status="ok"` on any path where
      the PR was not created.

## Test Impact

Real-git tests in a temp repo are **required** for the git surface. More
`unittest.mock.patch` over `subprocess.run` is explicitly not an acceptable strategy
for `_push_branch_and_pr`, the staging set, the restore path, or the sweeper's close
path. The house precedent is `tests/unit/reflections/test_merged_branch_cleanup.py`:
a real repo on disk, `monkeypatch.setattr(module, "PROJECT_ROOT", repo)`, a
`cmd[0] == "gh"` dispatcher as `subprocess.run` side-effect that **falls through to
the real `subprocess.run` for `git`**, and `git status --porcelain` asserted empty.
`tests/unit/test_validate_docs_changed.py` supplies the `git_repo` fixture shape.

Existing tests in `tests/unit/test_docs_auditor_substrate.py` (81 tests today):

- [ ] `TestGitLogFollowCap::test_cap_enforced_after_n_calls` — DELETE: tests
      `_git_log_follow_renames`, which is deleted.
- [ ] `TestGitLogFollowCap::test_subprocess_failure_returns_empty` — DELETE: same.
- [ ] `TestDoDocsContract::test_hook_invocation_under_pr_mode` (`:412`, `:424`) —
      REPLACE: it patches `_commit_current_branch` and asserts
      `mock_commit.assert_called_once()`. Rewrite to assert the hook fires **and**
      that no commit occurred (`git status --porcelain` non-empty in a real repo).
- [ ] `TestNonMarkdownApplyGuard::test_html_with_stale_term_in_attribute_left_untouched`
      (`:486`, `:503`) — UPDATE: drop the `_commit_current_branch` patch and the
      `assert_not_called()`; keep the untouched-content assertion.
- [ ] `TestNonMarkdownApplyGuard::test_markdown_sibling_still_rewritten` (`:516`) —
      UPDATE: drop the now-nonexistent patch target.
- [ ] The six `_push_branch_and_pr` patch sites (`:226`, `:256-259`, `:277`,
      `:294-297`, `:385`, `:602-605`) — UPDATE: the signature gains `files_touched`,
      so the `return_value` mocks stay but call assertions must accept the new arg.
      These stay mocked (they test `run_docs_auditor`'s orchestration, not git);
      the real-git coverage is added separately below.
- [ ] Any test asserting `_detect_renamed_link_fixes` / `_detect_renamed_symbol_fixes`
      — DELETE. (Grep confirms none exist today; re-verify at build time.)
- [ ] `tests/README.md:270` — UPDATE: the index row still says 62 tests for
      `test_docs_auditor_substrate.py`; it is 81 today and will change again.

New coverage required (new file `tests/unit/reflections/test_docs_auditor_git_surface.py`,
real git throughout — the filename keeps the `docs_auditor` keyword so
`tests/conftest.py` `FEATURE_MAP` auto-tags it `validation`):

- [ ] Staging set: after `_push_branch_and_pr`, the commit contains **exactly**
      `files_touched`. Seed an unrelated dirty file in the temp repo and assert it is
      **not** in `git show --name-only HEAD` and is still dirty afterward. This is the
      direct anti-regression for `git add -A`.
- [ ] Early-return restore: force each failure (`git push` to a nonexistent remote,
      `gh pr create` returning non-zero, `git add --` on a missing path) and assert
      HEAD is back on the starting ref, the created branch is gone, and
      `git status --porcelain` matches the pre-call state byte for byte.
- [ ] Failed-restore reporting: make `checkout` fail and assert the function reports
      failure and `run_docs_auditor` returns `status="error"`.
- [ ] Guard hoisting: with the daily cap set, assert `audit()` is never called and
      the tree is untouched.
- [ ] `files_touched == []` creates no branch and no commit.
- [ ] Sweeper close path: real repo + `gh` dispatcher. Assert a PR whose body
      contains `WITHHELD_PR_MARKER` is **not** closed and **no** `--delete-branch`
      is issued for it, and that an escalation issue is filed. Assert a non-marker
      stale PR is still closed.
- [ ] Sweeper auto-merge absence (anti-criterion): assert no `gh pr merge` is ever
      dispatched for any input.
- [ ] `agent/reflection_scheduler.py`: assert `mark_completed` receives
      `output_summary` equal to the reflection's returned `summary`. Add to
      `tests/unit/test_reflection_scheduler*.py` (or a new case in the nearest
      existing scheduler test file).

Test hygiene for this lane — every invocation:

```bash
cd /Users/valorengels/src/ai/.worktrees/docs-auditor-review-gate
POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/reflections/test_docs_auditor_git_surface.py -q
POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q
```

`POPOTO_TEST_DB=13` is mandatory and **DB 15 is forbidden** — popoto's pytest11
plugin flushes DB 15 before every test, and two other lanes are running
concurrently. Never bare `pytest`; `scripts/pytest-clean.sh` reaps xdist workers.

## Rabbit Holes

- **Rebuilding rename detection.** Three coupled fixes (query, chain-walk with cycle
  protection, frame-correct re-relativization) for a capability that has never once
  worked, at the moment we are trying to *reduce* unreviewed automated writes. Filed
  as #2741. Delete, do not repair.
- **Designing a general review-gate framework for all reflections.** Other reflections
  also write. This plan fixes one auditor. A framework is a different project.
- **Making `_write_liveness` useful.** Tempting because the code is right there, but
  the dashboard `output_summary` path already carries the same information through a
  surface with a real reader. Building a second one is the parallel-run migration
  Principle 1 forbids. Scoped to #2743 as deletion.
- **Adding a `do-not-auto-merge` GitHub label.** PR #2728's own comment explains why
  it did not: the label does not exist in this repo and `gh pr create --label` fails
  outright when it is missing. Moot anyway once auto-merge is deleted.
- **Rewriting the PR body to lift the withheld disqualification.** The issue floats
  "making the disqualification liftable once reviewed". With auto-merge gone there is
  no disqualification to lift — a reviewed PR is merged by a human. Do not build a
  body-rewriting mechanism.
- **Fixing the `/do-docs` skill's Step 4 push-ancestry guard, the memory-refresh hook
  body (#1249), or the skill's `status: "ok"` handling.** All adjacent, all separate.
- **Auditing every other `git add -A` in the repo.** In scope: `reflections/docs_auditor.py`.

## Risks

### Risk 1: The cascade loses its commit and the `/do-docs` agent does not pick it up

**Impact:** docs fixes are applied to the working tree and never committed. On a
feature branch that means the fixes silently vanish at the next checkout, or worse,
get swept into an unrelated commit.
**Mitigation:** the skill's Step 4 already ends in `git add -A && git commit` — the
commit is not new machinery, only the review in front of it. The three doc edits are
explicit acceptance criteria. Add a Verification anti-criterion asserting the phrase
"commits them itself" appears nowhere in the repo, and a positive check that the
skill-context declares the new ownership.

### Risk 2: Deleting the rename detectors removes a capability someone believes in

**Impact:** a reviewer reads "deleted three detectors" as a regression.
**Mitigation:** spike-1 is reproducible in two lines against real repo history and is
recorded in both this plan and #2741. The `_detect_readme_broken_entries` edit is
provably zero-behavior-change. Present the positive control (a HEAD-present path
*does* return rename data) so the finding is not mistaken for a broken experiment.

### Risk 3: Landing while a rotation is in flight

**Impact:** the daily reflection is mid-`checkout -b` in the shared main checkout
when the code changes underneath it, leaving a wedged tree — exactly the failure this
plan fixes.
**Mitigation:** the Prerequisites table gates on `REDIS_RUNNING_KEY` being unset, a
clean shared checkout, and no open `docs-audit/*` PR. Note that the **deployed** code
does not change at merge — the worker keeps running the old module until the next
`/update` service restart. Run `/update` deliberately, after confirming no rotation
is in flight, rather than letting the change land silently at an arbitrary time.

### Risk 4: Removing auto-merge lets docs PRs accumulate

**Impact:** a growing pile of open `docs-audit/*` PRs.
**Mitigation:** rotation is capped at 1 PR per calendar day by
`_daily_pr_cap_reached`, and the sweeper still closes non-withheld PRs at 14 days.
Steady state is bounded at ~14 open PRs worst case, and in practice far fewer. If
this becomes real, the answer is routing them to `/do-pr-review`, not resurrecting
unreviewed merges.

### Risk 5: The scheduler `output_summary` change affects every reflection

**Impact:** a reflection returning a huge or malformed `summary` now writes it to the
model and renders it on the dashboard.
**Mitigation:** guard with the same `isinstance(result, dict)` check that already
protects `projects`, coerce to `str`, and truncate. The field is already nullable and
already rendered for other producers, so the surface is not new.

## Race Conditions

### Race 1: A concurrent lane dirties the shared main checkout mid-rotation

**Location:** `reflections/docs_auditor.py` `run_docs_auditor:1737` (dirty-tree guard)
through `_push_branch_and_pr:1436-1443` (restore).
**Trigger:** rotation passes the dirty-tree guard, then another process writes to
`/Users/valorengels/src/ai` before `git add`. With `git add -A` that foreign work is
committed into a docs PR; with the narrowed staging it is not, but a `checkout -f`
restore could still discard it.
**Data prerequisite:** `files_touched` must be resolved before staging.
**State prerequisite:** the restore must not touch paths outside `files_touched`.
**Mitigation:** stage an explicit path list (Q3), and scope the restore — `checkout -f`
the starting **ref** while `git checkout -- <files_touched>` handles the auditor's own
paths, so foreign dirt is preserved rather than destroyed. Assert in a real-git test
that an unrelated dirty file survives both the commit and the restore.

### Race 2: Two machines run the sweeper simultaneously

**Location:** `run_docs_branch_sweeper:1965` (`REDIS_SWEEPER_RUNNING_KEY` SETNX).
**Trigger:** `do-docs-branch-sweeper` has **no `project_key`** in
`config/reflections.yaml:214-222`, so unlike `docs-auditor` it runs on **every**
machine. Two machines could both evaluate the same PR.
**Data prerequisite:** the Redis lock is per-machine only if the machines share a
Redis instance.
**State prerequisite:** close and issue-filing must be idempotent.
**Mitigation:** pre-existing, not introduced here, and the operations are naturally
idempotent (`gh pr close` on a closed PR is a no-op; `_file_issue_if_new` dedups via
`_open_issue_exists` which is explicitly a cross-machine check). Note it in the docs
so the next reader does not rediscover it.

### Race 3: Guard hoisting widens the check-to-use window

**Location:** `run_docs_auditor` preflight vs. `_push_branch_and_pr`.
**Trigger:** `_has_open_pr_for_slug` now runs before `audit()` instead of immediately
before the branch creation, so a PR opened during the substrate run is missed and a
duplicate PR is created.
**Data prerequisite:** none.
**State prerequisite:** at most one open PR per slug.
**Mitigation:** accepted and bounded. `REDIS_RUNNING_KEY` already serializes rotation
globally, the daily cap allows only one PR per day regardless, and the failure mode
is a duplicate PR (visible, closeable) rather than a wedged checkout (invisible,
blocking). Do **not** re-check inside `_push_branch_and_pr` — a late guard return is
the exact structure this plan is removing.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2741] Rebuilding rename detection correctly — repaired query,
  chain-walking with cycle protection, and frame-correct re-relativization. This plan
  deletes the dead code; #2741 carries the spike evidence and the decision about
  whether the capability is worth having at all.
- [SEPARATE-SLUG #2743] Deleting `_write_liveness` and its two zero-reader Redis keys.
  This plan routes the same information to the dashboard via `output_summary`;
  removing the dead channel is a clean follow-up and keeping both would be the
  parallel-run migration Principle 1 forbids.
- [ORDERED] Running `/update` to restart the worker so the new module is actually
  deployed. Must wait until PR merge, and must be run deliberately at a moment when
  no rotation is in flight — the code on disk changes at merge, but the running
  worker keeps the old module until the restart.
- [EXTERNAL] Manually clearing the shared main checkout if it is already wedged from a
  pre-change rotation. A human must inspect the dirt and decide whether it is
  auditor output or another lane's in-flight work before anything is discarded.

## Update System

No `/update` **script or skill changes** are required — this touches no dependencies,
no config files, and no new binaries.

One deployment note that is not a code change: `.claude/skills/update/SKILL.md:68`
runs `pytest tests/unit/test_docs_auditor_substrate.py` as a smoke test. That file is
modified by this plan (two tests deleted, three rewritten), so the smoke test must be
green before merge or `/update` will fail on every machine. The new real-git file is
deliberately **not** added to the update smoke test — it shells out to git and `gh`
and is too slow for that path.

Deployment behavior to state plainly: merging changes the code on disk; it does not
change the running worker. The docs auditor keeps executing the old module until the
next `./scripts/valor-service.sh restart` that `/update` performs.

## Agent Integration

No agent integration required — this is a reflection-internal change. There is no MCP
tool and no `pyproject.toml [project.scripts]` entry for the docs auditor, and none is
added. Both callers are unchanged in *how* they are reached:

- Caller A remains `config/reflections.yaml:265-273` →
  `reflections.docs_auditor.run_docs_auditor`, invoked by the scheduler.
- Caller B remains the `/do-docs` skill's bash block, invoked by the agent as part of
  the SDLC docs stage. Its **contract** changes (the skill now commits), but its
  invocation surface does not.

The one cross-cutting change, `agent/reflection_scheduler.py` passing
`output_summary`, uses a model field and a dashboard renderer that both already exist.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/docs-auditor.md` to describe the new commit and review
      contract as the **only** status quo — no migration notes, no "previously"
      sections. Specific passages: the ASCII diagram (`:27-28`, currently
      "commit on current branch"), the Caller B section (`:40-46`), and the
      memory-refresh-hook description (`:153-157`, currently "after fixes are applied
      and committed" — the hook now fires after apply, before any commit).
- [ ] In the same file, fix the stale path `.claude/skills/do-docs/SKILL.md` at `:42`
      and `:300` — the real path is `.claude/skills-global/do-docs/SKILL.md`.
- [ ] Update the `## Branch Sweeper` section (`:164`) to remove auto-merge and
      describe the withheld-PR exemption.
- [ ] Update the `## Tests` section (`:279-289`) to name the new real-git test file.
- [ ] `docs/features/reflections.md:147-152` and the registry row at `:205` — update
      the Caller B contract note.
- [ ] `docs/features/README.md` — verify the docs-auditor rows (`:65`, `:251`) still
      describe the feature accurately.

### Skill Documentation (the review gate itself)

- [ ] `.claude/skills-global/do-docs/SKILL.md` Step 2d (`:220-221`) — replace the
      "do not re-commit" instruction with the carry-`files_touched`-into-Step-4
      contract. **Note this file is a hardlink** to `~/.claude/skills/`
      (`scripts/update/hardlinks.py`); the edit propagates on `/update`.
- [ ] `.claude/skills-global/do-docs/SKILL.md` Step 4 (`:252`) — the expected file set
      is the Step 2 task list **∪** the substrate's `files_touched`.
- [ ] `.claude/skill-context/do-docs.md` (`:143-152`) — rewrite the description and
      delete the imperative.
- [ ] `.claude/skills-global/new-audit-skill/BEST_PRACTICES.md:32` and
      `.claude/skills-global/new-audit-skill/SKILL.md:26` — both cite the docs auditor
      as the canonical "skill commits results" pattern to copy. Leaving them is a
      historical artifact that will propagate the deleted contract into new skills.

### Inline Documentation

- [ ] `audit()` docstring — state that it never commits and that the caller owns the
      commit.
- [ ] `_push_branch_and_pr` docstring — document the explicit staging set and the
      verified-restore postcondition.
- [ ] `__main__` block docstring (`:2156-2158`) — state that it leaves a dirty tree.
- [ ] `config/settings.py:215` — verify the `git_subprocess_s` consumer docstring is
      still accurate after `_commit_current_branch` is deleted.

## Success Criteria

- [ ] No code path in `reflections/docs_auditor.py` creates a git commit that has not
      passed a review gate; the gate is named and documented for each caller.
- [ ] `_commit_current_branch` is gone; `.claude/skill-context/do-docs.md` and
      `.claude/skills-global/do-docs/SKILL.md` state the new commit ownership, and no
      "do not re-commit, it commits them itself" instruction survives anywhere.
- [ ] `git add -A` does not appear in `reflections/docs_auditor.py`; rotation stages
      only `files_touched`.
- [ ] `_pr_is_auto_merge_eligible` is gone and the sweeper never runs `gh pr merge`.
- [ ] A rotation run that returns early leaves the shared main checkout exactly as it
      found it, and does not report `status="ok"` for a pass whose output it discarded.
- [ ] A run that withholds fixes files a deduped GitHub issue, and the sweeper never
      closes or deletes the branch of a PR carrying `WITHHELD_PR_MARKER`.
- [ ] `agent/reflection_scheduler.py` passes `output_summary` to `mark_completed`.
- [ ] The three rename detectors are deleted per Q6, with the rationale and the spike
      evidence recorded in this plan and in #2741.
- [ ] Real-git tests in a temp repo cover: the staging set, the early-return restore,
      the failed-restore error path, and the sweeper's close path. No new
      `unittest.mock.patch` over `subprocess.run` for the git surface.
- [ ] `docs/features/docs-auditor.md` describes the new contract as the only status
      quo — no migration notes, no "previously" sections.
- [ ] Tests pass (`/do-test`), run with `POPOTO_TEST_DB=13` via
      `scripts/pytest-clean.sh`.
- [ ] Documentation updated (`/do-docs`).
- [ ] No xfail tests relate to this bug (verified at plan time: none exist).

## Team Orchestration

### Team Members

- **Builder (substrate git surface)**
  - Name: `substrate-builder`
  - Role: the `reflections/docs_auditor.py` changes for Q1-Q5
  - Agent Type: builder
  - Resume: true

- **Builder (dead code removal)**
  - Name: `deadcode-builder`
  - Role: Q6 deletion of the three rename detectors
  - Agent Type: builder
  - Resume: true

- **Test engineer (real git surface)**
  - Name: `git-test-engineer`
  - Role: the new real-git test file and the existing-test dispositions
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `contract-documentarian`
  - Role: skill bodies, skill-context, feature docs
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `gate-validator`
  - Role: verify every acceptance criterion, especially the anti-criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 0. Preflight

- **Task ID**: preflight
- **Depends On**: none
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm PR #2728 is `MERGED`; if not, stop and report. Rebase this lane onto the
  post-#2728 `main`.
- Run every row of the Prerequisites table. In particular confirm
  `REDIS_RUNNING_KEY` is unset and `/Users/valorengels/src/ai` is clean.
- Do not enter any worktree other than `.worktrees/docs-auditor-review-gate`, and
  never `.worktrees/docs-auditor-rename-guard`.

### 1. Delete dead rename detection (Q6)

- **Task ID**: build-deadcode
- **Depends On**: preflight
- **Validates**: tests/unit/test_docs_auditor_substrate.py
- **Informed By**: spike-1 (detectors provably never fire), spike-2, spike-3
- **Assigned To**: deadcode-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `_detect_renamed_link_fixes`, `_detect_renamed_symbol_fixes`,
  `_git_log_follow_renames`, `GIT_LOG_FOLLOW_CAP`, the `_RENAME_QUERY_COUNT` global
  and its reset in `audit()`, and the two call sites in `audit()`'s detector loop.
- In `_detect_readme_broken_entries`, remove the rename branch: a broken entry
  unconditionally yields `(line, "")`. Verify by inspection that this is
  zero-behavior-change given `renames` is always empty.
- Delete `TestGitLogFollowCap` (both tests).
- Sequenced first and alone so the deletion diff is reviewable in isolation.

### 2. Substrate git surface (Q1, Q3, Q4)

- **Task ID**: build-substrate
- **Depends On**: build-deadcode
- **Validates**: tests/unit/test_docs_auditor_substrate.py, tests/unit/reflections/test_docs_auditor_git_surface.py (create)
- **Informed By**: spike-5 (files_touched is the complete write set)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: git / shared-checkout safety
- Q1: delete `_commit_current_branch` and its call site; keep `refresh_docs_in_memory`
  firing on the applied set.
- Q3: add the required `files_touched` parameter to `_push_branch_and_pr`; replace
  `git add -A` with `git add -- <files_touched>`; return early without creating a
  branch when the list is empty.
- Q4: hoist `_daily_pr_cap_reached` and `_has_open_pr_for_slug` into
  `run_docs_auditor`'s preflight, before `audit()`; record the starting ref on entry
  to `_push_branch_and_pr`; make the `finally` restore verified (`checkout -f` the
  starting ref, delete the created branch, assert not dirty) and scoped so foreign
  dirt survives (Race 1); make a failed restore a reported failure.
- Q4: route outcomes as `ok` / `skipped` / `error` and ensure `_write_liveness`,
  `_update_rotation_hash`, and the success Telegram fire only on `ok`.

### 3. Escalation and the review gate (Q2, Q5)

- **Task ID**: build-escalation
- **Depends On**: build-substrate
- **Validates**: tests/unit/reflections/test_docs_auditor_git_surface.py
- **Informed By**: spike-4 (gh issue is the durable channel; output_summary is one line)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- Q2: delete `_pr_is_auto_merge_eligible` and the auto-merge branch of
  `run_docs_branch_sweeper`.
- Q5: file a deduped issue via `_file_issue_if_new` when `fixes_withheld > 0`.
- Q5: the sweeper must skip close **and** branch deletion for any PR whose body
  contains `WITHHELD_PR_MARKER`, filing the escalation issue instead.
- Q5: wire `output_summary` in `agent/reflection_scheduler.py:515`, guarded by the
  existing `isinstance(result, dict)` check, coerced to `str` and truncated.

### 4. Real-git test surface

- **Task ID**: build-tests
- **Depends On**: build-escalation
- **Validates**: tests/unit/reflections/test_docs_auditor_git_surface.py (create), tests/unit/test_docs_auditor_substrate.py
- **Assigned To**: git-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Domain**: testing — real integrations, no mocks over the subject under test
- Create `tests/unit/reflections/test_docs_auditor_git_surface.py` following
  `test_merged_branch_cleanup.py`: real `git init` repo, `PROJECT_ROOT` monkeypatched,
  a `cmd[0] == "gh"` dispatcher as `subprocess.run` side-effect that falls through to
  real `subprocess.run` for `git`.
- Cover every bullet in the "New coverage required" list under **Test Impact**.
- Apply every disposition in the existing-test list under **Test Impact**.
- Update the `tests/README.md` index row.
- Every run: `POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh <file> -q`. Never DB 15,
  never bare pytest.

### 5. Contract documentation

- **Task ID**: document-contract
- **Depends On**: build-escalation
- **Assigned To**: contract-documentarian
- **Agent Type**: documentarian
- **Parallel**: true
- Apply every checkbox in the **Documentation** section.
- Describe only the new status quo. No "previously", no migration notes.
- Remember `.claude/skills-global/do-docs/SKILL.md` is a hardlink; edit it in the repo.

### 6. Final validation

- **Task ID**: validate-all
- **Depends On**: build-tests, document-contract
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the **Verification** table, including the anti-criteria.
- Confirm each **Success Criteria** checkbox.
- Confirm the shared main checkout at `/Users/valorengels/src/ai` is still clean and
  that no worktree other than this slug's was touched.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass (substrate) | `POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Tests pass (real git surface) | `POPOTO_TEST_DB=13 ./scripts/pytest-clean.sh tests/unit/reflections/test_docs_auditor_git_surface.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No `git add -A` in the auditor | `grep -c '"-A"' reflections/docs_auditor.py` | match count == 0 |
| Self-commit helper gone | `grep -rc '_commit_current_branch' reflections/ .claude/ docs/features/` | match count == 0 |
| Auto-merge predicate gone | `grep -rc '_pr_is_auto_merge_eligible' reflections/` | match count == 0 |
| Sweeper never merges | `grep -c '"merge"' reflections/docs_auditor.py` | match count == 0 |
| Old contract instruction gone | `grep -rn 'commits them itself' .claude/ docs/` | exit code 1 |
| Dead rename query gone | `grep -rc '_git_log_follow_renames\|--follow' reflections/docs_auditor.py` | match count == 0 |
| New commit ownership declared | `grep -c 'files_touched' .claude/skill-context/do-docs.md` | output > 0 |
| Scheduler wires the summary | `grep -c 'output_summary' agent/reflection_scheduler.py` | output > 0 |
| No new subprocess mocking of the git surface | `grep -c 'patch("subprocess.run"' tests/unit/reflections/test_docs_auditor_git_surface.py` | match count == 0 |
| Real git in the new test file | `grep -c '"init"' tests/unit/reflections/test_docs_auditor_git_surface.py` | output > 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/reflections/test_docs_auditor_git_surface.py` | exit code 1 |
| Shared checkout clean after run | `git -C /Users/valorengels/src/ai status --porcelain` | output does not contain `docs/features` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Q6 / #2741 — confirm the deletion.** spike-1 proves the three rename detectors
   have never produced a fix, so this plan deletes them rather than repairing a
   target-selection bug in unreachable code. The rebuild question is filed as #2741
   with all three prerequisites written up. This is the one resolution that
   contradicts the issue's framing outright — confirm, or say the word and the
   rebuild folds back into this slug (it would push the appetite to Large).
2. **Q2 — confirm unreviewed auto-merge dies.** The predicate is inverted today
   (any review disqualifies), and repairing it yields "merge approved PRs", which
   `/do-merge` already does. This plan deletes it. The cost is that docs PRs now
   need a human or `/do-pr-review`, bounded to 1 new PR per calendar day. Acceptable?
3. **Q5 — is "leave the withheld PR open forever plus a deduped issue" the right
   stopping condition?** The alternative is closing it without `--delete-branch` so
   the branch survives for recovery. Leaving it open keeps the fixes one click from
   merging; closing it keeps the PR list tidy. This plan chooses leaving it open,
   because an open PR with an open issue is the loudest available signal and the
   silent loop is the actual defect.
