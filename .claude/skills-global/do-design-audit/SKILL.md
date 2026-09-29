---
name: do-design-audit
description: "Audit a web UI's design quality. Use on 'review this design', 'check the UI', 'audit this page', or a URL for design feedback."
allowed-tools: mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_click, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab, mcp__byob__browser_list_tabs, mcp__byob__browser_wait_for, Read
context: fork
effort: medium
---

Audit an existing web interface against premium design criteria and return the report below: every rubric row scored with a specific finding, plus the three most impactful fixes. This is the review-time companion to `/frontend-design`: that skill builds to the standard, this one audits against it. Be opinionated; "acceptable" is not a compliment.

**Done:** every page reviewed has a screenshot you opened and looked at; all ten rows are scored with a finding that names the exact element or section ("the hero heading and body share a weight, so the page reads flat", never "typography needs work"); each top-3 fix is actionable without a follow-up question.

## Repo Context Probe

If `.claude/skill-context/do-design-audit.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file declares how the repo's harness opts a session into real-Chrome mode. Without it, make sure your browser automation is in whatever "use the real, logged-in browser" mode your harness provides.

## Constraints and facts

- Runs against the user's real, logged-in Chrome via BYOB (`mcp__byob__browser_*`); public and authenticated pages are audited the same way. There is no headless fallback. Two concurrent real-Chrome sessions race on the active tab and corrupt each other's DOM, so run one audit at a time.
- Findings only: modify no code. Fixes go through `/frontend-design` (or the design system for systemic issues like type or palette), then re-audit.
- Inputs: `url` (required; local dev server or deployed) and optional `--pages` (comma-separated paths). Without `--pages`, discover pages by following nav links from `browser_read`'s `interactiveElements`, up to 6 pages.
- Screenshot each page with `browser_screenshot(savePath="/tmp/design-audit-<slug>.png")` right after load, before interacting (first impression). Desktop viewport only; mobile responsiveness is an informed prediction, so flag specific concerns rather than a blanket pass.

## Rubric

Score each: ✅ Premium, ⚠️ Acceptable (won't embarrass, won't impress), ❌ Needs work.

1. **Visual hierarchy**: a clear focal point and primary action scannable in 3 seconds. Fails: everything at equal weight, no "start here", CTA blends in.
2. **Typography**: distinct display and body fonts on a real scale, 45-75 character measure. Fails: Inter, Roboto, Arial, Open Sans, or system-ui as the sole face; near-identical heading and body weights; arbitrary sizes; monospace as a "technical" signal.
3. **Color and contrast**: a committed palette, tinted neutrals, sparing accent, readable contrast. Fails: cyan-on-dark or purple-to-blue gradients, gradient text on headings, gray text on colored backgrounds, four-plus competing accents, neon-on-dark as the default.
4. **Spacing and alignment**: rhythm from varied spacing on an implicit grid. Fails: identical padding everywhere, unexplained misalignment, sections bleeding together.
5. **Visual details**: purposeful imagery and decoration. Fails: generic drop-shadow rounded rectangles, decorative glassmorphism, an icon above every heading, data-free sparklines, the thick one-side colored border, placeholder imagery.
6. **Micro-interactions**: designed hover, focus, active, and disabled states (infer from HTML/CSS when you cannot interact). Fails: no hover state, default blue focus rings on styled elements, every button primary.
7. **Consistency**: one component vocabulary across sections and pages. Fails: unexplained second button style, mixed corner radii, sections that look like different templates.
8. **Trust signals**: polish that shows care. Fails: lorem ipsum, broken images, a bare footer, badly cropped logos or testimonials.
9. **Mobile responsiveness** (predicted): fluid structure, readable at 375px, 44px touch targets. Fails: four-column grids with no breakpoint strategy, hover-only menus, tiny type.
10. **AI slop check**: would someone guess "AI made this"? Fails on the tells above plus the hero-metric template (big number, small label, gradient accent), cards inside cards, endless same-sized icon cards, everything centered, and current-model defaults used without a brand reason: cream or off-white ground, an italic accent word in headlines, numbered 01/02/03 section labels, monospace eyebrow labels, pill buttons everywhere, emoji bullets.

## Output

```markdown
## Design Review: [URL]

**Pages reviewed:** [pages screenshotted]

| Dimension | Rating | Findings |
|-----------|--------|----------|
| Visual hierarchy | ✅/⚠️/❌ | [specific observation] |
| Typography | | |
| Color & contrast | | |
| Spacing & alignment | | |
| Visual details | | |
| Micro-interactions | | |
| Consistency | | |
| Trust signals | | |
| Mobile responsiveness | | |
| AI Slop Check | | |

### Top 3 Improvements
**1. [The problem, named]** What to change, where, and why ("raise the nav-to-hero gap from 12px to 48px; they read as one block").
**2. ...**
**3. ...**

### Overall Assessment
[1-2 sentences: quality level, strongest quality, most critical weakness. No hedging.]
```
