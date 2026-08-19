# Local Doctor Tool

Unified health check CLI that consolidates scattered environment checks into a single `python -m tools.doctor` command. Runs all checks, prints a pass/fail report with actionable fix suggestions, and exits with an appropriate status code.

## Motivation

Health checks were spread across four separate locations:

- `monitoring/health.py` -- Redis, Telegram bridge status, disk space, API key presence
- `scripts/update/verify.py` -- Python deps, system tools, Telegram session auth, SDK auth, MCP servers
- `scripts/update/service.py` -- bridge/worker running status
- `monitoring/resource_monitor.py` -- memory/CPU/disk monitoring

Developers discovered broken environments mid-task instead of upfront. The doctor tool surfaces all issues before work begins.

## Usage

```bash
python -m tools.doctor           # Run all standard checks
python -m tools.doctor --quick   # Skip slow checks (Telegram session, model verification)
python -m tools.doctor --quality # Include ruff lint, ruff format, pytest
python -m tools.doctor --json    # Machine-readable JSON output
python -m tools.doctor --install-hook  # Install git pre-push hook
```

## Check Categories

| Category | Checks | Source |
|----------|--------|--------|
| Environment | Python version, system tools, Python deps, dev tools | `scripts/update/verify.py` |
| Services | Redis connectivity, bridge running, worker running | `monitoring/health.py`, `scripts/update/service.py` |
| Auth | Telegram session, API keys, SDK auth | `scripts/update/verify.py`, `monitoring/health.py` |
| Resources | Disk space | `monitoring/health.py` |
| Quality | Ruff lint, ruff format, pytest (opt-in via `--quality`) | subprocess |

## Console-Script Resolution and Interpreter Check

`_check_console_scripts_resolve` (`tools/doctor.py`) is the console script
health check. It verifies every name declared
in `pyproject.toml`'s `[project.scripts]` table -- `valor-tts`, `valor-session`,
`critique-roster-check`, and 23 others. Skills, hooks, and SDLC gates invoke
these by bare name (`critique-roster-check`, `critique-resume-probe`, and
`sdlc-push-guard` are fail-closed *gates* called that way), so what a bare name
actually resolves to -- and what the resolved file's shebang actually binds to
-- is load-bearing, and both are pure host state no amount of correct packaging
can guarantee. The check has two parts, run together on every invocation
including `--quick`.

### Part 1: resolution (#2566, #2665)

For each declared name, `shutil.which()` finds the winning PATH entry and the
check classifies it into one of three states, each with its own remedy:

| State | Cause | Remedy |
|-------|-------|--------|
| Resolves into a repo venv bin dir | Healthy | none |
| Resolves outside every repo venv (a stale `~/Library/Python/3.12/bin` shim, for example) | A stale shim wins the PATH race; running it dies with `ModuleNotFoundError: No module named 'tools'` | Put the repo venv first on PATH |
| Does not resolve at all, or resolves to a name never built into the venv | `[project.scripts]` grew a new entry the puller has not synced | `uv sync` |

A hardlinked copy of the venv file (same inode, anywhere on PATH) is accepted
as healthy -- `_same_file` treats it as the same script, not a wrong one.

### Part 2: interpreter identity (#2748)

Resolving into the right *directory* says nothing about whether the winning
file's shebang binds to a real interpreter. For every name that resolves, the
check opens the winning file, reads its shebang, and classifies the target:

| Reason | Meaning | Fires when |
|--------|---------|------------|
| `ok` | Interpreter exists, is in a repo venv, and matches the `.python-version` pin (or the pin is unresolvable) | the healthy case |
| `missing` | The shebang target does not exist (a retired uv-managed download, a broken symlink) | always, pin or no pin |
| `off-pin` | The shebang target belongs to a repo venv, but that venv's `pyvenv.cfg` version differs from the pin | only when the pin resolves |
| `outside` | The shebang target is outside every repo venv bin dir -- no editable install of this repo | always, pin or no pin |
| `unverified` | The shebang could not be classified (see below) | neither a pass nor a finding |

Only a **plain absolute shebang** (`#!/path/to/python`, optionally with
interpreter flags that are discarded) is classified. Every other shape --
the `/bin/sh` two-line polyglot pip/distlib emit once the interpreter path
exceeds the kernel's shebang length cap, `uv venv --relocatable`'s
`dirname $0` variant, an `env`-mediated shebang, a shebang-less binary, or an
unreadable/undecodable file -- is `unverified`: neither a pass nor a finding.
None of these forms occurs in this fleet today, so parsing them would add
untested surface to exactly the check whose own risk is a false accusation.

Findings sharing one `(reason, target)` collapse into a single line naming
the target, the reason, and the affected script names, rather than one line
per script. A script accepted onto PATH via a stale hardlinked copy gets an
extra remedy clause naming that copy, since a plain venv rebuild leaves it
still winning the PATH race with the old shebang.

**Fail-open scoping.** An unresolvable `.python-version` pin disables only
the `off-pin` comparison -- `missing` and `outside` still fire with no pin,
and the pass/failure message discloses the skip with
`(pin unresolvable; off-pin comparison skipped)`. A venv whose own version
cannot be read (`pyvenv.cfg` missing or unparseable) is `unverified`, never
compared against the pin, so it never renders a false `is Python None`
accusation and is excluded from the pass message's verified count.

The pass message discloses how many scripts were interpreter-verified, as a
ratio (`N of M interpreter-verified`) -- a run that verified nothing cannot
read as a clean bill of health, and the ratio drops visibly whenever a host
carries an unclassifiable shebang form.

No subprocess is spawned and nothing is written: existence, venv membership,
and version all come from filesystem reads (`Path.exists()`, `pyvenv.cfg`),
matching `_check_worktree_interpreters`'s reference-vs-drift check
(`tools/doctor.py`), which this check deliberately reuses rather than
duplicates. The two checks can both fire on the same drifted venv -- when
they do, they prescribe the identical `rm -rf .venv && uv sync --all-extras`
remedy, so one action clears both findings.

## Flags

| Flag | Behavior |
|------|----------|
| `--quick` | Skips slow checks: Telegram session auth probe and `verify_models()` |
| `--quality` | Adds code quality checks: ruff lint, ruff format --check, pytest |
| `--json` | Outputs structured JSON instead of the text report |
| `--install-hook` | Writes a `.git/hooks/pre-push` script that runs `python -m tools.doctor --quick` |

## Output

### Text Report (default)

Each check prints a status line with a pass/fail indicator, the check name, and a message. Failed checks include an actionable fix suggestion indented below.

Exit code 0 when all checks pass, 1 when any check fails.

### JSON Output (`--json`)

```json
{
  "passed": false,
  "checks": [
    {
      "name": "Redis",
      "category": "Services",
      "passed": true,
      "message": "Connected",
      "fix": null
    }
  ],
  "summary": {
    "total": 12,
    "passed": 11,
    "failed": 1
  }
}
```

## Architecture

- **Single file**: `tools/doctor.py` with `tools/__main__.py` support
- **Read-only**: Observes system state, never mutates it
- **Reuse over duplication**: Wraps existing check functions from `monitoring/` and `scripts/update/`
- **Graceful degradation**: Each check is wrapped in try/except; one failure does not crash the run
- **Timeouts**: Each check has a default timeout to prevent hanging

## Git Pre-Push Hook

Running `python -m tools.doctor --install-hook` writes a `.git/hooks/pre-push` script that runs `python -m tools.doctor --quick` before every push. This catches environment issues before code leaves the local machine.

## Related

- [Plan document](../plans/local-doctor.md)
- [GitHub Issue #855](https://github.com/tomcounsell/ai/issues/855)
- `monitoring/health.py` -- HealthChecker class
- `scripts/update/verify.py` -- Environment verification functions
- `tools/doctor.py` -- Implementation
- `tests/unit/test_doctor.py` -- Unit tests
