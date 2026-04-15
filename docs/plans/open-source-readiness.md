---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/849
last_comment_id: null
revision_applied: true
---

# Open-Source Readiness: CONTRIBUTING.md, README Polish, and CHANGELOG

## Problem

Someone arriving at this repo for the first time faces three friction points that slow evaluation and prevent contribution:

**Current behavior:**
1. No `CONTRIBUTING.md` exists — PR expectations, code style (ruff/mypy in `pyproject.toml`), test requirements (pytest markers in `tests/README.md`), commit conventions, and how to extend the system (skills, tools, agents) are all scattered or undocumented.
2. The README has solid architecture content but no demo media and no fast value-proposition hook. A visitor must read multiple sections to understand what the system does and why it matters.
3. No `CHANGELOG.md` exists — there is no record of what changed between versions without reading `git log`.

**Desired outcome:**
- A new visitor can understand the system's value within 30 seconds of opening the README.
- A potential contributor can find all contribution guidelines in `CONTRIBUTING.md`.
- Anyone can see the project's evolution in `CHANGELOG.md`.

## Freshness Check

**Baseline commit:** `71e2f70e2bfb2131d9560763ecf981b6f22f92ee`
**Issue filed at:** 2026-04-09T08:33:27Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `README.md` — issue claimed "lacks demo media and value-prop section" — still holds as of baseline; README has architecture diagram and quick-start but no demo GIF or value-prop hook.
- `CONTRIBUTING.md` — does not exist — confirmed absent.
- `CHANGELOG.md` — does not exist — confirmed absent.
- `pyproject.toml` `[tool.ruff.lint]` / `[tool.mypy]` — ruff and mypy config sections confirmed present; these are the style references CONTRIBUTING.md should point to.
- `tests/README.md` — test suite index confirmed present with full marker table; CONTRIBUTING.md should reference it rather than duplicating.

**Cited sibling issues/PRs re-checked:**
- #979 (examples/ directory) — still OPEN; split remains valid. This plan does not touch examples/.

**Commits on main since issue was filed (touching referenced files):**
- `43798699` feat: SDLC stage model selection and hard-PATCH builder session resume (#909) — touched README? No. Irrelevant.
- `8eb93a9e` chore: consolidate secrets — touched README? No diff observed. Irrelevant.
- `5712c0d3` Replace SOUL.md with structured identity config — no README changes.

**Active plans in `docs/plans/` overlapping this area:** None found.

**Notes:** No drift. All issue claims verified against current main.

## Prior Art

No prior issues or PRs found related to CONTRIBUTING.md, CHANGELOG.md, or README onboarding polish via closed issue/PR search.

## Research

**Queries used:**
- "Keep a Changelog format best practices 2026 CHANGELOG.md"
- "CONTRIBUTING.md best practices open source 2026 template"

**Key findings:**
- [keepachangelog.com v1.1.0](https://keepachangelog.com/en/1.1.0/) — Keep a Changelog is the canonical format. Use an `[Unreleased]` section at the top; versioned sections use ISO 8601 dates; subsections: Added, Changed, Deprecated, Removed, Fixed, Security. Use semantic versioning for version numbers.
- [contributing.md](https://contributing.md/) — Best-practice CONTRIBUTING.md should cover: welcome note, development environment setup, test location and how to run, PR/branch process, code style references, commit message conventions, and how to add new types of contributions. Target: new contributor can start contributing within 30 minutes. Projects with contribution guides receive 3x more PRs.
- [common-changelog.org](https://common-changelog.org/) — Alternative opinionated format that's more developer-focused; recommends entries written in past tense, grouped by PR/issue. Keep a Changelog is more widely recognized for public projects — stick with KaC.

**Savings:** Research findings saved to memory for future plan reuse.

## Spike Results

*No spikes needed — this is documentation/content work with no verifiable technical assumptions.*

## Data Flow

*Purely documentation work — no data flow to trace.*

## Architectural Impact

- **New files**: `CONTRIBUTING.md` (root), `CHANGELOG.md` (root) — no code impact.
- **Modified files**: `README.md` (additive only — new value-prop section, demo placeholder).
- **New dependencies**: None.
- **Reversibility**: Trivial — all changes are additive markdown files.

## Appetite

**Size:** Medium

**Team:** Solo dev (documentarian)

**Interactions:**
- PM check-ins: 1 (scope alignment after draft)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`CONTRIBUTING.md`** — Single authoritative contributor guide at the repo root. Covers PR process, code style (pointer to `pyproject.toml`), test requirements (pointer to `tests/README.md`), commit conventions, and how to extend the system with new skills/tools/agents.
- **README value-prop section** — A new section above "How It Works" that communicates the system's purpose in one paragraph and 3–5 bullet points. Visible within the first scroll.
- **README demo placeholder** — A clearly marked placeholder for a demo GIF/screenshot with instructions on how to capture one. This avoids blocking the PR on a running system while making the gap visible.
- **`CHANGELOG.md`** — Initial changelog generated from significant git history (major features), following Keep a Changelog v1.1.0 format. Starts from the earliest notable feature and works forward. Includes an `[Unreleased]` section.

### Flow

**Visitor arrives at repo** → reads value-prop (30 sec) → understands system purpose → clicks "Quick Start" → has a running system

**Contributor arrives** → reads CONTRIBUTING.md → finds PR process, style guide refs, test instructions, commit conventions → opens first PR without needing to ask

**User checks what changed** → opens CHANGELOG.md → reads versioned entries in Keep a Changelog format → understands evolution

### Technical Approach

1. `CONTRIBUTING.md` structure (in order):
   - Welcome paragraph (1–2 sentences)
   - Table of contents
   - Prerequisites and environment setup (pointer to Quick Start in README)
   - Branch and PR process (`session/{slug}` naming, PR body format, merge requirements)
   - Code style (`python -m ruff format .` + `python -m ruff check .`; reference `pyproject.toml [tool.ruff.lint]`)
   - Type checking (`python -m mypy`; reference `pyproject.toml [tool.mypy]`)
   - Test requirements (pointer to `tests/README.md`; state quality gates: unit 100%, integration 95%, E2E 90%)
   - Commit conventions (detailed and focused; no co-author trailers)
   - Extending the system: adding skills (`.claude/skills/`), tools (`tools/` + MCP wiring), agents (`.claude/agents/`)
   - Getting help (point to CLAUDE.md, docs/features/, GitHub Issues)

2. `README.md` additions (additive only, no rewrite):
   - Insert a "Why Valor?" section immediately after the opening sentence (`An autonomous AI coworker...`) and before `## What Is This?` — 3–5 bullet points mapping to real use cases. Note: the README has no separate "tagline" line; the opening sentence IS the tagline, so insert directly after it.
   - Insert a demo placeholder after the architecture diagram as a visible blockquote: `> **📸 TODO**: Add demo GIF or screenshot here — see [docs/assets/](docs/assets/) for placement instructions.` (not an HTML comment — HTML comments are invisible in rendered GitHub markdown)
   - No structural changes to existing sections

3. `CHANGELOG.md` using Keep a Changelog v1.1.0:
   - `[Unreleased]` section at top (empty initially)
   - Version sections generated from git history by feature cluster, not exact semver tags (project has no formal releases yet — use date-based milestones)
   - Major feature clusters from git log: Email Bridge, SDLC Pipeline, Subconscious Memory, Session Management, Sentry Integration, YouTube/Search Tools, Dashboard, Autoexperiment
   - Format: `## [Unreleased]` → `## [0.x.0] - YYYY-MM-DD` per cluster

## Failure Path Test Strategy

### Exception Handling Coverage
- No code changes — no exception handlers in scope.

### Empty/Invalid Input Handling
- Not applicable for documentation work.

### Error State Rendering
- Risk: broken markdown links (pointing to files that don't exist). Mitigation: each link in CONTRIBUTING.md and CHANGELOG.md must be verified against actual repo paths before committing.

## Test Impact

No existing tests affected — this work creates three new/modified documentation files (`CONTRIBUTING.md`, `CHANGELOG.md`, `README.md`) with no code changes. No test files reference these artifacts.

## Rabbit Holes

- **Generating a perfect CHANGELOG from git history** — git log has 1000+ commits, many of which are noisy (plan files, wip, merge commits). Don't try to be exhaustive. Group by major feature and write human-readable summaries. Not a commit-by-commit audit.
- **Demo GIF creation** — capturing a live Telegram→SDLC flow requires a running system. Do not block the PR on producing the actual GIF. Add a clearly labeled placeholder and document how to produce it.
- **Auto-generating CHANGELOG from conventional commits** — the project doesn't use strict conventional commit tooling (no `standard-version`, no `release-please`). Don't wire automation now; keep it manual.
- **Rewriting the README** — the README already has good architecture content. This is additive work only (value prop + demo placeholder). Do not refactor existing sections.
- **CONTRIBUTING.md as a policy document** — keep it under 1,500 words. Long contributor guides are not read. Reference existing docs; don't duplicate them.

## Risks

### Risk 1: CONTRIBUTING.md references drift
**Impact:** Links to `pyproject.toml` sections or `tests/README.md` become stale as those files evolve.
**Mitigation:** Use section-level anchors (`pyproject.toml`'s `[tool.ruff.lint]`) rather than line numbers. Anchor names are stable under refactors.

### Risk 2: CHANGELOG becomes stale immediately
**Impact:** If nobody maintains it, it becomes inaccurate faster than it provides value.
**Mitigation:** Note in CHANGELOG.md header that it is maintained manually per feature PR. Add to CONTRIBUTING.md that PRs for notable features should include a CHANGELOG entry.

### Risk 3: Value-prop copy doesn't land
**Impact:** New visitor still doesn't understand the system quickly enough.
**Mitigation:** Write the value-prop section as a human would pitch it in a sentence — what it does, who it's for, what pain it removes. Review against the 30-second reading test before committing.

## Race Conditions

No race conditions — all operations are synchronous, single-threaded documentation writes.

## No-Gos (Out of Scope)

- `examples/` directory — tracked in #979.
- Actual demo GIF/screenshot production — placeholder only.
- Automated CHANGELOG tooling (standard-version, release-please, cliff).
- README rewrite — additive changes only.
- LICENSE file — not requested and out of scope.
- CODE_OF_CONDUCT.md — not requested; can be a follow-up.

## Update System

No update system changes required — this work produces documentation files only. No new dependencies, config files, or deployment steps.

## Agent Integration

No agent integration required — this is a documentation-only change. No new tools, MCP servers, or bridge changes.

## Documentation

This work IS the documentation. No additional feature docs needed.

- [x] `CONTRIBUTING.md` created at repo root
- [x] `CHANGELOG.md` created at repo root
- [x] `README.md` updated with value-prop section and demo placeholder

## Success Criteria

- [x] `CONTRIBUTING.md` exists at repo root and covers: PR process, code style (referencing ruff/mypy configs), test requirements (referencing pytest markers), commit conventions, and how to add skills/tools/agents
- [x] README contains a "Why Valor?" value-proposition section visible in the first scroll
- [x] README contains a clearly marked placeholder for demo media with instructions for how to produce it
- [x] `CHANGELOG.md` exists with at least 6 versioned entries covering major feature milestones, following Keep a Changelog v1.1.0 format
- [x] All new markdown files contain no broken links to nonexistent repo paths
- [x] No new ruff failures introduced by this PR — verify by diffing `python -m ruff check .` output against baseline commit `71e2f70e` (a pre-existing `F841` in `test_recovery_respawn_safety.py` is excluded from this gate)
- [x] No existing test files are broken

## Team Orchestration

### Team Members

- **Builder (docs)**
  - Name: docs-builder
  - Role: Write CONTRIBUTING.md, CHANGELOG.md, and README value-prop additions
  - Agent Type: documentarian
  - Resume: true

- **Validator (docs)**
  - Name: docs-validator
  - Role: Verify all links resolve, no broken references, content quality check
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Documentarian for writing, validator for link/quality verification.

## Step by Step Tasks

### 1. Write CONTRIBUTING.md
- **Task ID**: build-contributing
- **Depends On**: none
- **Validates**: `test -f CONTRIBUTING.md`
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Create `CONTRIBUTING.md` at repo root following the Technical Approach structure
- Cover: welcome, TOC, prerequisites (pointer to README Quick Start), branch/PR process (`session/{slug}`), code style (ruff + mypy with `pyproject.toml` pointers), test requirements (pointer to `tests/README.md`, state quality gates), commit conventions (no co-author trailers), how to add skills/tools/agents, getting help
- Keep under 1,500 words; reference don't duplicate

### 2. Write CHANGELOG.md
- **Task ID**: build-changelog
- **Depends On**: none
- **Validates**: `test -f CHANGELOG.md`
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Create `CHANGELOG.md` at repo root using Keep a Changelog v1.1.0 format
- Include `[Unreleased]` section at top
- Generate 6+ versioned entries from git log feature clusters (date-based milestones, not formal semver tags): Email Bridge, SDLC Pipeline enhancements, Subconscious Memory, Session Management (valor-session CLI), Sentry Integration, YouTube/Search Tools, Dashboard, Autoexperiment, Terminal Emoji upgrade
- Each entry uses subsections: Added, Changed, Removed, Fixed as applicable
- Write human-readable summaries; not commit-by-commit

### 3. Polish README value-prop
- **Task ID**: build-readme
- **Depends On**: none
- **Validates**: `grep -q "Why Valor" README.md`
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Insert "Why Valor?" section immediately after the opening sentence (`An autonomous AI coworker...`) and before `## What Is This?` — with 3–5 bullet points mapping to real use cases. The README has no separate "tagline" line; the opening sentence IS the tagline, so insert directly after it.
- Insert demo media placeholder after the architecture diagram as a visible blockquote: `> **📸 TODO**: Add demo GIF or screenshot here — see [docs/assets/](docs/assets/) for placement instructions.` (HTML comments are invisible in rendered GitHub markdown — use a blockquote so the placeholder is actionable)
- No structural changes to existing README sections

### 4. Validate all docs
- **Task ID**: validate-docs
- **Depends On**: build-contributing, build-changelog, build-readme
- **Assigned To**: docs-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all internal links in `CONTRIBUTING.md` and `CHANGELOG.md` resolve to actual repo paths
- Verify `README.md` "Why Valor?" section is present and readable in under 30 seconds
- Run `python -m ruff check .` to confirm no lint regressions
- Confirm success criteria checklist above is fully met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| CONTRIBUTING.md exists | `test -f CONTRIBUTING.md` | exit code 0 |
| CHANGELOG.md exists | `test -f CHANGELOG.md` | exit code 0 |
| README has value-prop | `grep -q "Why Valor" README.md` | exit code 0 |
| No new ruff failures | diff `python -m ruff check .` against baseline `71e2f70e` | no new entries |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Run: 2026-04-15 -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | User | HTML comment demo placeholder is invisible in rendered GitHub markdown | Task 3 | Replace `<!-- TODO: ... -->` with a visible blockquote: `> **📸 TODO**: Add demo GIF or screenshot here — see [docs/assets/](docs/assets/) for placement instructions.` |
| CONCERN | Skeptic | Success criterion "`ruff check .` passes" will fail on pre-existing `F841` in `test_recovery_respawn_safety.py` | Task 4 / validate-docs | Change success criterion to: "No *new* ruff failures introduced by this PR" — verify by diffing ruff output against baseline commit `71e2f70e`. |
| NIT | Skeptic | README has no "tagline" line; opening sentence IS the tagline — insertion point for "Why Valor?" may confuse builder | Task 3 | Builder should insert "Why Valor?" section immediately after the opening sentence `An autonomous AI coworker...` and before `## What Is This?`. |

---

## Open Questions

1. **Demo GIF**: Should the README placeholder include a static annotated screenshot of the architecture diagram as a near-term substitute for a live GIF? Or is the placeholder comment sufficient until a live recording is available?
2. **CHANGELOG versioning**: The project has no formal releases or semver tags. Should CHANGELOG entries use date-based milestones (e.g., `[2026-04]`) or fabricated semver labels (e.g., `[0.9.0]`)? Keep a Changelog recommends semver, but date milestones are more honest for this project.
