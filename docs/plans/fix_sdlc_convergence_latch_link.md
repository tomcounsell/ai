---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2048
last_comment_id:
---

# Fix broken convergence-latch sub-file link in the `sdlc` skill

## Problem

The `skills-audit` reflection flags a rule 9 FAIL ("Broken sub-file links") for
the project-only skill `sdlc` on two consecutive runs. The offending link is in
`.claude/skills/sdlc/SKILL.md:184`:

```
[SDLC Pipeline — Convergence Latch](../../docs/features/sdlc-pipeline.md#convergence-latch-revision_applied_at-issue-1760)
```

**Current behavior:**
Rule 9 resolves each relative link against the skill directory. `.claude/skills/sdlc/`
is three levels below the repo root, so a link to `docs/` needs `../../../`. This
link uses only `../../`, which resolves to the non-existent
`.claude/docs/features/sdlc-pipeline.md`. The audit records a FAIL every run, and
because the finding persisted for two consecutive runs it auto-filed issue #2048.

**Desired outcome:**
The link points at the real file. Rule 9 passes for `sdlc`, and the audit streak
counter resets naturally.

## Freshness Check

**Baseline commit:** 4e297c6d
**Issue filed at:** 2026-07-13T04:47:03Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills/sdlc/SKILL.md:184` — broken link with `../../` prefix — still present, verified by grep.
- `docs/features/sdlc-pipeline.md:119` — heading `## Convergence Latch (revision_applied_at, issue #1760)` — present; GitHub-style slug is exactly `convergence-latch-revision_applied_at-issue-1760`, so the anchor half of the link is already correct.
- `.claude/skills/sdlc/SKILL.md:23` — sibling link `../../../docs/features/sdlc-tool-resolver.md` — uses the correct three-level depth and passes, confirming `../../../` is the right prefix.

**Cited sibling issues/PRs re-checked:** None cited beyond the audit finding itself.

**Commits on main since issue was filed (touching referenced files):** None (`git log --since` over both files is empty).

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** Root cause is purely the relative path depth. The anchor and target file are both correct.

## Prior Art

No prior issues or PRs found related to this specific broken link. This is a
mechanical audit finding, not a recurring design problem.

## Root Cause

Rule 9 (`rule_09_sub_file_links` in
`.claude/skills-global/do-skills-audit/scripts/audit_skills.py:339`) computes
`target = skill_dir / path_part` and checks `target.exists()`. For the `sdlc`
skill, `skill_dir = .claude/skills/sdlc`. The link's `../../` climbs only to
`.claude/`, so the resolved path `.claude/docs/features/sdlc-pipeline.md` does
not exist. Three `../` are required to reach the repo root.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this is a one-token edit to a Markdown file with no external dependencies.

## Solution

### Key Elements

- **`.claude/skills/sdlc/SKILL.md:184`**: change the link prefix from `../../` to `../../../` so it resolves to the real `docs/features/sdlc-pipeline.md`. The anchor stays untouched.

### Technical Approach

- Single-line edit. Replace `](../../docs/features/sdlc-pipeline.md#` with `](../../../docs/features/sdlc-pipeline.md#` on line 184.
- Re-run rule 9's resolver logic over every relative link in the file to confirm no other links are broken (already verified during recon: line 23 passes, line 184 is the only offender).

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is a documentation-link edit, no code changes.

### Empty/Invalid Input Handling
- Not applicable — no functions are added or modified.

### Error State Rendering
- Not applicable — no user-visible runtime output. The only observable behavior is the audit's rule 9 verdict flipping from FAIL to PASS.

## Test Impact

No existing tests affected — this is a one-line Markdown link correction in a skill body; no Python behavior, test fixtures, or interfaces change. The audit script itself is unchanged.

## Rabbit Holes

- Do NOT "fix" the anchor — it already matches the heading slug; only the path depth is wrong.
- Do NOT audit or repair links in other skills. This issue is scoped to `sdlc` rule 9 only; a broader sweep is separate work.
- Do NOT modify the audit script's resolver — the resolver is correct; the link is wrong.

## Risks

### Risk 1: Editing the wrong `../` count
**Impact:** Link stays broken; rule 9 still fails.
**Mitigation:** Verification step replays rule 9's exact resolver (`skill_dir / path_part`).exists() and asserts the link resolves. The sibling line-23 link is the proven reference for the correct depth.

## Race Conditions

No race conditions identified — the change is a synchronous, single-file text edit with no concurrent access.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this is a repo-local documentation edit. The
`sdlc` skill is project-only (`.claude/skills/`) and is never synced to other
machines, so no `/update` wiring is touched.

## Agent Integration

No agent integration required — this edits a skill Markdown body. No CLI entry
point, MCP surface, or bridge import is involved.

## Documentation

No documentation changes needed — the edit *is* to a documentation-class file
(the skill body). There is no `docs/features/` doc to create for a one-line link
correction, and the referenced `docs/features/sdlc-pipeline.md` already documents
the convergence latch mechanism.

## Success Criteria

- [ ] `.claude/skills/sdlc/SKILL.md:184` link uses `../../../docs/features/sdlc-pipeline.md#convergence-latch-revision_applied_at-issue-1760`.
- [ ] Rule 9 resolver confirms the link target exists (replay `skill_dir / path_part`).
- [ ] `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py` (or its rule 9 path) reports PASS for `sdlc`.
- [ ] No other relative links in `SKILL.md` regress.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Corrected link present | `grep -c '](../../../docs/features/sdlc-pipeline.md#convergence-latch-revision_applied_at-issue-1760)' .claude/skills/sdlc/SKILL.md` | output contains 1 |
| Old broken prefix gone | `grep -c '](../../docs/features/sdlc-pipeline.md#' .claude/skills/sdlc/SKILL.md` | match count == 0 |
| Link target resolves | `python3 -c "from pathlib import Path; import sys; sys.exit(0 if (Path('.claude/skills/sdlc')/'../../../docs/features/sdlc-pipeline.md').exists() else 1)"` | exit code 0 |

## Open Questions

None — root cause, fix, and verification are fully determined. This is a mechanical one-line correction.
