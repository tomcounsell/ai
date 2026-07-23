---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2211
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T02:57:03Z
---

# Terminal-Flush Attachment Conversion (validator-aware self-draft flush)

## Problem

A teammate session's **final** message referenced a machine-local path
(`/tmp/eng_review_jul15-22.txt`) instead of attaching the file. The delivery
validator correctly flagged `local_file_path_reference` and injected self-draft
steering telling the agent to re-send via `tools/send_message.py --file`. But the
flagged output was the session's **last** message — the steering landed ~1 s
before the session completed, so the agent never consumed it, and
`_reenqueue_leftover_steering` deliberately drops drafter-fallback steering from
re-enqueue (#1794/#2197). The terminal fallback
(`flush_deferred_self_draft_sync`, `agent/session_health.py`) then delivered the
**exact text the validator had just rejected** — dead `/tmp` path included —
because the sync flush hard-omits attachments.

**Current behavior:**
- On a terminal turn, a wire-format violation can never be self-drafted (the
  turn is already over; the steering is suppressed from re-enqueue). This is the
  *common* case, since delivery validation runs on final outputs.
- The flush delivers the rejected text verbatim with no attachment, so the
  corrective (`send_message.py --file`) never executes. The recipient receives a
  dead local path pointing at a file on a machine they can't reach.

**Desired outcome:**
- When the terminal flush is about to deliver text carrying a
  `local_file_path_reference` violation, it becomes **validator-aware**: it
  extracts the referenced path(s), and for any that exist on disk it attaches
  them to the outbox payload and scrubs the bare path token(s) from the delivered
  text. The recipient gets the real file plus clean prose instead of a dead path.
- Delivery on the terminal path no longer defeats the validator for the
  local-path violation class.

## Freshness Check

**Baseline commit:** 704c28b19eb0934854784d179918f0cad3d187fa
**Issue filed at:** 2026-07-22T10:00:28Z
**Disposition:** Minor drift

**File:line references re-verified (against baseline HEAD):**
- `agent/session_health.py:2089-2243` — `flush_deferred_self_draft_sync` — still present. Telegram branch (2211-2221) builds `build_telegram_outbox_payload(chat_id, message, reply_to, session_id)` and the inline comment at line 2214 confirms "This sync flush never carries attachments, so file_paths is omitted." Confirmed.
- `agent/output_handler.py:668-669` — `_ctx["deferred_self_draft_text"] = text` persists the raw (rejected) text at defer time. Confirmed. Note: only the raw text is persisted; the structured `violations` list is NOT stored in extra_context.
- `agent/output_handler.py:960-1073` — `_inject_self_draft_steering`; local-path addendum appended at 1047-1055, pushed with `sender=DRAFTER_FALLBACK_SENDER`. Confirmed.
- `agent/session_executor.py:802-838` — `_reenqueue_leftover_steering` drops `drafter-fallback` steering from re-enqueue (per #1794/#2197). Confirmed.
- `bridge/message_drafter.py:315-355` — `LOCAL_FILE_PATH_RULE` + `detect_local_file_reference` + `_LOCAL_FILE_PATH_PATTERNS` (`/tmp/\S+`, `/Users/\S+`, `/home/\S+`, `~/\S+`, `open -a ...`). Confirmed — the path-detection primitive already exists.
- `agent/output_handler.py:267-308` — `build_telegram_outbox_payload` already accepts `file_paths` and sets the `file_paths` payload key when truthy (307-308). `build_email_outbox_payload` (168-259) sets an `attachments` key. Confirmed — the wire format already supports attachments.
- `bridge/telegram_relay.py:369,404` — the relay reads `file_paths` from the payload and filters to `os.path.isfile(fp)` before attaching. Confirmed end-to-end.

**Commits on main since issue was filed (touching referenced files):**
- `64bd16e26` "Fix deferred self-draft dedup keys swallowing resumed-session replies (#2211 context)" — the per-run dedup-key hotfix the issue explicitly calls out as already-landed context. It scoped `self_draft_completed_flush_sent` / `self_draft_fallback_sent` per-run (AgentSession record id). It does NOT touch the two design defects in this issue. The code read for this plan is already post-hotfix.

**Cited sibling issues/PRs re-checked:**
- #1794 / #2197 / PR #2198 — merged; established that the terminal flush is the sole owner of the held drafter-fallback content and that re-enqueue must drop it. Still the current design.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Minor drift only — the dedup hotfix landed but changed neither root cause. Both design defects remain reproducible against baseline.

## Prior Art

- **PR #1796** (`fix(delivery): flush deferred self-draft on completed terminal path (#1794)`): introduced `flush_deferred_self_draft_sync` so a cleanly-completed session that deferred for self-draft and never redrafted no longer silently swallows the reply. Succeeded at *delivering something*, but chose to deliver the raw deferred text with no attachment — the seed of Defect 2.
- **PR #1807** (`fix(delivery): flush deferred self-draft on email-completed path (#1797)`): extended the flush to the email-completed path via `build_email_outbox_payload`. Same no-attachment limitation.
- **PR #2198** (`Suppress drafter-fallback re-enqueue on terminal self-draft deferral`): made `_reenqueue_leftover_steering` drop drafter-fallback steering so the flush is the sole delivery owner (fixing a context-blind re-spawn). Cemented that the flush — not a re-drafted turn — owns terminal delivery, which is precisely why the flush must now become validator-aware.
- **PR #2115** (`Consolidate agent-message-delivery send paths (#1370)`): consolidated the outbox payload builders (`build_telegram_outbox_payload` / `build_email_outbox_payload`) — the shared builders this plan reuses.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1796 / #1807 | Added the terminal flush so deferred replies are never silently swallowed | Solved *silence*, not *correctness*: the flush delivers the raw rejected text and hard-omits attachments, so a `local_file_path_reference` violation on the final message is delivered verbatim with a dead path |
| PR #2198 | Made the flush the sole owner of the held content (dropped re-enqueue) | Correctly removed a context-blind re-spawn, but by making the flush authoritative it also made the flush's no-attachment limitation the *only* outcome — there is no longer any path that could self-correct the violation |

**Root cause pattern:** each fix improved *delivery reliability* while treating
the deferred text as opaque. None made the delivery path *validator-aware*. The
flush is now the authoritative terminal delivery seam, so the fix belongs there:
re-apply the local-path corrective at flush time instead of re-delivering the
rejected text.

## Data Flow

1. **Entry point:** agent emits a final message containing `/tmp/....txt`.
   `TelegramRelayOutputHandler.send()` routes it through the drafter/validator.
2. **Validation:** `detect_local_file_reference` flags `local_file_path_reference`;
   `draft.needs_self_draft` is True.
3. **Deferral:** `_inject_self_draft_steering` pushes drafter-fallback steering;
   `send()` persists `extra_context.deferred_self_draft_pending = True` and
   `deferred_self_draft_text = <raw rejected text>` (`agent/output_handler.py:668-669`)
   and returns `DeliveryOutcome.deferred_self_draft`.
4. **Turn ends** before the steering is consumed. `_reenqueue_leftover_steering`
   drops the drafter-fallback message (no re-draft).
5. **Terminal flush:** `finalize_session` → `flush_deferred_self_draft_sync`
   reads the persisted raw text, narration-gates it, builds a telegram payload
   **without file_paths**, and pushes to `telegram:outbox:{session_id}`.
6. **Relay:** `bridge/telegram_relay.py` sends the text (dead path included), no
   attachment. **← the defect surfaces here.**

**Fix inserts a conversion step between 5 and 6:** before building the payload,
re-detect local paths in the flush `message`, and for existing files populate
`file_paths` and scrub the path tokens from the text.

## Architectural Impact

- **New dependencies:** none. Reuses existing primitives (`detect_local_file_reference` patterns, the outbox payload builders' `file_paths`/`attachments` params, the relay's `os.path.isfile` filter).
- **Interface changes:** one new pure helper in `bridge/message_drafter.py` (e.g. `convert_local_paths_to_attachments(text) -> tuple[str, list[str]]`). No public signature changes to the flush.
- **Coupling:** slightly increases `session_health` → `message_drafter` coupling (already imports `bridge.message_quality`). Acceptable; the detection logic stays owned by `message_drafter`.
- **Data ownership:** unchanged. The flush remains the sole terminal-delivery owner.
- **Reversibility:** high — the conversion is additive and gated on a violation being present + file existing; if it no-ops, behavior is identical to today.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 required (the three prior open questions are resolved with
  documented defaults under **Resolved Decisions** — build proceeds unblocked)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All touch-points are
internal Python modules.

## Solution

### Key Elements

- **`convert_local_paths_to_attachments(text)` helper** (`bridge/message_drafter.py`):
  a pure function that finds machine-local *file* paths in the text using the
  existing `_LOCAL_FILE_PATH_PATTERNS`, filters to paths that exist on disk
  (`os.path.isfile`), scrubs the matched path tokens from the text, and returns
  `(scrubbed_text, existing_paths)`. Returns the text unchanged with an empty
  list when nothing converts. Does NOT convert the `open -a ...` command pattern
  (that is not a file to attach).
- **Validator-aware flush** (`agent/session_health.py::flush_deferred_self_draft_sync`):
  call the helper on `message` before building the payload; pass the resulting
  `file_paths` into `build_telegram_outbox_payload(...)` (telegram branch) and
  into `build_email_outbox_payload(...)` (email branch) so attachments ride the
  existing wire format.
- **Empty-text guard (BLOCKER fix — dead-path-only case):** after calling
  `convert_local_paths_to_attachments`, the scrubbed text can be empty in *two*
  ways, and both must be guarded **before** building either outbox payload:
  1. **Scrubbed empty but a file WAS attached** → caption with the basename(s):
     `", ".join(os.path.basename(p) for p in attached)`.
  2. **Scrubbed empty and NOTHING attached** (the dead-path-only case: the text
     was *only* a non-existent local path, so scrubbing empties it and no file
     attaches) → substitute a canned notice, e.g.
     `"(the referenced file is no longer available)"`.
  Without guard (2) the flush would build a payload with neither `text` nor
  `file_paths`, and `bridge/telegram_relay.py` drops it at
  `if not text and not file_paths: return None` (line 394) — silently
  re-introducing the exact swallowed-reply defect PR #1796 fixed. The guard is a
  single check: `if not scrubbed.strip() and not attached: scrubbed = <canned notice>`,
  applied on both the telegram and email branches before payload construction.

### Flow

Final message with `/tmp/report.txt` → validator flags `local_file_path_reference`
→ deferred (steering suppressed on terminal turn) → **terminal flush re-detects
`/tmp/report.txt`, confirms it exists, attaches it, scrubs the token** → relay
delivers the file with clean caption text → recipient gets the real attachment.

### Technical Approach

- **Reuse, don't reinvent detection.** The helper reuses
  `_LOCAL_FILE_PATH_PATTERNS`; extract the *full* matched path substring (not the
  80-char-truncated `Violation.snippet`). Only the four filesystem-path patterns
  are convertible; the `open -a` command pattern is scrubbed-or-left per the same
  rule the addendum already implies (not a file — leave detection to the addendum
  path, do not attempt to attach).
- **Match ALL occurrences, not just the first.** Use `pattern.finditer(text)` (not
  `pattern.search`) so multiple paths on one line — e.g. two `/tmp/...` tokens in a
  single sentence — are each detected, existence-checked, and scrubbed. Using
  `search` would catch only the first and leave a second dead path in the text.
- **Existence is the gate — expand `~` first.** Only attach paths where
  `os.path.isfile(os.path.expanduser(path))` is True at flush time. The `~/\S+`
  pattern yields a tilde-prefixed token; `os.path.isfile("~/foo")` is always False
  because `os.path.isfile` does NOT expand `~`, so tilde paths would never attach
  without the explicit `os.path.expanduser`. Attach the expanded absolute path (so
  the relay's own `os.path.isfile` re-check at send time also passes). `/tmp` files
  may have been reaped; a non-existent path is scrubbed from the text but NOT
  attached (the relay would drop a missing file at line 404 anyway — do the
  filtering flush-side so the caption is honest).
- **Scrub, then guard.** Remove the matched path token(s) from the text; collapse
  any doubled whitespace left behind. If the result is empty/whitespace-only,
  caption with `", ".join(os.path.basename(p) for p in attached)`.
- **Both transports.** Apply identically on the telegram and email-completed
  branches (email builder uses `attachments`; telegram uses `file_paths`).
- **Never raises.** The flush is wrapped in a never-raise try/except already; the
  helper must also be internally defensive (a regex/`os.path` failure returns the
  original text + empty list) so a conversion error can never suppress delivery.
- **Dedup unchanged.** The per-run `self_draft_completed_flush_sent:{sid}:{run_id}`
  SETNX gate is untouched — conversion happens after the gate is acquired.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `flush_deferred_self_draft_sync` already wraps its body in
  `except Exception` → `logger.warning` (session_health.py:2238-2243). Add a test
  asserting that when the conversion helper raises, the flush still delivers the
  (unconverted) text rather than swallowing the message — i.e. conversion failure
  degrades to today's behavior, and a warning is logged.
- [ ] The new helper's internal `except` returns `(original_text, [])`; test that
  a malformed input still yields a usable result.

### Empty/Invalid Input Handling
- [ ] Helper on `""`/`None`/whitespace → returns input unchanged, empty list.
- [ ] Text that is ONLY an EXISTING local path → after scrub, empty; flush
  substitutes the basename caption and still attaches (assert payload has
  non-empty text AND file_paths).
- [ ] **Dead-path-only case (BLOCKER guard):** text that is ONLY a NON-existent
  local path → after scrub, empty, and nothing attaches; flush substitutes the
  canned "no longer available" notice so the payload has non-empty text and is NOT
  dropped by the relay's `if not text and not file_paths` guard. Assert the built
  payload has non-empty `text` (the notice) and no `file_paths`.
- [ ] Path matched but file does not exist on disk (alongside other content) → not
  attached; token scrubbed; assert `file_paths` absent/empty and text no longer
  contains the dead path.
- [ ] Tilde path (`~/existing.txt`) that exists after `expanduser` → attached as
  the expanded absolute path; token scrubbed.
- [ ] Two paths on one line (both existing) → both attached and both scrubbed
  (guards the `finditer` vs `search` choice).

### Error State Rendering
- [ ] Assert the recipient-visible outcome: for an existing file, the outbox
  payload carries `file_paths=[<path>]` and text without the raw path token.
- [ ] Assert no double-send: the existing dedup gate still fires exactly once.

### End-to-End Send-Path Validation
- [ ] Drive an existing-file payload through the relay's send path
  (`bridge/telegram_relay.py`) — not just the payload-builder unit assertion —
  confirming the file survives the relay's `os.path.isfile` filter (line 404) and
  reaches the file-send branch. This closes the gap between "payload has
  `file_paths`" and "the relay actually attaches it," catching path-shape
  mismatches (e.g. an unexpanded `~` token) that a builder-only test would miss.
- [ ] Drive the dead-path-only payload through the relay and assert it is NOT
  dropped by the line-394 guard (the canned notice keeps `text` non-empty).

## Test Impact

- [ ] `tests/unit/test_deferred_self_draft_completed.py` — UPDATE: existing tests
  assert the flush writes text-only payloads. Add cases for the conversion path;
  update any assertion that hard-codes "no file_paths key" for the local-path
  scenario. Non-local-path deferrals must still produce text-only payloads
  (regression guard).
- [ ] `tests/unit/test_output_handler.py` — UPDATE (if any test asserts the flush
  payload shape). The `_inject_self_draft_steering` addendum tests
  (`test_local_file_path_violation_adds_attach_addendum_to_steering`) are
  unaffected — the steering-injection path is unchanged.
- [ ] `tests/unit/test_message_drafter.py` / `tests/unit/test_medium_validators.py`
  — ADD: unit tests for the new `convert_local_paths_to_attachments` helper
  (co-located with the detection tests). No existing cases change; `detect_local_file_reference` is untouched.
- [ ] `tests/unit/test_telegram_relay.py` (or the nearest relay send-path test
  module) — ADD: an end-to-end case driving a converted payload through the relay
  so the file survives the `os.path.isfile` filter, plus a dead-path-only case
  asserting the canned-notice payload is not dropped by the line-394 guard. If no
  relay send-path test module exists, add these cases to the flush test module
  invoking the relay send function directly.

## Rabbit Holes

- **Do NOT build a synchronous pre-finalization re-draft turn (Direction B).**
  Running the full self-draft cycle synchronously before finalization (the
  flush's `completed` path has no running event loop) is a large, invasive change
  to the finalization chokepoint for marginal additional coverage. The concrete,
  common incident is the local-path class; the validator-aware flush neutralizes
  it directly.
- **Do NOT generalize to every wire-format violation class.** Markdown-table and
  other cosmetic violations delivered by the flush are lower-harm (readable, just
  imperfect) than a dead path (actively broken/misleading). Converting arbitrary
  violations flush-side has no bounded corrective. Scope to local-path→attachment.
- **Do NOT try to parse "intent"** (did the agent *mean* to attach?). Existence on
  disk + a detected path is a sufficient, mechanical signal. No LLM call.
- **Do NOT touch the dedup-key logic** (just hotfixed in `64bd16e26`).

## Risks

### Risk 1: Attaching a file the agent referenced only for context, not sharing
**Impact:** A `/tmp` path mentioned illustratively gets attached as a file the
recipient didn't ask for.
**Mitigation:** This is strictly better than delivering a dead path, and the
validator already treats any local path in an outbound message as a wire-format
violation (the agent should not be pasting local paths at all). The scrub-plus-
attach keeps the prose intact minus the token. Acceptable per the validator's
existing contract.

### Risk 2: Path with trailing punctuation captured by `\S+`
**Impact:** `/tmp/report.txt.` (sentence-final period) or `(/tmp/x)` yields a path
string that fails `os.path.isfile`, so nothing attaches.
**Mitigation:** Trim a small set of trailing punctuation (`.,;:)]}'"`) from the
matched token before the existence check; test both `report.txt.` and
`(report.txt)` forms.

### Risk 3: Multiple paths, some existing some not
**Impact:** Partial conversion.
**Mitigation:** Attach the subset that exists; scrub only the tokens actually
converted OR all detected tokens (decision: scrub all detected file-path tokens
so no dead path survives, attach only existing ones). Unit-test the mixed case.

## Race Conditions

### Race 1: File reaped between reference and flush
**Location:** `agent/session_health.py` flush, existence check.
**Trigger:** `/tmp` file deleted after the agent referenced it but before the
terminal flush runs.
**Data prerequisite:** the file must exist at flush time to be attached.
**State prerequisite:** none beyond file existence.
**Mitigation:** `os.path.isfile` is checked at flush time (the last possible
moment before the payload is written); the relay re-checks at send time
(`telegram_relay.py:404`). A file that vanishes between flush and send is dropped
by the relay's own filter — no crash, and the text was already scrubbed. This is
the honest, best-effort outcome; documented, not prevented.

No other race conditions — the flush is fully synchronous and the dedup SETNX
already serializes concurrent finalizers.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2211] Synchronous pre-finalization self-draft re-draft turn
  (Direction B) — the general structural-race fix for *all* violation classes is
  a distinct, larger design; this plan deliberately fixes the local-path class at
  the flush seam. (Tracked by this same issue's remaining scope; if pursued it
  warrants its own issue.)
- Nothing else deferred — every relevant item for the local-path class is in
  scope for this plan.

<!-- Note: the [SEPARATE-SLUG] entry above references this issue itself as the
     home for the deferred Direction-B design; no separate anti-criterion row is
     required because the No-Go describes an approach not taken, not a forbidden
     code artifact in this PR. -->

## Update System

No update system changes required — this feature is purely internal (a
delivery-path bug fix in `agent/` + `bridge/`). No new deps, config, or
propagation steps.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change on the
terminal delivery path. No new tool or MCP surface; the agent's existing
`tools/send_message.py --file` affordance is unchanged. The fix makes the
*fallback* flush behave correctly when the agent did NOT use that affordance in
time.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` or the relevant delivery-path
  doc if one exists; otherwise add a short note to
  `docs/features/bridge-worker-architecture.md` describing the validator-aware
  terminal flush (local-path→attachment conversion).
- [ ] If a dedicated self-draft/deferred-delivery doc exists, update it to state
  that the terminal flush now converts local-path references to attachments.

### Inline Documentation
- [ ] Docstring on `convert_local_paths_to_attachments` describing the existence
  gate (with `os.path.expanduser`), `finditer` all-occurrences scrub behavior, and
  never-raise contract.
- [ ] Correct the stale **inline comment** at `agent/session_health.py:2214`
  ("This sync flush never carries attachments, so file_paths is omitted") — it is
  an inline comment, not the function docstring — to describe the new
  local-path→attachment conversion behavior.

## Success Criteria

- [ ] A terminal-turn deferral whose text references an existing local file
  delivers that file as a real attachment with the path token scrubbed from the
  text.
- [ ] A terminal-turn deferral referencing a non-existent local path scrubs the
  dead path from the delivered text and attaches nothing (no dead path reaches
  the recipient).
- [ ] A terminal-turn deferral whose text is ONLY a non-existent local path
  delivers the canned "no longer available" notice (never an empty payload that
  the relay's line-394 guard would silently drop).
- [ ] Non-local-path deferrals still deliver text-only payloads (no regression).
- [ ] The per-run dedup gate still fires exactly once (no double-send).
- [ ] Conversion failure degrades to today's behavior with a logged warning
  (never suppresses delivery).
- [ ] Email-completed branch has attachment parity with telegram.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `flush_deferred_self_draft_sync` references
  `convert_local_paths_to_attachments` (or the shared helper name chosen).

## Team Orchestration

### Team Members

- **Builder (flush-conversion)**
  - Name: flush-builder
  - Role: Implement the `convert_local_paths_to_attachments` helper and wire it
    into both branches of `flush_deferred_self_draft_sync`.
  - Agent Type: builder
  - Domain: async/delivery, untrusted-input (paths)
  - Resume: true

- **Validator (flush-conversion)**
  - Name: flush-validator
  - Role: Verify success criteria, run the targeted unit tests, confirm no
    double-send and no regression on non-local-path deferrals.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: flush-docs
  - Role: Update the delivery-path feature doc and the two docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement conversion helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: tests/unit/test_message_drafter.py (add cases)
- **Assigned To**: flush-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `convert_local_paths_to_attachments(text) -> tuple[str, list[str]]` to
  `bridge/message_drafter.py`, reusing the four filesystem `_LOCAL_FILE_PATH_PATTERNS`
  (exclude the `open -a` command pattern).
- Use `pattern.finditer(text)` (not `search`) so ALL occurrences on a line are
  detected, not just the first.
- Extract full path tokens, trim trailing punctuation, then existence-check with
  `os.path.isfile(os.path.expanduser(token))` (expand `~` — `os.path.isfile` does
  not). Attach the expanded absolute path.
- Scrub all detected file-path tokens from the text; collapse doubled whitespace.
- Internally defensive: any exception returns `(original_text, [])`.
- Unit tests: empty/None, single existing path, non-existent path, multiple mixed,
  two-on-one-line, tilde path, trailing-punctuation, path-only text.

### 2. Wire helper into the terminal flush
- **Task ID**: build-flush
- **Depends On**: build-helper
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: flush-builder
- **Agent Type**: builder
- **Parallel**: false
- In `flush_deferred_self_draft_sync`, after computing `message` and acquiring the
  dedup gate, call the helper.
- Telegram branch: pass `file_paths` into `build_telegram_outbox_payload`.
- Email branch: pass `file_paths` into `build_email_outbox_payload`.
- Empty-text-after-scrub guard: substitute basename caption when files attached;
  substitute the canned "no longer available" notice on the dead-path-only case
  (BLOCKER fix — prevents the relay from dropping a text+file-less payload).
- Correct the stale inline comment at `agent/session_health.py:2214` ("This sync
  flush never carries attachments, so file_paths is omitted") to describe the new
  conversion behavior — it is an inline comment, not the function docstring.

### 3. Validate
- **Task ID**: validate-flush
- **Depends On**: build-flush
- **Assigned To**: flush-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the two targeted unit test modules; verify all Success Criteria.
- Confirm dedup gate unchanged and non-local-path deferrals still text-only.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-flush
- **Assigned To**: flush-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update the delivery-path feature doc and both docstrings.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: flush-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification checks; confirm docs updated; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Deferred-flush tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py -q` | exit code 0 |
| Drafter/helper tests pass | `pytest tests/unit/test_message_drafter.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flush references helper | `grep -c "convert_local_paths_to_attachments" agent/session_health.py` | output > 0 |
| Helper exists | `grep -c "def convert_local_paths_to_attachments" bridge/message_drafter.py` | output > 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Empty-text guard misses dead-path-only case → payload with neither text nor file_paths → relay drops it (line 394), re-introducing the #1796 defect | Key Elements empty-text guard (case 2); build-flush task; dead-path-only test | Add `if not scrubbed.strip() and not attached: scrubbed = "(the referenced file is no longer available)"` before building either payload |
| Concern | critique | Tilde paths never attach (`os.path.isfile` doesn't expand `~`) | Technical Approach "expand `~` first"; build-helper task; tilde unit test | Existence check via `os.path.isfile(os.path.expanduser(token))`; attach expanded absolute path |
| Concern | critique | Duplicate paths on one line — `search` catches only first | Technical Approach "match ALL occurrences"; build-helper task; two-on-one-line test | Use `pattern.finditer(text)` not `pattern.search` |
| Concern | critique | Open Questions 1-2 locked into build spec while pending PM confirmation | Resolved Decisions section (all 3 resolved with documented defaults) | Build proceeds on defaults; no PM gate |
| Concern | critique | No end-to-end send-path validation through the relay's `os.path.isfile` filter | End-to-End Send-Path Validation test subsection; Test Impact relay test module | Drive converted + dead-path-only payloads through `bridge/telegram_relay.py` send path |
| Nit | critique | Task 2/Documentation referenced flush "docstring" but "never carries attachments" is an inline comment (line 2214) | build-flush task + Inline Documentation corrected | Corrected to "inline comment at `agent/session_health.py:2214`" |

---

## Resolved Decisions

All three formerly-open questions are resolved with documented defaults so the
build is unambiguous and does not block on human input. If the PM later prefers a
different call on any of these, it is a one-line change to the helper/guard — but
the build proceeds on these defaults.

1. **Text-scrub behavior — DECIDED: scrub the path token from the delivered
   text** (cleaner UX). The flush attaches the file AND removes the bare path
   token from the prose; if the scrub empties the text, a basename caption
   (existing file) or the canned notice (dead-path-only) fills it. Chosen over
   leaving the raw path in the text, which would defeat the point of the
   validator flag.
2. **Non-existent path at flush time — DECIDED: fall back to the canned notice.**
   When the referenced file no longer exists at flush time, the dead path is
   scrubbed from the text and nothing attaches; if that empties the text, the
   canned `"(the referenced file is no longer available)"` notice is substituted
   (the BLOCKER-fix guard). This guarantees the payload is never text-and-file
   empty, so the relay's line-394 guard never silently drops the reply.
3. **Scope — DECIDED: local-path violation class only (Direction A).** The
   general structural race for other violation classes (Direction B, synchronous
   pre-finalization re-draft) stays out of scope and is captured under No-Gos.
