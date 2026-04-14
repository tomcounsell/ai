---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/946
last_comment_id:
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

**Notes:** All cited line numbers and code claims remain accurate against current main.

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

**New unit tests in `tests/unit/test_worker_startup.py`:**
- Project with `email.contacts` only → handler registered (regression guard for current behavior)
- Project with `email.domains` only → handler registered (the failing case)
- Project with both contacts and domains → handler registered
- Project with neither → handler NOT registered
- Project with empty dicts `{"email": {"contacts": {}, "domains": []}}` → NOT registered

**Extended domain tests in `tests/unit/test_email_routing.py`:**
- `build_email_to_project_map()` returns correct domain_map for domain-only projects
- `find_project_for_email()` domain fallback lookup works correctly
  (Note: `test_email_routing.py` currently only tests contacts; domain path is untested)

**Integration test extension in `tests/integration/test_email_bridge.py`:**
- New test class `TestDomainRoutedEmailReply` covering:
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

- [ ] `tests/unit/test_email_routing.py` — UPDATE: add domain-map tests (currently no domain test coverage); existing contact tests unaffected
- [ ] `tests/integration/test_email_bridge.py` — UPDATE: add `TestDomainRoutedEmailReply` class; existing tests unaffected
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add transport-keyed callback resolution tests; existing tests unaffected

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

- [ ] Update `docs/features/email-bridge.md` — the "Configuration" section currently only documents `email.contacts` routing; add a parallel `email.domains` configuration example and explain that either field (or both) enables `EmailOutputHandler` registration and inbound routing
- [ ] Update `docs/features/email-bridge.md` — add or expand a "Worker Registration" section that explicitly states the registration gate condition: handler is registered when `email.contacts` OR `email.domains` is non-empty, explaining why this matches the inbound routing logic in `bridge/routing.py`
- [ ] Update `docs/features/README.md` — the existing entry for `email-bridge.md` mentions "sender-based project routing"; update to clarify it covers both contact-based and domain-based routing

## Success Criteria

- [ ] Worker registers `EmailOutputHandler` for projects with `email.domains` only
- [ ] Worker registers `EmailOutputHandler` for projects with `email.contacts` only (no regression)
- [ ] Worker registers `EmailOutputHandler` for projects with both
- [ ] Worker does NOT register for projects with neither
- [ ] SMTP reply has correct `In-Reply-To` and `References` headers for threading
- [ ] "No bridge callbacks registered" log line is NOT emitted for email sessions
- [ ] Unit tests: all four registration permutations covered
- [ ] Domain routing unit tests added to `test_email_routing.py`
- [ ] Integration test: domain-routed inbound → `_send_smtp` called with correct headers
- [ ] Existing email tests still pass (no regressions)
- [ ] Tests pass (`pytest tests/unit/test_worker_startup.py tests/unit/test_email_routing.py tests/integration/test_email_bridge.py -q`)
- [ ] Lint clean (`python -m ruff check .`)

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
- **Validates**: tests/unit/test_worker_startup.py (create), tests/unit/test_email_routing.py (update), tests/integration/test_email_bridge.py (update), tests/unit/test_agent_session_queue.py (update)
- **Assigned To**: worker-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `worker/__main__.py:237-238` to widen the gate condition
- Create `tests/unit/test_worker_startup.py` with all four registration permutations
- Add domain tests to `tests/unit/test_email_routing.py`
- Add `TestDomainRoutedEmailReply` to `tests/integration/test_email_bridge.py`
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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the fix is fully scoped. The narrow gate-widening approach is correct.
Implementation can proceed directly to build.
