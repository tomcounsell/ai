---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1140
last_comment_id:
---

# .env.example: Per-Variable Comments + Completeness Check

## Problem

The `.env.example` file is the canonical reference for all configurable environment variables in this project, but it serves two jobs poorly:

1. **Documentation quality is uneven.** Some variables have detailed inline comments; many are bare `KEY=value` lines. When someone adds a new variable, there is no structural prompt to document it — so knowledge about purpose, required/optional status, and how to obtain the value stays implicit.

2. **New machines silently miss new variables.** When features land and add new variables to `.env.example`, existing machines' vault `.env` files are never automatically alerted. The gap is silent — no warning appears during `scripts/update/run.py --verify`.

**Current behavior:**
- ~15 variables in `.env.example` have no comment guidance at all (bare `KEY=value` lines)
- `scripts/update/verify.py` checks tools, deps, and SDK auth but never compares `.env` against `.env.example`
- Missing keys in `.env` cause silent failures or confusing runtime errors rather than clear warnings

**Desired outcome:**
- Every variable in `.env.example` has at least one descriptive comment line
- During `python scripts/update/run.py --verify`, missing keys are surfaced as `WARN` with the variable name and its description from `.env.example`
- Blank values in `.env` are treated as present (valid for optional vars using system defaults)

## Freshness Check

**Baseline commit:** `9142dade3e69d448b902f89773256188a831ed53`
**Issue filed at:** 2026-04-23T06:09:48Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/update/verify.py` — `verify_environment()` function exists, no `.env.example` parsing present — still holds
- `.env.example` — bare `KEY=value` lines confirmed at lines 80-82 (ANTHROPIC_API_KEY), 84 (OPENAI_API_KEY), 86 (PERPLEXITY_API_KEY), and REFLECTIONS_AUTO_FIX_ENABLED at line 139 — still holds

**Cited sibling issues/PRs re-checked:**
- No siblings cited in issue body

**Commits on main since issue was filed (touching referenced files):**
- None — `scripts/update/verify.py`, `scripts/update/run.py`, and `.env.example` are unchanged since filing

**Active plans in `docs/plans/` overlapping this area:** none — no active plans touch the update system or env configuration

**Notes:** The issue's count of "~20 of ~35 variables" was directionally correct — the actual file has ~35 variables, of which approximately 12–15 are bare or lightly commented. The plan targets all underdocumented lines.

## Prior Art

No prior issues found related to this work. Searched "env completeness verify" and "env.example completeness" in closed issues and merged PRs — no results.

## Research

No relevant external findings — this is a purely internal chore involving Python stdlib (`re`, `pathlib`) parsing of a local file. No external libraries or ecosystem patterns are involved.

## Data Flow

The completeness check flows as:

1. **Entry point**: `python scripts/update/run.py --verify` invokes `verify_environment()`
2. **`check_env_completeness(project_dir)`** (new function in `scripts/update/verify.py`):
   - Reads `.env.example` line by line, extracts all `KEY=` declarations and their immediately preceding comment line(s)
   - Reads the live `.env` file (which is a symlink to `~/Desktop/Valor/.env`), extracts all present keys (whether or not blank)
   - Diffs: declared keys minus present keys = missing keys
3. **Result**: Returns a `ToolCheck`-style result. Missing keys yield `available=False` with `error` listing each missing key and its description from `.env.example`
4. **`verify_environment()`**: Appends the completeness `ToolCheck` to `result.valor_tools` (or a new `env_checks` list — see Technical Approach)
5. **`run_update()`** in `run.py`: The existing verification reporting loop already prints `WARN` for tools with `available=False` — no changes to `run.py` needed beyond the `verify_environment()` call receiving the new check

## Architectural Impact

- **New dependencies**: None — only Python stdlib (`re`, `pathlib`, `os`)
- **Interface changes**: `verify_environment()` gains one more check in `result.valor_tools`; `VerificationResult` is unchanged (check attaches to existing `valor_tools` list)
- **Coupling**: No coupling increase — the new check reads two local files and returns a `ToolCheck` value object
- **Data ownership**: `.env.example` is already the canonical env var source of truth; this plan adds a runtime reader, not a new owner
- **Reversibility**: Trivially reversible — remove one function call in `verify_environment()`

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It reads local files using Python stdlib.

## Solution

### Key Elements

- **`.env.example` annotation pass**: Every bare `KEY=value` line gets a short comment block (1–3 lines) added above it. Existing detailed comments are preserved as-is.
- **`check_env_completeness()`**: New function in `scripts/update/verify.py` that parses `.env.example` for declared variables (lines matching `^[A-Z_]+=`), extracts the comment line immediately above each key, then compares against keys present in `.env`. Returns a `ToolCheck` per missing key — or a single `ToolCheck` summarizing all missing keys.
- **Wire into `verify_environment()`**: Call `check_env_completeness()` and append result to `result.valor_tools` (using `available=True` if no missing keys, or `available=False` with error listing missing keys if gaps exist)
- **Unit test**: New test in `tests/unit/test_env_completeness.py` using a tmp-dir fixture with a minimal `.env.example` and a partial `.env` to verify parsing, missing-key detection, and blank-value treatment

### Flow

`run.py --verify` → `verify_environment()` → `check_env_completeness()` → reads `.env.example` → reads `.env` → diffs keys → returns `ToolCheck` → appended to `valor_tools` → existing reporting loop prints `WARN` for missing keys

### Technical Approach

**Parsing `.env.example`**:
```python
import re
KEY_RE = re.compile(r'^([A-Z][A-Z0-9_]*)=')

def _parse_env_example(path: Path) -> list[tuple[str, str]]:
    """Returns list of (key, description) pairs."""
    lines = path.read_text().splitlines()
    result = []
    pending_comment = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            pending_comment = stripped.lstrip('#').strip()
        elif m := KEY_RE.match(stripped):
            key = m.group(1)
            result.append((key, pending_comment))
            pending_comment = ""
        else:
            pending_comment = ""  # blank line resets comment accumulation
    return result
```

**Parsing live `.env`** (keys only, tolerating blank values):
```python
def _parse_env_keys(path: Path) -> set[str]:
    """Returns set of all keys present in .env (blank values are present)."""
    keys = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key = line.split('=', 1)[0].strip()
            if KEY_RE.match(key + '='):
                keys.add(key)
    return keys
```

**`check_env_completeness()` return**: Returns a single `ToolCheck`:
- `available=True, version="all {N} vars present"` when `.env` contains all declared keys
- `available=False, error="Missing: KEY1 (description), KEY2 (description), ..."` when gaps exist
- `available=True, version="skipped (.env not found)"` when `.env` doesn't exist (new machine before vault sync)

The single-`ToolCheck` approach avoids flooding the verify output with N individual checks for a new machine that is legitimately missing many optional vars. The description from the immediately preceding comment gives the operator enough context to decide whether to add the var.

## Failure Path Test Strategy

### Exception Handling Coverage
- The new `check_env_completeness()` function must never crash `verify_environment()`. Wrap the entire function body in `try/except OSError` — if either file is unreadable (TCC, iCloud eviction), return `ToolCheck(name="env-completeness", available=True, version="skipped (read error)")` so the update run continues. This matches the existing pattern in `check_sdk_auth()`.
- Test: `test_env_completeness.py::test_unreadable_env_returns_skipped` — mock `Path.read_text` to raise `OSError`, assert the function returns a skipped `ToolCheck` rather than propagating the exception.

### Empty/Invalid Input Handling
- `.env.example` with no `KEY=` lines → function returns `ToolCheck(available=True, version="0 vars declared")` (no-op)
- `.env` with only blank-value entries (`KEY=`) → all those keys are considered present (correct behavior per AC)
- `.env` with Windows CRLF line endings → `splitlines()` handles this transparently
- Test: `test_env_completeness.py::test_blank_values_are_present` — `.env` with `REDIS_URL=` (blank) and `.env.example` with `REDIS_URL=redis://...` → no missing key reported

### Error State Rendering
- Missing keys surface as `WARN` in the existing reporting loop in `run.py` — no UI change needed. The error string in `ToolCheck.error` is displayed verbatim: `"Missing: REDIS_URL (Redis connection URL), ANTHROPIC_API_KEY (Anthropic API key)"`.

## Test Impact

No existing tests are affected — this is a purely additive change. The new function `check_env_completeness()` is a new leaf function with no callers to existing tests. `verify_environment()` gains one additional `ToolCheck` in `valor_tools`, but no existing test asserts the exact count or membership of `valor_tools`.

Check existing test for any `valor_tools` assertions:
- `tests/unit/` — no test file covering `verify.py` exists today; `test_update_log_rotate_agent.py` tests the log-rotate install path, not `verify_environment()`.

## Rabbit Holes

- **Required vs. optional classification**: Tempting to classify each variable as required/optional and surface errors vs. warnings per variable. This adds complexity (how do we store the classification?) and creates maintenance burden. The issue explicitly says "surface as WARN, not hard failure" — that's the scope.
- **Grouping by section**: The update output could group missing vars by `.env.example` section (Telegram, API Keys, etc.). Nice to have, not needed for the AC.
- **Suggesting where to get the value**: The comment text sometimes includes "get from my.telegram.org". We could parse URLs from comments and include them in the warning. Rabbit hole — the comment text is already included in the error output.
- **`.env.example` linting**: Adding a CI check that enforces every new `KEY=` has a preceding comment. Out of scope for this issue — good candidate for a follow-on chore.

## Risks

### Risk 1: `.env` is a symlink to iCloud-synced vault; read may fail with TCC/PermissionError
**Impact:** `check_env_completeness()` raises an exception, crashing `verify_environment()` on machines where the vault is TCC-restricted or iCloud hasn't synced.
**Mitigation:** Wrap the function body in `try/except OSError` and return a `ToolCheck` with `version="skipped (read error)"` — identical to the existing pattern in `check_sdk_auth()`. The update run continues cleanly.

### Risk 2: Comment parsing is fragile if the `.env.example` format drifts
**Impact:** New variables added with multi-line comment blocks or unusual formatting may get blank descriptions in the warning output.
**Mitigation:** The parser only needs the immediately preceding non-blank comment line. Blank descriptions degrade gracefully ("Missing: KEY (no description)"). The unit test fixture exercises the multi-line comment case. This is tolerable — perfect parsing is a rabbit hole.

## Race Conditions

No race conditions identified — the check reads two static files synchronously. No shared mutable state is involved.

## No-Gos (Out of Scope)

- Per-variable required/optional classification with different severity levels
- A CI enforcement hook that blocks commits with undocumented variables in `.env.example`
- Auto-generating vault `.env` entries for missing optional vars
- `.env.example` syntax validation beyond comment/key extraction
- Updating the update *skill* (`.claude/skills/update/`) — the check is internal to `verify.py`

## Update System

The new `check_env_completeness()` function runs automatically during `scripts/update/run.py --verify` and `--full`. No changes to the update script, skill, or launchd plists are needed — the check attaches to the existing `verify_environment()` call which is already wired into every update mode.

New machines will benefit automatically on first update after this lands — the check will surface any variables their vault `.env` is missing (expected on a fresh install before full vault sync).

## Agent Integration

No agent integration required — this is an update-system internal change. The agent invokes `/update` via the skill which calls `scripts/update/run.py`; the new check surfaces in the existing `WARN:` lines that the skill already reports. No MCP changes needed.

## Documentation

- [ ] Update `docs/features/env-completeness-validation.md` — new feature doc describing the completeness check behavior, how to interpret warnings, and the `.env.example` comment convention
- [ ] Add entry to `docs/features/README.md` index table for the new feature doc

## Success Criteria

- [ ] Every variable in `.env.example` has at least one descriptive comment line above it
- [ ] `scripts/update/verify.py::check_env_completeness()` exists and parses `.env.example` for declared keys
- [ ] Running `python scripts/update/run.py --verify` with a `.env` missing a declared key surfaces `WARN: env-completeness: Missing: KEY_NAME (description)`
- [ ] Blank values in `.env` (`KEY=`) are treated as present — no false warning
- [ ] `.env` not found returns a skipped result (no exception)
- [ ] `tests/unit/test_env_completeness.py` passes covering: missing key detection, blank-value tolerance, unreadable-file graceful skip
- [ ] Tests pass (`pytest tests/unit/test_env_completeness.py`)
- [ ] Lint/format clean (`python -m ruff check . && python -m ruff format --check .`)

## Team Orchestration

### Team Members

- **Builder (env-completeness)**
  - Name: env-builder
  - Role: Add comments to `.env.example`, implement `check_env_completeness()` in `verify.py`, wire into `verify_environment()`, write unit test
  - Agent Type: builder
  - Resume: true

- **Validator (env-completeness)**
  - Name: env-validator
  - Role: Verify all AC are met, run tests, confirm no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian (env-completeness)**
  - Name: env-documentarian
  - Role: Create `docs/features/env-completeness-validation.md` and update README index
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Annotate `.env.example` with per-variable comments
- **Task ID**: build-env-example-comments
- **Depends On**: none
- **Validates**: manual review — every KEY= line must have a preceding comment
- **Assigned To**: env-builder
- **Agent Type**: builder
- **Parallel**: true
- Read all lines of `.env.example` and identify bare `KEY=value` lines with no preceding comment
- Add 1–3 comment lines above each bare line explaining: what it controls, required/optional, default if unset, where to obtain it
- Preserve all existing comments exactly as-is
- Ensure the file ends with a newline

### 2. Implement `check_env_completeness()` in `scripts/update/verify.py`
- **Task ID**: build-completeness-check
- **Depends On**: none
- **Validates**: `tests/unit/test_env_completeness.py` (create)
- **Assigned To**: env-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_parse_env_example(path: Path) -> list[tuple[str, str]]` helper — returns `(key, description)` pairs
- Add `_parse_env_keys(path: Path) -> set[str]` helper — returns set of all keys present in `.env` (including blank-value entries)
- Add `check_env_completeness(project_dir: Path) -> ToolCheck` — compares declared keys vs. present keys, returns a single `ToolCheck`
- Wrap the body in `try/except OSError` — return `ToolCheck(name="env-completeness", available=True, version="skipped (read error)")` on failure
- Wire into `verify_environment()`: call `check_env_completeness(project_dir)` and append to `result.valor_tools`

### 3. Write unit tests for `check_env_completeness()`
- **Task ID**: build-tests
- **Depends On**: build-completeness-check
- **Validates**: `tests/unit/test_env_completeness.py`
- **Assigned To**: env-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_env_completeness.py` with fixture tmp-dir helper
- `test_missing_key_reported`: `.env.example` declares `REDIS_URL=`, `.env` missing it → `available=False, "Missing: REDIS_URL"`
- `test_blank_value_is_present`: `.env` has `REDIS_URL=` (blank) → no missing key, `available=True`
- `test_all_present`: `.env` has all declared keys → `available=True, version contains "all"`
- `test_env_not_found_returns_skipped`: `.env` does not exist → `available=True, version contains "skipped"`
- `test_unreadable_env_returns_skipped`: `.env.example` read raises `OSError` → `available=True, version contains "skipped"`
- `test_description_extracted_from_comment`: comment above key → description appears in `ToolCheck.error`

### 4. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-env-example-comments, build-completeness-check, build-tests
- **Assigned To**: env-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_env_completeness.py -v` — must pass
- Run `python -m ruff check scripts/update/verify.py tests/unit/test_env_completeness.py` — must be clean
- Run `python -m ruff format --check scripts/update/verify.py tests/unit/test_env_completeness.py` — must be clean
- Manually inspect `.env.example` — confirm every `KEY=` line has a preceding comment
- Run `python scripts/update/run.py --verify` against local env — verify no exceptions, verify output contains env-completeness result
- Confirm `verify_environment()` returns a `VerificationResult` with the new check in `valor_tools`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: env-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/env-completeness-validation.md` — describe the check, what triggers it, how to interpret WARN output, the `.env.example` comment convention
- Add entry to `docs/features/README.md` index table: `env-completeness-validation` row

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_env_completeness.py -v` | exit code 0 |
| All unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update/verify.py tests/unit/test_env_completeness.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update/verify.py tests/unit/test_env_completeness.py` | exit code 0 |
| verify.py has new function | `grep -n "check_env_completeness" scripts/update/verify.py` | output contains "check_env_completeness" |
| .env.example has no bare KEY= lines | `python -c "import re; lines=open('.env.example').read().splitlines(); bare=[l for i,l in enumerate(lines) if re.match(r'^[A-Z][A-Z0-9_]*=', l) and (i==0 or not lines[i-1].startswith('#'))]; print(bare); assert not bare, f'Bare lines: {bare}'"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — scope is fully locked by the issue acceptance criteria.
