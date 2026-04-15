# Contributing to Valor

Welcome. Valor is an autonomous AI coworker that owns its own machine and does real work. Contributions that extend its capabilities, fix bugs, or improve reliability are appreciated.

This guide covers everything you need to open your first PR in under 30 minutes.

## Table of Contents

1. [Prerequisites and Setup](#prerequisites-and-setup)
2. [Branch and PR Process](#branch-and-pr-process)
3. [Code Style](#code-style)
4. [Type Checking](#type-checking)
5. [Tests](#tests)
6. [Commit Conventions](#commit-conventions)
7. [Extending the System](#extending-the-system)
8. [Getting Help](#getting-help)

---

## Prerequisites and Setup

Follow the Quick Start in [README.md](README.md) to get a working local environment. You will need Python 3.11+, `uv`, and a `.env` file with secrets.

```bash
pip install -e .
```

All dev dependencies (ruff, mypy, pytest) are included.

---

## Branch and PR Process

1. **Create a branch** from `main` using the `session/{slug}` naming convention:
   ```bash
   git checkout -b session/my-feature
   ```
2. **Open an issue first** for non-trivial work. The issue is the source of truth for scope.
3. **Open a PR** against `main`. Use the PR body to summarize changes, link the issue (`Closes #N`), and list what was tested.
4. **All checks must pass** before merge: ruff, mypy (if applicable), and pytest.
5. **One reviewer** is sufficient for most PRs.

PR body format:
```
## Summary
Brief description of what changed and why.

## Changes
- bullet points

## Testing
- [ ] Unit tests passing
- [ ] Integration tests passing

Closes #N
```

---

## Code Style

Valor uses [Ruff](https://docs.astral.sh/ruff/) for formatting and linting. All configuration lives in [`pyproject.toml`](pyproject.toml) under `[tool.ruff]` and `[tool.ruff.lint]`.

```bash
python -m ruff format .        # Format all files
python -m ruff check .         # Lint all files
python -m ruff check --fix .   # Auto-fix fixable issues
```

A pre-commit hook runs ruff automatically on final commits — it auto-fixes what it can and blocks on genuine errors. Use `--no-verify` during WIP commits only.

---

## Type Checking

Valor uses [mypy](https://mypy.readthedocs.io/) for static type checking on core modules. Configuration lives in [`pyproject.toml`](pyproject.toml) under `[tool.mypy]`.

```bash
python -m mypy agent/ tools/ bridge/
```

New code in `agent/`, `tools/`, and `bridge/` should be fully typed.

---

## Tests

Test infrastructure is documented in [`tests/README.md`](tests/README.md), including the full marker table and feature coverage map.

**Running tests:**
```bash
pytest tests/unit/ -n auto     # Unit tests in parallel (~60s)
pytest tests/integration/      # Integration tests (requires live APIs)
pytest tests/                  # Full suite
pytest -m sdlc                 # Tests for a specific feature marker
```

**Quality gates:**
- Unit: 100% pass rate required
- Integration: 95% pass rate required
- E2E: 90% pass rate required

**Testing philosophy:** No mocks for external services — use real APIs. Use AI judges for intelligence validation, not keyword matching.

**New tests** should use existing feature markers (see `tests/README.md`) and live under the appropriate subdirectory (`unit/`, `integration/`, `e2e/`).

**CHANGELOG:** PRs introducing notable features should include a new entry in [`CHANGELOG.md`](CHANGELOG.md) under `[Unreleased]`.

---

## Commit Conventions

Commits should be detailed and focused. Follow this format:

```
type(scope): short description (#issue-number)

Optional longer body explaining why the change was made.
```

**Types:** `feat`, `fix`, `chore`, `docs`, `test`, `refactor`

**Examples:**
```
feat(memory): add status subcommand to memory_search CLI (#970)
fix(bridge): conversation terminus detection to break bot reply loops (#969)
chore: remove completed plan after PR merge
```

**Rules:**
- Use imperative mood in the subject line ("add", not "added")
- Keep the subject line under 72 characters
- Reference the issue number when applicable
- Do not include co-author trailers

---

## Extending the System

### Adding a Skill

Skills live in [`.claude/skills/`](.claude/skills/). Each skill is a directory with a `SKILL.md` entry point.

1. Create `.claude/skills/my-skill/SKILL.md`
2. Register it in [`.claude/settings.local.json`](.claude/settings.local.json) if it needs special permissions
3. Document it in [`docs/features/`](docs/features/)

### Adding a Tool

Python tools live in [`tools/`](tools/). Tools callable by the agent must also be wired into an MCP server.

1. Add your tool logic in `tools/my_tool.py`
2. Expose it via an MCP server in `mcp_servers/` and register in [`.claude/settings.json`](.claude/settings.json)
3. Add integration tests in `tests/integration/`

### Adding a Bridge

Comms bridges live in [`bridge/`](bridge/). Each bridge connects an external channel (Telegram, Email, etc.) to the session queue.

1. Implement the bridge following the pattern in `bridge/telegram_bridge.py`
2. Register output handlers using the `OutputHandler` protocol in `agent/output_handler.py`
3. Add configuration to `config/settings.py`

---

## Getting Help

- **Architecture and working guide:** [CLAUDE.md](CLAUDE.md)
- **Feature documentation:** [docs/features/README.md](docs/features/README.md)
- **Test suite index:** [tests/README.md](tests/README.md)
- **Open an issue:** [GitHub Issues](https://github.com/tomcounsell/ai/issues)
