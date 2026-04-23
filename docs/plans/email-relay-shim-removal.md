---
status: Ready
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1095
last_comment_id: none
revision_applied: true
---

# Remove email_relay legacy text -> body compat shim

## Problem

`bridge/email_relay.py::_normalize_payload` carries a one-release compat shim
(lines 88-91 on current `main`) that renames an incoming `text` payload key to
`body` on read. The shim was introduced by merged PR #1094 (`valor-email CLI`,
merged 2026-04-21) as a transitional safety net while the unified outbox payload
shape rolled out. PR #1094 documented the removal plan inline via a `FIXME(#1095)`
comment and docstring paragraph pointing at this issue.

Two days have elapsed since PR #1094 merged. Every current producer
(`tools/send_message.py::_send_via_email`, `tools/valor_email.py`) emits
`body` — grep across the tree finds no producer emitting the legacy `text`
key. The queue had no consumer before PR #1094, so in-flight entries with the
legacy shape are effectively zero. The transitional safety margin has been held;
now it is dead code.

**Current behavior:**
- `_normalize_payload` silently accepts `text` payloads by renaming `text` ->
  `body` before the rest of validation runs.
- Two test cases lock this in: `TestNormalizePayload::test_text_aliases_to_body`
  and the end-to-end `TestProcessOutbox::test_drains_legacy_text_payload`.
- Module docstring, `_normalize_payload` docstring, and the test module
  docstring all mention the `text` field as a supported payload shape.

**Desired outcome:**
- `_normalize_payload` only understands `body`. Payloads carrying `text`
  without `body` are rejected (return `None`), which the caller already DLQs
  without retry — this is the same disposition as any malformed payload.
- All shim-specific tests deleted. A single replacement assertion confirms
  legacy `text`-only payloads now DLQ, so the behavior change is captured.
- Docstrings no longer advertise legacy compat.

## Freshness Check

**Baseline commit:** `61a11980` on `main`
**Issue filed at:** 2026-04-21T06:45:38Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/email_relay.py:25-28` (issue's "Legacy compat paragraph") —
  drifted to lines **25-31** on `main`. PR #1094 landed with an additional
  `FIXME(#1095)` paragraph at lines 29-31 that must also be deleted. Both
  paragraphs describe the shim.
- `bridge/email_relay.py:84-86` (issue's "shim branch inside `_normalize_payload`") —
  drifted to lines **88-91** on `main`. The shim branch is still a two-line
  `if "body" not in message and "text" in message: message["body"] = message.pop("text")`
  preceded by a two-line `FIXME(#1095)` comment (lines 88-89).
- `bridge/email_relay.py:85-86` (`_normalize_payload` docstring mentioning
  `body`/`text`) — docstring at lines 83-87 on `main` says payload is
  unrecoverable "(missing `to` or both `body`/`text`)". The
  `/`text`` must be dropped.
- `tests/unit/test_email_relay.py::test_normalize_legacy_text_compat`
  (issue's cited test name) — **no test by this name exists.** The
  equivalent tests on `main` are:
  - `TestNormalizePayload::test_text_aliases_to_body` (line 41-45) — unit test
  - `TestProcessOutbox::test_drains_legacy_text_payload` (line 178-200) — integration
  - `TestNormalizePayload::test_missing_body_and_text_rejected` (line 51-53) —
    the assertion is still correct (missing `to` still rejects), but the test
    name and `msg` fixture mention `text`. Rename-only edit.
  - `tests/unit/test_email_relay.py` module docstring (lines 4-5) references
    "legacy `text` field compatibility" — must be edited.

**Cited sibling issues/PRs re-checked:**
- PR #1094 — MERGED at 2026-04-21T08:00:15Z. Introduced the shim and the
  `FIXME(#1095)` markers. Still the correct precondition for this work.

**Commits on main since issue was filed (touching referenced files):**
- `50cbd43` `feat(email): reply-all by default — capture To/CC on inbound, send to all recipients`
  — touches `bridge/email_relay.py` and `tests/unit/test_email_relay.py`.
  Reviewed: this commit changed the `to` field to support comma-separated
  strings and lists. It did NOT touch the `text`/`body` shim, did NOT change
  the payload's `body` field, and did NOT introduce new callers that emit
  `text`. Scope of #1095 is unchanged.

**Active plans in `docs/plans/` overlapping this area:** none.
Scanned for `email`, `relay`, `shim`, and `compat` — no active plans touch
`bridge/email_relay.py` or the outbox payload shape.

**Notes:** All issue claims hold. Line numbers drifted by 2-5 lines. The
cited test name `test_normalize_legacy_text_compat` doesn't exist; the plan
uses the actual test names discovered in-file.

## Prior Art

- **PR #1094** (`valor-email CLI: read / send / threads + outbox relay`, merged
  2026-04-21) — introduced the shim, wrote the two shim tests, and planted the
  `FIXME(#1095)` markers pointing at this follow-up. The PR's design
  explicitly scoped removal as a one-release follow-up. This plan executes
  that follow-up unchanged.

No prior failed attempts — this is a planned, scheduled cleanup, not a
recurring bug.

## Research

No relevant external findings — this is a localized internal cleanup with
zero ecosystem surface. Skipped WebSearch per Phase 0.7 guidance.

## Data Flow

Trivially intra-function. The shim is one of several transforms inside
`_normalize_payload`. Removing it means a `text`-only payload falls through
the "missing `body`" check (lines 98-100 on `main`) and returns `None`, which
the caller (`process_outbox`) routes to the DLQ without retry — the same
treatment as any other malformed payload.

Flow before:
  Payload with `text` -> shim renames to `body` -> validation passes -> send.

Flow after:
  Payload with `text` -> `body` key absent -> `_normalize_payload` returns
  `None` -> caller DLQs the payload (no retry).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none externally observable. The outbox queue
  contract as documented in the module docstring is unchanged — the `text`
  key was never part of the unified contract, only an unadvertised
  transitional tolerance.
- **Coupling:** slightly decreased (one less legacy shape to reason about).
- **Data ownership:** unchanged.
- **Reversibility:** trivial to revert (single commit, ~10 lines). If a
  producer is later discovered emitting `text`, it should be fixed at the
  producer, not reintroduced here.

## Appetite

**Size:** Small

**Team:** Solo dev.

**Interactions:**
- PM check-ins: 0 (scope locked by issue)
- Review rounds: 1 (normal PR review)

## Prerequisites

No prerequisites — this work touches only source files and tests that
already exist in the repo and requires no external credentials.

## Solution

### Key Elements

- **`bridge/email_relay.py`**: delete the `text` -> `body` shim and its
  surrounding `FIXME(#1095)` comment; strip "Legacy compat" and
  `FIXME(#1095)` paragraphs from the module docstring; strip `/text` from
  the `_normalize_payload` docstring's "(missing `to` or both
  `body`/`text`)" clause.
- **`tests/unit/test_email_relay.py`**: delete the two shim-specific tests
  (`test_text_aliases_to_body`, `test_drains_legacy_text_payload`); add a
  single replacement assertion confirming a `text`-only payload now DLQs
  (integration path) so the behavior change is pinned in place; update the
  module docstring to remove the "legacy `text` field compatibility" phrase;
  rename `test_missing_body_and_text_rejected` to
  `test_missing_body_rejected` and drop the `text`-related commentary.

### Flow

Single commit:
`session/email-relay-shim-removal` (worktree already exists on this branch)
-> apply the three source edits -> run `pytest tests/unit/test_email_relay.py`
-> `python -m ruff format .` -> open PR `Closes #1095`.

### Technical Approach

- Edit `bridge/email_relay.py` with two `Edit` tool calls:
  1. Replace the 7-line docstring block (lines 25-31 on `main` baseline
     `61a11980`) covering both the "Legacy compat" paragraph and the
     `FIXME(#1095)` paragraph with a blank trailing line (or collapse
     adjacent sections). The module docstring shrinks; no other content
     changes.
  2. Replace the 4-line block at lines 88-91 (the `FIXME(#1095)` comment +
     the `if "body" not in message and "text" in message: message["body"] =
     message.pop("text")` branch) with nothing. The function body contracts
     by 4 lines.
  3. Strip `/`text`` from the `_normalize_payload` docstring at line 86 so
     it reads "(missing `to` or `body`)" instead of
     "(missing `to` or both `body`/`text`)".

- Edit `tests/unit/test_email_relay.py`:
  1. Delete `TestNormalizePayload::test_text_aliases_to_body` (lines 41-45).
  2. Rename `test_missing_body_and_text_rejected` -> `test_missing_body_rejected`
     and drop the inline `text` comment if any (line 51-53). The assertion
     itself remains valid.
  3. Replace `TestProcessOutbox::test_drains_legacy_text_payload` (lines
     178-200) with `test_text_payload_dlqd_as_malformed`: same setup
     (payload with `text` not `body`), same `process_outbox()` invocation,
     but patches `bridge.email_dead_letter.write_dead_letter` and asserts
     DLQ was called exactly once, queue is empty (LPOPped, not re-pushed),
     AND that the DLQ record's `body == ""`. The `body == ""` assertion is
     load-bearing: `_dead_letter_message` reads `message.get("body", "")`,
     so once the shim is removed the `text` field's content is *dropped*
     rather than preserved on the DLQ record. Pinning `body == ""`
     documents this specific regression mode — if a future change
     reintroduces `text` aliasing at the DLQ boundary, this assertion
     fails. Matches the existing `test_malformed_payload_dlqd_without_retry`
     (line 202) pattern but with the stricter body-content check.
  4. Strip "legacy `text` field compatibility, and" from the module
     docstring (line 4-5) so it reads: "failure, DLQ after
     `MAX_EMAIL_RELAY_RETRIES` attempts, and heartbeat writes."

- Run `pytest tests/unit/test_email_relay.py -x -q` and
  `python -m ruff format bridge/email_relay.py tests/unit/test_email_relay.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No new `except Exception: pass` blocks are introduced. The existing
  exception-swallowing paths in `_normalize_payload` (explicit `None` returns
  for missing fields) are already covered by `test_missing_to_rejected` and
  the renamed `test_missing_body_rejected`.

### Empty/Invalid Input Handling
- [x] Empty-body behavior is already covered by `test_empty_body_allowed`
  (line 55-61) and is not affected by this change.
- [x] Invalid input (`text` without `body`) is the new behavior this plan
  adds coverage for: `test_text_payload_dlqd_as_malformed` asserts that
  `process_outbox` routes the payload to the DLQ without retry, rather than
  silently accepting it, and pins `body == ""` on the DLQ record to
  document that the `text` content is lost at the DLQ boundary (not
  aliased to `body` anymore).
- [x] No agent output processing involved — this is a bridge-internal
  cleanup with no agent-facing surface.

### Error State Rendering
- [x] No user-visible output. Behavior is observable only via DLQ writes,
  which are covered by the replacement test above.

## Test Impact

- [ ] `tests/unit/test_email_relay.py::TestNormalizePayload::test_text_aliases_to_body` — DELETE: the behavior being asserted is being removed.
- [ ] `tests/unit/test_email_relay.py::TestNormalizePayload::test_missing_body_and_text_rejected` — UPDATE: rename to `test_missing_body_rejected`. The assertion remains valid (missing `to` -> `None`); only the name changes.
- [ ] `tests/unit/test_email_relay.py::TestProcessOutbox::test_drains_legacy_text_payload` — REPLACE: replaced by `test_text_payload_dlqd_as_malformed`, which asserts the new (strict) behavior — a `text`-only payload is DLQ'd rather than silently accepted, the queue is empty after, and the DLQ record's `body == ""` (the `text` content is dropped at the DLQ boundary because `_dead_letter_message` looks up `body`, not `text`).
- [ ] `tests/unit/test_email_relay.py` module docstring — UPDATE: strip the "legacy `text` field compatibility" phrase from the summary (lines 4-5).

No other tests affected — scanned `tests/unit/test_email_bridge.py`,
`tests/unit/test_email_dead_letter.py`, `tests/integration/test_email*.py`
(if present). None reference `text` as an outbox payload key.

## Rabbit Holes

- **Don't add deprecation warnings.** The shim was never advertised as public
  API; the transitional window is over. Emitting a logger warning on `text`
  payloads would be more code than the shim itself, and there are no
  producers left to warn. Just delete.
- **Don't generalize to "any unknown key"**. Scope is specifically the
  `text` shim. Adding a "reject all unknown keys" strict-mode check is a
  separate concern — it would break forward compatibility for future payload
  extensions.
- **Don't touch the DLQ path.** The existing
  `test_malformed_payload_dlqd_without_retry` already covers the DLQ
  contract; the new `test_text_payload_dlqd_as_malformed` just proves the
  `text`-only payload takes that same path. No DLQ logic changes.

## Risks

### Risk 1: A straggler producer still emits `text`
**Impact:** An email that would previously have been delivered is DLQ'd instead.
**Mitigation:** Grep across the tree (done in this plan's Research/Freshness
check) confirms no producer emits `text`. The queue is populated only by
`tools/send_message.py` and `tools/valor_email.py`, both of which emit `body`.
DLQ is observable (`./scripts/valor-service.sh email-dead-letter list`) and
replayable (`email-dead-letter replay --all`), so if a forgotten producer is
ever discovered, the failure mode is recoverable, not catastrophic.

### Risk 2: An in-flight `text` payload is sitting in Redis at deploy time
**Impact:** Same as Risk 1 — one message DLQ'd instead of delivered.
**Mitigation:** The queue had no consumer before PR #1094 and has been
drained continuously since. In practice no in-flight `text` payloads exist.
Two days of drain history elapsed between PR #1094 merging and this cleanup
running.

## Race Conditions

No race conditions identified — `_normalize_payload` is a pure synchronous
transform on a single dict. The relay's concurrency is unchanged by this
edit.

## No-Gos (Out of Scope)

- No changes to the outbox payload schema beyond removing the `text`
  tolerance.
- No changes to the DLQ contract or retry policy.
- No changes to producers (`tools/send_message.py`, `tools/valor_email.py`) —
  they already emit `body`.
- No changes to `bridge/email_bridge.py`, `bridge/email_dead_letter.py`, or
  any SMTP / IMAP logic.
- No deprecation warnings, no feature flags — just delete.

## Update System

No update system changes required — this is purely an internal source edit
on the bridge host. The next `/update` run picks up the merged commit via
`git pull` with no additional migration or config propagation step.

## Agent Integration

No agent integration required — this is a bridge-internal cleanup. The
agent does not see outbox payloads; it only interacts with email via
`tools/valor_email.py` (producer) and `tools/send_message.py::_send_via_email`
(producer). Both already emit `body` and are unaffected by this change.

No `.mcp.json` or `mcp_servers/` changes. No bridge import changes.

## Documentation

### Feature Documentation
- [ ] Delete the "Transitional payload compat" paragraph in
  `docs/features/email-bridge.md` (line 286 on baseline `61a11980`, between
  the paragraph ending `... for operator liveness probes.` and the paragraph
  beginning `**\`EmailOutputHandler.send()\` does NOT write to the outbox**`).
  The exact paragraph to remove is:
  > **Transitional payload compat.** The relay's `_normalize_payload` accepts
  > the prior `{session_id, to, text, timestamp}` shape (aliasing `text` →
  > `body`) for one transitional release so in-flight entries from before
  > this change never get stranded.
  Remove the paragraph and the blank line that precedes it so the surrounding
  flow reads cleanly. No other edits to `email-bridge.md` are required; the
  payload contract documented above that paragraph already names `body` as
  the canonical key.

### External Documentation Site
Not applicable — this repo does not publish an external docs site.

### Inline Documentation
- [x] Module docstring and function docstring updates are covered by the
  source edits in the Technical Approach section.

### Justification for Minimal Docs
The only two authoritative mentions of the `text` payload key are (1) the
`bridge/email_relay.py` source itself and (2) the single "Transitional
payload compat" paragraph in `docs/features/email-bridge.md` flagged above.
Both are edited directly by this plan. A `grep -rn '"text"' docs/features/`
pass during validation confirms no other feature doc references the legacy
key.

## Success Criteria

- [ ] `bridge/email_relay.py` contains zero occurrences of the string
  `"text"` as a payload key reference or `#1095` in FIXME comments.
- [ ] `tests/unit/test_email_relay.py` contains zero `test_*legacy*` or
  `test_*text_alias*` test names.
- [ ] `pytest tests/unit/test_email_relay.py -x -q` passes.
- [ ] `python -m ruff format --check bridge/email_relay.py tests/unit/test_email_relay.py` is clean.
- [ ] `grep -n "1095" bridge/email_relay.py tests/unit/test_email_relay.py`
  returns nothing.
- [ ] `docs/features/*.md` contains no mention of a `text` payload key (grep confirmed).
- [ ] PR body contains `Closes #1095` so the issue auto-closes on merge.

## Team Orchestration

### Team Members

- **Builder (shim-removal)**
  - Name: `shim-removal-builder`
  - Role: Apply the three edits to `bridge/email_relay.py`, the four edits
    to `tests/unit/test_email_relay.py`, run pytest and ruff, commit and
    push.
  - Agent Type: builder
  - Resume: true

- **Validator (shim-removal)**
  - Name: `shim-removal-validator`
  - Role: Verify no `text` payload-key references remain, all tests pass,
    ruff is clean, and the PR body closes #1095.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Uses Tier 1: `builder`, `validator`.

## Step by Step Tasks

### 1. Apply source edits and test updates
- **Task ID**: build-shim-removal
- **Depends On**: none
- **Validates**: `tests/unit/test_email_relay.py`
- **Informed By**: Freshness Check (line numbers), Technical Approach (exact edits)
- **Assigned To**: shim-removal-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/email_relay.py`:
  - Remove the "Legacy compat" paragraph from the module docstring (lines 25-27 on baseline `61a11980`).
  - Remove the `FIXME(#1095)` paragraph from the module docstring (lines 29-31).
  - Strip `/`text`` from the `_normalize_payload` docstring so it reads "(missing `to` or `body`)" (line 86).
  - Remove the `FIXME(#1095)` comment and the `if "body" not in message and "text" in message: message["body"] = message.pop("text")` branch inside `_normalize_payload` (lines 88-91).
- In `tests/unit/test_email_relay.py`:
  - Delete `TestNormalizePayload::test_text_aliases_to_body` (lines 41-45).
  - Rename `test_missing_body_and_text_rejected` to `test_missing_body_rejected` (line 51-53).
  - Replace `TestProcessOutbox::test_drains_legacy_text_payload` (lines 178-200) with a new test `test_text_payload_dlqd_as_malformed` that pushes a `text`-only payload and asserts: (a) DLQ is called exactly once, (b) no send occurs, (c) the queue is empty (LPOPped, not re-pushed), and (d) the DLQ record's `body == ""` — the text content is dropped because `_dead_letter_message` reads `message.get("body", "")` and the removed shim no longer aliases `text` → `body` before the DLQ path runs. Model the structure on `test_malformed_payload_dlqd_without_retry`; the `body == ""` assertion is the extra pin that documents the content-loss behavior.
  - Strip "legacy `text` field compatibility, and" from the module docstring (lines 4-5).
- Run `pytest tests/unit/test_email_relay.py -x -q`.
- Run `python -m ruff format bridge/email_relay.py tests/unit/test_email_relay.py`.
- Commit with message `Remove legacy text->body compat shim in email_relay (#1095)` and push.

### 2. Validate
- **Task ID**: validate-shim-removal
- **Depends On**: build-shim-removal
- **Assigned To**: shim-removal-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -n "1095" bridge/email_relay.py tests/unit/test_email_relay.py` — expect no matches.
- Run `grep -n '"text"' bridge/email_relay.py` — expect no occurrences as a payload-key string.
- Run `pytest tests/unit/test_email_relay.py -x -q` — expect pass.
- Run `python -m ruff format --check bridge/email_relay.py tests/unit/test_email_relay.py` — expect clean.
- Run `grep -rn '"text"' docs/features/ | grep -i email` — expect no matches; if any found, route to docs task.
- Report pass/fail.

### 3. Documentation
- **Task ID**: document-shim-removal
- **Depends On**: validate-shim-removal
- **Assigned To**: shim-removal-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Grep `docs/features/` for any mention of the `text` payload key in an email outbox context. If found, strip the mention with a surgical edit.
- No new feature doc needed — the shim was never a public feature.
- If no doc updates are found necessary, note "No documentation updates needed — the shim was never documented as a public feature" in the PR body.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-shim-removal
- **Assigned To**: shim-removal-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run all Verification table commands (see below) and confirm all exit codes match expected.
- Confirm the PR body contains `Closes #1095`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_email_relay.py -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/email_relay.py tests/unit/test_email_relay.py` | exit code 0 |
| No residual #1095 FIXMEs | `grep -n "1095" bridge/email_relay.py tests/unit/test_email_relay.py` | exit code 1 |
| No `text` payload-key in source | `grep -n "\"text\"" bridge/email_relay.py` | exit code 1 |
| No legacy-text tests remain | `grep -En "test_.*(legacy_text\|text_aliases)" tests/unit/test_email_relay.py` | exit code 1 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — 2 concerns resolved via plan
revision (Row 4b path). `revision_applied: true` set in frontmatter.

### Concern 1 — Documentation section too conditional
The original Documentation section only said "Check ... for any mention" of
the `text` payload key, leaving the editor to re-derive whether a doc edit
was needed. A concrete paragraph exists at `docs/features/email-bridge.md`
line 286 ("**Transitional payload compat.** The relay's `_normalize_payload`
accepts the prior `{session_id, to, text, timestamp}` shape ...") and must
be deleted alongside the source shim — otherwise the docs would still
advertise the compat behavior after the shim is gone.

**Resolution:** Documentation section rewritten to specify the exact
paragraph to remove, quoted verbatim, with the baseline line number
(286 @ `61a11980`). The task is now a concrete edit, not a grep.

### Concern 2 — DLQ assertion didn't pin content-loss behavior
The replacement test `test_text_payload_dlqd_as_malformed` originally asserted
only that DLQ was called once and the queue was empty. But
`_dead_letter_message` (line 141) reads `message.get("body", "")`, and the
removed shim means `body` is never populated from `text`. The `text` content
is silently dropped at the DLQ boundary. Without an assertion that pins this
behavior, a future regression that reintroduces `text` aliasing at the DLQ
layer (e.g., someone "helpfully" adding a compat branch inside
`_dead_letter_message`) would not be caught.

**Resolution:** Test now asserts `body == ""` on the DLQ record, documenting
the content-loss behavior as part of the contract. Technical Approach,
Step-by-step task, Test Impact, and Failure Path Test Strategy sections all
updated to call out the stricter assertion.

---

## Open Questions

None. Scope is fully locked by issue #1095 and PR #1094's `FIXME` markers;
the Freshness Check resolved all references against current `main`.
