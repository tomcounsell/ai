---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1899
last_comment_id:
revision_applied: true
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
  non-fatal sentinel default (`type=None`), intent → `new_work`. Regression tests lock this in
  against the real production condition (the resolver now returns `None` on absence).
- A permanently keyless process still surfaces a Sentry signal, but only after a *sustained*
  window: the work-type classifier escalates from WARNING to a single ERROR when the miss streak
  has both crossed K=5 consecutive misses AND persisted longer than `MIN_STREAK_SECONDS` (~60s).
  The time gate keeps a restart burst — Telethon `catch_up=True` can flood many queued messages
  through the sub-second env/`.env` settle window — from tripping a false ERROR during a
  self-healing transient; a fire-once latch ensures at most one ERROR per sticky-misconfig
  episode. A genuine, persistent misconfig still pages an operator once the streak outlives the
  time gate.

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

- **`get_anthropic_api_key()` self-heals AND returns `None` on absence:** only cache a truthy
  resolution; on absence return `None` (not `""`), annotated `-> str | None`. The self-heal
  comes entirely from the resolver no longer caching the absent result: the next call re-reads
  env/`.env` (no cache short-circuit) after the LaunchAgent env and `.env` sourcing settle, so
  it resolves the real key. Returning `None` rather than `""` is on cache-poisoning grounds — it
  keeps the "absent" sentinel distinct from a cached value so the truthy-only cache guard is
  unambiguous. (Note: `AsyncAnthropic(api_key=None)` with an empty env does not raise at
  construction, identical to `api_key=""`; the None change is a correctness/clarity choice, not
  an SDK-fallback trigger.)
- **Missing-key degrades quietly in the async work-type classifier only
  (`classify_request_async`):** distinguish "no API key" from real API/parse errors. Missing
  key → log at WARNING and return a message-preserving sentinel default, instead of raising a
  `ValueError` that surfaces as an ERROR-level Sentry event. Genuine API errors and JSON-decode
  failures keep ERROR-level logging. **The sync twin `classify_request` is out of scope** — it
  has no live keyless production caller (only in-module docstring examples and tests; the sole
  production caller is `classify_request_async` at `bridge/telegram_bridge.py:1646`). Editing it
  would be gold-plating on a code path the Sentry event never traverses.
- **Time-aware consecutive-miss escalation (transient vs. permanent):** three module-level
  variables in `tools/classifier.py` distinguish a transient startup race (including a
  restart-driven message burst) from a permanently keyless process:
  `_consecutive_missing_key_count`, `_first_missing_key_ts` (monotonic timestamp recorded on the
  0→1 transition), and `_missing_key_error_emitted` (a fire-once latch). Each missing-key
  classification logs WARNING and increments the counter. The single ERROR fires only when
  **`_consecutive_missing_key_count >= _MISSING_KEY_ERROR_THRESHOLD` (K=5) AND
  `(monotonic() - _first_missing_key_ts) > _MIN_STREAK_SECONDS` (~60s) AND not
  `_missing_key_error_emitted`** — then it sets the latch so no further ERROR fires for the same
  episode (avoids one ERROR per message past the 5th). The time gate is what defeats the restart
  burst: Telethon `catch_up=True` can flood ≥K queued messages through the sub-second env/`.env`
  settle window, tripping the raw count instantly, but that burst is younger than
  `_MIN_STREAK_SECONDS`, so it stays at WARNING and self-heals. All three variables reset
  (count→0, ts→None, latch→False) on the first successful classification. This keeps startup-race
  and restart-burst noise quiet while still paging an operator once a genuinely keyless streak
  outlives the time gate.
- **Explicit RESPOND / non-drop regression coverage:** lock in that all three inbound
  classification entry points preserve the message when the key is absent (`None`, the real
  production value after this change).

### Flow

Inbound message → classification call resolves key → **populated:** normal classification,
miss-counter reset to 0 → **absent (transient):** WARNING + message-preserving sentinel default
(work-type→`type=None`, terminus→RESPOND, intent→new_work), resolver returns `None` and does
not cache it → next message re-resolves the real key → normal classification resumes → **absent
(persistent, ≥K consecutive AND streak older than `_MIN_STREAK_SECONDS`):** one ERROR (Sentry,
fire-once) flags the permanent misconfig while still returning the safe default. A restart burst
that floods ≥K queued misses within the settle window stays at WARNING because the streak is
younger than the time gate.

### Technical Approach

- `utils/api_keys.py`: change the signature to `-> str | None`; short-circuit the cache only on
  a truthy cached value; on absence `return None` WITHOUT assigning `_cached_anthropic_key`.
  Behavior for a populated key is unchanged (still cached once). This is the highest-leverage
  change: it removes the persistence amplifier so a transient absence self-heals on the next
  no-cache `.env` re-read. **Call-site audit for the `"" → None` change** (all resolve safely —
  verified during planning):
  - Truthiness guards — `if api_key:` / `if not api_key:` behave identically for `None` and
    `""`: `bridge/routing.py:946,989`, `bridge/promise_gate.py:504`, `bridge/session_router.py:118`,
    `bridge/agent_catchup.py:201`, `agent/intent_classifier.py:202`,
    `agent/memory_extraction.py:455,717,881`, `tools/valor_calendar.py:600`,
    `tools/classifier.py:74,169,312,401`.
  - `AsyncAnthropic(api_key=...)` / `Anthropic(api_key=...)` constructors —
    `agent/anthropic_client.py:76`, `bridge/read_the_room.py:430`, `agent/session_completion.py:496`,
    `agent/memory_extraction.py:289`: passing `None` is *equivalent* to `""` at construction —
    `AsyncAnthropic(api_key=None)` with an empty env does not raise, same as `api_key=""`. No
    string operations run on the return value at any site, so no `AttributeError` risk either
    way; `None` is preferred purely for sentinel clarity.
  - `agent/health_check.py:348` returns the resolver value directly; a `None` health probe is a
    truthful "no key" signal (was falsy `""` before). Confirm the health-check assertion treats
    both as absent during build.
- `tools/classifier.py`, `classify_request_async` **only**: replace
  `if not api_key: raise ValueError(...)` with a missing-key branch that (a) increments
  `_consecutive_missing_key_count`, recording `_first_missing_key_ts = time.monotonic()` on the
  `0→1` transition (i.e. when the count was 0 before this miss); (b) logs WARNING for the miss;
  (c) escalates to a single ERROR **only when all three hold**:
  `_consecutive_missing_key_count >= _MISSING_KEY_ERROR_THRESHOLD` (K=5),
  `(time.monotonic() - _first_missing_key_ts) > _MIN_STREAK_SECONDS` (~60s), and
  `not _missing_key_error_emitted` — then set `_missing_key_error_emitted = True` so the ERROR
  fires at most once per episode; and (d) returns the sentinel default
  `{"type": None, "confidence": 0.0, "reason": "no anthropic api key — classification skipped"}`.
  On the successful-return path (after response validation) reset all three:
  `_consecutive_missing_key_count = 0`, `_first_missing_key_ts = None`,
  `_missing_key_error_emitted = False`. Use `time.monotonic()` (not wall-clock) so the streak
  age is immune to clock adjustments. The outer `except Exception` ERROR log stays for real
  failures. The bridge caller
  (`bridge/telegram_bridge.py:1646`) reads `result.get("type")`; `type=None` is already the
  handled sentinel there — the comment at `telegram_bridge.py:1665` documents
  `classification_type=None → default "question"`, the most conservative routing (it does not
  spuriously spawn SDLC work). This is why the sentinel `None` is preferred over a concrete
  `"chore"`.
- **No durable caching of the default:** `classify_request_async` uses no JSON sidecar cache
  (the #1182 `JsonCache` lives in `agent/intent_classifier.py`, a separate out-of-scope module
  whose own no-key default returns *before* its cache write at `intent_classifier.py:205`, so it
  already never persists a default). The `confidence: 0.0` sentinel therefore cannot be durably
  cached on any in-scope path. Invariant to preserve: **a `confidence == 0.0` result must never
  be written to a durable cache.** If a sidecar cache is ever added to `classify_request_async`,
  guard the write with `if result["confidence"] > 0.0`.
- No change to `classify_conversation_terminus` logic (already correct); only add a regression
  test for the missing-key case.
- `agent/anthropic_client.py::anthropic_slot()` constructs `AsyncAnthropic(api_key=...)` with
  whatever the resolver returns — the cache fix means it stops receiving a stale cached value
  (empty before, now a fresh re-read once the env settles), so its call sites benefit
  automatically from the self-heal. No code change there.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/classifier.py` `classify_request_async` (`except Exception` → ERROR) — after the
  fix, add a test asserting a **single transient** missing-key call does NOT reach the ERROR log
  (asserts WARNING instead), and a separate test asserting a **real** API error still logs at
  ERROR.
- [ ] Time-aware consecutive-miss escalation — with `time.monotonic` monkeypatched to control
  the streak age: (a) assert that K=5 consecutive misses whose streak age is **below**
  `_MIN_STREAK_SECONDS` (the restart-burst case) stay at WARNING and emit **no** ERROR;
  (b) assert that once the streak age exceeds `_MIN_STREAK_SECONDS` the next miss emits exactly
  **one** ERROR and the `_missing_key_error_emitted` latch suppresses any further ERROR on
  subsequent misses in the same episode; (c) assert a successful call resets all three
  (`_consecutive_missing_key_count`, `_first_missing_key_ts`, `_missing_key_error_emitted`) so
  the WARNING regime resumes and a fresh episode can escalate again.
- [ ] `bridge/telegram_bridge.py:1653-1654` (non-fatal catch) — covered indirectly; assert
  `classify_work_type` leaves the message enqueue path intact when classification returns the
  sentinel default (`type=None`).

### Empty/Invalid Input Handling
- [ ] `get_anthropic_api_key()` with empty env and no readable `.env` returns `None` and does
  NOT cache it (subsequent populated call returns the real key). Reset `_cached_anthropic_key`
  to `None` between cases.
- [ ] `classify_request_async("")` and `classify_request_async(<text>)` with a missing key
  return the sentinel default (`type=None`, `confidence=0.0`) without raising.

### Error State Rendering
- [ ] A single transient missing-key case produces a WARNING log line (observable), not a silent
  swallow and not an ERROR/Sentry event. Assert via `caplog`. A miss streak that has crossed both
  K=5 **and** `_MIN_STREAK_SECONDS` (monotonic monkeypatched) produces exactly one ERROR line,
  and no further ERROR on later misses in the same episode.

## Test Impact

- [ ] `tests/unit/test_routing.py::test_classify_terminus_ollama_failure_defaults_to_respond` —
  UPDATE: keep the existing `None` case (now the exact production value the resolver returns on
  absence) asserting `RESPOND`. The prior plan called for adding an `""` case; with the resolver
  now returning `None`, `None` *is* the production condition, so no `""` case is needed.
- [ ] `tests/unit/test_work_request_classifier.py` — UPDATE: add cases asserting
  `classify_request_async` returns the sentinel default (`type=None`, `confidence=0.0`) on a
  missing key (no raise) and logs at WARNING for a transient miss; add a case (monotonic
  monkeypatched) asserting K consecutive misses **under** `_MIN_STREAK_SECONDS` stay WARNING-only
  (restart-burst guard), a case asserting a streak past both K and the time gate escalates to
  exactly one ERROR with the fire-once latch suppressing repeats, and a case asserting a success
  resets all three module-level variables. Existing
  populated-key tests are unaffected. **No changes to the sync `classify_request` tests** — that
  function is out of scope this revision.
- [ ] `tests/unit/test_api_keys.py` — CREATE: new file covering `get_anthropic_api_key`
  no-cache-on-absence self-healing (`None` returned and not cached; subsequent populated call
  returns the real key) and truthy-caching behavior. Must reset the module-level
  `_cached_anthropic_key` to `None` between cases (monkeypatch).

No other existing tests assert the current raise-on-missing-key behavior of
`classify_request_async`, so nothing needs DELETE/REPLACE. Tests in
`tests/tools/test_classifier.py` and `tests/unit/test_intent_classifier.py` exercise
populated-key or non-work-type paths and are unaffected by this scope.

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

### Risk 1: Sentinel default misroutes work-type classification
**Impact:** If the sentinel default routed wrongly, PR/issue messages could be mis-bucketed when
the key is transiently missing.
**Mitigation:** The sentinel is `type=None`, which the bridge already treats as its most
conservative default ("question", per the `telegram_bridge.py:1665` comment) — it does not
spawn SDLC work. The synchronous PR/issue fast-path at `bridge/telegram_bridge.py:1668-1674`
also forces `type="sdlc"` for issue/PR references independent of the async classifier, so the
sentinel only affects genuinely ambiguous messages during a short self-healing window.

### Risk 2: No-cache-on-absence adds `.env` re-reads on a permanently keyless process
**Impact:** A deployment with no key re-reads `.env` files on every classification call (minor
extra I/O) instead of once, and the WARNING-only degrade would otherwise hide a permanent
misconfig.
**Mitigation:** The extra cost is three `Path.exists()` checks per call; populated deployments
still cache once. The time-aware escalation fires ERROR/Sentry only after the miss streak crosses
both K=5 AND `_MIN_STREAK_SECONDS` (~60s), so a genuinely keyless process is flagged for an
operator to fix rather than degrading silently forever — bounding the re-read window to the time
it takes to notice and repair the misconfig, while the time gate keeps a restart burst from
paging on a transient that self-heals within seconds.

## Race Conditions

### Race 1: Startup env/.env sourcing vs. first classification
**Location:** `utils/api_keys.py::get_anthropic_api_key` + bridge startup.
**Trigger:** A message arrives after process start but before the LaunchAgent env or `.env`
symlink is readable, causing an empty resolution.
**Data prerequisite:** A truthy key present in env or one of the three `.env` paths.
**State prerequisite:** The resolver must not persist an absent resolution.
**Mitigation:** The fix's no-cache-on-absence behavior (return `None`, do not cache) means the
absent resolution is transient per-call, not sticky per-process; the next call after the env
settles resolves the real key.

## No-Gos (Out of Scope)

- **Sync `classify_request` missing-key hardening is out of scope** — no live keyless
  production caller exists (only in-module docstring examples and test suites reference it; the
  sole production caller of the classifier is `classify_request_async`). The Sentry event is
  async-only, so editing the sync twin would be gold-plating.
- **Intent classifiers (`classify_message_intent` / `classify_message_intent_async`, lines
  312/401) are out of scope** — they already default to `new_work` internally and never emit
  the "Classification failed (async)" string.
- Otherwise nothing deferred. The fix is contained to `utils/api_keys.py`,
  `classify_request_async` in `tools/classifier.py`, and the three test files above.

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
- [ ] Comment in `utils/api_keys.py` explaining why absent resolutions return `None` and are not
  cached (self-healing after startup env/.env race comes from the no-cache re-read; `None` is a
  distinct absent-sentinel kept out of the cache, ref #1899).
- [ ] Comment in `tools/classifier.py` distinguishing the missing-key WARNING path (and the
  consecutive-miss ERROR escalation) from the ERROR path for real failures (ref #1899).

## Success Criteria

- [ ] Root cause documented with exact call site: `tools/classifier.py:171/219`
  (`classify_request_async`), amplified by empty-key caching in
  `utils/api_keys.py::get_anthropic_api_key`; issue's terminus attribution corrected.
- [ ] `get_anthropic_api_key()` returns `None` on absence (annotated `-> str | None`) and no
  longer caches absent resolutions (self-heals via the no-cache `.env` re-read).
- [ ] A transient missing-key classification logs at WARNING, not ERROR (no new Sentry events),
  and returns the message-preserving sentinel default (`type=None`, `confidence=0.0`) without
  raising. A restart burst of ≥K misses inside the settle window stays WARNING-only; only a
  streak past both K=5 AND `_MIN_STREAK_SECONDS` (~60s) escalates to a single, fire-once ERROR
  (permanent-misconfig signal); a success resets all three streak variables.
- [ ] `classify_conversation_terminus` returns `RESPOND` under a `None` key — regression test
  present.
- [ ] Regression tests for the key-missing path across resolver, work-type (including the
  time-aware escalation: restart-burst stays WARNING, sustained streak fires one ERROR), and
  terminus.
- [ ] The `confidence==0.0` sentinel is never written to a durable cache (no sidecar cache on
  the in-scope path; invariant documented).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (classification-hardening)**
  - Name: classify-builder
  - Role: Implement the resolver no-cache-on-absence (`None`) fix, the classifier missing-key
    WARNING path with time-aware (count + streak-age + fire-once-latch) ERROR escalation, and the
    three regression tests.
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
- In `utils/api_keys.py::get_anthropic_api_key`, change the signature to `-> str | None`;
  short-circuit the cache only on a truthy cached value; `return None` on the absent tail
  WITHOUT assigning `_cached_anthropic_key`.
- Audit the `"" → None` call sites listed in Technical Approach (all resolve safely; no code
  change needed at truthiness guards or SDK constructors).
- Add an inline comment referencing #1899.

### 2. Harden classifier missing-key path
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: tests/unit/test_work_request_classifier.py
- **Assigned To**: classify-builder
- **Agent Type**: builder
- **Parallel**: true
- In `classify_request_async` **only**, replace the raise-on-missing-key with a WARNING log +
  sentinel default dict (`{"type": None, "confidence": 0.0, "reason": "..."}`); keep the
  ERROR-level `except Exception` for real failures. Do NOT touch the sync `classify_request` or
  the intent classifiers.
- Add the module-level state: `_consecutive_missing_key_count`, `_first_missing_key_ts`
  (monotonic, set on the `0→1` transition), `_missing_key_error_emitted` (fire-once latch), and
  the constants `_MISSING_KEY_ERROR_THRESHOLD` (K=5) and `_MIN_STREAK_SECONDS` (~60s). Increment
  the count on each missing-key call; escalate to a single ERROR only when
  `count >= K AND (time.monotonic() - _first_missing_key_ts) > _MIN_STREAK_SECONDS AND not
  _missing_key_error_emitted`, then set the latch. Reset all three (count→0, ts→None,
  latch→False) on the successful-return path. Use `time.monotonic()`, never wall-clock.
- Confirm `bridge/telegram_bridge.py:1646` handles the sentinel dict (reads `.get("type")`;
  `type=None` → downstream "question" default).

### 3. Regression tests
- **Task ID**: build-tests
- **Depends On**: build-resolver, build-classifier
- **Validates**: tests/unit/test_api_keys.py, tests/unit/test_work_request_classifier.py, tests/unit/test_routing.py
- **Assigned To**: classify-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_api_keys.py`: absent resolution returns `None` and is not cached
  (self-heals), truthy cached once. Reset `_cached_anthropic_key` to `None` between cases.
- Update `test_work_request_classifier.py` (monkeypatch `time.monotonic` to drive streak age):
  missing key → sentinel dict (`type=None`) + WARNING (assert via caplog), no raise; K misses
  **under** `_MIN_STREAK_SECONDS` → WARNING-only, no ERROR (restart-burst guard); streak past
  both K and the time gate → exactly one ERROR with the latch suppressing repeats; success resets
  all three module-level variables; real API error still ERROR.
- Update `test_routing.py`: confirm the terminus fallback test's `None` key case asserts
  `RESPOND` (no `""` case needed — `None` is now the production value).

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
| Empty result not cached | `grep -c '_cached_anthropic_key = ""' utils/api_keys.py` | output `0` |
| Resolver returns None on absence | `grep -c 'return None' utils/api_keys.py` | output `>= 1` |
| Missing-key no longer raises in `classify_request_async` (range-scoped to the one changed function; single awk, no shell pipe, no `\|`, runs byte-for-byte from raw .md) | `awk '/^async def classify_request_async/{cap=1;next} /^async def /{cap=0} /^def /{cap=0} cap && /No Anthropic API key found for classification/{n++} END{print n+0}' tools/classifier.py` | output `0` |
| Sync + intent classifiers untouched (raise still present) | `grep -c 'No Anthropic API key found for classification' tools/classifier.py` | output `3` |
| Consecutive-miss counter present | `grep -c '_MISSING_KEY_ERROR_THRESHOLD' tools/classifier.py` | output `>= 2` |
| Time gate present (escalation is time-aware) | `grep -c '_MIN_STREAK_SECONDS' tools/classifier.py` | output `>= 2` |
| Fire-once latch present | `grep -c '_missing_key_error_emitted' tools/classifier.py` | output `>= 3` |
| Streak timestamp uses monotonic clock | `grep -c '_first_missing_key_ts' tools/classifier.py` | output `>= 3` |
| Terminus missing-key test present | `grep -c terminus tests/unit/test_routing.py` | output > 0 |

## Critique Results

Verdict: **NEEDS REVISION** (round 1). All six findings addressed in this revision pass.

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | Verification grep gate unsatisfiable — string appears 4× (lines 76/171/314/403), gate expected 0 for whole file | Verification table row range-scoped to the `classify_request_async` function body via `awk`; added a companion row asserting the sync + intent classifiers keep their raise (count `3`) | The one changed function loses the raise (0); the three out-of-scope functions keep it (3). Validated the `awk` extractor against current `tools/classifier.py`. |
| CONCERN | WARNING path collapses transient miss with permanent misconfig — no Sentry signal, unbounded `.env` re-read | Added module-level `_consecutive_missing_key_count` + `_MISSING_KEY_ERROR_THRESHOLD` (K=5): WARNING for transient, ERROR/Sentry after K consecutive, reset on success | Solution Key Elements, Technical Approach, Risk 2, tasks 2/3, tests. Bounds the re-read window to time-to-notice for a genuinely keyless process. |
| CONCERN | Resolver should return `None` (not `""`), annotated `-> str \| None`, and stop caching absent resolutions so a transient absence self-heals on the next `.env` re-read; update all call sites | Signature `-> str \| None`; `return None` on absence without caching; audited all 15 non-test call sites (truthiness guards + SDK constructors) — all safe, `None` equivalent-or-clearer at constructors | Technical Approach call-site audit; task 1; test_api_keys assertions. |
| CONCERN | Sync `classify_request` edit may be unreachable gold-plating (Sentry event is async-only) | Grepped for a live keyless sync caller — none (only docstring examples + tests). Dropped the sync edit from scope | No-Gos, Solution, tasks 2/3, Test Impact all now async-only. |
| NIT | Safe-default `type="chore"` both asserted and punted to Open Question 1 (self-contradiction) | Pinned invariant to `confidence==0.0` / no-raise; sentinel `type=None` (downstream already maps `None → "question"`) preferred over `"chore"`; deleted Open Question 1 | Technical Approach, Risk 1, Success Criteria. |
| NIT | #1182 JSON sidecar could durably cache the `confidence=0.0` default | Documented that `classify_request_async` uses no sidecar cache (the `JsonCache` is in the out-of-scope `agent/intent_classifier.py`, which returns its default before its cache write); pinned invariant "never cache `confidence==0.0`" + guard note if caching is ever added | Technical Approach "No durable caching of the default". |

Verdict: **NEEDS REVISION** (round 2). All three findings addressed in this revision pass.

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | K=5 consecutive-miss escalation misfires on restart bursts (Telethon `catch_up=True` floods ≥K queued messages through the sub-second settle window → false permanent-misconfig ERROR during a self-healing transient); operator unpinned so >=K fires one ERROR per message past the 5th | Made escalation **time-aware**: record `_first_missing_key_ts` (monotonic) on the `0→1` transition; gate the ERROR on `count >= K AND (monotonic - first_ts) > _MIN_STREAK_SECONDS` (~60s); add `_missing_key_error_emitted` fire-once latch; reset all three on the success-reset path | Problem desired-outcome, Solution Key Elements, Flow, Technical Approach, Failure Path Test Strategy, Test Impact, Risk 2, tasks 2/3, Verification (3 new grep rows), Success Criteria. |
| CONCERN | awk-scoped Verification gate not runnable byte-for-byte — `\|` inside the awk alternation `/^(async def\|def) /` is treated as a literal, so the reset never matches (verified: broken form outputs `3`, not `0`) | Rewrote the gate as a single awk with two separate anchor rules (`/^async def /{cap=0}` and `/^def /{cap=0}`) and an inline `END{print n+0}` count — no `\|`, no shell pipe, runs byte-for-byte from raw .md (verified: outputs `1` on the unfixed file, will be `0` after the fix). Also removed the `\|` from the terminus-test presence row | Verification table. |
| NIT | "None enables the SDK `os.environ` fallback" overstated — `AsyncAnthropic(api_key=None)` with empty env does not raise, identical to `api_key=""` | Attributed the self-heal entirely to the resolver's no-cache `.env` re-read; dropped all SDK-fallback wording. The `None` change stays on cache-poisoning / sentinel-clarity grounds | Solution Key Elements, Technical Approach call-site audit, `anthropic_client` note, Inline Documentation, Success Criteria, round-1 critique-row wording. |

---

## Open Questions

1. Accept the corrected root-cause framing (Sentry noise + cache-poisoning, not a
   message-dropping bug)? The AC "never silent-drop" is satisfied because all three inbound
   paths already preserve the message; the plan adds proof rather than changing drop behavior.
