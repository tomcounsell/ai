---
title: Blue-sky / fog-forward goal-setting — exploratory do-issue mode + do-chart decision-map skill
slug: blue-sky-fog-planning
type: feature
status: Ready
appetite: Medium
tracking: https://github.com/tomcounsell/ai/issues/2340
revision_applied: true
revision_applied_at: 2026-07-24T15:41:56Z
---

# Blue-sky / fog-forward goal-setting

## Problem

Our SDLC on-ramp only supports *well-scoped* work. `do-issue` demands defined
terms and verifiable acceptance criteria ("Think Like a Teacher"); `do-plan`
narrows a request toward a single plan. When the owner has a **loose, blue-sky,
foggy** goal — a direction he wants to name without a locked spec — the skills
push back and ask him to narrow it before the system engages. Premature
crispness fabricates certainty we don't have.

There is also no affordance for work that is **too big or unclear for one
session**: no way to say "this is one decision among many, resolve it, then
re-survey." Wayfinder ([mattpocock/skills](https://github.com/mattpocock/skills/blob/main/skills/engineering/wayfinder/SKILL.md))
names this problem and solves it with a *map* of one-at-a-time *decision
tickets*.

**Desired outcome:** blue-sky goal-setting is a first-class supported mode, fog
(in-scope territory we can't yet specify) has an explicit home and a way to
graduate out, and a decision-map affordance exists for multi-session foggy work.

Source issue: #2340 (itself filed in the new fog-forward style as a dogfood).

## Recon Summary (from #2340, re-verified)

- `do-issue` is crispness-biased; single one-line uncertainty concession in
  Step 4. Verified in `.claude/skills-global/do-issue/SKILL.md`.
- `do-plan` Phase 1.5 **Spike Resolution** already runs prototype/research
  spikes in worktrees — the "prototype ticket" affordance is latent, not
  missing. Verified in `.claude/skills-global/do-plan/SKILL.md`.
- Wayfinder has **no** model-selection guidance — "fog and model selection" is
  net-new (owner's idea), not a port. Verified against the fetched SKILL.
- New `.claude/skills-global/{dir}/SKILL.md` dirs auto-sync to every machine via
  `scripts/update/hardlinks.py::sync_claude_dirs()`; renames need a
  `RENAMED_REMOVALS` entry, but a brand-new dir needs none.

## Freshness Check

**Disposition: Unchanged.** Issue #2340 filed today (2026-07-24); baseline
commit `f79cea679`. No commits have landed on the two target skills since. The
file:line claims above were read directly at plan time. Proceed.

## Decision: Wayfinder as its own skill vs folded into do-plan

**Recommendation: a NEW global skill (`do-chart`, working name) for the
map/decision-ticket charting, plus a lightweight fog affordance folded into
`do-issue` and `do-plan`.**

Justification — **altitude and discipline**:
- `do-issue`/`do-plan` operate on **one work item**: they produce a spec/plan
  and run to completion within a session.
- Wayfinder operates **one altitude above**: it charts a *route across many
  future work items and multiple sessions*, and its explicit product is
  **decisions, not deliverables**. Its core discipline — *one decision per
  session*, a *persistent map issue*, *fog graduation* — directly contradicts
  do-plan's "produce a finished plan this session." Folding it in would give
  do-plan two conflicting operating modes.
- Therefore a separate skill is cleaner. We adapt Wayfinder's ticket types to
  tools we already have (no new sub-skill tree):

  | Wayfinder ticket | Our tool |
  |---|---|
  | research (AFK) | `Explore` / `general-purpose` subagent |
  | prototype (HITL) | `do-plan` worktree spike / builder-in-worktree |
  | grilling (HITL) | `/ask-me` skill |
  | task | manual prerequisite issue |

**Open owner decision (surfaced to PM):** the skill NAME. Working name
`do-chart` (it charts the route/map). Alternatives: `do-wayfinder`, `do-map`,
`do-survey`. Build proceeds under `do-chart` as provisional; a rename is a
trivial, reversible follow-up (dir rename + `RENAMED_REMOVALS` entry).

## Solution

Three coordinated changes, all documentation/skills (no runtime code):

### 1. `do-issue` — first-class blue-sky mode
- Add a **Step 0: Mode Select** — *well-scoped* (default; bugs, defined
  features) vs *exploratory / blue-sky* (a direction with acknowledged fog).
  Give 2-3 sentence selection criteria.
- In blue-sky mode:
  - Recon fan-out is **lighter** (read the affected area; skip the multi-agent
    fan-out unless a concern is cheap to resolve) **but the `## Recon Summary`
    section is NON-NEGOTIABLE**: blue-sky issues STILL emit the four-bucket
    summary (Confirmed / Revised / Pre-requisites / Dropped) with ≥1 item —
    lighter *content*, identical *shape*. This is a hard contract: the
    ISSUE→PLAN gate `.claude/hooks/validators/validate_issue_recon.py` exits 2
    (blocks `/do-plan`) without it. `## Recon: Skipped` is NOT a valid escape
    hatch for blue-sky work (that's for trivial issues, not exploratory ones).
    Add a one-line note in `SKILL.md` Step 3 making this explicit. The dogfood
    issue #2340 already follows this pattern (full four-bucket summary), proving
    it composes.
  - Definitions/terms still encouraged but **not blocking**.
  - Acceptance Criteria reframed as **"signals the fog cleared"** rather than
    verifiable checkboxes.
  - A **`## Fog (Not Yet Specified)`** section becomes a first-class,
    required-in-this-mode part of the template: known unknowns + suspected
    decisions that hang on open questions.
- `ISSUE_TEMPLATE.md`: add the conditional `## Fog (Not Yet Specified)` section
  and the fog-clearing framing for Acceptance Criteria.
- **`CHECKLIST.md` — make mode-aware (required, was missing from earlier draft).**
  Three hard checks that blue-sky mode softens must branch on well-scoped vs
  blue-sky instead of being silently violated:
  - *Measurable acceptance criteria* → in blue-sky mode, criteria are
    "fog-clearing signals," not yes/no checkboxes.
  - *No undefined jargon* → in blue-sky mode, definitions are encouraged, not
    blocking.
  - *Recon summary present* → unchanged: still REQUIRED in both modes (four
    buckets). This item stays a hard check.
- Anti-Patterns: add **"Premature crispness"** for exploratory work (balancing,
  not replacing, the existing "vague problem statements" which still governs
  well-scoped issues).
- Cross-link: point to `do-chart` when the direction is too big for one session.

### 2. `do-plan` — welcome fog, don't narrow it away
- `SKILL.md` Phase 1 "Narrow the problem": if the issue is fog-forward (has a
  `## Fog` section), do **not** force-narrow; chart the route instead — resolve
  what can be resolved, keep the rest as an explicit "Not yet specified" list,
  and recommend spinning a `do-chart` map if scope exceeds one session.
- `SCOPING.md`: add a **"Fog is legitimate"** principle — deliberately staying
  at low resolution is valid for exploratory issues.
- **Fog and model selection**: a short note (Phase 1.5 spikes) — route
  survey/research spikes to cheap/fast models (Haiku); reserve stronger models
  (Opus/Sonnet) for the load-bearing decision.

### 3. New global skill `do-chart` (working name)
- `.claude/skills-global/do-chart/SKILL.md` — generic body following the
  skill-context convention (probe sentence for `.claude/skill-context/do-chart.md`,
  generic `git`/`gh` baseline).
- Adapts Wayfinder to our GitHub workflow:
  - **Map** = a parent issue labeled `chart:map`, body sections: Destination,
    Notes, Decisions so far, Not yet specified (fog), Out of scope.
  - **Decision tickets** = child issues labeled `chart:decision` + a type
    (`research`/`prototype`/`grilling`/`task`), mapped to our tools (table
    above).
  - **Discipline**: one decision per session (except research); post resolution
    as a comment, close the ticket, append to Decisions-so-far, graduate fog.
  - **Fog and model selection** guidance included.
- `.claude/skill-context/do-chart.md` — repo-specific layer: `chart:*` label
  creation via `gh`, `GH_REPO` targeting, `sdlc-tool` markers, how to spawn
  `Explore`/spike agents for ticket types.
- Sync: new dir under `skills-global/` auto-propagates via `hardlinks.py` — no
  `RENAMED_REMOVALS` needed (brand new). Verify against the sync invariant test.

## Data Flow

N/A — no runtime data flow. This changes skill markdown read by the agent at
skill-invocation time. The only "flow" is: owner request → `do-issue` (mode
select) → issue with `## Fog` → `do-plan` (charts fog / recommends `do-chart`)
→ optionally `do-chart` map + decision tickets → back to `do-plan`/`do-build`
per decision.

## Documentation
- [ ] Create `docs/features/blue-sky-fog-planning.md` documenting the
      exploratory `do-issue` mode, the fog concept, `do-chart`, and fog-and-
      model-selection guidance.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Create `.claude/skill-context/do-chart.md` (repo-specific addendum).

## Update System
No update-script logic changes required. The new `do-chart` skill dir under
`.claude/skills-global/` is picked up automatically by the existing
`sync_claude_dirs()` hardlink wiring on the next `/update`; adding a directory
with a `SKILL.md` is the only requirement (no registration step). No new
dependencies or config files.

## Agent Integration
No agent-integration code required — no new CLI entry point in `pyproject.toml`
and no bridge import. `do-chart` is a slash-command skill the agent invokes like
any other `/do-*` skill; `do-issue`/`do-plan` edits are in-place skill-body
changes. The agent reaches all three through the existing skill surface.

## Test Impact
- [ ] `tests/unit/test_update_hardlinks.py` skill-sync invariant tests — VERIFY
      still pass with the new `do-chart` dir. Confirmed via critique that no test
      enumerates the live skill set in a way an *added* dir would trip
      (`test_renamed_removals_covers_deleted_skills` walks git deletions only).
      No UPDATE expected.
- [ ] Add a focused unit test asserting `do-chart/SKILL.md` exists with valid
      frontmatter (`name`, `description`) and carries the skill-context probe
      sentence — ADD as a new test.
- [ ] Manually re-run `validate_issue_recon.py` against a fog-forward issue
      shape (e.g. #2340) to CONFIRM the mode-aware blue-sky path still passes the
      ISSUE→PLAN gate (four-bucket Recon Summary preserved).
No existing behavioral tests touch `do-issue`/`do-plan` bodies (they are
markdown); the risk surface is the sync invariant + the recon gate.

## Failure Path Test Strategy
The failure mode that matters: the new skill dir breaks the machine-wide sync
invariant (a project-only skill accidentally becoming a sync destination, or a
malformed SKILL.md). Covered by running the existing
`test_no_project_only_skill_is_a_sync_destination` (and sibling sync tests) plus
the new frontmatter/probe guard. Markdown skill-body edits have no runtime
failure path to exercise beyond "does the skill still parse / is the probe
sentence present" — asserted by the `do-skills-audit` coupling guard
(`rule_13_coupling_signals`) which we run against the edited bodies.

## No-Gos
- **No full Wayfinder port.** We do NOT import its `/research`, `/prototype`,
  `/grilling`, `/domain-modeling` sub-skill tree. Justification: *separate slug*
  — those are meaningfully different features; we adapt to existing tools
  instead. (Legitimate No-Go: separate slug.)
- **No new GitHub labels beyond `chart:map` / `chart:decision`.** Created lazily
  by `do-chart` via `gh label create` at first use, not pre-provisioned.
- **No changes to the SDLC router (`/sdlc`) stage graph** in this PR. `do-chart`
  is invoked directly, not wired as a pipeline stage. (Legitimate No-Go:
  separate slug — router integration is its own future decision.)

## Rabbit Holes
- **Rebuilding Wayfinder faithfully** — its sub-skill tree is a deep well; the
  adaptation-to-existing-tools table is the boundary.
- **Over-formalizing fog** — the `## Fog` section is prose + a bullet list, not
  a schema. Resist adding validators/hooks that would re-impose crispness on the
  exploratory mode (that would defeat the point).
- **Router integration** — tempting to wire `do-chart` into `/sdlc`. Out of
  scope; it's a standalone charting skill for now.

## Success Criteria
- [ ] `do-issue` blue-sky mode is a first-class documented path with a
      `## Fog` section; an ill-defined goal files without being narrowed first,
      AND still passes `validate_issue_recon.py` (four-bucket Recon Summary
      preserved). `CHECKLIST.md` is mode-aware (no silent hard-check violations).
- [ ] `do-plan` welcomes fog (SCOPING "Fog is legitimate") and recommends
      `do-chart` for multi-session work; fog-and-model-selection note present.
- [ ] `do-chart` skill exists, follows the skill-context convention, and its
      dir satisfies the sync invariant (test green).
- [ ] The own-skill-vs-fold-in decision is recorded with justification; owner
      signs off on the final name.
- [ ] `docs/features/blue-sky-fog-planning.md` + README index entry created.
- [ ] `python -m ruff check` clean; targeted tests green.

## Open Questions
1. **`do-chart` final name** — owner call (`do-chart` / `do-wayfinder` /
   `do-map` / `do-survey`). **Sequencing (per critique):** the name is threaded
   through the skill dir, `skill-context/{name}.md`, feature doc, README entry,
   `{name}:*` labels, and cross-links from `do-issue`/`do-plan`, so a post-build
   rename is multi-file churn, not trivial. Therefore: the `do-issue` +
   `do-plan` fog edits (no naming dependency) build FIRST; the new charting
   skill is created only AFTER the owner picks the name. Both land in the same
   PR/branch.
