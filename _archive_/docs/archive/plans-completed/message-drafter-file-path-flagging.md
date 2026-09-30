---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1955
last_comment_id: null
revision_applied: true
---

# Message Drafter File-Path Flagging

## Problem

A user ran `/weekly-review` via Telegram (session `5c6571edf9ba4852baa42cbf2cd3ed1a`,
thread `tg_psyoptimal_-1003743854645_313`, project PsyOPTIMAL). The skill saved
its output to `/tmp/eng_review_jul1-8.txt` and the agent sent one Telegram
message:

> ✅ Weekly review done (Jul 1–8, 2026) — saved to `/tmp/eng_review_jul1-8.txt`. Open with `open -a TextEdit /tmp/eng_review_jul1-8.txt`.
>
> # Engineering Review - Jul 1-8, 2026
> ... (full review pasted inline) ...

**Current behavior:** `/tmp/eng_review_jul1-8.txt` and `open -a TextEdit` only
resolve on the machine that ran the session — meaningless to anyone else in
the group chat. Nothing in the delivery pipeline catches this before send.
Investigation (recorded in issue #1955) traced the deeper cause: the message
drafter (`bridge/message_drafter.py`) computes wire-format `Violation`s (e.g.
markdown-table detection) on every drafted message, but its only historical
consumer — `agent/hooks/stop.py`'s "delivery review gate" — is dead code for
`session_type=eng` sessions run through the current `agent/session_runner/`
headless architecture (confirmed independently twice: once for this issue,
once during the freshness re-check of the sibling `consolidate_delivery_paths`
plan, issue #1370). So **today, every wire-format violation computed by the
drafter for an eng session is silently discarded** — nobody ever sees it, and
nothing prevents a local-path reference from reaching the user verbatim.

**Desired outcome:** When drafted text contains a local filesystem path or a
machine-local shell command reference (`/tmp/\S+`, `~/\S+`, `` `open -a \w+` ``,
bare `/Users/...`, etc.), the message drafter detects it as a `Violation`, and
that violation (like any other violation) reaches the agent via the one
mechanism actually live for eng/session_runner sessions today — the
self-draft steering path (`agent/output_handler.py:429-441`) — so the agent
can rewrite/resend, e.g. by attaching the file via
`tools/send_message.py "<caption>" --file <path>`, instead of leaving a dead
local-path reference in the message.

## Freshness Check

**Baseline commit:** `99ad930e1` (`git rev-parse HEAD` at plan time, 2026-07-08 — this includes the just-landed `consolidate_delivery_paths` freshness-correction commit)
**Issue filed at:** 2026-07-08T09:31:43Z
**Disposition:** Unchanged. The issue was filed today; no commits landed between filing and planning except the `consolidate_delivery_paths` plan-doc-only correction (docs, no code), which does not touch any file this plan cites.

**File:line references re-verified (all confirmed live against `99ad930e1`):**
- `bridge/message_drafter.py:702-830` (`draft_message()`) — confirmed current; flow steps 1-8 in its docstring match the code exactly, including the stale claim at `:728-730` ("wire-format violations... surface via the stop-hook review gate (`agent/hooks/stop.py`)") that this plan corrects.
- `bridge/message_drafter.py:244-267` (`validate_telegram`), `:284-309` (`validate_email`), `:323-329` (`_validate_for_medium` dispatcher), `:184-190` (`Violation` dataclass), `:312-320` (`format_violations`) — all confirmed current, unchanged line numbers.
- `agent/output_handler.py:414-425` (drafter call site inside `TelegramRelayOutputHandler.send()`), `:429-441` (self-draft steering branch), `:794-863` (`_inject_self_draft_steering`, including the `SELF_DRAFT_MAX_ATTEMPTS` budget and `peek_steering_sender` concurrent-guard) — all confirmed current.
- `agent/hooks/stop.py:145-199` (`_generate_draft`/`_build_review_prompt`, the dead "DELIVERY REVIEW" gate) — confirmed still present in the file but confirmed **unreachable** for session_runner turns (`agent/session_runner/hook_edge.py::generate_hook_settings` wires only `hook_forwarder.py` for the Stop hook, never `agent/hooks/stop.py`).
- `.claude/skills-global/weekly-review/SKILL.md:97-104` ("Final step: save the document") — confirmed current; still instructs `/tmp/` save + `open -a TextEdit <path>` with no attachment-delivery instruction.
- `models/agent_session.py:260` (`recent_sent_drafts` field), `:1773-1819` (`record_recent_sent_draft`), called from `agent/output_handler.py:782` — confirmed current; this is what proved the incident's sent text came from `draft_message()`.

**Cited sibling issues/PRs re-checked:**
- #1802 (open) — "PM session has no file-capable send path" — re-read in full. Confirmed genuinely distinct: #1802 is about the granite/session_runner **PM persona** having no `file_paths` hook into the outbox *at all* (a structural gap for a shell-forbidden persona). This plan's incident session had Bash access and the attachment mechanism was fully reachable — the gap here is detection/surfacing, not a missing capability. No overlap in code scope; #1802's fix (teaching the outbound contract to carry `file_paths`) is orthogonal to and would not need to touch anything this plan changes.
- #1680 (closed) — "Reposition message drafter from rewriting summarizer to pass-through validation filter" — established the current drafter architecture (verbatim pass-through + validators) this plan extends. No conflict.
- #1794 (closed) — "deferred self-draft reply is lost when the session reaches 'completed'" — fixed a reliability gap in the self-draft steering path this plan routes new violations through. Its fix (a `completed`-path flush at the `finalize_session` chokepoint) already covers the deferred-delivery-lost failure mode generically, regardless of which violation type triggered the defer — so promoting a new violation class through the same `needs_self_draft` path inherits that fix for free. No new work needed here.
- #1370 (open, `docs/plans/consolidate_delivery_paths.md`) — active, unbuilt (status: Planning). Discovered mid-recon to have a stale Freshness Check (predating the #1924 teardown); corrected and pushed (commit `99ad930e1`) before writing this plan, with a note added to its own Freshness Check flagging the overlap with this plan. That plan's scope (retiring `tools/send_telegram.py`, `DeliveryOutcome` return values, `deliver_system_notice()` seam) is orthogonal to this plan's scope (detecting + surfacing one new violation class). Both touch `agent/output_handler.py`'s self-draft-steering block (`:429-441`) but in non-conflicting ways: this plan changes what triggers `needs_self_draft`; #1370 changes what `send()` returns after the fact. Sequencing either order is safe; no blocking dependency.

**Active plans in `docs/plans/` overlapping this area:** #1370 (`consolidate_delivery_paths.md`), addressed above — coordinate, not merge or block.

**Notes:** No drift requiring plan-scope changes. The issue's Recon Summary (in the GitHub issue body) is the primary source for this plan's Solution; this Freshness Check re-confirms nothing has moved since.

**Re-verification at plan-finalize time (2026-07-09, HEAD `2fb1f8ef`):** Two commits landed on `main` between the original baseline (`99ad930e`) and finalize. Neither affects this plan's scope. Disposition: **Minor drift (evidence-only)**.
- `bec97694` ("Fix headless runner zombie wedge") touched `agent/session_runner/` internals only — none of this plan's cited files (`bridge/message_drafter.py`, `agent/output_handler.py`, `.claude/skills-global/weekly-review/SKILL.md`). Irrelevant to scope.
- `0f33567e` ("SDLC issue ownership lock") added 10 lines to `models/agent_session.py`. This plan cites `models/agent_session.py:260` / `:1773-1819` (`recent_sent_drafts` / `record_recent_sent_draft`) as **evidence only** (not modified). Line numbers may have shifted by a few lines but the claim (the incident's sent text was produced by `draft_message()`) still holds. No plan-scope change.
- Core modification targets re-confirmed live at HEAD: `draft_message()` @ `message_drafter.py:702`, `_validate_for_medium` @ `:323`, `validate_telegram` @ `:244`, `validate_email` @ `:284`, stale docstring still present @ `:729-730`, empty-promise `needs_self_draft` trigger @ `:801-806`, self-draft steering branch @ `output_handler.py:434`, `open -a TextEdit` still at `weekly-review/SKILL.md:104`. `detect_local_file_reference` confirmed absent (work still needed).

**Re-confirmation at first tracked PLAN dispatch (2026-07-09, HEAD `6dd434f0`):** Four commits landed on `main` since the `2fb1f8ef` stamp above — all **plan-document-only** (`0029d6cb`, `fb9d1cc6` this plan's own finalize/revision; `459c8eb4`, `6ce43e34`, `3c524806`, `6dd434f0` other plans/docs). `git log 2fb1f8ef..HEAD -- bridge/message_drafter.py agent/output_handler.py .claude/skills-global/weekly-review/SKILL.md agent/hooks/stop.py` returns empty — zero source drift. Disposition: **Unchanged**. All cited anchors re-verified present (`draft_message`, `_validate_for_medium`, `SELF_DRAFT_INSTRUCTION`, `_inject_self_draft_steering` all live; `detect_local_file_reference` still absent; `open -a TextEdit` still in the skill). Recon gate re-passes (4 buckets, 5 items). No plan-scope change.

## Prior Art

- **Issue #1680 / PR #1685** — drafter repositioned to verbatim pass-through + validation (no LLM rewriting). This plan extends that validator set with one new rule; it does not reopen the rewriting question.
- **Issue #1794** — deferred self-draft reliability fix. Confirms the `needs_self_draft` path this plan routes through has already had its main failure mode (lost delivery on session completion) patched — no new reliability work required.
- **Issue #1370** — sibling plan, addressed in Freshness Check above.
- No prior attempt to add local-file-path detection was found (`gh issue list --state closed --search "local file path violation drafter"` returned no results) — this is new-territory detection logic, not a re-fix.

## Research

No relevant external findings — this is a regex-based internal validator addition with no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

1. **Entry point:** An agent (any persona, any skill) produces turn text ending in `[/user]`/`[/complete]` (or, for a direct CLI invocation, calls `tools/send_message.py`). Either path funnels to `agent/output_handler.py::TelegramRelayOutputHandler.send()`.
2. **Drafter call:** `send()` calls `draft_message(text, session=session, medium=drafter_medium)` (`output_handler.py:414-420`).
3. **Validation:** Inside `draft_message()`, `_validate_for_medium(composed_text, medium)` (`message_drafter.py:798`) dispatches to `validate_telegram`/`validate_email`, which today return `Violation` objects for markdown-table/markdown-syntax problems only. This plan adds a medium-agnostic `detect_local_file_reference(text)` check, merged into the same violations list regardless of transport.
4. **Promotion to actionable — BOTH return paths (critique B1):** Today, a non-empty `violations` list is returned in the `MessageDraft` but does **not** set `needs_self_draft` (only `_detect_empty_promise` does, `message_drafter.py:801-809`) — so violations are silently dropped for eng sessions (their only consumer, `stop.py`, is dead). `draft_message()` has **two** distinct return statements that carry a `violations` list without promoting it, and the incident text (`"Done. Saved to /tmp/x.txt."` — short, no `?`, no fence, no artifacts, no SDLC session) exits through the *first* of them:
   - **Short-output early return** (`message_drafter.py:770-781`) — returns `MessageDraft(text=raw_response, artifacts=artifacts, violations=_validate_for_medium(raw_response, medium))` for brief non-SDLC replies. It computes violations inline but never inspects them, so a terse message carrying a raw local path ships verbatim. This is the path the reported incident-class message hits.
   - **Main-path return** (`message_drafter.py:823-831`) — reached for composed/longer messages; also returns `needs_self_draft=False` with a populated `violations` list (unless the earlier empty-promise branch at `:801-809` fired first).
   This plan promotes violations to `needs_self_draft=True` at **both** return points, generalizing the existing empty-promise-triggers-self-draft pattern to cover ALL violations, not just the new local-path one, on every path that can carry them. Covering only the main path (as the pre-critique draft did) would let the exact reported message class slip through — hence B1 is a hard blocker, not a nicety.
5. **Surfacing:** Back in `output_handler.py:434-441`, `needs_self_draft=True` triggers `_inject_self_draft_steering(session, draft)` — a steering message is pushed into the session's Redis steering queue, the agent notices it at its next turn boundary, and rewrites/resends (or the budget/pending-guard falls through to narration fallback, per existing logic — see Race Conditions).
6. **Instruction content — violation-aware nudge (critique B2):** The steering message the agent receives is built from `SELF_DRAFT_INSTRUCTION`. That constant (`message_drafter.py:570-578`) is fixed and violation-type-agnostic: it never mentions local paths or file attachment and actively says "Omit internal code details" — so on its own it does **not** steer the agent toward the outcome the issue asks for ("attach the file as a real Telegram attachment"). This plan makes the injected instruction violation-aware: when the deferred draft carries a `local_file_path_reference` violation, `_inject_self_draft_steering` appends a targeted addendum instructing the agent to attach the referenced file via `tools/send_message.py "<caption>" --file <path>` (or drop the path if no file was meant), rather than re-pasting it. Without this, the self-draft round-trip would fire but produce the same unusable local-path text — the plan's outcome would under-claim relative to the issue.
7. **Output:** The agent's next turn either resends corrected text (replacing the local path with a real attachment via `tools/send_message.py --file`, as the addendum now explicitly directs) or, if the self-draft budget is exhausted, the original text is delivered as-is (existing non-blocking-guard behavior — never silently dropped).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|---------------------------------|
| PR #1685 (#1680) | Repositioned drafter to pass-through + validators, documented that "wire-format violations... surface via the stop-hook review gate" | Correct when written, but the #1924 granite-PTY teardown (PR #1930) later made `agent/hooks/stop.py` unreachable for session_runner/eng turns, silently orphaning every violation the drafter computes for that session type. Nobody updated the drafter's docstring or wiring when the teardown landed, because the teardown's own plan (`docs/plans/completed/granite-pty-teardown.md`) was scoped to the runner architecture, not the delivery-validation contract it incidentally broke. |

**Root cause pattern:** a feature (violation surfacing) depended on a specific hook wiring (`agent/hooks/stop.py`) that a later, unrelated architecture migration silently removed from the reachable path for the majority of production sessions. No test asserted "a computed violation actually reaches the agent" end-to-end for an eng/session_runner session, so the breakage was invisible until a live incident (this issue) surfaced it.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `draft_message()`'s behavior changes for any text containing a wire-format violation, on **both** its return paths (short-output early return and main-path return) — previously delivered as-is (silently, for eng sessions) or bounced via the dead stop.py gate (for whatever, if anything, still used the pre-teardown path); now uniformly triggers `needs_self_draft=True` and the (already-live) self-draft steering flow. `MessageDraft`'s public shape is unchanged (no new fields) — `violations` already exists; only the wiring of "does a violation set `needs_self_draft`" changes.
- **`_inject_self_draft_steering` signature change:** in `agent/output_handler.py`, `_inject_self_draft_steering(self, session)` gains a second parameter — the deferred `draft` (or its `violations` list) — so it can compose a violation-aware steering instruction (critique B2). The single existing call site (`output_handler.py:435`) already has `draft` in scope; no other caller exists (verified). This is an internal method; no public API changes.
- **Coupling:** the new validator function lives in the same file as its siblings and is invoked through the same existing dispatcher. One additional file enters scope — `agent/output_handler.py` — but only to thread the already-in-scope `draft`/`violations` into the instruction builder; no new cross-module import beyond the `local_file_path_reference` rule-name constant already exported by `message_drafter.py`.
- **Data ownership:** unchanged.
- **Reversibility:** high — the change is additive (one new validator function) plus two matching boolean-condition tweaks (`if violations:` promotion at both return paths in `draft_message()`) plus a violation-aware instruction addendum in `_inject_self_draft_steering`. Reverting is a single-commit revert.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies; everything runs against repo-local code and the existing test harness.

## Solution

### Key Elements

- **`detect_local_file_reference(text)`**: a new medium-agnostic validator in `bridge/message_drafter.py`, alongside `validate_telegram`/`validate_email`, that regex-scans for local filesystem path patterns and macOS-only shell command references. Emits `Violation(rule="local_file_path_reference", ...)`. The rule-name string is exported as a module constant (`LOCAL_FILE_PATH_RULE = "local_file_path_reference"`) so the self-draft instruction builder can detect it without hard-coding the literal.
- **Violation promotion on BOTH return paths (critique B1)**: `draft_message()` sets `needs_self_draft=True` whenever `_validate_for_medium()` returns any non-empty violations list — applied at **both** the short-output early return (`message_drafter.py:770-781`) and the main-path return (`:823-831`), generalizing the existing empty-promise trigger. The short-output path is the one the reported incident message class actually exits through, so promoting only the main path (the pre-critique draft) would leave the exact bug unfixed.
- **Violation-aware self-draft instruction (critique B2)**: the fixed `SELF_DRAFT_INSTRUCTION` constant stays compact and unchanged as the base. `_inject_self_draft_steering` (in `agent/output_handler.py`) is extended to accept the deferred `draft` and, when its violations include a `local_file_path_reference`, append a targeted addendum telling the agent to attach the referenced file via `tools/send_message.py "<caption>" --file <path>` (or drop the path if no file was intended). This is what actually produces the issue's requested "real Telegram attachment" outcome — the base instruction alone steers toward omitting details, not attaching.
- **Docstring correction**: `draft_message()`'s docstring (currently stale at `message_drafter.py:728-730`) updated to describe the corrected flow (both return paths promote violations; violation-aware steering).
- **Skill fix**: `.claude/skills-global/weekly-review/SKILL.md`'s "Final step" section stops instructing `open -a TextEdit <path>` as the delivery mechanism.

### Flow

Agent drafts a reply containing `/tmp/eng_review_jul1-8.txt` → `draft_message()` runs `_validate_for_medium()` → `detect_local_file_reference()` fires a `Violation(rule="local_file_path_reference")` → `violations` is non-empty → the return path taken (short-output early return for the terse incident message, or main-path return for a longer one) promotes to `MessageDraft(text="", needs_self_draft=True, violations=[...])` → `output_handler.py` calls `_inject_self_draft_steering(session, draft)`, which composes the base instruction plus a local-file addendum ("attach via `tools/send_message.py --file <path>`") because the draft carries a `local_file_path_reference` violation → agent's next turn sees the steering nudge, rewrites using `tools/send_message.py "<caption>" --file /tmp/eng_review_jul1-8.txt` → file arrives as a real Telegram attachment.

### Technical Approach

1. **`bridge/message_drafter.py` — new validator**: add `detect_local_file_reference(text: str) -> list[Violation]` near `validate_telegram`/`validate_email` (after line 309), plus a module constant `LOCAL_FILE_PATH_RULE = "local_file_path_reference"` used as the emitted rule name (so the instruction builder in step 6 matches on the constant, not a bare literal). Patterns to check (case-sensitive, since these are Unix paths):
   - `` /tmp/\S+ `` — temp-file paths
   - `` /Users/\S+ `` and `` /home/\S+ `` — absolute home-directory paths
   - `` ~/\S+ `` — tilde-relative paths
   - `` `open (-a\s+\S+\s+)?\S+` `` (backtick-wrapped) or bare `open -a \S+` — macOS `open` command references
   Each match produces `Violation(rule=LOCAL_FILE_PATH_RULE, line=..., snippet=...)`. Keep the same shape/pattern as `validate_telegram`'s markdown-table scan (single pass over lines, `re.compile` module-level patterns).
2. **`bridge/message_drafter.py::_validate_for_medium`** (line 323): after the medium-specific dispatch, extend the returned list with `detect_local_file_reference(text)` regardless of medium — local paths are meaningless on both Telegram and email.
3. **`bridge/message_drafter.py::draft_message` — promote violations on BOTH return paths (critique B1)**:
   - **Short-output early return (`:770-781`)**: this path currently returns `MessageDraft(text=raw_response, artifacts=artifacts, violations=_validate_for_medium(raw_response, medium))` and never inspects the violations. Capture the validator result into a local (`short_violations = _validate_for_medium(raw_response, medium)`) and, when it is non-empty, return `MessageDraft(text="", needs_self_draft=True, artifacts=artifacts, violations=short_violations)` instead of the verbatim pass-through. This is the path the reported incident message (`"Done. Saved to /tmp/x.txt."`) exits through, so it MUST promote. When `short_violations` is empty, behavior is unchanged (verbatim pass-through).
   - **Main-path return (`:801-831`)**: change the early-return condition that currently checks only `_detect_empty_promise(...)` to also fire on a non-empty `violations` list — i.e. `if _detect_empty_promise(stripped_text.lower()) or violations:` returns `MessageDraft(text="", needs_self_draft=True, full_output_file=full_output_file, artifacts=artifacts, violations=violations)`. Preserve the existing empty-promise-specific log message; add a parallel log line for the violations-only case (e.g. `"Wire-format violation(s) detected — requesting self-draft via steering: %s"`, listing violation rules). With this early return in place, the final `:823-831` return (`needs_self_draft=False`) is reached only when `violations` is empty, so its `needs_self_draft=False` remains correct.
   - Consider extracting a tiny local helper (e.g. `_promoted_draft(violations, artifacts, full_output_file=None)`) to keep the two promotion sites identical and avoid drift; optional, builder's discretion.
4. **`agent/output_handler.py` — violation-aware self-draft instruction (critique B2)**:
   - Change `_inject_self_draft_steering(self, session)` → `_inject_self_draft_steering(self, session, draft)` and update the sole call site at `output_handler.py:435` to pass the in-scope `draft`.
   - Build the pushed message as `SELF_DRAFT_INSTRUCTION` plus, when `draft.violations` contains a violation whose `rule == LOCAL_FILE_PATH_RULE` (imported from `message_drafter`), a concise addendum such as: `"\n\nOne or more local filesystem paths were detected in your message. Those paths are meaningless to the recipient. If you meant to share a file, attach it as a real Telegram attachment with `tools/send_message.py \"<caption>\" --file <path>` instead of pasting the path. If no file was meant, remove the path reference."` Keep the base `SELF_DRAFT_INSTRUCTION` constant compact and unchanged (the existing `len < 1000` assertion in `test_message_drafter.py:270` still applies to the base constant).
   - The addendum composition lives in `_inject_self_draft_steering` (not in the constant) so it fires only when the local-path rule is present; other violation types (markdown table, empty promise) keep the base instruction alone.
5. **Docstring**: update `draft_message()`'s docstring at `message_drafter.py:709-735` to remove the stale "surface via the stop-hook review gate (agent/hooks/stop.py)" line and replace with: "Wire-format violations (markdown table, local file-path reference, etc.) now trigger `needs_self_draft=True` directly on both the short-output and main return paths, same as empty-promise detection — all route through the self-draft steering path (`agent/output_handler.py:429-441`), where a `local_file_path_reference` violation adds an attach-via-`--file` instruction."
6. **`.claude/skills-global/weekly-review/SKILL.md`** (lines 97-104): replace "After saving, offer: `open -a TextEdit <path>`" with guidance that the file has been saved locally and the agent should let the delivery pipeline flag it (no special-casing needed — the new drafter check handles it generically). Keep the `/tmp/` save instruction (still correct — the file must exist somewhere before it can be attached); only the "how to hand it to the user" framing changes.

## Failure Path Test Strategy

### Exception Handling Coverage
- `detect_local_file_reference` is a pure regex function with no I/O and no exception handlers — nothing to test here beyond normal input coverage.
- `draft_message()`'s existing try/except-free flow is unchanged in structure; the only new failure surface is a regex match, which cannot raise on well-formed `str` input. No new exception handling needed.

### Empty/Invalid Input Handling
- [ ] `detect_local_file_reference("")` returns `[]` (matches sibling validators' empty-string contract).
- [ ] `detect_local_file_reference` on text with no path-like substrings returns `[]` (no false positives on ordinary prose, including text containing standalone `/` or `~` characters that aren't part of a path).
- [ ] `draft_message()` with a violations-only (non-empty-promise) composed text still returns `needs_self_draft=True` and `text=""` — verify the promotion doesn't accidentally require both conditions.
- [ ] `draft_message()` on a SHORT-OUTPUT message carrying a local path (e.g. `"Done. Saved to /tmp/x.txt."` — under 200 chars, no `?`, no fence, no artifacts, no SDLC session) returns `needs_self_draft=True` and `text=""` (critique B1 — proves the short-output early return promotes, not just the main path). A short-output message with NO violation still returns verbatim pass-through with `needs_self_draft=False`.

### Error State Rendering
- [ ] Not applicable — this validator produces no user-facing rendering of its own; it feeds into the existing self-draft steering message, whose rendering is already tested (`tests/unit/test_output_handler.py`).

## Test Impact

- [ ] `tests/unit/test_medium_validators.py` — UPDATE: add a `TestDetectLocalFileReference` class covering `/tmp/...`, `~/...`, `/Users/...`, `open -a ...` patterns, plus false-positive guards (ordinary prose, code blocks referencing unrelated paths like URLs).
- [ ] `tests/unit/test_drafter_validators.py` — UPDATE: this file duplicates `test_medium_validators.py`'s validator coverage (both test `validate_telegram`/`validate_email` independently) — add the same new test class here too for consistency with the existing (if redundant) pattern, OR flag the duplication to the user as a candidate cleanup outside this plan's scope. Default: add matching coverage in both files to avoid diverging test suites; do not attempt to de-duplicate the two files (out of scope, see No-Gos).
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage` — UPDATE: add TWO cases (critique B1): (a) a **short-output** message carrying a local path returns `needs_self_draft=True` / `text=""` (exercises the `:770-781` early-return promotion), and (b) a **long/composed** message carrying a local path returns `needs_self_draft=True` / `text=""` (exercises the `:801-831` main-path promotion). Mirror the existing `test_default_needs_self_draft_false` and the over-length-does-NOT-trigger-self-draft case at line ~218-222. Also update/extend any assertion that currently expects wire-format violations to leave `needs_self_draft=False` — that expectation flips as part of this fix. The existing `SELF_DRAFT_INSTRUCTION` content/`len < 1000` assertion (`:264-270`) stays valid — the base constant is unchanged; the addendum is composed at injection time in `output_handler.py`, tested there.
- [ ] `tests/unit/test_output_handler.py` — UPDATE: (a) update existing callers/mocks of `_inject_self_draft_steering` for the new `(session, draft)` signature (the mock at `:1190` and the flow tests at ~490-622). (b) Add a test asserting that when the deferred draft carries a `local_file_path_reference` violation, the pushed steering message CONTAINS the attach-via-`--file` addendum (`tools/send_message.py` + `--file`), and that a draft with a non-local-path violation (e.g. markdown table) pushes the base `SELF_DRAFT_INSTRUCTION` WITHOUT the addendum (critique B2 — proves the instruction actually tells the agent to attach the file). (c) Add one end-to-end test that constructs the draft via a real `draft_message()` call with short local-path text to prove the full chain (drafter short-output path → handler → violation-aware steering).
- [ ] `tests/integration/test_message_drafter_integration.py` — UPDATE: add an integration case for the local-file-path incident scenario (text resembling the actual weekly-review message) to guard against regression of this exact bug.

## Rabbit Holes

- **De-duplicating `test_medium_validators.py` and `test_drafter_validators.py`.** Both files test the same functions with near-identical cases. Tempting to merge them while touching this area, but it's an unrelated test-hygiene cleanup with its own blast radius (import updates, possible CI config references) — not in scope here. Filed as a note in Test Impact, not a task.
- **Reviving or repairing `agent/hooks/stop.py`'s review gate for session_runner.** Once you notice stop.py is dead, it's tempting to "fix" it by wiring it into `hook_edge.py`. Don't — that's a structurally different, larger change (redesigning the session_runner hook surface) that's explicitly out of scope; the self-draft steering path is already the live, working mechanism for eng sessions and needs no new gate.
- **Building a general "local path detector" library shared across the codebase.** The regex patterns here are intentionally narrow and drafter-specific (messages about to leave the machine). Don't generalize into a shared utility module for a four-pattern regex check — YAGNI.

## Risks

### Risk 1: False positives on legitimate text mentioning paths
**Impact:** A message that legitimately references a path as informational content (e.g., "the config lives at `/etc/nginx/nginx.conf` on your server," describing a REMOTE path) could get flagged and deferred unnecessarily, adding a steering round-trip for no benefit.
**Mitigation:** Patterns are scoped to paths that are specifically *local-machine-only in a Telegram/email delivery context* — `/tmp/`, `/Users/`, `/home/`, `~/`, and `open -a`. These prefixes are overwhelmingly used for "this file is on my machine" in agent-drafted text, not for describing remote/other-system paths (which would more commonly reference `/etc/`, `/var/`, arbitrary server paths without the `/Users`/`/home`/`~` home-directory signal). Test suite includes explicit false-positive guards (Failure Path Test Strategy). If false positives prove common in practice, narrowing the pattern set is a one-line follow-up — the self-draft steering path already degrades gracefully (worst case: one extra rewrite round-trip, not a broken message).

### Risk 2: Promoting ALL violations to `needs_self_draft` changes behavior for markdown-table violations too
**Impact:** Previously (in the dead-stop.py world), a markdown-table violation was silently delivered as-is for eng sessions. After this fix, it will trigger a self-draft steering round-trip instead. This is a deliberate, in-scope behavior change (not a side effect to avoid) — but it means more messages will take the self-draft round-trip path than before.
**Mitigation:** The self-draft steering path already has a budget (`SELF_DRAFT_MAX_ATTEMPTS`) and a concurrent-guard (`peek_steering_sender`) to prevent runaway loops or duplicate steering pushes (`agent/output_handler.py:794-863`), and a proven reliability fix for the "reply lost on session completion" failure mode (#1794). No new safety mechanism is needed — this plan reuses infrastructure already hardened for exactly this kind of traffic increase.

## Race Conditions

### Race 1: Self-draft steering budget exhaustion during a burst of violating messages
**Location:** `agent/output_handler.py:794-863` (`_inject_self_draft_steering`, `SELF_DRAFT_MAX_ATTEMPTS` check)
**Trigger:** A session produces several consecutive messages that each trip a violation (e.g., repeatedly re-referencing the same bad path after a flawed self-draft attempt).
**Data prerequisite:** `bump_self_draft_attempts(session_id)` count must exceed `SELF_DRAFT_MAX_ATTEMPTS` (existing Redis-backed counter, `agent/steering.py:219-244`).
**State prerequisite:** None new — this plan does not change the budget mechanism, only what triggers a call into it.
**Mitigation:** Existing behavior: once the budget is exhausted, `_inject_self_draft_steering` returns `False`, and the caller falls through to `_apply_narration_fallback` (delivers the original text as-is unless it's judged pure narration). This means a persistently-violating message still eventually reaches the user rather than looping forever or being dropped — the existing non-blocking-guard contract holds unchanged.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1802] Giving the granite/session_runner PM persona a file-capable send path when it currently has none (shell-forbidden personas can't invoke `tools/send_message.py --file` at all). That is a structurally different, already-filed problem.
- De-duplicating `tests/unit/test_medium_validators.py` and `tests/unit/test_drafter_validators.py` — noted as a Rabbit Hole; not in scope for this fix.
- Reviving `agent/hooks/stop.py`'s review gate for `session_runner` — noted as a Rabbit Hole; the self-draft steering path is the correct live mechanism and needs no new gate built alongside it.
- Coordinating code changes with `docs/plans/consolidate_delivery_paths.md` (#1370) beyond the Freshness Check note already added to both plans — that plan is Medium appetite and not yet critiqued; this plan's small, additive change does not need to wait on it (see Freshness Check).

## Update System

No update system changes required — this is a Python code change (one new function, one conditional tweak, one docstring update) plus a global-skill Markdown edit that propagates via normal `git pull` in `/update`'s hardlink sync (`.claude/skills-global/` → `~/.claude/skills/`). No new dependencies, no config files, no Popoto schema changes.

## Agent Integration

No agent integration changes required beyond the skill-doc edit already covered in Technical Approach — this plan does not add a new CLI entry point, MCP server, or bridge-internal call path. The mechanism it fixes (self-draft steering) is already fully wired end-to-end (`agent/output_handler.py`); this plan changes what *triggers* it, not how it's invoked. `.claude/skills-global/weekly-review/SKILL.md` is a global skill (synced via `/update`'s hardlink mechanism) — no separate integration step needed beyond the edit itself.

## Documentation

- [ ] Update `docs/features/message-drafter.md` (or create if it doesn't yet exist — check `docs/features/README.md` index first) to document: `detect_local_file_reference` as a new validator rule, and the corrected fact that ALL violations (not just empty-promise) now trigger `needs_self_draft`. Correct any existing reference to "violations surface via the stop-hook review gate."
- [ ] Update `docs/features/agent-message-delivery.md` if it documents the violation-surfacing mechanism, to reflect that `agent/hooks/stop.py`'s review gate is dead for session_runner/eng sessions and self-draft steering is the live path (cross-reference the correction already made in `docs/plans/consolidate_delivery_paths.md`'s Freshness Check).
- [ ] No `docs/infra/` changes — no new infrastructure, dependencies, or deployment changes.

## Success Criteria

- [x] `detect_local_file_reference()` exists in `bridge/message_drafter.py` and correctly flags `/tmp/...`, `~/...`, `/Users/...`, `/home/...`, and `` `open -a ...` `` references while passing false-positive guards on ordinary prose.
- [x] `draft_message()` sets `needs_self_draft=True` whenever `_validate_for_medium()` returns a non-empty violations list, on **both** return paths — the short-output early return (`:770-781`) and the main-path return (`:801-831`) — not just on empty-promise detection (critique B1).
- [x] A SHORT terse message carrying a local path (the reported incident class, e.g. `"Done. Saved to /tmp/x.txt."`) is deferred via self-draft steering rather than delivered verbatim — verified by an explicit short-output test case.
- [x] The self-draft steering message for a `local_file_path_reference` violation includes an addendum instructing the agent to attach the file via `tools/send_message.py "<caption>" --file <path>` (critique B2), while other violation types get the base instruction unchanged.
- [x] `.claude/skills-global/weekly-review/SKILL.md` no longer instructs `open -a TextEdit <path>` as its delivery mechanism.
- [x] `bridge/message_drafter.py`'s docstring no longer references the dead `agent/hooks/stop.py` review gate as the violation-surfacing mechanism.
- [x] All Test Impact items implemented and passing.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (drafter)**
  - Name: drafter-builder
  - Role: `detect_local_file_reference`, `_validate_for_medium` wiring, `draft_message` promotion logic on BOTH return paths, `output_handler.py::_inject_self_draft_steering` violation-aware addendum, docstring fix
  - Agent Type: builder
  - Resume: true
- **Builder (skill fix)**
  - Name: skill-builder
  - Role: update `.claude/skills-global/weekly-review/SKILL.md`'s Final Step section
  - Agent Type: builder
  - Resume: true
- **Test Engineer**
  - Name: drafter-tester
  - Role: implement all Test Impact items
  - Agent Type: test-engineer
  - Resume: true
- **Documentarian**
  - Name: drafter-docs
  - Role: feature doc updates
  - Agent Type: documentarian
  - Resume: true
- **Validator (final)**
  - Name: final-validator
  - Role: run Verification table, confirm Success Criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Drafter validator + promotion logic + violation-aware steering
- **Task ID**: build-drafter
- **Depends On**: none
- **Validates**: tests/unit/test_medium_validators.py, tests/unit/test_message_drafter.py, tests/unit/test_output_handler.py
- **Assigned To**: drafter-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `detect_local_file_reference(text)` + `LOCAL_FILE_PATH_RULE` constant to `bridge/message_drafter.py`
- Wire it into `_validate_for_medium` (medium-agnostic)
- Change `draft_message()` to set `needs_self_draft=True` on any non-empty violations list at **BOTH** return paths: the short-output early return (`:770-781`) and the main-path return (`:801-831`) (critique B1)
- Extend `agent/output_handler.py::_inject_self_draft_steering` to accept the `draft` and append the attach-via-`--file` addendum when a `local_file_path_reference` violation is present; update the call site at `:435` (critique B2)
- Correct the stale docstring reference to `agent/hooks/stop.py`

### 2. Weekly-review skill fix
- **Task ID**: build-skill
- **Depends On**: none
- **Validates**: manual read-through of updated SKILL.md
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills-global/weekly-review/SKILL.md`'s "Final step" section per Technical Approach item 5

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-drafter
- **Validates**: tests/unit/test_medium_validators.py, tests/unit/test_drafter_validators.py, tests/unit/test_message_drafter.py, tests/unit/test_output_handler.py, tests/integration/test_message_drafter_integration.py
- **Assigned To**: drafter-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Implement every Test Impact item

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: drafter-docs
- **Agent Type**: documentarian
- **Parallel**: false
- All items in the Documentation section

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-skill
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; verify all Success Criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_medium_validators.py tests/unit/test_drafter_validators.py tests/unit/test_message_drafter.py tests/unit/test_output_handler.py tests/integration/test_message_drafter_integration.py -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/message_drafter.py agent/output_handler.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/message_drafter.py agent/output_handler.py` | exit code 0 |
| New validator exists | `grep -c "def detect_local_file_reference" bridge/message_drafter.py` | output > 0 |
| Promotion wired on BOTH paths | `grep -c "needs_self_draft=True" bridge/message_drafter.py` | output >= 2 |
| Violation-aware steering wired | `grep -c "send_message.py" agent/output_handler.py` | output > 0 |
| Instruction builder takes draft | `grep -c "_inject_self_draft_steering(self, session, draft" agent/output_handler.py` | output > 0 |
| Stale docstring gone | `grep -c "stop-hook review gate" bridge/message_drafter.py` | match count == 0 |
| Skill fixed | `grep -c "open -a TextEdit" .claude/skills-global/weekly-review/SKILL.md` | match count == 0 |

## Critique Results

Critique verdict (round 1): **NEEDS REVISION** — 2 blockers, 2 concerns. Revision applied 2026-07-09.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | B1 | Promotion logic covered only the main-path return (`:801-809`); the short-output early return (`:770-781`) also carries a violations list but was unpatched — so a terse message like `"Done. Saved to /tmp/x.txt."` would ship the raw local path, contradicting the plan's universal-coverage Success Criteria. | Technical Approach item 3 (now covers BOTH return paths); Data Flow step 4; Solution "Violation promotion on BOTH return paths"; Success Criteria (short-output criterion); Test Impact `test_message_drafter.py` (two cases: short + long); Verification (`>= 2` promotion sites). | The short-output path is the exact path the reported incident message class exits through — it is the primary fix target, not an edge case. |
| Blocker | B2 | `SELF_DRAFT_INSTRUCTION` (`:570-578`) is a fixed, violation-type-agnostic constant that never mentions local paths or `--file` and actively steers toward omitting details — so the issue's "attach the file as a real Telegram attachment" outcome is not actually produced; the plan's criteria stopped at "deferred via self-draft" and under-claimed. | Technical Approach item 4 (violation-aware `_inject_self_draft_steering(session, draft)` composing a base + local-file addendum in `agent/output_handler.py`); Data Flow step 6; Solution "Violation-aware self-draft instruction"; Success Criteria (attach-instruction criterion); Test Impact `test_output_handler.py` (asserts addendum presence/absence by violation type); Architectural Impact (signature change). | Base constant stays compact (`len < 1000` test still valid); the addendum is composed at injection time so it fires only for the local-path rule. |
| Concern | — | Full concern text was not retrievable on this machine (`sdlc-tool verdict get` returns no persisted state — no PM session resolved here; no issue comments recorded). Both concerns are addressed defensively by the B1/B2 revisions: promotion-path coverage, instruction efficacy, and test coverage for both the short-output path and the instruction content are now explicit. If a concern named something outside this surface, it should be re-raised at re-critique. | B1/B2 revisions + expanded Test Impact. | Noted for the critique re-run: verify no concern was silently dropped. |

---

## Resolved Decisions

The two open questions were resolved by adopting the plan's own documented defaults (both are low-risk tuning choices with graceful-degradation safety nets):

1. **weekly-review SKILL.md `open -a TextEdit` removal — no explicit `--file` guidance added.** Just remove the bad instruction; the new drafter-level `detect_local_file_reference` check is the generalized safety net. Teaching every skill explicit attachment syntax individually doesn't scale and is outside this issue's scope.
2. **`detect_local_file_reference` pattern set — ship the four-pattern v1** (`/tmp/`, `/Users/`, `/home/`, `~/`, `open -a`). This repo runs exclusively on macOS/Linux dev machines, so Windows-style paths and `file://` URIs are deferred; expand only if a future incident surfaces a gap. The self-draft steering path degrades gracefully (worst case: one extra rewrite round-trip), so an incomplete pattern set never breaks delivery.
