---
status: Done
type: feature
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/624
last_comment_id:
---

# Vet and Install OfficeCLI

## Problem

The agent has no way to work with Microsoft Office files (.docx, .xlsx, .pptx). When tasks involve reading, editing, or creating Office documents, the agent cannot fulfill them.

**Current behavior:**
Office file operations require manual workarounds or external tools not integrated into the system.

**Desired outcome:**
OfficeCLI is installed on all bridge machines, documented in CLAUDE.md with usage patterns, and the `/update` skill ensures it stays current across deployments. The agent can create, read, and edit Word, Excel, and PowerPoint files via CLI commands.

## Prior Art

- **Issue #19**: Google Workspace Integration -- established the pattern for CLI tool integration + CLAUDE.md documentation
- **Issue #40**: Add allowed-tools to google-workspace -- refined tool exposure
- **`gws` in CLAUDE.md**: Reference implementation for how third-party CLIs are documented with usage patterns, flags, and common commands

## Data Flow

```
Agent receives task involving Office file
  -> Agent runs `officecli` commands (read/create/edit)
  -> OfficeCLI reads/writes .docx/.xlsx/.pptx files on disk
  -> Agent processes JSON output or confirms file creation
```

No network calls, no API keys, no runtime dependencies. Pure local file manipulation.

## Architectural Impact

- **New files**: `scripts/update/officecli.py` (install/update module), `.claude/skills/officecli/SKILL.md` (agent skill reference)
- **Modified files**: `CLAUDE.md` (add OfficeCLI section), `scripts/update/run.py` (wire officecli step)
- **Interface changes**: None -- OfficeCLI is invoked as a subprocess, same as `gws` or `gh`
- **Coupling**: Zero runtime coupling. OfficeCLI is a standalone binary. The only integration point is the update system downloading it.
- **Reversibility**: High. Remove the update step, delete the binary, revert CLAUDE.md. No schema changes, no dependencies.

## Appetite

**Size:** Small

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 0 (straightforward integration, follows established pattern)
- Review rounds: 1

Four files to create or modify. The pattern is well-established by `gws` integration. Primary work is the install script and CLAUDE.md documentation.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Internet access | `curl -sI https://github.com/iOfficeAI/OfficeCLI/releases` | Download binary from GitHub releases |
| `~/.local/bin` in PATH | `echo $PATH \| grep -q '.local/bin'` | Standard install location for user binaries |

## Solution

### Key Elements

- **Update system module** (`scripts/update/officecli.py`): Downloads OfficeCLI binary from GitHub releases, verifies SHA256 checksum, places in `~/.local/bin/officecli`. Handles both macOS ARM64 and Linux x64. Checks installed version against latest release to skip unnecessary downloads.
- **Update orchestrator integration**: Wire the officecli step into `scripts/update/run.py` so it runs during full and cron updates.
- **CLAUDE.md documentation**: Add a section parallel to the `gws` block documenting OfficeCLI usage, commands, output format, and the three-layer architecture (L1 read, L2 DOM, L3 raw XML).
- **Agent skill file**: Create `.claude/skills/officecli/SKILL.md` with condensed usage reference based on OfficeCLI's own SKILL.md.

### Flow

1. `/update` runs -> `officecli.install_or_update()` checks if binary exists and is current
2. If missing or outdated: download platform-specific binary from GitHub releases, verify SHA256, install to `~/.local/bin/officecli`
3. Agent reads CLAUDE.md or skill file -> knows how to use `officecli` commands
4. Agent runs commands like `officecli word read doc.docx --json` or `officecli excel create report.xlsx --json '...'`

### Technical Approach

1. **Install module** (`scripts/update/officecli.py`)
   - `get_installed_version()`: Run `officecli --version`, parse output
   - `get_latest_release()`: Query GitHub API for latest release tag
   - `download_and_install(version)`: Download platform binary, verify SHA256, chmod +x, move to `~/.local/bin/`
   - `install_or_update()`: Orchestrate the above, return a result dataclass
   - Platform detection: `platform.system()` + `platform.machine()` to select correct binary asset
   - SHA256 verification: Download `.sha256` file from release, compare against downloaded binary
   - Atomic install: Download to temp file, verify, then move to final location

2. **Update orchestrator wiring** (`scripts/update/run.py`)
   - Add `officecli` import to the update module imports
   - Add a step between dependency sync and service restart (Step 3.7 or similar)
   - Log install/update/skip status
   - Non-fatal: failure to install OfficeCLI should be a warning, not an error

3. **CLAUDE.md section**
   - Place after the `gws` section (similar CLI tool, similar documentation pattern)
   - Document: binary location, core commands (word/excel/powerpoint), read/create/edit subcommands
   - Show JSON output examples for agent consumption
   - Note the three-layer architecture: L1 (read), L2 (DOM manipulation), L3 (raw XML)

4. **Agent skill file** (`.claude/skills/officecli/SKILL.md`)
   - Condensed from OfficeCLI's upstream SKILL.md
   - Focus on patterns the agent actually needs: read with `--json`, create with JSON input, edit operations
   - Include examples for each format (Word, Excel, PowerPoint)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] GitHub API unreachable: `install_or_update()` returns warning, does not block update
- [ ] SHA256 mismatch: abort install, log error, leave existing binary in place
- [ ] Binary download interrupted: temp file cleaned up, no partial binary in PATH
- [ ] `~/.local/bin` does not exist: create it (same as OfficeCLI's own install script)

### Empty/Invalid Input Handling
- [ ] No internet during update: skip OfficeCLI step with warning
- [ ] Binary exists but `--version` fails: treat as corrupted, reinstall
- [ ] GitHub release has no matching platform asset: log warning, skip

### Timeout Budget
- [ ] GitHub API query: 10s timeout
- [ ] Binary download: 60s timeout (binary is ~29MB)
- [ ] SHA256 verification: instant (local computation)

### Error State Rendering
- [ ] All failures surfaced as warnings in update output (not errors that block the update)
- [ ] Update summary includes OfficeCLI version after successful install

## Test Impact

No existing tests affected -- this is a greenfield feature adding a new CLI tool integration. The update system tests (`tests/unit/test_update_*.py`) do not need modification because the new module is additive and wired via a new step in the orchestrator.

New tests to create:
- `tests/unit/test_officecli_install.py`: Test version parsing, platform detection, SHA256 verification logic (mock network calls)

## Rabbit Holes

- **Building from source**: Do not attempt to compile OfficeCLI from source to verify binary integrity. The trust model is the same as `gh`, `gws`, and other closed-source binaries -- download from official releases, verify checksums.
- **MCP server registration**: OfficeCLI supports `officecli mcp claude` for MCP integration. This is explicitly out of scope for this issue. Evaluate as a follow-up after direct CLI usage is proven.
- **Windows support**: No Windows bridge machines exist. Do not add Windows platform detection or binaries.
- **Auto-update on every command**: Do not check for updates every time OfficeCLI is invoked. The update system handles versioning on its own schedule.

## Risks

### Risk 1: Binary Size in PATH
**Impact:** The ~29MB binary in `~/.local/bin` is larger than typical CLI tools. Disk space is not a concern, but download time on slow connections could slow updates.
**Mitigation:** Version check before download -- only download when the installed version differs from latest. The update step should complete in under 30s on a reasonable connection.

### Risk 2: Upstream Release Cadence
**Impact:** OfficeCLI is a young project (org created Sep 2023). Release format or asset naming could change.
**Mitigation:** Pin to known release asset naming pattern. If the pattern breaks, the install step fails gracefully with a warning. The existing binary remains functional.

## Race Conditions

None. OfficeCLI installation is a single-writer operation (only the update system writes to `~/.local/bin/officecli`). The binary is not modified at runtime.

## No-Gos (Out of Scope)

- **MCP server registration**: Deferred to follow-up issue. Start with direct CLI usage.
- **Windows support**: No Windows bridge machines currently.
- **Building from source**: Trust model matches other CLI tools (download binary, verify SHA256).
- **Office file format validation**: OfficeCLI handles this internally. The agent does not need to validate file structure.
- **Python wrapper library**: Do not create a Python wrapper around OfficeCLI. Direct subprocess calls are sufficient and follow the `gws` pattern.

## Update System

The update system is the primary integration point for this feature:

- **New module**: `scripts/update/officecli.py` handles download, SHA256 verification, and install
- **Orchestrator change**: `scripts/update/run.py` gains a new step that calls `officecli.install_or_update()` during full and cron updates
- **Binary location**: `~/.local/bin/officecli` -- same convention as other user-installed CLIs
- **Version tracking**: The install module checks `officecli --version` against the latest GitHub release tag
- **Propagation**: All machines get OfficeCLI on their next update cycle (no manual intervention)
- **No new pip dependencies**: The install module uses only stdlib (`urllib.request`, `hashlib`, `platform`, `shutil`)

## Agent Integration

- **No MCP server changes**: OfficeCLI is invoked as a subprocess via `officecli` commands. No MCP registration needed for initial integration.
- **No bridge changes**: The bridge does not need to know about OfficeCLI. The agent invokes it directly.
- **CLAUDE.md documentation**: The primary integration mechanism. The agent reads CLAUDE.md to learn available tools and their usage patterns.
- **Skill file**: `.claude/skills/officecli/SKILL.md` provides a focused reference for OfficeCLI operations. This file is available to the agent via the skills system.
- **No `.mcp.json` changes**: MCP integration is deferred to a follow-up.
- **Integration validation**: After install, run `officecli --version` and a basic read/create cycle to confirm the binary works.

## Documentation

- [ ] Add OfficeCLI section to `CLAUDE.md` (after `gws` section) with usage patterns, flags, and common commands
- [ ] Create `.claude/skills/officecli/SKILL.md` with condensed agent-oriented reference
- [ ] Add entry to `docs/features/README.md` index table linking to the skill file
- [ ] Inline docstrings in `scripts/update/officecli.py` for all public functions

## Success Criteria

- [ ] OfficeCLI binary installed and functional on dev machine
- [ ] Security vet completed: install script reviewed, binary tested, no unexpected network calls
- [ ] `scripts/update/officecli.py` module created with install/update/version-check logic
- [ ] `scripts/update/run.py` wired to call officecli install step
- [ ] CLAUDE.md updated with OfficeCLI usage section
- [ ] `.claude/skills/officecli/SKILL.md` created with agent reference
- [ ] Agent can create, read, and edit a .docx, .xlsx, and .pptx file via CLI
- [ ] `/update` tested end-to-end on dev machine
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Step by Step Tasks

### 1. Local Vetting
- **Task ID**: vet-officecli
- **Depends On**: none
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Install OfficeCLI manually on dev machine using upstream install script
- Test basic operations: create/read/edit for .docx, .xlsx, .pptx
- Verify JSON output format works for agent consumption
- Monitor for unexpected network calls during file operations
- Document any quirks or limitations discovered

### 2. Create Update System Module
- **Task ID**: build-update-module
- **Depends On**: vet-officecli
- **Validates**: tests/unit/test_officecli_install.py (create)
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/update/officecli.py` with platform detection, version check, download, SHA256 verify, and install
- Wire into `scripts/update/run.py` as a new step
- Test install and update flows

### 3. Document in CLAUDE.md
- **Task ID**: build-claude-md-docs
- **Depends On**: vet-officecli
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true (parallel with task 2)
- Add OfficeCLI section to CLAUDE.md after the gws section
- Include: binary location, usage pattern, flags, common commands, output format
- Follow the same structure as the gws documentation block

### 4. Create Agent Skill File
- **Task ID**: build-skill-file
- **Depends On**: vet-officecli
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true (parallel with tasks 2-3)
- Create `.claude/skills/officecli/SKILL.md`
- Condense from upstream SKILL.md, focusing on patterns the agent needs
- Include examples for Word, Excel, and PowerPoint operations

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-update-module, build-claude-md-docs, build-skill-file
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Run update system end-to-end on dev machine
- Verify OfficeCLI binary works after update-system install
- Verify CLAUDE.md section is complete and accurate

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Binary installed | `officecli --version` | Version string output |
| Update module importable | `python -c "from scripts.update.officecli import install_or_update; print('OK')"` | output OK |
| Word read | `officecli word read test.docx --json` | JSON output with document content |
| Excel read | `officecli excel read test.xlsx --json` | JSON output with spreadsheet data |
| PowerPoint read | `officecli powerpoint read test.pptx --json` | JSON output with slide content |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. **Install location**: The upstream install script uses `~/.local/bin`. Should we follow this convention or install to a project-local location (e.g., `~/src/node_modules/.bin/` like `gws`)? Using `~/.local/bin` is more standard for user binaries but differs from the `gws` pattern.

2. **Version pinning**: Should we pin to a specific OfficeCLI version or always install latest? Pinning is safer but requires manual bumps. Always-latest matches the `gws` pattern but risks breaking changes.
