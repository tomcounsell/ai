---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-06
tracking: https://github.com/tomcounsell/ai/issues/3050
last_comment_id:
---

# docs-auditor: restore and escalate when the rotation aborts after writing

## Problem

The docs auditor's daily rotation writes fixes into the **shared main checkout** at `~/src/ai`, then hands the dirty tree to `_push_branch_and_pr`, which branches, commits, pushes, opens a PR, and restores the checkout in its own `finally`. PR #2887 (issue #2739) put that restore in place and it works for every failure inside the push sequence.

It does not work for a failure *before* the push sequence starts. `reflections/docs_auditor.py::run_docs_auditor` runs the substrate (`audit(...)`), then filters withheld fixes, files one GitHub issue per withheld entry through `gh`, checks the zero-diff gate, and only then calls `_push_branch_and_pr`. Every one of those steps can raise. When one does, the outer `except Exception` in `run_docs_auditor` catches it, logs a warning, and returns `{"status": "error"}`. `_push_branch_and_pr` was never entered, so its `finally` never ran, so `_restore_checkout` never ran.

The code says so itself. A `NOTE (#3050)` sits immediately above the `_push_branch_and_pr(...)` call in `run_docs_auditor`, stating the gap and deferring the decision to this issue.

**Current behavior:**

1. Rotation writes N markdown files into the shared main checkout.
2. An exception fires in the window before the push (say `gh issue create` times out inside the withheld-filing loop).
3. `run_docs_auditor` returns `{"status": "error"}`. Nothing restores the checkout. The auditor's edits sit uncommitted on `main`.
4. `agent/reflection_scheduler.py` reads only `result.get("projects")` from that dict, so `status="error"` reaches no human surface. The failure is a log line nobody reads.
5. Every subsequent rotation hits the step-3 dirty-tree guard, returns `{"status": "skipped"}`, and files nothing — by design, because that guard cannot tell the auditor's dirt from a peer lane's routine uncommitted work.
6. The auditor is now permanently dormant and silent. A human notices when they eventually run `git status` in the shared checkout and find stale docs edits they did not make.

The R5-1 `operational-failure` escalation added alongside #2887 does not cover this: it is inside the `if pr_url is None:` branch, which an exception in the window never reaches.

**Desired outcome:**

A rotation that writes and then aborts before the push cleans up after itself, in the same run, scoped to exactly the paths it wrote. If the cleanup does not fully succeed, that run files a distinct `operational-failure` issue naming the paths that need manual cleanup and stating what the restore actually achieved. The auditor never goes silently dormant behind a dirty tree it created.

## Freshness Check

**Baseline commit:** `5ae3cbb3dd4288daba1a506041ac0736e259ec63`
**Issue filed at:** 2026-08-28T03:11:14Z
**Disposition:** Minor drift — every claim in the issue still holds; the issue cited no line numbers, and all references below were re-derived by symbol at the baseline commit.

**Symbol references re-verified (never carried from memory or an older read):**

- `reflections/docs_auditor.py::run_docs_auditor` — the `NOTE (#3050)` comment block sits directly above the `pr_url = _push_branch_and_pr(slug, PROJECT_ROOT, files_touched, withheld=withheld)` call. Still present and still accurate. Locate it with `grep -n "NOTE (#3050)" reflections/docs_auditor.py`.
- `reflections/docs_auditor.py::_push_branch_and_pr` — `starting_ref = _current_ref(repo_root)` and the `starting_ref is None` early `return None` both sit **before** the `try`. The `finally` calls `_restore_checkout(repo_root, starting_ref, branch, files_touched)`. Confirmed.
- `reflections/docs_auditor.py::_restore_checkout` — signature is `(repo_root: Path, starting_ref: str, branch: str, files_touched: list[str]) -> bool`. Scoped by construction: `git checkout <starting_ref>`, `git checkout HEAD -- <files_touched>`, conditional `git branch -D <branch>` gated on `git rev-parse --verify --quiet refs/heads/<branch>`, then two postconditions. Confirmed.
- `reflections/docs_auditor.py::audit` — no top-level exception handler. `touched` is a local list appended inside the per-file loop; the advisory `_file_issue_if_new` loop and `_detect_orphan_plan_issues(root)` run **after** that loop and can raise, discarding `touched` with the frame. Confirmed.
- `reflections/docs_auditor.py::run_docs_auditor` step 3 dirty-tree guard — the comment explaining why it files nothing ("a filing guard would mint issues blaming the auditor for a peer's dirt", "Q4 item 5") is present at the baseline. This is the evidence that kills option (b); see Solution.
- `agent/reflection_scheduler.py` — reads `result.get("projects")` from the reflection's return dict. Nothing else in that dict reaches a human surface. Confirmed by `grep -n "projects" agent/reflection_scheduler.py`.
- `reflections/docs_auditor.py` module constant `_RECURRING_CONDITION_CATEGORIES = frozenset({"vault-drift", "operational-failure"})` — the new escalation's category must be one of these or its Redis fast-path will suppress a genuine recurrence for 30 days after a human closes the first issue.

**Cited sibling issues/PRs re-checked:**

- #2739 — CLOSED. Its PR #2887 merged 2026-08-28T03:43:52Z, ~30 minutes after this issue was filed. Scoped the restore into `_push_branch_and_pr`'s `finally`; that is exactly the boundary this issue extends.
- PR #2887 — MERGED. Its review flagged this window as non-blocking tech debt, which is how #3050 was born.

**Commits on main touching `reflections/docs_auditor.py` since the issue was filed:**

- `7ccd27d5d` review-gate every write, report broken .md links (#2739, #2834) — the change this issue is a follow-up to. Root cause unchanged.
- `974be6532` route Telegram notifications by audited repo root (#3077) — irrelevant; touches `_resolve_notify_chat` / `_send_telegram_notification`.
- `6c68f29ab` resolve installed-package citations, exclude plan docs from outbound links — irrelevant; touches detectors, not the write/push path.
- `8934583dc` (#3186) delete `_write_liveness` Redis channel, rehome vault count — **rewrote overlapping regions of `run_docs_auditor`**. It removed the liveness channel and moved `_run_vault_drift_detection` into step 3b. It did **not** close this window: the `NOTE (#3050)` marker survives it at the baseline commit. Every symbol reference above was re-derived after this commit landed.

**Active plans in `docs/plans/` overlapping this area:** none target `run_docs_auditor`. `docs/plans/sibling-reflections-hardcode-eng-valor.md` (issue #3072, OPEN) touches `FALLBACK_ENG_CHAT`, which lives at the top of `reflections/docs_auditor.py` and is read only inside `_resolve_notify_chat` — far from every region this plan edits. Expect a trivial rebase; this plan deliberately plans no edits in that region.

**Notes:** No expected-failure (`xfail`) test covers this bug anywhere in `tests/` — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` returns nothing auditor-related, so there is no xfail to convert.

## Prior Art

- **#2739 / PR #2887** — *docs_auditor is its own committer: put a review gate in front of every write it makes.* Introduced `_restore_checkout`, made it verified (postconditions on HEAD and on the scoped `git status --porcelain`), made a failed restore return `None` from `_push_branch_and_pr` even on a successful PR, and added the R5-1 `operational-failure` escalation on the `pr_url is None` branch. **Succeeded** for its stated scope. This plan extends that same mechanism one step earlier; it does not replace it.
- **PR #2887's own review** — flagged this window explicitly as non-blocking tech debt and filed #3050. The `NOTE (#3050)` comment in the code is the paper trail.
- **#3186 / commit `8934583dc`** — deleted the `_write_liveness` Redis channel from the auditor. Relevant as a warning, not as a fix: it is why every reference in this plan was re-derived by symbol rather than reused.
- **#2728 / PR #2728, #2782 / PR #2782** — word-anchored stale terms, the path-existence invariant, and the migration-context hatch. These built the `withheld` list that the in-window issue-filing loop iterates, which is the most likely place for an exception to fire. Relevant as context for the failure injection point, not as prior attempts at this fix.

No prior attempt to close *this* window exists. There is no "Why Previous Fixes Failed" section in this plan because there were no previous fixes.

## Research

No relevant external findings — proceeding with codebase context. The work is purely internal: Python control flow and `git` subprocess calls already in use in this module. No new library, API, or ecosystem pattern is involved, so Phase 0.7's skip condition applies.

## Data Flow

One rotation run, traced through `reflections/docs_auditor.py::run_docs_auditor`. The **write window** is marked; today it has no restore owner.

1. **Entry point** — `agent/reflection_scheduler.py` invokes the `docs-auditor` reflection callable on its daily schedule and reads only `result["projects"]` from what comes back.
2. **Preflight (no writes)** — auth probe, Redis SETNX lock, dirty-tree guard, `_run_vault_drift_detection`, `_select_primary_doc` rotation pick, then the daily-cap and open-PR guards. Every failure here returns before anything touches the disk, so no restore is owed.
3. **Substrate write** — `audit(primary_path=primary, scope_mode="rotation", apply_mode="apply", ...)`. Inside, `_apply_fixes_to_file` rewrites markdown files in place and appends each to a local `touched` list. **The shared main checkout is now dirty.** After the write loop, `audit` files advisory issues through `_file_issue_if_new` (a `gh` subprocess) and runs `_detect_orphan_plan_issues`. An exception in either discards `touched` and propagates out of `audit`.
4. **⟨WRITE WINDOW — no restore owner today⟩** — back in `run_docs_auditor`: unwrap each withheld regex source with `re.sub`, file one issue per withheld entry through `_file_issue_if_new` (another `gh` subprocess, bounded by `ISSUE_FILING_PER_RUN_CAP`), then the zero-diff gate (`_git_diff_quiet`), `_update_rotation_hash`, and on the withheld-zero-diff path `_send_telegram_notification`. Any exception here unwinds to `run_docs_auditor`'s outer `except Exception`, which restores nothing.
5. **Push** — `_push_branch_and_pr` reads `starting_ref`, then in a `try` branches, `git add -- <files_touched>`, commits, pushes, and calls `gh pr create`. Its `finally` calls `_restore_checkout(repo_root, starting_ref, branch, files_touched)` and a failed restore forces the return value to `None`.
6. **Failure escalation** — `pr_url is None` files one `operational-failure` issue titled `docs-auditor: rotation failed to produce a PR for {slug}` and returns `{"status": "error"}`.
7. **Success** — memory-refresh hook, Telegram notification, `_update_rotation_hash(project_key, files_touched)`, return `{"status": "ok"}`.
8. **Lock release** — outermost `finally` calls `_release_lock`.
9. **Next day** — if step 4 aborted, the step-2 dirty-tree guard fires, returns `{"status": "skipped"}`, files nothing. Repeat forever.

The fix inserts a restore owner around steps 3–5 and an escalation on that owner's failure path, so the flow can never reach step 9 with dirt the auditor created.

## Architectural Impact

- **New dependencies:** none. No new import, service, or library. Everything used is already in `reflections/docs_auditor.py`.
- **Interface changes:** two, both internal to the module and both private:
  - `_restore_checkout(repo_root, starting_ref, branch, files_touched)` — `branch` widens to `str | None`, where `None` means "no branch was created, skip the branch delete". The existing `git rev-parse --verify --quiet refs/heads/<branch>` gate already makes a nonexistent branch harmless; `None` makes the intent explicit instead of relying on a lookup miss.
  - `_push_branch_and_pr(slug, repo_root, files_touched, withheld=None)` — gains a required keyword `starting_ref: str` and stops calling `_current_ref` itself. The ref is now captured once by the caller **before** the substrate write, which is the only point at which it is guaranteed to name the pre-write state.
- **Coupling:** slightly reduced. Ownership of "the ref this run started on" moves up to the one function that owns the whole write lifecycle, instead of being re-derived inside the callee after the tree is already dirty.
- **Data ownership:** unchanged. Rotation state, the Redis lock, and the issue-dedup keys are all untouched. No Popoto model changes, so no migration is required.
- **Reversibility:** high. The change is additive control flow in one module plus two private signature widenings. Reverting is a single-file revert.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the scope decision is made in this plan and justified from code evidence)
- Review rounds: 1

One module, two private signature widenings, one new escalation, and a focused test class. The cost is in getting the failure-injection tests right, not in the production diff.

## Prerequisites

No prerequisites — this work has no external dependencies. The tests use the existing `repo` fixture in `tests/unit/reflections/test_docs_auditor_git_surface.py` (a real `git init` checkout with a real bare local `origin`) and the existing `fake_redis` fixture; both `git` and `gh` are already intercepted or real in that file.

## Solution

### The decision: widen the try/finally (option a). Reject the guard-skip escalation (option b).

The issue offers two options. This plan takes **(a) widen the restore to cover substrate-write-through-push**, and explicitly **rejects (b) a dirty-tree-guard-skip escalation**. The reason is written in the code the issue is about:

> `run_docs_auditor` step 3, dirty-tree guard: *"Deliberately files nothing: `_git_dirty` tests the whole shared main checkout, where concurrent lanes routinely hold uncommitted work as a matter of routine, so a filing guard would mint issues blaming the auditor for a peer's dirt. The escalation belongs on the failure path below, which knows it caused the dirt — this guard, which cannot know, stays quiet (Q4 item 5)."*

Option (b) asks that guard to do the one thing a prior review already decided it must not do. `~/src/ai` is a shared main checkout with several agents working in it concurrently; a dirty tree there is the normal case, not an alarm. A guard-skip escalation would file "the checkout needs manual cleanup" against a peer's in-flight work, repeatedly, and the auditor's genuine wedge would be one indistinguishable entry in that flood. It is also temporally detached: the escalation would fire on the *next* run, hours after the run that caused the dirt, with no record of which paths were the auditor's.

Option (a) puts the cleanup where the knowledge is. The run that wrote the files knows exactly which files it wrote, knows the ref it started on, and can restore both — scoped, with no `checkout -f`, no `reset --hard`, no `clean`, preserving foreign dirt outside its own path set, exactly as `_restore_checkout` already does on the push path.

**This plan also adds an escalation, and that escalation is not option (b).** It is a *same-run* escalation on the widened handler's own failure path — the identical shape and category as the existing R5-1 `operational-failure` issue, filed by the run that caused the dirt, naming the exact paths, and stating the restore outcome it actually observed. Scoped that way it cannot fire on a peer's dirt and cannot fire on a later run. Without it, a restore that fails inside the new handler would be as silent as the bug being fixed, because `status="error"` reaches nobody: `agent/reflection_scheduler.py` reads only `result["projects"]`.

### Key Elements

- **One ref capture, taken before the first write** — `run_docs_auditor` calls `_current_ref(PROJECT_ROOT)` once, after the preflight guards and before `audit(...)`. If it returns `None`, the run returns `skipped` *before writing anything*, so no restore is owed. `_push_branch_and_pr` stops reading the ref itself and receives it as a required keyword. This also closes the second uncovered sub-window found during recon: `_push_branch_and_pr`'s own `starting_ref is None` early return, which today exits over a dirty tree before its `try` is entered.
- **`audit()` never loses its write ledger** — `audit` gains a top-level guard so an exception after the write loop returns `_ok_result("error", files_touched=touched, fixes_applied=..., withheld=...)` instead of propagating and discarding `touched`. A caller cannot restore paths it was never told about, and the post-write `_file_issue_if_new` / `_detect_orphan_plan_issues` calls in `audit` are the most likely exception source in the whole window.
- **A single restore owner around write-through-push** — an `except Exception` in `run_docs_auditor` wrapping the region from `audit(...)` through `_push_branch_and_pr(...)`, which calls `_restore_checkout(PROJECT_ROOT, starting_ref, None, files_touched)` and then escalates. Plus an explicit check of `result["status"] == "error"` immediately after `audit` returns, routing the Part-2 error result down the same restore-and-escalate path instead of pushing a partially-written tree.
- **A distinct `operational-failure` escalation** — title `docs-auditor: rotation aborted after writing for {slug}`, deliberately different from R5-1's `docs-auditor: rotation failed to produce a PR for {slug}` so the two failure modes stay distinguishable in the issue tracker and in the title-based dedup. Category `operational-failure`, which is already in `_RECURRING_CONDITION_CATEGORIES`, so the 30-day Redis fast-path does not suppress a genuine recurrence after a human closes it. Unlike R5-1, this body **does** state the restore outcome, because this handler observes it directly.

### Flow

Rotation preflight → guards pass → **capture `starting_ref`** → substrate writes N files → *(exception fires anywhere here)* → **restore `starting_ref` and discard exactly the N written paths** → file one `operational-failure` issue naming the paths and the restore outcome → return `{"status": "error"}` → next day's rotation finds a clean tree and runs normally.

### Technical Approach

Locate every edit site by symbol, never by remembered line number — `8934583dc` rewrote overlapping regions of this file four days after the issue was filed.

1. **`_restore_checkout`** — widen `branch: str` to `branch: str | None` and skip the `rev-parse` / `branch -D` block when it is `None`. Extend the docstring to say `None` means "no branch was created". No other behavior changes; both postconditions stay exactly as they are.
2. **`_push_branch_and_pr`** — add a required keyword-only `starting_ref: str`, delete the internal `starting_ref = _current_ref(repo_root)` read and its `is None` early return. Update the docstring to record that the caller now owns the ref, captured before the write.
3. **`audit`** — wrap the body from the detector loop through the advisory issue-filing block so that any exception returns `_ok_result("error", files_touched=touched, fixes_applied=total_fixes, issues_filed=issues_filed, fixes_withheld=len(withheld), withheld=withheld, extras={"reason": str(e)})` and logs a warning. The early returns above the loop (auth, scope resolution) already return results and stay as they are. Note in the docstring that a caller must branch on `status == "error"` with a non-empty `files_touched` as "wrote, then failed".
4. **`run_docs_auditor`** — capture `starting_ref` after the daily-cap/open-PR guards and before step 5; return `skipped` if it is `None` (see the stamping note below). Wrap steps 5 through 8 in a `try` / `except Exception` whose handler restores and escalates. Immediately after `audit` returns, route `status == "error"` into the same handler logic. Pass `starting_ref=starting_ref` into `_push_branch_and_pr`. Delete the `NOTE (#3050)` comment — the gap it describes no longer exists, and per the repo's no-legacy rule the comment must not survive as a historical artifact.
5. **Factor the restore-and-escalate body once** — the `audit`-returned-error path and the `except Exception` path do the same three things (restore, escalate, return error). Write it once as a small module-level helper (`_abort_after_write(...)` returning the error dict) so the two call sites cannot drift apart.

**Why the ref-read guard does not stamp the rotation hash.** The step-4b cap and open-PR guards stamp `_update_rotation_hash` because their condition is doc-specific: without a stamp, `_select_primary_doc` re-picks the same doc forever while the guard fires. A failed `_current_ref` read is doc-independent — it blocks every doc equally, so it cannot pin the rotation on one doc, and stamping would advance the rotation past a doc that was never audited. This guard deliberately does not stamp, and the code carries a comment saying so with that reason.

**Use `except Exception`, not `finally`, for the widened region.** The success path's restore is already owned by `_push_branch_and_pr`'s own `finally` and must not run twice. The intermediate `return`s inside the region (the zero-diff gate) leave nothing to restore by construction: that branch is reached only when `files_touched` is empty or `git diff --quiet` reports no diff. A `finally` would fire a pointless `git checkout` on every clean run.

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

_placeholder_
