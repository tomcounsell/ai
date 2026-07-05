---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1899
last_comment_id:
---

# Bridge Classification Fails With "No Anthropic API key found" (Async Path)

## Problem

Sentry VALOR-BZ reports `Classification failed (async): No Anthropic API key found for
classification` on the production bridge (`Valor-the-Captain.local`), 6 events in 4 days.
The bridge otherwise runs fine, so the key is not globally absent — a specific async
classification call resolves an empty key and logs at ERROR level, feeding Sentry.

**Current behavior:**
- The exact Sentry string originates at `tools/classifier.py:219` — the `except Exception`
  handler of `classify_request_async`, re-raising the `ValueError` raised at line 171 when
  `get_anthropic_api_key()` returns empty. This is the **work-type / SDLC-routing**
  classifier, invoked from the non-blocking background task `classify_work_type()` at
  `bridge/telegram_bridge.py:1641-1660`. That task's own handler (line 1653-1654) catches
  the failure as non-fatal (`logger.debug("Work classification failed (non-fatal)")`), so
  the inbound message is **not** dropped — the work-type just defaults downstream. The
  visible damage is Sentry noise at ERROR level, not a dropped message.
- The issue attributes the error to `classify_conversation_terminus` / `should_respond`.
  That is a **misdiagnosis**: the terminus classifier
  (`bridge/routing.py:942-962`) guards its Haiku fallback with `if api_key:` and returns
  the conservative default `"RESPOND"` on any failure — it never emits "Classification
  failed (async)". Its missing-key→RESPOND path is already covered by
  `tests/unit/test_routing.py:197` (`test_classify_terminus_ollama_failure_defaults_to_respond`),
  though that test uses `None`, not the empty-string `""` production returns.
- **Why intermittent, why it persists:** `get_anthropic_api_key()`
  (`utils/api_keys.py`) caches the resolved value in the module-level
  `_cached_anthropic_key`, **including an empty resolution** (`_cached_anthropic_key = ""`
  at the tail). Once a process resolves the key empty (a startup window where the env var
  is empty AND none of the three `.env` paths are readable yet — LaunchAgent env vs. `.env`
  sourcing race), that empty value is cached and poisons **every** subsequent classification
  in that process until restart. The 6 events cluster into a few such windows across bridge
  restarts. The intent classifier (`classify_message_intent_async`) already defaults to
  `new_work` on any error and never emits this string.

**Desired outcome:**
- A transient missing-key window self-heals: once the env/`.env` is populated, the next
  classification call resolves the real key instead of a cached empty string.
- The missing-key case degrades quietly (WARNING, not ERROR → no Sentry noise) while
  genuine API/parse errors keep their ERROR-level visibility.
- Every async classification entry point used on an inbound bridge message provably
  preserves the message when the key is missing: terminus → `RESPOND`, work-type →
  non-fatal default, intent → `new_work`. Regression tests lock this in against the
  empty-string production condition.

## Freshness Check

**Baseline commit:** `63e43118`
**Issue filed at:** 2026-07-04T15:46:00Z
**Disposition:** Minor drift (issue's root-cause attribution corrected; underlying concern real)

**File:line references re-verified:**
- `tools/classifier.py:171` — raises `ValueError("No Anthropic API key found for
  classification")` in `classify_request_async` — still holds.
- `tools/classifier.py:219` — `logger.error(f"Classification failed (async): {e}")` — still
  holds; this is the exact Sentry string source, and the ONLY occurrence of that string in
  non-test code.
- `bridge/routing.py:942-962` — terminus Haiku fallback guarded by `if api_key:` with a
  `RESPOND` conservative default — still holds. The issue's attribution to this path is a
  misdiagnosis: it cannot produce the reported error string.
- `bridge/telegram_bridge.py:1641-1660` — `classify_work_type()` background task calling
  `classify_request_async`, failure caught non-fatal at 1653-1654 — still holds.
- `utils/api_keys.py:get_anthropic_api_key` — caches empty resolutions (`_cached_anthropic_key = ""`)
  — still holds; this is the intermittency/persistence mechanism.

**Cited sibling issues/PRs re-checked:**
- #1836 (bare-link dropped-message class) — referenced in `routing.py` Fast-Path 1.5; a
  different bug (LLM REACT misclassification), not the missing-key path. No overlap with
  the fix.
- #1090, #1318 — terminus fast-path history; unrelated to key resolution.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=2026-07-04T15:46:00Z -- tools/classifier.py bridge/routing.py
utils/api_keys.py agent/anthropic_client.py bridge/telegram_bridge.py` is empty).

**Active plans in `docs/plans/` overlapping this area:** none. (`consolidate_delivery_paths.md`
touches delivery, not classification key resolution.)

**Notes:** The bug is real but narrower than the issue framed it — it is Sentry noise + a
cache-poisoning intermittency amplifier, not a message-dropping defect. The correlation
with the wedged Cuttlefish session mentioned in the issue is coincidental: the work-type
classification failure is non-fatal and does not affect delivery. The plan proceeds on the
corrected premise and still satisfies all three acceptance criteria.

## Prior Art

- **#1182** (closed): JSON sidecar cache for deterministic Haiku call sites (intent
  classification + knowledge indexer) — touched the same classifier module but for caching
  responses, not key resolution. No conflict.
- **#1225 / PR #1225** (merged): empty-promise gate across delivery paths — a
  "never silently drop" hardening in a different layer; establishes the repo's preference
  for message-preserving defaults, which this plan follows.
- No prior issue or PR addressed the empty-key cache poisoning in `utils/api_keys.py`.

## Data Flow

1. **Entry point:** Inbound Telegram message → `bridge/telegram_bridge.py` handler.
2. **Terminus decision (reply-to path):** `classify_conversation_terminus` → fast-paths →
   Ollama → Haiku fallback (guarded `if api_key:`) → `RESPOND` default. Missing key here is
   already safe (RESPOND).
3. **Work-type classification (background, non-blocking):** `classify_work_type()` →
   `classify_request_async` → `get_anthropic_api_key()`. On empty key: raises → caught
   ERROR at classifier.py:219 (Sentry) → re-caught non-fatal at bridge:1653 → work-type
   defaults. Message still enqueues.
4. **Intent classification (active-session path):** `classify_message_intent_async` →
   `get_anthropic_api_key()`. On empty key: caught internally → defaults `new_work`.
   Message still enqueues.
5. **Key resolution (shared by all three):** `get_anthropic_api_key()` reads env, then
   three `.env` paths, then caches — **including empty**. This is the single shared point
   whose empty-caching turns a startup race into a persistent per-process failure.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0-1 (confirm the corrected root-cause framing is acceptable)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` present in a `.env` path | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Confirms the resolver finds a real key locally so tests exercise the populated path |

Run via `python scripts/check_prerequisites.py docs/plans/bridge-classification-missing-api-key.md`.

## Solution

### Key Elements

- **`get_anthropic_api_key()` self-heals:** only cache a truthy resolution; an empty result
  is returned but not cached, so the next call re-reads env/`.env` after the LaunchAgent env
  and `.env` sourcing settle.
- **Missing-key degrades quietly in `classify_request_async` (and its sync twin
  `classify_request`):** distinguish "no API key" from real API/parse errors. Missing key →
  log at WARNING and return a safe, low-confidence default (message-preserving), instead of
  raising a `ValueError` that surfaces as an ERROR-level Sentry event. Genuine API errors and
  JSON-decode failures keep ERROR-level logging.
- **Explicit RESPOND / non-drop regression coverage:** lock in that all three inbound
  classification entry points preserve the message when the key is empty (`""`, the real
  production value — not just `None`).

### Flow

Inbound message → classification call resolves key → **populated:** normal classification →
**empty (transient):** WARNING + message-preserving default (RESPOND / non-fatal / new_work),
cache not poisoned → next message re-resolves the real key → normal classification resumes.

### Technical Approach

- `utils/api_keys.py`: change the early-return guard to short-circuit only on a truthy
  cached value, and drop the `_cached_anthropic_key = ""` assignment on the empty tail
  (return `""` without caching). Behavior for a populated key is unchanged (still cached
  once). This is the highest-leverage change: it removes the persistence amplifier.
- `tools/classifier.py`: in `classify_request_async` and `classify_request`, replace the
  `if not api_key: raise ValueError(...)` with a missing-key branch that logs WARNING and
  returns a safe default dict (e.g. `{"type": "chore", "confidence": 0.0, "reason": "no
  anthropic api key — classification skipped"}` — the exact default type chosen so the
  downstream routing behaves conservatively; validate against `bridge` routing defaults
  during build). The outer `except Exception` ERROR log stays for real failures. Confirm the
  bridge caller (`bridge/telegram_bridge.py:1646`) still handles the returned default
  gracefully (it reads `result.get("type")`, so a default dict is fine).
- No change to `classify_conversation_terminus` logic (already correct); only add a
  regression test for the empty-string case.
- `agent/anthropic_client.py::anthropic_slot()` constructs `AsyncAnthropic(api_key=...)` with
  whatever the resolver returns — the cache fix means it stops receiving a stale empty string,
  so its call sites (intent async, etc.) benefit automatically. No code change there.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/classifier.py:218-220` (`except Exception` → ERROR) — after the fix, add a test
  asserting the **missing-key** path does NOT reach this ERROR log (asserts WARNING instead),
  and a separate test asserting a **real** API error still logs at ERROR.
- [ ] `bridge/telegram_bridge.py:1653-1654` (non-fatal catch) — covered indirectly; assert
  `classify_work_type` leaves the message enqueue path intact when classification returns the
  default.

### Empty/Invalid Input Handling
- [ ] `get_anthropic_api_key()` with empty env and no readable `.env` returns `""` and does
  NOT cache it (subsequent populated call returns the real key).
- [ ] `classify_request_async("")` and `classify_request_async(<text>)` with empty key return
  the safe default dict without raising.

### Error State Rendering
- [ ] The missing-key case produces a WARNING log line (observable), not a silent swallow and
  not an ERROR/Sentry event. Assert via `caplog`.

## Test Impact

- [ ] `tests/unit/test_routing.py::test_classify_terminus_ollama_failure_defaults_to_respond` —
  UPDATE: add/parametrize an empty-string (`""`) key case alongside the existing `None` case,
  asserting `RESPOND` — matches the real production value.
- [ ] `tests/unit/test_work_request_classifier.py` — UPDATE: add cases asserting
  `classify_request` / `classify_request_async` return the safe default dict on missing key
  (no raise) and log at WARNING (not ERROR). Existing populated-key tests are unaffected.
- [ ] `tests/unit/test_api_keys.py` — CREATE: new file covering `get_anthropic_api_key`
  no-cache-on-empty self-healing and truthy-caching behavior. Must reset the module-level
  `_cached_anthropic_key` between cases (monkeypatch to `None`).

No other existing tests assert the current raise-on-missing-key behavior of
`classify_request_async`, so nothing needs DELETE/REPLACE.

## Rabbit Holes

- **Do not** rearchitect env/`.env` loading order or the LaunchAgent environment plumbing.
  The cache-poisoning fix makes the process self-heal regardless of which source eventually
  supplies the key; chasing the exact startup race is disproportionate for a 6-in-4-days
  noise bug.
- **Do not** convert `classify_request_async` to route through `anthropic_slot()` in this
  change — that is an orthogonal consolidation and would widen the blast radius.
- **Do not** try to eliminate the terminus classifier's separate Haiku client in favor of
  the shared slot here; it is correct and tested. Leave it.

## Risks

### Risk 1: Chosen safe-default `type` misroutes work-type classification
**Impact:** If the default type is wrong, PR/issue messages could be mis-bucketed when the
key is transiently missing.
**Mitigation:** The synchronous PR/issue fast-path at `bridge/telegram_bridge.py:1668-1674`
already forces `type="sdlc"` for issue/PR references independent of the async classifier, so
the default only affects genuinely ambiguous messages during a short self-healing window.
Pick the most conservative default and document it inline.

### Risk 2: Cache change alters behavior for a legitimately keyless deployment
**Impact:** A deployment with no key would re-read `.env` files on every call (minor extra
I/O) instead of once.
**Mitigation:** Acceptable — keyless deployments are not a supported production state, and the
extra cost is three `Path.exists()` checks per call. Populated deployments still cache once.

## Race Conditions

### Race 1: Startup env/.env sourcing vs. first classification
**Location:** `utils/api_keys.py::get_anthropic_api_key` + bridge startup.
**Trigger:** A message arrives after process start but before the LaunchAgent env or `.env`
symlink is readable, causing an empty resolution.
**Data prerequisite:** A truthy key present in env or one of the three `.env` paths.
**State prerequisite:** The resolver must not persist an empty resolution.
**Mitigation:** The fix's no-cache-on-empty behavior means the empty resolution is transient
per-call, not sticky per-process; the next call after the env settles resolves the real key.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. The fix is contained to
  `utils/api_keys.py`, `tools/classifier.py`, and the three test files above.

## Update System

No update system changes required — the fix is purely internal Python logic in existing
modules. No new dependencies, config files, or Popoto model changes; `scripts/update/run.py`
and `migrations.py` are untouched.

## Agent Integration

No agent integration required — this is a bridge-internal change. `classify_request_async`
and `classify_conversation_terminus` are already invoked by `bridge/telegram_bridge.py`; no
new CLI entry point (`pyproject.toml [project.scripts]`), MCP surface (`mcp_servers/` /
`.mcp.json`), or bridge import is added. The existing integration path is preserved with a
safer failure mode.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/telegram-messaging.md` (or the routing/classification feature doc
  it links) with a short note: missing-key classification degrades to a message-preserving
  default (terminus→RESPOND, work-type→default, intent→new_work) and the key resolver
  self-heals rather than caching an empty result. If no existing doc covers classification
  key resolution, add a subsection to `docs/features/bridge-worker-architecture.md`.
- [ ] No new `docs/features/README.md` index entry needed (no new feature; hardening an
  existing path).

### Inline Documentation
- [ ] Comment in `utils/api_keys.py` explaining why empty resolutions are not cached
  (self-healing after startup env/.env race, ref #1899).
- [ ] Comment in `tools/classifier.py` distinguishing the missing-key WARNING path from the
  ERROR path for real failures (ref #1899).

## Success Criteria

- [ ] Root cause documented with exact call site: `tools/classifier.py:171/219`
  (`classify_request_async`), amplified by empty-key caching in
  `utils/api_keys.py::get_anthropic_api_key`; issue's terminus attribution corrected.
- [ ] `get_anthropic_api_key()` no longer caches empty resolutions (self-heals).
- [ ] Missing-key classification logs at WARNING, not ERROR (no new Sentry events for this
  condition), and returns a message-preserving default without raising.
- [ ] `classify_conversation_terminus` returns `RESPOND` under an empty-string (`""`) key —
  regression test added.
- [ ] Regression tests for the key-missing path across resolver, work-type, and terminus.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (classification-hardening)**
  - Name: classify-builder
  - Role: Implement the resolver no-cache-on-empty fix, the classifier missing-key WARNING
    path, and the three regression tests.
  - Agent Type: builder
  - Domain: async (see DOMAIN_FRAMING.md — async/await + shared client semantics)
  - Resume: true

- **Validator (classification-hardening)**
  - Name: classify-validator
  - Role: Verify all success criteria and that no ERROR-level log fires on the missing-key
    path.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix key resolver self-healing
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_api_keys.py (create)
- **Assigned To**: classify-builder
- **Agent Type**: builder
- **Parallel**: true
- In `utils/api_keys.py::get_anthropic_api_key`, short-circuit the cache only on a truthy
  cached value; return `""` on the empty tail WITHOUT assigning `_cached_anthropic_key = ""`.
- Add an inline comment referencing #1899.

### 2. Harden classifier missing-key path
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: tests/unit/test_work_request_classifier.py
- **Assigned To**: classify-builder
- **Agent Type**: builder
- **Parallel**: true
- In `classify_request_async` and `classify_request`, replace the raise-on-missing-key with a
  WARNING log + safe default dict; keep the ERROR-level `except Exception` for real failures.
- Confirm `bridge/telegram_bridge.py:1646` handles the default dict (reads `.get("type")`).

### 3. Regression tests
- **Task ID**: build-tests
- **Depends On**: build-resolver, build-classifier
- **Validates**: tests/unit/test_api_keys.py, tests/unit/test_work_request_classifier.py, tests/unit/test_routing.py
- **Assigned To**: classify-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_api_keys.py`: empty resolution not cached (self-heals), truthy
  cached once. Reset `_cached_anthropic_key` between cases.
- Update `test_work_request_classifier.py`: missing key → default dict + WARNING (assert via
  caplog), no raise; real API error still ERROR.
- Update `test_routing.py`: parametrize the terminus fallback test to include empty-string
  `""` key → `RESPOND`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: classify-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the classification/routing feature doc per the Documentation section.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: classify-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm success criteria; confirm no ERROR log on
  missing-key path.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_api_keys.py tests/unit/test_work_request_classifier.py tests/unit/test_routing.py -q` | exit code 0 |
| Lint clean | `python -m ruff check utils/api_keys.py tools/classifier.py` | exit code 0 |
| Format clean | `python -m ruff format --check utils/api_keys.py tools/classifier.py` | exit code 0 |
| Empty result not cached | `grep -n '_cached_anthropic_key = ""' utils/api_keys.py` | match count == 0 |
| Missing-key no longer raises in async classifier | `grep -c 'No Anthropic API key found for classification' tools/classifier.py` | output contains 0 |
| Terminus empty-key test present | `grep -rn 'RESPOND' tests/unit/test_routing.py \| grep -c terminus` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Safe-default `type` for `classify_request_async` on missing key: `"chore"` (most
   conservative — avoids spuriously spawning SDLC work) vs. a `None`/`"question"` sentinel the
   downstream already treats as default. Confirm the preferred default, or leave to the
   builder to pick the least-surprising value given `bridge` routing defaults.
2. Accept the corrected root-cause framing (Sentry noise + cache-poisoning, not a
   message-dropping bug)? The AC "never silent-drop" is satisfied because all three inbound
   paths already preserve the message; the plan adds proof rather than changing drop behavior.
