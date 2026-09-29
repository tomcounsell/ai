---
title: Blue-sky / fog-forward goal-setting — exploratory do-issue mode + fog in do-plan
slug: blue-sky-fog-planning
type: feature
status: Ready
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/2340
revision_applied: true
revision_applied_at: 2026-09-29T06:19:59Z
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
`Refs #2340` and names what remains. The naming question (candidates
`do-chart` / `do-wayfinder` / `do-map` / `do-survey`) is tracked on #2340 and
is not needed for this plan.

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
lists. Rather than rebase it, the change is **redone on current main** in this
issue's recorded SDLC lane (the lane-identity resolver owns the branch name;
do not re-derive it from this plan's filename). The build closes draft PR
#3577 as superseded, with a comment linking the new PR; nothing from its
single commit is cherry-picked.

Re-verified 2026-09-29 against `origin/main` at 37de11f3e (the single
baseline for every check in this plan): no commits since f43ab3669
touch `.claude/skills-global/do-issue/`, `.claude/skills-global/do-plan/`, or
`docs/features/README.md`; every section anchor cited below (do-issue Step 1,
Step 4 rule 4, CHECKLIST "No undefined jargon" / "Measurable acceptance
criteria", do-plan SCOPING §1, SKILL Phase 1 step 2 and Phase 1.5) still exists;
the recon gate passes for #2340; the repo-token grep in Verification is clean
(exit 1) on the baseline; the skill audit passes for both `do-issue` and
`do-plan` (exit 0); `do-plan/SKILL.md` is 453 lines against the audit's
500-line limit. #2340 has no comments.

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
  specifics the requester did not give, it is blue-sky. Default to
  well-scoped unless the requester signals exploration (so an unattended run
  never stalls on the choice), and record the chosen mode in the issue body.
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
  Two further checks get a one-line blue-sky reading so the checklist does not
  kill every blue-sky issue: **Observed, not inferred** applies to the pain
  motivating the direction, not to the specifics still in fog (the kill
  criterion stays in force for that pain: an unobserved "could/would" pain
  still means file nothing); **Recon performed** is met by the broad scan plus
  fan-out on cheap concerns only. **Not already decided** and the remaining
  falsification checks apply unchanged in both modes. Each of the four
  blue-sky readings is appended to its own check's line (every check is a
  single bullet line today), so each check carries the word "blue-sky" on
  exactly its own line.

### 2. `do-plan` — chart fog instead of narrowing it away
- `SCOPING.md` §1 gains **"When the issue is fog-forward"**. Trigger: the
  issue carries a `## Fog (Not Yet Specified)` section or records blue-sky
  mode in its body. Then: keep the low
  resolution, resolve what can be resolved, carry the rest as a
  **Not Yet Specified** list in the plan that graduates into tasks as it clears.
  For work spanning several interdependent decisions across sessions, **chart a
  decision map**: one parent issue (destination, decisions so far, open fog) and
  one child issue per decision, resolved one at a time with the result recorded
  on the map, then re-plan. Distinguish fog (one unknown direction: chart it)
  from a grab-bag (several known features: split it).
- `SKILL.md` Phase 1 step 2 points to that SCOPING section for fog-forward
  issues (same trigger).
- `SKILL.md` additions stay short (target under 15 lines total) because the
  file is 453 lines against the skill audit's 500-line limit; any longer fog
  guidance goes in `SCOPING.md`.
- `SKILL.md` Phase 1.5 gains **fog and model selection**: survey/research
  spikes go to the cheapest capable model; the plan author's strongest
  reasoning goes to the load-bearing decision the fog hangs on.

### 3. Docs
- `docs/features/blue-sky-fog-planning.md` describing the mode, the Fog section,
  decision maps, model selection, and the deferred charting skill. It states
  that the charting skill, once built, absorbs and replaces the SCOPING
  decision-map guidance, so the two never coexist as divergent sources.
- Entry in `docs/features/README.md`.

## Prior Art

- **Draft PR #3577** (`[WIP] Blue-sky/fog mode in do-issue + fog and model
  selection in do-plan`, open): the July attempt at this same change. Written
  against a pre-Step-3.5 do-issue and a pre-genericized do-plan, and it puts
  repo tooling and model family names into global bodies. Superseded by this
  plan's redo on current main; closed by the build once the new PR exists
  (task `close-superseded-pr`).
- **Wayfinder** (mattpocock/skills): external source of the decision-map idea;
  borrowed as a lightweight affordance only (see No-Gos).

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
- [ ] `python .claude/hooks/validators/validate_issue_recon.py 2340` passes.
      This is a regression smoke check only (it already exits 0 on the
      baseline with an unedited validator); the real evidence that blue-sky
      issues keep satisfying the gate is that `ISSUE_TEMPLATE.md` keeps
      `## Recon Summary` in both modes (Verification row below).
- [ ] The skill audit run on the real edited bodies passes:
      `audit_skills.py --no-sync --skill do-issue` and `--skill do-plan`
      (exit 0 on the baseline). This is the check that catches global-body
      coupling (audit rule 21, a bare `/sdlc` or `/update` slash invocation)
      and the 500-line SKILL.md limit (rule 1).
- [ ] Skill-infrastructure tests stay green, run via `scripts/pytest-clean.sh`:
      `tests/unit/test_skills_audit.py` (audit rules on synthetic fixtures),
      `tests/unit/test_update_hardlinks.py`, `tests/unit/test_symlinks.py`,
      `tests/unit/test_relink_global_skills.py` (link integrity, not content).
      None reads these skill bodies' wording, so no UPDATE/DELETE/REPLACE is
      expected; they guard that in-place edits keep the hardlinks intact.
- [ ] Grep every edited global body (do-issue/, do-plan/SCOPING.md,
      do-plan/SKILL.md) for repo-specific tokens (`sdlc-tool`,
      `validate_issue_recon`, `valor`, model family names): none.

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
      fog-forward issue still passes the recon gate; CHECKLIST blue-sky readings
      present for all four affected checks (No undefined jargon, Measurable
      acceptance criteria, Observed not inferred, Recon performed).
- [ ] `do-plan` welcomes fog, offers the decision-map affordance, and carries
      the fog-and-model-selection note.
- [ ] Edited global bodies contain no repo-specific tooling or model names.
- [ ] Feature doc and README entry exist.
- [ ] PR body says `Refs #2340` and lists the deferred charting skill.
- [ ] Draft PR #3577 closed as superseded, with a comment linking the new PR.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Recon gate passes for the fog-forward dogfood issue | `python .claude/hooks/validators/validate_issue_recon.py 2340` | exit code 0 |
| Global bodies free of repo tooling | Command A (fenced block below the table) | exit code 1 (clean on baseline 37de11f3e) |
| Blue-sky mode present | `grep -c 'Fog (Not Yet Specified)' .claude/skills-global/do-issue/ISSUE_TEMPLATE.md` | output > 0 |
| Recon Summary kept in template | `grep -c 'Recon Summary' .claude/skills-global/do-issue/ISSUE_TEMPLATE.md` | output > 0 |
| CHECKLIST blue-sky readings present, one per check | Command B (fenced block below the table) | output 4 (each of the four check lines carries its own blue-sky reading; 0 on the baseline) |
| Skill audit passes on the edited bodies | `python .claude/skills-global/audit-skills/scripts/audit_skills.py --no-sync --skill do-issue && python .claude/skills-global/audit-skills/scripts/audit_skills.py --no-sync --skill do-plan` | exit code 0 (both pass on baseline 37de11f3e) |
| do-plan SKILL.md within the line limit | `wc -l < .claude/skills-global/do-plan/SKILL.md` | output < 500 |
| Skill tests green | `scripts/pytest-clean.sh tests/unit/test_skills_audit.py tests/unit/test_update_hardlinks.py tests/unit/test_symlinks.py tests/unit/test_relink_global_skills.py` | exit code 0 |
| Decision map present | `grep -c -i 'decision map' .claude/skills-global/do-plan/SCOPING.md` | output > 0 |
| Feature doc indexed | `grep -c 'blue-sky-fog-planning' docs/features/README.md` | output > 0 |

Commands A and B contain regex alternation pipes, so they live outside the
table: a markdown table forces `|` to be escaped as `\|`, and run verbatim the
escaped form makes `grep -E` match a literal pipe (a vacuous exit 1 for A, a
false 0 for B). Run them exactly as written here.

Command A (token grep; must exit 1):

```bash
git grep -n -E 'sdlc-tool|validate_issue_recon|valor|[Hh]aiku|[Ss]onnet|Opus' -- .claude/skills-global/do-issue .claude/skills-global/do-plan/SCOPING.md .claude/skills-global/do-plan/SKILL.md
```

Before trusting A's exit 1, prove it red: run the same pattern with plain
`grep -n -E` against a scratch file containing `sdlc-tool`; it must exit 0
and print the line. (Proven at plan time on 2026-09-29: the scratch file
matched with exit 0, and the real bodies exit 1 on baseline 37de11f3e.)

Command B (CHECKLIST readings; must print 4):

```bash
grep -i -E '^- \[ \] \*\*(No undefined jargon|Measurable acceptance criteria|Observed, not inferred|Recon performed)\*\*.*blue-sky' .claude/skills-global/do-issue/CHECKLIST.md | wc -l
```

(Proven at plan time: against a scratch file with two matching check lines it
prints 2, so the pattern counts real lines, not a literal pipe.)

## Team Orchestration

One builder, sequential, then a validator. Every edit is skill markdown or a
feature doc; there is no parallelizable code.

### Team Members

- **Builder (fog-skills)**
  - Name: fog-builder
  - Role: edit the do-issue and do-plan global skill bodies and write the feature doc
  - Agent Type: builder
  - Resume: true

- **Validator (fog-skills)**
  - Name: fog-validator
  - Role: run every Verification row and the skill tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fog affordances in the skill bodies
- **Task ID**: build-fog-skills
- **Depends On**: none
- **Validates**: tests/unit/test_skills_audit.py, tests/unit/test_update_hardlinks.py
- **Assigned To**: fog-builder
- **Agent Type**: builder
- **Parallel**: false
- do-issue `SKILL.md`: Step 1 mode decision (default well-scoped, record mode), Blue-sky mode subsection, Step 4 rule 4 cross-reference (Solution §1).
- do-issue `ISSUE_TEMPLATE.md`: conditional `## Fog (Not Yet Specified)`, blue-sky acceptance-criteria comment; `## Recon Summary` unchanged.
- do-issue `CHECKLIST.md`: blue-sky variants for No undefined jargon, Measurable acceptance criteria, Observed not inferred, Recon performed, each appended to its own check's line.
- do-plan `SCOPING.md` §1 "When the issue is fog-forward" (triggered by a `## Fog (Not Yet Specified)` section or recorded blue-sky mode) with the decision map; `SKILL.md` Phase 1 step 2 pointer and Phase 1.5 fog-and-model-selection note (Solution §2), with no model family names and under 15 added lines.
- Edit files in place (Edit tool), never replace-and-rename, so the `~/.claude/skills/` hardlinks survive.

### 2. Feature doc
- **Task ID**: build-docs
- **Depends On**: build-fog-skills
- **Assigned To**: fog-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/blue-sky-fog-planning.md` (Solution §3, including the charting-skill-absorbs-decision-map statement) and index it in `docs/features/README.md`.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-fog-skills, build-docs
- **Assigned To**: fog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table, including the skill audit for `do-issue` and `do-plan`; report pass/fail per row.

### 4. Close the superseded draft
- **Task ID**: close-superseded-pr
- **Depends On**: validate-all, and the new PR existing
- **Assigned To**: orchestrator (the do-build lead, after PR creation)
- **Agent Type**: n/a
- **Parallel**: false
- `gh pr close 3577 --comment "Superseded by #<new PR number>, which redoes this change on current main."`
- The PR body says `Refs #2340` (never a closing keyword) and names the deferred charting skill.

## Critique Results

Round 3 (re-critique of revision 3b15ad0ca; sequential lenses, Agent tool unavailable). Verdict READY TO BUILD (WITH CONCERNS); both findings addressed in the round-3 revision pass. All round-2 findings are closed: the Verification table now runs the skill audit and checks the line limit, the Test Impact wording is corrected, the SCOPING trigger is stated, the CHECKLIST readings sit on their own lines, the Open Questions section is gone, and one baseline (37de11f3e) is named throughout.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The "Global bodies free of repo tooling" and "CHECKLIST blue-sky readings" rows carry markdown-escaped `\\|` in their commands. Run verbatim, `grep -E` treats `\\|` as a literal pipe, so the token grep exits 1 vacuously (a false green) and the CHECKLIST count returns 0 (a false red). Verified against a file that contains sdlc-tool. | Commands A and B moved to fenced blocks below the Verification table, with a prove-red step for A (both proven at plan time) | Put both commands in fenced code blocks outside the table, or note that `\\|` is table escaping and means `\|` in the shell. Before trusting the token grep's exit 1, check that it returns 0 against a scratch file containing "sdlc-tool". |
| NIT | Scope & Value | The Success Criteria bullet says blue-sky variants exist "for the two checks the mode changes", but Solution §1 and Verification require four readings. | Success Criteria bullet now names all four checks | Reword it to name all four checks. |
