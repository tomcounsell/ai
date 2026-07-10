# Test Coverage Standards

Standards and tooling for preventing silent failure classes in the test suite. Addresses five categories of coverage gaps discovered through production incident analysis.

## Problem

Several classes of bugs can pass all existing tests while silently degrading the system:

1. **Silent exception swallowing** -- `except Exception: pass` blocks hide failures with no logging or observable side effects
2. **Empty output loops** -- empty agent output classified as STATUS_UPDATE triggers infinite auto-continue cycles
3. **Coupled test logic** -- tests that replicate production routing logic inline rather than calling the production function, meaning tests pass even when production behavior changes
4. **Missing error state rendering** -- message drafter tests only cover success paths, not failure/error rendering
5. **Silent build completion** -- builders that produce zero commits complete without any user-visible warning

## Solution

### Gap 1: Exception Logging (agent/agent_session_queue.py)

Replaced 7 `except Exception: pass` blocks in critical session queue functions with `except Exception as e: logger.warning(...)` calls. Each warning includes identifying context (session_id, file path) for debugging.

**Functions covered:**
- `_push_agent_session` -- lifecycle transition logging
- `_pop_agent_session` -- lifecycle transition logging
- `_enqueue_continuation` -- plan file resolution
- `_execute_agent_session` -- session re-read from Redis
- `_load_cooldowns` -- file read
- `_save_cooldowns` -- file write
- `check_revival` -- branch existence check

**Test approach:** `tests/integration/test_silent_failures.py` uses `caplog` to assert warnings are emitted when exceptions occur. Assertions check log level and presence of key identifiers -- not exact message text -- to avoid brittle tests.

**Superseded by ruff S110/S112 (issue #2004).** The guard test above only
ever covered these 7 hand-picked functions via a source-text scan
(`TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions` —
`inspect.getsource()` plus a line-by-line `pass`/`except Exception` string
match, not an AST analysis). Repo-wide, ~87 additional `except Exception:
pass`/`except Exception: continue` sites existed uncovered outside those 7
functions. Issue #2004 replaced the whole approach: ruff's `S110`
(try-except-pass) and `S112` (try-except-continue) rules are now enabled
(`pyproject.toml`, scoped to `agent/ bridge/ tools/ worker/ monitoring/` plus
the four `scripts/` files this sweep touches) and enforced by `python -m
ruff check .` on every commit — a real AST-level check, not a string scan,
and one that covers every function in scope rather than 7 named ones. The
131 sites found in-scope were triaged: 39 fixed with `logger.warning`
(matching this section's original pattern), 92 allowlisted with a per-line
`# noqa: S110`/`# noqa: S112` plus a mandatory reason comment (e.g. memory
ops that are silent by documented design, best-effort cleanup/teardown, or
optional telemetry counters). `TestNoSilentPassRemaining` was deleted; the
behavioral `caplog`-based test classes above (`TestPushJobLogging`,
`TestPopJobLogging`, etc.) were kept unchanged — they test actual logging
behavior, not source text, so the lint rule doesn't make them redundant.

### Gap 2: Empty Output Anomaly Detection (agent/agent_session_queue.py)

Extracted the empty output guard into a pure function `should_guard_empty_output(msg, is_sdlc, has_remaining_stages) -> bool` in `agent/agent_session_queue.py`. The function returns True when output is empty/whitespace AND the session is SDLC with remaining stages. The production `send_to_chat` closure calls this function, and tests call it directly — following the same extraction pattern used for Gap 3.

When the guard triggers, the empty output is delivered to the user with a "(empty output)" placeholder instead of being classified as STATUS_UPDATE and auto-continued.

**Tests:** `TestEmptyOutputAnomalyDetection` in `tests/test_auto_continue.py` (5 unit tests calling `should_guard_empty_output` directly) and `TestEmptyOutputLoopTermination` in `tests/test_enqueue_continuation.py`. (`classify_output` was removed from the drafter in drafter_passthrough_validation; the async test for that behavior was deleted with it.)

### Gap 3: Routing Decision Extraction (agent/agent_session_queue.py)

> **Updated**: `classify_routing_decision()` and `RoutingDecision` were removed in PR #321 (Observer Agent). Routing decisions are now made by the [Observer Agent](observer-agent.md) with full session context. The `TestClassifyRoutingDecision` test class was removed from `tests/test_auto_continue.py`. Observer decision quality is now validated by 13 integration tests in `tests/test_observer.py` using real API calls with Haiku as a robustness floor.

### Gap 4: Error State Rendering (bridge/message_drafter.py)

Added 7 tests for error/failure rendering paths in `_compose_structured_draft`:
- Failed session emoji rendering
- Failed session with completion flag (error takes precedence)
- Failed stage in stage progress display
- Error message propagation to output
- Failed session with link footer
- `_get_status_emoji` with failed status
- `_render_stage_progress` with failed stages

**Tests:** `TestErrorStateRendering` in `tests/unit/test_message_drafter.py`.

### Gap 5: Build Output Validation

Tests documenting the expected behavior when builders produce no commits:
- Warning should be surfaced (not silent success)
- Pipeline should NOT hard-block (config-only changes are legitimate)
- Commit count detection via `git log --oneline main..HEAD`

**Tests:** `tests/test_build_validation.py` with `TestBuildOutputVerification` and `TestBuildValidationIntegration`.

## Skill Documentation Updates

### Plan Template (do-plan)

Added a **Failure Path Test Strategy** section to the plan template (`.claude/skills/do-plan/PLAN_TEMPLATE.md`) with three subsections:
- **Exception Handling Coverage** -- identify `except Exception: pass` blocks and require corresponding tests
- **Empty/Invalid Input Handling** -- document empty/None/whitespace behavior
- **Error State Rendering** -- test failure rendering paths, not just success

### Test Skill (do-test)

Added a **Quality Checks (Post-Test)** section to the test skill (`.claude/skills/do-test/SKILL.md`) with three automated scans:
- **Exception Swallow Scan** -- grep for bare exception handlers without logging. **Updated in #1042:** this scan was promoted from advisory to a mandatory blocking gate that runs before OUTCOME emission. New `except Exception` blocks without `logger`/`raise`/`# swallow-ok:` fail the TEST stage.
- **Empty Input Check** -- verify empty input edge cases are tested
- **Closure Coverage Flag** -- detect untested inner functions/closures

## Validation

Run the full test suite to verify all coverage standards are met:

```bash
python -m pytest tests/integration/test_silent_failures.py tests/test_build_validation.py tests/test_auto_continue.py tests/test_enqueue_continuation.py tests/unit/test_message_drafter.py -v
```

Verify no silent `except: pass`/`except: continue` remain in the linted scope (issue #2004 — this replaces the old `grep`-based heuristic that used to live here):

```bash
python -m ruff check --select S110,S112 agent/ bridge/ tools/ worker/ monitoring/
```

## Files Changed

| File | Change |
|------|--------|
| `agent/agent_session_queue.py` | Replace 7 silent exception handlers with logger.warning; extract `should_guard_empty_output()` and `classify_routing_decision()` |
| `tests/integration/test_silent_failures.py` | New: 8 test classes for Gap 1 exception logging (the source-scan meta-test, `TestNoSilentPassRemaining`, was later deleted and superseded by ruff S110/S112 — issue #2004) |
| `tests/test_auto_continue.py` | Add `TestClassifyRoutingDecision` (10 tests) and `TestEmptyOutputAnomalyDetection` (6 tests calling production `should_guard_empty_output`) |
| `tests/test_enqueue_continuation.py` | Add `TestEmptyOutputLoopTermination` (3 tests) |
| `tests/unit/test_message_drafter.py` | Add `TestErrorStateRendering` (7 tests) |
| `tests/test_build_validation.py` | New: 6 tests for build output verification |
| `.claude/skills/do-plan/PLAN_TEMPLATE.md` | Add Failure Path Test Strategy section |
| `.claude/skills/do-test/SKILL.md` | Add Quality Checks (Post-Test) section |
