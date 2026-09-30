# Test Coverage Standards

Standards and tooling for preventing silent failure classes in the test suite. The suite covers five categories of failure that would otherwise pass silently.

## Silent Failure Classes Covered

1. **Silent exception swallowing** -- `except Exception: pass` blocks that hide failures with no logging or observable side effects
2. **Empty output loops** -- empty agent output classified as STATUS_UPDATE triggers infinite auto-continue cycles
3. **Coupled test logic** -- tests that replicate production routing logic inline rather than calling the production function, meaning tests pass even when production behavior changes
4. **Missing error state rendering** -- message drafter tests only cover success paths, not failure/error rendering
5. **Silent build completion** -- builders that produce zero commits complete without any user-visible warning

## Enforcement

### Exception Logging (agent/agent_session_queue.py)

Critical session queue functions log exceptions with `logger.warning(...)`; each warning includes identifying context (session_id, file path) for debugging.

**Functions covered:**
- `_push_agent_session` -- lifecycle transition logging
- `_pop_agent_session` -- lifecycle transition logging
- `_enqueue_continuation` -- plan file resolution
- `_execute_agent_session` -- session re-read from Redis
- `_load_cooldowns` -- file read
- `_save_cooldowns` -- file write
- `check_revival` -- branch existence check

**Test approach:** `tests/integration/test_silent_failures.py` uses `caplog` to assert warnings are emitted when exceptions occur. Assertions check log level and presence of key identifiers -- not exact message text -- to avoid brittle tests.

Repo-wide, ruff's `S110` (try-except-pass) and `S112` (try-except-continue) rules are enabled (`pyproject.toml`, scoped to `agent/ bridge/ tools/ worker/ monitoring/` plus the four `scripts/` files in scope) and enforced by `python -m ruff check .` on every commit — an AST-level check covering every function in scope. Sites in scope are either fixed with `logger.warning` or allowlisted with a per-line `# noqa: S110`/`# noqa: S112` plus a mandatory reason comment (e.g. memory ops that are silent by documented design, best-effort cleanup/teardown, optional telemetry counters). The behavioral `caplog`-based test classes (`TestPushJobLogging`, `TestPopJobLogging`, etc.) test actual logging behavior, not source text.

### Empty Output Anomaly Detection (agent/agent_session_queue.py)

`should_guard_empty_output(msg, is_sdlc, has_remaining_stages) -> bool` is a pure function in `agent/agent_session_queue.py`. It returns True when output is empty/whitespace AND the session is SDLC with remaining stages. The production `send_to_chat` closure calls this function, and tests call it directly.

When the guard triggers, the empty output is delivered to the user with a "(empty output)" placeholder instead of being classified as STATUS_UPDATE and auto-continued.

**Tests:** `TestEmptyOutputAnomalyDetection` in `tests/test_auto_continue.py` (unit tests calling `should_guard_empty_output` directly) and `TestEmptyOutputLoopTermination` in `tests/test_enqueue_continuation.py`.

### Routing Decision Extraction

Routing decisions are made by the [Observer Agent](observer-agent.md) with full session context. Observer decision quality is validated by integration tests in `tests/test_observer.py` using real API calls with Haiku as a robustness floor.

### Error State Rendering (bridge/message_drafter.py)

`_compose_structured_draft` is covered for error/failure rendering paths:
- Failed session emoji rendering
- Failed session with completion flag (error takes precedence)
- Failed stage in stage progress display
- Error message propagation to output
- Failed session with link footer
- `_get_status_emoji` with failed status
- `_render_stage_progress` with failed stages

**Tests:** `TestErrorStateRendering` in `tests/unit/test_message_drafter.py`.

### Build Output Validation

Builders that produce no commits surface a warning (not silent success). The pipeline does not hard-block (config-only changes are legitimate). Commit count is detected via `git log --oneline main..HEAD`.

**Tests:** `tests/test_build_validation.py` with `TestBuildOutputVerification` and `TestBuildValidationIntegration`.

## Skill Documentation

### Plan Template (do-plan)

The plan template (`.claude/skills/do-plan/PLAN_TEMPLATE.md`) carries a **Failure Path Test Strategy** section with three subsections:
- **Exception Handling Coverage** -- identify `except Exception: pass` blocks and require corresponding tests
- **Empty/Invalid Input Handling** -- document empty/None/whitespace behavior
- **Error State Rendering** -- test failure rendering paths, not just success

### Test Skill (do-test)

The test skill (`.claude/skills/do-test/SKILL.md`) carries a **Quality Checks (Post-Test)** section with three automated scans:
- **Exception Swallow Scan** -- grep for bare exception handlers without logging. This scan is a mandatory blocking gate that runs before OUTCOME emission; new `except Exception` blocks without `logger`/`raise`/`# swallow-ok:` fail the TEST stage.
- **Empty Input Check** -- verify empty input edge cases are tested
- **Closure Coverage Flag** -- detect untested inner functions/closures

## Validation

Run the full test suite to verify all coverage standards are met:

```bash
python -m pytest tests/integration/test_silent_failures.py tests/test_build_validation.py tests/test_auto_continue.py tests/test_enqueue_continuation.py tests/unit/test_message_drafter.py -v
```

Verify no silent `except: pass`/`except: continue` remain in the linted scope:

```bash
python -m ruff check --select S110,S112 agent/ bridge/ tools/ worker/ monitoring/
```

## Files

| File | Role |
|------|--------|
| `agent/agent_session_queue.py` | Exception logging; `should_guard_empty_output()` |
| `tests/integration/test_silent_failures.py` | Exception logging test classes |
| `tests/test_auto_continue.py` | `TestEmptyOutputAnomalyDetection` |
| `tests/test_enqueue_continuation.py` | `TestEmptyOutputLoopTermination` |
| `tests/unit/test_message_drafter.py` | `TestErrorStateRendering` |
| `tests/test_build_validation.py` | Build output verification tests |
| `.claude/skills/do-plan/PLAN_TEMPLATE.md` | Failure Path Test Strategy section |
| `.claude/skills/do-test/SKILL.md` | Quality Checks (Post-Test) section |
