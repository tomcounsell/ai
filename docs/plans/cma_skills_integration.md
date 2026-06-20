---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-20
tracking: https://github.com/tomcounsell/ai/issues/1747
last_comment_id:
---

# Integrate imagine-agent and build-agent CMA Skills

## Problem

Valor now has two built, smoke-tested global skills — `/imagine-agent` and `/build-agent` — that together let Valor create **Claude Managed Agents (CMA)**: live, scheduled AI agents running in a client's Anthropic account. `/imagine-agent` interviews a non-technical stakeholder about outcomes and emits a `build-sheet.json`; `/build-agent` consumes that sheet and runs the create → launch → grade → schedule loop against the live CMA API.

These skills exist on disk in `.claude/skills-global/` and work when invoked, but they are invisible to the rest of the system:

**Current behavior:**
- None of Valor's persona segments (`config/personas/segments/tools.md`, `work-patterns.md`, etc.) mention CMA creation, so the agent has no self-awareness it can do this work or talk to non-technical stakeholders about agent requirements.
- No feature doc in `docs/features/` describes the paired workflow — contributors and the planner have no reference for where it fits.
- No structural tests confirm the two `SKILL.md` files are well-formed or that the persona mention exists.

**Desired outcome:**
- Valor's persona explicitly names CMA creation as a known **secondary, non-SDLC** capability via a brief subsection in `tools.md`.
- A feature doc `docs/features/imagine-build-agent-cma.md` describes the two-skill workflow, the build-sheet contract, the sync wiring, and the Skill-tool integration path; it is indexed in `docs/features/README.md`.
- Structural tests in `tests/unit/test_cma_skills_well_formed.py` verify both `SKILL.md` files are well-formed, build-agent's reference files exist, and `tools.md` mentions `imagine-agent`.

The two `SKILL.md` files themselves are **out of scope** — already built and smoke-tested. This is pure integration.

## Freshness Check

**Baseline commit:** `33b571a43d2caf3545865ede546c77ff3121d3ce`
**Issue filed at:** 2026-06-20T05:17:09Z
**Disposition:** Unchanged

The issue was filed today and HEAD is the same commit (`33b571a4`) the skills were built and smoke-tested on. `git log --since` the issue createdAt against `tools.md`, `docs/features/README.md`, `tests/unit/test_skills_exist.py`, and `scripts/update/hardlinks.py` returns no commits — nothing has moved.

**File:line references re-verified:**
- `.claude/skills-global/imagine-agent/SKILL.md` — has valid frontmatter (`name: imagine-agent`, `description:`, `allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent`) — still holds.
- `.claude/skills-global/build-agent/SKILL.md` — has valid frontmatter (`name: build-agent`, `description:`, `allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion`) — still holds.
- `.claude/skills-global/build-agent/references/{cma-primitives.md,build-sheet.md}` — both present — still holds.
- `config/personas/segments/tools.md` — capability-group structure confirmed (MCP Servers, Development Tools, BYOB, Computer Use, Local Python Tools, Communication); no CMA / imagine-agent / build-agent mention anywhere — gap confirmed.
- `config/personas/segments/manifest.json` — segment order is `identity.md`, `work-patterns.md`, `tools.md`, `private-tag.md`; all personas render all segments, so adding to `tools.md` reaches every persona — confirmed.
- `scripts/update/hardlinks.py` — `sync_claude_dirs()` calls `_sync_skills(.claude/skills-global, ~/.claude/skills)` which syncs every directory; `PROJECT_ONLY_SKILLS` does not contain `imagine-agent` or `build-agent` — sync coverage confirmed.
- `docs/features/README.md` — single alphabetical "Features" table; the new entry lands between "Image Vision Support" and "Intake Classifier" — confirmed.

**Cited sibling issues/PRs re-checked:** None cited beyond the branch `skills/imagine-build-agent-cma` at commit `33b571a4`, which is the current HEAD.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** No drift. The issue's Recon Summary (5 confirmed, 1 revised, 0 prereqs, 1 dropped) is fully accurate against current main.

## Prior Art

No prior issues found related to this work — this is the integration follow-up for newly built skills, not a re-attempt. The integration mirrors the existing pattern established by `tests/unit/test_skills_exist.py` (added for the 6 prior global skills: ontologies, grill-me, deepen, observability, zoom-out, tdd) and the `docs/features/email-google-workspace-skills.md` doc (the closest precedent: a feature doc for globally-synced Skill-tool skills).

## Research

No relevant external findings — this is purely internal integration (markdown persona segments, a feature doc, and structural pytest checks). No external libraries, APIs, or ecosystem patterns are involved. The CMA API itself is documented inside the skills' own `references/` files, which are out of scope for modification.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified in the issue's Solution Sketch and Recon Summary)
- Review rounds: 1 (one code review pass)

This is a low-risk additive integration: one persona subsection, one feature doc + one index row, one new structural test file. No code paths change; no existing behavior is touched.

## Prerequisites

No prerequisites — this work has no external dependencies. Everything needed (the two `SKILL.md` files, their references, the persona segment, the test pattern) already exists in the repo at HEAD.

## Solution

### Key Elements

- **Persona subsection (`config/personas/segments/tools.md`):** A new `### Managed Agent Creation (CMA)` subsection under "Tools I Use", framed as a *secondary, non-SDLC* capability. One short paragraph: what CMA is, when to reach for it, and the two skills (`/imagine-agent` for the client interview → build-sheet, `/build-agent` for build-sheet → live CMA loop). Because all personas render `tools.md` (per the manifest), this single edit reaches every persona — no overlay edits needed.
- **Feature doc (`docs/features/imagine-build-agent-cma.md`):** Describes the paired workflow, its place in the system (client-facing, off the core SDLC loop), the `build-sheet.json` contract handoff, how global-skill sync makes both skills available on every machine, and the Skill-tool integration path (no CLI, no bridge import).
- **Index row (`docs/features/README.md`):** One alphabetically-placed entry between "Image Vision Support" and "Intake Classifier".
- **Structural test file (`tests/unit/test_cma_skills_well_formed.py`):** Existence + frontmatter + reference-file + persona-mention checks, modeled on `tests/unit/test_skills_exist.py`.

### Flow

Reader/agent perspective:

Agent reads `tools.md` → sees `### Managed Agent Creation (CMA)` → knows it can create managed agents and interview non-technical stakeholders → reaches the capability via the Skill tool (`/imagine-agent` then `/build-agent`).

Contributor perspective:

Contributor opens `docs/features/README.md` → finds "Imagine/Build Agent (CMA)" row → reads `imagine-build-agent-cma.md` → understands the paired workflow and that sync is automatic.

### Technical Approach

- **Persona edit:** Insert a new `###` subsection in `config/personas/segments/tools.md` under "Tools I Use" (e.g. after "Local Python Tools" / before "Communication"). Keep it to a few sentences. Must contain the literal token `imagine-agent` (the persona-mention test asserts on it) and reference CMA / build-agent. Frame as secondary explicitly ("a secondary, non-SDLC capability").
- **Feature doc:** Follow the structure of `docs/features/email-google-workspace-skills.md` (the precedent for globally-synced Skill-tool skills). Sections: what CMA is, the two skills and their handoff via `build-sheet.json`, place in the system (non-SDLC / client-facing), Update System note (sync is automatic via `sync_claude_dirs()`), and Agent Integration note (Skill-tool only — no `pyproject.toml` entry, no bridge import).
- **Index row:** Add to the single "Features" table in alphabetical position (between Image Vision and Intake Classifier). Link text "Imagine/Build Agent (CMA)", status "Shipped".
- **Tests:** New file `tests/unit/test_cma_skills_well_formed.py`. Parametrize over `["imagine-agent", "build-agent"]` for directory + `SKILL.md` existence and frontmatter (`name:`, `description:`, `allowed-tools:`). A dedicated test asserts `build-agent/references/cma-primitives.md` and `build-agent/references/build-sheet.md` exist. A dedicated test reads `config/personas/segments/tools.md` and asserts it contains `imagine-agent`. Use `pathlib.Path(__file__).parent.parent.parent` for `REPO_ROOT`, matching the existing pattern. Tests are structural only — no CMA API calls.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. The deliverables are markdown (persona segment, feature doc) and a structural pytest file that uses plain assertions on filesystem reads — there is no `try/except` logic to test.

### Empty/Invalid Input Handling
- Not applicable to the deliverables. The new test file is itself the validation surface: it fails loudly (assertion error with a descriptive message) if a `SKILL.md` is missing, lacks required frontmatter, a reference file is absent, or the `tools.md` mention is missing. There are no new runtime functions that accept user input.

### Error State Rendering
- No user-visible runtime output is produced by this work. The persona text and feature doc are static content; the test file's "error state" is a failing assertion, which pytest renders with the embedded failure message (e.g. `f"SKILL.md for '{skill_name}' is missing 'allowed-tools:' frontmatter"`).

## Test Impact

No existing tests affected — this work is purely additive (a brand-new test file, a new feature doc, a new index row, and an additive persona subsection). It does not modify any existing function, interface, or behavior, so no current test's assertions change. `tests/unit/test_skills_exist.py` is used only as a copy-from pattern reference and is not edited; its `NEW_SKILLS` list is intentionally left untouched (it covers a different skill cohort).

## Rabbit Holes

- **Modifying the `SKILL.md` files.** They are built and smoke-tested at HEAD; touching them is explicitly out of scope. Resist any "while I'm here, let me tidy the frontmatter" urge.
- **Building integration tests that call the live CMA API.** Tests are structural only (file/frontmatter/content existence). Exercising `/v1/agents` or `/v1/sessions` is a different, heavier effort and is not what this issue asks for.
- **Adding a CLI entry point or bridge import.** These are Skill-tool skills by design. Wiring a `valor-*` entry point or a bridge import would be net-new surface area the issue explicitly rules out.
- **Expanding the persona beyond a brief subsection.** The capability is secondary; a large persona expansion would mis-weight it relative to the core SDLC loop.
- **Adding `RENAMED_REMOVALS` entries.** These are new skill names, not renames or moves between `skills/` and `skills-global/`, so no cleanup entry is needed.

## Risks

### Risk 1: Persona-mention test couples too tightly to exact wording
**Impact:** If the test asserts on a long literal phrase, a future minor persona reword breaks the test for no real reason.
**Mitigation:** Assert only on the stable token `imagine-agent` (the skill name, which won't change without a deliberate rename), not on a full sentence.

### Risk 2: Feature-doc index row placed non-alphabetically
**Impact:** Breaks the alphabetical convention of `docs/features/README.md` and may trip a docs-hygiene reflection.
**Mitigation:** Insert between "Image Vision Support" (line 78) and "Intake Classifier" (line 79) — verified during freshness check.

## Race Conditions

No race conditions identified — all deliverables are static files (markdown + a synchronous pytest module). No async operations, shared mutable state, or cross-process data flows are introduced.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The persona subsection, feature doc, index row, and structural tests are all completable within this plan. The two `SKILL.md` files and their `references/` are deliberately read-only context (already shipped), not deferred work.

## Update System

No update system changes required. Both skills live under `.claude/skills-global/`, and `scripts/update/hardlinks.py::sync_claude_dirs()` already hardlinks **every** directory under `skills-global/` into `~/.claude/skills/` on each machine via `_sync_skills(...)`. Neither `imagine-agent` nor `build-agent` appears in `PROJECT_ONLY_SKILLS`, so both are synced automatically. No `RENAMED_REMOVALS` entry is needed — these are new names, not renames or moves between the two skill directories. This plan adds **zero** lines to `hardlinks.py` or the `/update` skill. The feature doc will state this automatic-sync fact explicitly so future readers don't add redundant wiring.

## Agent Integration

No agent integration wiring required — `/imagine-agent` and `/build-agent` are **Skill-tool skills**. Valor reaches them through the Claude Code Skill tool (the same surface as every other `/do-*` global skill), not through a CLI entry point or a direct bridge import. Specifically:

- **No `pyproject.toml [project.scripts]` entry** — there is no `valor-imagine-agent` or `valor-build-agent` binary, and none is needed.
- **No bridge import** — `bridge/telegram_bridge.py` does not (and should not) import either skill; invocation is via the Skill tool at runtime.
- **No new MCP server / `.mcp.json` change** — the skills use already-allowed tools (Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent) declared in their own frontmatter.
- **Integration verification is structural:** `tests/unit/test_cma_skills_well_formed.py` confirms the skills are discoverable (directories + valid `SKILL.md` frontmatter), which is exactly what the Skill tool needs to surface them. The feature doc records this Skill-tool-only integration path explicitly.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/imagine-build-agent-cma.md` describing the paired `/imagine-agent` + `/build-agent` CMA workflow, the `build-sheet.json` handoff contract, its non-SDLC client-facing place in the system, the automatic global-skill sync, and the Skill-tool-only Agent Integration path.
- [ ] Add an entry to the `docs/features/README.md` "Features" table, alphabetically between "Image Vision Support" and "Intake Classifier" (link text "Imagine/Build Agent (CMA)", status "Shipped").

### External Documentation Site
- This repo has no Sphinx/MkDocs/RTD site for these features — no external docs site update needed.

### Inline Documentation
- [ ] The new test file `tests/unit/test_cma_skills_well_formed.py` gets a module docstring and descriptive assertion messages (matching the `test_skills_exist.py` style). No other inline docs apply (no new runtime code).

## Success Criteria

- [ ] `config/personas/segments/tools.md` contains a `### Managed Agent Creation (CMA)` subsection that mentions `imagine-agent`, `build-agent`, and Claude Managed Agents (CMA), framed as a secondary non-SDLC capability.
- [ ] `docs/features/imagine-build-agent-cma.md` exists, describes the paired workflow, states the Update System auto-sync fact, and notes Agent Integration is via the Skill tool (no CLI entry point, no bridge import).
- [ ] `docs/features/README.md` contains an alphabetically-placed entry for the CMA skills.
- [ ] `tests/unit/test_cma_skills_well_formed.py` exists and verifies: both skill dirs exist, both `SKILL.md` files have `name:` / `description:` / `allowed-tools:` frontmatter, build-agent's two reference files exist, and `tools.md` contains `imagine-agent`.
- [ ] `pytest tests/unit/test_cma_skills_well_formed.py` passes.
- [ ] The two `SKILL.md` files are unchanged by this PR (`git diff` shows no modifications under `.claude/skills-global/imagine-agent/` or `.claude/skills-global/build-agent/`).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. Given the Small appetite, a single builder handles all four deliverables, followed by one validator.

### Team Members

- **Builder (cma-integration)**
  - Name: cma-builder
  - Role: Add the persona subsection, write the feature doc + index row, and create the structural test file.
  - Agent Type: builder
  - Resume: true

- **Validator (cma-integration)**
  - Name: cma-validator
  - Role: Verify all success criteria, confirm the SKILL.md files are untouched, and run the new test.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using Tier 1 core agents only: `builder` and `validator`. No specialists needed for additive markdown + a structural test.

## Step by Step Tasks

### 1. Add persona subsection
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: tests/unit/test_cma_skills_well_formed.py (the persona-mention test)
- **Assigned To**: cma-builder
- **Agent Type**: builder
- **Parallel**: true
- Insert a `### Managed Agent Creation (CMA)` subsection in `config/personas/segments/tools.md` under "Tools I Use" (after "Local Python Tools", before "Communication").
- One short paragraph: what CMA is, when to use it, and the two skills (`/imagine-agent`, `/build-agent`). Frame as a secondary, non-SDLC capability. Must include the literal token `imagine-agent`.

### 2. Write feature doc and index row
- **Task ID**: build-docs
- **Depends On**: none
- **Validates**: manual review (no test asserts on doc content)
- **Assigned To**: cma-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `docs/features/imagine-build-agent-cma.md` (model on `docs/features/email-google-workspace-skills.md`): paired workflow, build-sheet handoff, non-SDLC place in system, automatic sync via `sync_claude_dirs()`, Skill-tool-only Agent Integration.
- Add an alphabetically-placed row to the `docs/features/README.md` "Features" table between "Image Vision Support" and "Intake Classifier".

### 3. Create structural test file
- **Task ID**: build-tests
- **Depends On**: build-persona
- **Validates**: tests/unit/test_cma_skills_well_formed.py (create)
- **Assigned To**: cma-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_cma_skills_well_formed.py` modeled on `tests/unit/test_skills_exist.py`.
- Parametrize over `["imagine-agent", "build-agent"]`: directory exists, `SKILL.md` exists, frontmatter has `name:`, `description:`, `allowed-tools:`.
- Add a test asserting `build-agent/references/cma-primitives.md` and `references/build-sheet.md` exist.
- Add a test asserting `config/personas/segments/tools.md` contains `imagine-agent`.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-persona, build-docs, build-tests
- **Assigned To**: cma-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_cma_skills_well_formed.py` — must pass.
- Confirm `git diff` shows no changes under `.claude/skills-global/imagine-agent/` or `.claude/skills-global/build-agent/`.
- Verify each Success Criteria checkbox.
- Run `python -m ruff check tests/unit/test_cma_skills_well_formed.py` and `python -m ruff format --check`.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New tests pass | `pytest tests/unit/test_cma_skills_well_formed.py -q` | exit code 0 |
| Persona mention present | `grep -c 'imagine-agent' config/personas/segments/tools.md` | output > 0 |
| Feature doc exists | `test -f docs/features/imagine-build-agent-cma.md` | exit code 0 |
| Index row present | `grep -c 'imagine-build-agent-cma' docs/features/README.md` | output > 0 |
| SKILL.md files untouched | `git diff --quiet HEAD -- .claude/skills-global/imagine-agent .claude/skills-global/build-agent` | exit code 0 |
| Lint clean | `python -m ruff check tests/unit/test_cma_skills_well_formed.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_cma_skills_well_formed.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
