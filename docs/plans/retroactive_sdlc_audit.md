---
status: docs_complete
type: chore
appetite: Large
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/444
last_comment_id:
---

# Retroactive SDLC Audit: Catalog Gaps and Ship Targeted Fixes

## Problem

The SDLC pipeline was built incrementally. Many PRs merged before full enforcement existed — missing reviews, skipped critiques, deleted plans without deliverable verification. Issue #823 identified 18 specific PRs that merged with incomplete SDLC. Beyond those, plan files get deleted from `docs/plans/` when marked "completed" without verifying that all plan deliverables (docs, tests, config updates) were actually shipped.

**Current behavior:**
270 plan files have been deleted from `docs/plans/` over the last 3 months. Of the 18 #823 issues, only 6 still have plan files — the other 12 were deleted. Nobody knows the full scope of missing test coverage, missing feature docs, stale references, and orphaned code.

**Desired outcome:**
A complete, deduplicated inventory of SDLC gaps across recent merged work, with targeted fix PRs that close each gap. After completion, every merged feature from the audit set has its docs, tests, and references in order.

## Prior Art

- **#823**: Enforce structured review comment check in `/do-merge` — identified 18 PRs that merged without proper review; now closed, merge gate enforced going forward
- **#443 / PR**: Build validation script — created `scripts/validate_build.py` with deterministic plan-vs-codebase checking; closed, script exists and works
- **#770**: The critical incident — merged with 2x "Changes Requested" reviews and 0 approvals, triggered #823
- **#708**: Retroactive SDLC verification for session zombie fix (#700/#703) — closed, one-off verification
- **#706 / #717**: Follow-up SDLC quality gate verification — closed, addressed specific PRs

## Data Flow

1. **Entry point**: Git history — `git log --diff-filter=D -- 'docs/plans/*.md'` recovers deletion commits
2. **Plan reconstruction**: `git show {commit}^:docs/plans/{slug}.md` recovers plan content before deletion
3. **Audit set assembly**: Combine #823 list (18 PRs) + recently deleted plans (post-SDLC enforcement) into `data/retroactive-audit-set.json`
4. **Per-item audit**: Each audit item gets a subagent that checks plan deliverables against HEAD — docs, tests, file assertions, verification table entries
5. **Synthesis**: Orchestrator collects all subagent findings, deduplicates, and triages into prioritized report at `docs/audits/retroactive-sdlc-audit.md`
6. **Fix shipping**: Targeted fix PRs per finding category, each going through normal SDLC

## Appetite

**Size:** Large

**Team:** Solo dev with orchestrator-controlled subagents

**Interactions:**
- PM check-ins: 2 (audit set review, triage review before fix PRs)
- Review rounds: 1 per fix PR

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `validate_build.py` exists | `test -f scripts/validate_build.py && echo OK` | Core validation functions for plan deliverable checking |
| `gh` CLI authenticated | `gh auth status` | GitHub API access for PR/issue lookups |
| Git history available | `git log --oneline -1` | Plan recovery from deleted files |

Run all checks: `python scripts/check_prerequisites.py docs/plans/retroactive_sdlc_audit.md`

## Solution

### Key Elements

- **Audit Set Builder**: Assembles the list of issues/plans to audit from two sources — the #823 list and recently deleted plan files recoverable from git history
- **Per-Item Auditor**: Subagent that receives plan content + merged PR diff and checks every deliverable against HEAD, returning structured JSON findings
- **Synthesis Engine**: Deduplication, relevance filtering, and severity triage of all findings into a prioritized report
- **Fix Dispatcher**: Creates focused fix PRs grouped by category (missing docs, missing tests, stale refs)

### Flow

**Git history** → Recover deleted plans → Build audit set JSON → Spawn auditor subagents → Collect JSON findings → Deduplicate & triage → **Prioritized report** → Spawn fix builders per category → **Targeted fix PRs**

### Technical Approach

- **Phase 1 (audit set)**: Single agent walks git history, recovers plan content via `git show`, cross-references with `gh` CLI to find merged PRs. For each audit item, fetches the PR diff via `gh pr diff {number}` and saves it to a temp file `data/retroactive-audit-diffs/{slug}.diff` (or embeds inline in the JSON if under 50KB). If `merged_pr_number` is null, skip diff fetch and proceed with plan content only. Plan content is truncated to 10,000 chars if needed to keep the JSON manageable. Output: `data/retroactive-audit-set.json`
- **Phase 2 (per-item audit)**: 1 read-only subagent per audit item using P-Thread pattern. Each subagent receives plan content and the PR diff (if available) and checks against HEAD using `validate_build.py` functions (`parse_file_assertions`, `parse_verification_table`, `parse_success_criteria_commands`) plus manual doc/test checks. Output: `data/retroactive-audit-findings/{slug}.json`
- **Phase 3 (synthesis)**: Single orchestrator pass — collect findings, purge `still_relevant: false`, deduplicate overlapping gaps, merge related findings, severity triage. Output: `docs/audits/retroactive-sdlc-audit.md`
- **Phase 4 (fixes)**: 1 builder agent per fix category (missing docs, test gaps, stale refs, etc.), each creating a focused PR through full SDLC (Plan → Build → Test → Review → Merge) — no hotfix shortcuts

Key decisions:
- Reuse `validate_build.py` functions — no reimplementation
- File-existence checks only against HEAD — do not execute verification table commands from old plans (too risky)
- Plans predating structured format (no frontmatter) are counted but skipped for validation
- Subagents are read-only in Phase 2 — catalog only, never modify code
- Intermediate results saved to `data/` for resumability

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `validate_build.py` functions handle malformed plan content gracefully (empty strings, missing sections) — verify existing tests cover this
- [ ] Git operations handle missing commits or force-pushed history — add try/except with logging around `git show` calls
- [ ] If no exception handlers exist in scope, state "No exception handlers in scope"

### Empty/Invalid Input Handling
- [ ] Plans with no frontmatter (pre-structured format) return empty findings, not crashes
- [ ] Plans with no checkboxes, no verification table, no success criteria return `"findings": []`
- [ ] Audit set with zero items produces an empty report, not an error

### Error State Rendering
- [ ] Triage report clearly marks items that could not be audited (plan unrecoverable, PR not found) vs items with no findings
- [ ] Each finding includes `evidence` field pointing to the specific plan line that was undelivered

## Test Impact

No existing tests affected — this is a greenfield chore with no prior test coverage. The work produces data artifacts (JSON, markdown) and fix PRs, not new library code. `validate_build.py` is used as-is; no modifications to its interface.

## Rabbit Holes

- **Auditing all 270 deleted plans**: Most old plans predate structured SDLC and their requirements are stale after months of refactoring. Scope to the 18 #823 issues + post-SDLC deleted plans (~30-50 total).
- **Deep AI judge review**: The original #444 proposed AI-judged review of every finding. Replace with simpler subagent checks against HEAD — if the file exists and tests pass, the deliverable is satisfied.
- **Fixing everything found**: Some findings will be `still_relevant: false` because features were rewritten or deprecated. Only fix what still matters at HEAD.
- **Running verification table commands**: Old plan verification commands may reference removed files, changed APIs, or dangerous operations. Stick to file-existence and grep checks only.

## Risks

### Risk 1: Audit set too large to process in parallel
**Impact:** Subagent spawning hits rate limits or context limits
**Mitigation:** Batch subagents in groups of 10. Save intermediate results so work is resumable if interrupted.

### Risk 2: Many findings are stale/irrelevant
**Impact:** Triage report is noisy, fix PRs waste effort on dead code
**Mitigation:** Each subagent checks `still_relevant` against HEAD. Synthesis pass purges irrelevant findings before triage.

### Risk 3: Git history doesn't have recoverable plan content
**Impact:** Some deleted plans can't be reconstructed
**Mitigation:** Mark as "unrecoverable" in audit set. For #823 items, the issue body often contains enough context to audit without the plan file.

## Race Conditions

No race conditions identified — this is a batch analysis workflow with no concurrent writes. Each subagent is read-only and writes to its own output file. The orchestrator collects results sequentially.

## No-Gos (Out of Scope)

- Plans predating structured SDLC format (no frontmatter, no checklists) — can't be meaningfully audited
- Automatic issue creation from findings — Tom reviews and approves/dismisses each finding
- Modifying `validate_build.py` — use it as-is
- Retroactive review of PRs that already merged — focus on deliverable gaps, not review quality

## Update System

No update system changes required — this is a one-time audit chore that produces data artifacts and fix PRs. No new dependencies, no runtime changes, no config propagation needed.

## Agent Integration

No agent integration required — this is an orchestrator-controlled workflow using existing tools (git, gh CLI, validate_build.py). No new MCP servers, no bridge changes, no new tools to expose. The subagents use standard read-only capabilities already available.

## Documentation

- [ ] Create `docs/audits/retroactive-sdlc-audit.md` — the triage report itself (Phase 3 output)
- [ ] No `docs/features/` entry needed — this is a one-time chore, not a feature

## Success Criteria

- [ ] Audit set built: all 18 #823 issues + post-SDLC deleted plans identified with recovered plan content, saved to `data/retroactive-audit-set.json`
- [ ] Every item in the audit set has structured JSON findings from a dedicated subagent, saved to `data/retroactive-audit-findings/`
- [ ] Findings deduplicated and triaged into a prioritized report at `docs/audits/retroactive-sdlc-audit.md`
- [ ] Each category of findings (missing docs, missing tests, stale refs, etc.) has a fix PR that passes SDLC
- [ ] Zero findings with `still_relevant: true` and `severity: high` remain unaddressed after Phase 4
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Audit Set Builder**
  - Name: audit-set-builder
  - Role: Recover deleted plans from git history, assemble audit set JSON
  - Agent Type: builder
  - Resume: true

- **Per-Item Auditor** (spawned N times, one per audit item)
  - Name: item-auditor-{slug}
  - Role: Check plan deliverables against HEAD, produce structured findings JSON
  - Agent Type: validator
  - Resume: true

- **Synthesis Processor**
  - Name: synthesis-processor
  - Role: Deduplicate, filter, and triage all findings into prioritized report
  - Agent Type: builder
  - Resume: true

- **Fix Builder (docs)**
  - Name: fix-builder-docs
  - Role: Create missing feature documentation
  - Agent Type: documentarian
  - Resume: true

- **Fix Builder (tests)**
  - Name: fix-builder-tests
  - Role: Add missing test coverage
  - Agent Type: test-engineer
  - Resume: true

- **Fix Builder (cleanup)**
  - Name: fix-builder-cleanup
  - Role: Remove stale references and orphaned code
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Build Audit Set (Phase 1)
- **Task ID**: build-audit-set
- **Depends On**: none
- **Assigned To**: audit-set-builder
- **Agent Type**: builder
- **Parallel**: false
- Walk git history anchored to issue #443 close date (2026-03-24): `git log --diff-filter=D --after="2026-03-24" --name-only -- 'docs/plans/*.md'` to find deleted plans in the post-SDLC-enforcement window
- For each deleted plan, recover content: `git show {commit}^:docs/plans/{slug}.md`
- Parse frontmatter to get `tracking:` issue URL and `status:` field
- Cross-reference with `gh` CLI to find the merged PR for each tracking issue
- For each audit item with a known `merged_pr_number`, fetch the PR diff: `gh pr diff {number} > data/retroactive-audit-diffs/{slug}.diff` (skip if PR number unknown)
- Include all 18 #823 issues explicitly: #819, #815, #813, #812, #807, #803, #802, #801, #796, #794, #793, #790, #789, #787, #781, #784, #764, #749
- Filter to post-SDLC plans (those with structured frontmatter and checklists)
- Truncate `plan_content` to 10,000 chars if needed before embedding in JSON
- Save output to `data/retroactive-audit-set.json` with schema: `{issue_number, plan_slug, plan_content, merged_pr_number, pr_diff_path, source}`

### 2. Per-Item Audit (Phase 2)
- **Task ID**: audit-items
- **Depends On**: build-audit-set
- **Assigned To**: item-auditor-{slug} (one per audit item)
- **Agent Type**: validator
- **Parallel**: true (batches of 10)
- Receive plan content and merged PR diff (loaded from `pr_diff_path` if present in audit set; proceed with plan content only if absent)
- Use `validate_build.py` functions to check file assertions against HEAD
- Check `## Documentation` section tasks — verify referenced docs exist
- Check `## Test Impact` section — verify referenced test files exist
- Check `## Success Criteria` — verify file-based criteria are met
- Mark each finding with `still_relevant: true/false` based on current HEAD state; fallback rule: if uncertain, default to `still_relevant: true, confidence: "low"` — never mark `still_relevant: false` without a concrete HEAD check (file deleted or feature removed from codebase entrypoints)
- Save per-item findings to `data/retroactive-audit-findings/{slug}.json`

### 3. Synthesize and Triage (Phase 3)
- **Task ID**: synthesize-findings
- **Depends On**: audit-items
- **Assigned To**: synthesis-processor
- **Agent Type**: builder
- **Parallel**: false
- Collect all JSON findings from `data/retroactive-audit-findings/`
- Purge findings where `still_relevant: false`
- Deduplicate — group findings that reference the same missing file or test
- Merge related findings (e.g., missing doc + missing test for same feature)
- Severity triage: High (live features missing docs/tests), Medium (stale refs in use), Low (deprecated features)
- Create `docs/audits/` directory
- Save prioritized report to `docs/audits/retroactive-sdlc-audit.md`

### 4. Ship Fix PRs (Phase 4)
- **Task ID**: ship-fixes-docs
- **Depends On**: synthesize-findings
- **Assigned To**: fix-builder-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Create one PR for all missing feature documentation findings

### 5. Ship Test Fixes
- **Task ID**: ship-fixes-tests
- **Depends On**: synthesize-findings
- **Assigned To**: fix-builder-tests
- **Agent Type**: test-engineer
- **Parallel**: true
- Create one PR for test coverage gap findings

### 6. Ship Cleanup Fixes
- **Task ID**: ship-fixes-cleanup
- **Depends On**: synthesize-findings
- **Assigned To**: fix-builder-cleanup
- **Agent Type**: builder
- **Parallel**: true
- Create one PR for stale reference cleanup findings

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: ship-fixes-docs, ship-fixes-tests, ship-fixes-cleanup
- **Assigned To**: synthesis-processor
- **Agent Type**: validator
- **Parallel**: false
- Verify all fix PRs pass CI
- Re-run file-existence checks for each finding — a finding is "addressed" when its `file_path` exists at HEAD (for missing docs/tests) or the stale reference no longer appears (for stale refs)
- Verify zero `still_relevant: true` + `severity: high` findings remain unaddressed: `grep -rl '"severity": "high"' data/retroactive-audit-findings/ | xargs grep -l '"still_relevant": true'` should return no files
- Generate final report documenting fix PRs merged and outstanding low/medium findings

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Audit set exists | `test -f data/retroactive-audit-set.json && echo OK` | exit code 0 |
| Audit set valid JSON | `python -c "import json; json.load(open('data/retroactive-audit-set.json'))"` | exit code 0 |
| Findings directory exists | `ls data/retroactive-audit-findings/*.json \| wc -l` | output > 0 |
| Triage report exists | `test -f docs/audits/retroactive-sdlc-audit.md && echo OK` | exit code 0 |
| No high-severity gaps remaining | `grep -rl '"severity": "high"' data/retroactive-audit-findings/ | xargs grep -l '"still_relevant": true' 2>/dev/null` | empty output (no matching files) |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Adversary, Skeptic | Verification table "No high-severity gaps remaining" greps JSON strings from a markdown file — always passes vacuously | Fix verify command to target `data/retroactive-audit-findings/*.json` | Use `grep -rl '"severity": "high".*"still_relevant": true' data/retroactive-audit-findings/` expecting empty output |
| CONCERN | Skeptic, Operator | Task 2 says subagents "receive merged PR diff" but no fetch mechanism defined in Task 1 output schema | Clarify in Task 2 that PR diff is optional context; use `gh pr view {number} --json body` instead of full diff | If `merged_pr_number` is null, skip diff fetch. Guard: default to plan content only. |
| CONCERN | Skeptic, Simplifier | Source B ("recently deleted plans") has no git anchor — date/commit unspecified; actual count is 270, not 259 | Pin to #443 merge date: `gh pr view 443 --json mergedAt` then use `--after` in git log filter | `git log --diff-filter=D --after="{#443_mergedAt}" --name-only -- 'docs/plans/*.md'` then grep for frontmatter `^---` |
| CONCERN | Adversary, Operator | `still_relevant` judgment by subagents is substantive, not mechanical — incorrect `false` silently discards real gaps | Add fallback rule: if uncertain, default to `still_relevant: true, confidence: "low"` | Never mark `still_relevant: false` without a concrete HEAD check (file deleted or feature removed from entrypoints) |
| CONCERN | Operator, User | Open Question #2 (fix PR SDLC process) left unresolved — affects Phase 4 effort estimate significantly | **RESOLVED:** All fix PRs go through full SDLC (Plan → Build → Test → Review → Merge) — standard for this repo | No abbreviated paths; full pipeline for every fix category |
| NIT | Skeptic | Plan says "259 deleted plans" but git shows 270 | Update or remove specific count | n/a |
| NIT | Simplifier | `plan_content` stored inline in JSON array — 30-50 plans × ~5KB = 150-250KB blob | Consider `data/retroactive-audit-plans/{slug}.md` files with lean JSON index | n/a |
| NIT | Operator | No mechanism to mark findings "addressed" after fix PRs merge — Task 7 "verify zero high" is undefined | Task 7 should re-run file-existence checks per finding or define "addressed" = fix PR merged + file exists at HEAD | n/a |

---

## Open Questions

1. Should we scope Source B (recently deleted plans) to the last 3 months, or extend further back? There are 270 deleted plans in 3 months — auditing all is possible but may produce mostly stale findings. The git date anchor (2026-03-24, issue #443 close date) provides the cutoff; plans deleted before that date are out of scope.
2. ~~For Phase 4 fix PRs, should each fix PR go through the full SDLC pipeline?~~ **RESOLVED:** Each fix PR goes through the full SDLC pipeline (Plan → Build → Test → Review → Merge) — this is the standard for this repo and ensures the fixes themselves don't accumulate new SDLC debt.
3. Should findings with `severity: low` be tracked as future issues or dismissed permanently?
