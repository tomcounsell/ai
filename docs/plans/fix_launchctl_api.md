---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-05
tracking: https://github.com/tomcounsell/ai/issues/251
---

# Fix Deprecated launchctl API (bootstrap/bootout)

## Problem

After the daydream-to-reflections rename (#233), the reflections service never fires on secondary machines. The root cause: `launchctl load` / `launchctl unload` are deprecated since macOS 13 (Ventura) and silently fail on modern macOS. The `|| true` guards in `remote-update.sh` swallow the error entirely.

**Current behavior:**
- `install_reflections.sh` uses `launchctl load/unload` which silently fails on macOS 13+
- `remote-update.sh` uses `launchctl load/unload` with `|| true` hiding failures
- `service.py` `install_reflections()` uses the same deprecated commands
- `service.py` `install_caffeinate()` also uses deprecated `launchctl load`
- Reflections never fire on secondary machines after `/update`

**Desired outcome:**
- All launchctl calls use `launchctl bootstrap gui/$(id -u)` and `launchctl bootout gui/$(id -u)` (the modern API)
- Errors are visible, not swallowed
- Reflections fire reliably on all machines after install or update

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Straightforward find-and-replace across three files plus test updates.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Shell script fixes**: Replace `launchctl load/unload` with `launchctl bootstrap/bootout` in both shell scripts
- **Python function fixes**: Update `install_reflections()` and `install_caffeinate()` in `service.py`
- **Error visibility**: Remove `|| true` from `remote-update.sh` launchctl calls, add `echo` on failure
- **Test updates**: Update existing test assertions and add new `TestInstallMechanism` tests

### Flow

**Install/update** -> bootout existing label (if present) -> copy plist -> bootstrap new plist -> verify loaded

### Technical Approach

The modern launchctl API uses domain targets instead of plist paths:

```bash
# Unload (replaces `launchctl unload <plist>`)
launchctl bootout gui/$(id -u)/<label>

# Load (replaces `launchctl load <plist>`)
launchctl bootstrap gui/$(id -u) <plist_path>
```

Key differences:
- `bootout` takes the domain + label (not the plist path)
- `bootstrap` takes the domain + plist path
- `bootout` for a non-existent label returns non-zero (need to guard with label check)

Files to modify:
1. `scripts/install_reflections.sh` (lines 27, 34, 43) -- replace load/unload
2. `scripts/remote-update.sh` (lines 46, 51, 54) -- replace load/unload, remove `|| true`
3. `scripts/update/service.py` `install_reflections()` (lines 151, 156, 163) -- replace load/unload
4. `scripts/update/service.py` `install_caffeinate()` (line 233) -- replace load
5. `tests/test_reflections_scheduling.py` -- update assertions at lines 83-84, add `TestInstallMechanism`

## Rabbit Holes

- Do NOT convert `launchctl list` to the modern `launchctl print` API -- `list | grep` still works fine for checking if a label is loaded
- Do NOT attempt to fix bridge or watchdog plist loading in this PR -- scope is only reflections, daydream migration, and caffeinate
- Do NOT add integration tests that actually run launchctl -- keep tests as content/assertion checks

## Risks

### Risk 1: bootout fails if label not loaded
**Impact:** Script errors out on first install (no prior label exists)
**Mitigation:** Guard bootout with `launchctl list | grep -q "$LABEL"` check (already done in current code)

### Risk 2: Different uid on different machines
**Impact:** `id -u` could theoretically differ
**Mitigation:** Use `$(id -u)` dynamically in all scripts; never hardcode uid

## No-Gos (Out of Scope)

- Converting `launchctl list` to `launchctl print` -- not needed, list works
- Fixing bridge/watchdog plist management -- separate concern
- Adding actual launchctl integration tests -- too risky in CI, stick with content checks

## Update System

The update system IS the affected code. `scripts/remote-update.sh` is one of the three files being fixed. After this change, the update script will correctly reload reflections on all machines. No new dependencies or config files. Existing installations will self-heal on next `/update` since the update script itself is what gets pulled and re-executed.

## Agent Integration

No agent integration required -- this is a fix to shell scripts and a Python service utility. No MCP servers, bridge changes, or tool wrappers needed.

## Documentation

- [x] Update `docs/features/reflections.md` to use bootstrap/bootout in reload example
- [x] Verified `docs/features/bridge-self-healing.md` does not reference launchctl load/unload (no changes needed)
- [x] Verified `CLAUDE.md` emergency recovery section does not reference deprecated commands (no changes needed)
- [x] No new feature docs needed -- this is a bug fix to existing infrastructure

## Success Criteria

- [ ] `install_reflections.sh` uses `launchctl bootstrap/bootout` (not `load/unload`)
- [ ] `remote-update.sh` uses `launchctl bootstrap/bootout` with visible error output (no `|| true`)
- [ ] `service.py` `install_reflections()` uses correct subprocess commands
- [ ] `service.py` `install_caffeinate()` uses correct subprocess commands
- [ ] Existing tests in `TestInstallScript` updated to assert bootstrap/bootout
- [ ] New `TestInstallMechanism` tests pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (launchctl-fix)**
  - Name: launchctl-builder
  - Role: Replace deprecated launchctl commands in all three files and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (launchctl-fix)**
  - Name: launchctl-validator
  - Role: Verify all launchctl load/unload references are gone, tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix shell scripts
- **Task ID**: build-shell-scripts
- **Depends On**: none
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `launchctl unload` with `launchctl bootout gui/$(id -u)/<label>` in `install_reflections.sh`
- Replace `launchctl load` with `launchctl bootstrap gui/$(id -u) <plist>` in `install_reflections.sh`
- Same replacements in `remote-update.sh`
- Remove `|| true` from launchctl calls in `remote-update.sh`, add error echo

### 2. Fix Python service module
- **Task ID**: build-python-service
- **Depends On**: none
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `install_reflections()` in `service.py` to use `bootout`/`bootstrap`
- Update `install_caffeinate()` in `service.py` to use `bootstrap`

### 3. Update and add tests
- **Task ID**: build-tests
- **Depends On**: build-shell-scripts, build-python-service
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `TestInstallScript.test_install_script_references_plist` to assert `bootstrap`/`bootout`
- Add `TestInstallMechanism` class with tests for:
  - `install_reflections.sh` uses `launchctl bootstrap` and `launchctl bootout`
  - `remote-update.sh` uses `launchctl bootstrap` and `launchctl bootout`
  - `service.install_reflections()` Python function uses correct commands (read source and assert)

### 4. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: launchctl-validator
- **Agent Type**: validator
- **Parallel**: false
- `grep -r "launchctl load\|launchctl unload" scripts/ scripts/update/service.py` returns nothing
- `grep -r "launchctl bootstrap\|launchctl bootout" scripts/ scripts/update/service.py` returns matches
- `pytest tests/test_reflections_scheduling.py -v` passes
- `ruff check . && ruff format --check .` passes

## Validation Commands

- `grep -rn "launchctl load\|launchctl unload" scripts/ scripts/update/service.py` -- must return zero matches
- `grep -rn "launchctl bootstrap\|launchctl bootout" scripts/ scripts/update/service.py` -- must return matches in all three files
- `pytest tests/test_reflections_scheduling.py -v` -- all tests pass
- `ruff check . && ruff format --check .` -- clean lint
