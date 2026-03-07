---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-03-07
tracking: https://github.com/tomcounsell/ai/issues/289
target_repo: yudame/psyoptimal
---

# Fix Tailwind 4 Visual Regressions

## Problem

After deploying the Tailwind 4 upgrade to staging (stage.psyoptimal.com), visual differences exist between the production environment (app.psyoptimal.com, still on Tailwind 3) and the staging environment (Tailwind 4). These differences indicate potential regressions or migration issues that need to be identified and fixed before deploying Tailwind 4 to production.

**Current behavior:**
- Staging environment shows visual inconsistencies compared to production
- Unknown scope of differences (could be spacing, colors, typography, layout, etc.)
- No systematic comparison has been performed
- Risk of shipping visual regressions to production users

**Desired outcome:**
- Complete visual parity between staging (Tailwind 4) and production (Tailwind 3)
- All Tailwind 4 migration issues identified and documented
- Confidence that Tailwind 4 can be safely deployed to production
- Clean, consistent UI across both environments

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (1 check-in for scope confirmation)

**Interactions:**
- PM check-ins: 1 (confirm visual differences are acceptable or need fixing)
- Review rounds: 1 (code review for fixes)

This is primarily detective work followed by targeted fixes. The bottleneck is the systematic comparison process and validating that fixes don't introduce new issues.

## Prerequisites

**Cross-Repository Work:** This issue was created in `tomcounsell/ai` but the implementation happens in `yudame/psyoptimal`. The developer must have access to both repositories.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PsyOPTIMAL repo access | `cd /Users/valorengels/src/psyoptimal && git status` | Local checkout of psyoptimal repo |
| Production access | `curl -I https://app.psyoptimal.com` | Verify production is accessible |
| Staging access | `curl -I https://stage.psyoptimal.com` | Verify staging is accessible |
| Screenshot tool | `which screencapture` | For visual comparison (macOS) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/fix_tailwind_4_visual_regressions.md`

## Solution

### Key Elements

- **Visual Comparison Tool**: Systematic side-by-side comparison of production vs staging pages
- **Difference Catalog**: Document all identified visual discrepancies with screenshots and descriptions
- **Targeted Fixes**: Apply Tailwind 4 class updates or CSS adjustments to match production appearance
- **Regression Testing**: Verify fixes don't introduce new visual issues

### Flow

**Production baseline** → Screenshot key pages → **Staging comparison** → Screenshot same pages → **Diff analysis** → Identify discrepancies → **Fix implementation** → Apply Tailwind 4 corrections → **Validation** → Re-screenshot and compare → **Sign-off**

Example pages to compare:
- Dashboard/home page
- Settings page
- Main feature pages (therapy sessions, mood tracking, etc.)
- Forms and input components
- Navigation and headers
- Modals and overlays

### Technical Approach

**Phase 1: Discovery**
- Use browser DevTools or Percy/Chromatic-style visual regression tools
- Screenshot key pages on both environments at same viewport sizes
- Create a visual diff report documenting all differences
- Categorize differences by severity (critical layout breaks vs minor spacing)

**Phase 2: Fixes**
- Most fixes will be Tailwind class updates (v3 → v4 syntax changes)
- Some may require CSS overrides if Tailwind 4 changed default behaviors
- Check `tailwind.config.js` for migration-related config issues
- Update components to use Tailwind 4 class names where syntax changed

**Phase 3: Validation**
- Re-run visual comparison after each batch of fixes
- Ensure no new regressions introduced
- Document any intentional visual improvements from Tailwind 4

## Rabbit Holes

- **Pixel-perfect matching** - Don't spend time on sub-pixel differences or anti-aliasing variations. Focus on user-visible layout and styling issues.
- **Redesigning components** - This is a regression fix, not a redesign. Don't improve the design, just match production.
- **Automated visual regression suite** - Building a full Percy/BackstopJS setup is out of scope. Manual comparison is sufficient for this one-time migration.
- **Browser compatibility testing** - Focus on Chrome/Safari (primary user browsers). Don't test IE11 or obscure browsers.

## Risks

### Risk 1: Unknown scope of differences
**Impact:** Could discover hundreds of small differences, exceeding Medium appetite
**Mitigation:** Start with high-traffic pages. If scope balloons, escalate to PM for priority call. Some minor differences may be acceptable.

### Risk 2: Tailwind 4 breaking changes
**Impact:** Some differences may not have simple fixes (e.g., removed classes, changed defaults)
**Mitigation:** Reference Tailwind v3 → v4 migration guide. If a fix requires significant refactoring, document it as a known issue and decide if it's a blocker.

### Risk 3: Staging environment differences
**Impact:** Some visual differences may be due to staging data/config, not Tailwind
**Mitigation:** Use browser DevTools to inspect computed styles and confirm Tailwind is the cause before fixing.

## No-Gos (Out of Scope)

- **Full redesign** - Only fix regressions, don't improve the design
- **Automated visual regression testing** - Manual comparison is sufficient for this migration
- **Responsive breakpoint testing** - Focus on desktop viewport first (primary user device)
- **Dark mode** - If PsyOPTIMAL has dark mode, it's out of scope unless explicitly broken
- **Component library updates** - Don't refactor components unless necessary for the fix

## Update System

No update system changes required — this work is specific to the PsyOPTIMAL application repository and doesn't affect the Valor AI system deployment process.

## Agent Integration

No agent integration required — this is a cross-repository issue tracking plan. The actual implementation happens in the `yudame/psyoptimal` repository, not the `tomcounsell/ai` repository where this plan lives.

**Cross-Repository Note:** When `/do-build` is invoked, it should switch working directory to `/Users/valorengels/src/psyoptimal` and create the feature branch there. The plan document remains in the AI repo for tracking purposes.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/tailwind-4-migration.md` in the **psyoptimal repo** describing the migration and any known changes
- [ ] Update psyoptimal repo README if Tailwind version is mentioned

### Internal Documentation
No internal documentation changes needed in the AI repo — this is a cross-repository tracking issue.

If genuinely no docs are needed in the psyoptimal repo (unlikely), document that decision in the PR description.

## Success Criteria

- [ ] Systematic comparison completed for all critical pages (dashboard, settings, main features)
- [ ] All critical visual differences documented with screenshots
- [ ] Fixes applied for all user-facing visual regressions
- [ ] Re-validation shows staging matches production appearance
- [ ] PR opened in `yudame/psyoptimal` with fixes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated in psyoptimal repo (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Visual Comparison Engineer**
  - Name: comparison-engineer
  - Role: Systematically compare production vs staging, document differences
  - Agent Type: test-engineer
  - Resume: true

- **Tailwind Migration Fixer**
  - Name: tailwind-fixer
  - Role: Apply Tailwind 4 class updates and CSS fixes
  - Agent Type: builder
  - Resume: true

- **Visual Validator**
  - Name: visual-validator
  - Role: Verify fixes match production appearance
  - Agent Type: validator
  - Resume: true

- **Documentation Writer**
  - Name: docs-writer
  - Role: Document migration changes and known issues
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Visual Comparison Analysis
- **Task ID**: compare-envs
- **Depends On**: none
- **Assigned To**: comparison-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Visit production (app.psyoptimal.com) and screenshot key pages
- Visit staging (stage.psyoptimal.com) and screenshot same pages at same viewport
- Create visual diff report with side-by-side comparisons
- Categorize differences by severity (critical vs minor)
- Document findings in a markdown file

### 2. Apply Tailwind 4 Fixes
- **Task ID**: apply-fixes
- **Depends On**: compare-envs
- **Assigned To**: tailwind-fixer
- **Agent Type**: builder
- **Parallel**: false
- Switch working directory to `/Users/valorengels/src/psyoptimal`
- Create feature branch for Tailwind 4 fixes
- Apply class updates for identified visual differences
- Update `tailwind.config.js` if needed
- Commit fixes with clear descriptions

### 3. Validate Fixes
- **Task ID**: validate-fixes
- **Depends On**: apply-fixes
- **Assigned To**: visual-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-screenshot staging pages after fixes
- Compare with production baseline
- Verify all critical differences resolved
- Document any remaining minor differences
- Report pass/fail status

### 4. Documentation
- **Task ID**: document-migration
- **Depends On**: validate-fixes
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/tailwind-4-migration.md` in psyoptimal repo
- Document known changes from Tailwind 3 to 4
- List any acceptable visual differences
- Update README if needed

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-migration
- **Assigned To**: visual-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report with before/after screenshots

## Validation Commands

- `cd /Users/valorengels/src/psyoptimal && git status` - Verify working in correct repo
- `cd /Users/valorengels/src/psyoptimal && git branch --show-current` - Verify on feature branch
- `curl -I https://stage.psyoptimal.com` - Verify staging is accessible
- Visual inspection of staging vs production screenshots - Manual validation

---

## Open Questions

1. **Scope confirmation**: How many pages should be systematically compared? Should we focus on just the top 5 user-facing pages or do a comprehensive audit of all routes?

2. **Acceptable differences**: Are there any visual improvements in Tailwind 4 that we want to keep even if they differ from production (e.g., improved default spacing, better color contrast)?

3. **Deployment timeline**: Is there a target date for deploying Tailwind 4 to production? This affects whether we can take the full Medium appetite or need to rush.

4. **Testing environment**: Should comparison be done manually with screenshots, or should we invest time in setting up a visual regression testing tool (Percy, BackstopJS)?

5. **Cross-repository workflow**: Should this issue be moved to the psyoptimal repo, or is it fine to track it here with the plan noting the implementation happens elsewhere?
