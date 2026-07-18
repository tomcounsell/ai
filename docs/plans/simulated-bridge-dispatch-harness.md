---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2159
last_comment_id: none
---

# Simulated Bridge Dispatch Harness

Extract the transport-agnostic dispatch decision (steer running / steer pending / resume completed with context / new session) out of the Telethon handler closure into an importable function driven by a plain dataclass, and add a simulated-bridge integration harness that drives multi-turn reply-to scenarios end-to-end against test Redis.

## Problem

The bridge's dispatch decision has regressed repeatedly (#567 → #919 → #949 → #1064 → #1836 → #2136) because it lives as inline control flow inside `async def handler(event)` (`bridge/telegram_bridge.py:1152`), a ~900-line closure over the live Telethon client. No test can import it, so every fix is verified only in production, and every refactor silently re-breaks one of the multi-turn invariants.

**Current behavior:**
- The steer/pending-steer/resume-completed decision (`bridge/telegram_bridge.py:1755–2000`) and the coalescing guards (`:1617–1623`, `:2002+`) are unreachable from tests. Only their leaf primitives (dedup, steering push, enqueue, context builders) have coverage.
- Four multi-turn invariants have zero end-to-end tests: reply resumes the original session; reply mid-run becomes steering; reply after completion resumes with prior context (including the live-session re-check race at `:1830–1855`); rapid follow-ups coalesce.

**Desired outcome:**
- The decision is an importable function taking a plain inbound-message dataclass plus injected transport ports — no Telethon types in its signature or module.
- A simulated-bridge harness in `tests/integration/` scripts multi-turn message sequences through the real decision + real Redis (isolated test DB) and asserts the four invariants.
- The Telegram handler shrinks to parsing + one call; behavior is bit-for-bit preserved and pinned by the new scenario tests.

## Freshness Check

**Baseline commit:** `ab6e517374519c8d2379df95dd10a9d6f4660d5e`
**Issue filed at:** 2026-07-18T15:02:36Z (same day as this plan; zero commits on main since)
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1152` — handler closure start — still holds
- `bridge/telegram_bridge.py:1755–2000` — reply-to steer/pending/resume-completed branch — still holds (read in full at plan time)
- `bridge/telegram_bridge.py:1826–1863` — live-session re-check guard + dedup short-circuit inside resume-completed — still holds (exact region `:1830–1871`)
- `bridge/telegram_bridge.py:787` — `_build_completed_resume_text` module-level — still holds
- `bridge/context.py:536` — `resolve_root_session_id(client, chat_id, reply_to_msg_id, project_key)` — still holds
- `bridge/dispatch.py:84` — `dispatch_telegram_session` claim→enqueue→dedup wrapper — still holds

**Cited sibling issues/PRs re-checked:** #2136 closed 2026-07-17 (goal re-injection on resume — merged; its `_build_completed_resume_text` path is part of what this harness pins). #2147 (test/live notify isolation) open, plan on main — adjacent, not blocking.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** `test-suite notify isolation` (#2147) touches test-suite/worker isolation, not bridge dispatch — coordination signal only: our harness must follow whatever db-scoped notify convention it lands.

## Prior Art

- **#567**: Reply-to should resume original AgentSession — introduced reply-based session continuity.
- **#919 / #949 / #1064**: split sessions, missing thread history on reply-to — added `resolve_root_session_id` chain walking and reply-chain context.
- **#997**: duplicate enqueue on reply-chain timeout — added the `_steering_session_enqueued` sentinel (`bridge/telegram_bridge.py:1759`).
- **#318 / #705 / #449**: semantic routing into active sessions; in-memory coalescing guard for rapid-fire messages (`_recent_session_by_chat`).
- **#730**: terminal-status guard on intake path (prevents completed→superseded cycling).
- **#1836 / #2136**: reply-to drops and goal-less resumes — latest recurrences, both fixed inline in the closure.
- **#1574**: `--await-reply` live E2E probe — tests the real bridge over a live Telegram connection; not CI-runnable, complements (does not replace) this harness.
- **`tests/unit/test_bridge_dispatch_contract.py`**: AST-level guards asserting the handler contains no direct enqueue/dedup calls outside `dispatch_telegram_session` — static shape checks this plan must keep passing.

## Research

No relevant external findings — purely internal refactor + test harness; proceeding with codebase context. (Phase 0.7 skipped per skill rule: no external libraries, APIs, or ecosystem patterns involved.)

## Spike Results

### spike-1: Extraction boundary of the reply-to decision branch
- **Assumption**: "The steer/pending/resume-completed decision reads only dataclass-expressible inputs plus Redis, with Telethon needed solely for side effects."
- **Method**: code-read (`bridge/telegram_bridge.py:1490–2020` read in full at plan time)
- **Finding**: Confirmed. The branch reads scalars already computed by the handler (`is_reply_to_valor`, `message.reply_to_msg_id`, `message.id`, `message.date`, `event.chat_id`, `session_id`, `project_key`, `project` dict, `chat_title`, `sender_name`, `sender_id`, `clean_text`, `safe_clean_text`, `stored_msg_id`) plus `AgentSession.query` and `is_duplicate_message`. Telethon objects are needed by exactly two effects: `_ack_steering_routed` (only for the reaction ack — its steering push + dedup core is transport-agnostic) and `fetch_reply_chain(client, …)`. Both are injectable ports.
- **Confidence**: high
- **Impact on plan**: the decision extracts cleanly behind an `InboundMessage` dataclass + two injected callables; no fake Telethon layer needed.

### spike-2: `resolve_root_session_id` cache path works without a client
- **Assumption**: "The Redis-cache walk (Steps 0–1) never touches the Telethon client; only the API fallback does."
- **Method**: code-read (`bridge/context.py:536`+, corroborated by recon for #2159)
- **Finding**: Confirmed — the client is used only in the Step-2 API fallback via `fetch_reply_chain`. An optional `client=None` short-circuit (cache-only mode) is a minimal additive seam.
- **Confidence**: high
- **Impact on plan**: harness drives the cache path with seeded `TelegramMessage` records; no fake client required.

## Data Flow

1. **Entry point**: Telegram message arrives → Telethon `handler(event)` parses text, media flags, sender, computes `is_reply_to_valor`, resolves `session_id` (reply chain → `resolve_root_session_id`; else semantic routing → fresh ID).
2. **Intake decision (extracted by this plan)**: `route_reply_intake(msg: InboundMessage, ports: IntakePorts)` — checks `AgentSession` by (session_id, status) in order running/active → pending → completed; applies the live re-check guard, dedup short-circuit, and #997 sentinel.
3. **Steering path**: transport-agnostic core of `_ack_steering_routed` → `push_steering_message` (`agent/steering.py:37`) + dedup record; transport ack (reaction) fires via injected port.
4. **Resume path**: `fetch_reply_chain` port → `format_reply_chain` → `_build_completed_resume_text` → `dispatch_telegram_session` (`bridge/dispatch.py:84`: claim → enqueue → dedup record).
5. **New-session path**: terminal-status guard (#730) → in-memory coalescing guard (#705) → `dispatch_telegram_session`.
6. **Output**: `AgentSession` in Redis queue → standalone worker executes; steering messages drained at turn boundary.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #567 | Made reply-to resume the original session | Logic landed inline in the closure; next routing refactor (#919) split sessions again |
| #919/#949/#1064 | Root-ID resolution + thread history on resume | Primitives got unit tests but the decision consuming them stayed untested; #1836 re-broke delivery |
| #1836 | Fixed silent drops + resume for granite sessions | Fixed at the classifier/session layer; the closure's decision remained unpinned |
| #2136 | Re-injected goal/context on resume | Correct fix, again inline; nothing prevents the next refactor from dropping it |

**Root cause pattern:** every fix patches inline closure logic that no test can import. The fix layer is right; the missing piece is an importable seam plus scenario tests that make regressions loud at PR time. This plan adds the seam and the tests rather than another behavior patch.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: new module `bridge/intake_decision.py` (dataclass + ports + decision function); `resolve_root_session_id` gains an optional cache-only mode (`client=None`) — additive, default behavior unchanged; `_ack_steering_routed` splits into a transport-agnostic core + a thin Telethon wrapper keeping its current signature.
- **Coupling**: decreases — the decision no longer closes over the Telethon client; email convergence (#2160) becomes possible later.
- **Data ownership**: unchanged — AgentSession/steering/dedup Redis keys keep their owners.
- **Reversibility**: high — behavior-preserving extraction; reverting restores the inline branch.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (open-questions round, pre-build confirmation)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running locally | `redis-cli ping` | Integration tests use the isolated test DB via `redis_test_db` fixture |

## Solution

### Key Elements

- **`InboundMessage` dataclass** (`bridge/intake_decision.py`): the transport-agnostic projection of an inbound message — text, safe_text, chat_id, message_id, reply_to_msg_id, is_reply_to_valor, sender_name, sender_id, chat_title, message_ts, project_key, project config, stored_msg_id.
- **`IntakePorts`**: injected effects — `ack_steer(...)` (transport ack; Telegram supplies the reaction, tests supply a recorder), `fetch_reply_chain(chat_id, reply_to_msg_id)` (Telegram wraps the Telethon call, tests return canned chains), `enqueue(...)` (defaults to `dispatch_telegram_session`).
- **`route_reply_intake(msg, ports) -> RouteResult`**: the extracted decision — running/active steer → pending steer → completed resume (live re-check guard, dedup short-circuit, chain hydration, `_build_completed_resume_text`, dispatch, #997 sentinel semantics) → signal fall-through. Returns a small result enum (`STEERED_LIVE | STEERED_PENDING | STEERED_LIVE_GUARD | RESUMED_COMPLETED | DUPLICATE_SKIPPED | FALL_THROUGH`) for logging and test assertions.
- **Coalescing guard extraction**: the `_recent_session_by_chat` in-memory guard becomes a small `RecentSessionGuard` class with an injectable clock, used by the handler and drivable by tests.
- **Simulated-bridge harness** (`tests/integration/test_simulated_bridge_dispatch.py` + a `SimulatedBridge` helper): constructs `InboundMessage` sequences, seeds `AgentSession`/`TelegramMessage` records in the test Redis, runs the real decision with recording ports, and asserts session counts, steering-queue contents, and resume-text content.

### Flow

**Inbound message (any transport)** → handler parses into `InboundMessage` → `route_reply_intake` consults AgentSession state → **steer** (push + dedup + ack port) / **resume** (chain port + context build + dispatch) / **fall through** → new-session path with terminal + coalescing guards → **AgentSession enqueued for worker**.

### Technical Approach

- Extraction is **behavior-preserving**: move the branch at `bridge/telegram_bridge.py:1755–2000` verbatim into `route_reply_intake`, replacing Telethon touches with port calls. Preserve the #997 sentinel semantics (on port/Redis exceptions after dispatch, do not fall through), status-check order, the `max(created_at)` completed-record selection, and the `reply_chain_hydrated` extra-context flag.
- Split `_ack_steering_routed` (`bridge/telegram_bridge.py:890`): transport-agnostic core (abort detection, `push_steering_message`, dedup record, chat-log write) moves to the new module or `bridge/dispatch.py`; the Telethon wrapper keeps the reaction ack and existing call sites.
- `resolve_root_session_id` gains `client: TelegramClient | None` — `None` skips the Step-2 API fallback (cache-only). Harness seeds the Redis message cache instead of faking Telethon.
- Handler keeps: event parsing, media enrichment, reactions, revival replies, semantic routing (#318) — all Telegram-specific, out of scope.
- Harness respects the #2147 notify-isolation conventions (db-scoped fixtures already standard in `tests/integration/`).
- Follow the transport-keyed callback convention (`docs/sdlc/do-plan.md`) for any port registration.
- Blast radius (hand-traced; `tools.code_impact_finder` timed out at plan time): modify `bridge/telegram_bridge.py`, `bridge/context.py`, `bridge/dispatch.py` (or new `bridge/intake_decision.py`); tests `tests/unit/test_bridge_dispatch_contract.py`; add `tests/integration/test_simulated_bridge_dispatch.py`; docs `docs/features/`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The extracted branch keeps its two broad handlers (`ConnectionError/OSError` and `Exception` at `bridge/telegram_bridge.py:1979–2000`) — each gets a scenario test asserting the observable behavior: post-dispatch exception → no second enqueue (#997 sentinel); pre-dispatch exception → fall-through to the new-session path with an ERROR log.
- [ ] Reply-chain fetch timeout/exception (`RESUME_REPLY_CHAIN_FAIL`) → test asserts resume still dispatches with summary-only preamble and a WARNING.

### Empty/Invalid Input Handling
- [ ] `InboundMessage` with empty/whitespace text follows the existing `--empty message--` normalization (asserted in harness setup, not re-implemented).
- [ ] Reply to a session_id with no AgentSession records in any status → falls through to new-session path (test).

### Error State Rendering
- [ ] Steering-ack port failure does not lose the steering message (push happens before ack; test asserts queue contents when ack port raises).

## Test Impact

- [ ] `tests/unit/test_bridge_dispatch_contract.py` — UPDATE: AST guards scan the handler for banned direct enqueue/dedup calls; extend them to also scan `bridge/intake_decision.py` so the invariant (all enqueues via `dispatch_telegram_session`, steering paths record dedup) follows the code to its new home.
- [ ] `tests/integration/test_steering.py` — UPDATE (minor): `resolve_root_session_id` tests gain one case for `client=None` cache-only mode; existing cases unchanged (param is additive with a default).

No other existing tests affected — the extraction is behavior-preserving and all other coverage targets leaf primitives whose signatures do not change.

## Rabbit Holes

- **Faking Telethon** — do not build a FakeTelegramClient or fabricate Telethon event objects; the port seam + cache-only resolver mode makes them unnecessary. (Issue Recon explicitly dropped this.)
- **Email-bridge convergence** — tempting while touching the seam; it is #2160, not this plan.
- **Edit-handler steering** (`bridge/telegram_bridge.py:2515`) — independently re-implements steer; Telegram-specific, leave untouched.
- **Semantic routing extraction** (#318 branch at `:1526–1585`) — depends on `find_matching_session` LLM calls; pulling it into the harness drags in model mocking. Leave in the handler.
- **Refactoring the rest of the closure** — the handler has ~2,000 more lines of enrichment/reaction/revival logic; extract only the decision branch.

## Risks

### Risk 1: Extraction subtly changes behavior (the exact bug class this plan exists to stop)
**Impact:** A fifth regression in the reply-to lineage, self-inflicted.
**Mitigation:** Move code verbatim; scenario tests written against the CURRENT inline behavior first (on a branch commit before the extraction), then the extraction must keep them green. Reviewer diffs the moved block against the original.

### Risk 2: AST contract guards fight the new module layout
**Impact:** `test_bridge_dispatch_contract.py` fails or, worse, silently stops guarding the moved code.
**Mitigation:** Task 1 explicitly extends the guards to the new module; Verification includes the contract test file.

### Risk 3: Harness couples to Popoto/Redis internals and rots
**Impact:** Tests break on unrelated model changes, get skipped, blind spot returns.
**Mitigation:** Harness only uses public seams: Popoto ORM models, `push_steering_message`/`pop_all_steering_messages`, `dispatch_telegram_session`, and the new ports. No raw Redis (enforced repo-wide by the no-raw-redis hook).

## Race Conditions

### Race 1: Completed-resume vs concurrently created live session
**Location:** `bridge/telegram_bridge.py:1830–1855` (moves into `route_reply_intake`)
**Trigger:** Two rapid replies to a completed session; the first re-enqueues (pending) while the second is between its status checks.
**Data prerequisite:** Live-guard re-check must run against current Redis state after the completed lookup.
**State prerequisite:** Guard order pending→running→active preserved.
**Mitigation:** Preserved verbatim; scenario test injects a live record via a port hook between resolution and dispatch and asserts the result flips to `STEERED_LIVE_GUARD`.

### Race 2: Rapid-fire duplicate replies to the same completed session
**Location:** dedup short-circuit `bridge/telegram_bridge.py:1857–1871`
**Trigger:** Same (chat_id, message_id) processed twice before Redis dedup write completes.
**Data prerequisite:** `is_duplicate_message` checked before reply-chain fetch.
**Mitigation:** Preserved; test asserts second identical message returns `DUPLICATE_SKIPPED` with exactly one enqueue.

### Race 3: In-memory coalescing window vs Redis visibility (#705)
**Location:** `_recent_session_by_chat` set at `:1617–1623`, read at `:2002+`
**Trigger:** Two non-reply messages <200ms apart; second must see first's session before its Redis write lands.
**Data prerequisite:** Guard dict entry set before any await on the enqueue path.
**Mitigation:** `RecentSessionGuard` with injectable clock; test drives two messages with a frozen clock and asserts one session.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2160] Email-bridge convergence on the extracted decision (steering support + shared dedup wrapper for `_process_inbound_email`) — filed as #2160, blocked on this plan merging. Anti-criterion in Verification: this PR must not touch `bridge/email_bridge.py`.

Everything else raised during recon (Telethon failure modes, edit-handler, semantic-routing extraction, full closure refactor) is a permanent anti-goal or rabbit hole documented above, not deferred work.

## Update System

No update system changes required — this is an internal refactor plus tests: no new dependencies, config files, launchd services, or migrations. No Popoto model changes (no `scripts/update/migrations.py` entry needed). Deployed machines pick it up via the normal `/update` git pull; the bridge must be restarted after deploy per the standard restart rule (already part of `/update`).

## Agent Integration

No agent integration required — this is a bridge-internal refactor and test harness. No new CLI entry point in `pyproject.toml [project.scripts]`, no MCP server or `.mcp.json` changes. The bridge continues to call the extracted function via direct Python import (`bridge/telegram_bridge.py` → `bridge/intake_decision.py`), which is one of the two sanctioned integration paths.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/simulated-bridge-dispatch-harness.md` — the decision seam (dataclass + ports), the RouteResult vocabulary, how to add a scenario test, and the boundary with the #1574 live probe
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/session-steering.md` — note the steering-routing core's new home
- [ ] Update `tests/README.md` — move `bridge/telegram_bridge.py` blind-spot entry to "partially covered" with a pointer to the harness

### Inline Documentation
- [ ] Module docstring on `bridge/intake_decision.py` mapping each branch to its origin issue (#567, #997, #730, #705, #2136)

## Success Criteria

- [ ] `route_reply_intake` importable with no Telethon types in `bridge/intake_decision.py`
- [ ] Scenario: reply to a prior Valor message resolves to the original session and creates no second AgentSession
- [ ] Scenario: reply while session is running/active lands in the steering queue, not the session queue
- [ ] Scenario: reply after completion dispatches a resume whose message_text contains the prior goal/context; flips to steer when a live session appears mid-decision
- [ ] Scenario: two rapid messages coalesce into one session
- [ ] `test_bridge_dispatch_contract.py` guards extended to the new module and passing
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

- **Builder (intake-decision)**
  - Name: intake-builder
  - Role: Extract the decision module, port seams, resolver cache-only mode, guard class; keep contract tests green
  - Agent Type: builder
  - Resume: true

- **Test Engineer (harness)**
  - Name: harness-tester
  - Role: SimulatedBridge helper + scenario/race tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator (dispatch)**
  - Name: dispatch-validator
  - Role: Verify behavior preservation, run full bridge test subset, check success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: harness-documentarian
  - Role: Feature doc, index, tests/README blind-spot update
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Pin current behavior
- **Task ID**: build-baseline-tests
- **Depends On**: none
- **Validates**: tests/integration/test_simulated_bridge_dispatch.py (create)
- **Informed By**: spike-1 (extraction boundary confirmed), spike-2 (cache-only resolver)
- **Assigned To**: harness-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Write the SimulatedBridge helper and the four scenario tests + three race tests against the CURRENT code, driving the smallest reachable seams (they will call the extracted function once it exists; initially mark the not-yet-reachable ones xfail with `# pending extraction` runtime-free decorators only)
- Seed helpers: AgentSession factory per status, TelegramMessage cache seeding for `resolve_root_session_id`

### 2. Extract the decision
- **Task ID**: build-intake-decision
- **Depends On**: build-baseline-tests
- **Validates**: tests/integration/test_simulated_bridge_dispatch.py, tests/unit/test_bridge_dispatch_contract.py, tests/integration/test_steering.py
- **Informed By**: spike-1, spike-2
- **Assigned To**: intake-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/intake_decision.py`: `InboundMessage`, `IntakePorts`, `RouteResult`, `route_reply_intake` (verbatim move of `:1755–2000`)
- Split `_ack_steering_routed` core; add `client=None` mode to `resolve_root_session_id`; extract `RecentSessionGuard`
- Replace the handler branch with the single call; remove all xfail markers from Task 1 tests (convert to hard assertions)
- Extend AST contract guards to the new module

### 3. Validate behavior preservation
- **Task ID**: validate-dispatch
- **Depends On**: build-intake-decision
- **Assigned To**: dispatch-validator
- **Agent Type**: validator
- **Parallel**: false
- Diff the moved block against the original for semantic drift; run `scripts/pytest-clean.sh tests/unit tests/integration -q`; confirm zero remaining xfails in the new test file; report pass/fail per success criterion

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-dispatch
- **Assigned To**: harness-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Feature doc, README index row, session-steering doc note, tests/README blind-spot update

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: dispatch-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; verify all success criteria including docs; generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Scenario tests pass | `scripts/pytest-clean.sh tests/integration/test_simulated_bridge_dispatch.py -q` | exit code 0 |
| Contract guards pass | `scripts/pytest-clean.sh tests/unit/test_bridge_dispatch_contract.py -q` | exit code 0 |
| Full suite | `scripts/pytest-clean.sh tests/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No Telethon in decision module | `grep -ci "telethon" bridge/intake_decision.py` | match count == 0 |
| Handler delegates to extracted fn | `grep -c "route_reply_intake" bridge/telegram_bridge.py` | output > 0 |
| No stale pending-extraction xfails | `grep -rn "pending extraction" tests/integration/test_simulated_bridge_dispatch.py \| wc -l` | match count == 0 |
| Anti-criterion: email bridge untouched (#2160 stays separate) | `git diff --name-only origin/main...HEAD \| grep -c "bridge/email_bridge.py"` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Module home**: new `bridge/intake_decision.py` (recommended — `bridge/dispatch.py` stays the thin enqueue+dedup wrapper) vs. growing `bridge/dispatch.py`. Any preference?
2. **Fall-through scope**: the plan extracts the reply-to branch and the coalescing guard but leaves the non-reply new-session assembly (terminal guard at `:1592–1615` + final enqueue) in the handler, calling shared helpers. Extracting that too would make the whole intake decision one function but roughly doubles the moved surface. Keep minimal (recommended) or extract the full intake path in one pass?
3. **Baseline-first ordering**: Task 1 writes scenario tests before the extraction (some initially xfail until the seam exists). Acceptable, or would you rather extract first and write tests after (loses the behavior-pinning property)?
