---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/946
last_comment_id:
revision_applied: true
---

# Email Bridge: Fix OutputHandler Registration for Domain-Only Projects

## Problem

An inbound email from `tcounsell@psyoptimal.com` was processed by the worker and produced
a 673-character reply — but the reply was written to a local log file instead of being
emailed back. The sender saw nothing.

**Current behavior:**
`worker/__main__.py:237-238` gates `EmailOutputHandler` registration on
`project_cfg.get("email", {}).get("contacts", {})`. Projects routed only via `email.domains`
never satisfy this check, so no outbound handler is registered. The worker falls through to
`FileOutputHandler`, silently discarding the reply.

**Desired outcome:**
When a project has any email routing configured (contacts OR domains), the worker registers
`EmailOutputHandler`. SMTP reply is sent to the original sender with correct `In-Reply-To`
threading.

## Freshness Check

**Baseline commit:** 712638dd
**Issue filed at:** 2026-04-14
**Disposition:** Unchanged

**File:line references re-verified:**
- `worker/__main__.py:235-240` — Gate condition checks only `email.contacts` — still holds exactly
- `bridge/routing.py:161-208` — `build_email_to_project_map()` reads both contacts AND domains — still holds
- `agent/agent_session_queue.py:1920-1973` — `register_callbacks()` and `_resolve_callbacks()` — unchanged

**Cited sibling issues/PRs re-checked:**
- #847 — Closed (merged PR #908, 2026-04-13) — email bridge feature shipped
- #936 — Closed (merged PR #939, 2026-04-13) — operational test coverage, no overlap with this fix

**Commits on main since issue was filed (touching referenced files):**
- `712638dd`, `82186dcc`, `697f7489` — touch `agent_session_queue.py` for session lifecycle fixes, none affect email registration logic — irrelevant

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** All cited line numbers and code claims remain accurate against current main. Post-critique discovery: `build_email_to_project_map()` in `bridge/routing.py` was updated to return `tuple[dict, dict]` at some point after the tests were written — 9/15 `TestBuildEmailToProjectMap` tests now fail with `TypeError: tuple indices must be integers or slices, not str`. These pre-existing failures must be fixed as part of this plan's build step.

## Prior Art

- **Issue #847 / PR #908**: Original email bridge implementation — shipped contacts + domains
  inbound routing but the worker startup only registered `EmailOutputHandler` for contacts.
  This is the source of the bug, not a failed fix attempt.
- **Issue #936 / PR #939**: Added operational test coverage (env loading, batch cap, health
  timestamp). Did not address outbound handler registration. Not a failed fix — different scope.

No prior attempts to fix this specific registration gap.

## Data Flow

**Current (broken) path for domain-only project:**
1. **IMAP poll** → `_poll_imap()` fetches unseen message from `@psyoptimal.com`
2. **Routing** → `bridge/routing.py:find_project_for_email()` matches via `DOMAIN_TO_PROJECT` → project found ✓
3. **Enqueue** → `_process_inbound_email()` calls `enqueue_agent_session()` with `extra_context.transport="email"` ✓
4. **Worker dequeue** → session popped, `_resolve_callbacks("psyoptimal", "email")` called
5. **Callback miss** → `(psyoptimal, "email")` key not registered → tries plain `psyoptimal` key → MISS
6. **Fallback** → `_resolve_callbacks` returns `(None, None)` → `FileOutputHandler` used
7. **Silent loss** → reply written to `logs/worker/{session_id}.log`, no SMTP send, sender sees nothing

**Fixed path:**
- Step 4 succeeds: `(psyoptimal, "email")` key IS registered at worker startup
- `_resolve_callbacks` returns `(EmailOutputHandler.send, EmailOutputHandler.react)`
- `EmailOutputHandler.send()` calls `_send_smtp()` via `asyncio.to_thread()`
- SMTP reply delivered with `In-Reply-To: <original-message-id>` and `Re:` subject

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — `register_callbacks()` API unchanged, same call site
- **Coupling**: No change — fix is internal to `worker/__main__.py` startup logic
- **Data ownership**: No change
- **Reversibility**: Trivially reversible — two-line condition change

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The fix modifies only
`worker/__main__.py` and adds tests. No new env vars, services, or migrations.

## Solution

### Key Elements

- **Gate widening**: Change `if email_contacts` to `if email_cfg.get("contacts") or email_cfg.get("domains")` in `worker/__main__.py:237-238`
- **Domain routing test coverage**: Add domain-keyed tests to `tests/unit/test_email_routing.py` for `build_email_to_project_map()` with domains
- **Worker registration unit tests**: New `tests/unit/test_worker_startup.py` covering all four permutations of contacts/domains/both/neither
- **Integration test**: End-to-end path in `tests/integration/test_email_bridge.py` verifying domain-routed inbound email produces SMTP send

### Flow

Inbound email from `@psyoptimal.com` → domain routing matches project → worker registered handler → SMTP reply sent

### Technical Approach

**Fix (2-line change in `worker/__main__.py:235-240`):**
```python
# Before
email_contacts = project_cfg.get("email", {}).get("contacts", {})
if email_contacts:

# After
email_cfg = project_cfg.get("email", {}) or {}
if email_cfg.get("contacts") or email_cfg.get("domains"):
```

This is the narrow fix preferred over unconditional registration — it preserves the audit
signal that a project expects email routing, and keeps the log message informative.

**New unit tests in `tests/unit/test_worker_startup.py` — isolation strategy:**

The test must target only the gate condition, not the full worker startup. The narrowest approach is to extract the condition into a testable helper:
```python
def _should_register_email_handler(project_cfg: dict) -> bool:
    email_cfg = project_cfg.get("email", {}) or {}
    return bool(email_cfg.get("contacts") or email_cfg.get("domains"))
```
Tests call this helper directly — no Redis, no worker imports, no callback dict pollution. This avoids the cross-test bleeding that arises when `register_callbacks()` writes into module-level dicts `_send_callbacks` and `_reaction_callbacks` in `agent/agent_session_queue.py`. If the builder prefers testing `register_callbacks()` directly instead, each test must add `monkeypatch.setattr("agent.agent_session_queue._send_callbacks", {})` in a fixture to reset state.

Tests to cover:
- Project with `email.contacts` only → `True` (regression guard for current behavior)
- Project with `email.domains` only → `True` (the failing case)
- Project with both contacts and domains → `True`
- Project with neither → `False`
- Project with empty dicts `{"email": {"contacts": {}, "domains": []}}` → `False`
- Project with `"email": None` → `False` (the `or {}` guard)

**Extended domain tests in `tests/unit/test_email_routing.py`:**

After fixing the 9 broken `TestBuildEmailToProjectMap` tests (tuple unpacking), add:
- `build_email_to_project_map()` returns correct domain_map for domain-only projects (assert `domain_map["psyoptimal.com"]["_key"] == "psyoptimal"`)
- `build_email_to_project_map()` returns empty addr_map and populated domain_map when only domains configured
- `find_project_for_email()` domain fallback: use `monkeypatch.setattr(routing_module, "EMAIL_DOMAIN_TO_PROJECT", {"psyoptimal.com": project})` and verify sender from `@psyoptimal.com` resolves to the project

**Integration test extension in `tests/integration/test_email_bridge.py`:**

Patch strategy: mirror the existing `test_email_bridge.py` pattern for contacts (`EMAIL_TO_PROJECT`), but for domains:
```python
monkeypatch.setattr(routing_module, "EMAIL_DOMAIN_TO_PROJECT", {"psyoptimal.com": domain_project})
```
This ensures the actual domain lookup code path in `find_project_for_email()` is exercised, not a mocked-too-early shortcut. A separate unit test in `test_worker_startup.py` validates the registration gate; the integration test validates the outbound path once routing works.

New test class `TestDomainRoutedEmailReply` covering:
- Domain-routed sender → `EmailOutputHandler._send_smtp` called once with correct `To`, `In-Reply-To`, `Re:` subject
- Regression guard: "No bridge callbacks registered" log line NOT emitted for email sessions

**Callback resolution test in `tests/unit/test_agent_session_queue.py`:**
- `register_callbacks("proj", transport="email", handler=h)` then `_resolve_callbacks("proj", "email")` returns `(h.send, h.react)`
- No registration → returns `(None, None)` (explicit test to pin the fallback behavior)

## Failure Path Test Strategy

### Exception Handling Coverage
- The `email_cfg.get("domains")` call cannot raise — dict.get() is safe on None after `or {}`
- No new exception handlers introduced; no new `except Exception: pass` blocks

### Empty/Invalid Input Handling
- `project_cfg.get("email", {}) or {}` handles `None` email config (e.g., `"email": null` in JSON)
- Empty list `domains: []` → falsy → not registered (correct)
- Empty dict `contacts: {}` → falsy → not registered (correct; same as before)
- Both empty → not registered (tested explicitly)

### Error State Rendering
- No user-visible output affected; this is worker startup logic
- The existing log line `[{project_key}] Registered EmailOutputHandler (transport=email)` is sufficient observability

## Test Impact

- [x] `tests/unit/test_email_routing.py` — FIX + UPDATE: **9 of 15 tests in `TestBuildEmailToProjectMap` are currently failing on main** because `build_email_to_project_map()` now returns `tuple[dict, dict]` (addr_map, domain_map) but all tests treat the return as a single `dict`. Every call must be unpacked: `addr_map, domain_map = build_email_to_project_map(config)`. Assertions on contact tests use `addr_map`; new domain tests assert against `domain_map`. The `test_returns_empty_dict_for_empty_config` test becomes `assert addr_map == {} and domain_map == {}`. After fixing the 9 broken tests, add new domain-map tests for `build_email_to_project_map()` with `email.domains` config.
- [x] `tests/integration/test_email_bridge.py` — UPDATE: add `TestDomainRoutedEmailReply` class; existing tests unaffected
- [x] `tests/unit/test_agent_session_queue.py` — UPDATE: add transport-keyed callback resolution tests; existing tests unaffected

## Rabbit Holes

- **Unconditional registration**: Registering `EmailOutputHandler` for every project is tempting but loses audit signal. The narrow fix is cleaner.
- **SMTP config validation at startup**: Could validate SMTP config is present when registering — but that's a separate concern (health check / startup guard). Out of scope.
- **Domain-based find_project_for_email in integration tests**: The existing integration tests use `EMAIL_TO_PROJECT` for routing. Adding full domain-based routing to integration tests would require refactoring test fixtures. Narrow the integration test to mock the routing lookup and focus on the outbound path.

## Risks

### Risk 1: `project_cfg.get("email")` returns `None` instead of `{}`
**Impact:** `None.get("domains")` would raise `AttributeError`
**Mitigation:** `or {}` guard in `email_cfg = project_cfg.get("email", {}) or {}` handles this

### Risk 2: Test for registration requires importing `worker.__main__`
**Impact:** Worker startup imports could fail in test environments (missing Redis, etc.)
**Mitigation:** Test only the registration gate logic by extracting the condition into a
testable helper, OR mock worker dependencies. Prefer the mock approach to keep the test
scoped to the condition logic.

## Race Conditions

No race conditions identified. Worker startup is single-threaded sequential initialization.
`register_callbacks()` is called before the event loop starts processing sessions.

## No-Gos (Out of Scope)

- SMTP config validation at worker startup (separate issue)
- `find_project_for_email()` domain lookup unit test refactoring beyond adding domain tests
- Metrics/alerting for missed email callbacks
- Multi-project email routing edge cases (sender matches two domain configs)

## Update System

No update system changes required — this is a purely internal bug fix to `worker/__main__.py`.
No new dependencies, no new config files, no migration steps needed.

## Agent Integration

No agent integration required — this is a worker startup fix. The agent (bridge) is unaffected.
The fix is entirely within the worker's initialization path.

## Documentation

- [x] Update `docs/features/email-bridge.md` — the "Configuration" section currently only documents `email.contacts` routing; add a parallel `email.domains` configuration example and explain that either field (or both) enables `EmailOutputHandler` registration and inbound routing
- [x] Update `docs/features/email-bridge.md` — add or expand a "Worker Registration" section that explicitly states the registration gate condition: handler is registered when `email.contacts` OR `email.domains` is non-empty, explaining why this matches the inbound routing logic in `bridge/routing.py`
- [x] Update `docs/features/README.md` — the existing entry for `email-bridge.md` mentions "sender-based project routing"; update to clarify it covers both contact-based and domain-based routing

## Success Criteria

- [x] Worker registers `EmailOutputHandler` for projects with `email.domains` only
- [x] Worker registers `EmailOutputHandler` for projects with `email.contacts` only (no regression)
- [x] Worker registers `EmailOutputHandler` for projects with both
- [x] Worker does NOT register for projects with neither
- [x] SMTP reply has correct `In-Reply-To` and `References` headers for threading
- [x] "No bridge callbacks registered" log line is NOT emitted for email sessions
- [x] Unit tests: all four registration permutations covered
- [x] Domain routing unit tests added to `test_email_routing.py`
- [x] Integration test: domain-routed inbound → `_send_smtp` called with correct headers
- [x] Existing email tests still pass (no regressions)
- [x] Tests pass (`pytest tests/unit/test_worker_startup.py tests/unit/test_email_routing.py tests/integration/test_email_bridge.py -q`)
- [x] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (worker-fix)**
  - Name: worker-fix-builder
  - Role: Apply the 2-line fix to `worker/__main__.py` and write all new/updated tests
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Run test suite and verify all success criteria met
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Apply Fix and Write Tests
- **Task ID**: build-fix
- **Depends On**: none
- **Validates**: tests/unit/test_worker_startup.py (create), tests/unit/test_email_routing.py (fix + update), tests/integration/test_email_bridge.py (update), tests/unit/test_agent_session_queue.py (update)
- **Assigned To**: worker-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `worker/__main__.py:237-238` to widen the gate condition
- **Add `_should_register_email_handler(project_cfg: dict) -> bool` helper at module scope in `worker/__main__.py`** and update the registration loop to call it: `if _should_register_email_handler(project_cfg):`. The helper is defined at module scope (not inside `_run_worker()`). Tests import it directly (`from worker.__main__ import _should_register_email_handler`) — no worker initialization, no Redis, no callback dicts touched. This is the primary change; the 2-line fix in the Technical Approach section is the body of this helper.
- **Fix 9 broken `TestBuildEmailToProjectMap` tests** in `tests/unit/test_email_routing.py`: unpack tuple return (`addr_map, domain_map = build_email_to_project_map(config)`) in all 9 failing test methods; contact assertions use `addr_map`, `test_returns_empty_dict_for_empty_config` becomes `assert addr_map == {} and domain_map == {}`
- Add domain-map tests to `tests/unit/test_email_routing.py` (domain_map population, `find_project_for_email()` domain fallback via `monkeypatch.setattr(routing_module, "EMAIL_DOMAIN_TO_PROJECT", ...)`)
- Create `tests/unit/test_worker_startup.py` with `_should_register_email_handler()` helper and all six gate permutations (contacts-only, domains-only, both, neither, empty dicts, None email config)
- Add `TestDomainRoutedEmailReply` to `tests/integration/test_email_bridge.py` using `monkeypatch.setattr(routing_module, "EMAIL_DOMAIN_TO_PROJECT", ...)` patch strategy
- Add transport-keyed callback resolution tests to `tests/unit/test_agent_session_queue.py`

### 2. Update Documentation
- **Task ID**: document-fix
- **Depends On**: build-fix
- **Assigned To**: worker-fix-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/email-bridge.md` Worker Registration section

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-fix, document-fix
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_startup.py tests/unit/test_email_routing.py tests/integration/test_email_bridge.py tests/unit/test_agent_session_queue.py -q`
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Worker fix tests pass | `pytest tests/unit/test_worker_startup.py -q` | exit code 0 |
| Email routing tests pass | `pytest tests/unit/test_email_routing.py -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_email_bridge.py -q` | exit code 0 |
| Queue callback tests pass | `pytest tests/unit/test_agent_session_queue.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist, Skeptic | 9/15 `TestBuildEmailToProjectMap` tests failing on main — `build_email_to_project_map()` returns `tuple[dict, dict]` but tests treat it as `dict` | Task 1 (build-fix): fix all 9 broken tests before adding domain tests | Unpack: `addr_map, domain_map = build_email_to_project_map(config)`; contact assertions use `addr_map`; `test_returns_empty_dict_for_empty_config` becomes `assert addr_map == {} and domain_map == {}` |
| CONCERN | Skeptic, Operator | Integration test must patch at correct level (`EMAIL_DOMAIN_TO_PROJECT`) not mock routing too early | Task 1 (build-fix): use `monkeypatch.setattr(routing_module, "EMAIL_DOMAIN_TO_PROJECT", ...)` | Matches existing `EMAIL_TO_PROJECT` pattern; exercises actual domain lookup code path in `find_project_for_email()` |
| CONCERN | Adversary, Operator | `test_worker_startup.py` callback dict isolation — `register_callbacks()` writes into module-level dicts; cross-test bleed if not cleared | Task 1 (build-fix): extract gate to `_should_register_email_handler()` helper and test that directly | No worker imports, no Redis, no callback dict pollution; alternatively `monkeypatch.setattr("agent.agent_session_queue._send_callbacks", {})` per test |
| CONCERN | Skeptic, Simplifier | Task 1 task description never says to create `_should_register_email_handler()` in `worker/__main__.py`; builder could write tests against the helper but forget to add and wire it | Task 1 (build-fix): explicit sub-bullet added — "Add `_should_register_email_handler(project_cfg: dict) -> bool` at module scope in `worker/__main__.py` and update the registration loop to call it" | Helper defined at module scope; called as `if _should_register_email_handler(project_cfg):` in the existing for-loop; body is the 2-line condition from Technical Approach |
| NIT | — | Verification table missing `test_agent_session_queue.py` row | Already present at line 291 of original plan | No change needed |

---

## Open Questions

None — the fix is fully scoped. The narrow gate-widening approach is correct.
Implementation can proceed directly to build.
