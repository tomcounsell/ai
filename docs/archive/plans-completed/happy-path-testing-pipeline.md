---
status: Shipped
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
2. **Stage 1 - Discovery**: agent-browser explores site pages, interacts with elements via `@ref`, records each step. After each interaction, `agent-browser eval` runs a JS helper that computes a unique CSS selector for the interacted element (using id, data attributes, or a computed path)
3. **Trace JSON**: Structured output per happy path -- URLs, CSS selectors (extracted via JS injection), input values, wait conditions, expected states (page title, visible text, URL pattern)
4. **Stage 2 - Generation**: Python script reads trace JSON, maps each step to a Rodney command (`click`, `input`, `wait`, `assert`, `screenshot`), outputs a standalone `.sh` file
5. **Stage 3 - Execution**: Runner iterates over generated `.sh` scripts, executes each via Rodney, collects exit codes (0=pass, 1=fail, 2=error), captures screenshots, produces summary report
6. **Output**: Pass/fail summary table integrated into `/do-test` results

## Architectural Impact

- **New dependencies**: Rodney prebuilt binary (from `github.com/simonw/rodney/releases`) must be installed on each machine
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
| agent-browser installed | `which agent-browser` | Browser exploration for discovery stage |
| Chrome/Chromium available | `ls /Applications/Google\ Chrome.app 2>/dev/null \|\| which chromium` | Headless browser backend for Rodney |

Run all checks: `python scripts/check_prerequisites.py docs/plans/happy-path-testing-pipeline.md`

## Solution

### Key Elements

- **Trace JSON schema**: The critical interface between discovery and generation. Each trace file represents one happy path as an ordered list of steps with CSS selectors, action types, input values, wait conditions, and expected states
- **Discovery skill**: Uses agent-browser to explore a target site, extracts CSS selectors via `agent-browser eval` JS injection after each interaction, and writes structured trace JSON to `tests/happy-paths/traces/`
- **Rodney script generator**: Pure Python, reads trace JSON and emits a standalone Rodney shell script to `tests/happy-paths/scripts/`
- **Test runner**: Executes generated scripts in batch, collects results, integrates with `/do-test` as the `happy-paths` target

### Flow

**User** -> `/do-test happy-paths` -> **Runner** reads `tests/happy-paths/scripts/*.sh` -> executes each via `rodney` -> collects exit codes and screenshots -> **Summary table** in test results

For discovery: **User** -> discovery skill with target URL -> **agent-browser** explores site -> produces `tests/happy-paths/traces/*.json` -> **generator** converts to `tests/happy-paths/scripts/*.sh`

### Technical Approach

- **Trace JSON schema** defined as a Python dataclass or TypedDict for validation, with a JSON Schema file for documentation
- **Discovery** implemented as a Claude skill (`.claude/skills/do-discover-paths/SKILL.md`) that instructs the agent to use agent-browser systematically. After each `snapshot -i` to identify elements and each interaction (click, fill), the skill instructs the agent to run `agent-browser eval` with a CSS selector extraction helper (see **CSS Selector Extraction** below) to capture a stable, unique CSS selector for the interacted element
- **Generator** implemented as a Python script (`tools/happy_path_generator.py`) -- no LLM, pure template-based conversion
- **Runner** integrated into `/do-test` SKILL.md as a new target type alongside `frontend`. The `happy-paths` target is dispatched directly via bash (`python tools/happy_path_runner.py`), not via a subagent, because it requires no LLM involvement (see **do-test Integration** below)
- **Rodney installation** handled via prebuilt binary download from GitHub Releases (v0.4.0). No Go toolchain required (see **Rodney Installation** below)
- **Credentials** sourced from `projects.json` testing section for login flows

### CSS Selector Extraction (B1 Resolution)

`agent-browser snapshot -i --json` returns only `name` and `role` for each `@ref` -- it does NOT provide CSS selectors. The discovery skill resolves this by using `agent-browser eval` to inject a JavaScript helper that computes a unique CSS selector for a given element.

**JS helper function** (injected via `agent-browser eval`):

```javascript
// Given a description (text content, role) from snapshot, find the element and compute its CSS selector
function getSelector(el) {
  if (el.id) return '#' + CSS.escape(el.id);
  if (el.getAttribute('data-testid')) return '[data-testid="' + el.getAttribute('data-testid') + '"]';
  if (el.getAttribute('name')) return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
  // Fall back to computed path: tag + nth-child chain
  const path = [];
  while (el && el !== document.body) {
    let selector = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
      if (siblings.length > 1) selector += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
    }
    path.unshift(selector);
    el = parent;
  }
  return path.join(' > ');
}
```

**Discovery workflow per step:**
1. `agent-browser snapshot -i` -- get `@ref` identifiers and semantic descriptions
2. Agent decides which element to interact with (e.g., `@e3` with name "Submit" and role "button")
3. `agent-browser eval "getSelector(document.querySelector('button'))"` -- extract CSS selector using the element's known tag/role/name to locate it in the DOM, then compute a stable selector
4. Agent records the CSS selector in the trace JSON
5. Agent performs the interaction: `agent-browser click @e3`

The key insight: `@ref` is used for agent-browser interaction (ephemeral), while the JS-extracted CSS selector is persisted in the trace JSON for Rodney script generation (durable).

**Selector priority order** (most stable first):
1. `#id` -- most stable, preferred when available
2. `[data-testid="..."]` -- explicit test hooks
3. `[name="..."]` -- form elements
4. `tag:nth-of-type(N)` path -- fallback, least stable

### Rodney Installation (B2 Resolution)

Go is not installed on any machine. Rodney v0.4.0 publishes prebuilt binaries on GitHub Releases for darwin-arm64, darwin-amd64, linux-amd64, and linux-arm64.

**Installation command** (for the update script):

```bash
# Detect architecture
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64) ARCH="amd64" ;;
esac

RODNEY_VERSION="v0.4.0"
RODNEY_URL="https://github.com/simonw/rodney/releases/download/${RODNEY_VERSION}/rodney-${OS}-${ARCH}.tar.gz"

curl -sL "$RODNEY_URL" | tar xz -C /usr/local/bin/ rodney
chmod +x /usr/local/bin/rodney
```

No Go toolchain required. The binary is self-contained with no runtime dependencies beyond Chrome/Chromium.

### do-test Integration (B3 Resolution)

The `/do-test` SKILL.md argument parsing table gets a new `happy-paths` entry. Unlike the `frontend` target (which dispatches a subagent for LLM-driven browser interaction), `happy-paths` runs a deterministic Python script directly via bash:

**New entry in argument parsing table:**

| Input | Behavior |
|-------|----------|
| `happy-paths` | Run `python tools/happy_path_runner.py tests/happy-paths/scripts/` directly via bash. No subagent dispatch. |

**Routing logic addition to SKILL.md parsing rules:**
- Rule 2 becomes: "If target is `frontend`, route to Frontend Testing. If target is `happy-paths`, route to Happy Path Testing (see below). Otherwise, proceed with pytest."

**New section in SKILL.md** (after Frontend Testing):

```markdown
## Happy Path Testing (`happy-paths` target)

When `TEST_ARGS` starts with `happy-paths`, run the deterministic test runner directly. No subagent needed.

### Execution:
\`\`\`bash
python tools/happy_path_runner.py tests/happy-paths/scripts/
\`\`\`

### Result format:
The runner outputs a JSON summary to stdout with pass/fail/error counts per script.
Include results in the summary table alongside pytest and frontend suites.

### When running all tests:
If `tests/happy-paths/scripts/` contains `.sh` files, include happy-paths execution
alongside pytest and frontend targets. Run via bash, not subagent.
```

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
- [x] Generator must handle malformed trace JSON gracefully -- validate schema before generation, emit clear error for missing required fields
- [x] Runner must handle Rodney exit code 2 (error) distinctly from exit code 1 (test failure)
- [x] Discovery must handle agent-browser failures (page not loading, element not found) without producing partial traces

### Empty/Invalid Input Handling
- [x] Generator handles empty steps array -- produces no script, logs warning
- [x] Generator handles missing selector field -- skips step with warning
- [x] Runner handles empty `tests/happy-paths/scripts/` directory -- reports "no happy path scripts found" instead of crashing
- [x] Discovery handles unreachable URLs -- reports error, does not produce trace

### Error State Rendering
- [x] Runner produces clear pass/fail table even when all tests error
- [x] Failed assertions include the actual vs expected state from Rodney output
- [x] Screenshots are captured on failure for debugging evidence

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

### Risk 2: CSS selectors extracted via JS injection may be fragile
**Impact:** Generated selectors (from `agent-browser eval` JS helper) may break when the target site's DOM changes
**Mitigation:** Selector priority prefers stable attributes (id > data-testid > name) over positional selectors (nth-of-type path); regeneration via discovery is cheap (one LLM call)

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

- Add a Rodney binary download step using `curl` from GitHub Releases (v0.4.0 pinned). No Go toolchain required
- Detect OS and architecture (`uname -s`, `uname -m`) to download the correct binary (`rodney-darwin-arm64.tar.gz`, `rodney-linux-amd64.tar.gz`, etc.)
- Install to `/usr/local/bin/rodney` (or `~/.local/bin/rodney` if no root access)
- Skip download if `rodney --version` already reports v0.4.0
- Rodney is a single binary with no runtime dependencies beyond Chrome/Chromium
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

- [x] Create `docs/features/happy-path-testing-pipeline.md` describing the three-stage pipeline, trace format, and usage
- [x] Add entry to `docs/features/README.md` index table
- [x] Add inline documentation in `tools/happy_path_generator.py` for the trace-to-script conversion logic
- [x] Document the trace JSON schema in `tests/happy-paths/SCHEMA.md`
- [x] Update `.claude/skills/do-test/SKILL.md` with the `happy-paths` target documentation

## Success Criteria

- [x] Discovery skill produces valid trace JSON from agent-browser exploration of a target URL
- [x] Generator converts trace JSON into executable Rodney shell scripts without LLM tokens
- [x] Runner executes Rodney scripts in batch and produces pass/fail summary
- [x] `/do-test happy-paths` target works end-to-end
- [x] Generated scripts run deterministically (same result on repeated execution)
- [x] Credentials are never inlined in generated scripts (environment variable substitution)
- [x] Existing `/do-test frontend` target is unaffected
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

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
- Map trace actions to Rodney commands (verified against Rodney v0.4.0 README): navigate->`rodney open`, input->`rodney input`, click->`rodney click`, wait->`rodney wait`, assert->`rodney assert` (JS expression form), screenshot->`rodney screenshot`, exists->`rodney exists`
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
- Skill instructs agent to use `agent-browser snapshot -i` to identify elements via `@ref`, then `agent-browser eval` with the CSS selector extraction JS helper to resolve each interacted element to a stable CSS selector
- Skill instructs agent to write trace JSON conforming to the schema, with CSS selectors (not `@ref` identifiers) in the `selector` field
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
- Update `.claude/skills/do-test/SKILL.md` to add `happy-paths` target: add entry to argument parsing table (`happy-paths` -> run `python tools/happy_path_runner.py tests/happy-paths/scripts/` directly via bash, no subagent), add parsing rule to route before pytest dispatch, add "Happy Path Testing" section after "Frontend Testing" section
- Write unit tests for result parsing and summary generation

### 5. Update System Script
- **Task ID**: build-update
- **Depends On**: none
- **Validates**: scripts/remote-update.sh contains rodney install step
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Rodney prebuilt binary download step to `scripts/remote-update.sh`
- Detect OS/arch and download from GitHub Releases (v0.4.0 pinned)
- Skip if `rodney` is already at the target version

### 6. Create Directory Structure
- **Task ID**: build-dirs
- **Depends On**: none
- **Assigned To**: schema-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/happy-paths/traces/` directory with `.gitkeep`
- Create `tests/happy-paths/scripts/` directory with `.gitkeep`
- Create `tests/happy-paths/evidence/` directory with `.gitkeep`
- Add `tests/happy-paths/evidence/` to the project `.gitignore` file

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

| Severity | Critic | Concern | Resolution |
|----------|--------|---------|------------|
| BLOCKER | Skeptic | `snapshot -i --json` does NOT provide CSS selectors -- only returns `name` and `role`. The core pipeline assumption (ref-to-selector resolution) is wrong. Discovery stage cannot produce trace JSON with CSS selectors. | RESOLVED -- Use `agent-browser eval` with JS helper to extract CSS selectors after each interaction. Selector priority: id > data-testid > name > computed nth-of-type path. See "CSS Selector Extraction (B1 Resolution)" section. |
| BLOCKER | Operator | Go toolchain not installed on any machine. Update script gates on `which go` and silently skips. Rodney will never be installed. | RESOLVED -- Use prebuilt binary from GitHub Releases (v0.4.0). Download via curl with OS/arch detection. No Go required. See "Rodney Installation (B2 Resolution)" section. |
| BLOCKER | Operator | `/do-test happy-paths` routing mechanism unspecified. Current SKILL.md has no `happy-paths` entry and plan does not specify whether it invokes runner via bash or subagent. | RESOLVED -- `happy-paths` target runs `python tools/happy_path_runner.py` directly via bash (no subagent, no LLM). New entry in argument parsing table, new parsing rule, new "Happy Path Testing" section in SKILL.md. See "do-test Integration (B3 Resolution)" section. |
| CONCERN | Skeptic | Rodney CLI command mapping (`navigate->open`, `input->input`, etc.) is unvalidated. No spike has verified these command names and argument formats. | RESOLVED -- Validated against Rodney v0.4.0 README. Correct mappings: navigate->`rodney open`, input->`rodney input`, click->`rodney click`, wait->`rodney wait`, assert->`rodney assert` (JS expression), screenshot->`rodney screenshot`, exists->`rodney exists`. |
| CONCERN | Adversary | Credential security relies on convention (env vars) with no enforcement. No pre-commit hook or CI check to prevent accidental credential leaks in generated scripts. | ACKNOWLEDGED -- Enforcement mechanism deferred to follow-up. Convention + verification table grep is sufficient for initial implementation. |
| CONCERN | Simplifier | Six team members defined for solo-dev project with mostly sequential task dependencies. Parallelism is illusory. | ACKNOWLEDGED -- Team orchestration section represents logical roles for the builder agent, not actual parallel humans. Sequential execution is expected. |
| NIT | Simplifier | Duplicate SCHEMA.md ownership between Task 1 (schema-builder) and Task 8 (docs-writer). | RESOLVED -- Task 8 references SCHEMA.md but does not create it (owned by Task 1). |
| NIT | Operator | Missing `.gitignore` edit step in Task 6 for `tests/happy-paths/evidence/`. | RESOLVED -- Added explicit `.gitignore` edit step to Task 6. |

---

## Open Questions

No open questions -- the issue provided detailed specifications for all three stages, trace format, and integration approach. The plan can proceed to critique and build.
