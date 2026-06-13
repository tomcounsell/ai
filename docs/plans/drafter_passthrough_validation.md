---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-13
tracking: https://github.com/tomcounsell/ai/issues/1680
last_comment_id:
revision_applied: true
---

# Reposition Message Drafter from Rewriting Summarizer to Pass-Through Validation Filter

## Problem

The repo drives an interactive Claude Code TUI through a PTY. The "driving" session
runs on **Opus** (the most capable model) and writes the final user-facing message
itself. But before that message reaches the human over Telegram/email, it passes
through the **message drafter** (`bridge/message_drafter.py`), which does not just
validate — it **regenerates** the message by feeding the agent's output to **Haiku**
(or OpenRouter Haiku on failure) via a `structured_draft` tool call and emitting
Haiku's rewritten `response` as the message the human sees.

The result is a quality-laundering pipeline: **Opus writes the message → Haiku
rewrites it → the human reads Haiku's version.** For terse SDLC status lines this is
tolerable. For conversational (teammate) and PM-driver replies it is actively
harmful — Haiku strips Opus's voice, nuance, and precision, producing the fumbling,
generic, sometimes-truncated replies seen in chit-chat. We pay for Opus and ship
Haiku.

The original value of the drafter — catching glaring wire-format and integrity
problems — is still wanted. We keep the **guardrail**, drop the **rewrite**.

**Current behavior:**
- `draft_message()` (`bridge/message_drafter.py:1763`) routes any non-trivial output
  through `_draft_with_haiku()` (line 1488; model `MODEL_FAST`) → `_draft_with_openrouter()`
  (line 1549; `OPENROUTER_HAIKU`) and returns the LLM-regenerated `response` as the
  message text.
- Short outputs (<200 chars, no artifacts/question/code) already bypass the LLM
  (`SHORT_OUTPUT_THRESHOLD`, line 1760; early return at 1814-1826). Teammate sessions
  get a prose bypass after drafting (`_compose_structured_draft`, line 1730). These
  bypasses are evidence the rewrite is already understood to be unwanted in the common
  case — they are patches over a design that should be inverted.

**Desired outcome:**
- The Opus driving session's text is delivered **verbatim** (modulo non-semantic
  process-narration stripping) for teammate and PM-driver output. The drafter never
  substitutes model-generated prose for the agent's own words.
- The drafter becomes a **pass-through validation filter** that only *flags* glaring
  problems and never rewrites to fix them:
  - **Too long** → attach full output as a `.txt` file (`FILE_ATTACH_THRESHOLD` = 3000)
    and/or flag.
  - **Wire-format violations** → markdown tables in Telegram, markdown in email
    (existing `validate_telegram` / `validate_email` / `_validate_for_medium`).
  - **Empty/false promises** → "will do", "going forward" with no substance
    (existing `_detect_empty_promise`).
  - **Process narration** → "Let me check…", "Now let me read…" (existing
    `_strip_process_narration`).
- A flag drives a **steering nudge back to the authoring agent** ("your message is too
  long / contains a markdown table / makes an empty promise — rewrite it yourself")
  rather than a silent third-party rewrite. The existing `needs_self_draft` →
  `_inject_self_draft_steering` path becomes the **primary** mechanism instead of a
  failure fallback.
- **Net negative diff** in `bridge/message_drafter.py`: the entire `_draft_with_haiku`
  / `_draft_with_openrouter` / `structured_draft` rewrite machinery is deleted. The PR
  removes more lines than it adds.

## Freshness Check

**Baseline commit:** `b4545fbdd15f2a22fc5959830a0897e6246a7ff6`
**Issue filed at:** 2026-06-13T16:21:32Z (same day as planning)
**Disposition:** Minor drift (line numbers in the issue drifted by ~10-40 lines under
edits; all cited symbols and claims still hold)

**File:line references re-verified:**
- `bridge/message_drafter.py` — `_draft_with_haiku` claimed at :1488 → confirmed at :1488. Still holds.
- `bridge/message_drafter.py` — `_draft_with_openrouter` claimed at :1549 → confirmed at :1549. Still holds.
- `bridge/message_drafter.py` — `structured_draft` schema claimed at :245-273 → confirmed `STRUCTURED_DRAFT_TOOL` at :245-273. Still holds.
- `bridge/message_drafter.py` — `MessageDraft` claimed at :285 → confirmed at :285. Still holds.
- `bridge/message_drafter.py` — `classify_output` claimed at :883 → confirmed at :883. Still holds; verified **orphaned for routing** (no production caller; only its internal `_delegate_to_promise_gate` reaches `bridge/promise_gate.py`).
- `bridge/message_drafter.py` — `_validate_for_medium` claimed at :381 → confirmed at :381. `validate_telegram` :302, `validate_email` :342. Still holds.
- `bridge/message_drafter.py` — `_strip_process_narration` claimed at :174 → confirmed at :174. Still holds.
- `bridge/message_drafter.py` — `_detect_empty_promise` claimed at :666 → confirmed at :666 (delegates to `bridge/promise_gate.py:_detect_empty_promise`). Still holds.
- `bridge/message_drafter.py` — `SHORT_OUTPUT_THRESHOLD` claimed at :1760 → confirmed :1760; bypass at :1814-1826. Still holds.
- `bridge/message_drafter.py` — `FILE_ATTACH_THRESHOLD` behavior claimed at :1830 → confirmed at :1830. Still holds.
- Call site `agent/output_handler.py:371` → confirmed `draft_message(...)` at :371; reads `.text`, `.full_output_file`, `.needs_self_draft`, `.was_drafted`, `.violations`, persists `.context_summary`/`.expectations`. Still holds.
- Call site `agent/hooks/stop.py:150` → confirmed; reads `.text` + `.violations` for the review-gate warning. Still holds.
- Call site `tools/send_telegram.py:91` → confirmed; reads `.text` only. Still holds.
- Call site `bridge/email_bridge.py:578` → confirmed; reads `.text` only. Still holds.

**Cited sibling issues/PRs re-checked:**
- Granite layer (`agent/granite_container/granite_classifier.py`) — file confirmed present (19KB). Filed as follow-up **#1681** (see No-Gos). Out of scope here.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:**
- `summarizer-fallback-steering.md` (PR #892) — the plan that *introduced* the
  `needs_self_draft` → steering fallback. This plan **inverts** it: steering becomes
  primary, not fallback. Coordination signal, not blocker.
- `summarizer_integration_audit_676.md` (PR #681) — historical audit. No conflict.

**Notes:** The issue's docstring example for `_compose_structured_draft` shows an
SDLC stage-progress line (`ISSUE 243 → PLAN → ▶ BUILD → …`). In the current code,
`_compose_structured_draft` produces an emoji prefix + parsed bullets + a `>>`
questions block + inline `PR #N`/`Issue #N` linkification — all **deterministic,
non-LLM**. The stage-progress line itself is composed upstream by `session_progress`
and linkified here. This is load-bearing for Open Question #1's resolution below.

## Prior Art

- **PR #1072**: "Message drafter: rename, medium-aware drafting, tool-call delivery,
  length guard (#1035)" — renamed `summarizer.py` → `message_drafter.py`, introduced
  `structured_draft` tool-call delivery and `medium` parameter. This is the machinery
  #1680 now reverses-in-part (keep medium-aware validators, remove tool-call rewrite).
- **PR #892**: "Summarizer fallback: agent self-summary via session steering" — added
  the `needs_self_draft` → `_inject_self_draft_steering` path as a *fallback* when all
  LLM backends fail. This plan promotes that path to the *primary* flag-handling
  mechanism.
- **PR #1204**: "Read-the-Room pre-send pass for the Telegram drafter (#1193)" — added
  the redundancy/read-the-room (RTR) wiring downstream of `draft_message`. RTR tests
  use a drafter-bypass fixture and are unaffected by this change.
- **PR #602**: "Agent-controlled message delivery: stop-hook review gate" — established
  the stop-hook review gate (`agent/hooks/stop.py`) that surfaces `violations` to the
  agent. This plan reinforces that pattern (violations stay; rewrite goes).
- **PR #1077**: "#1035 deferred scope: consolidate bridge/response.py, validator tests"
  — consolidated the validator surface this plan keeps.

No prior attempt tried to remove the rewrite while keeping the validators; this is the
first such attempt. **Why Previous Fixes Failed** section omitted — there is no prior
failed fix for this specific problem.

## Data Flow

1. **Entry point**: Opus driving session emits user-facing text at a turn boundary.
2. **Output handler** (`agent/output_handler.py:371`): calls `draft_message(text, session, medium)`.
3. **Drafter today** (`bridge/message_drafter.py:draft_message`): strips narration →
   builds prompt → calls Haiku (`_draft_with_haiku`) → on failure OpenRouter
   (`_draft_with_openrouter`) → extracts `structured.response` → `_compose_structured_draft`
   (emoji + bullets + questions + linkify) → runs `_validate_for_medium` → returns
   `MessageDraft(text=<rewritten>, was_drafted=True, violations=[...])`.
4. **Drafter after this change**: strips narration → runs `_validate_for_medium` on
   the **raw** text → if over `FILE_ATTACH_THRESHOLD`, writes full-output file → if any
   blocking flag fires, returns `MessageDraft(text="", needs_self_draft=True, ...)` to
   trigger steering → otherwise returns `MessageDraft(text=<raw, deterministically
   composed>, violations=[...])`.
5. **Output handler** reads `.needs_self_draft` (→ `_inject_self_draft_steering` pushes
   `SELF_DRAFT_INSTRUCTION` to `queued_steering_messages`; defers outbox write) or
   delivers `.text` through the RTR/redundancy filter to the Redis outbox.
6. **Output**: human reads the Opus-authored text verbatim, or the agent receives a
   steering nudge and re-drafts on its next turn.

## Architectural Impact

- **New dependencies**: none. This is a removal.
- **Interface changes**: `draft_message()` signature is **unchanged** (keeps `medium`,
  `persona`, `session`). `MessageDraft` shrinks — `was_drafted` is removed (it is read
  in exactly one place, `agent/output_handler.py:404`, to gate routing-field
  persistence; that gate is replaced — see Technical Approach). `context_summary` and
  `expectations` fields are both **retained on `MessageDraft` and still persisted**:
  `expectations` is populated deterministically from `_extract_open_questions`;
  `context_summary` is now populated by a new deterministic helper
  `_derive_context_summary(raw_text)` (first non-narration sentence, capped) instead of
  Haiku. **The field is NOT removed** — see Blocker-1 resolution in Technical Approach.
  Three live routing readers (`bridge/session_router.py:85`,
  `bridge/telegram_bridge.py:2001`, `agent/session_executor.py:1979`, plus a fourth at
  `bridge/telegram_bridge.py:779`) consume `session.context_summary` for session-resume
  routing and incoming-message intent classification; removing the writer would silently
  degrade all four (each or-guards to a fallback string — no crash, just worse routing).
- **Coupling**: **decreases.** Removes the Anthropic/OpenRouter HTTP dependency from
  the hot message-delivery path. The drafter no longer imports `MODEL_FAST`,
  `OPENROUTER_HAIKU`, `OPENROUTER_URL` for the rewrite path (`classify_output` may still
  need `MODEL_FAST` — see OQ#2). The retained `context_summary` source is deterministic
  (string slicing), so it adds **no** new LLM dependency.
- **Data ownership**: the **agent** now owns the final message prose end-to-end; the
  drafter owns only the format/integrity verdict.
- **Reversibility**: high. The deleted functions are self-contained; reverting the PR
  restores them.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (resolve the two open questions before build)
- Review rounds: 1 (this is a removal with a clear net-negative-diff success metric;
  the risk surface is the four call sites and the steering-promotion)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo importable | `python -c "import bridge.message_drafter"` | Module loads after edits |
| Anthropic key (only if `classify_output` retained — see OQ#2) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Classification gate (conditional) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/drafter_passthrough_validation.md`

## Solution

### Key Elements

- **Verbatim pass-through**: `draft_message()` returns the agent's raw output (cleaned
  only of process narration) as `MessageDraft.text`. No LLM regeneration.
- **Deterministic composition retained**: emoji prefix, `>>` question parsing, and
  inline `PR #N`/`Issue #N` linkification stay (they are non-LLM and add no prose).
  The SDLC footer/link append stays as a **deterministic, non-LLM mechanical step**.
- **Validators retained**: `validate_telegram`, `validate_email`, `_validate_for_medium`,
  `_detect_empty_promise`, `_strip_process_narration` are untouched and now run against
  the raw text.
- **Steering-first flagging**: when a blocking flag fires (over-length without a clean
  truncation path, wire-format violation, empty promise), the drafter returns
  `needs_self_draft=True` so `_inject_self_draft_steering` nudges the authoring agent.
  An **atomic Redis counter** `steering:attempts:{session_id}` (cap =
  `SELF_DRAFT_MAX_ATTEMPTS` = 2), incremented server-side via `INCR` so two concurrent
  flagged outputs cannot under-count, bounds the **sequential** nudge loop so a
  structurally-unfixable flag eventually falls through to narration-fallback/file delivery
  instead of looping forever (Blocker-2). The counter lives **outside** the `AgentSession`
  hash, so it adds no full-hash `save()` and cannot clobber `context_summary`/`expectations`
  (new Race 3).
- **Routing fields retained deterministically**: `expectations` (from
  `_extract_open_questions`) and `context_summary` (from a new deterministic
  `_derive_context_summary`) are still persisted to the `AgentSession` so the live
  session-routing readers (`session_router.py`, `telegram_bridge.py`,
  `session_executor.py`) keep working — no reader site is touched (Blocker-1).
- **Rewrite machinery deleted**: `_draft_with_haiku`, `_draft_with_openrouter`,
  `STRUCTURED_DRAFT_TOOL`, `StructuredDraft`, `_build_draft_prompt`,
  `BASE_DRAFTER_PROMPT`/`MEDIUM_RULES`/`DRAFTER_SYSTEM_PROMPT` (the rewrite system
  prompt), `_parse_draft_and_questions` (only if no longer needed by composition),
  `SAFETY_TRUNCATE` rewrite-length guard, and the `was_drafted` field.

### Flow

Opus emits message → output_handler calls `draft_message` → drafter strips narration +
runs validators on raw text → **(a)** clean: deliver raw text verbatim (emoji/linkify
applied) → human reads Opus's words; **(b)** flagged: return `needs_self_draft=True` →
steering nudge → agent re-drafts next turn → loop until clean or delivered.

### Technical Approach

**Blocker-1 resolution — retain `context_summary` via a deterministic source (war-room critique):**
The critique found that removing `context_summary`'s only writer
(`_persist_routing_fields`, `agent/output_handler.py:790-808`) would **silently break
four live routing readers**, all of which or-guard to a fallback so the regression ships
with no crash, just degraded routing:
- `bridge/session_router.py:85` — `context = s.context_summary or "(no context)"` —
  builds the multiple-choice prompt that routes a reply-less message to the right active
  session.
- `bridge/telegram_bridge.py:2001` — `session_context=target_session.context_summary or ""`
  — feeds the intake intent classifier (interjection vs. new_work).
- `agent/session_executor.py:1979` — `getattr(agent_session, "context_summary", None) or
  "This continues a previously completed session."` — augments a resumed session's prompt.
- `bridge/telegram_bridge.py:779` — reads the completed session's `context_summary` for
  the best resume context.

Removing the writer also contradicts this plan's own No-Go "do not redesign
`bridge/session_router.py`."

**Decision: option (a) — retain a deterministic `context_summary` source so persistence
keeps populating the field.** This is preferred over option (b) (migrate all four reader
sites + add a Redis round-trip integration test) because it preserves the
net-negative-diff goal AND the No-Go, and a cheap deterministic summary is viable: the
routing readers consume `context_summary` only as a coarse topic hint (each already
or-guards to a generic fallback), so a deterministic first-sentence summary is strictly
better than empty and good enough for routing.

Implementation: add a small helper to `bridge/message_drafter.py`:
```python
def _derive_context_summary(raw_text: str) -> str | None:
    """Deterministic, non-LLM one-line session topic for routing.
    First non-narration sentence of the agent's own text, capped at ~140 chars.
    Returns None for empty/whitespace input."""
```
`draft_message` populates `MessageDraft.context_summary` from this helper (operating on
the narration-stripped raw text) instead of `structured.context_summary`. The
`MessageDraft.context_summary` field and the `_persist_routing_fields` write path are
**unchanged in shape** — only the source flips from Haiku to deterministic. No reader
site is touched; the No-Go holds. The `STRUCTURED_DRAFT_TOOL` schema's `context_summary`
property is deleted along with the rest of the tool (that schema field was only the
Haiku input), but the persisted `session.context_summary` keeps getting populated.

**Verification-gate fix (Blocker-1, second half):** the war room flagged that the
Verification table inspected the wrong file — it would pass while this regression ships.
The Verification section now includes an explicit check that the `context_summary` writer
still fires and a `grep` asserting the deterministic helper exists, replacing any
`promise_gate.py`-targeted check that does not exercise this path.

**Open Question #1 resolution — SDLC structured template / footer:**
Keep the **deterministic, non-LLM footer-appender**. `_compose_structured_draft` does
*not* call an LLM — it applies an emoji prefix, parses `>>` questions, and linkifies
`PR #N`/`Issue #N` references. None of that is prose regeneration. We **retain** this
mechanical composition for SDLC (and the teammate prose bypass), but it now operates on
the agent's **own** bullets, not Haiku's rewrite. The agent already emits the bullet
shape (the SDLC PM persona prompt instructs it). The stage-progress line is composed
upstream (`session_progress`) and mechanically linkified here — unchanged. **Decision:
footer is mechanically appended (deterministic), prose is agent-authored.** This is
consistent with "agent writes verbatim" because the footer adds links/emoji, never
prose.

**Open Question #2 resolution — `classify_output()` fate:**
`classify_output` is orphaned for routing (verified: no production caller; only its
internal `_delegate_to_promise_gate` reaches `bridge/promise_gate.py` for an audit
trail). The empty-promise detection that the *validator surface* needs already lives in
`_detect_empty_promise` (deterministic, in `bridge/promise_gate.py`) and is independent
of `classify_output`. **Decision: delete `classify_output` and its entire classification cluster**
(`ClassificationResult`, `OutputType`, `CLASSIFIER_SYSTEM_PROMPT`, the heuristic/parse/
confidence-gate helpers, `_delegate_to_promise_gate`, and the classification audit
helpers — the builder removes the full transitive set) — *contingent on
confirming `bridge/promise_gate.py` does not depend on the drafter-delegation path for
a live verdict* (it derives its own verdict; the delegation only avoids a double Haiku
charge that no longer exists once the drafter stops calling Haiku). If promise_gate
turns out to need the delegation, downgrade to: keep `_detect_empty_promise` usage,
delete only the LLM classification body, and make `classify_output` a thin deterministic
heuristic wrapper. **Preferred: full deletion (deterministic heuristics already exist
elsewhere).** This is the single largest line-removal and must be confirmed by the
builder against `bridge/promise_gate.py` and `tests/unit/test_cross_wire_fixes.py` /
`tests/integration/test_message_drafter_integration.py::test_classify_output_real_api`
before deletion.

**Open Question #3 resolution — all four call sites:**
None depend on the regenerated `response` text beyond `.text` (which is now the raw
agent text). Confirmed reads:
- `agent/output_handler.py:371` — uses `.text`, `.full_output_file`, `.needs_self_draft`,
  `.violations`, and persists `.context_summary`/`.expectations` only when
  `was_drafted` (line 404). **Change:** remove the `was_drafted` gate (which is being
  deleted), but **keep persisting BOTH `.context_summary` and `.expectations`** via
  `_persist_routing_fields` — the persistence call must now fire on the pass-through
  path, not only the old "drafted" path, or the four routing readers silently degrade
  (see Blocker-1 resolution). The persist gate condition changes from
  `getattr(draft, "was_drafted", False)` to `session is not None` (persist whenever we
  have a draft and a session). `_persist_routing_fields` already writes
  `context_summary` only when truthy and `expectations` only when `is not None`, so the
  None-vs-empty contract below is honored unchanged. Keep the `needs_self_draft` →
  `_inject_self_draft_steering` path (now the primary path, not just the all-backends-
  failed path).

**CONCERN resolution — `expectations` None-vs-empty-string contract + recall parity:**
The war room noted that demoting Haiku to deterministic-only for `expectations` is a
behavioral change, not a no-op "retention" — today Haiku's `structured.expectations`
takes priority and `_extract_open_questions` is only the fallback
(`message_drafter.py:1865-1869`). Removing Haiku makes `_extract_open_questions` the
**sole** source. Two tightening requirements:
- **Explicit None-vs-empty contract:** `MessageDraft.expectations` is `str | None`.
  When `_extract_open_questions` finds questions, `expectations` is a non-empty
  `>>`-joined string. When it finds none, `expectations` is **`None`** (never `""`).
  `_persist_routing_fields` persists `expectations` exactly when `is not None`, so an
  empty extraction must yield `None` and leave the prior persisted value untouched —
  it must NOT clobber a session's existing `expectations` with an empty string. This is
  the persist gate's contract and the deterministic path must respect it.
- **Recall-parity test:** add a test asserting that a representative output which
  previously produced Haiku `expectations` now produces equivalent `expectations` from
  `_extract_open_questions` on the same raw text (the `## Open Questions` / trailing-`?`
  extraction covers the real cases), and that a declarative-only output yields `None`
  (no fabricated questions). See Test Impact.
- `agent/hooks/stop.py:150` — uses `.text` + `.violations`. No change beyond `.text`
  now being raw.
- `tools/send_telegram.py:91` — uses `.text` only. No change.
- `bridge/email_bridge.py:578` — uses `.text` only. No change.

**Open Question #4 resolution — granite layer:** Out of scope. Filed as **#1681** (see
No-Gos).

**`needs_self_draft` promotion:** the existing `_inject_self_draft_steering`
(`agent/output_handler.py`) + `SELF_DRAFT_INSTRUCTION` + `STEERING_DEFERRED` machinery
is reused. `SELF_DRAFT_INSTRUCTION` text should be updated to reflect "your
message was flagged for <reason>; rewrite it yourself" rather than "could not be
drafted by the automated drafter."

**Blocker-2 resolution — bound sequential re-draft attempts, not just concurrent ones
(war-room critique):** The existing loop guard in `_inject_self_draft_steering`
(`agent/output_handler.py:730`) checks `peek_steering_sender(session_id) ==
"drafter-fallback"`, which only caps **pending/concurrent** injections. Once the agent
consumes the nudge at a turn boundary, the steering queue empties and the guard resets.
A **structurally-unfixable** flag — a persona that always emits a markdown table, or
output that strips to empty after narration removal — therefore yields an unbounded
`flag → inject → consume → re-emit → flag …` loop that never terminates and never
delivers.

**Decision: bound the loop with an ATOMIC per-session re-draft attempt counter stored as
a dedicated Redis key — NOT a field on the `AgentSession` hash.** The second war-room pass
found that the original "field + read-modify-write + `session.save()`" design re-introduces
two defects (see the New-Blocker resolution immediately below); the atomic-key design
avoids both by construction.

- **Storage:** a dedicated Redis key `steering:attempts:{session_id}` managed through
  `agent/steering.py` (which already owns the `steering:{session_id}` namespace via
  `_get_redis()` → `POPOTO_REDIS_DB`). The counter is **NOT** added to
  `models/agent_session.py` and **NOT** registered in `_AGENT_SESSION_FIELDS`. Keeping it
  out of the `AgentSession` hash means there is no `session.save()` on the steering path at
  all, so the full-hash clobber of `context_summary`/`expectations` cannot happen.
- **Atomic increment-and-branch:** add two helpers to `agent/steering.py`:
  `bump_self_draft_attempts(session_id) -> int` (calls `r.incr(key)`, sets a TTL via
  `r.expire(key, <e.g. 1 hour>)` on first bump so abandoned sessions don't leak keys, and
  returns the **post-increment** value) and `reset_self_draft_attempts(session_id)` (calls
  `r.delete(key)`). `INCR` is a single atomic server-side op: two concurrent flagged
  `send()` coroutines for the same session get distinct return values (1 and 2), so neither
  under-counts and the cap genuinely bounds the loop.
- **Branch in `_inject_self_draft_steering`:** call `bump_self_draft_attempts(session_id)`
  and branch on its returned value. If the returned count is `> SELF_DRAFT_MAX_ATTEMPTS`
  (constant = 2), log a warning, return `False` (so the caller applies
  `_apply_narration_fallback` and delivers via file/narration), and do NOT push another
  nudge. Otherwise push the nudge and return `True`. Because the bump is the gate, the
  read and the increment are the same atomic op — there is no read-then-increment window.
- **Reset semantics + PINNED location (supporting concern #1):** the counter is reset via
  `reset_self_draft_attempts(session_id)` on the first successful (un-flagged) delivery for
  that session. **The reset MUST fire on the `not needs_self_draft` clean branch, BEFORE
  the `steering_deferred` early-return at `agent/output_handler.py:417-424` — NOT at the
  terminal outbox `rpush`.** The redundancy filter and RTR-suppress paths return early
  (around `:480-499`), so a reset pinned to the terminal `rpush` is unreachable whenever a
  clean send is suppressed as redundant or RTR-deferred; a session that produces a clean
  (but suppressed) send would then never reset and would burn its self-draft budget
  permanently. Pin the reset to the point where the code has decided the output is NOT
  flagged (`needs_self_draft` is falsy) and `session is not None`, i.e. immediately after
  the `needs_self_draft` block and before the `steering_deferred` early-return — that point
  is reached on every non-flagged path, suppressed or not.
- The existing `peek_steering_sender` concurrent guard is **retained** — it still prevents
  double *injection* (two pending nudges) from two near-simultaneous flagged outputs. The
  two guards are complementary and address different things: `peek` gates whether a second
  steering *message* is pushed; the atomic `steering:attempts` counter bounds the
  **sequential** loop across turn boundaries (where `peek` resets) AND is the
  read-modify-write-safe counter under concurrency (where the old in-memory `+= 1; save()`
  raced). `peek` does NOT impose read-modify-write ordering on the counter, so it cannot
  substitute for the atomic increment.

**New-Blocker resolution — `self_draft_attempts` TOCTOU + full-hash `save()` clobber
(second war-room pass):** The critique found that the *original* Blocker-2 fix (a
`self_draft_attempts` int field on `AgentSession`, read-incremented-saved in
`_inject_self_draft_steering`) created a fresh blocker that the atomic-key design above
fixes:
- **TOCTOU under-count.** `send()` operates on a stale in-memory `session` it never
  re-reads from Redis. Under two near-simultaneous flagged outputs for the same session
  (the plan's own Race 2 premise), both read `attempts == 0`, both `+= 1` to `1`, both
  save `1`: the counter under-counts and the sequential cap silently fails to bound the
  loop. `peek_steering_sender` does NOT serialize the read-modify-write — it only gates a
  second *push*. **Fix:** the atomic `INCR` returns distinct post-increment values to the
  two coroutines; there is no read-then-write window.
- **Full-hash `save()` clobber.** A bare `session.save()` is a full-hash write that
  re-introduces exactly the `context_summary`/`expectations` clobber that
  `record_recent_sent_draft` (`agent/output_handler.py:689-690`,
  `models/agent_session.py:1755`) deliberately avoids via
  `update_fields=["recent_sent_drafts", "updated_at"]` — softly reopening Blocker-1's
  routing regression. **Fix:** the counter is not an `AgentSession` field, so the steering
  path performs **no** `session.save()` at all; nothing on this path can clobber the
  routing fields. (If a future maintainer insists on a persisted field instead of the
  Redis key, the field write MUST use `session.save(update_fields=["self_draft_attempts",
  "updated_at"])` and the increment MUST still be made race-safe — but the atomic Redis key
  is the chosen design precisely because it sidesteps both requirements.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `try/except` around `_write_full_output_file` (`message_drafter.py:1831-1834`)
  is retained — test asserts a `logger.warning` fires and delivery proceeds with the
  raw text when file write fails.
- [ ] The session-refresh `try/except` in `_compose_structured_draft` (:1717-1726) is
  retained — already covered; verify it stays green.
- [ ] No new bare `except Exception: pass` blocks introduced (this is a removal).

### Empty/Invalid Input Handling
- [ ] Empty/whitespace `raw_response`: `draft_message` already early-returns
  (`message_drafter.py:1794`). Test asserts it returns `MessageDraft(text="")` (or SDLC
  empty-progress render) and does **not** trigger a self-draft loop.
- [ ] None session: validators run, raw text passes through, emoji defaults apply.
- [ ] Verify the **concurrent** steering guard: `_inject_self_draft_steering` guards
  against re-injecting when a `"drafter-fallback"` sender is already pending
  (`peek_steering_sender`). Test asserts a second flagged draft while one is still
  pending does NOT push a second steering message.
- [ ] Verify the **sequential** steering bound (Blocker-2): simulate a structurally-
  unfixable flag across multiple turns (consume the nudge between each, so
  `peek_steering_sender` resets). Test asserts that after `SELF_DRAFT_MAX_ATTEMPTS` (2)
  injections, the next call returns `False`, pushes NO further steering, and falls
  through to `_apply_narration_fallback` / file delivery — i.e. the loop terminates.
  Assert the atomic `steering:attempts:{session_id}` Redis counter is capped.
- [ ] Verify the **concurrency safety** of the bound (Race 3 / New Blocker): spawn **two
  concurrent flagged `send()` coroutines** for the same session (via `asyncio.gather`)
  and assert the atomic counter reaches the true count (no lost increment / TOCTOU
  under-count) and the loop still terminates — a sequential-only test would pass while
  the race ships. Also assert NO `session.save()` (full-hash write) is issued on the
  steering path, so `context_summary`/`expectations` are not clobbered.
- [ ] Verify the counter **reset is pinned correctly** (supporting concern #1): after a
  clean (un-flagged) delivery the counter is reset to 0; AND the reset fires on a clean
  delivery that is **suppressed by the redundancy/RTR filter** (which returns early before
  the terminal `rpush`) — proving the reset is pinned to the `not needs_self_draft` branch
  before the `steering_deferred` early-return, not to the unreachable terminal `rpush`. A
  session whose clean send is suppressed must NOT burn its self-draft budget permanently.

### Error State Rendering
- [ ] Over-length output: test asserts `full_output_file` is set and the user-visible
  message references the attachment rather than silently truncating.
- [ ] Wire-format violation (markdown table in Telegram): test asserts the violation is
  surfaced (stop-hook warning via `format_violations`) and/or routes a steering nudge —
  never a silent server-side fix.

## Test Impact

Disposition for every affected test (verified against the test surface):

**DELETE — test the removed rewrite path with no salvageable assertion:**
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_long_response_still_uses_drafter` — DELETE
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_long_response_calls_haiku` — DELETE
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_haiku_fails_falls_back_to_openrouter` — DELETE
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_all_backends_fail_requests_self_summary` — DELETE (the "all backends failed" trigger is gone; replace with a "flag fires → needs_self_draft" test below)
- [ ] `tests/unit/test_message_drafter.py::TestQuestionFabricationPrevention::*` (7 tests) — DELETE: they patch `_draft_with_haiku` and assert Haiku-sourced `expectations`. Replace the still-relevant behavior (questions extracted verbatim, declaratives not turned into questions) with tests against `_extract_open_questions` directly.
- [ ] `tests/unit/test_message_drafter.py::TestQuestionFabricationIntegration::*` (3 tests) — DELETE (real Haiku calls)
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessageIntegration::test_real_haiku_summarization` — DELETE
- [ ] `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_routing_fields_persisted_on_successful_draft` — REPLACE (not delete): the `was_drafted` gate is gone, but routing-field persistence is RETAINED on the pass-through path. Rewrite to assert BOTH `context_summary` (deterministic source) and `expectations` persist when `session is not None`, and that an empty `expectations` (None) does NOT clobber a pre-existing value.
- [ ] `tests/integration/test_worker_pm_long_output.py` — DELETE or REPLACE: file attachment tied to LLM summarization; rewrite to assert over-length attaches a file on the verbatim path.
- [ ] `tests/integration/test_message_drafter_integration.py::test_classify_output_real_api` — DELETE **if** `classify_output` is removed (OQ#2 full-deletion path); otherwise keep.

**REPLACE / UPDATE — assert verbatim pass-through + flagging:**
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_short_response_skips_drafter` — UPDATE: assert raw text returned verbatim, `was_drafted` field gone.
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_long_response_creates_file` — REPLACE: long output now passes through verbatim AND attaches file (no Haiku mock).
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage::test_mid_length_response_no_file` — UPDATE: drop Haiku mock; assert verbatim, no file.
- [ ] `tests/unit/test_message_drafter.py::TestComposeStructuredDraft::*` (6 tests) — UPDATE: remove LLM expectations from setup; composition now operates on agent bullets.
- [ ] `tests/integration/test_message_drafter_integration.py::test_response_summarizer_wiring` — UPDATE: assert pass-through (`MessageDraft.text == raw`, no rewrite).
- [ ] `tests/unit/test_output_handler.py::TestDrafterInHandler::test_send_invokes_draft_message` — UPDATE: assert pass-through delivery.
- [ ] `tests/unit/test_output_handler.py::TestDrafterInHandler::test_send_includes_file_paths_when_drafter_returns_file` — UPDATE: file attach on verbatim path.
- [ ] `tests/unit/test_output_handler.py::TestDrafterInHandler::test_send_falls_back_to_raw_text_on_drafter_exception` — UPDATE: exception path now applies to validators, not LLM calls.
- [ ] `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_needs_self_draft_pushes_steering_and_defers_outbox_write` — UPDATE/KEEP: steering is now primary; assert a flag (not backend failure) triggers it.
- [ ] `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_needs_self_draft_skips_steering_if_already_pending` — KEEP (concurrent `peek_steering_sender` guard still required).
- [ ] **NEW** `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_self_draft_attempts_bound_terminates_loop` — ADD: must spawn **TWO CONCURRENT flagged `send()` coroutines** for the same session (`asyncio.gather`), NOT a sequential loop — a sequential-only test passes while the TOCTOU race ships. Assert: the atomic `steering:attempts:{session_id}` Redis counter reaches the true count under concurrency (no lost increment); after `SELF_DRAFT_MAX_ATTEMPTS` the next call returns `False`, pushes no further steering, falls through to narration fallback; and NO full-hash `session.save()` is issued on the steering path (so routing fields are not clobbered — Race 3 / New Blocker).
- [ ] **NEW** `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_self_draft_attempts_reset_pinned_before_early_return` — ADD: the counter reset must fire on the `not needs_self_draft` clean branch BEFORE the `steering_deferred` early-return — assert reset on a normal clean delivery AND on a clean delivery that the redundancy/RTR filter suppresses (early-returns before the terminal `rpush`). A clean-but-suppressed send must reset the budget; pinning the reset to the terminal `rpush` (unreachable on the suppress path) would fail this test (supporting concern #1).
- [ ] **NEW** `tests/unit/test_steering.py::test_self_draft_attempts_atomic_increment` — ADD: `bump_self_draft_attempts` returns distinct post-increment values under concurrent calls; TTL is set on first bump; `reset_self_draft_attempts` deletes the key.
- [ ] **NEW** `tests/unit/test_output_handler.py::TestDrafterInHandler::test_routing_fields_persisted_on_passthrough` — ADD: `context_summary` (deterministic) + `expectations` persist on the verbatim path; None `expectations` does not overwrite a prior value.
- [ ] `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_narration_fallback_*` (2 tests) — UPDATE: narration fallback path retained for the non-steering case.
- [ ] `tests/unit/test_output_handler.py::TestDrafterFailureRecovery::test_routing_field_persistence_failure_is_silent` / `test_routing_fields_not_persisted_when_draft_skipped` — UPDATE for the new persistence gate.
- [ ] `tests/unit/test_open_question_gate.py::TestSummarizeResponseOpenQuestions::*` — UPDATE: `expectations` now sourced from `_extract_open_questions` on raw text, not Haiku; drop `_draft_with_haiku` patches.
- [ ] **NEW** `tests/unit/test_message_drafter.py::TestExpectationsRecallParity::*` — ADD (CONCERN): a representative output that previously produced Haiku `expectations` now produces equivalent `expectations` from `_extract_open_questions` on the same raw text; a declarative-only output yields `None` (no fabrication); assert the None-vs-empty contract (`expectations` is `None`, never `""`, when no questions found).
- [ ] **NEW** `tests/unit/test_message_drafter.py::TestDeriveContextSummary::*` — ADD (Blocker-1): `_derive_context_summary` returns a deterministic first-sentence summary (capped), `None` for empty/whitespace input, and strips narration; `draft_message` populates `MessageDraft.context_summary` from it on the pass-through path.
- [ ] **NEW** `tests/unit/test_message_drafter.py::TestDeriveContextSummaryRecallParity::*` — ADD (supporting concern #2): a **recall-parity** test (parallel to `TestExpectationsRecallParity`, not a mere existence test) that VALIDATES the "strictly better than empty" claim rather than asserting it. For ≥3 representative agent outputs (a code-task reply, a question-bearing reply, a multi-paragraph status reply), assert the deterministic `_derive_context_summary` produces a routing-usable topic hint: non-empty, ≤ the cap, drawn from the agent's own text, and meaningfully distinguishing one session's topic from another's (e.g. two different-topic outputs yield different summaries). Explicitly acknowledge in the test docstring that a first-sentence slice is often NOT the session topic the interpretive Haiku source produced — the assertion is "strictly better than the `(no context)` fallback the readers or-guard to," NOT "equivalent to Haiku." If the first-sentence heuristic fails the distinguishing assertion for a realistic case, that is a signal to widen the heuristic (e.g. first N sentences) before build, not to ship a worse summary than empty.
- [ ] `tests/unit/test_send_telegram.py` (long-text draft test ~line 802) — REPLACE: expect pass-through, not rewrite.

**SAFE — no change (use drafter-bypass fixture or test composition/validation only):**
- [ ] `tests/unit/test_drafter_validators.py::*` — SAFE (already exercise the short-circuit/validator path; verbatim is now the norm).
- [ ] `tests/unit/test_message_drafter_chat_log.py::*` — SAFE only if `_build_draft_prompt` is retained; **if `_build_draft_prompt` is deleted, DELETE these** (they test the rewrite prompt). Builder decides based on final deletion set.
- [ ] `tests/unit/test_drafter_medium_split.py::*` — tests `BASE_DRAFTER_PROMPT + MEDIUM_RULES`; **DELETE if the rewrite system prompt is removed**, else SAFE.
- [ ] `tests/integration/test_message_drafter_integration.py` RTR + redundancy tests — SAFE (drafter-bypass fixture).
- [ ] `tests/unit/test_output_handler.py` RTR/TransportAware/Redundancy wiring — SAFE.
- [ ] `tests/integration/test_agent_session_lifecycle.py`, `test_connectivity_gaps.py` (compose tests) — SAFE.
- [ ] `tests/unit/test_cross_wire_fixes.py` (classify_output tests) — DELETE if `classify_output` removed (OQ#2), else SAFE.
- [ ] `tests/unit/test_tool_call_delivery.py` — VERIFY: confirm it patches `draft_message` as pass-through; UPDATE only if it asserts rewrite.

## Rabbit Holes

- **Rewriting the validators.** They are already good. Do not "improve" the markdown-table
  regex or add new wire-format rules — that is a separate concern.
- **Building a deterministic re-implementation of Haiku's summarization.** The whole
  point is to stop summarizing the **user-facing message**. Do not replace Haiku with a
  regex/heuristic "summarizer" for the delivered prose. **Carve-out:** the small
  `_derive_context_summary` helper (a first-sentence slice for the internal *routing*
  `context_summary` field) is explicitly in scope — it is a coarse routing hint, never
  user-facing prose, and exists only so the four routing readers stay fed (Blocker-1). It
  must stay deliberately dumb (string slicing, capped); do not grow it into an
  LLM-or-NLP-backed summarizer.
- **Touching the granite layer.** Tracked in #1681. Stay in `bridge/message_drafter.py`
  + the four call sites + tests + docs.
- **Re-architecting the steering mechanism.** `_inject_self_draft_steering` already
  exists and works; reuse it. Do not build a new nudge channel.
- **Read-the-Room / redundancy filter.** Downstream of the drafter, bypass-fixtured in
  tests, unaffected. Do not refactor it.
- **`expectations` / `context_summary` / session-routing semantics.** Keep
  `_extract_open_questions` verbatim extraction and the deterministic
  `_derive_context_summary` helper; **do not** touch or redesign
  `bridge/session_router.py`, `bridge/telegram_bridge.py`, or
  `agent/session_executor.py`. The retained deterministic `context_summary` source keeps
  all four routing readers working without any reader-site change — that is the entire
  point of choosing option (a) over migrating the readers.

## Risks

### Risk 1: Removing `classify_output` breaks `bridge/promise_gate.py` delegation
**Impact:** The promise gate's audit trail (or, worse, a live verdict) could lose its
classifier input, changing pause/continue behavior.
**Mitigation:** Builder confirms `evaluate_promise` derives its own verdict and the
drafter-delegation is audit-only before deleting. If it is load-bearing, downgrade to
the "thin deterministic wrapper" fallback in Technical Approach. Gate the deletion on
`tests/integration/test_message_drafter_integration.py::test_classify_output_real_api`
and `tests/unit/test_cross_wire_fixes.py` passing/updated.

### Risk 2: Over-length output now loops on steering instead of delivering
**Impact:** A genuinely long, legitimate message could ping-pong (flag → nudge → agent
re-emits long → flag …) and never deliver.
**Mitigation:** Over-length is handled by **file attachment**, not by a blocking flag —
the message delivers with a `.txt` attachment and a short pointer. Reserve `needs_self_draft`
for wire-format/empty-promise flags, which the agent can actually fix. Two guards bound
the loop: the `peek_steering_sender` guard caps **concurrent** re-injection at one pending
nudge, and the new **atomic Redis counter** `steering:attempts:{session_id}` (cap =
`SELF_DRAFT_MAX_ATTEMPTS` = 2, incremented via `INCR` so concurrent flagged outputs cannot
under-count) caps **sequential** re-violations across turn boundaries — a
structurally-unfixable flag abandons self-draft and falls through to
narration-fallback/file delivery rather than looping forever (Blocker-2 + New-Blocker
resolution; the counter is off the `AgentSession` hash so it issues no full-hash `save()`).

### Risk 3: Teammate/PM voice regressions are subjective and untested by unit tests
**Impact:** "Verbatim" could surface raw narration or formatting the old rewrite hid.
**Mitigation:** `_strip_process_narration` still runs. Add an integration assertion that
a representative Opus teammate reply passes through byte-identical (modulo narration
stripping). The subjective UX side is no longer a buried footnote — it is promoted to a
**checkable Success Criterion** (the user-outcome / message-quality criterion: verbatim
reply runs LONGER than the Haiku-condensed one in the common sub-3000-char case) that must
be run with a proof artifact during the review round.

## Race Conditions

### Race 1: Session re-read in `_compose_structured_draft` vs. concurrent stage write
**Location:** `bridge/message_drafter.py:1717-1726`
**Trigger:** `session_progress.py` writes `[stage]` entries / link URLs while the
drafter composes.
**Data prerequisite:** stage data + link URLs must be in Redis before linkify/footer
composition reads them.
**State prerequisite:** the re-read fetches the freshest `AgentSession`.
**Mitigation:** Already handled — `_compose_structured_draft` re-reads the session from
Redis (`AgentSession.query.filter(session_id=...)`) immediately before composing. This
plan does not change that path; the re-read stays.

### Race 2: Double steering injection from concurrent flagged drafts
**Location:** `agent/output_handler.py::_inject_self_draft_steering`
**Trigger:** two near-simultaneous flagged outputs for the same session.
**Data prerequisite:** the pending-steering inbox must reflect the first injection
before the second checks it.
**State prerequisite:** `peek_steering_sender` must see the first `"drafter-fallback"`
message.
**Mitigation:** Existing `peek_steering_sender == "drafter-fallback"` guard returns
`False` (skips) on the second injection. No new race introduced; test asserts the guard.

### Race 3: Self-draft attempt counter under-count / routing-field clobber
**Location:** `agent/output_handler.py::_inject_self_draft_steering` + `agent/steering.py`
**Trigger:** two near-simultaneous flagged `send()` coroutines for the same session (the
same concurrency premise as Race 2), each trying to bound the self-draft loop.
**Data prerequisite:** the attempt counter must reflect BOTH increments before either
coroutine reads it to decide whether the cap is hit.
**State prerequisite:** the increment must be a single atomic read-modify-write; and it
must NOT be carried on the `AgentSession` hash (a full-hash `save()` would clobber the
deterministically-persisted `context_summary`/`expectations` from Blocker-1).
**Hazard if naive:** a Python `session.self_draft_attempts += 1; session.save()` on a
stale in-memory session loses one of the two increments (TOCTOU under-count → cap never
binds → unbounded loop) AND the full-hash `save()` overwrites the routing fields that
`record_recent_sent_draft` protects with `update_fields=`.
**Mitigation:** the counter is a dedicated Redis key `steering:attempts:{session_id}`
incremented with atomic `INCR` (returns the post-increment value; concurrent callers get
distinct values), with a TTL on first bump and `DELETE` for reset. It is **not** an
`AgentSession` field, so the steering path issues no `session.save()` and cannot clobber
the routing fields. Test asserts the bound holds under **two concurrent** flagged `send()`
coroutines (see `test_self_draft_attempts_bound_terminates_loop` in Test Impact).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1681] The granite PTY operator
  (`agent/granite_container/granite_classifier.py`) LLM-rewrites the user-facing reply
  via `granite4.1:3b` before it reaches the drafter — a second laundering layer. Filed
  as #1681. This plan is scoped to `bridge/message_drafter.py` and its four call sites
  only.
- Nothing else deferred — every other relevant item (all four call sites, validators,
  steering promotion, tests, docs) is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the bridge/agent
runtime. No new dependencies (it removes the Anthropic/OpenRouter rewrite calls from the
hot path), no new config files, no new CLI entry points, no machine-propagation steps.
`scripts/remote-update.sh` and `.claude/skills/update/` need no changes. If the bridge
or worker is running, restart after merge per the standard
`./scripts/valor-service.sh restart` rule (bridge/agent code changed).

## Agent Integration

No new agent integration required — this is a bridge-internal change to an existing
post-processing path. The agent reaches the drafter implicitly through the output
handler (`agent/output_handler.py`) on every turn; no new CLI entry point in
`pyproject.toml [project.scripts]` and no `.mcp.json` change. The behavioral change the
agent will *experience* is the steering nudge (`SELF_DRAFT_INSTRUCTION`) when its output
is flagged — that path already exists (`_inject_self_draft_steering`) and is exercised
by `tests/unit/test_output_handler.py`. Update `SELF_DRAFT_INSTRUCTION` text to describe
the specific flag reason. Integration test
(`tests/integration/test_message_drafter_integration.py`) verifies the end-to-end
verbatim-pass-through + flag-routes-steering behavior.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/message-drafter.md` — rewrite the "what it does" framing
  from "summarizes/rewrites via Haiku" to "validates and passes through verbatim;
  flags route a steering nudge back to the authoring agent." Remove the
  `structured_draft` / Haiku / OpenRouter sections; document the validator surface and
  the steering-first flagging.
- [ ] Update `docs/features/agent-message-delivery.md` — update the delivery-path
  description to reflect verbatim pass-through and the promoted `needs_self_draft`
  steering path; remove references to server-side rewrite.
- [ ] Verify `docs/features/README.md` index entries for both docs are still accurate.

### Inline Documentation
- [ ] Update the `draft_message` docstring (`bridge/message_drafter.py:1770`) — remove
  the "Uses structured tool_use output… Fallback chain: Haiku → OpenRouter" paragraph;
  describe pass-through + validation + steering.
- [ ] Update `SELF_DRAFT_INSTRUCTION` text and its comment.
- [ ] Update `MessageDraft` docstring: `was_drafted` removed; `context_summary` now
  deterministic; `expectations` None-vs-empty contract documented.
- [ ] Document the atomic self-draft attempt counter: the
  `bump_self_draft_attempts`/`reset_self_draft_attempts` helpers and the
  `steering:attempts:{session_id}` Redis key in `agent/steering.py` (docstrings), the
  `SELF_DRAFT_MAX_ATTEMPTS` constant, and the `_derive_context_summary` helper
  (docstring). Note explicitly that the counter is a Redis key (NOT an `AgentSession`
  field) so the steering path issues no `session.save()`.
- [ ] In `docs/features/message-drafter.md` / `agent-message-delivery.md`, document the
  bounded sequential self-draft loop and the deterministic routing-field sources.

## Success Criteria

- [ ] The Opus driving session's message text reaches the human **verbatim** (modulo
  process-narration stripping) for teammate and PM-driver output. No Haiku/OpenRouter
  rewrite of the `response` text remains in the delivery path.
- [ ] The drafter still **flags** (not fixes): over-length (file attachment), wire-format
  violations, empty/false promises, process narration.
- [ ] Flagged problems route a **steering nudge** back to the authoring agent via the
  promoted `needs_self_draft` → `_inject_self_draft_steering` path; the validator never
  substitutes its own prose.
- [ ] **Sequential self-draft loop is bounded (Blocker-2):** a structurally-unfixable
  flag abandons self-draft after `SELF_DRAFT_MAX_ATTEMPTS` (2) persisted attempts and
  falls through to narration-fallback/file delivery — verified by
  `test_self_draft_attempts_bound_terminates_loop`.
- [ ] **`context_summary` routing readers stay fed (Blocker-1):** the persisted
  `session.context_summary` is populated deterministically (`_derive_context_summary`),
  the four reader sites are untouched, and `_persist_routing_fields` still fires on the
  pass-through path — verified by `test_routing_fields_persisted_on_passthrough`.
- [ ] **Net-negative diff** in `bridge/message_drafter.py`: `_draft_with_haiku`,
  `_draft_with_openrouter`, `STRUCTURED_DRAFT_TOOL`, and the rewrite system prompt are
  deleted; the PR removes more lines than it adds in that file
  (`git show --stat` shows deletions > insertions for `bridge/message_drafter.py`).
- [ ] All four `draft_message()` call sites updated and green
  (`agent/output_handler.py`, `agent/hooks/stop.py`, `tools/send_telegram.py`,
  `bridge/email_bridge.py`).
- [ ] Drafter tests updated to assert **verbatim pass-through + flagging**, not
  rewriting (see Test Impact for the full disposition).
- [ ] **User-outcome / message-quality criterion (supporting concern #3):** message
  quality measurably *improves*, not merely changes byte-for-byte. The named, checkable
  behavior change: for the common sub-3000-char teammate/PM reply, the delivered message
  is the **verbatim Opus text and runs LONGER** than the Haiku-condensed version it
  replaces (Haiku's rewrite systematically shortens/strips). Verify by a **manual UX
  smoke** in a local session: send a representative conversational prompt, confirm the
  delivered reply is the agent's own words (not a condensed paraphrase) and is at least as
  long/detailed as the prior Haiku output for the same input. Capture the before/after
  pair (or a single after-sample plus a note that it is no longer Haiku-shaped) as a
  review-round proof artifact. This is an executable success criterion, not a Risk
  footnote — it must be actually run and the artifact attached, not flipped to `[x]` on
  the basis of byte-identity/grep/diff checks alone.
- [ ] Open Question #2 resolved in code: `classify_output` either fully deleted or
  reduced to a deterministic wrapper, with `bridge/promise_gate.py` confirmed unaffected.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -c "_draft_with_haiku\|_draft_with_openrouter\|STRUCTURED_DRAFT_TOOL" bridge/message_drafter.py` returns 0.

## Team Orchestration

### Team Members

- **Builder (drafter-core)**
  - Name: `drafter-builder`
  - Role: Remove the rewrite machinery in `bridge/message_drafter.py`; make
    `draft_message` a verbatim pass-through + validation filter; resolve OQ#1/#2 in code.
  - Agent Type: builder
  - Resume: true

- **Builder (call-sites)**
  - Name: `callsite-builder`
  - Role: Update the four `draft_message` call sites and promote the `needs_self_draft`
    steering path in `agent/output_handler.py`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (drafter-tests)**
  - Name: `drafter-test-engineer`
  - Role: Apply the Test Impact dispositions — delete/replace/update per the table.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `drafter-documentarian`
  - Role: Update `docs/features/message-drafter.md`, `agent-message-delivery.md`,
    docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `drafter-validator`
  - Role: Verify net-negative diff, all four call sites green, verbatim pass-through,
    steering-first flagging, promise_gate unaffected.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Confirm `classify_output` / promise_gate coupling (decision gate)
- **Task ID**: spike-classify-coupling
- **Depends On**: none
- **Assigned To**: drafter-builder
- **Agent Type**: builder
- **Parallel**: false
- Read `bridge/promise_gate.py::evaluate_promise` and confirm whether the
  drafter-delegation path supplies a live verdict or audit-only data.
- Decide: full deletion of `classify_output` (preferred) vs. thin deterministic wrapper.
- Record the decision inline before deleting anything.

### 2. Remove rewrite machinery, make drafter pass-through
- **Task ID**: build-drafter-core
- **Depends On**: spike-classify-coupling
- **Validates**: tests/unit/test_message_drafter.py, tests/unit/test_drafter_validators.py
- **Assigned To**: drafter-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `_draft_with_haiku`, `_draft_with_openrouter`, `STRUCTURED_DRAFT_TOOL`,
  `StructuredDraft`, `_build_draft_prompt`, the rewrite system prompt
  (`DRAFTER_SYSTEM_PROMPT`/`BASE_DRAFTER_PROMPT`/`MEDIUM_RULES` as applicable), and the
  `was_drafted` field.
- Rewrite `draft_message` to: strip narration → run `_validate_for_medium` on raw text
  → write full-output file if over `FILE_ATTACH_THRESHOLD` → return verbatim text (with
  deterministic emoji/linkify/footer composition) OR `needs_self_draft=True` on a
  blocking flag.
- Retain `_extract_open_questions` → `expectations` (sole source now; honor the
  None-vs-empty contract — `None` when no questions, never `""`).
- **Retain `context_summary`** on `MessageDraft`, sourced from a new deterministic
  `_derive_context_summary(raw_text)` helper (first non-narration sentence, capped),
  NOT from Haiku. The field and persistence path stay; only the source flips. This keeps
  the four live routing readers working (Blocker-1).
- Per the gate decision, delete or thin-wrap `classify_output` and its helpers.
- Remove now-unused imports (`MODEL_FAST` etc.) where applicable.

### 3. Update the four call sites + promote steering
- **Task ID**: build-call-sites
- **Depends On**: build-drafter-core
- **Validates**: tests/unit/test_output_handler.py, tests/unit/test_send_telegram.py
- **Assigned To**: callsite-builder
- **Agent Type**: builder
- **Parallel**: false
- `agent/output_handler.py`: remove the `was_drafted` persistence gate; persist BOTH
  `context_summary` and `expectations` via `_persist_routing_fields` on the pass-through
  path (gate becomes `session is not None`); keep/promote `needs_self_draft` →
  `_inject_self_draft_steering` as the primary flag handler.
- **Blocker-2 + New Blocker (atomic, no AgentSession field):** add
  `bump_self_draft_attempts(session_id) -> int` (atomic `r.incr` + TTL on first bump,
  returns post-increment value) and `reset_self_draft_attempts(session_id)` (`r.delete`)
  to `agent/steering.py`, keyed `steering:attempts:{session_id}`. Do NOT add a field to
  `models/agent_session.py` or `_AGENT_SESSION_FIELDS` — the counter stays out of the
  hash so the steering path issues no `session.save()` (no routing-field clobber). Add a
  `SELF_DRAFT_MAX_ATTEMPTS = 2` constant. In `_inject_self_draft_steering`, call
  `bump_self_draft_attempts` and branch on its return: if `> SELF_DRAFT_MAX_ATTEMPTS`,
  return `False` (abandon to narration fallback) and push no nudge; else push and return
  `True`. **Reset:** call `reset_self_draft_attempts` on the `not needs_self_draft` clean
  branch BEFORE the `steering_deferred` early-return (`agent/output_handler.py:417-424`),
  so a clean-but-redundancy/RTR-suppressed send still resets the budget — NOT at the
  terminal `rpush` (unreachable on suppress paths).
- `agent/hooks/stop.py`, `tools/send_telegram.py`, `bridge/email_bridge.py`: confirm
  `.text` consumption still correct against raw text.
- Update `SELF_DRAFT_INSTRUCTION` to describe the flag reason.

### 4. Apply Test Impact dispositions
- **Task ID**: build-tests
- **Depends On**: build-call-sites
- **Validates**: full drafter + output_handler test set
- **Assigned To**: drafter-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Execute every DELETE/REPLACE/UPDATE/SAFE disposition from the Test Impact section.
- Add the new failure-path tests (bounded steering loop, over-length file attach,
  verbatim pass-through assertion).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: drafter-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/message-drafter.md`, `docs/features/agent-message-delivery.md`,
  the index, and docstrings per the Documentation section.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: drafter-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify net-negative diff in `bridge/message_drafter.py`.
- Verify all four call sites green and `grep` for removed symbols returns 0.
- Run the full drafter + output_handler test suites.
- Confirm `bridge/promise_gate.py` behavior unchanged.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_message_drafter.py tests/unit/test_drafter_validators.py tests/unit/test_output_handler.py -q` | exit code 0 |
| Rewrite machinery gone | `grep -c "_draft_with_haiku\|_draft_with_openrouter\|STRUCTURED_DRAFT_TOOL" bridge/message_drafter.py` | output contains 0 |
| Net-negative diff | `git show --stat HEAD -- bridge/message_drafter.py` | deletions > insertions |
| context_summary writer retained (Blocker-1) | `grep -c "_derive_context_summary" bridge/message_drafter.py` | output ≥ 1 (deterministic source exists) |
| context_summary persist GATE flipped (Blocker-1) | `grep -n "was_drafted" agent/output_handler.py` | the persist gate no longer reads `was_drafted` (gate is `session is not None`); checks the call-site gate flip, not the unchanged `_persist_routing_fields` body |
| Routing readers untouched (Blocker-1, No-Go) | `git diff --stat HEAD~1 -- bridge/session_router.py bridge/telegram_bridge.py agent/session_executor.py` | no changes to reader sites |
| Self-draft bound is ATOMIC + off the hash (Blocker-2 / New Blocker) | `grep -c "bump_self_draft_attempts\|reset_self_draft_attempts\|SELF_DRAFT_MAX_ATTEMPTS" agent/steering.py agent/output_handler.py` | output ≥ 2 |
| No AgentSession field / no full-hash save on steering path (New Blocker) | `grep -c "self_draft_attempts" models/agent_session.py` | output is 0 (counter is a Redis key, not an AgentSession field) |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Module imports | `python -c "import bridge.message_drafter, agent.output_handler, models.agent_session"` | exit code 0 |

## Critique Results

Two war-room passes. Pass 1 verdict: **NEEDS REVISION** (Blocker-1 context_summary
readers; Blocker-2 sequential loop). Pass 2 verdict: **NEEDS REVISION** — the Blocker-2
fix introduced one new blocker plus supporting concerns. All resolved below.

**Pass 1:**

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist/Adversary | Removing `context_summary`'s only writer silently breaks 4 live routing readers (`session_router.py:85`, `telegram_bridge.py:2001`, `session_executor.py:1979`, `telegram_bridge.py:779`); contradicts the No-Go. Verification gate inspected wrong file. | Technical Approach → Blocker-1 resolution; Verification table | Option (a): retain `context_summary` via deterministic `_derive_context_summary`; keep persist path; no reader touched. Verification gate now checks the writer + reader-untouched + deterministic source. |
| BLOCKER | Operator | `peek_steering_sender=="drafter-fallback"` guard bounds only concurrent injections; sequential re-violations loop unbounded after the agent consumes the nudge. | Technical Approach → Blocker-2 resolution; Failure Path Test Strategy; Test Impact | (superseded by Pass 2) original fix added a `self_draft_attempts` int field on `AgentSession`. |
| CONCERN | Skeptic | Demoting Haiku `expectations` to deterministic-only is a behavioral change presented as a no-op; needs recall parity + None-vs-empty contract. | OQ#3 → CONCERN resolution; Test Impact | Explicit `None` (never `""`) contract on empty extraction; recall-parity test added. |

**Pass 2 (this revision):**

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator/Adversary | The Blocker-2 fix's `self_draft_attempts += 1; session.save()` on a stale in-memory session is a TOCTOU under-count under two concurrent flagged outputs (Race 2 premise), so the cap silently fails to bound the loop; AND the bare full-hash `save()` re-clobbers `context_summary`/`expectations` (reopening Blocker-1). | Technical Approach → New-Blocker resolution + Blocker-2 rewrite; Race 3; Failure Path Test Strategy; Test Impact; Verification | Counter moved off the `AgentSession` hash to an atomic Redis key `steering:attempts:{session_id}` (`INCR` returns post-increment value; no read-then-write window; no `session.save()` on the steering path). `test_self_draft_attempts_bound_terminates_loop` rewritten to spawn TWO CONCURRENT `send()` coroutines. |
| CONCERN | Operator | Counter reset at the terminal `rpush` is unreachable on redundancy/RTR-suppress paths — a clean-but-suppressed send would permanently burn its budget. | Technical Approach → reset pinned location; Failure Path Test Strategy; Test Impact; Step 3 | Reset pinned to the `not needs_self_draft` branch BEFORE the `steering_deferred` early-return (`output_handler.py:417-424`); new test exercises the suppress path. |
| CONCERN | Skeptic | `_derive_context_summary` had only an existence test; the "strictly better than empty" claim was asserted, not validated (a first-sentence slice is often not the session topic). | Test Impact → `TestDeriveContextSummaryRecallParity` | Recall-parity test (parallel to expectations parity) validating distinguishing routing-usable topic hints, with an explicit "better than `(no context)`, not equivalent to Haiku" framing. |
| CONCERN | User | Manual UX smoke was a Risk-3 footnote, not a checkable outcome. | Success Criteria → user-outcome/message-quality criterion; Risk 3 | Promoted to an executable Success Criterion naming the behavior change: verbatim replies run LONGER than Haiku-condensed ones in the common sub-3000-char case; requires a proof artifact. |

---

## Open Questions

The two issue-flagged open questions are **resolved in this plan** (see Technical
Approach): (#1) the SDLC footer is a deterministic, non-LLM mechanical append (kept);
(#2) `classify_output` is deleted in favor of the existing deterministic
`_detect_empty_promise`, contingent on a build-time confirmation that
`bridge/promise_gate.py` does not need the delegation for a live verdict.

The remaining question for supervisor input:

1. **Confirm the `classify_output` full-deletion preference.** The plan prefers deleting
   `classify_output` and its LLM classification entirely (the deterministic
   `_detect_empty_promise` already covers the validator need). If you want to keep a
   deterministic classifier surface for future routing, say so and the builder will
   reduce it to a heuristic wrapper instead of deleting it.
