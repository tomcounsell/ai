---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-17
tracking: https://github.com/tomcounsell/ai/issues/121
---

# Build do-test Command and Skill

## Problem

The `/do-test` command is currently a stub that just suggests running `pytest tests/ -v` manually. There's no intelligent test orchestration — no way to run targeted tests based on changed files, no parallel test type execution, no aggregated reporting, and no integration with the `/do-build` workflow.

**Current behavior:**
Running `/do-test` prints a message telling you to run `pytest tests/ -v` yourself. No test selection, no coverage, no structured reporting.

**Desired outcome:**
`/do-test` is a full skill that intelligently runs the right tests, dispatches test types to parallel subagents, and returns a structured pass/fail summary usable by both humans and the `/do-build` orchestrator.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on which test types to include v1 vs later)
- Review rounds: 1

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies. The test infrastructure (`pytest`, `ruff`, `black`) and test directories already exist.

## Solution

### Key Elements

- **SKILL.md**: Full skill definition replacing the current stub, with dispatch logic
- **Command file**: Updated `do-test.md` command that invokes the skill properly
- **Test dispatcher**: Logic to determine which test suites to run based on arguments and changed files
- **Parallel execution**: Independent test types run as parallel subagents via Task tool
- **Result aggregation**: Structured pass/fail summary with counts and failure details

### Flow

**User invokes /do-test** → Skill parses arguments → Detects changed files (if no args) → Dispatches test suites to parallel agents → Agents run tests → Results aggregated → Summary reported

Argument patterns:
- `/do-test` — run all tests + lint checks
- `/do-test unit` — run only unit tests + lint
- `/do-test tests/unit/test_bridge_logic.py` — run specific file + lint
- `/do-test --changed` — run tests related to changed files only (auto-detects branch comparison base)
- `/do-test --no-lint` — run tests without ruff/black checks
- `/do-test unit --no-lint` — combinable flags

### Technical Approach

- The skill is a **Claude Code slash command** (`.claude/commands/do-test.md`) backed by a **skill definition** (`.claude/skills/do-test/SKILL.md`)
- The skill orchestrates using `Task` tool calls with `test-engineer` and `validator` subagent types
- **CWD-relative execution**: The skill runs all commands relative to the current working directory. When invoked from `/do-build`, the SDK client sets `cwd` to the worktree path (`.worktrees/{slug}/`) via `ClaudeAgentOptions`. When invoked directly in a Claude Code session, CWD is the main repo. No worktree detection needed — it just works in whatever directory it's given.
- Changed-file detection uses `git diff --name-only` to find modified files, then maps them to relevant test files using path conventions (`tests/unit/test_{module}.py`, `tests/tools/test_{tool}.py`, etc.)
- **Smart branch comparison for `--changed`**: Auto-detect the comparison base. If on a `session/*` or non-main branch, diff against `main` (all changes in the feature branch). If on `main`, diff against `HEAD~1` (last commit only). This handles both `/do-build` worktree contexts and direct Claude Code sessions without flags.
- Test types map to existing directory structure:
  - `unit` → `tests/unit/`
  - `integration` → `tests/integration/`
  - `e2e` → `tests/e2e/`
  - `performance` → `tests/performance/`
  - `tools` → `tests/tools/`
  - `all` → `tests/` (root-level tests + all subdirs)
- **Lint checks run by default** with `--no-lint` opt-out flag. The `/do-build` Definition of Done requires both tests and quality checks, so a single `/do-test` invocation must cover both. Users running quick iterations can skip lint with `--no-lint`.
- Each agent returns structured output: pass/fail, test count, failure details
- The orchestrator aggregates results into a summary table

## Rabbit Holes

- **Coverage reporting** — Tempting to add `--cov` and coverage thresholds, but adds complexity for marginal v1 value. Defer to v2.
- **Security/penetration testing** — The issue mentions it, but there's no existing security test infrastructure. Building that from scratch is a separate project.
- **Scalability/load testing** — Same as above — no existing load test framework. The `tests/performance/` dir has benchmarks but not load tests. Defer.
- **AI judge integration** — The `tests/ai_judge/` module exists but integrating it into the dispatch flow adds complexity. Keep it as a manually-invoked test for now.
- **Test result caching** — Skipping unchanged tests sounds smart but introduces cache invalidation complexity.

## Risks

### Risk 1: Subagent overhead for small test runs
**Impact:** Running 5 parallel agents for a quick `pytest tests/unit/` adds unnecessary latency
**Mitigation:** Smart dispatch — if a specific test type or file is requested, run it directly in a single agent instead of fanning out

### Risk 2: Changed-file detection misses relevant tests
**Impact:** `/do-test --changed` might miss tests that should run due to indirect dependencies
**Mitigation:** Conservative mapping — when unsure, include the broader test directory. Users can always run `/do-test` for full coverage.

## No-Gos (Out of Scope)

- No penetration testing or security scanning (separate project)
- No load/stress testing (separate project)
- No coverage reporting or thresholds
- No test result persistence or historical tracking
- No automatic test generation
- No CI/CD integration (this is a local dev tool)

## Update System

No update system changes required — this is a skill definition change (`.claude/` directory files only). The skill files are part of the repo and propagate via `git pull` during normal updates.

## Agent Integration

No agent integration required — this is a Claude Code slash command invoked by the human operator (or by `/do-build`). It doesn't need MCP server exposure or bridge integration. The agent already has access to `Task` tool for subagent dispatch and `Bash` for running pytest/ruff/black.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/do-test.md` describing the skill, arguments, and test type mapping
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Clear comments in SKILL.md explaining dispatch logic and argument parsing

## Success Criteria

- [ ] `/do-test` runs all tests and returns a structured pass/fail summary
- [ ] `/do-test unit` runs only unit tests
- [ ] `/do-test tests/path/to/file.py` runs a specific test file
- [ ] `/do-test --changed` detects changed files and runs relevant tests
- [ ] Lint checks (ruff, black) included in the test run
- [ ] Multiple test types run in parallel via subagents
- [ ] Results aggregated into a clear summary table
- [ ] `/do-build` can invoke `/do-test` as part of its workflow
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill)**
  - Name: skill-builder
  - Role: Implement the SKILL.md and command file
  - Agent Type: builder
  - Resume: true

- **Validator (skill)**
  - Name: skill-validator
  - Role: Verify the skill works end-to-end
  - Agent Type: validator
  - Resume: true

- **Builder (docs)**
  - Name: docs-builder
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria met
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Used: `builder`, `validator`, `documentarian`

## Step by Step Tasks

### 1. Build the do-test skill
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `.claude/skills/do-test/SKILL.md` stub with full skill definition
- Update `.claude/commands/do-test.md` to reference the skill properly
- Implement argument parsing: no args (all), type name (unit/integration/e2e/performance/tools), file path, `--changed` flag, `--no-lint` flag
- Implement changed-file detection using `git diff --name-only` with test file mapping
- Implement parallel dispatch logic using Task tool with test-engineer agents
- Implement result aggregation into summary table format
- Implement lint check (ruff + black) as a parallel agent

### 2. Validate the skill
- **Task ID**: validate-skill
- **Depends On**: build-skill
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Read the SKILL.md and command file, verify they are well-structured
- Verify argument parsing covers all documented patterns
- Verify changed-file detection logic is sound
- Verify parallel dispatch uses correct agent types
- Verify result aggregation format is clear and actionable
- Verify the skill can be invoked by `/do-build` (check compatibility)

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-skill
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/do-test.md` with usage, arguments, test types, and examples
- Add entry to `docs/features/README.md` index table
- Ensure inline documentation in SKILL.md is clear

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `ruff check .` and `black --check .`
- Verify all success criteria are met
- Verify documentation exists and is accurate
- Generate final pass/fail report

## Validation Commands

- `cat .claude/skills/do-test/SKILL.md | head -5` - Verify skill file exists and has frontmatter
- `cat .claude/commands/do-test.md | head -5` - Verify command file updated
- `test -f docs/features/do-test.md && echo "docs exist"` - Feature docs created
- `grep -c "do-test" docs/features/README.md` - Entry added to index
- `ruff check .` - Lint passes
- `black --check .` - Formatting passes

---

## Resolved Design Decisions

1. **Lint checks run by default** with `--no-lint` opt-out. The `/do-build` Definition of Done requires both tests and quality checks pass, so the default must cover both. Quick iteration users can opt out.

2. **CWD-relative, no worktree detection needed.** The SDK client sets `cwd` via `ClaudeAgentOptions` — when invoked from `/do-build` it's the worktree path, when invoked directly it's the main repo. The skill just runs commands in whatever directory it's given. This was confirmed by reviewing `agent/sdk_client.py`.

3. **Smart branch auto-detection for `--changed`.** If `git rev-parse --abbrev-ref HEAD` returns `main`, diff against `HEAD~1`. Otherwise (e.g., `session/{slug}` branches), diff against `main`. This handles both contexts — `/do-build` worktrees on feature branches and direct Claude Code sessions on main — without requiring explicit flags.
