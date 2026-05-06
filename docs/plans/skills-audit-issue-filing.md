---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-05-06
tracking: https://github.com/tomcounsell/ai/issues/1299
last_comment_id:
---

# Skills-Audit Issue Filing with Two-Run Gate

## Problem

The `skills-audit` reflection (`reflections/auditing.py:516`) runs every project's `audit_skills.py` and aggregates findings, but it's read-only telemetry. FAIL findings land in the reflection record and worker log — nowhere else. With a current baseline of 0 FAIL / 0 WARN across 536 skills, the audit acts only as a quiet canary; when a real regression eventually appears it will be silently logged and forgotten until someone happens to scroll the reflection feed.

**Current behavior:** FAIL findings → string list in reflection record → no GitHub issue, no Telegram, no human action.

**Desired outcome:** FAIL findings → tracked GitHub issue in the appropriate repo, deduplicated, gated to filter audit-script regression bursts.

## Freshness Check

**Baseline commit:** 9a92bc36 (HEAD as of plan time)
**Issue filed at:** N/A — plan initiated from conversation, tracking issue created post-plan
**Disposition:** Unchanged

**File:line references re-verified:**
- `reflections/auditing.py:516` — `run_skills_audit` entrypoint — confirmed
- `reflections/auditing.py:464` — `_skills_audit_for_project` per-project body — confirmed
- `reflections/docs_auditor.py:569` — `_file_issue_if_new` template — confirmed (deduped via `REDIS_ISSUE_DEDUP_PREFIX` + 30-day TTL)
- `.claude/skills/do-skills-audit/scripts/audit_skills.py` — confirmed present, exposes `--no-sync --json` mode

**Cited sibling issues/PRs re-checked:** none cited.

**Active plans in `docs/plans/` overlapping this area:** none — `per-project-audit-reflections.md` and `daily-reflections-unification.md` are upstream substrate that this plan consumes; no overlap.

## Prior Art

- **`reflections/docs_auditor.py::_file_issue_if_new`** — Working template for gh-CLI issue filing with SHA-256 title-hash dedup and 30-day Redis TTL. Uses `REDIS_ISSUE_DEDUP_PREFIX = "docs_audit:issues_filed"`. Filed-only-on-success semantics (dedup key set after `gh issue create` returns 0). This plan ports the same pattern.
- **No prior failed attempts** — `skills-audit` has never had an action-taking arm; this is its first.

## Research

No relevant external findings — purely internal infrastructure change reusing an in-repo template.

## Data Flow

1. **Entry point**: Reflection scheduler invokes `run_skills_audit()` on its cron tick.
2. **Per-project iteration**: `run_per_project_audit` walks every project from `load_local_projects()`, calls `_skills_audit_for_project(project)` for each.
3. **Per-project audit**: subprocess invokes that repo's `.claude/skills/do-skills-audit/scripts/audit_skills.py --no-sync --json`, parses stdout, extracts FAIL findings.
4. **NEW — streak update**: For each FAIL finding `(skill, message)`, compute SHA-256 hash of a stable issue-title string. Look up `skills_audit:streak:{hash}` in Redis. Increment to N. Set 7-day TTL on the streak key (a finding that disappears for a full week resets cleanly).
5. **NEW — gate**: If streak == 1, do nothing this run. If streak ≥ 2 AND `skills_audit:issues_filed:{hash}` does not exist, fire `gh issue create` against the *project's* repo (cwd = project's repo_root). On success, set the dedup key with 30-day TTL.
6. **Aggregation** (unchanged): per-project `findings` and `summary` flow up into the reflection record. New field `issues_filed: int` rolls up across projects.
7. **Output**: Reflection record returned; existing dashboard/log surfaces unchanged. New issues appear in the project's GitHub.

## Architectural Impact

- **New dependencies**: none — reuses `gh` CLI (already required by `docs_auditor`), `hashlib` (stdlib), `popoto.redis_db.POPOTO_REDIS_DB` (already imported via `reflections/utils`).
- **Interface changes**: `_skills_audit_for_project` return dict gains optional `issues_filed: int`. `run_skills_audit` aggregate gains the same.
- **Coupling**: introduces a Redis dependency in `reflections/auditing.py` that wasn't previously needed for this code path. The dependency already exists in the `reflections/` package via `docs_auditor`, so this is a same-package import, not a new layer.
- **Data ownership**: two new Redis key namespaces — `skills_audit:streak:{hash}` (7d TTL) and `skills_audit:issues_filed:{hash}` (30d TTL). NOT Popoto-managed (no Model behind them — same pattern as `REDIS_ISSUE_DEDUP_PREFIX` in `docs_auditor`). The "no raw Redis on Popoto-managed keys" rule does not apply here; these are pure bookkeeping keys.
- **Reversibility**: trivial — delete the streak/file-issue helpers and the call site reverts to read-only telemetry. Existing Redis keys expire on their own TTL.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (decision points already pinned in this plan)
- Review rounds: 1 (standard PR review)

This is a ~150-line addition to a single module plus one short helper extracted alongside the existing template. The two-run gate adds 6 Redis ops per finding per run.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | Required to create issues |
| Redis reachable | `redis-cli ping` | Streak counter + dedup state |
| `audit_skills.py` present per project | `ls .claude/skills/do-skills-audit/scripts/audit_skills.py` | Reflection skips projects without it (existing behavior) |

## Solution

### Key Elements

- **Title hash function**: stable string → SHA-256 → first 16 hex chars. Title format: `[skills-audit] {skill_name}: {rule_id} — {short_message}`. Stable means same finding produces same hash across runs even if message wording changes slightly (rule_id is the deterministic anchor).
- **Streak counter**: Redis `INCR` on `skills_audit:streak:{hash}`, expire 7 days. Incremented exactly once per finding per run. Resets to 1 if the finding has been absent long enough for the key to expire.
- **Dedup key**: Redis `SET ... EX 86400*30 NX` on `skills_audit:issues_filed:{hash}` AFTER successful `gh issue create`. Same semantics as `docs_auditor._file_issue_if_new`.
- **Gate**: file issue iff `streak >= 2` AND dedup key does not exist.
- **Repo targeting**: `gh issue create --repo {owner}/{name}` derived from each project's repo_root (resolved via `gh repo view --json nameWithOwner -q .nameWithOwner` from cwd, cached per-project per-run).

### Flow

Reflection tick → per-project loop → run audit_skills.py → for each FAIL finding: hash title → INCR streak → if streak ≥ 2 and not yet filed: gh issue create in that project's repo → on success, set 30d dedup key → aggregate counts → reflection record.

### Technical Approach

- **Single-purpose helper**: `_file_skills_audit_issue_if_streaked(finding, repo_root) -> bool` in `reflections/auditing.py`, mirroring `docs_auditor._file_issue_if_new` but with the additional streak check up front. Returns True iff a new issue was filed this run.
- **Streak key TTL = 7 days**: the gate fires on two consecutive *runs*, not two consecutive findings within a fixed wall-clock window. With reflections running hourly, 7 days is generous slack — a finding can disappear for up to 168 hours and reappear without resetting. This deliberately favors not-filing over double-filing.
- **Flapping behavior** (decision pinned): if a finding shows up run 1 (streak=1), disappears run 2, reappears run 3 — the streak key still exists (7d TTL), so run 3 increments to streak=2 and fires. This is correct: a flapping FAIL is signal, not noise. The 7d window is long enough that genuine intermittent issues are caught; it's short enough that a finding fixed and forgotten won't re-fire weeks later.
- **Issue title format**: `[skills-audit] {project_slug}/{skill_name}: {rule_id}` — title-hash uses *only* the project_slug + skill_name + rule_id, NOT the message. This way, a finding whose message text gets reworded by a future audit_skills.py change still hits the same dedup key.
- **Issue body**: includes the full finding message, the rule severity, the audit_skills.py rule reference, the project, and a one-liner "this issue was filed by the skills-audit reflection after appearing on N consecutive runs."
- **Labels**: `skills`, `bug`. Per CLAUDE.md the `skills` label is for "skills, tools, or SDLC pipeline" work. FAIL findings are deterministic structural violations (broken sub-file links, missing frontmatter, malformed name field), so `bug` is appropriate — they are not unverified anomalies that would warrant the `investigation` label.
- **Manual flag scope** (decision pinned): do NOT add `--file-issues` to `audit_skills.py`. Keep issue-filing exclusively in the reflection wrapper. Rationale: (1) the streak gate is a reflection-cadence concept that doesn't make sense for one-off CLI invocations; (2) keeping the script side-effect-free preserves its value as a fast pre-commit check; (3) operators who want to one-shot file an issue from a finding can run `python .claude/skills/do-skills-audit/scripts/audit_skills.py --json` and pipe to `gh issue create` manually.
- **Project iteration safety**: if `gh repo view` fails for a project (no remote, detached state), log warning and skip issue filing for that project's findings only. Audit telemetry continues uninterrupted.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks. The `_file_skills_audit_issue_if_streaked` helper logs warnings on each catch arm (mirrors `docs_auditor._file_issue_if_new`).
- [ ] Test asserts that a `gh` CLI failure (subprocess returncode != 0) does NOT set the dedup key, so the next run will retry.
- [ ] Test asserts that a Redis outage does NOT crash the reflection — issue-filing degrades to "fire every run" but telemetry continues.

### Empty/Invalid Input Handling
- [ ] Test: empty `findings` list → 0 issues filed, 0 streak increments, no Redis writes.
- [ ] Test: finding with empty `skill` or `message` field → skipped (no hash, no issue).

### Error State Rendering
- [ ] When `gh issue create` fails, the per-project return dict includes a count of failed-to-file issues so the aggregate summary can surface "N issues failed to file."

## Test Impact

- [ ] `tests/unit/test_skills_audit.py` — UPDATE: existing tests assert the audit script's deterministic rules; no change to script behavior, so these tests remain green. Add a parallel test module `tests/unit/test_skills_audit_reflection.py` for the new wrapper logic (streak gate, dedup, gh-CLI subprocess mock).
- [ ] `tests/unit/test_reflections_package.py` — UPDATE: if it imports `run_skills_audit` and asserts return shape, extend assertions to include the new `issues_filed` field.
- [ ] No integration test changes needed — issue filing is exercised in unit tests via subprocess mocking; live `gh issue create` runs against the test repo would create real issues.

## Rabbit Holes

- **Building a Reflection-side dict for streak state instead of Redis**: tempting because it avoids a new Redis namespace, but reflection callables are stateless across runs. Persistence requires Redis or disk. Redis matches the existing `docs_auditor` pattern.
- **Filing issues for WARN findings too**: out of scope. WARN is for quality suggestions (trigger phrasing, classification hints) — they're noise at issue-tracker granularity. If WARN ever needs action, a separate plan can layer it on with a higher streak threshold.
- **Auto-closing issues when the finding clears**: tempting but adds significant state machinery (find the issue by hash, determine if still FAIL, close with comment). Out of scope for this plan. Operators close issues manually when fixed.
- **Cross-project issue routing**: each project's findings file in that project's repo (via cwd). Don't try to centralize all skill-audit issues in one repo — defeats the per-project model.

## Risks

### Risk 1: Burst of issues from an audit_skills.py regression
**Impact:** A bug in `audit_skills.py` flips many skills FAIL simultaneously. With a two-run gate, the burst is delayed but still fires on the second run.
**Mitigation:** The two-run gate buys ~1 hour to notice and fix audit_skills.py before issues land. If a regression slips through, dedup limits each unique finding to one issue per 30 days; closing the issues and reverting the audit script ends the burst.

### Risk 2: Title hash collision causing dedup mismatch
**Impact:** Two distinct findings hash to the same key — second finding never files.
**Mitigation:** SHA-256 truncated to 16 hex chars = 64 bits of entropy. With ~536 skills × ~12 rules = ~6,432 max distinct hashes, collision probability is negligible (~10⁻¹⁵). Same approach used by `docs_auditor` without incident.

### Risk 3: gh CLI rate limiting under burst
**Impact:** GitHub rate-limits issue creation; subsequent calls fail.
**Mitigation:** `gh` CLI returns non-zero on rate limit; we don't set the dedup key, so retry on next reflection tick (1 hour later) when the bucket has refilled. Per-run cap is implicit (only NEW findings fire), so steady-state load is near zero.

## Race Conditions

### Race 1: Concurrent reflection runs
**Location:** `reflections/auditing.py::_skills_audit_for_project`
**Trigger:** Two reflection workers tick simultaneously and process the same finding.
**Data prerequisite:** Both workers see streak=1, both INCR to 2, both attempt to file.
**State prerequisite:** Redis INCR is atomic; dedup key write uses `SET NX EX` semantics.
**Mitigation:** Use `SET NX EX` for the dedup key — only the first writer wins. The second `gh issue create` still runs (sees streak=2, dedup key absent at gate time) but the dedup `SET NX` fails so the issue gets filed twice. Mitigation: gate on `EXISTS` of dedup key inside the helper after gh succeeds but before next call. Actually — simpler: acquire a per-finding lock with `SET NX EX 60` on `skills_audit:filing_lock:{hash}` before the `gh` call. Released after success. Concurrent workers see lock, skip filing this run. Identical to `docs_auditor._acquire_lock` pattern (already in module).

## No-Gos (Out of Scope)

- WARN-finding issue filing
- Auto-closing issues when findings clear
- Cross-project issue centralization
- A `--file-issues` flag on `audit_skills.py` itself
- Telegram notification on issue filing (existing reflection telemetry surfaces are sufficient)
- Restructuring `_file_issue_if_new` in `docs_auditor.py` into a shared helper — premature extraction; revisit if a third caller appears.

## Update System

No update system changes required. The reflection runs in-process inside the worker; new behavior ships with the next worker restart on each machine via the standard `/update` flow.

## Agent Integration

No agent integration required. This is a reflection-internal change. The agent does not invoke `run_skills_audit` directly; the reflection scheduler does. No new MCP tools, no new CLI entry points.

## Documentation

- [ ] Update `docs/features/do-skills-audit.md` — add a "Reflection Issue Filing" subsection under "Reflections Integration" describing the streak gate, dedup, and label conventions.
- [ ] Update `CLAUDE.md` GitHub Issue Labels table only if new conventions are introduced (none in this plan — `skills` and `bug` already documented).

## Success Criteria

- [ ] On 2 consecutive reflection runs with the same FAIL finding, exactly 1 GitHub issue is filed in the project's repo.
- [ ] On 30 consecutive reflection runs after the first issue is filed, no duplicate issue is filed.
- [ ] When a transient regression in `audit_skills.py` flips 100 skills to FAIL on a single run, 0 issues are filed; reverting the audit script before the next reflection tick produces 0 issues filed total.
- [ ] When `gh` CLI is broken (e.g., auth expired), the reflection still completes and returns telemetry; no dedup key is poisoned.
- [ ] Tests pass (`/do-test`)
- [ ] `docs/features/do-skills-audit.md` updated

## Team Orchestration

### Team Members

- **Builder (reflection-wrapper)**
  - Name: skills-audit-issue-builder
  - Role: Add `_file_skills_audit_issue_if_streaked` and wire into `_skills_audit_for_project`
  - Agent Type: builder
  - Resume: true

- **Validator (reflection-wrapper)**
  - Name: skills-audit-issue-validator
  - Role: Verify streak gate, dedup, repo targeting, error paths
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: skills-audit-doc-updater
  - Role: Update `docs/features/do-skills-audit.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add issue-filing helper with streak gate
- **Task ID**: build-reflection-wrapper
- **Depends On**: none
- **Validates**: `tests/unit/test_skills_audit_reflection.py` (create)
- **Assigned To**: skills-audit-issue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_file_skills_audit_issue_if_streaked(finding, repo_root, project_slug) -> bool` to `reflections/auditing.py`, modeled on `docs_auditor._file_issue_if_new`.
- Define module-level constants: `_SKILLS_AUDIT_STREAK_PREFIX = "skills_audit:streak"`, `_SKILLS_AUDIT_DEDUP_PREFIX = "skills_audit:issues_filed"`, `_SKILLS_AUDIT_LOCK_PREFIX = "skills_audit:filing_lock"`, TTLs 7d / 30d / 60s respectively.
- Title hash uses `f"{project_slug}/{skill_name}/{rule_id}"` → SHA-256 → first 16 hex.
- Acquire per-finding `SET NX EX 60` lock; on success proceed, on lock-held return False (skip this run).
- INCR streak counter, set 7d TTL on first creation only (`EXPIRE NX` semantics — emulate via pipeline).
- Gate: `streak < 2` → return False. `EXISTS` dedup → return False.
- Subprocess `gh issue create --repo OWNER/NAME --title T --body B --label skills --label bug`.
- On `returncode == 0`, `SET NX EX 30d` on dedup key, return True. Otherwise log warning, return False.

### 2. Wire helper into per-project audit
- **Task ID**: build-wire-callsite
- **Depends On**: build-reflection-wrapper
- **Validates**: `tests/unit/test_skills_audit_reflection.py`
- **Assigned To**: skills-audit-issue-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_skills_audit_for_project` (`reflections/auditing.py:464`), after the FAIL findings loop, iterate findings (severity == FAIL only) and call the helper.
- Resolve target repo via `gh repo view --json nameWithOwner -q .nameWithOwner` with `cwd=repo_root`; cache the result inside the function call. On failure, log warning and skip issue filing for this project.
- Track `issues_filed_count` and include it in the per-project return dict.
- Update `run_per_project_audit` aggregation (or inline aggregation in `run_skills_audit`) to surface the cross-project total in the summary string: `"Skills audit: N skills, X fails, Y warns, Z issues filed"`.

### 3. Tests for streak gate, dedup, and error paths
- **Task ID**: build-tests
- **Depends On**: build-wire-callsite
- **Validates**: `pytest tests/unit/test_skills_audit_reflection.py -v`
- **Assigned To**: skills-audit-issue-builder
- **Agent Type**: builder
- **Parallel**: false
- Mock `subprocess.run` for both `gh repo view` and `gh issue create`. Mock Redis via `popoto.redis_db.POPOTO_REDIS_DB` patching (use the same fakeredis fixture pattern other reflections tests use).
- Test cases:
  - First run with FAIL: streak=1, no issue, no dedup key.
  - Second run with same FAIL: streak=2, issue filed once, dedup key set with 30d TTL.
  - Third run with same FAIL: streak=3, no issue (dedup key blocks).
  - Run with FAIL, run without it, run with FAIL again (streak 1 → unchanged → 2): issue files on third appearance (correct flapping handling).
  - `gh` failure: dedup key NOT set, issue retries on next run.
  - Redis unavailable: helper returns False, telemetry path unaffected, no crash.
  - 100 simultaneous FAIL findings on a single run: 0 issues filed (all streak=1).
  - Filing lock contention: second concurrent caller skips, no double-fire.

### 4. Validate behavior end-to-end
- **Task ID**: validate-reflection-wrapper
- **Depends On**: build-tests
- **Assigned To**: skills-audit-issue-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Success Criteria assertions pass.
- Verify no new `except Exception: pass` patterns introduced.
- Verify the `audit_skills.py` script itself is untouched.
- Spot-check: dry-run the reflection in a sandbox repo and confirm a deliberately-broken skill produces an issue on the second tick.

### 5. Update feature doc
- **Task ID**: document-feature
- **Depends On**: validate-reflection-wrapper
- **Assigned To**: skills-audit-doc-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Add "Reflection Issue Filing" subsection to `docs/features/do-skills-audit.md` covering: gate semantics, label conventions, dedup window, flapping behavior, manual operator escape hatch.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skills-audit-issue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full validation table.
- Confirm tracking issue gets `Closes #N` linkage in the implementation PR.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reflection unit tests pass | `pytest tests/unit/test_skills_audit_reflection.py -v` | exit code 0 |
| Existing skills-audit tests pass | `pytest tests/unit/test_skills_audit.py tests/unit/test_reflections_package.py -v` | exit code 0 |
| Lint clean | `python -m ruff check reflections/auditing.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/auditing.py` | exit code 0 |
| Helper exists | `grep -q '_file_skills_audit_issue_if_streaked' reflections/auditing.py` | exit code 0 |
| Wired into per-project body | `grep -q '_file_skills_audit_issue_if_streaked' reflections/auditing.py && grep -A 50 '_skills_audit_for_project' reflections/auditing.py \| grep -q '_file_skills_audit_issue_if_streaked'` | exit code 0 |
| Audit script untouched | `git diff main -- .claude/skills/do-skills-audit/scripts/audit_skills.py` | empty output |
| Docs updated | `grep -q 'Reflection Issue Filing' docs/features/do-skills-audit.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Empty until critique runs. -->

---

## Open Questions

1. **Streak threshold = 2 confirmed?** This plan locks in two consecutive runs as the gate. Higher (3+) reduces false-positive risk further but delays real regressions by another hour each. Two feels right given the ~1-hour reflection cadence and the deterministic nature of FAIL findings — confirm or override.
2. **Issue label `bug` vs `investigation`?** Plan uses `bug` because FAIL findings are deterministic structural violations (broken links, missing frontmatter). If the policy is "all auto-filed issues start as `investigation` until a human triages," override here.
3. **Per-project dedup TTL = 30 days correct?** Matches `docs_auditor`. If skill regressions tend to recur on longer cycles (e.g., quarterly template churn), bump to 90d.
