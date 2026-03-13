---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-13
tracking: https://github.com/tomcounsell/ai/issues/375
---

# Fix Cross-Repo gh Resolution via GH_REPO Environment Variable

## Problem

When a message like "SDLC issue 193" arrives from the Popoto Telegram chat, the agent resolves it against `tomcounsell/ai` instead of `tomcounsell/popoto`. PR #378 claimed to fix this but only added `--repo` text instructions to SKILL.md files and wrote static tests that check the text exists. The actual runtime behavior is broken.

**Current behavior:**
1. Tom sends "SDLC issue 193" in the "Dev: Popoto" Telegram group
2. Bridge resolves project as "popoto" (correct)
3. `sdk_client.py` classifies as "sdlc", sets `working_dir=ai/` (correct for orchestrator pattern)
4. `sdk_client.py` appends `GITHUB: tomcounsell/popoto` to the enriched message text (correct)
5. Claude Code subprocess starts with `cwd=ai/` and NO `GH_REPO` env var
6. The LLM agent reads SKILL.md, which says "extract the GITHUB: line and use --repo"
7. The LLM may or may not remember to add `--repo tomcounsell/popoto` to every `gh` command
8. `gh issue view 193` resolves against the ai repo (issue #193 in ai, not popoto)
9. Worker reports the wrong issue as "already merged and complete"

**Desired outcome:**
All `gh` commands in the subprocess automatically target the correct repo without relying on the LLM to parse text instructions and remember `--repo` on every invocation.

## Prior Art

- **PR #378**: "Fix Observer SDLC pipeline: cross-repo gh, classification race, typed outcome merge" -- Added `--repo` instructions to 6 SKILL.md files and 2 static tests that grep for the text `--repo` in those files. Did not change any Python code for cross-repo resolution. Merged 2026-03-12.
- **Plan: fix_cross_repo_build.md** (issue #249): Fixed `/do-build` cross-repo dispatch for worktrees and `pipeline_state.py`. Added `resolve_repo_root()` utility. Different problem (build-time repo root), same symptom category.

## Data Flow

1. **Entry point**: Telegram message "SDLC issue 193" in "Dev: Popoto" group
2. **bridge/routing.py `find_project_for_chat()`**: Maps chat title to popoto project config from `config/projects.json`
3. **agent/sdk_client.py `get_agent_response_sdk()`**: Calls `classify_work_request("SDLC issue 193")` which returns "sdlc". Since `project_working_dir != AI_REPO_ROOT`, sets `working_dir = AI_REPO_ROOT` (orchestrator pattern).
4. **Enrichment (line 1058-1067)**: Appends `\nGITHUB: tomcounsell/popoto` to enriched message text. This is a plain text line -- nothing programmatic reads it.
5. **ValorAgent creation (line 1096-1104)**: Creates agent with `working_dir=ai/`. Does NOT receive or pass GitHub repo info. The `_create_options()` method builds an `env` dict that goes to the subprocess, but `GH_REPO` is never set.
6. **Claude Code subprocess**: Starts with `cwd=ai/`, `GH_REPO` not set. All `gh` commands default to the ai repo.
7. **SKILL.md instructions**: Tell the LLM to "extract the GITHUB: line" and "use --repo". This is unreliable -- the LLM must parse text and remember to add `--repo` to every single `gh` command.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #378 (cross-repo part) | Added `--repo` text to SKILL.md files. Added static tests checking text exists in markdown. | The fix relies on the LLM reading markdown instructions and voluntarily adding `--repo` to every `gh` command. This is fundamentally unreliable -- the LLM can forget, misparse, or skip the flag. The tests only verify the text exists in markdown files, not that `gh` commands actually target the right repo at runtime. |

**Root cause pattern:** The fix was applied at the wrong layer. Cross-repo resolution was implemented as LLM instructions (soft, unreliable) instead of environment configuration (hard, deterministic). The `gh` CLI supports `GH_REPO` as an environment variable that automatically applies to all commands -- this was the correct mechanism but was never used.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `ValorAgent.__init__` gets an optional `gh_repo` parameter
- **Coupling**: No increase -- the env var is set in the same place other env vars are already set
- **Data ownership**: No change
- **Reversibility**: Trivial -- remove the env var injection

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The fix is 3 lines of production code (pass `gh_repo` to ValorAgent, set `GH_REPO` in env dict) plus behavioral tests.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **`GH_REPO` env var injection**: When `classification == "sdlc"` and the project has a non-ai GitHub repo, set `GH_REPO=org/repo` in the subprocess environment. This makes ALL `gh` commands in the subprocess automatically target the correct repo.
- **Behavioral tests**: Test the full path from project config to env var injection, verifying that `GH_REPO` is set correctly for cross-repo cases and NOT set for ai-repo cases.
- **SKILL.md cleanup**: The `--repo` / `REPO_FLAG` instructions in SKILL.md files become a belt-and-suspenders safety net rather than the primary mechanism.

### Flow

**Message arrives** → `find_project_for_chat()` resolves project → `classify_work_request()` returns "sdlc" → `get_agent_response_sdk()` extracts `github.org`/`github.repo` from project config → passes `gh_repo="tomcounsell/popoto"` to `ValorAgent` → `_create_options()` sets `env["GH_REPO"] = "tomcounsell/popoto"` → Claude Code subprocess inherits `GH_REPO` → all `gh` commands automatically target popoto

### Technical Approach

1. **`get_agent_response_sdk()` (line ~1096)**: Extract `github_org`/`github_repo` from project config when `classification == "sdlc"` and `project_working_dir != AI_REPO_ROOT`. Pass as `gh_repo=f"{github_org}/{github_repo}"` to `ValorAgent()`.

2. **`ValorAgent.__init__`**: Accept optional `gh_repo: str | None = None` parameter.

3. **`ValorAgent._create_options()`**: If `self.gh_repo` is set, add `env["GH_REPO"] = self.gh_repo`.

4. **Tests**: Write behavioral tests that:
   - Verify `GH_REPO` is set in the env dict when a cross-repo project is used
   - Verify `GH_REPO` is NOT set when working on the ai repo itself
   - Verify `GH_REPO` is NOT set for PM mode projects
   - Verify `GH_REPO` is NOT set when classification is not "sdlc"
   - Verify the enriched message still contains `GITHUB:` line (belt-and-suspenders)

5. **Delete static tests**: Remove `TestCrossRepoGhResolution` from `tests/unit/test_observer.py` (the tests that just grep for `--repo` in markdown files). Replace with the behavioral tests above.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- the change is env var injection, which cannot throw.

### Empty/Invalid Input Handling
- [ ] Test that `gh_repo=None` (default) does NOT set `GH_REPO` in env
- [ ] Test that `gh_repo=""` (empty) does NOT set `GH_REPO` in env
- [ ] Test that projects without `github` config key do not crash

### Error State Rendering
- Not applicable -- this is backend env var injection with no user-visible output.

## Rabbit Holes

- **Modifying SKILL.md further**: The SKILL.md `--repo` instructions should remain as documentation/safety-net, but don't invest time making them "more explicit". The env var is the real fix.
- **Testing that the LLM correctly uses `--repo`**: This would require E2E agent testing. The env var approach makes this unnecessary.
- **Changing the orchestrator pattern**: The pattern of running the worker in `cwd=ai/` for cross-project SDLC is correct. Don't change it -- just fix the env var.

## Risks

### Risk 1: GH_REPO leaks into ai-repo work
**Impact:** `gh` commands for ai-repo SDLC work would target a wrong repo
**Mitigation:** Only set `GH_REPO` when `classification == "sdlc"` AND `project_working_dir != AI_REPO_ROOT`. The condition already exists at line 1058 and is well-tested.

### Risk 2: GH_REPO conflicts with explicit --repo flags
**Impact:** If SKILL.md instructions AND the env var both specify a repo, they could conflict
**Mitigation:** `--repo` flag takes precedence over `GH_REPO` per gh CLI docs. Since both would point to the same repo, no conflict. If SKILL.md has `--repo` from PR #378 text and env var is set, they agree.

## Race Conditions

No race conditions identified. The env var is set synchronously during `_create_options()` before the subprocess starts.

## No-Gos (Out of Scope)

- Changing how `classify_work_request()` works (it correctly classifies cross-repo messages)
- Modifying the orchestrator pattern (worker running in `cwd=ai/` is correct by design)
- E2E testing that actually runs `gh` against GitHub API (unit tests with env var verification are sufficient)
- Fixing bugs 1 and 2 from issue #375 (classification race, typed outcome merge) -- those were fixed in PR #378

## Update System

No update system changes required -- this is a small code change to `agent/sdk_client.py` with no new dependencies or config files.

## Agent Integration

No agent integration required -- this is a change to how the bridge spawns the agent subprocess. No MCP servers, tools, or bridge message handling is affected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-first-routing.md` to document `GH_REPO` env var injection (replace the section about SKILL.md `--repo` instructions as the primary mechanism)

### Inline Documentation
- [ ] Docstring on `gh_repo` parameter in `ValorAgent.__init__`
- [ ] Comment in `_create_options()` explaining why `GH_REPO` is set

## Success Criteria

- [ ] `GH_REPO` is set in subprocess env when processing a cross-repo SDLC request
- [ ] `GH_REPO` is NOT set for ai-repo SDLC requests, PM mode, or non-SDLC classifications
- [ ] Behavioral tests verify the env var injection path (not just markdown text existence)
- [ ] Static `TestCrossRepoGhResolution` tests replaced with behavioral tests
- [ ] All existing tests pass
- [ ] Sending "issue 193" to Dev: Popoto would result in `GH_REPO=tomcounsell/popoto` in the subprocess env

## Team Orchestration

### Team Members

- **Builder (gh-repo-env)**
  - Name: env-builder
  - Role: Add `gh_repo` parameter to `ValorAgent`, set `GH_REPO` in env, pass from `get_agent_response_sdk`
  - Agent Type: builder
  - Resume: true

- **Test Engineer (gh-repo-tests)**
  - Name: test-engineer
  - Role: Write behavioral tests, delete static tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator (gh-repo-verify)**
  - Name: env-validator
  - Role: Verify env var injection, run full test suite
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add GH_REPO env var injection to ValorAgent
- **Task ID**: build-env-injection
- **Depends On**: none
- **Assigned To**: env-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `gh_repo: str | None = None` parameter to `ValorAgent.__init__` (line 511)
- Store as `self.gh_repo = gh_repo`
- In `_create_options()` (line 655 area), add: `if self.gh_repo: env["GH_REPO"] = self.gh_repo`
- In `get_agent_response_sdk()` (line 1096 area), when creating `ValorAgent`, pass `gh_repo=f"{github_org}/{github_repo}"` when both are non-empty and `classification == "sdlc"` and `project_working_dir != AI_REPO_ROOT`

### 2. Write behavioral tests
- **Task ID**: build-tests
- **Depends On**: none
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_cross_repo_gh_resolution.py` with:
  - `test_gh_repo_set_for_cross_repo_sdlc`: Mock `ValorAgent._create_options()`, verify `GH_REPO` in env when popoto project + sdlc classification
  - `test_gh_repo_not_set_for_ai_repo`: Verify `GH_REPO` NOT in env when ai project
  - `test_gh_repo_not_set_for_pm_mode`: Verify `GH_REPO` NOT in env for PM projects
  - `test_gh_repo_not_set_for_question_classification`: Verify `GH_REPO` NOT in env when classification != "sdlc"
  - `test_enriched_message_contains_github_line`: Verify enriched message text still has `GITHUB: org/repo`
  - `test_gh_repo_not_set_when_github_config_missing`: Verify graceful handling when project lacks `github` key
- Delete `TestCrossRepoGhResolution` class from `tests/unit/test_observer.py` (lines 1114-1158)

### 3. Update documentation
- **Task ID**: update-docs
- **Depends On**: build-env-injection
- **Assigned To**: env-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/sdlc-first-routing.md` cross-repo section to document `GH_REPO` env var as primary mechanism

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-env-injection, build-tests, update-docs
- **Assigned To**: env-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_cross_repo_gh_resolution.py -v`
- Run `pytest tests/unit/test_observer.py -v` (verify old static tests removed cleanly)
- Run `pytest tests/ -x -q` (full suite)
- Run `python -m ruff check . && python -m ruff format --check .`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New tests exist | `pytest tests/unit/test_cross_repo_gh_resolution.py -v` | exit code 0 |
| Old static tests removed | `grep -c "TestCrossRepoGhResolution" tests/unit/test_observer.py` | exit code 1 |
| GH_REPO in sdk_client | `grep -c "GH_REPO" agent/sdk_client.py` | output > 0 |
