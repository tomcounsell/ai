---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-03-07
tracking: https://github.com/tomcounsell/ai/issues/291
---

# Fix Tailwind 4 Visual Regressions

## Problem

After the Tailwind 4 upgrade was deployed to staging (stage.psyoptimal.com), visual differences remain between staging and production (app.psyoptimal.com). These differences indicate that the Tailwind 4 upgrade introduced styling regressions that need to be fixed before deploying to production.

**Current behavior:**
- Production (app.psyoptimal.com) is running the old Tailwind version with correct styling
- Staging (stage.psyoptimal.com) is running Tailwind 4 with visual differences
- No systematic audit has been performed to catalog all the differences
- Risk of deploying broken UI to production if regressions aren't fixed

**Desired outcome:**
- Complete visual parity between staging and production
- All Tailwind 4 regressions identified and fixed
- Staging visually matches production (meaning the Tailwind 4 upgrade is truly drop-in compatible)
- Confidence to deploy the Tailwind 4 upgrade to production

## Appetite

**Size:** Medium

**Team:** Solo dev + AI agent with browser capabilities

**Interactions:**
- PM check-ins: 0 (clear scope - fix visual regressions)
- Review rounds: 1 (code review before deploying to production)

This is primarily execution work (find differences, fix them), not alignment work. The bottleneck is thoroughness (ensuring we catch all visual differences) rather than communication overhead.

## Prerequisites

**CRITICAL: This is a cross-repo issue.** The tracking issue lives in the `ai` repo, but the implementation work must happen in the `psyoptimal` repo (where the Tailwind code lives).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Access to `psyoptimal` repository | `gh repo view psyoptimal --json name` | Where the Tailwind code lives |
| Agent-browser capability | `which agent-browser` or equivalent tool availability check | For systematic visual comparison |
| Staging environment access | `curl -I https://stage.psyoptimal.com` returns 200 | Need to view staging |
| Production environment access | `curl -I https://app.psyoptimal.com` returns 200 | Need baseline for comparison |

Run all checks: `python scripts/check_prerequisites.py docs/plans/fix-tailwind-4-visual-regressions.md`

## Solution

### Key Elements

- **Visual Audit Tool**: Use agent-browser to systematically compare production vs staging across all key pages/flows
- **Regression Catalog**: Document each visual difference with screenshots and element selectors
- **Targeted Fixes**: Fix Tailwind 4 migration issues one-by-one, verifying each fix visually
- **Verification Loop**: Re-run visual comparison after fixes to ensure no new regressions introduced

### Flow

**Audit Phase** → Navigate key pages on both environments → **Catalog Phase** → Document each difference → **Fix Phase** → Apply Tailwind 4 fixes → **Verification Phase** → Re-audit to confirm parity

### Technical Approach

**Phase 1: Systematic Visual Audit**
- Define list of critical pages/flows to audit (homepage, dashboard, key user flows)
- Use agent-browser to visit each page on both production and staging
- Take screenshots and identify visual differences (spacing, colors, typography, layout)
- Document each difference with:
  - Page/component affected
  - Element selector (CSS class/ID)
  - Production appearance (screenshot/description)
  - Staging appearance (screenshot/description)
  - Suspected root cause (Tailwind class change, config issue, etc.)

**Phase 2: Root Cause Analysis**
- For each identified difference, investigate:
  - What Tailwind classes are applied?
  - Did Tailwind 4 change the behavior of these classes?
  - Is it a config issue (tailwind.config.js)?
  - Is it a breaking change in Tailwind 4 that requires code updates?

**Phase 3: Fix Implementation**
- Work through regression catalog systematically
- For each issue:
  - Apply fix (update Tailwind classes, adjust config, etc.)
  - Test locally if possible, or deploy to staging
  - Verify fix using agent-browser
  - Mark as resolved in the catalog

**Phase 4: Final Verification**
- Re-run complete visual audit on staging
- Confirm zero visual differences between staging and production
- Sign off that Tailwind 4 upgrade is production-ready

## Rabbit Holes

**Avoid refactoring unrelated styles while fixing regressions** - Stay laser-focused on achieving visual parity. Don't take this opportunity to "improve" the design or refactor components that aren't broken.

**Avoid over-engineering the audit process** - A simple markdown document with screenshots is sufficient. Don't build elaborate tooling for one-time visual regression testing.

**Avoid fixing production instead of staging** - The goal is to make staging match production by fixing Tailwind 4 issues, not to change production's appearance.

**Avoid getting sidetracked by Tailwind 4 features** - This is a compatibility fix, not a "let's use all the new Tailwind 4 features" project. That's a separate effort.

## Risks

### Risk 1: Incomplete Audit
**Impact:** Missing visual regressions that then ship to production
**Mitigation:**
- Define comprehensive page list upfront (cover all major user flows)
- Use agent-browser systematically rather than ad-hoc manual checks
- Create a checklist and mark each page as audited

### Risk 2: Fixes Break Other Pages
**Impact:** Fixing one regression introduces new regressions elsewhere
**Mitigation:**
- After each fix, re-test the specific page AND adjacent pages that might share styles
- Consider running a full re-audit before final sign-off
- Use granular, scoped fixes rather than global style changes

### Risk 3: Tailwind 4 Breaking Changes Require Extensive Rewrites
**Impact:** Some regressions can't be fixed with simple class tweaks - they require component rewrites
**Mitigation:**
- Identify these early in the audit phase
- If multiple components need rewrites, escalate to supervisor to re-scope as a larger project
- Consider rolling back Tailwind 4 if breaking changes are too extensive

### Risk 4: Cross-Repo Coordination Overhead
**Impact:** Tracking issue in `ai` repo but work in `psyoptimal` repo creates confusion
**Mitigation:**
- Create a mirror tracking issue in the `psyoptimal` repo
- Cross-link both issues
- Update both issues as work progresses
- Close both when work is complete

## No-Gos (Out of Scope)

**Design improvements** - This is a regression fix, not a redesign. Match production's appearance exactly, even if there are design issues you'd like to improve.

**Tailwind 4 feature adoption** - Don't rewrite components to use new Tailwind 4 features. That's a separate project.

**Component refactoring** - If a component's code is messy but visually correct, leave it alone. Refactoring is out of scope.

**Responsive breakpoint fixes** - Only focus on the primary desktop viewport. Mobile/tablet regression fixes are a separate project unless they're quick wins.

**Performance optimization** - Even if Tailwind 4 offers performance benefits, optimizing for performance is out of scope. This is purely a visual parity fix.

## Update System

No update system changes required - this work happens entirely in the `psyoptimal` repository, which has its own deployment process. The `ai` repo's update system is not involved.

## Agent Integration

The agent will use browser-based tools to perform the visual audit, but this doesn't require new MCP integrations. Existing agent capabilities are sufficient:

- Browser automation for navigation and screenshots (agent-browser or equivalent)
- File/code editing tools to apply fixes in the `psyoptimal` repo
- Git/GitHub tools to create branches and PRs in the `psyoptimal` repo

No changes to the `ai` repo's MCP configuration needed - this is a cross-repo issue where the agent performs work in a different repository.

## Documentation

### Feature Documentation
- [ ] Update `docs/plans/fix-tailwind-4-visual-regressions.md` with final regression catalog and resolution notes
- [ ] Create a brief summary document in the `psyoptimal` repo documenting Tailwind 4 migration gotchas for future reference

### Cross-Repo Tracking
- [ ] Create mirror issue in `psyoptimal` repo and cross-link with issue #291
- [ ] Update both issues as work progresses
- [ ] Close both issues when complete

### Inline Documentation
- [ ] Add comments in the `psyoptimal` codebase on any non-obvious Tailwind 4 fixes

No extensive documentation needed - this is a bug fix, not a new feature. The regression catalog serves as the primary documentation artifact.

## Success Criteria

- [ ] Complete visual audit performed (all key pages compared)
- [ ] Regression catalog created with screenshots and element selectors
- [ ] All identified regressions fixed
- [ ] Staging environment (stage.psyoptimal.com) visually matches production (app.psyoptimal.com)
- [ ] Code changes committed to `psyoptimal` repo and deployed to staging
- [ ] Final verification audit confirms zero visual differences
- [ ] Tests pass in `psyoptimal` repo
- [ ] Mirror tracking issue created in `psyoptimal` repo
- [ ] Both tracking issues (ai #291 and psyoptimal mirror issue) closed

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Visual Auditor**
  - Name: visual-auditor
  - Role: Systematically compare production vs staging, document all visual differences
  - Agent Type: frontend-tester
  - Resume: true

- **Regression Fixer**
  - Name: regression-fixer
  - Role: Apply Tailwind 4 fixes for each cataloged regression
  - Agent Type: builder
  - Resume: true

- **Verification Specialist**
  - Name: verification-specialist
  - Role: Re-audit after fixes to confirm visual parity achieved
  - Agent Type: frontend-tester
  - Resume: true

- **Documentation Writer**
  - Name: doc-writer
  - Role: Update plan with final catalog, create mirror issue, summarize learnings
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Setup Cross-Repo Tracking
- **Task ID**: setup-tracking
- **Depends On**: none
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create mirror tracking issue in `psyoptimal` repo
- Cross-link issue #291 (ai repo) with the new psyoptimal issue
- Confirm both issues are properly linked

### 2. Initial Visual Audit
- **Task ID**: visual-audit
- **Depends On**: setup-tracking
- **Assigned To**: visual-auditor
- **Agent Type**: frontend-tester
- **Parallel**: false
- Define list of critical pages/flows to audit
- For each page, visit both production and staging
- Document visual differences with screenshots
- Create regression catalog (markdown table or document)

### 3. Fix Regressions
- **Task ID**: fix-regressions
- **Depends On**: visual-audit
- **Assigned To**: regression-fixer
- **Agent Type**: builder
- **Parallel**: false
- Work through regression catalog systematically
- For each regression: investigate root cause, apply fix, test on staging
- Mark each regression as resolved in the catalog
- Commit fixes to `psyoptimal` repo

### 4. Verification Audit
- **Task ID**: verify-fixes
- **Depends On**: fix-regressions
- **Assigned To**: verification-specialist
- **Agent Type**: frontend-tester
- **Parallel**: false
- Re-run complete visual audit on staging
- Confirm zero visual differences between staging and production
- Document any remaining issues (escalate if found)

### 5. Documentation and Closeout
- **Task ID**: document-closeout
- **Depends On**: verify-fixes
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update this plan with final regression catalog and resolution notes
- Create summary document in `psyoptimal` repo of Tailwind 4 migration gotchas
- Update both tracking issues (ai #291 and psyoptimal mirror) with completion status
- Close both issues

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-closeout
- **Assigned To**: verification-specialist
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met (including cross-repo documentation)
- Confirm staging visually matches production
- Generate final sign-off report

## Validation Commands

- `curl -I https://stage.psyoptimal.com` - Staging is accessible
- `curl -I https://app.psyoptimal.com` - Production is accessible
- `gh issue view 291 --repo tomcounsell/ai` - AI repo issue exists and is linked
- `gh issue view <psyoptimal-issue-number> --repo <psyoptimal-repo>` - Psyoptimal repo mirror issue exists
- Visual inspection: staging and production pages are visually identical

---

## Open Questions

1. **Psyoptimal repo location** - What is the full GitHub path to the `psyoptimal` repository? (needed for creating mirror issue and PRs)

2. **Critical page list** - Which pages/flows are most critical to audit? Should we focus on:
   - Public marketing pages (homepage, pricing, about)?
   - Authenticated app pages (dashboard, settings, key features)?
   - Both?

3. **Staging deployment process** - How do we deploy fixes to staging for testing? Is there a CI/CD pipeline, or manual deployment?

4. **Agent-browser availability** - What specific tool should be used for automated visual comparison? Is `agent-browser` available, or should we use a different approach (Playwright, Puppeteer, manual screenshots)?

5. **Production deployment timeline** - What's the urgency for getting this fixed? Is there a target date for deploying Tailwind 4 to production?

6. **Acceptable regression threshold** - Are minor, barely-visible differences acceptable (e.g., 1px spacing change), or must staging be pixel-perfect identical to production?
