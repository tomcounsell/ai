# Env Completeness Validation

Detects missing environment variables during update runs by comparing the live `.env` file against the canonical `.env.example` declaration list.

## Problem

New features frequently add variables to `.env.example`, but existing machines' vault `.env` files are never automatically alerted. The gap is silent — no warning appears during `scripts/update/run.py --verify`. Operators discover missing variables only when runtime errors occur.

## How It Works

The completeness check runs automatically as part of `python scripts/update/run.py --verify` and `--full`. It performs three steps:

1. **Parse `.env.example`**: Reads all `KEY=` declarations and extracts the immediately preceding comment block. The last non-empty, non-separator comment line above each key is used as the description.
2. **Parse live `.env`**: Reads all keys present in `.env`, treating blank values (`KEY=`) as present. The `.env` file is a symlink to `~/Desktop/Valor/.env`.
3. **Diff**: Keys declared in `.env.example` but absent from `.env` are surfaced as a `WARN` line.

### Output Format

When keys are missing, the verify output shows:

```
[update]   WARN: env-completeness: 2 missing: REDIS_URL (Redis connection URL); OPENROUTER_API_KEY (OpenRouter API Key)
```

When all keys are present:

```
(no output — check passes silently)
```

The check appears in `result.valor_tools` in the `VerificationResult`. Missing keys are also appended to `result.warnings` so they appear in the final `FAILED with N error(s)` count.

## .env.example Comment Convention

Every `KEY=` declaration in `.env.example` must be preceded by at least one comment line. The convention:

```
# Short description of what this controls (required/optional, default if unset, where to get it)
KEY_NAME=placeholder-value
```

For multi-line blocks, the last non-empty comment line is used as the description:

```
# Prefix used for all macOS launchd Label fields.
# Changing this after install requires uninstall + reinstall.
# The canonical Valor install uses com.valor.
SERVICE_LABEL_PREFIX=com.valor
```

Section separator lines (`# ======...`) are ignored by the parser and do not contribute to descriptions.

## Interpreting Warnings

A `WARN: env-completeness:` line means your vault `.env` is missing one or more declared variables. For each missing variable:

1. **Check the description** — it tells you what the variable controls and whether it's optional.
2. **Add optional vars** if you want the feature they enable (e.g., `STRIPE_API_KEY` for the payment skill). Omitting optional vars is fine; the warning is informational.
3. **Add required vars** before running dependent services. Missing required vars cause runtime errors.

To add a variable: edit `~/Desktop/Valor/.env` directly (the vault), then run `--verify` again to confirm the warning clears.

## Graceful Degradation

The check never crashes the update run:

- **`.env` not found** — returns `skipped (.env not found)`. Expected on a fresh machine before vault sync.
- **`.env.example` not found** — returns `skipped (.env.example not found)`.
- **`OSError` reading either file** — returns `skipped (read error)`. Covers TCC permission errors and iCloud eviction.

## Implementation

**`scripts/update/verify.py`**:
- `_parse_env_example(path)` — extracts `(key, description)` pairs using a comment block accumulator
- `_parse_env_keys(path)` — returns the set of keys present in `.env` (blank values count as present)
- `check_env_completeness(project_dir)` — orchestrates the comparison and returns a `ToolCheck`
- `verify_environment()` — calls `check_env_completeness()` and appends to `result.valor_tools`

**`scripts/update/run.py`**:
- New `valor_tools` loop after the `system_tools` loop (Step 6) surfaces `WARN:` lines for any `ToolCheck` with `available=False`

## Tests

`tests/unit/test_env_completeness.py` — 14 tests covering:
- Missing key detection with description extraction
- Blank-value tolerance
- Multiple missing keys (semicolon-separated)
- All-present happy path
- Skipped results for missing files
- OSError graceful recovery
- Multi-line comment block parsing
- Section separator exclusion
- Comment lines in `.env` not treated as keys
