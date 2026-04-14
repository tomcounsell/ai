---
status: docs_complete
type: bug
appetite: Small
owner: valorengels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/911
last_comment_id:
revision_applied: true
---

# Agent Reply Loop: Conversation Terminus Detection (RESPOND / REACT / SILENT)

## Problem

When Valor and another AI agent (e.g., Nick's agent) are both active in the same Telegram group, they can get trapped in an endless reply loop. Each agent receives the other's message as a "reply to themselves," which triggers a response unconditionally — before any passive-listener or persona rules fire.

**Current behavior:** Agent A replies to Valor → Valor responds → Agent A responds → infinite loop. Must be broken manually.

**Desired outcome:** Valor can judge when a reply-to-Valor message is a natural conversation terminus and choose SILENT or REACT instead of RESPOND. The reply-to-Valor early return in `should_respond_async` calls `classify_conversation_terminus` before deciding, so loops break naturally.

## Freshness Check

**Baseline commit:** `d00a1c9586305f786be374e7e4544d32adb0949c`
**Issue filed at:** `2026-04-12T04:25:46Z`
**Disposition:** Minor drift

**File:line references re-verified:**

- `bridge/routing.py:865–870` — Reply-to-Valor early return (issue cited lines 777–783; shifted after 3 commits). The unconditional `return True, True` is confirmed at lines 865–870. Root cause still holds.
- `bridge/routing.py:876–884` — Teammate persona passive-listener check (issue cited lines 786–796). Confirmed at lines 874–884. Still runs AFTER the reply-to-Valor block; bug still present.
- `bridge/routing.py:376–423` — `_ACKNOWLEDGMENT_TOKENS` set. Confirmed still present at these lines. Not used in reply-to-Valor path — confirmed.
- `bridge/routing.py:426–481` — `classify_needs_response` / `classify_needs_response_async`. Confirmed. Uses Ollama-first / fallback pattern that can be reused.
- `bridge/response.py:759` — `set_reaction` definition (issue cited `bridge/telegram_bridge.py:115`; `set_reaction` is actually defined in `bridge/response.py:759` and imported into `telegram_bridge.py`). Corrected location noted.
- `config/enums.py` — `PersonaType.TEAMMATE` — confirmed present (not inspected line by line, but used at routing.py:876).

**Commits on main since issue was filed (touching referenced files):**

- `78b275b3` fix(bridge,health): add dedup to steering paths and child-aware _has_progress — irrelevant to routing logic
- `a043e46a` refactor(bridge): centralize dedup recording in dispatch wrapper — irrelevant
- `82186dcc` fix(bridge): hydrate reply-thread context in resume-completed branch — touches routing indirectly; does not change reply-to-Valor early return
- `9c1d9e21` Fix: deterministic reply-to root cache + completed session resume — touches reply routing; does not fix the unconditional early return bug

**Active plans in `docs/plans/` overlapping this area:** None found in bridge/routing space.

**Notes:** Line numbers shifted by ~90 lines due to refactor commits. All claims verified correct at current HEAD. `set_reaction` is imported from `bridge/response.py` not `telegram_bridge.py` — plan's implementation references updated accordingly.

## Prior Art

- **PR #559**: Config-driven chat mode resolution — established the Teammate persona passive-listener pattern via `resolve_persona()`. Did not address reply-to-Valor ordering. This plan builds on top of that work.

No prior issues or PRs attempted to fix the reply-loop / terminus detection problem.

## Data Flow

1. **Entry point**: Telegram message arrives — another agent's reply to a Valor message
2. **`handler()` in `telegram_bridge.py`**: Extracts `sender`, `text`, `chat_title`, `is_dm`; calls `should_respond_async(...)`
3. **`should_respond_async()` in `bridge/routing.py`**: Checks `message.reply_to_msg_id`; fetches replied message; if `replied_msg.out` → currently returns `(True, True)` unconditionally
4. **NEW — `classify_conversation_terminus()`**: Called at the reply-to-Valor decision point with `text`, `thread_messages`, `sender_is_bot` (no `chat_title` — unused). Returns `"RESPOND"`, `"REACT"`, or `"SILENT"`.
5. **Dispatch**:
   - `RESPOND` → `return True, True` (existing behavior preserved)
   - `REACT` → caller sets emoji reaction via `set_reaction`; `return False, True`
   - `SILENT` → `return False, True`
6. **`handler()` receives `(False, True)`**: `is_reply_to_valor=True` still propagates for session continuation logic; no text reply sent

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — no new deps, no config changes required. Ollama and Haiku fallback already wired.

## Solution

### Key Elements

- **`classify_conversation_terminus()`**: New async function in `bridge/routing.py`. Takes `text`, `thread_messages` (recent turns), `sender_is_bot`. Returns `"RESPOND"`, `"REACT"`, or `"SILENT"`.
- **Wire-up in `should_respond_async()`**: At the reply-to-Valor detection point (lines 865–870), call `classify_conversation_terminus` before returning `(True, True)`. If terminus detected, return `(False, True)` optionally with a reaction.
- **Reaction dispatch**: When terminus is `REACT`, `should_respond_async` calls `set_reaction` via a **deferred local import** inside `should_respond_async` (not a top-level import) to avoid a circular dependency with `bridge/response.py`, which already defers `from bridge.routing import DEFAULT_MENTIONS` at its line 331.

### Flow

Incoming reply to Valor → `should_respond_async` detects reply-to-Valor → calls `classify_conversation_terminus` → `RESPOND`: continue as now / `REACT` (human sender only): set emoji + return silent / `SILENT`: return silent + preserve `is_reply_to_valor` flag. Bot senders always collapse to SILENT when terminus is non-RESPOND.

### Technical Approach

**`classify_conversation_terminus` function signature:**

```python
async def classify_conversation_terminus(
    text: str,
    thread_messages: list[str],  # recent turns, oldest first
    sender_is_bot: bool = False,
) -> str:  # "RESPOND" | "REACT" | "SILENT"
```

The `chat_title` parameter is removed — it was unused and added no signal to terminus detection.

**Signifier priority (for LLM prompt context):**

Fast-paths (checked after `sender_is_bot` guard, in this order):
1. Sender is a bot + text contains no question → SILENT (strongest signal; checked first because bot sources are the primary loop scenario)
2. Short/acknowledgment message (≤ 10 words or text in `_ACKNOWLEDGMENT_TOKENS`) — applied **only after** `sender_is_bot` is checked, to avoid silencing human short acknowledgments before knowing the sender → SILENT
3. Text contains a standalone `?` (not preceded by `=` or `&`, i.e., not a URL query parameter) → RESPOND (fast exit for questions before LLM call)

LLM-evaluated (when no fast-path fires):
4. Completion language without follow-up ("that makes sense", "agreed", "fair enough") → REACT
5. Semantic redundancy (restates content already in last 2 turns) → REACT

**Ordering rationale:** Critiques found that the original draft fired `_ACKNOWLEDGMENT_TOKENS` *before* `sender_is_bot` check, which could silence human "yes/no" replies. The revised order: check sender first, then acknowledgment tokens. The `?` fast-path is narrowed to avoid false-negatives on URL-containing messages (e.g., `https://example.com?q=1` would not trigger RESPOND).

**Collapse REACT → SILENT for bot senders:** The REACT path was originally added to set an acknowledgment emoji when a human-initiated thread winds down naturally. For bot senders, REACT adds emoji spam with minimal value since the bot loop is the primary concern and the bot won't see the reaction. **Revised behavior:** When `sender_is_bot=True` and terminus is non-RESPOND, always return `SILENT`. REACT is reserved for human-sender threads only. This eliminates the import complexity concern — `set_reaction` is called only when `sender_is_bot=False` and terminus is `REACT`.

**LLM approach:** Ollama-first, Haiku fallback — matching the existing `classify_needs_response` pattern. The signifiers above become heuristic context injected into the prompt. Conservative fallback: if both fail, return `"RESPOND"` to avoid false silences on genuine questions.

**Thread context retrieval:** Use the already-fetched `replied_msg` as one-message thread context. No additional API calls. This is intentional (per No-Gos).

**Wire-up point (replacing current lines 865–870):**

```python
if replied_msg and replied_msg.out:
    sender_is_bot = getattr(sender, "bot", False)
    terminus = await classify_conversation_terminus(
        text=text,
        thread_messages=[replied_msg.message or ""] if replied_msg else [],
        sender_is_bot=sender_is_bot,
    )
    if terminus == "RESPOND":
        logger.info("Reply to Valor detected - continuing session")
        return True, True
    if terminus == "REACT" and not sender_is_bot:
        from bridge.response import set_reaction  # deferred to avoid circular import
        await set_reaction(client, event.chat_id, message.id, "👍")
    logger.info(f"Reply to Valor: terminus={terminus}, not responding")
    return False, True
```

**Return signature unchanged:** `should_respond_async` still returns `(bool, bool)`. All terminus logic is self-contained — no caller changes needed. The deferred `from bridge.response import set_reaction` inside `should_respond_async` avoids the circular import: `bridge/response.py` already defers `from bridge.routing import DEFAULT_MENTIONS` at its line 331, so making routing's import of response also deferred keeps both directions lazy and cycle-free.

**Log level:** Terminus decisions log at `INFO` (not `DEBUG`) so they are visible in production log tails without enabling debug verbosity.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `classify_conversation_terminus` must catch Ollama + Haiku API failures and return `"RESPOND"` (conservative default — never silently drop a genuine question due to classifier error)
- [ ] The existing `classify_needs_response` sets the pattern: `except Exception: return True` — mirror this

### Empty/Invalid Input Handling

- [ ] `text=""` or `text=None` → treat as potential continuation, return `"RESPOND"`
- [ ] `thread_messages=[]` → reduce confidence in terminus detection; lean toward `"RESPOND"`
- [ ] `sender_is_bot=False` (unknown) → do not apply stricter bot terminus rules

### Error State Rendering

- [ ] When reaction emoji fails (`set_reaction` returns `False`), log at DEBUG level and continue — reaction failure must never bubble up to crash the routing layer

## Test Impact

- [ ] `tests/unit/test_routing.py` — UPDATE: add `classify_conversation_terminus` test cases (new function; existing tests unaffected)
- [ ] `tests/unit/test_routing.py::test_no_legacy_valor_usernames_constant` — UNCHANGED: no impact
- [ ] No existing tests test `should_respond_async` — no existing tests break

New tests to add (in `tests/unit/test_routing.py`):

- `test_classify_terminus_bot_no_question_returns_silent` — bot sender + declarative message → SILENT
- `test_classify_terminus_human_question_returns_respond` — human sender + "?" → RESPOND
- `test_classify_terminus_url_with_query_param_not_respond` — `"https://example.com?q=1"` with no standalone `?` — bot sender → SILENT (not RESPOND; `?` inside URL query string must not trigger fast-path)
- `test_classify_terminus_acknowledgment_token_returns_silent` — "got it" from human → SILENT
- `test_classify_terminus_acknowledgment_fires_after_bot_check` — "yes" from bot → SILENT (same result, but sender_is_bot fast-path fires first)
- `test_classify_terminus_ollama_failure_defaults_to_respond` — mock Ollama failure → RESPOND
- `test_classify_terminus_empty_text_returns_respond` — empty text → RESPOND
- `test_classify_terminus_bot_react_collapses_to_silent` — when LLM returns REACT but sender_is_bot=True → SILENT

## Rabbit Holes

- **Thread history retrieval cost**: Full multi-turn thread fetch adds API calls. Plan deliberately limits to the already-fetched `replied_msg` as thread context (1 message). Richer context can be added as a follow-up if detection quality is poor.
- **Reaction spam**: REACT on every agent reply fills chat with emoji. Per the issue's own note: prefer SILENT as default for bot senders, REACT only for human-initiated threads that are winding down naturally.
- **Per-group tuning**: No per-group config knobs — universal classifier is sufficient (per issue No-Gos).
- **Semantic redundancy detection**: Full embedding-based redundancy check is overkill for a Small appetite. The LLM prompt context covers this heuristically.

## Risks

### Risk 1: False positives silencing genuine agent questions
**Impact:** Valor stays silent when another agent asks a real question via reply chain.
**Mitigation:** Conservative fallback (`"RESPOND"` on classifier error or uncertainty). Prompt includes explicit "if message contains a question, return RESPOND" instruction. `sender_is_bot=True` only applies stricter rules, not absolute silence.

### Risk 2: Circular import between routing and response modules
**Impact:** `bridge/response.py` already defers `from bridge.routing import DEFAULT_MENTIONS` at line 331. Adding a top-level `from bridge.response import set_reaction` in `routing.py` would create a circular import at module load time.
**Mitigation:** Use a deferred local import inside `should_respond_async` — `from bridge.response import set_reaction` is placed inline, only executed when terminus is REACT and sender is human. Both directions stay lazy; Python's import machinery handles this safely. This is the same pattern `bridge/response.py` already uses for `bridge.routing`.

## Race Conditions

No race conditions identified — the terminus check is a synchronous decision at message receipt time, within a single `async` handler with no shared mutable state introduced by this change.

## No-Gos (Out of Scope)

- Do not change Teammate persona behavior for @mention or human-originated messages
- Do not add per-group config knobs for terminus sensitivity
- Do not break the `is_reply_to_valor` flag — downstream session continuation still needs it
- Do not implement full multi-turn thread fetching for context — use the already-fetched replied message only

## Update System

No update system changes required — this feature is purely bridge-internal routing logic with no new config files, environment variables, or dependencies.

## Agent Integration

No agent integration required — this is a bridge-internal routing decision. The agent itself never sees terminus classification; it either gets invoked or doesn't. No new MCP tools, no `.mcp.json` changes.

## Documentation

- [ ] Create `docs/features/agent-reply-terminus.md` describing the conversation terminus detection system (signifiers, RESPOND/REACT/SILENT semantics, wire-up point)
- [ ] Add entry to `docs/features/README.md` index table under "Bridge"

## Success Criteria

- [ ] `classify_conversation_terminus(text, thread_messages, sender_is_bot) -> str` exists in `bridge/routing.py` — no `chat_title` parameter
- [ ] Reply-to-Valor path in `should_respond_async` calls `classify_conversation_terminus` before deciding to respond
- [ ] When `sender_is_bot=True` AND message has no question, result is SILENT (not RESPOND, not REACT)
- [ ] `_ACKNOWLEDGMENT_TOKENS` fast-path fires AFTER `sender_is_bot` check — never before
- [ ] Standalone `?` in text triggers RESPOND fast-path; `?` inside URL query strings (preceded by `=` or `&`) does NOT
- [ ] When terminus is REACT and sender is human (not bot), a reaction emoji is set via `set_reaction` using a deferred local import (not top-level)
- [ ] No top-level `from bridge.response import` in `bridge/routing.py` — circular import prevented
- [ ] Terminus decisions log at INFO level (not DEBUG)
- [ ] Existing behavior for human reply chains is NOT degraded (genuine questions still get responses)
- [ ] `classify_conversation_terminus` returns `"RESPOND"` on any classifier failure (conservative)
- [ ] Unit tests pass: bot-sender + no-question → SILENT; human-sender + question → RESPOND; acknowledgment token → SILENT; URL with query param → not RESPOND for bot sender; classifier failure → RESPOND; empty text → RESPOND; bot REACT collapses to SILENT
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement `classify_conversation_terminus` in `bridge/routing.py` and wire into `should_respond_async`
  - Agent Type: builder
  - Resume: true

- **Test Engineer (routing)**
  - Name: routing-test-engineer
  - Role: Write unit tests for `classify_conversation_terminus` covering all specified cases
  - Agent Type: test-engineer
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify implementation, run tests, confirm no regression in existing routing behavior
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create `docs/features/agent-reply-terminus.md` and update `docs/features/README.md`
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(see template for full list)

## Step by Step Tasks

### 1. Implement `classify_conversation_terminus`
- **Task ID**: build-terminus-classifier
- **Depends On**: none
- **Validates**: `tests/unit/test_routing.py` (new tests)
- **Informed By**: freshness check (confirmed `classify_needs_response` Ollama/Haiku pattern at `bridge/routing.py:426–481`)
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classify_conversation_terminus(text, thread_messages, sender_is_bot) -> str` to `bridge/routing.py` using Ollama-first, Haiku fallback pattern matching `classify_needs_response`. No `chat_title` parameter — unused.
- Fast-path order (critical — see critique findings): (a) if `sender_is_bot` and no question → `"SILENT"`; (b) if `text.strip().lower() in _ACKNOWLEDGMENT_TOKENS` or len ≤ 1 word → `"SILENT"` (acknowledgment check runs AFTER sender check, never before); (c) if standalone `?` in text (regex: `(?<![=&])\?` to exclude URL query strings) → `"RESPOND"`
- LLM prompt: inject signifiers (sender_is_bot, thread depth, question detection) as context; return `RESPOND`, `REACT`, or `SILENT`; fallback to `"RESPOND"` on any exception
- REACT collapse: when `sender_is_bot=True`, map any `REACT` result to `SILENT` — no emoji spam for bot loops
- Wire into `should_respond_async` at lines 865–870: call `classify_conversation_terminus` after confirming `replied_msg.out`; use deferred `from bridge.response import set_reaction` inside `should_respond_async` (NOT a top-level import) to avoid circular import with `bridge/response.py` which already defers `from bridge.routing import DEFAULT_MENTIONS` at line 331; log at `INFO` not `DEBUG`
- Keep `should_respond_async` return signature as `(bool, bool)` — no caller changes needed

### 2. Write unit tests
- **Task ID**: test-terminus-classifier
- **Depends On**: build-terminus-classifier
- **Validates**: `tests/unit/test_routing.py`
- **Assigned To**: routing-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add to `tests/unit/test_routing.py`: `test_classify_terminus_bot_no_question_returns_silent`, `test_classify_terminus_human_question_returns_respond`, `test_classify_terminus_acknowledgment_token_returns_silent`, `test_classify_terminus_ollama_failure_defaults_to_respond`, `test_classify_terminus_empty_text_returns_respond`
- Mock Ollama and Haiku clients to keep tests offline/fast

### 3. Validate routing behavior
- **Task ID**: validate-routing
- **Depends On**: test-terminus-classifier
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_routing.py -v` — all tests must pass
- Run `python -m ruff check bridge/routing.py` and `python -m ruff format --check bridge/routing.py`
- Confirm `should_respond_async` return signature unchanged (still `tuple[bool, bool]`)
- Confirm `_ACKNOWLEDGMENT_TOKENS` fast-path fires AFTER `sender_is_bot` check (not before)
- Confirm no top-level `from bridge.response import` in `routing.py` — deferred import only
- Confirm terminus decisions log at `INFO` not `DEBUG`
- Confirm `classify_conversation_terminus` signature has NO `chat_title` parameter

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-reply-terminus.md` — describe RESPOND/REACT/SILENT semantics, signifier priority, wire-up point, and bot-loop break behavior
- Add row to `docs/features/README.md` index table under Bridge section

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -q` — all unit tests pass
- Verify `docs/features/agent-reply-terminus.md` and `docs/features/README.md` updated
- Confirm all success criteria checked

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_routing.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/routing.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/routing.py` | exit code 0 |
| Terminus function exists | `grep -n "classify_conversation_terminus" bridge/routing.py` | output > 0 |
| Wire-up in should_respond_async | `grep -A5 "replied_msg.out" bridge/routing.py \| grep "terminus"` | output > 0 |
| Signature unchanged | `grep "async def should_respond_async" bridge/routing.py` | output contains "tuple\[bool, bool\]" |
| No top-level circular import | `grep "^from bridge.response" bridge/routing.py` | no output |
| chat_title param absent | `grep "chat_title" bridge/routing.py` | no output in terminus function |
| INFO log level | `grep "logger.info.*terminus" bridge/routing.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist | `bridge/response.py:331` defers `from bridge.routing import DEFAULT_MENTIONS`; adding top-level `from bridge.response import set_reaction` in `routing.py` creates a circular import | Resolved | Use deferred local import inside `should_respond_async` — same pattern response.py already uses. No top-level import of response in routing. |
| CONCERN | Skeptic | `_ACKNOWLEDGMENT_TOKENS` fast-path fired before `sender_is_bot` check, potentially silencing human "yes/no" replies | Resolved | Fast-path order revised: (1) sender_is_bot check, (2) acknowledgment tokens, (3) standalone `?` fast-path. |
| CONCERN | Adversary | `"?" in text` fast-path false-negatives on URL query strings (e.g., `?q=1`) | Resolved | Narrowed to regex `(?<![=&])\?` — standalone `?` only, not query-param `?` |
| CONCERN | Operator | Signifier 2 (thread depth ≥ 3) is unreachable — plan limits to 1-message context | Resolved | Signifier 2 removed from signifier list. Thread depth not evaluated with single-message context. |
| CONCERN | Simplifier | REACT path adds import complexity for marginal value when sender is a bot | Resolved | REACT is now human-only. When `sender_is_bot=True`, any REACT result collapses to SILENT. Import only runs for human-sender REACT cases. |
| NIT | Operator | Log level DEBUG for terminus decisions makes them invisible in production | Resolved | Changed to INFO in wire-up pseudocode and Step 3 validation checklist. |
| NIT | Simplifier | `chat_title` parameter in `classify_conversation_terminus` was unused | Resolved | Parameter removed from function signature throughout plan. |

---

## Open Questions

None — the issue recon was thorough and all implementation choices are clear from the freshness check and code review.
