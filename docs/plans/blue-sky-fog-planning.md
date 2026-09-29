---
title: Blue-sky / fog-forward goal-setting — exploratory do-issue mode + fog in do-plan
slug: blue-sky-fog-planning
type: feature
status: Ready
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/2340
---

# Blue-sky / fog-forward goal-setting

## Problem

Our SDLC on-ramp only supports *well-scoped* work. `do-issue` demands defined
terms and verifiable acceptance criteria ("Think Like a Teacher"); `do-plan`
narrows a request toward a single plan. When the owner has a **loose, blue-sky,
foggy** goal (a direction he wants to name without a locked spec), the skills
push back and ask him to narrow it before the system engages. Premature
crispness fabricates certainty we don't have.

There is also no affordance for work that is **too big or unclear for one
session**: no way to say "this is one decision among many, resolve it, then
re-survey." Wayfinder ([mattpocock/skills](https://github.com/mattpocock/skills/blob/main/skills/engineering/wayfinder/SKILL.md))
names this problem and answers it with a *map* of one-at-a-time *decision
tickets*.

Source issue: #2340 (itself filed in the fog-forward style as a dogfood).

## Scope of this plan (partial: Refs #2340, not Closes)

This plan ships the part of #2340 that has no open owner decision:

1. `do-issue` blue-sky mode.
2. `do-plan` welcomes fog, with a lightweight route-charting affordance
   (a parent map issue plus one-decision-per-issue children, using plain `gh`).
3. Fog-and-model-selection guidance.
4. A feature doc.

**[EXTERNAL] Deferred to a follow-up under #2340:** a dedicated charting skill (the full
Wayfinder-style map discipline). The July revision of this plan recommended a
new global skill (working name `do-chart`) and made its **name an owner
decision** that must precede building it, since the name threads through the
dir, skill-context file, labels, docs, and cross-links. That decision has not
been made, so the skill is out of this PR. #2340 stays open for it; the PR says
`Refs #2340` and names what remains.

## Recon Summary (from #2340, re-verified 2026-09-29)

- `do-issue` is still crispness-biased: its only uncertainty concession is the
  Step 4 "write open questions instead of approaches" line.
- `do-plan` Phase 1.5 Spike Resolution still runs prototype/research spikes in
  worktrees, so the "prototype ticket" affordance is latent, not missing.
- `do-plan` Phase 0 now treats the recon gate as a repo-declared mechanism
  (generic body; this repo's gate is `.claude/hooks/validators/validate_issue_recon.py`,
  which requires a `## Recon Summary` with ≥1 bucket item or `## Recon: Skipped`).
  Blue-sky issues must keep the Recon Summary shape.

## Freshness Check

**Disposition: Revised.** The July WIP commit on `session/dev-da3457d4`
(draft PR #3577) was written against a July baseline. Since then do-issue gained
Step 3.5 "Try to Kill the Issue" and Falsification Checks, and do-plan's
Phase 0 was genericized (the recon gate is now a context-file declaration).
The July diff also names repo-specific tooling (`validate_issue_recon.py`) and
specific model families in global skill bodies, and leans on long anti-pattern
lists. Rather than rebase it, the change is **redone on current main** in a new
lane (`session/blue-sky-fog-planning`); PR #3577 is closed as superseded.

The Opus 5.5 skills audit (`docs/audits/opus-5-5-skills/`, #3565) has open
recommendations for these same skills (do-issue RECON headless stop, label
default, start marker; do-plan stash block, kebab slugs). They are independent
bug fixes with their own lanes and are **not** folded in here; this change only
adds fog affordances and does not touch the lines those fixes target.

## Solution

All edits are skill markdown under `.claude/skills-global/`, kept generic per
`docs/features/skill-context-convention.md` (no repo tooling, no model names;
say what to do rather than enumerate failure modes).

### 1. `do-issue` — first-class blue-sky mode
- `SKILL.md` Step 1 gains a **mode** decision: *well-scoped* (default) or
  *blue-sky* (a direction whose specifics are genuinely unknown). One short
  criterion: if writing verifiable acceptance criteria would require inventing
  specifics the requester did not give, it is blue-sky; when unsure, ask.
- A short **Blue-sky mode** subsection states what changes:
  - Recon reads the area to ground the direction; fan-out only for cheap
    concerns. The `## Recon Summary` keeps its four-bucket shape in both modes.
  - Definitions: define what you can; terms the exploration exists to pin down
    go in the Fog section.
  - Acceptance criteria become **signals the fog cleared** (still checkable).
  - A `## Fog (Not Yet Specified)` section lists known unknowns and the
    decisions that hang on them.
- Step 4 rule 4 (open questions instead of approaches) cross-references the Fog
  section.
- `ISSUE_TEMPLATE.md`: conditional `## Fog (Not Yet Specified)` section; an
  acceptance-criteria comment explaining the blue-sky framing.
- `CHECKLIST.md`: the two checks blue-sky mode changes (**No undefined jargon**,
  **Measurable acceptance criteria**) each carry a one-line blue-sky variant.
  Recon and falsification checks are unchanged in both modes.

### 2. `do-plan` — chart fog instead of narrowing it away
- `SCOPING.md` §1 gains **"When the issue is fog-forward"**: keep the low
  resolution, resolve what can be resolved, carry the rest as a
  **Not Yet Specified** list in the plan that graduates into tasks as it clears.
  For work spanning several interdependent decisions across sessions, **chart a
  decision map**: one parent issue (destination, decisions so far, open fog) and
  one child issue per decision, resolved one at a time with the result recorded
  on the map, then re-plan. Distinguish fog (one unknown direction: chart it)
  from a grab-bag (several known features: split it).
- `SKILL.md` Phase 1 step 2 points to that SCOPING section for fog-forward issues.
- `SKILL.md` Phase 1.5 gains **fog and model selection**: survey/research
  spikes go to the cheapest capable model; the plan author's strongest
  reasoning goes to the load-bearing decision the fog hangs on.

### 3. Docs
- `docs/features/blue-sky-fog-planning.md` describing the mode, the Fog section,
  decision maps, model selection, and the deferred charting skill.
- Entry in `docs/features/README.md`.

## Data Flow

No runtime data flow; skill markdown read at invocation time. Flow of work:
owner request → `do-issue` (mode) → issue with `## Fog` → `do-plan` (charts fog;
recommends a decision map when the work spans sessions) → per-decision issues →
`do-plan`/`do-build` as each clears.

## Documentation
- [ ] Create `docs/features/blue-sky-fog-planning.md`.
- [ ] Add it to `docs/features/README.md`.

## Update System
No update-script changes. Edited files already live under
`.claude/skills-global/` and propagate through the existing hardlinks on
`/update`. No new skill directory, so no `RENAMED_REMOVALS` entry.

## Agent Integration
None. In-place edits to existing skill bodies reached through the existing
skill surface.

## Test Impact
No existing test asserts on these skill bodies' fog/mode wording. Verification:
- [ ] `python .claude/hooks/validators/validate_issue_recon.py 2340` passes
      (the fog-forward dogfood issue satisfies the ISSUE→PLAN gate).
- [ ] Existing skill lint/coupling tests that scan `skills-global` bodies stay
      green (run the tests that reference `skills-global` by grep; e.g. skill
      frontmatter / coupling guards).
- [ ] Grep the edited global bodies for repo-specific tokens
      (`sdlc-tool`, `validate_issue_recon`, `valor`, model family names): none.

## Failure Path Test Strategy
The only failure mode is a global body regressing to repo coupling or breaking
the recon-gate contract; both are covered by the verification greps and the
validator run above.

## No-Gos
- **[EXTERNAL] The dedicated charting skill** ( blocked on the owner's naming
  decision; tracked on #2340).
- **No full Wayfinder port** of its sub-skill tree; existing tools cover the
  ticket types (Explore/general-purpose agents for research, do-plan worktree
  spikes for prototypes, `/ask-me` for grilling).
- **No validators or hooks for the Fog section**: re-imposing crispness on the
  exploratory mode would defeat it.
- **No audit-recommendation fixes** for these skills (own lanes).
- **No `/sdlc` router changes.**

## Rabbit Holes
- Over-formalizing fog into a schema.
- Writing long anti-pattern lists for blue-sky mode; state the mode's rules.

## Success Criteria
- [ ] `do-issue` has a documented blue-sky mode with a `## Fog` section; a
      fog-forward issue still passes the recon gate; CHECKLIST blue-sky variants
      present for the two checks the mode changes.
- [ ] `do-plan` welcomes fog, offers the decision-map affordance, and carries
      the fog-and-model-selection note.
- [ ] Edited global bodies contain no repo-specific tooling or model names.
- [ ] Feature doc and README entry exist.
- [ ] PR body says `Refs #2340` and lists the deferred charting skill.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Recon gate passes for the fog-forward dogfood issue | `python .claude/hooks/validators/validate_issue_recon.py 2340` | exit code 0 |
| Global bodies free of repo tooling | `git grep -n -E 'sdlc-tool\|validate_issue_recon\|[Hh]aiku\|[Ss]onnet\|Opus' -- .claude/skills-global/do-issue .claude/skills-global/do-plan/SCOPING.md` | exit code 1 |
| Blue-sky mode present | `grep -c 'Fog (Not Yet Specified)' .claude/skills-global/do-issue/ISSUE_TEMPLATE.md` | output > 0 |
| Decision map present | `grep -c -i 'decision map' .claude/skills-global/do-plan/SCOPING.md` | output > 0 |
| Feature doc indexed | `grep -c 'blue-sky-fog-planning' docs/features/README.md` | output > 0 |

## Open Questions
1. **Charting skill name** (owner): `do-chart` / `do-wayfinder` / `do-map` /
   `do-survey`. Needed before the follow-up; not needed for this plan.
