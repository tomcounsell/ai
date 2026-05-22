---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-05-13
tracking: https://github.com/tomcounsell/ai/issues/1350
last_comment_id:
revision_applied: true
---

# Widen Agent-Definition Fallback to Cover Malformed YAML and OS Errors

## Problem

The "agent-definition fallback" was built to prevent session crashes when `.claude/agents/*.md` files are absent. Today it only catches the missing-file case. If a file exists but has malformed YAML, no frontmatter, a permission error, or an encoding error, the underlying exception (`ValueError` from `re.match` not matching, or `OSError`/`PermissionError` from `read_text`) propagates back through `get_agent_definitions()` to `_create_options()` and kills the session — exactly what the feature was supposed to prevent.

The feature doc reads as if any agent-file problem degrades gracefully, so an operator hitting a malformed file would (reasonably) be surprised when the session dies anyway.

**Current behavior:**
- Missing file (`path.exists() == False`) → fallback dict, session continues.
- File exists, no/malformed YAML frontmatter → `ValueError` raised at `agent/agent_definitions.py:64`, session dies.
- File exists but `read_text()` fails (perm denied, bad encoding) → `OSError`/`PermissionError`/`UnicodeDecodeError` raised at `agent/agent_definitions.py:59`, session dies.

**Desired outcome:**
All three failure modes produce the same fallback dict, with a warning log identifying the exception class name and the file path so operators can diagnose. `validate_agent_files()` extends from existence-only to a trial-parse so the same warnings surface at process startup, not just mid-run.

## Freshness Check

**Baseline commit:** 4c1c1888194953bfa06a7b0c244c5fe1311ad996
**Issue filed at:** 2026-05-08T22:14:42Z
**Disposition:** Unchanged (with minor drift noted)

**File:line references re-verified against current `agent/agent_definitions.py`:**
- `agent/agent_definitions.py:49` — `if not path.exists():` is the only fallback gate — **still holds**.
- `agent/agent_definitions.py:62-64` — `if not match: raise ValueError(...)` — **still holds**.
- `agent/agent_definitions.py:59` — unguarded `path.read_text(encoding="utf-8")` — **still holds**.
- `agent/agent_definitions.py:53-56` — fallback dict inline inside the `not path.exists()` branch — **still holds**.
- `agent/agent_definitions.py:133-145` — `validate_agent_files()` existence-only check — **still holds**.

**Cited sibling issues/PRs re-checked:**
- #539 — closed 2026-03-26 with the original missing-file fallback. Did not address parse or read errors. Direct precursor to this issue.

**Commits on main since issue was filed (touching referenced files):**
- `999ea688` docs: remove stale `_load_dev_session_prompt` reference — doc cleanup only. Irrelevant to behavior.
- `5c2375b6` fix(worker): call `validate_agent_files()` at startup — extends the startup-validation call site to the worker. Confirms `validate_agent_files()` is now invoked from both bridge AND worker `main()`. Relevant: any improvement to `validate_agent_files()` automatically benefits the worker too.
- `bf35e5e5` cleanup(agent): remove dead `get_definition` and stale `dev-session.md` — removes ~22 lines including the old `get_definition()` helper. Does not change `_parse_agent_markdown` or `validate_agent_files`. The current `_EXPECTED_AGENT_FILES` list contains exactly three files: `builder.md`, `validator.md`, `code-reviewer.md`.

**Active plans in `docs/plans/` overlapping this area:** None. `docs/plans/sdk_graceful_agent_fallback.md` is the closed parent plan; this plan widens its scope.

**Notes:** All referenced line numbers in the issue body still resolve correctly. No premise has changed.

## Prior Art

- **Issue #539** ([closed 2026-03-26](https://github.com/tomcounsell/ai/issues/539)): "SDK client crashes on missing agent definition files instead of degrading gracefully." Original feature that introduced `_parse_agent_markdown` missing-file fallback and `validate_agent_files()`. Successfully landed but scoped only to `path.exists()` check. This plan extends that scope.
- **PR #1353** (merged 2026-05-09): Added `validate_agent_files()` call to the worker startup path (it was previously only on the bridge). This means any extension to `validate_agent_files()` automatically applies on both processes.
- **PR #1354** (merged 2026-05-09): Doc-only cleanup removing a stale prior-art reference from the feature doc. Confirms the feature doc is the authoritative description of behavior.
- **PR #1360 / #1363** (merged 2026-05-13): Phase 5 follow-up cleanup removing `get_definition()` and `dev-session.md`. Reduces the agent registry to three agents (`builder`, `validator`, `code-reviewer`). Does not change the parse-failure surface.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Original #539 fallback | Caught `FileNotFoundError` via `path.exists()` check | Treated "missing file" as the only failure mode worth handling. Real-world failures include malformed YAML, permission errors, and encoding errors — none caught by `.exists()`. The fix solved the originally observed crash and stopped. |

**Root cause pattern:** The original implementation gated on the **specific cause** (file absent) rather than the **observed outcome** (parse failed). Defensive widening should pivot to "wrap the entire read+parse in a single try, fall through to the same fallback on any of the known exception classes."

## Research

No relevant external findings — this is purely internal Python error handling against a known callable surface in our own codebase. WebSearch would surface generic Python try/except patterns that add no signal beyond `logging.exception()` best practices we already follow.

## Data Flow

Skipped — change is isolated to one function (`_parse_agent_markdown`) with a small extension to a sibling (`validate_agent_files`). No multi-component flow to trace.

## Architectural Impact

- **New dependencies**: None. Pure stdlib (`logging`, `re`, `pathlib`).
- **Interface changes**: `_parse_agent_markdown` return shape is unchanged. `validate_agent_files()` may change from returning `list[str]` (missing paths) to a structured `list[tuple[str, str]]` of `(path, reason)` — *or* stay as `list[str]` if reason is logged eagerly. To preserve the established contract and avoid breaking PR #1353's test (`test_partial_missing` asserts `len(missing) == 2`), we keep the signature as `list[str]` of paths that failed any check (missing OR malformed OR unreadable). Reasons are logged, not returned.
- **Coupling**: No change. The module retains its single dependency on the agents directory.
- **Data ownership**: No change.
- **Reversibility**: Trivial revert — one file changed in `agent/`, two test files extended, one doc file updated. Pure-Python change, no migration, no state.

## Appetite

**Size:** Small

**Team:** Solo dev (builder)

**Interactions:**
- PM check-ins: 0 (issue is well-defined)
- Review rounds: 1 (code review on PR)

This is a single-function widening with clear acceptance criteria, ~50 LOC of production code plus ~80 LOC of tests. The coding cost is modest; the value comes from getting the test matrix exactly right.

## Prerequisites

No prerequisites — this work has no external dependencies. The change touches one production file (`agent/agent_definitions.py`), one test file (`tests/unit/test_agent_definitions.py`), and one doc file (`docs/features/agent-definition-fallback.md`). All run under the existing repo virtualenv.

## Solution

### Key Elements

- **Shared fallback constructor**: Extract the dict at `agent_definitions.py:51-57` into `_fallback_definition(path: Path, reason: str) -> dict` so the same shape is returned from every failure branch. The dict carries an explicit `"_is_fallback": True` marker so callers (notably `validate_agent_files`) can detect fallback dicts via key lookup rather than parsing the free-text `description`.
- **Unified try/except in `_parse_agent_markdown`**: Wrap the read+parse in a single `try`/`except (OSError, ValueError)` (keeping `path.exists()` as a fast-path check too) and route every failure to `_fallback_definition` with a reason string that names the exception class. `OSError` covers `FileNotFoundError`, `PermissionError`, and other I/O subclasses; `ValueError` covers the missing-frontmatter raise and `UnicodeDecodeError` (a `ValueError` subclass). An explicit comment in the source enumerates the covered subclasses for future readers.
- **Warning log on every fallback path**: Log via `logger.warning("Agent definition %s unusable (%s: %s) — using fallback prompt", path, exc.__class__.__name__, exc)`. Never swallow silently. `logger.warning` (not `logger.exception`) is intentional: these failures are *expected and handled*, and the path + exception class + str(exc) suffice for diagnosis. Operators needing a traceback can re-run with the log level temporarily raised.
- **Trial-parse in `validate_agent_files()`**: Only after confirming the file exists, attempt `_parse_agent_markdown` and check the returned dict for `"_is_fallback": True`. If set, push the path into the returned "problematic" list. Missing files take the existing existence-only branch (with their existing warning) and never reach the trial-parse call, so no warning is double-logged. We retain the existing return type of `list[str]` — reasons go to the log only.

### Flow

Worker / bridge startup → `validate_agent_files()` → list of problematic files logged as warnings → execution continues.

Session creation → `get_agent_definitions()` → `_parse_agent_markdown(path)` for each agent → fallback returned for any unreadable/malformed file → session starts with degraded prompts.

### Technical Approach

- Keep the function signature and the existing `path.exists()` fast-path. Wrap `path.read_text()` + frontmatter regex match in one try/except.
- Catch `(OSError, ValueError)` — covers `FileNotFoundError`, `PermissionError`, `UnicodeDecodeError` (all subclasses) plus the explicit `ValueError` raised for missing frontmatter. An inline comment in the source enumerates the covered subclasses so future readers see the surface without running `python -c "issubclass(...)"`.
- `_fallback_definition` accepts `path` and `reason` (short string suitable for the agent's `description` field, e.g. `"Fallback for unreadable {path.name}: {reason}"`) and stamps `"_is_fallback": True` on the returned dict.
- `validate_agent_files()` keeps its existing missing-file branch (`if not path.exists(): missing.append(...); continue`) intact so PR #1353's `test_partial_missing` log format and assertions remain valid. Only when the file exists does it call `_parse_agent_markdown` and inspect `result.get("_is_fallback")`; any True hit appends the path to the "problematic" list. We retain the existing return type of `list[str]`.
- The `_is_fallback` key is read by `validate_agent_files` only. Downstream consumers (`get_agent_definitions` and `AgentDefinition` construction) read `frontmatter` and `body`; they ignore extra keys, so no stripping step is needed.
- Update `docs/features/agent-definition-fallback.md` "Behavior" section to enumerate the three failure modes, the warning log format, and the startup trial-parse.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `try/except` block in `_parse_agent_markdown` has tests for each of: `FileNotFoundError` (via `path.exists() == False`, existing case), `ValueError` (missing frontmatter), `OSError`/`PermissionError` (via mock of `Path.read_text`), and `UnicodeDecodeError` (via a file with invalid UTF-8 bytes).
- [ ] Each test asserts both observable behavior (`logger.warning` emitted with the exception class name) and return shape (fallback dict).

### Empty/Invalid Input Handling
- [ ] Test: an agent file that is completely empty (`""`) — should fall back via `ValueError` ("No YAML frontmatter found").
- [ ] Test: an agent file that contains only whitespace — same expected outcome.
- [ ] Test: a file with valid `---\n---\n` but no body content — should NOT fall back (this is a valid empty-frontmatter case; body is just empty string). Confirms we don't accidentally widen to false positives.

### Error State Rendering
- N/A — no user-visible output from this code path. Failures surface as `logger.warning` to stderr/log files.

## Test Impact

- [ ] `tests/unit/test_agent_definitions.py::TestParseAgentMarkdown` — UPDATE: add three new tests for malformed-frontmatter, OSError-from-read, UnicodeDecodeError. Existing `test_missing_file_returns_fallback` and `test_missing_file_logs_warning` continue to pass unchanged.
- [ ] `tests/unit/test_agent_definitions.py::TestValidateAgentFiles::test_partial_missing` — UPDATE: extend to also assert that a malformed file in the same dir is reported alongside missing ones. Existing assertions remain valid.
- [ ] `tests/unit/test_agent_definitions.py::TestValidateAgentFiles::test_no_missing_files` — verify it still passes (no real agent files in the repo should be malformed).
- [ ] `tests/unit/test_agent_definitions.py::TestGetAgentDefinitions::test_returns_complete_dict_when_all_files_missing` — verify still passes; extend the patched `_AGENTS_DIR` scenario to also include one malformed file and assert that agent still gets the fallback.
- [ ] `tests/unit/test_worker_startup_validation.py` — no changes needed; the worker just calls `validate_agent_files()` regardless of return contents.

## Rabbit Holes

- **Inventing a structured error type / dataclass for fallback reasons** — overkill for ~5 call sites; a string reason in the fallback `description` field is enough.
- **Adding retries for transient OSErrors** — explicitly out of scope per the issue. Agent files are local disk reads; if they're failing, the operator needs the warning, not a retry loop.
- **Refactoring `validate_agent_files` to return a structured `list[ValidationProblem]`** — would break PR #1353's assertions and worker tests. Stay with `list[str]` of paths; reasons go to the log only.
- **Changing the regex or parser** — out of scope. The existing regex is fine; we are widening *error handling*, not parsing.
- **Adding a CLI / dashboard view of agent-file health** — out of scope. The warning log is the operator surface.

## Risks

### Risk 1: Catching `Exception` too broadly hides real bugs
**Impact:** If we widened the except to `Exception`, a programmer error inside the parser (e.g., a typo in a regex) would silently produce fallback dicts, and we'd lose the agent's intended prompt without anyone noticing — until quality regressed.
**Mitigation:** Catch `(OSError, ValueError)` — covers the full surface (`FileNotFoundError`, `PermissionError`, `UnicodeDecodeError` are subclasses). Anything outside that tree (e.g., `KeyError`, `AttributeError`, `TypeError`) propagates as before. A unit test asserts that an unrelated exception raised from inside the patched parser does NOT fall back. **Note:** the negative test must raise a class that is neither an `OSError` nor a `ValueError` subclass — `KeyError` qualifies; `UnicodeDecodeError` does not (it inherits from `ValueError` and would correctly hit the fallback path).

### Risk 2: A subtle parse change later expands the failure surface
**Impact:** A future contributor adds a new step to `_parse_agent_markdown` (e.g., YAML safe-load via PyYAML) whose exception type isn't in the tuple → silent crash returns.
**Mitigation:** The test "malformed YAML → fallback" exercises the parse error branch through real malformed input, not a mock. Any future parser that raises a different class will fail that test until the maintainer extends the tuple deliberately.

### Risk 3: Startup trial-parse slows worker boot
**Impact:** Reading three small files at startup is sub-millisecond; not a real risk. Documented to make the tradeoff explicit.
**Mitigation:** None needed. Three `path.read_text()` calls at boot are negligible.

## Race Conditions

No race conditions identified — all operations are synchronous, single-threaded, and read-only against the filesystem. `_parse_agent_markdown` and `validate_agent_files` are called at process startup (and during session setup) from a single execution context. No shared mutable state, no async boundaries.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The issue's "Out of scope" list (no changes to fallback prompt text, no broader file restructuring, no retry/backoff) is also our scope ceiling.

## Update System

No update system changes required — this is a pure-Python change to one module plus tests and a doc file. The next `/update` pull on each machine will deploy it via the normal git path. No new dependencies, no hardlinks, no config files.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to startup validation and session-setup error handling. Agents do not call `_parse_agent_markdown` or `validate_agent_files` directly; they are infrastructure functions invoked by `bridge/telegram_bridge.py::main()`, `worker/__main__.py::main()`, and `agent/sdk_client.py::_create_options()`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-definition-fallback.md` "Behavior" section to enumerate all three failure modes (missing file, malformed/no-frontmatter, OS read errors) and the warning log format.
- [ ] Update the "Key Files" table if new helpers are added (e.g., `_fallback_definition`).
- [ ] No new entry needed in `docs/features/README.md` — the feature already has an index entry.

### Inline Documentation
- [ ] Update `_parse_agent_markdown` docstring to enumerate the three handled exception classes and the fallback contract.
- [ ] Update `validate_agent_files` docstring to mention the trial-parse and that the returned list now includes both missing AND malformed/unreadable paths.

## Success Criteria

- [ ] `_parse_agent_markdown()` returns the fallback dict on all of: missing file (existing), malformed/missing YAML frontmatter (new), `OSError`/`PermissionError` from `read_text` (new), `UnicodeDecodeError` from `read_text` (new).
- [ ] Each fallback path logs a `logger.warning` that includes the exception class name and the file path.
- [ ] `tests/unit/test_agent_definitions.py` covers all four branches; unrelated exceptions still propagate (negative test).
- [ ] `validate_agent_files()` reports malformed/unreadable files at startup, not only missing ones; PR #1353's `test_partial_missing` continues to pass.
- [ ] `docs/features/agent-definition-fallback.md` "Behavior" section accurately enumerates the failure modes covered.
- [ ] Tests pass (`pytest tests/unit/test_agent_definitions.py -q`).
- [ ] Lint and format clean (`python -m ruff check . && python -m ruff format --check .`).

## Team Orchestration

### Team Members

- **Builder (agent-definitions)**
  - Name: agent-def-builder
  - Role: Extend `_parse_agent_markdown` and `validate_agent_files` with widened error handling, plus tests.
  - Agent Type: builder
  - Resume: true

- **Validator (agent-definitions)**
  - Name: agent-def-validator
  - Role: Verify all success criteria, run pytest + ruff, confirm doc accuracy.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Widen `_parse_agent_markdown` and add tests
- **Task ID**: build-widen-fallback
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_definitions.py`
- **Informed By**: Prior Art (#539 precedent), Freshness Check (line refs confirmed)
- **Assigned To**: agent-def-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract the fallback dict construction in `agent/agent_definitions.py:51-57` into a helper `_fallback_definition(path: Path, reason: str) -> dict`. The helper stamps `"_is_fallback": True` on the returned dict so callers can detect fallbacks via key lookup instead of parsing the free-text `description`.
- Refactor `_parse_agent_markdown` to wrap `read_text` + regex match in a single `try/except (OSError, ValueError)` (covers `FileNotFoundError`, `PermissionError`, `UnicodeDecodeError`, plus the explicit `ValueError` for missing frontmatter). Each branch logs `logger.warning(... exception class name ... path ...)` (intentionally `warning`, not `exception` — these failures are expected and handled) and returns `_fallback_definition(path, reason)`. Add an inline comment above the except clause enumerating the covered subclasses.
- Extend `validate_agent_files()` to keep its existing existence-only branch intact (`if not path.exists(): missing.append(...); continue`), and only trial-parse files that exist. For each existing file, call `_parse_agent_markdown(path)` and check `result.get("_is_fallback")`; if True, append the path to the returned list. This preserves PR #1353's `test_partial_missing` log format (one warning per missing file) and avoids double-logging.
- Add tests in `tests/unit/test_agent_definitions.py`:
  - `test_malformed_yaml_returns_fallback` (file with body but no frontmatter delimiters)
  - `test_malformed_yaml_logs_warning` (asserts exception class name appears in log)
  - `test_oserror_returns_fallback` (mocks `Path.read_text` to raise `PermissionError`)
  - `test_oserror_logs_warning`
  - `test_unicode_decode_error_returns_fallback` (writes invalid UTF-8 bytes to file)
  - `test_unrelated_exception_still_propagates` (mocks parser to raise `KeyError`; asserts no fallback, exception escapes)
  - Extend `test_partial_missing` to include one malformed file in the tmp dir; assert both the missing AND malformed file appear in the returned list.
- Update docstrings on `_parse_agent_markdown` and `validate_agent_files`.

### 2. Update feature documentation
- **Task ID**: build-update-docs
- **Depends On**: build-widen-fallback
- **Assigned To**: agent-def-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/agent-definition-fallback.md` "Behavior" section to list all three failure modes covered, the warning log format, and the trial-parse extension to `validate_agent_files`.
- If a new helper `_fallback_definition` is exported as a public-ish symbol, add it to the "Key Files" table description for `agent/agent_definitions.py`.

### 3. Validate the change
- **Task ID**: validate-widen-fallback
- **Depends On**: build-widen-fallback, build-update-docs
- **Assigned To**: agent-def-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_definitions.py -v` and confirm all new + existing tests pass.
- Run `pytest tests/unit/test_worker_startup_validation.py -v` and confirm worker startup test still passes (no signature change to `validate_agent_files`).
- Run `python -m ruff check agent/agent_definitions.py tests/unit/test_agent_definitions.py docs/features/agent-definition-fallback.md`.
- Run `python -m ruff format --check agent/agent_definitions.py tests/unit/test_agent_definitions.py`.
- Confirm `docs/features/agent-definition-fallback.md` "Behavior" section now enumerates all three failure modes.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_agent_definitions.py -q` | exit code 0 |
| Worker startup test passes | `pytest tests/unit/test_worker_startup_validation.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_definitions.py tests/unit/test_agent_definitions.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_definitions.py tests/unit/test_agent_definitions.py` | exit code 0 |
| Behavior doc enumerates malformed-YAML mode | `grep -i 'malformed\|frontmatter\|yaml' docs/features/agent-definition-fallback.md` | output contains malformed |
| Behavior doc enumerates OS-error mode | `grep -i 'OSError\|permission\|read error' docs/features/agent-definition-fallback.md` | output contains OSError |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — 0 blockers, 3 concerns, 1 nit. All addressed by this revision pass.

| # | Severity | Critics | Finding | Resolution |
|---|----------|---------|---------|------------|
| 1 | Concern | Skeptic, Adversary | Fallback-dict detection via `description.startswith("Fallback")` is a fragile contract. | Switched to explicit `"_is_fallback": True` marker stamped by `_fallback_definition` and read by `validate_agent_files` via `result.get("_is_fallback")`. Documented in Solution → Key Elements and Technical Approach. |
| 2 | Concern | Operator, Simplifier | `validate_agent_files()` would double-log warnings for missing files if it trial-parsed them. | Trial-parse is now guarded behind `path.exists()`; missing files take the existing existence-only branch and never reach `_parse_agent_markdown`. Preserves PR #1353's `test_partial_missing` log format. |
| 3 | Concern | Archaeologist, Simplifier | Exception tuple `(FileNotFoundError, PermissionError, OSError, ValueError, UnicodeDecodeError)` is redundant — `OSError` and `ValueError` already cover the subclasses. | Collapsed to `(OSError, ValueError)` with an inline comment enumerating covered subclasses. Risk 1 mitigation language updated. The negative test explicitly uses `KeyError` (neither subclass). |
| 4 | Nit | User | Plan didn't justify `logger.warning` over `logger.exception`. | Added explicit note in Solution → Key Elements: `warning` is intentional because these failures are expected/handled; path + exception class + str(exc) suffice for diagnosis. |

---

## Open Questions

None — the issue is well-scoped, the acceptance criteria are concrete, and the implementation surface is one function plus its sibling validator. Proceeding to critique.
