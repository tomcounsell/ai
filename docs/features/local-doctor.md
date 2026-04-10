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
