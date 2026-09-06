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

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

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
