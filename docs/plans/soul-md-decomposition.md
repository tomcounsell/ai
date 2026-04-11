---
slug: soul-md-decomposition
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/852
last_comment_id:
---

# Replace SOUL.md with Structured Identity Config + Composable Prompt Segments

## Problem

`config/SOUL.md` (501 lines) is a monolithic markdown file conflating five distinct concerns: structured identity data, behavioral instructions, system configuration, philosophy, and persona-specific rules. The persona system (`config/personas/`) was built to address this but only partially succeeded -- `_base.md` (475 lines) is ~90% duplicated content copied from SOUL.md, and SOUL.md remains the ultimate fallback in three code paths.

**Current behavior:**

- Identity data (name, email, timezone, org) is embedded as a markdown table in SOUL.md lines 5-12, not queryable by code
- `_base.md` duplicates ~90% of SOUL.md content because it was created by copying SOUL.md
- `load_persona_prompt()` falls back to SOUL.md when overlays are missing (line 565)
- `load_system_prompt()` has an independent SOUL.md fallback (line 595)
- `load_pm_system_prompt()` has an independent SOUL.md fallback (line 635)
- Adding a new identity field requires editing markdown and hoping the prompt parses it
- Deploying a second instance with different identity requires forking SOUL.md entirely
- 34 files across the codebase reference SOUL.md or SOUL_PATH

**Desired outcome:**

- Identity data lives in structured JSON, queryable by code and overridable per-instance
- Behavioral content is split into composable segments by concern
- SOUL.md is retired -- the persona system is the primary path with no fallback
- `_base.md` duplication is eliminated -- shared content lives in one canonical location
- New instance deployment needs only an identity config override, not hundreds of lines of markdown

## Freshness Check

**Baseline commit:** `41f57151`
**Issue filed at:** 2026-04-09T08:34:21Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `config/SOUL.md` (501 lines) -- identity table at lines 5-12 -- still holds
- `config/personas/_base.md` (475 lines) -- duplication claim -- still holds
- `agent/sdk_client.py:343` -- `SOUL_PATH` constant -- still holds
- `agent/sdk_client.py:346` -- `PERSONAS_BASE_DIR` constant -- still holds
- `agent/sdk_client.py:350` -- `PERSONAS_OVERLAY_DIR` constant -- still holds
- `agent/sdk_client.py:512` -- `load_persona_prompt()` definition -- still holds
- `agent/sdk_client.py:561-568` -- SOUL.md fallback in `load_persona_prompt()` -- still holds
- `agent/sdk_client.py:593-596` -- SOUL.md fallback in `load_system_prompt()` -- still holds
- `agent/sdk_client.py:633-636` -- SOUL.md fallback in `load_pm_system_prompt()` -- still holds

**Cited sibling issues/PRs re-checked:**
- #395 -- closed 2026-03-20, multi-persona system shipped. The persona base+overlay architecture is in place.
- #432 -- closed 2026-03-20, made persona name configurable via projects.json
- #368 -- closed 2026-03-13, TELOS-style principal context adopted

**Commits on main since issue was filed (touching referenced files):**
- `41f57151` Unified analytics system -- irrelevant to persona/SOUL
- `9cee8e0f` Summarizer fallback: agent self-summary -- touches summarizer but not SOUL.md references
- `8a755bc6` Fix session isolation bypass -- irrelevant
- `d24dd07f` Add CLI harness abstraction -- irrelevant
- `d0478bf8` Restrict PM sessions to read-only Bash allowlist -- irrelevant

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** All 6 commits since filing are irrelevant to this plan. The issue's claims about line counts, fallback locations, and duplication percentages are all verified accurate against current main.

## Prior Art

- **Issue #395** (closed): Multi-persona system: PM as communication layer, specialized agents via SDK -- Shipped the base+overlay persona architecture. Created `_base.md` by copying SOUL.md with minor edits. This is the direct predecessor; the current issue continues the work by eliminating the duplication and retiring the monolithic fallback.
- **Issue #432** (closed): Make chief persona name configurable via system config -- Made the persona name configurable through `projects.json`. Relevant because identity fields are already partially externalized.
- **PR #448** (merged): Make persona name configurable via layered soul files -- Implementation of #432. Established the pattern of using `projects.json` for persona metadata.
- **PR #164** (merged): Enforce SDLC pipeline at Agent SDK level: strip SOUL.md workflow content -- Previously stripped SDLC workflow content from SOUL.md, moving it to `sdk_client.py`. Demonstrates the pattern of extracting concerns from SOUL.md into proper code.
- **Issue #368** (closed): TELOS-Style Principal Context -- Added `PRINCIPAL.md` as a separate config file for strategic context. Same decomposition pattern this plan follows.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #448 (from #395) | Created `config/personas/_base.md` + overlay architecture | Copied SOUL.md content into `_base.md` wholesale rather than decomposing it. Left SOUL.md as fallback, creating a maintenance burden of keeping two ~500-line files in sync. |
| PR #164 | Stripped SDLC workflow from SOUL.md into `sdk_client.py` constants | Only addressed one concern (SDLC workflow). Left identity data, behavioral instructions, machine config, and philosophy all still in the monolith. |

**Root cause pattern:** Both prior efforts extracted one concern at a time but left the rest of the monolith intact. The duplication was created by copying rather than decomposing. This plan addresses all remaining concerns in one pass.

## Data Flow

### Prompt Assembly Flow (Current)

1. **Entry point**: `load_persona_prompt(persona)` called from `load_system_prompt()` or `load_pm_system_prompt()`
2. **Base load**: Reads `config/personas/_base.md` (475 lines, mostly duplicated from SOUL.md)
3. **Overlay resolution**: `_resolve_overlay_path(persona)` checks `~/Desktop/Valor/personas/` then `config/personas/`
4. **Concatenation**: `base_content + "\n\n---\n\n" + overlay_content`
5. **Fallback**: If overlay missing, reads `config/SOUL.md` directly (bypassing base entirely)
6. **Wrapping**: `load_system_prompt()` prepends `WORKER_RULES`, appends principal context and completion criteria

### Prompt Assembly Flow (Proposed)

1. **Entry point**: `load_persona_prompt(persona)` called (same API)
2. **Identity load**: Read `config/identity.json`, merge with `~/Desktop/Valor/identity.json` overrides
3. **Segment assembly**: Load composable segments from `config/personas/segments/` based on persona manifest
4. **Template rendering**: Inject identity fields into segment templates
5. **Overlay resolution**: Same private overlay path as today (unchanged)
6. **Concatenation**: Assembled segments + persona overlay (no monolithic base)
7. **No SOUL.md fallback**: If files are missing, raise FileNotFoundError (fail loudly, not silently)

## Architectural Impact

- **New dependencies**: None. Uses stdlib `json` for identity config, existing `pathlib` for file operations.
- **Interface changes**: `load_persona_prompt()` signature unchanged. Internal implementation changes. New `load_identity()` function added. `SOUL_PATH` constant removed.
- **Coupling**: Decreases coupling. Identity data is no longer embedded in prompt text but loaded from structured config, making it queryable by code. Segments are independent and composable.
- **Data ownership**: Identity data ownership moves from a markdown table to a JSON config file. Behavioral content ownership moves from two duplicated monoliths to composable segments.
- **Reversibility**: Medium. The change touches 34+ files. However, the public API (`load_persona_prompt()`, `load_system_prompt()`, `load_pm_system_prompt()`) is unchanged, so callers need no modification.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (validate segment decomposition boundaries)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All required files and config paths already exist.

## Solution

### Key Elements

- **Identity config** (`config/identity.json`): Structured key-value data -- name, email, timezone, org, machine description. Per-instance overrides via `~/Desktop/Valor/identity.json`.
- **Three composable segments** (`config/personas/segments/`): All behavioral content decomposed into exactly three segments, each a standalone markdown file:
  1. **`identity.md`** -- Who I Am, values, voice, communication style, response summarization. For a text agent, voice *is* identity -- these are inseparable.
  2. **`work-patterns.md`** -- Autonomy rules, escalation policy, decision heuristics, philosophy-as-actionable-rules. Each pattern includes an inline "why" annotation explaining the reasoning. Abstract frameworks are translated into concrete behavioral guidance.
  3. **`tools.md`** -- MCP servers, development tools, browser automation, CLI references, local Python tools, machine self-management commands. Unified reference catalog.
- **Segment manifest**: A simple JSON list defining segment order. All personas render all segments -- no segment skipping. The manifest is universal.
- **F-string substitution**: Identity fields injected via Python f-strings (e.g., `f"name: {identity['name']}"`). Simple and sufficient for current needs. A future issue will add cached/compiled per-context variable support.
- **Output format**: Assembled segments render to JSON or markdown for context priming.
- **Retired SOUL.md**: All fallback references removed. SOUL.md deleted. `_base.md` replaced by segment assembly.

### Flow

**load_persona_prompt("developer")** call -> Load `config/identity.json` + private overrides -> Assemble all 3 segments from `config/personas/segments/*.md` per manifest -> Inject identity fields via f-string substitution -> Append persona overlay from `~/Desktop/Valor/personas/developer.md` -> Return combined prompt

### Technical Approach

**Phase 1: Create identity config and segments (additive)**

- Create `config/identity.json` with fields extracted from SOUL.md lines 5-12
- Create `config/personas/segments/` directory with content decomposed from `_base.md` and SOUL.md into 3 segments:
  - `identity.md` -- Who I Am, As an AI Coworker, Professional Standards, Values, Communication Style, Response Summarization, When I Reach Out, What I Do Not Do. Voice and identity are unified because for a text agent, communication style *is* identity.
  - `work-patterns.md` -- How I Work, Autonomous Execution, When I Escalate, What I Do NOT Ask About, Decision Heuristic, Escape Hatch, Subconscious Memory, Intentional Memory, Agentic Engineering Philosophy (translated to actionable patterns with inline "why" annotations). Merges behavioral rules with philosophy-as-action and memory guidance.
  - `tools.md` -- MCP Servers, Development Tools, Browser Automation, Local Python Tools (SMS, Telegram, Link Analysis), Machine Self-Management (restart, health, logs), Daily Operations. Unified reference catalog including former machine/tools/escape-hatch content.
- Create `config/personas/segments/manifest.json` defining the universal segment order (all personas render all segments)
- Use f-string substitution for identity field injection in segment templates

**Phase 2: Update load functions (swap implementation)**

- Add `load_identity()` function to `agent/sdk_client.py` that reads `config/identity.json` with `~/Desktop/Valor/identity.json` override merge
- Update `load_persona_prompt()` to assemble segments instead of reading `_base.md`
- Inject identity fields into the assembled prompt (template substitution for name, email, timezone, org)
- Remove all three SOUL.md fallback branches from `load_persona_prompt()`, `load_system_prompt()`, `load_pm_system_prompt()`
- Remove `SOUL_PATH` constant
- Update `_resolve_overlay_path()` -- no behavioral changes needed, overlay resolution stays the same

**Phase 3: Retire SOUL.md and update references (cleanup)**

- Delete `config/SOUL.md`
- Delete `config/personas/_base.md` (replaced by segment assembly)
- Update all 34 files referencing SOUL.md:
  - **Code files** (must change):
    - `agent/sdk_client.py` -- remove `SOUL_PATH`, remove fallback branches
    - `tools/doc_impact_finder.py` -- update `SOUL.md` reference in important files list
    - `.claude/hooks/post_tool_use.py` -- remove SOUL.md modification reminder hook
  - **Test files** (must change):
    - `tests/unit/test_persona_loading.py` -- update to test segment-based loading, remove `test_soul_md_still_exists`, update fallback tests
    - `tests/unit/test_sdk_client.py` -- update any SOUL.md assertions
    - `tests/unit/test_sdk_permissions.py` -- update if referencing SOUL.md
    - `tests/unit/test_sdk_client_sdlc.py` -- update if referencing SOUL.md
    - `tests/unit/test_pm_channels.py` -- update if referencing SOUL.md
    - `tests/integration/test_doc_impact_finder_sdk.py` -- update important files list
  - **Documentation files** (update references):
    - `CLAUDE.md` -- update See Also table to reference identity config
    - `README.md` -- update directory tree and reference table
    - `config/README.md` -- update SOUL.md section description
    - `.claude/README.md` -- update reference to SOUL.md
    - `docs/features/personas.md` -- major update: document new segment architecture
    - `docs/features/pm-channels.md` -- update prompt composition description
    - `docs/features/completion-tracking.md` -- update prompt composition example
    - `docs/features/sdlc-enforcement.md` -- update SOUL.md cleanup section
    - `docs/features/sdlc-first-routing.md` -- update system prompt references
    - `docs/features/semantic-doc-impact-finder.md` -- update config reference
    - `docs/features/qa-conversational-humility.md` -- update config reference
    - `docs/features/telegram-messaging.md` -- update persona reference
    - `docs/features/README.md` -- update persona feature description
    - `docs/guides/setup.md` -- update architecture diagram
    - `docs/guides/valor-evolution-summary.md` -- update persona evolution note
    - `docs/guides/valor-name-references.md` -- update SOUL.md references throughout
    - `docs/guides/cursor-lessons.md` -- update SOUL.md references
    - `docs/guides/summarizer-output-audit.md` -- update SOUL.md citation
  - **Skill/command files** (update references):
    - `.claude/commands/prime.md` -- update SOUL.md reference in architecture
    - `.claude/skills/prime/SKILL.md` -- update SOUL.md reference
    - `.claude/skills/do-docs/SKILL.md` -- update important files list
    - `.claude/skills/new-valor-skill/SKILL.md` -- update SOUL.md persona reference
  - **Plan files** (historical, minimal updates):
    - `docs/plans/dennett_thinking_skills.md` -- historical reference, leave as-is
    - `docs/plans/pm-skips-critique-and-review.md` -- historical reference, leave as-is
    - `docs/plans/hardcoded_paths_docs_skills.md` -- historical reference, leave as-is
  - **Config files:**
    - `config/projects.example.json` -- update `personas.*.soul` paths to reference segments or identity config

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `load_persona_prompt()` currently catches `FileNotFoundError` for base -- verify segment loading raises loudly for missing segments
- [ ] `load_identity()` must handle missing `identity.json` gracefully with sensible defaults
- [ ] `load_identity()` must handle malformed JSON (corrupted private override file)
- [ ] Verify private identity override merge does not silently swallow parse errors

### Empty/Invalid Input Handling
- [ ] Test `load_identity()` with empty JSON file (`{}`)
- [ ] Test segment assembly when segment directory exists but is empty
- [ ] Test identity template substitution with missing fields (should use defaults, not crash)
- [ ] Test `load_persona_prompt()` with empty string persona name

### Error State Rendering
- [ ] Verify error messages from missing segments include the segment path for debugging
- [ ] Verify error messages from malformed identity JSON include the parse error

## Test Impact

- [ ] `tests/unit/test_persona_loading.py::TestLoadPersonaPrompt::test_missing_overlay_falls_back_to_soul` -- REPLACE: rewrite to assert FileNotFoundError instead of SOUL.md fallback
- [ ] `tests/unit/test_persona_loading.py::TestLoadPersonaPrompt::test_nonexistent_persona_falls_back` -- UPDATE: may still fall back to developer, verify behavior
- [ ] `tests/unit/test_persona_loading.py::TestLoadSystemPromptIntegration::test_soul_md_still_exists` -- DELETE: SOUL.md will no longer exist
- [ ] `tests/unit/test_persona_loading.py::TestLoadPersonaPrompt::test_base_file_exists_in_repo` -- REPLACE: test segment files exist instead of `_base.md`
- [ ] `tests/unit/test_persona_loading.py::TestLoadPersonaPrompt::test_separator_between_base_and_overlay` -- UPDATE: verify segments are assembled with separators
- [ ] `tests/unit/test_sdk_client.py` -- UPDATE: remove any `SOUL_PATH` import references
- [ ] `tests/unit/test_pm_channels.py` -- UPDATE: remove SOUL.md fallback assertions if any
- [ ] `tests/integration/test_doc_impact_finder_sdk.py` -- UPDATE: change `config/SOUL.md` to new identity config path in important files

## Rabbit Holes

- **Dynamic persona switching mid-session** -- changing persona after session start requires session-level state management changes. Out of scope per issue.
- **Jinja2 or advanced templating** -- f-string substitution is sufficient for identity field injection. A future issue will add cached/compiled per-context variable support. Do not introduce a template engine dependency now.
- **YAML instead of JSON for identity config** -- JSON is simpler, has no external dependency, and is already used throughout the project (`.mcp.json`, `projects.json`). Do not debate format.
- **Migrating private overlay content** -- the private overlay files in `~/Desktop/Valor/personas/` are out of scope. They continue to work as-is; only the base/shared content changes.
- **Automated content migration tool** -- do not build a tool to programmatically split SOUL.md. The decomposition is a one-time manual operation guided by clear section boundaries.

## Risks

### Risk 1: Prompt Regression -- Assembled Segments Produce Different Behavior
**Impact:** Agent behavior changes subtly due to different content ordering, missing sections, or template substitution artifacts.
**Mitigation:** Create a test that assembles the full developer prompt from segments and compares total line count and key phrase presence against a snapshot. Run prompt diff before/after in PR review.

### Risk 2: Private Override Merge Conflicts
**Impact:** If `~/Desktop/Valor/identity.json` has keys that conflict with or duplicate `config/identity.json`, the merge logic could produce unexpected results.
**Mitigation:** Simple shallow merge: private values override repo values for matching keys. No deep merge. Document the merge behavior.

### Risk 3: Summarizer Voice Drift
**Impact:** The `SUMMARIZER_SYSTEM_PROMPT` in `bridge/summarizer.py` was written to match SOUL.md's communication style. If communication.md diverges, the summarizer voice may drift.
**Mitigation:** The summarizer prompt is self-contained (does not import from SOUL.md). Add a cross-reference comment in `communication.md` pointing to the summarizer. Include a test that verifies key communication phrases exist in both.

## Race Conditions

No race conditions identified -- all operations are synchronous file reads at agent startup time. Prompt assembly happens once per session and is not concurrent.

## No-Gos (Out of Scope)

- Dynamic persona switching mid-session
- Migrating private overlay files (`~/Desktop/Valor/personas/*.md`)
- Template engine dependency (Jinja2 etc.)
- YAML format for identity config
- `projects.json` schema breaking changes (backward-compatible additions only)
- Changing the `PersonaType` enum values
- Modifying the `SUMMARIZER_SYSTEM_PROMPT` content (just ensuring alignment)

## Update System

The update script (`scripts/remote-update.sh`) and update skill need minor changes:

- The identity config file `config/identity.json` ships with the repo (shared defaults). No update script changes needed for this file.
- Private identity overrides (`~/Desktop/Valor/identity.json`) are machine-local and iCloud-synced -- the update system does not manage these, same as existing private overlays.
- The deletion of `config/SOUL.md` is handled by `git pull` during updates -- no special migration needed.
- The new `config/personas/segments/` directory is created by the repo and pulled automatically.
- **No update system changes required** -- this feature uses existing file distribution patterns (repo files via git pull, private files via iCloud).

## Agent Integration

No agent integration required -- this is an internal change to prompt assembly. The persona system is not exposed through MCP servers or tools. The agent consumes the assembled prompt as its system prompt; it does not invoke persona-loading functions directly.

The only indirect agent integration concern is the `.claude/hooks/post_tool_use.py` SOUL.md modification reminder, which will be removed since SOUL.md no longer exists. The replacement segments should have their own modification reminders added to the hook (specifically for `communication.md` -> check `bridge/summarizer.py` alignment).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/personas.md` to document the new segment-based architecture, identity config, and removal of SOUL.md fallback
- [ ] Update `docs/features/README.md` index table entry for personas
- [ ] Update `docs/features/pm-channels.md` prompt composition description

### Inline Documentation
- [ ] Docstrings on `load_identity()`, updated `load_persona_prompt()` docstring
- [ ] Comments in `config/identity.json` (via `_doc` field, following `projects.example.json` pattern)
- [ ] Cross-reference comment in `config/personas/segments/communication.md` pointing to `bridge/summarizer.py`

## Success Criteria

- [ ] `config/identity.json` exists with name, email, timezone, org fields
- [ ] `load_identity()` function in `agent/sdk_client.py` loads identity from JSON
- [ ] Per-instance identity overrides work via `~/Desktop/Valor/identity.json`
- [ ] Shared behavioral content is split into exactly 3 composable segments (identity.md, work-patterns.md, tools.md) in `config/personas/segments/`
- [ ] `_base.md` is deleted -- no more duplication
- [ ] `config/SOUL.md` is deleted
- [ ] `SOUL_PATH` constant is removed from `agent/sdk_client.py`
- [ ] All three SOUL.md fallback branches are removed
- [ ] `load_persona_prompt()` assembles prompts from segments + overlay
- [ ] `load_system_prompt()` and `load_pm_system_prompt()` no longer reference SOUL.md
- [ ] All 34 file references are updated or removed
- [ ] `PersonaType` enum is unchanged
- [ ] `projects.example.json` updated to reflect new architecture
- [ ] `tests/unit/test_persona_loading.py` updated for new loading logic
- [ ] Existing persona behavior preserved -- equivalent system prompts produced
- [ ] `bridge/summarizer.py` alignment verified (communication style preserved)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (identity-config)**
  - Name: identity-builder
  - Role: Create identity.json, load_identity() function, and private override merge
  - Agent Type: builder
  - Resume: true

- **Builder (segments)**
  - Name: segment-builder
  - Role: Decompose _base.md into composable segments, create manifest, update load_persona_prompt()
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete SOUL.md, remove fallbacks, update all 34 file references
  - Agent Type: builder
  - Resume: true

- **Validator (prompt-equivalence)**
  - Name: prompt-validator
  - Role: Verify assembled prompts are equivalent to previous output
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Update test_persona_loading.py and add new tests for segment loading and identity config
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update personas.md, pm-channels.md, README references
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create Identity Config
- **Task ID**: build-identity
- **Depends On**: none
- **Validates**: tests/unit/test_persona_loading.py (update), tests/unit/test_identity_config.py (create)
- **Assigned To**: identity-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/identity.json` with fields from SOUL.md lines 5-12: name, email, timezone, google_workspace, organization
- Add `_doc` field explaining override behavior
- Add `load_identity()` function to `agent/sdk_client.py` that reads repo JSON and merges `~/Desktop/Valor/identity.json` overrides (shallow merge, private wins)
- Write unit tests for `load_identity()`: default load, private override merge, missing private file, malformed JSON handling

### 2. Decompose Base into Segments
- **Task ID**: build-segments
- **Depends On**: none
- **Validates**: tests/unit/test_persona_loading.py (update)
- **Assigned To**: segment-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/personas/segments/` directory
- Split `_base.md` and SOUL.md content into exactly 3 segment files:
  - `identity.md` -- Who I Am, As an AI Coworker, Professional Standards, Values, Communication Style, Response Summarization, When I Reach Out, What I Do Not Do. Add cross-reference comment to `bridge/summarizer.py` for voice alignment.
  - `work-patterns.md` -- How I Work, Autonomous Execution, When I Escalate, What I Do NOT Ask About, Decision Heuristic, Escape Hatch for Genuine Uncertainty, Subconscious Memory, Intentional Memory, Agentic Engineering Philosophy (rewritten as actionable patterns with inline "why" annotations preserving the inspirational framing as rationale).
  - `tools.md` -- MCP Servers, Development Tools, Browser Automation, Local Python Tools (SMS, Telegram, Link Analysis), Machine Self-Management (restart, health, logs, after reboot), Daily Operations, Issue Polling, Job Scheduler.
- Create `config/personas/segments/manifest.json` listing universal segment order (all personas render all 3 segments, no skipping)
- Update `load_persona_prompt()` to read segments from manifest and assemble, replacing `_base.md` read
- Inject identity fields from `load_identity()` into segments via f-string substitution

### 3. Remove SOUL.md Fallbacks
- **Task ID**: build-remove-fallbacks
- **Depends On**: build-identity, build-segments
- **Validates**: tests/unit/test_persona_loading.py (update)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `SOUL_PATH` constant from `agent/sdk_client.py`
- Remove SOUL.md fallback branch from `load_persona_prompt()` (lines 561-569)
- Remove SOUL.md fallback from `load_system_prompt()` (lines 593-598)
- Remove SOUL.md fallback from `load_pm_system_prompt()` (lines 633-638)
- When overlay is missing, raise `FileNotFoundError` with clear error message
- Delete `config/SOUL.md`
- Delete `config/personas/_base.md`

### 4. Update All References
- **Task ID**: build-update-refs
- **Depends On**: build-remove-fallbacks
- **Validates**: `grep -rn "SOUL\.md\|SOUL_PATH" --include="*.py" --include="*.md" . | grep -v docs/plans/ | grep -v node_modules/`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all 34 files listed in the Solution section above
- For code files: remove imports, constants, and fallback logic
- For doc files: replace "SOUL.md" references with "identity config" or "persona segments" as appropriate
- For historical plan docs: leave as-is (they document what was true at plan time)
- Update `config/projects.example.json` persona entries to reference segments
- Update `.claude/hooks/post_tool_use.py`: replace SOUL.md reminder with `communication.md` -> summarizer alignment reminder

### 5. Validate Prompt Equivalence
- **Task ID**: validate-equivalence
- **Depends On**: build-update-refs
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Assemble the full developer prompt from segments and compare key content presence against known phrases from the original SOUL.md/base
- Verify the developer prompt contains: "Valor Engels", "social justice", "Direct communication", "YOLO mode" (or equivalent from developer overlay), escalation policy
- Verify the PM prompt assembles correctly (base segments + PM overlay)
- Verify the teammate prompt assembles correctly (base segments + teammate overlay)
- Verify no SOUL.md references remain in Python code: `grep -rn "SOUL" --include="*.py" .`

### 6. Update Tests
- **Task ID**: build-tests
- **Depends On**: build-update-refs
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `tests/unit/test_persona_loading.py`:
  - Replace `test_soul_md_still_exists` with segment existence tests
  - Replace `test_missing_overlay_falls_back_to_soul` with test asserting FileNotFoundError
  - Update `test_base_file_exists_in_repo` to test segment files exist
  - Add tests for `load_identity()` function
  - Add tests for segment assembly ordering
  - Add test for identity field injection in assembled prompt
- Update other test files that import `SOUL_PATH` or reference SOUL.md

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/personas.md` with new segment architecture
- Update `docs/features/pm-channels.md` prompt composition description
- Update `CLAUDE.md` See Also table
- Update `README.md` directory tree and reference table
- Update `config/README.md`

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format check: `python -m ruff format --check .`
- Verify no SOUL.md references remain: `grep -rn "SOUL\.md" . --include="*.py" --include="*.md" | grep -v docs/plans/ | grep -v node_modules/`
- Verify `config/SOUL.md` does not exist
- Verify `config/personas/_base.md` does not exist
- Verify `config/identity.json` exists and is valid JSON
- Verify `config/personas/segments/` contains all expected segment files

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| SOUL.md deleted | `test ! -f config/SOUL.md` | exit code 0 |
| _base.md deleted | `test ! -f config/personas/_base.md` | exit code 0 |
| Identity config exists | `python -c "import json; json.load(open('config/identity.json'))"` | exit code 0 |
| 3 segments exist | `ls config/personas/segments/{identity,work-patterns,tools}.md` | exit code 0 |
| No SOUL.md in Python | `grep -rn "SOUL\.md" --include="*.py" .` | exit code 1 |
| No SOUL_PATH in code | `grep -rn "SOUL_PATH" --include="*.py" .` | exit code 1 |
| Persona loads | `python -c "from agent.sdk_client import load_persona_prompt; p = load_persona_prompt('developer'); assert 'Valor' in p"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-11. -->

### Blockers

#### 1. Ghost Segment Reference: `communication.md` Does Not Exist in Plan
- **Severity**: BLOCKER
- **Critics**: Skeptic, Operator
- **Location**: Risks (Risk 3, line 264), Agent Integration (line 295), Documentation (line 307), Task 4 (line 427)
- **Finding**: The plan references `communication.md` as a segment file in four locations, but the Solution section defines exactly three segments: `identity.md`, `work-patterns.md`, and `tools.md`. Communication style is merged into `identity.md` per the resolved Open Question #1. The four `communication.md` references are stale from an earlier draft.
- **Suggestion**: Replace all `communication.md` references with `identity.md` (since communication/voice is part of the identity segment). Update the summarizer cross-reference comment and hook reminder to point to `config/personas/segments/identity.md`.
- **Implementation Note**: Four occurrences to fix: (1) Risk 3 mitigation line 265: `communication.md` -> `identity.md`; (2) Agent Integration line 295: `communication.md` -> `identity.md`; (3) Documentation line 307: `config/personas/segments/communication.md` -> `config/personas/segments/identity.md`; (4) Task 4 line 427: `communication.md` -> `identity.md`. The FILE_REMINDERS dict in `.claude/hooks/post_tool_use.py` should key on `identity.md` with the same summarizer alignment reminder.

### Concerns

#### 2. SOUL.md-Only Sections May Be Lost in Decomposition
- **Severity**: CONCERN
- **Critics**: Skeptic, Archaeologist
- **Location**: Solution, Task 2 (line 384-398)
- **Finding**: SOUL.md contains `Autonomous Execution` (line 49) and `Full System Access` (line 60) sections that do NOT exist in `_base.md`. These are the most operationally critical permissions content (YOLO mode, unrestricted git, unrestricted bash). The plan says it decomposes from `_base.md` AND `SOUL.md`, but the segment descriptions in Task 2 don't explicitly list where these two sections land. If the builder only reads `_base.md` as the primary source, these sections could be silently dropped.
- **Suggestion**: Explicitly annotate in Task 2 which segment absorbs `Autonomous Execution` and `Full System Access`. Based on the content, `work-patterns.md` (autonomy rules) is the natural home.
- **Implementation Note**: `Autonomous Execution` (SOUL.md lines 49-58) and `Full System Access` (SOUL.md lines 60-88) need explicit placement. The builder should use SOUL.md as the authoritative source for these sections, not `_base.md` (which lacks them). Suggested placement: `work-patterns.md` alongside the autonomy rules and escalation policy, since "Full System Access" is the permission grant that enables autonomous execution.

#### 3. F-String Substitution in Markdown is Fragile
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Solution, Technical Approach Phase 1 (line 158), Task 2 (line 398)
- **Finding**: The plan specifies f-string substitution for identity field injection (e.g., `f"name: {identity['name']}"`). However, the segment markdown files will contain curly braces in code blocks (e.g., Python dict literals, JSON examples, bash variable expansions like `${PID}`). A naive `str.format(**identity)` or f-string `eval` would crash on these, and even `str.format_map()` with a defaultdict would silently swallow legitimate template variables.
- **Suggestion**: Use a simple string replacement approach (e.g., `text.replace("{name}", identity["name"])` for each field) rather than Python's format machinery. This avoids collisions with curly braces in markdown code blocks entirely.
- **Implementation Note**: The segment files contain code blocks with curly braces. For example, `_base.md` line 397 has `request_human_input(...)` with no braces, but SOUL.md's escape hatch section (lines 472-487) has dict-like `options=["OAuth 2.0", ...]`. The safe approach is `text.replace("{name}", val)` for each identity field, or use a non-brace delimiter like `{{name}}` or `$name`. The builder must test with actual segment content, not toy strings.

#### 4. Tasks 5-8 Missing Validates Field
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Step by Step Tasks, Tasks 5-8 (lines 429-481)
- **Finding**: Tasks 1-4 all have a `Validates` field specifying how to verify the task completed correctly. Tasks 5 (Validate Prompt Equivalence), 6 (Update Tests), 7 (Documentation), and 8 (Final Validation) lack this field. While Task 8 is itself a validation task, Tasks 5-7 should have explicit validation commands so the builder knows when they're done.
- **Suggestion**: Add `Validates` fields to Tasks 5-7. Task 5 could validate with `grep -rn "SOUL" --include="*.py" .` returning nothing. Task 6 with `pytest tests/unit/test_persona_loading.py -x -q`. Task 7 with a doc existence check.
- **Implementation Note**: Task 5 already describes its validation steps inline (grep for SOUL references) but lacks the structured `Validates` field. Task 6 should validate with `pytest tests/unit/test_persona_loading.py tests/unit/test_identity_config.py -x -q`. Task 7 should validate with `test -f docs/features/personas.md && grep -q "segment" docs/features/personas.md`.

### Nits

#### 5. Wisdom Section Disposition Unspecified
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Solution, Task 2 segment descriptions
- **Finding**: SOUL.md has a `## Wisdom` section (lines 452-468) with quotes. The segment decomposition doesn't mention where this content goes. It's not behavioral, not identity, and not tools. It could be dropped entirely or appended to `work-patterns.md` as motivational framing.
- **Suggestion**: Explicitly state that Wisdom quotes are dropped (simplest) or appended to `work-patterns.md` as "why" annotations.

#### 6. Test File `tests/unit/test_identity_config.py` Listed as "(create)" but Not in Test Impact
- **Severity**: NIT
- **Critics**: Operator
- **Location**: Task 1 Validates field (line 375) vs Test Impact section (line 234)
- **Finding**: Task 1 references `tests/unit/test_identity_config.py (create)` in its Validates field, but the Test Impact section only lists modifications to existing files. A new test file is not a "test impact" per se, but it could confuse the builder about which test files are new vs updated.
- **Suggestion**: Add a note in Test Impact clarifying that `test_identity_config.py` is a new file (not an update to an existing one).

#### 7. `_base.md` Has Sections Not in SOUL.md (Memory Sections)
- **Severity**: NIT
- **Critics**: Archaeologist
- **Location**: Task 2, segment decomposition
- **Finding**: `_base.md` contains `Subconscious Memory` and `Intentional Memory` sections (lines 424-475) that do NOT exist in SOUL.md. The plan correctly places these in `work-patterns.md`, but the Freshness Check says `_base.md` is "~90% duplicated from SOUL.md" which slightly understates the divergence. Not a functional issue, just an accuracy note.
- **Suggestion**: No action needed -- the plan's Task 2 already handles these sections. Just noting the duplication percentage is approximate.

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-8 sequential, no gaps |
| Dependencies valid | PASS | All Depends On references (build-identity, build-segments, etc.) point to valid Task IDs |
| File paths exist | PASS | 35 of 35 referenced files exist on disk |
| Prerequisites met | PASS | No prerequisites declared (plan states "no external dependencies") |
| Cross-references | PASS | Success criteria map to tasks; No-Gos do not appear in Solution as planned work |

## Verdict

**READY TO BUILD (with concerns)** -- No BLOCKERs remain after the `communication.md` ghost reference is fixed (a simple text replacement in the plan itself). Three CONCERN findings exist with Implementation Notes. A revision pass will embed these notes before build.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Operator | Ghost `communication.md` references (4 locations) | Plan revision | Replace with `identity.md` in lines 265, 295, 307, 427 |
| CONCERN | Skeptic, Archaeologist | SOUL.md-only sections (Autonomous Execution, Full System Access) may be lost | Task 2 annotation | Explicitly place in `work-patterns.md`; use SOUL.md as authoritative source |
| CONCERN | Adversary | F-string substitution fragile with curly braces in markdown | Task 2 implementation | Use `text.replace("{name}", val)` per field, not `str.format()` |
| CONCERN | Operator | Tasks 5-8 missing Validates fields | Plan revision | Add structured validation commands to Tasks 5-7 |
| NIT | Simplifier | Wisdom section disposition unspecified | Task 2 | Drop or append to work-patterns.md |
| NIT | Operator | test_identity_config.py listed as create but not in Test Impact | Test Impact section | Add clarifying note |
| NIT | Archaeologist | _base.md divergence slightly understated (~90% claim) | N/A | Informational only |

---

## Open Questions

*All resolved — decisions documented below.*

### Resolved

1. **Segment granularity**: ✅ **3 segments** — Identity (name, role, values, voice/communication), Work Patterns (autonomy, escalation, philosophy-as-actionable-rules with inline "why"), Tools (unified reference catalog including machine self-management). Rationale: for a text agent, voice *is* identity (merge communication into identity); philosophy without behavioral mapping adds no value (merge into work-patterns as "why" annotations); machine access is discoverable by the agent, operational commands belong with tools (merge machine into tools).

2. **Identity field injection mechanism**: ✅ **F-strings** — Python f-string substitution is good enough for now. A future issue will be drafted to add cached/compiled per-context variable support as dynamism needs grow.

3. **Manifest format**: ✅ **Universal manifest, no skipping** — Simple JSON list of filenames. All personas render all segments because segments are context priming and no persona should skip context. Output renders to JSON or markdown.

### Future Work

- [ ] Draft GitHub issue: Cached/compiled per-context variable substitution for persona segments (upgrade from f-strings when dynamic context needs grow)
