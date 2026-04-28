---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-28
tracking: https://github.com/tomcounsell/ai/issues/1190
last_comment_id:
---

# Bridge Steering Emoji Acks

## Problem

The Telegram bridge currently sends inline text auto-acknowledgments to users when their follow-up messages are routed into a running session as steering messages. The two strings are:

- `"Adding to current task"` — when a follow-up is merged into an in-flight session
- `"Stopping current task."` — when the follow-up matches an abort keyword

These strings are emitted by infrastructure code (`bridge/telegram_bridge.py`) — not by the PM persona — and have three concrete problems:

1. **They leak an internal primitive.** The word "task" maps to internal abstractions (`queued_steering_messages`, the steering queue, `task_list_id`). The user's mental model is a *conversation*, not a task queue.
2. **They sound like a ticketing system, not the persona.** The PM persona is "outcome-focused, in business terms." A robotic "Adding to current task" receipt breaks that voice.
3. **They violate a standing project rule.** Per durable user feedback (memory `feedback_telegram_persona_always`), every Telegram message must match the configured persona — no internal narration, no system strings ever reach the user.

**Current behavior:**

Six call sites in `bridge/telegram_bridge.py` (lines 1049, 1274, 1315, 1353, 1552, 1654) call `send_markdown(client, event.chat_id, "<text>", reply_to=message.id)` to text-acknowledge a steering or abort routing decision. The user sees an inline reply in the thread, which adds noise and breaks the persona.

**Desired outcome:**

Each of those six sites uses `set_reaction(client, event.chat_id, message.id, emoji)` instead, attaching an emoji reaction directly to the user's message. No text auto-acks, no thread pollution, no leaked vocabulary. The eventual real agent reply lands in the thread as a normal message authored by the PM session.

## Freshness Check

**Baseline commit:** `6b6307b5` (`chore: add gemma3:12b-it-qat to superseded Ollama models list`)
**Issue filed at:** 2026-04-28T08:16:52Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1274` — issue claims `ack_text = "Stopping current task." if is_abort else "Adding to current task"` — **still holds**.
- `bridge/telegram_bridge.py:1315` — issue claims same conditional — **still holds**.
- `bridge/telegram_bridge.py:1353` — issue claims same conditional — **still holds**.
- `bridge/telegram_bridge.py:1552` — issue claims `"Adding to current task"` literal — **still holds**.
- `bridge/telegram_bridge.py:1654` — issue claims `"Adding to current task"` literal — **still holds**.
- `bridge/telegram_bridge.py:1049` — **NEW SITE not in issue recon** — uses ack text `"Stopping current task." if is_abort else "Noted — I'll incorporate this on my next checkpoint."` and **already** calls `set_reaction(REACTION_RECEIVED)` on line 1058 redundantly. Plan handles this site too.
- `bridge/response.py:107` — `REACTION_RECEIVED = "👀"` — **constant already exists**, reuse instead of hardcoding `"👀"`.
- `bridge/response.py:253` — `set_reaction` signature confirmed: accepts `str | EmojiResult | None`.
- `bridge/update.py:14, :98, :171` — precedent `set_reaction` calls confirmed (👀 for `/update`, 🔥 for `/update --force`), wrapped in `try/except: pass`.

**Cited sibling issues/PRs re-checked:**
- #678 (REACT emoji leaks as literal text) — closed, foundational fix to reaction infrastructure.
- #690 (Premium custom emoji support) — closed, added custom-emoji path to `set_reaction`.
- #911 (Conversation terminus detection) — closed, taught the agent to choose RESPOND/REACT/SILENT at terminus.
- #975 (Upgrade terminal reaction emoji) — closed, raised default reaction quality.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none. Existing plans `consolidate-steering-docs.md`, `emoji-embedding-reactions.md`, `parent-child-steering.md`, and `summarizer-fallback-steering.md` are all closed/historical and do not touch the bridge ack strings.

**Notes:** The drift here is *additive*: one extra call site was missed by the issue's recon. The plan covers all six sites. Issue-text language about "five sites" should not be taken as a scope cap — the principle (no text acks for steering decisions) applies to all six.

## Prior Art

- **#678**: REACT emoji leaks as literal text — fixed the underlying reaction infrastructure that this plan depends on.
- **#690 / PR #691**: Premium custom emoji for reactions — extended `set_reaction` to handle `EmojiResult` objects with custom-emoji document IDs and standard-emoji fallback.
- **#911 / PR #969**: Conversation terminus detection — taught the agent to choose between reply and reaction at the *agent layer*. This issue extends the same reaction-over-text pattern to the *bridge layer*.
- **#975 / PR #992**: Terminal reactions via `find_best_emoji` — defined `REACTION_RECEIVED`, `REACTION_PROCESSING`, and other constants in `bridge/response.py`.
- **PR #602**: Agent-controlled message delivery — earlier work on routing decisions for user-facing messages.

None of these directly addressed bridge-side steering acks; that gap is exactly what #1190 fills.

## Research

No relevant external findings — proceeding with codebase context. This is a purely internal refactor of bridge auto-ack behavior.

## Data Flow

This change touches only the *outbound ack* side of the steering data flow. The flow is unchanged otherwise:

1. **Entry point**: User sends a follow-up Telegram message into a chat with a running session.
2. **Routing decision** (`bridge/telegram_bridge.py`): bridge inspects the message + session state and chooses one of six steering paths (semantic-routing-active, reply-to-running, reply-to-pending, reply-to-completed-with-live-guard, in-memory-coalescing-guard, intake-classifier-interjection).
3. **Steering enqueue** (`agent.steering.push_steering_message`): unchanged — still queues the text into the session's steering inbox.
4. **Ack to user** (THIS CHANGE): instead of `send_markdown(...)` with a text string, call `set_reaction(client, chat_id, message.id, emoji)` to attach an emoji reaction to the user's original message.
5. **Worker pickup**: unchanged — the worker drains the steering queue at the next tool boundary and the agent eventually replies as a normal message.
6. **Internal observability**: unchanged — `logger.info(...)` lines at each site stay exactly as written.

## Architectural Impact

- **New dependencies**: none. `set_reaction` is already used elsewhere in `telegram_bridge.py` (lines 1058, 1133, 1235, 2077, 2106, 2126).
- **Interface changes**: none. The bridge's external contract (Telegram message handlers) is unchanged.
- **Coupling**: slightly *decreases* coupling — removes six hardcoded user-facing string literals from infrastructure code, leaving only logger lines (internal observability) behind.
- **Data ownership**: unchanged. The PM persona owns user-facing utterances; this change removes the bridge's overreach into that domain.
- **Reversibility**: trivial. The six edits are localized and the precedent helper (`set_reaction`) already exists. Reverting is a single-commit rollback.
- **New constant**: `bridge/response.py` gains `REACTION_ABORT = "🫡"` (salute) to parallel the existing `REACTION_RECEIVED = "👀"`. The constant pattern is already established (lines 107-108). Emoji choice rationale: bot-emitted reactions speak in the *reactor's* voice, not directive-voice. 👀 reads as "I see / noted" from the reactor; 🫡 reads as "understood, standing down." Both work as first-person statements regardless of who reacts. Earlier candidate 🛑 was rejected because, applied to a user's message, it parses as a directive aimed at the author ("*you* stop") rather than a self-report. See memory `feedback_reactor_voice_emoji.md`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified)
- Review rounds: 1 (standard `/do-pr-review`)

This is a mechanical six-site replacement using an already-imported helper and an already-defined constant. The abort emoji choice (🫡) is resolved — see Architectural Impact for the reactor-voice rationale.

## Prerequisites

No prerequisites — the helper, the standard reaction emojis, and the import path are all already in place.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `set_reaction` already imported in `telegram_bridge.py` | `grep -n 'from bridge.response import' bridge/telegram_bridge.py \| grep -E 'set_reaction\|REACTION_RECEIVED'` | Confirm import path |
| `REACTION_RECEIVED` constant exists | `grep -n 'REACTION_RECEIVED' bridge/response.py` | Reuse over hardcoding |

## Solution

### Key Elements

- **`REACTION_ABORT` constant** (new): added to `bridge/response.py` alongside `REACTION_RECEIVED`. Value: `"🫡"`.
- **`set_reaction` calls** at each of the six sites: replace the `send_markdown(...)` ack call with `await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)` (or `REACTION_ABORT` for abort branches).
- **Defensive wrapping**: each `set_reaction` call wrapped in `try/except: pass` per the precedent at `bridge/update.py:98-100`. Reaction failures (non-Premium accounts, restricted chats, deleted messages) must never break the steering path.
- **Logger lines preserved**: every existing `logger.info(...)` line at each site stays exactly as written. Internal observability is untouched.
- **Removed imports**: where the only reason a site imported `bridge.markdown.send_markdown` was for the ack, remove the import. (Several sites use `from bridge.markdown import send_markdown` inline immediately before the ack — these become dead and should be removed.)

### Flow

User sends follow-up message → Bridge routes it via one of the six steering paths → `push_steering_message(...)` queues the text into the session's steering inbox → **`set_reaction(client, chat_id, msg_id, REACTION_RECEIVED)` attaches 👀 to the user's message** → User sees the reaction (no inline text reply) → Worker drains the steering queue at the next tool boundary → Agent eventually responds as a normal message authored by the PM persona.

### Technical Approach

- **Single file edit**: `bridge/telegram_bridge.py` for the six ack replacements.
- **Tiny additive edit**: `bridge/response.py` to define `REACTION_ABORT = "🫡"`.
- **Reuse the existing import line** in `bridge/telegram_bridge.py:122` that already imports `REACTION_RECEIVED` — extend to `REACTION_ABORT`.
- **Per-site behavior:**

| Line | Branch type | Replacement |
|------|-------------|-------------|
| 1049 | semantic-routing active session | Drop `ack_text = ...` and `send_markdown(...)` block. The line 1058 `set_reaction(REACTION_RECEIVED)` already exists — change it to use `REACTION_ABORT if is_abort else REACTION_RECEIVED`. |
| 1274 | reply-to running/active session | Replace `send_markdown(...)` with `await set_reaction(client, event.chat_id, message.id, REACTION_ABORT if is_abort else REACTION_RECEIVED)`. |
| 1315 | reply-to pending session within merge window | Same pattern as 1274. |
| 1353 | reply-to completed session with live guard | Same pattern as 1274. |
| 1552 | in-memory coalescing guard | `await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)` (no abort branch — coalescing is always a steer). |
| 1654 | intake-classifier interjection | `await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)` (no abort branch — interjection routing doesn't pass abort flag). |

- **Failure containment**: every `set_reaction` call wrapped in `try/except Exception: pass` (matching `bridge/update.py:98-100` precedent). Reactions are best-effort; they must not break steering.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks added without a corresponding `logger.debug(...)` line. The `try/except` wrapping each `set_reaction` call may use `pass` (matching `bridge/update.py:98-100`) — `set_reaction` itself already logs at debug level on failure (`bridge/response.py:286, :299, :315`), so a bare `pass` at the call site is consistent with project precedent.
- [ ] Existing `logger.info(...)` lines at each of the six sites are preserved verbatim — verified post-edit via grep.

### Empty/Invalid Input Handling
- [ ] `set_reaction` already handles None, empty strings, and invalid emoji types defensively (`bridge/response.py:280-290`). No new edge-case tests required.
- [ ] Steering enqueue (`push_steering_message`) is unchanged — its existing input handling is unaffected.

### Error State Rendering
- [ ] If `set_reaction` fails (non-Premium account, restricted chat, deleted source message), the user sees **no acknowledgment at all**. This is acceptable: the eventual agent reply will land normally, and the user retains evidence (their own outgoing message + the agent's eventual response). The previous text-ack behavior had the same fallback property — `send_markdown` could fail too.
- [ ] No silent infinite loop possible — the steering enqueue is idempotent and the reaction is one-shot per message.

## Test Impact

No existing tests affected — repo-wide grep `grep -rn "Adding to current task\|Stopping current task" tests/` returns zero matches. The literal strings being removed are not asserted on anywhere in the test suite. Existing infrastructure tests at `tests/unit/test_emoji_embedding.py` and `tests/integration/test_reply_delivery.py` cover `set_reaction` already.

A new unit test asserting that the six call sites use `set_reaction` and not `send_markdown` would be valuable but is *not strictly required* — the success criteria's repo-wide grep covers regression detection mechanically.

## Rabbit Holes

- **Don't move the ack into the PM session.** Architecturally cleaner (the PM owns all user-facing utterances), but adds 5–30s of latency before the user sees acknowledgment. The issue explicitly defers this to a separate piece of work.
- **Don't generalize a "forbidden vocabulary" persona-doc rule.** Separate persona-edit issue. In scope here is the six call sites only.
- **Don't refactor the six steering paths.** The routing logic is correct; only the ack-emission line at each terminal is wrong. Resist the urge to consolidate the six paths into a helper — they have subtly different preconditions and the consolidation has been deferred multiple times for good reasons.
- **Don't expand the abort emoji palette.** Pick one (🫡) and use it consistently across all abort sites. Visual consistency matters more than per-site nuance.
- **Don't update the historical plan `docs/plans/rapid_fire_coalescing_fix.md`** that mentions the old ack text — it's a record of past work, not active spec.

## Risks

### Risk 1: Telegram reactions silently fail on non-Premium accounts in some chats
**Impact:** Some users may receive no visible acknowledgment of their steering message.
**Mitigation:** `set_reaction` already returns `False` and logs at debug level on failure (`bridge/response.py:315`). The eventual agent reply still lands normally, and the user retains the visual evidence of their own outgoing message. This is the same failure mode as the old `send_markdown` calls — those could fail too.

### Risk 2: An emoji choice (🫡) might render differently across Telegram clients
**Impact:** Visual inconsistency for the abort acknowledgment.
**Mitigation:** 🫡 (U+1FAE1, "saluting face") is a standard Unicode 14.0 emoji (Sept 2021) with broad client support across modern Telegram desktop, mobile, and web clients. The existing `REACTION_RECEIVED = "👀"` precedent works across clients; 🫡 is in the same compatibility tier. If field testing reveals issues on older clients, the constant is one-line-changeable.

### Risk 3: The sixth call site (line 1049) has different existing semantics
**Impact:** Removing the text ack at line 1049 changes the user-visible behavior more than at the other sites — the old text was different ("Noted — I'll incorporate this on my next checkpoint." instead of "Adding to current task").
**Mitigation:** This is *more* aligned with the issue's intent, not less. The line 1049 site already calls `set_reaction(REACTION_RECEIVED)` on line 1058, so there is currently *both* a text ack *and* a reaction — clearly redundant. Removing the text and keeping the reaction is the correct cleanup. The changed text was itself a pre-existing inconsistency.

## Race Conditions

No new race conditions introduced. The existing `await record_telegram_message_handled(event.chat_id, message.id)` and `return` calls following each ack remain in the same order — the only change is the ack emission style. The reaction call is awaited synchronously like the original `send_markdown`, so message-handling sequencing is unchanged.

## No-Gos (Out of Scope)

- Moving ack-emission into the PM session (deferred — see issue #1190 "Out of scope" section).
- Persona-doc rule banning forbidden vocabulary (separate persona-edit issue).
- Refactoring the six steering paths into a unified helper (long-deferred for good reasons).
- Updating historical plan files in `docs/plans/` that reference the old strings (those are records, not active spec).
- Adding telemetry/metrics for reaction success rates (`set_reaction` already logs failures at debug level).
- Migrating other bridge auto-acks (e.g., `/update` already uses reactions; this issue is specifically about steering acks).

## Update System

No update system changes required — this is a purely internal bridge refactor. No new dependencies, no new config, no new env vars. After merge, `scripts/remote-update.sh` on each machine pulls the change and the bridge restarts as part of its normal flow.

## Agent Integration

No agent integration changes required. This change is entirely on the bridge's *outbound* path (how the bridge acknowledges incoming user messages). The agent-side message authoring, MCP servers, and `.mcp.json` are all unaffected. The PM persona will continue to author all real reply messages exactly as before — the bridge simply stops emitting its own competing text acks.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/mid-session-steering.md` line ~21 — replace the description of step 7 from "bridge replies 'Adding to current task'..." to "bridge attaches a 👀 (or 🫡 for abort) emoji reaction to the user's message". Keep the rest of the steering flow description intact.
- [ ] Update `docs/features/intake-classifier.md` — fix the diagram comment around line 28 (`(ack: "Adding to current task")`) to reference the emoji reaction instead.
- [ ] Update `docs/features/semantic-session-routing.md` line ~84 — replace the user-facing acknowledgment description from quoted text strings to "an emoji reaction (👀 for steer, 🫡 for abort)".
- [ ] Update `docs/features/steering-implementation-spec.md` lines ~52, ~60, ~103 — replace text-ack references with emoji-reaction references. Note: line 103 contains a code snippet showing `await client.send_message(event.chat_id, "Adding to current task", ...)` — replace with the new `set_reaction` pattern.

### Inline Documentation
- [ ] Add a brief comment above the new `REACTION_ABORT` constant in `bridge/response.py` explaining the parallel to `REACTION_RECEIVED` and the abort-keyword trigger.

The historical plan `docs/plans/rapid_fire_coalescing_fix.md` is **not** updated — it's a record of past work.

## Success Criteria

- [ ] All six call sites in `bridge/telegram_bridge.py` (lines 1049, 1274, 1315, 1353, 1552, 1654 at plan time) use `set_reaction(...)` instead of `send_markdown(...)` for the steering/abort ack.
- [ ] `REACTION_RECEIVED` (existing constant) used for steer acks; `REACTION_ABORT` (new constant in `bridge/response.py`) used for abort acks.
- [ ] Each `set_reaction` call wrapped in `try/except Exception: pass` per the `bridge/update.py:98-100` precedent.
- [ ] All existing `logger.info(...)` lines at each site preserved verbatim.
- [ ] Inline `from bridge.markdown import send_markdown` imports that exist solely to support the removed ack are deleted.
- [ ] Repo-wide grep returns zero matches in `.py` files: `grep -rn "Adding to current task\|Stopping current task" --include="*.py" .`
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — the four `docs/features/*.md` files listed above.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` exit clean.
- [ ] Manual verification (post-merge, opportunistic): send a steering follow-up to a running agent session; confirm the user's message gets a 👀 reaction and no text reply lands in the thread; send `stop` and confirm 🫡.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (bridge-edit)**
  - Name: `bridge-ack-builder`
  - Role: Apply the six site replacements in `bridge/telegram_bridge.py`, add `REACTION_ABORT` to `bridge/response.py`, and clean up the now-dead `send_markdown` imports.
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-edit)**
  - Name: `bridge-ack-validator`
  - Role: Verify all six sites are replaced, the new constant is wired correctly, no `send_markdown` literal-string ack calls remain, and the success-criteria grep returns zero matches.
  - Agent Type: validator
  - Resume: true

- **Documentarian (steering-docs)**
  - Name: `steering-docs-writer`
  - Role: Update the four `docs/features/*.md` files listed in the Documentation section to reflect the emoji-reaction acknowledgment pattern.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

#### 1. Add `REACTION_ABORT` constant
- **Task ID**: build-constant
- **Depends On**: none
- **Validates**: `grep -n "REACTION_ABORT" bridge/response.py` returns one match
- **Assigned To**: bridge-ack-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `REACTION_ABORT = "🫡"  # Steering abort acknowledged` immediately below `REACTION_PROCESSING` in `bridge/response.py:108`.
- Keep the same comment style as the surrounding constants.

#### 2. Replace the six ack call sites
- **Task ID**: build-acks
- **Depends On**: build-constant
- **Validates**: `grep -rn "Adding to current task\|Stopping current task" --include="*.py" .` returns zero matches; `grep -n "set_reaction" bridge/telegram_bridge.py` increases by approximately five (one site already had a reaction).
- **Assigned To**: bridge-ack-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend the existing import on `bridge/telegram_bridge.py:122` to include `REACTION_ABORT` alongside `REACTION_RECEIVED`.
- For each of the six sites listed in the Solution table, replace the `send_markdown(...)` ack call with the appropriate `set_reaction(...)` call wrapped in `try/except Exception: pass`.
- For line 1049: drop the `ack_text = ...` block and the `send_markdown(...)` call entirely; change the existing line 1058 `set_reaction(REACTION_RECEIVED)` to `set_reaction(REACTION_ABORT if is_abort else REACTION_RECEIVED)`.
- Remove now-dead `from bridge.markdown import send_markdown` inline imports at sites where they are no longer used.
- Preserve every `logger.info(...)` line verbatim at each site.

#### 3. Validate the build
- **Task ID**: validate-acks
- **Depends On**: build-acks
- **Assigned To**: bridge-ack-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the success-criteria grep (zero matches expected).
- Confirm `bridge/telegram_bridge.py` line count change is small and localized (no incidental refactor).
- Confirm `logger.info` line count is unchanged.
- Run `python -m ruff check bridge/telegram_bridge.py bridge/response.py` and `python -m ruff format --check bridge/telegram_bridge.py bridge/response.py`.
- Run `pytest tests/unit/ -x -q` and verify no regressions.

#### 4. Update feature docs
- **Task ID**: document-feature
- **Depends On**: validate-acks
- **Assigned To**: steering-docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update the four files listed in the Documentation section.
- Verify each updated doc still reads coherently end-to-end.

#### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bridge-ack-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table below.
- Confirm Success Criteria checklist is satisfied.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No literal ack strings remain in `.py` | `grep -rn "Adding to current task\|Stopping current task" --include="*.py" .` | exit code 1 (no matches) |
| `REACTION_ABORT` constant defined | `grep -n "^REACTION_ABORT" bridge/response.py` | output contains `REACTION_ABORT` |
| Bridge imports both reaction constants | `grep -n "REACTION_RECEIVED\|REACTION_ABORT" bridge/telegram_bridge.py` | output contains both names |
| Lint clean | `python -m ruff check bridge/` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Resolved Questions

1. **Abort emoji choice — RESOLVED: 🫡 (salute).** Initial proposal of 🛑 was rejected: bot-emitted reactions speak in the *reactor's* voice, and 🛑 applied to a user's message reads as a directive at the author ("*you* stop") rather than a self-report. 🫡 ("understood, standing down") is unambiguously first-person from the reactor and matches the PM persona's calm acknowledgment voice. Per Valor's framing: "a salute means I understand and there's nothing left to say." See memory `feedback_reactor_voice_emoji.md` for the general principle.
2. **Line 1049 special treatment — RESOLVED: consolidate.** Drop the redundant text ack ("Noted — I'll incorporate this on my next checkpoint."), keep the existing `set_reaction(REACTION_RECEIVED)` call, and extend it to be abort-aware (`REACTION_ABORT if is_abort else REACTION_RECEIVED`). This removes the only site that previously emitted both a text and a reaction.
