---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/960
last_comment_id:
revision_applied: true
---

# Health Digest: Replace Circuit Breaker Jargon with Plain-Language Status Labels

## Problem

The daily health digest sent to Telegram exposes raw circuit breaker state names (`CLOSED`, `OPEN`, `HALF_OPEN`) that are counterintuitive to users unfamiliar with the circuit breaker pattern.

**Current behavior:**
Tom received a digest showing `Circuits: anthropic=CLOSED · telegram=CLOSED · redis=CLOSED` and asked "What does it mean that circuits are closed?" — he assumed CLOSED meant the systems were down or unavailable.

**Desired outcome:**
The digest uses plain-language labels anyone can read at a glance: `OK`, `DOWN`, `RECOVERING` — no domain knowledge required.

## Freshness Check

**Baseline commit:** `17551724f78140556a88d1952d7fc6bfa7122ddc`
**Issue filed at:** 2026-04-14T13:26:39Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sustainability.py:452` — `anomalies.append("one or more circuits are not CLOSED")` — still holds exactly
- `agent/sustainability.py:464–477` — agent session prompt instructs LLM to collect circuit details without plain-language formatting — still holds
- `bridge/resilience.py:25–28` — `CircuitState` enum with `CLOSED`, `OPEN`, `HALF_OPEN` values — still holds

**Commits on main since issue was filed (touching referenced files):**
- None

**Active plans in `docs/plans/` overlapping this area:** `health-check-no-progress-recovery.md` — different concern (recovery detection), no overlap with display labels.

## Prior Art

No prior issues or PRs found for circuit breaker label display or digest formatting.

## Data Flow

The digest has two paths through `agent/sustainability.py:sustainability_digest()`:

1. **All-nominal path** — `_send_telegram()` sends a hardcoded one-liner directly. No circuit state labels appear in this path.
2. **Anomaly path** — An `AgentSession` is created with a `command` string that (a) lists detected anomalies and (b) instructs the LLM to collect and format circuit state details. The LLM output forms the Telegram message.

The raw enum labels appear because:
- The anomaly string at line 452 says `"one or more circuits are not CLOSED"` — leaks the internal enum name
- The agent prompt at lines 464–477 says "Circuit state per dependency (anthropic, telegram, redis)" without specifying how to label those states — the LLM mirrors the enum values

Fix both the anomaly description and the agent prompt instructions.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Anomaly string update**: Change `"one or more circuits are not CLOSED"` to `"one or more circuits are not healthy (not in OK state)"` — removes the internal enum name from the anomaly description
- **Agent prompt update**: Add explicit instructions to the dev session prompt to translate circuit states to plain-language labels before reporting

### Flow

Daily reflection tick → `sustainability_digest()` → anomaly detected → anomaly string uses plain language → agent session created with prompt that maps states to plain labels → Telegram message uses `OK`, `DOWN`, `RECOVERING`

### Technical Approach

Two targeted changes in `agent/sustainability.py`:

1. **Line 452** — Replace:
   ```python
   anomalies.append("one or more circuits are not CLOSED")
   ```
   With:
   ```python
   anomalies.append("one or more service circuits are not healthy")
   ```

2. **Agent session prompt (lines ~464–477)** — Add explicit label mapping instruction after the circuit state data-collection step:
   > Circuit states appear as 'closed'/'CLOSED' → report as OK, 'open'/'OPEN' → report as DOWN, 'half_open'/'HALF_OPEN' → report as RECOVERING. Never output any of these raw state strings in the user-facing message.

   **Implementation Note (from critique):** The mapping must cover **both** forms: the uppercase enum names (`CLOSED`, `OPEN`, `HALF_OPEN`) and the lowercase `.value` strings (`"closed"`, `"open"`, `"half_open"`). When the LLM uses tools to inspect live circuit state, it receives `cb.state.value` — the lowercase string form — not the enum name. Covering only uppercase forms would leave the lowercase variants unaddressed, allowing `closed`/`open`/`half_open` to leak into the output. The prompt instruction must reference both: `'closed'/'CLOSED'`, `'open'/'OPEN'`, `'half_open'/'HALF_OPEN'`.

No changes to `bridge/resilience.py` — the internal `CircuitState` enum is correct and should remain unchanged.

## Failure Path Test Strategy

### Exception Handling Coverage
- The existing `except Exception: circuits_ok = False` block at line 411 remains unchanged — no new exception handlers introduced
- No existing exception swallowing in the two lines being modified

### Empty/Invalid Input Handling
- The anomaly string is a constant — no input processing involved
- The agent prompt is a formatted string — no edge cases from empty inputs

### Error State Rendering
- The fix improves error state rendering: `DOWN` is unambiguous where `OPEN` was confusing
- If `circuits_ok = False` and the session is created, the agent prompt now explicitly maps states to readable labels

## Test Impact

- [ ] `tests/unit/test_sustainability.py` — UPDATE: no existing test cases are affected (none cover `sustainability_digest()`), but this file must be extended with `test_digest_anomaly_prompt_uses_plain_language` to assert (a) the anomaly string no longer contains `"not CLOSED"` and (b) the agent session command contains the plain-language label mapping instruction

## Rabbit Holes

- **Refactoring CircuitState enum** — tempting to add a `display_label` property to `CircuitState`, but this conflates internal state naming with presentation. The display concern belongs in the digest prompt, not the circuit breaker model.
- **Dashboard UI labels** — the web dashboard also shows circuit state. Out of scope; separate issue if needed.
- **Localisation/emoji** — whether to use ✅/🔴/🟡 vs plain text `OK/DOWN/RECOVERING`. Keep it plain text for now; emoji are easy to add later.

## Risks

### Risk 1: Agent LLM still outputs raw state names despite prompt instruction
**Impact:** Fix doesn't fully land — the LLM might ignore or reformat the instruction
**Mitigation:** The prompt instruction is explicit and imperative. If the LLM ignores it, a second iteration can add examples or use a Jinja template for the circuit section.

### Risk 2: Anomaly string change breaks a test assertion
**Impact:** CI failure
**Mitigation:** Checked `tests/unit/test_sustainability.py` — no test asserts the exact anomaly string content. Safe to change.

## Race Conditions

No race conditions identified — both changes are to string literals in a single-threaded function. No concurrency involved.

## No-Gos (Out of Scope)

- Changes to `bridge/resilience.py` internal enum
- Dashboard UI circuit state labels
- Adding emoji decorators to the labels (simple text change only)
- Refactoring `sustainability_digest()` beyond the two string changes

## Update System

No update system changes required — this is a purely internal string change with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration changes required — `agent/sustainability.py` is already wired into the reflection scheduler. The change is entirely within the existing prompt strings.

## Documentation

- [x] Updated `docs/features/sustainable-self-healing.md` — added plain-language circuit state label mapping description to the Daily Health Digest section and the unit test coverage table
- [x] Confirmed `docs/features/bridge-self-healing.md` has no circuit state display references — no update needed

## Success Criteria

- [ ] When all circuits are `CLOSED`, the digest one-liner path shows "all clear" (unchanged — already readable)
- [ ] When a circuit is `OPEN`, the agent session prompt instructs the LLM to report `DOWN` — never `OPEN`
- [ ] When a circuit is `HALF_OPEN`, the agent prompt instructs the LLM to report `RECOVERING`
- [ ] The anomaly string at line 452 no longer contains `"not CLOSED"`
- [ ] New test `test_digest_anomaly_prompt_uses_plain_language` passes
- [ ] Tests pass (`pytest tests/unit/test_sustainability.py`)
- [ ] Lint clean (`python -m ruff check agent/sustainability.py`)
- [ ] **Post-deploy smoke test** (manual): trigger a non-CLOSED circuit condition and confirm the Telegram digest output uses `OK`/`DOWN`/`RECOVERING` — the unit test validates prompt shape only; the live LLM output requires a real trigger to confirm end-to-end compliance

## Team Orchestration

### Team Members

- **Builder (sustainability-labels)**
  - Name: sustainability-builder
  - Role: Apply the two string changes in `agent/sustainability.py` and add the new test
  - Agent Type: builder
  - Resume: true

- **Validator (sustainability-labels)**
  - Name: sustainability-validator
  - Role: Verify the changes are correct — full unit suite, ruff, all success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply plain-language label changes
- **Task ID**: build-plain-labels
- **Depends On**: none
- **Validates**: `tests/unit/test_sustainability.py`
- **Assigned To**: sustainability-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sustainability.py:452`, replace `"one or more circuits are not CLOSED"` with `"one or more service circuits are not healthy"`
- In the agent session `command` string (lines ~464–477), add after the circuit-data-collection step: an explicit instruction to translate states to plain-language labels — must cover **both** the lowercase `.value` strings and uppercase enum names: `'closed'/'CLOSED'` → `OK`, `'open'/'OPEN'` → `DOWN`, `'half_open'/'HALF_OPEN'` → `RECOVERING`. Never output any raw state string in the user-facing message.
- Add `test_digest_anomaly_prompt_uses_plain_language` to `tests/unit/test_sustainability.py` asserting (a) the anomaly string does not contain `"not CLOSED"` and (b) the command string contains the plain-language label mapping instruction

### 2. Validate changes
- **Task ID**: validate-plain-labels
- **Depends On**: build-plain-labels
- **Assigned To**: sustainability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` to confirm all tests pass (including the new one) with no regressions
- Run `python -m ruff check .` and `python -m ruff format --check .` for full lint/format pass
- Confirm `agent/sustainability.py:452` no longer contains `"not CLOSED"`
- Confirm the agent session command string contains the plain-language label instruction covering both lowercase and uppercase forms
- Verify all success criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sustainability.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/sustainability.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sustainability.py` | exit code 0 |
| Anomaly string updated | `grep -n "not CLOSED" agent/sustainability.py` | exit code 1 |
| Plain-language instruction present | `grep -n "RECOVERING" agent/sustainability.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Adversary | Prompt covers uppercase enum names but `CircuitState.value` returns lowercase strings (`"closed"`, `"open"`, `"half_open"`); lowercase forms would still leak into output | Technical Approach + Task 1 | Prompt instruction updated to cover both forms: `'closed'/'CLOSED'` → OK, `'open'/'OPEN'` → DOWN, `'half_open'/'HALF_OPEN'` → RECOVERING |
| NIT | Skeptic | Unit test validates prompt shape only, not actual LLM output — no end-to-end signal | Success Criteria | Added post-deploy smoke-test note: trigger a non-CLOSED circuit and read Telegram output to confirm LLM compliance |
| NIT | Simplifier | Tasks 2 and 3 both assigned to same validator agent — redundant for a 2-string change | Tasks | Merged into single Task 2 covering full unit suite + ruff in one step |

---

## Open Questions

None — this is a well-scoped string change with no open trade-offs requiring supervisor input.
