---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-04-04
tracking: https://github.com/tomcounsell/ai/issues/686
last_comment_id:
---

# Happy Path Testing Pipeline

## Problem

We have agent-browser for interactive exploration and a frontend-tester subagent for one-off UI checks, but no system for defining, generating, and repeatedly executing deterministic regression tests for web applications. Every test run requires LLM tokens -- there is no way to codify a discovered happy path into a cheap, repeatable script.

**Current behavior:**
Each frontend test invocation uses agent-browser through the frontend-tester subagent, spending LLM tokens every time. There is no way to "record" a happy path and replay it deterministically.

**Desired outcome:**
A three-stage pipeline where: (1) an agent-browser exploration produces structured trace JSON of happy paths, (2) a generator converts those traces into deterministic Rodney shell scripts, and (3) a runner executes those scripts cheaply and repeatedly without LLM involvement.

## Prior Art

- **Issue #41**: Add "When to Use" section to agent-browser -- added usage guidance (closed)
- **Issue #121**: Build do-test command and skill -- built the test orchestrator that dispatches test runners including frontend tests (closed)

No prior work on deterministic browser test generation or Rodney integration exists.

## Data Flow

1. **Entry point**: User invokes `/do-test happy-paths` or triggers discovery on a target URL
2. **Stage 1 - Discovery**: agent-browser explores site pages, interacts with elements via `@ref`, records each step. `snapshot -i --json` provides machine-readable element data including CSS selectors
3. **Trace JSON**: Structured output per happy path -- URLs, CSS selectors (resolved from refs), input values, wait conditions, expected states (page title, visible text, URL pattern)
4. **Stage 2 - Generation**: Python script reads trace JSON, maps each step to a Rodney command (`click`, `input`, `wait`, `assert`, `screenshot`), outputs a standalone `.sh` file
5. **Stage 3 - Execution**: Runner iterates over generated `.sh` scripts, executes each via Rodney, collects exit codes (0=pass, 1=fail, 2=error), captures screenshots, produces summary report
6. **Output**: Pass/fail summary table integrated into `/do-test` results

## Architectural Impact

- **New dependencies**: Rodney Go binary (`github.com/simonw/rodney`) must be installed on each machine
- **Interface changes**: New `/do-test happy-paths` target in the test orchestrator; new trace JSON schema as the contract between stages 1 and 2
- **Coupling**: Low -- stages communicate only through trace JSON files and shell scripts on disk. No new imports into bridge or agent code
- **Data ownership**: Trace JSON and generated scripts live in `tests/happy-paths/` under version control
- **Reversibility**: Fully reversible -- removing the `happy-paths` target and `tests/happy-paths/` directory restores original state

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (trace format design, scope alignment)
- Review rounds: 1

Three distinct stages with a new external tool dependency and a schema design as the critical interface.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Rodney installed | `which rodney` | Headless Chrome test execution |
| Go toolchain | `which go` | Required to install Rodney from source |
| agent-browser installed | `which agent-browser` | Browser exploration for discovery stage |
| Chrome/Chromium available | `ls /Applications/Google\ Chrome.app 2>/dev/null \|\| which chromium` | Headless browser backend for Rodney |

Run all checks: `python scripts/check_prerequisites.py docs/plans/happy-path-testing-pipeline.md`

## Solution

### Key Elements

- **Trace JSON schema**: The critical interface between discovery and generation. Each trace file represents one happy path as an ordered list of steps with CSS selectors, action types, input values, wait conditions, and expected states
- **Discovery skill**: Uses agent-browser to explore a target site, resolves `@ref` identifiers to CSS selectors via `snapshot -i --json`, and writes structured trace JSON to `tests/happy-paths/traces/`
- **Rodney script generator**: Pure Python, reads trace JSON and emits a standalone Rodney shell script to `tests/happy-paths/scripts/`
- **Test runner**: Executes generated scripts in batch, collects results, integrates with `/do-test` as the `happy-paths` target

### Flow

**User** -> `/do-test happy-paths` -> **Runner** reads `tests/happy-paths/scripts/*.sh` -> executes each via `rodney` -> collects exit codes and screenshots -> **Summary table** in test results

For discovery: **User** -> discovery skill with target URL -> **agent-browser** explores site -> produces `tests/happy-paths/traces/*.json` -> **generator** converts to `tests/happy-paths/scripts/*.sh`

### Technical Approach

- **Trace JSON schema** defined as a Python dataclass or TypedDict for validation, with a JSON Schema file for documentation
- **Discovery** implemented as a Claude skill (`.claude/skills/do-discover-paths/SKILL.md`) that instructs the agent to use agent-browser systematically
- **Generator** implemented as a Python script (`tools/happy_path_generator.py`) -- no LLM, pure template-based conversion
- **Runner** integrated into `/do-test` SKILL.md as a new target type alongside `frontend`
- **Rodney installation** handled via `go install github.com/simonw/rodney@latest` in the update script
- **Credentials** sourced from `projects.json` testing section for login flows

### Trace JSON Schema

```json
{
  "name": "login-to-dashboard",
  "url": "https://myapp.com/login",
  "steps": [
    {
      "action": "navigate",
      "url": "https://myapp.com/login"
    },
    {
      "action": "input",
      "selector": "#email",
      "value": "{{credentials.username}}"
    },
    {
      "action": "input",
      "selector": "#password",
      "value": "{{credentials.password}}"
    },
    {
      "action": "click",
      "selector": "button[type=submit]"
    },
    {
      "action": "wait",
      "selector": ".dashboard-header"
    },
    {
      "action": "assert",
      "type": "url_contains",
      "value": "/dashboard"
    },
    {
      "action": "screenshot",
      "path": "evidence/login-to-dashboard-final.png"
    }
  ],
  "expected_final_url": "**/dashboard",
  "expected_text": ["Welcome", "Dashboard"]
}
```

Credential placeholders (`{{credentials.username}}`) are resolved at generation time from `projects.json`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Generator must handle malformed trace JSON gracefully -- validate schema before generation, emit clear error for missing required fields
- [ ] Runner must handle Rodney exit code 2 (error) distinctly from exit code 1 (test failure)
- [ ] Discovery must handle agent-browser failures (page not loading, element not found) without producing partial traces

### Empty/Invalid Input Handling
- [ ] Generator handles empty steps array -- produces no script, logs warning
- [ ] Generator handles missing selector field -- skips step with warning
- [ ] Runner handles empty `tests/happy-paths/scripts/` directory -- reports "no happy path scripts found" instead of crashing
- [ ] Discovery handles unreachable URLs -- reports error, does not produce trace

### Error State Rendering
- [ ] Runner produces clear pass/fail table even when all tests error
- [ ] Failed assertions include the actual vs expected state from Rodney output
- [ ] Screenshots are captured on failure for debugging evidence

## Test Impact

No existing tests affected -- this is a greenfield feature adding a new test target (`happy-paths`) alongside the existing `frontend` target. No existing test files, fixtures, or behavior are modified.

## Rabbit Holes

- **Screenshot visual diffing** -- pixel comparison between runs is valuable but complex; separate follow-up issue
- **Automatic happy path discovery without guidance** -- fully autonomous site crawling to find all happy paths is a research project, not a feature; start with human-guided discovery
- **Rodney command coverage** -- Rodney has many commands (PDF export, accessibility queries, file upload/download); only map the core set (click, input, wait, assert, screenshot, exists) initially
- **Cross-browser testing** -- Rodney uses rod/Chrome only; do not attempt Firefox/Safari support
- **CI integration** -- Running in CI requires headless Chrome in the CI environment; defer to a follow-up

## Risks

### Risk 1: Rodney is early-stage software
**Impact:** Breaking changes in Rodney's CLI interface could break generated scripts
**Mitigation:** Pin Rodney to a specific version; keep the generator's Rodney command mapping in one file for easy updates

### Risk 2: CSS selectors from agent-browser may be fragile
**Impact:** Generated selectors (from snapshot --json) may break when the target site's DOM changes
**Mitigation:** Prefer stable selectors (id, role, name attributes) over positional selectors; the trace schema supports multiple selector strategies; regeneration via discovery is cheap (one LLM call)

### Risk 3: Credential handling in generated scripts
**Impact:** Credentials could leak into version-controlled shell scripts
**Mitigation:** Generated scripts read credentials from environment variables at runtime, never inline; `.gitignore` the evidence screenshot directory

## Race Conditions

No race conditions identified -- all three pipeline stages are sequential and single-threaded. Discovery produces files, generation reads them, runner executes them. No concurrent access patterns.

## No-Gos (Out of Scope)

- Screenshot visual diffing (pixel comparison between runs)
- Replacing the existing frontend-tester subagent or `/do-test frontend` target
- CI/CD integration (headless Chrome in CI environments)
- Cross-browser support (Firefox, Safari)
- Automatic site crawling without human guidance
- Rodney commands beyond core set (PDF export, accessibility tree, file download)

## Update System

The update script (`scripts/remote-update.sh`) needs to install Rodney:

- Add `go install github.com/simonw/rodney@latest` to the update script, gated on `which go` availability
- Rodney is a single Go binary with no runtime dependencies beyond Chrome/Chromium
- No new Python dependencies required -- the generator and runner are pure Python using only stdlib
- No config file changes needed beyond the existing `projects.json` testing section

## Agent Integration

No agent integration required for stages 2 and 3 -- the generator is a Python script and the runner integrates with `/do-test` which is a skill, not an MCP tool.

Stage 1 (discovery) is implemented as a new Claude skill (`.claude/skills/do-discover-paths/SKILL.md`) that the agent uses via the existing agent-browser tool. No new MCP server or `.mcp.json` changes are needed.

- No changes to `.mcp.json`
- No changes to `mcp_servers/`
- No changes to `bridge/telegram_bridge.py`
- The discovery skill is invoked via `/do-discover-paths <url>` like any other skill
- Integration test: run discovery on a known test site, verify trace JSON is produced, generate a script, execute it, verify pass

## Documentation

- [ ] Create `docs/features/happy-path-testing-pipeline.md` describing the three-stage pipeline, trace format, and usage
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add inline documentation in `tools/happy_path_generator.py` for the trace-to-script conversion logic
- [ ] Document the trace JSON schema in `tests/happy-paths/SCHEMA.md`
- [ ] Update `.claude/skills/do-test/SKILL.md` with the `happy-paths` target documentation

## Success Criteria

- [ ] Discovery skill produces valid trace JSON from agent-browser exploration of a target URL
- [ ] Generator converts trace JSON into executable Rodney shell scripts without LLM tokens
- [ ] Runner executes Rodney scripts in batch and produces pass/fail summary
- [ ] `/do-test happy-paths` target works end-to-end
- [ ] Generated scripts run deterministically (same result on repeated execution)
- [ ] Credentials are never inlined in generated scripts (environment variable substitution)
- [ ] Existing `/do-test frontend` target is unaffected
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (trace-schema)**
  - Name: schema-builder
  - Role: Define trace JSON schema and validation
  - Agent Type: builder
  - Resume: true

- **Builder (discovery-skill)**
  - Name: discovery-builder
  - Role: Create the discovery skill that drives agent-browser and produces trace JSON
  - Agent Type: builder
  - Resume: true

- **Builder (generator)**
  - Name: generator-builder
  - Role: Build the trace-to-Rodney-script generator
  - Agent Type: builder
  - Resume: true

- **Builder (runner)**
  - Name: runner-builder
  - Role: Build the test runner and integrate with /do-test
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline)**
  - Name: pipeline-validator
  - Role: End-to-end validation of the full pipeline
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation and schema docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Define Trace JSON Schema
- **Task ID**: build-schema
- **Depends On**: none
- **Validates**: tests/unit/test_happy_path_schema.py (create)
- **Assigned To**: schema-builder
- **Agent Type**: builder
- **Parallel**: true
- Define trace JSON schema as Python dataclass/TypedDict in `tools/happy_path_schema.py`
- Create JSON Schema file at `tests/happy-paths/SCHEMA.md`
- Add validation function that checks a trace dict against the schema
- Write unit tests for schema validation (valid trace, missing fields, empty steps)

### 2. Build Rodney Script Generator
- **Task ID**: build-generator
- **Depends On**: build-schema
- **Validates**: tests/unit/test_happy_path_generator.py (create)
- **Assigned To**: generator-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/happy_path_generator.py` with `generate_script(trace_json, output_path, credentials)` function
- Map trace actions to Rodney commands: navigate->open, input->input, click->click, wait->wait, assert->assert, screenshot->screenshot
- Handle credential placeholder resolution from projects.json
- Write unit tests: valid trace produces valid script, missing selector skips step, empty steps produces no output

### 3. Build Discovery Skill
- **Task ID**: build-discovery
- **Depends On**: build-schema
- **Validates**: manual end-to-end test with a live site
- **Assigned To**: discovery-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-generator)
- Create `.claude/skills/do-discover-paths/SKILL.md` with instructions for systematic site exploration
- Skill instructs agent to use `agent-browser snapshot -i --json` to get element data with CSS selectors
- Skill instructs agent to write trace JSON conforming to the schema
- Output goes to `tests/happy-paths/traces/{path-name}.json`

### 4. Build Test Runner and /do-test Integration
- **Task ID**: build-runner
- **Depends On**: build-generator
- **Validates**: tests/unit/test_happy_path_runner.py (create)
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/happy_path_runner.py` with batch execution logic
- Handle Rodney exit codes: 0=pass, 1=fail, 2=error
- Capture screenshots as evidence on failure
- Produce summary table compatible with /do-test result format
- Update `.claude/skills/do-test/SKILL.md` to add `happy-paths` target routing
- Write unit tests for result parsing and summary generation

### 5. Update System Script
- **Task ID**: build-update
- **Depends On**: none
- **Validates**: scripts/remote-update.sh contains rodney install step
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Rodney installation step to `scripts/remote-update.sh`
- Gate on Go availability: skip if `which go` fails

### 6. Create Directory Structure
- **Task ID**: build-dirs
- **Depends On**: none
- **Assigned To**: schema-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/happy-paths/traces/` directory with `.gitkeep`
- Create `tests/happy-paths/scripts/` directory with `.gitkeep`
- Create `tests/happy-paths/evidence/` directory and add to `.gitignore`

### 7. Validate Full Pipeline
- **Task ID**: validate-pipeline
- **Depends On**: build-generator, build-runner, build-discovery
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify generator produces valid shell scripts from sample trace JSON
- Verify runner correctly parses Rodney exit codes
- Verify `/do-test happy-paths` routing works in the skill definition
- Verify credentials are never inlined in generated scripts

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pipeline
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/happy-path-testing-pipeline.md`
- Add entry to `docs/features/README.md`
- Document trace JSON schema in `tests/happy-paths/SCHEMA.md`

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit tests
- Verify all success criteria met
- Verify documentation exists and is linked

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_happy_path_*.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/happy_path_generator.py tools/happy_path_runner.py tools/happy_path_schema.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/happy_path_generator.py tools/happy_path_runner.py tools/happy_path_schema.py` | exit code 0 |
| Schema exists | `test -f tools/happy_path_schema.py` | exit code 0 |
| Generator exists | `test -f tools/happy_path_generator.py` | exit code 0 |
| Runner exists | `test -f tools/happy_path_runner.py` | exit code 0 |
| Discovery skill exists | `test -f .claude/skills/do-discover-paths/SKILL.md` | exit code 0 |
| Feature docs exist | `test -f docs/features/happy-path-testing-pipeline.md` | exit code 0 |
| No inline credentials | `grep -r 'password\|secret\|api_key' tests/happy-paths/scripts/ 2>/dev/null` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue provided detailed specifications for all three stages, trace format, and integration approach. The plan can proceed to critique and build.
