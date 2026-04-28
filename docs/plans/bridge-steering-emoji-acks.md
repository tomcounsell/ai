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
- **Coupling**: *decreases*. Removes six hardcoded user-facing string literals from infrastructure code AND collapses six duplicated terminal sequences (import → is_abort → push → ack → log → record-handled → return) into one helper call.
- **Data ownership**: unchanged. The PM persona owns user-facing utterances; this change removes the bridge's overreach into that domain.
- **Reversibility**: trivial. Helper-extraction is mechanically reversible; constant addition is one line.
- **New constant**: `bridge/response.py` gains `REACTION_ABORT = "🫡"` (salute) to parallel the existing `REACTION_RECEIVED = "👀"`. The constant pattern is already established (lines 107-108). Emoji choice rationale: bot-emitted reactions speak in the *reactor's* voice, not directive-voice. 👀 reads as "I see / noted" from the reactor; 🫡 reads as "understood, standing down." Both work as first-person statements regardless of who reacts. Earlier candidate 🛑 was rejected because, applied to a user's message, it parses as a directive aimed at the author ("*you* stop") rather than a self-report. See memory `feedback_reactor_voice_emoji.md`.
- **New helper**: `bridge/telegram_bridge.py` gains a private async helper `_ack_steering_routed(client, event, message, *, session_id, sender_name, text, log_context)` that bundles the entire terminal sequence shared by all six routing branches: `is_abort` detection, `push_steering_message`, emoji reaction (with defensive `try/except`), `logger.info(...)`, and `record_telegram_message_handled`. Each of the six routing branches collapses to a single helper call + `return`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified)
- Review rounds: 1 (standard `/do-pr-review`)

Two-part change: (1) extract a shared `_ack_steering_routed` helper in `bridge/telegram_bridge.py` that bundles the duplicated terminal sequence; (2) collapse all six routing branches to call the helper. Net diff target: **PR removes more lines than it adds.** The abort emoji choice (🫡) is resolved — see Architectural Impact for the reactor-voice rationale.

## Prerequisites

No prerequisites — the helper, the standard reaction emojis, and the import path are all already in place.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `set_reaction` already imported in `telegram_bridge.py` | `grep -n 'from bridge.response import' bridge/telegram_bridge.py \| grep -E 'set_reaction\|REACTION_RECEIVED'` | Confirm import path |
| `REACTION_RECEIVED` constant exists | `grep -n 'REACTION_RECEIVED' bridge/response.py` | Reuse over hardcoding |

## Solution

### Key Elements

- **`REACTION_ABORT` constant** (new): added to `bridge/response.py` alongside `REACTION_RECEIVED`. Value: `"🫡"`.
- **`_ack_steering_routed` helper** (new, private): a single async function in `bridge/telegram_bridge.py` that owns the entire terminal sequence shared across the six routing branches. Signature:

```python
async def _ack_steering_routed(
    client: TelegramClient,
    event,
    message,
    *,
    session_id: str,
    sender_name: str,
    text: str,
    log_context: str,
    agent_session: "AgentSession | None" = None,
) -> None:
    """Push a steering message, react to ack, log, and mark handled.

    Bundles the terminal sequence shared by every steering routing branch
    in handle_message. Caller is responsible for `return` after this call.

    If `agent_session` is provided, also writes to the AgentSession Popoto
    model's `queued_steering_messages` field (durable, PM-visible) before
    the Redis push. Two of the six routing branches hold the AgentSession
    object in hand and need the dual write so the PM session sees the
    steering message even before the worker drains the Redis queue. The
    other four branches push by session_id only.
    """
    is_abort = text.strip().lower() in ABORT_KEYWORDS
    if agent_session is not None:
        agent_session.push_steering_message(text)  # durable, PM-visible
    push_steering_message(session_id, text, sender_name, is_abort=is_abort)
    try:
        await set_reaction(
            client,
            event.chat_id,
            message.id,
            REACTION_ABORT if is_abort else REACTION_RECEIVED,
        )
    except Exception:
        pass
    action = "abort" if is_abort else "steer"
    logger.info(f"{log_context} ({action})")
    await record_telegram_message_handled(event.chat_id, message.id)
```

- **Six routing branches collapse** to a single helper call + `return`. The unique per-site information is the routing branch's `log_context` string, plus — at the two sites that already hold the `AgentSession` object — an `agent_session=` argument so the helper preserves the dual push.
- **Dual-push sites**: lines 1535 (in-memory coalescing guard, has `guard_session`) and 1648 (intake-classifier interjection, has `fresh_session`) currently perform a dual push: first to `<session>.push_steering_message(text)` (durable, PM-visible via the Popoto model's `queued_steering_messages` field) and then to the Redis steering queue. The helper's optional `agent_session` parameter preserves this behavior. The other four branches push by `session_id` only — they do not hold the AgentSession object and do not need the dual write.
- **Hoisted imports**: `ABORT_KEYWORDS`, `push_steering_message`, and `record_telegram_message_handled` move to module-level imports (most are already partially imported there). Inline `from agent.steering import ...` and `from bridge.markdown import send_markdown` clutter at each site is deleted.
- **`send_markdown` import**: dead at all six sites after the change. Verify no other code in this file's steering paths still depends on it before removing the top-of-file import — but the inline imports inside each branch are unambiguously dead and go away.
- **Defensive wrapping**: the `try/except: pass` lives once, inside the helper — not duplicated at six sites. Matches `bridge/update.py:98-100` precedent.

### Flow

User sends follow-up message → Bridge routes it via one of the six steering paths → `push_steering_message(...)` queues the text into the session's steering inbox → **`set_reaction(client, chat_id, msg_id, REACTION_RECEIVED)` attaches 👀 to the user's message** → User sees the reaction (no inline text reply) → Worker drains the steering queue at the next tool boundary → Agent eventually responds as a normal message authored by the PM persona.

### Technical Approach

- **Two files touched**: `bridge/response.py` (one-line constant addition) and `bridge/telegram_bridge.py` (helper definition + six site collapses + import hoisting).
- **Reuse the existing import line** in `bridge/telegram_bridge.py:122` that already imports `REACTION_RECEIVED` — extend to `REACTION_ABORT`.
- **Hoist module-level imports**: `ABORT_KEYWORDS` and `push_steering_message` from `agent.steering`. `record_telegram_message_handled` is already at module level (line 107).
- **Define `_ack_steering_routed`** near the top of `telegram_bridge.py` (after the existing helper definitions, before `handle_message`).
- **Collapse each of the six routing branches.** Every branch becomes shape:

```python
await _ack_steering_routed(
    client, event, message,
    session_id=<session_id>,
    sender_name=sender_name,
    text=clean_text,
    log_context=f"<branch-specific log string>",
)
return
```

- **Per-site `log_context` strings** preserve the existing semantics from each branch's `logger.info(...)` line (project name, branch type, session id, age, confidence, etc.). The `(action)` suffix `(abort|steer)` is appended by the helper, so the caller's `log_context` should NOT include it.
- **Pass `agent_session=` at the two dual-push sites:**
  - Line 1535 branch: `agent_session=guard_session`
  - Line 1648 branch: `agent_session=fresh_session`
- **Branches without an `is_abort` distinction** (lines 1552 and 1654 — coalescing guard and intake-classifier interjection) become abort-aware *for free* via the helper. This is a behavior change in those branches: previously a user sending "stop" via these routes saw the same generic "Adding to current task" text and would have to wait for the agent to honor the abort. Now they see 🫡 and `is_abort=True` is passed through to `push_steering_message`, which is the more correct behavior. This unification is part of "all six sites act the same."
- **Dead inline imports removed**: `from agent.steering import ABORT_KEYWORDS, push_steering_message` and `from bridge.markdown import send_markdown` blocks inside each branch all delete.
- **Net-line accounting**: each collapsed site goes from ~25 lines (imports + is_abort + push + send_markdown wrapper + log + record + return) to ~9 lines (one helper call + return + branch-detection condition). Six sites × 16 lines saved ≈ 96 lines removed. New helper is ~25 lines. Net target: **~−70 lines**, well above the "remove more than added" bar.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The single `try/except Exception: pass` lives inside `_ack_steering_routed` (matching `bridge/update.py:98-100`) — not duplicated at six sites. `set_reaction` itself already logs at debug level on failure (`bridge/response.py:286, :299, :315`), so a bare `pass` at the helper boundary is consistent with project precedent.
- [ ] Each routing branch emits its `logger.info(...)` via the helper's `log_context` parameter; the helper appends the `(action)` suffix. Branch-specific log content (project name, session id, age, confidence) is preserved by the per-site `log_context` strings — verified post-edit by grepping for each unique log fragment.

### Empty/Invalid Input Handling
- [ ] `set_reaction` already handles None, empty strings, and invalid emoji types defensively (`bridge/response.py:280-290`). No new edge-case tests required.
- [ ] Steering enqueue (`push_steering_message`) is unchanged — its existing input handling is unaffected.

### Error State Rendering
- [ ] If `set_reaction` fails (non-Premium account, restricted chat, deleted source message), the user sees **no acknowledgment at all**. This is acceptable: the eventual agent reply will land normally, and the user retains evidence (their own outgoing message + the agent's eventual response). The previous text-ack behavior had the same fallback property — `send_markdown` could fail too.
- [ ] No silent infinite loop possible — the steering enqueue is idempotent and the reaction is one-shot per message.

## Test Impact

No existing tests affected — repo-wide grep `grep -rn "Adding to current task\|Stopping current task" tests/` returns zero matches. The literal strings being removed are not asserted on anywhere in the test suite. Existing infrastructure tests at `tests/unit/test_emoji_embedding.py` and `tests/integration/test_reply_delivery.py` cover `set_reaction` already.

**New unit test (light, optional but recommended):** A small test for `_ack_steering_routed` that exercises both the steer and abort branches, verifies `push_steering_message` is called with the correct `is_abort`, and verifies the right reaction emoji is emitted. The helper centralizes behavior previously spread across six sites — testing it once is high-leverage. Existing routing-decision tests cover the upstream branches; no new tests are needed for the call sites themselves.

## Rabbit Holes

- **Don't move the ack into the PM session.** Architecturally cleaner (the PM owns all user-facing utterances), but adds 5–30s of latency before the user sees acknowledgment. The issue explicitly defers this to a separate piece of work.
- **Don't generalize a "forbidden vocabulary" persona-doc rule.** Separate persona-edit issue. In scope here is the six call sites only.
- **Don't refactor the six steering routing decisions.** The *routing logic* (how the bridge picks which session to steer into across semantic-routing, reply-to-running, reply-to-pending, reply-to-completed-with-live-guard, in-memory-coalescing, intake-classifier-interjection) has subtly different preconditions per branch and stays untouched. The *terminal sequence* (push + ack + log + record + return) IS uniform across all six and IS in scope to consolidate via `_ack_steering_routed` — that's the point of the audit-and-condense scope.
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

### Risk 4: Helper extraction loses subtle per-site behavior
**Impact:** Six routing branches each evolved independently — there may be subtle per-site behavior (logger formatting, edge-case handling, ordering) that a uniform helper would erase.
**Mitigation:** The helper takes a `log_context` parameter, so every branch's distinctive log content survives verbatim. The other terminal-sequence elements (`is_abort` detection, `push_steering_message` call, ack, `record_telegram_message_handled`) are byte-identical across the six sites today (verified by reading each site). The helper extraction is a true no-op refactor on the steer path; the only behavior delta is the abort-awareness gained at sites 1552 and 1654, which is desirable per "all six act the same."

### Risk 5: Behavior change for abort at sites 1552 / 1654
**Impact:** Two routing branches that previously did not detect abort keywords now will. A user sending "stop" via the in-memory coalescing path or intake-classifier interjection path will see 🫡 instead of 👀, and `push_steering_message` will receive `is_abort=True`, which the agent honors as an explicit cancel signal.
**Mitigation:** This is the explicitly desired behavior change ("all six sites act the same"). Document it in the PR description and the docs cascade. Existing tests do not assert on the prior behavior at these sites, so no breakage. If the unification proves wrong in field testing, it's a one-line revert in the helper or branch.

### Risk 6: Helper silently drops the durable PM-visible write at dual-push sites (CRITIQUE B1)
**Impact:** Sites 1535 and 1648 currently perform a dual push — first `<session>.push_steering_message(text)` (writes to the AgentSession Popoto model's `queued_steering_messages` field, durable and PM-visible) and then the Redis push. An earlier draft of the helper handled only the Redis push, so naively adopting it at those two sites would silently drop the durable write — the PM session would stop seeing those steering messages on pickup.
**Mitigation:** Helper signature explicitly takes `agent_session: AgentSession | None = None`. When provided, the helper writes via `agent_session.push_steering_message(text)` *before* the Redis push, preserving exact prior behavior. Plan calls out the two specific sites that must pass this argument. Validator step asserts the dual-push presence by counting `agent_session=` keyword arguments across the collapsed branches (must be exactly 2).

## Race Conditions

No new race conditions introduced. The existing `await record_telegram_message_handled(event.chat_id, message.id)` and `return` calls following each ack remain in the same order — the only change is the ack emission style. The reaction call is awaited synchronously like the original `send_markdown`, so message-handling sequencing is unchanged.

## No-Gos (Out of Scope)

- Moving ack-emission into the PM session (deferred — see issue #1190 "Out of scope" section).
- Persona-doc rule banning forbidden vocabulary (separate persona-edit issue).
- Refactoring the six steering *routing decisions* (the per-branch logic that picks the target session). The terminal *acknowledgment sequence* IS in scope; the routing logic stays as-is.
- Updating historical plan files in `docs/plans/` that reference the old strings (those are records, not active spec).
- Adding telemetry/metrics for reaction success rates (`set_reaction` already logs failures at debug level).
- Migrating other bridge auto-acks (e.g., `/update` already uses reactions; this issue is specifically about steering acks).
- Touching the message-edit handling at lines 2066–2101 — those use `push_steering_message` for a different reason (handling Telegram message edits) and are out of scope here.

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

- [ ] **Dual-push preserved at sites 1535 and 1648**: each of those two helper calls passes `agent_session=guard_session` (or `agent_session=fresh_session`) so the durable PM-visible write via `AgentSession.push_steering_message(text)` continues to occur before the Redis push. Verify by reading the collapsed branches and confirming the `agent_session=` argument is present at exactly two of the six call sites.
- [ ] **Net-negative diff**: `git diff --shortstat main...session/bridge-steering-emoji-acks -- bridge/` reports more lines removed than inserted. PR shrinks `bridge/telegram_bridge.py` net.
- [ ] `_ack_steering_routed` helper exists in `bridge/telegram_bridge.py` and is the *only* code path that emits steering acks.
- [ ] All six routing branches in `handle_message` collapse to a single helper call + `return` (plus their per-branch `log_context` string).
- [ ] `REACTION_RECEIVED` (existing constant) used for steer acks; `REACTION_ABORT` (new constant in `bridge/response.py`) used for abort acks. Both selected inside the helper, not at the call sites.
- [ ] The single `try/except Exception: pass` lives inside the helper, not at the call sites.
- [ ] Inline `from bridge.markdown import send_markdown` and `from agent.steering import ABORT_KEYWORDS, push_steering_message` blocks inside `handle_message` are all deleted; module-level imports cover what the helper needs.
- [ ] Repo-wide grep returns zero matches in `.py` files: `grep -rn "Adding to current task\|Stopping current task" --include="*.py" .`
- [ ] Per-branch log content preserved: for each of the six original `logger.info(...)` strings, a `grep -rn "<distinctive fragment>" bridge/telegram_bridge.py` still finds a match (now inside the `log_context` argument).
- [ ] Tests pass (`/do-test`). New unit test for `_ack_steering_routed` (steer + abort branches) is recommended and added if practical.
- [ ] Documentation updated (`/do-docs`) — the four `docs/features/*.md` files listed above.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` exit clean.
- [ ] Manual verification (post-merge, opportunistic): send a steering follow-up to a running agent session; confirm the user's message gets a 👀 reaction and no text reply lands in the thread; send `stop` and confirm 🫡.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (bridge-edit)**
  - Name: `bridge-ack-builder`
  - Role: Add `REACTION_ABORT` to `bridge/response.py`, add `_ack_steering_routed` helper to `bridge/telegram_bridge.py`, collapse all six routing branches to call the helper, hoist module-level imports, and delete dead inline imports.
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-edit)**
  - Name: `bridge-ack-validator`
  - Role: Verify the helper exists and is the only ack code path, all six sites are collapsed to helper calls, the new constant is wired correctly, branch-specific log content is preserved verbatim, no `send_markdown` literal-string ack calls remain, the success-criteria grep returns zero matches, and the diff is net-negative.
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

#### 2. Add `_ack_steering_routed` helper
- **Task ID**: build-helper
- **Depends On**: build-constant
- **Validates**: `grep -n "_ack_steering_routed" bridge/telegram_bridge.py` returns at least one match (definition).
- **Assigned To**: bridge-ack-builder
- **Agent Type**: builder
- **Parallel**: false
- Hoist these to module-level imports in `bridge/telegram_bridge.py`: `ABORT_KEYWORDS` and `push_steering_message` from `agent.steering`. Extend the existing `from bridge.response import (...)` to include `REACTION_ABORT` alongside `REACTION_RECEIVED`.
- Define `_ack_steering_routed` per the signature in Solution → Key Elements. Place it after the existing helper definitions, before `handle_message`.
- Include a one-line docstring noting the helper bundles the steering terminal sequence.

#### 3. Collapse the six routing branches
- **Task ID**: build-collapse
- **Depends On**: build-helper
- **Validates**: `grep -rn "Adding to current task\|Stopping current task" --include="*.py" .` returns zero matches. `grep -c "_ack_steering_routed" bridge/telegram_bridge.py` is at least 7 (one definition + six call sites).
- **Assigned To**: bridge-ack-builder
- **Agent Type**: builder
- **Parallel**: false
- For each of the six routing branches (lines 1049, 1274, 1315, 1353, 1552, 1654 at plan time), replace the entire terminal block (inline imports + `is_abort` detection + `push_steering_message` + ack + `logger.info` + `record_telegram_message_handled`) with a single `await _ack_steering_routed(...)` call followed by `return`.
- Build the per-site `log_context` string from each branch's existing `logger.info(...)` content, dropping any trailing `(steer|abort)` text since the helper appends it.
- Delete the now-dead inline imports at each site: `from agent.steering import ABORT_KEYWORDS, push_steering_message` and `from bridge.markdown import send_markdown`.
- Preserve all upstream branch logic (the routing decisions that lead to each terminal block) — only the terminal block is consolidated.

#### 4. Validate the build
- **Task ID**: validate-acks
- **Depends On**: build-collapse
- **Assigned To**: bridge-ack-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the success-criteria grep (zero matches expected for the literal ack strings).
- Verify net-negative diff: `git diff --stat main -- bridge/telegram_bridge.py bridge/response.py` shows more deletions than insertions.
- For each of the six original `logger.info(...)` strings, grep for a distinctive fragment in `bridge/telegram_bridge.py` and confirm it still appears (now inside a `log_context` argument).
- Confirm `_ack_steering_routed` is the only place ABORT_KEYWORDS, push_steering_message, set_reaction, and record_telegram_message_handled appear together in the steering paths.
- Run `python -m ruff check bridge/telegram_bridge.py bridge/response.py` and `python -m ruff format --check bridge/telegram_bridge.py bridge/response.py`.
- Run `pytest tests/unit/ -x -q` and verify no regressions.

#### 5. (Optional) Add helper unit test
- **Task ID**: build-helper-test
- **Depends On**: validate-acks
- **Assigned To**: bridge-ack-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a small unit test for `_ack_steering_routed` covering the steer and abort branches: assert `push_steering_message` is called with the right `is_abort`, `set_reaction` is called with the right emoji constant, and `record_telegram_message_handled` is called once per invocation.
- Skip this task if mocking the Telethon client/event objects proves disproportionate to the test's value — note the skip in the PR description.

#### 6. Update feature docs
- **Task ID**: document-feature
- **Depends On**: validate-acks
- **Assigned To**: steering-docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update the four files listed in the Documentation section.
- Verify each updated doc still reads coherently end-to-end.

#### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bridge-ack-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table below.
- Confirm Success Criteria checklist is satisfied, especially the net-negative-diff bar.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No literal ack strings remain in `.py` | `grep -rn "Adding to current task\|Stopping current task" --include="*.py" .` | exit code 1 (no matches) |
| Net-negative diff | `git diff --shortstat main -- bridge/telegram_bridge.py bridge/response.py` | deletions > insertions |
| `REACTION_ABORT` constant defined | `grep -n "^REACTION_ABORT" bridge/response.py` | output contains `REACTION_ABORT` |
| Helper defined exactly once | `grep -c "^async def _ack_steering_routed" bridge/telegram_bridge.py` | output is `1` |
| Helper called by all six branches | `grep -c "await _ack_steering_routed" bridge/telegram_bridge.py` | output is `6` |
| Dual-push preserved at exactly two sites | `grep -c "agent_session=" bridge/telegram_bridge.py` | output is `2` (one each at the in-memory coalescing guard and intake-classifier interjection branches) |
| Bridge imports both reaction constants | `grep -n "REACTION_RECEIVED\|REACTION_ABORT" bridge/telegram_bridge.py` | output contains both names |
| Inline `send_markdown` imports gone from `handle_message` | `awk '/^async def handle_message/,/^async def [^h]/' bridge/telegram_bridge.py \| grep -c "from bridge.markdown import send_markdown"` | output is `0` |
| Lint clean | `python -m ruff check bridge/` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |

## Critique Results

**Verdict (initial run):** NEEDS REVISION — 1 blocker, 4 concerns, 2 nits. Plan revised below.

### B1: Helper silently drops the durable PM-visible write at dual-push sites — RESOLVED

**Finding:** The plan's "byte-identical sequences" premise was wrong for two of the six sites. Lines 1535 (in-memory coalescing guard) and 1648 (intake-classifier interjection) perform a *dual* push — first to `<session>.push_steering_message(text)` (writes to the AgentSession Popoto model's `queued_steering_messages` field, durable and PM-visible) and then to the Redis steering queue (in-flight injection for the PostToolUse hook). The helper as originally drafted handled only the Redis push, so adopting it as written at those two sites would have silently dropped the durable PM-visible write.

**Fix applied:**
- Helper signature extended with optional `agent_session: AgentSession | None = None`.
- Helper body: when `agent_session` is provided, calls `agent_session.push_steering_message(text)` before the Redis push.
- Plan calls out the two specific sites that must pass `agent_session=guard_session` (line 1535) or `agent_session=fresh_session` (line 1648).
- Verification table adds `grep -c "agent_session=" bridge/telegram_bridge.py` → must equal `2`.
- Risks section adds Risk 6 (this finding) with the resolution.
- Success Criteria adds an explicit dual-push check.

### Concerns (4) and Nits (2) — DEFERRED

The critique skill returned a verdict summary noting 4 concerns and 2 nits beyond the blocker, but did not surface their content through the channel available here. They are not blocking; the build can proceed against the revised plan, and the concerns/nits will be re-raised by `/do-pr-review` if material. If a critique artifact file surfaces later, it should be appended to this section verbatim and the plan re-evaluated before merge.

---

## Resolved Questions

1. **Abort emoji choice — RESOLVED: 🫡 (salute).** Initial proposal of 🛑 was rejected: bot-emitted reactions speak in the *reactor's* voice, and 🛑 applied to a user's message reads as a directive at the author ("*you* stop") rather than a self-report. 🫡 ("understood, standing down") is unambiguously first-person from the reactor and matches the PM persona's calm acknowledgment voice. Per Valor's framing: "a salute means I understand and there's nothing left to say." See memory `feedback_reactor_voice_emoji.md` for the general principle.
2. **Line 1049 special treatment — RESOLVED: no special treatment.** All six sites should act the same. The plan applies the same uniform helper call at every site; the framing of 1049 as a "special case" was framing, not substance.
3. **Why six call sites doing the same thing — RESOLVED: extract a helper.** The duplication wasn't just the ack line — the entire terminal sequence (inline imports + is_abort + push + ack + log + record + return) was duplicated across six sites. Scope expanded to extract a single `_ack_steering_routed` helper that bundles the terminal sequence, with each routing branch collapsing to a one-line call. Net target: PR removes more lines than it adds. The routing decisions per branch (which session to steer into) stay distinct — only the terminal converges.
