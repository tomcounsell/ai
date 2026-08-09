---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-09
tracking: https://github.com/tomcounsell/ai/issues/2694
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-09T16:14:00Z
---

# Context-Recall Advisory Flag (inbound intake classifier + outbound PM-message gate)

## Problem

The PM session acts on messages it does not have the context to understand, on both edges of the
conversation, and nothing in the system ever tells it to go read the recent chat history.

**Inbound.** A human sends `"yes"`, `"go ahead"`, `"not that one"`, `"the second one"`. When the intake
classifier runs on such a message it forms a judgment about it — and then throws that judgment away.
Only `intent` is branched on (`bridge/telegram_bridge.py:2164`); `confidence` and `reason` are consumed
by a single `logger.info` at `:2158-2162` and never reach the session. The PM receives the literal
string `yes` with no hint that context is missing.

**The classifier does not run on every inbound message, and this plan does not change that.** It is
gated twice, and both gates are load-bearing:

1. `bridge/telegram_bridge.py:2136` — `if active_sessions:`. The classifier is reached only when a
   `running` / `active` / `dormant` session, or a `pending` session inside `PENDING_MERGE_WINDOW_SECONDS`,
   exists in this chat (collected at `:2108-2133`).
2. `tools/classifier.py:330-335` — `if not session_context: return {"reason": "No active session
   context", ...}`, a hard return **before any model call**. So even with an active session, a session
   whose `context_summary` is empty produces no verdict.

The inbound half of this feature is therefore scoped to exactly that intersection: **a message arriving
in a chat that has a live-or-recent session carrying a non-empty `context_summary`.** That is the shape
of the motivating case — a short approve/deny answers something, and the something is a session in the
same chat — but it is not universal coverage. See "Known limitation" below.

**Outbound.** The PM writes `"which PR do you mean?"` and it is delivered verbatim. That burns a full
human round trip asking for context that is sitting in a message store the PM could have read itself.
Nothing inspects outbound text for this.

**Current behavior:**
- A bare `"yes"` is classified, routed, and handed to the PM with no advisory.
- A clarifying question is sent to the human. No gate flags it.
- Neither edge tells the PM *how* to read history, even though `valor-telegram read` and
  `valor-email read` both exist and work.

**Desired outcome:**
Both edges raise an advisory when context is likely missing, and the advisory text handed to the PM
contains a fully-formed, copy-pasteable command with the **real chat id already interpolated**. The PM
reads history and answers correctly instead of guessing or asking.

**Known limitation (accepted, not deferred work):**
A bare `"yes"` sent into a chat with **no** live-or-recent session, or into one whose session has an
empty `context_summary`, gets no inbound advisory. Closing that gap would mean a granite call on every
inbound Telegram message — a per-message model call on the hot path, for the residual case where a
human answers a question nobody asked. That trade is explicitly rejected (Decision D4). The outbound
edge is unaffected: it runs on PM output and has no session-context precondition, so a PM that ends up
confused in this residual case is still caught on the way out.

## Freshness Check

**Baseline commit:** `81941a6c43a2e4d445fd422c0f2de53d8bdd7d9d`
**Issue filed at:** 2026-08-09T15:14:11Z
**Disposition:** Minor drift

No commits have landed on `main` since the issue was filed (`HEAD` dates to 2026-08-08T02:21Z), so
nothing in the codebase moved underneath it. Every drift below is an **authoring inaccuracy in the
issue body**, caught by re-reading each cited location. The underlying claims all hold.

**File:line references re-verified:**

| Issue claim | Reality | Effect on plan |
|---|---|---|
| `tools/classifier.py:283-374` is `classify_message_intent_async` | `283` is `IntentDecision`; the function starts at **299** | Cosmetic |
| Four literal return dicts at `325`, `328-332`, `353-357`, `372-374` | Four returns at **327**, **331-335**, **366**, **370-374**. The third is `return result` — the dict is *constructed* at 351-355 from the `IntentDecision`, then clamped at 363-364 | **Material.** A new field must be threaded through the construction site, not just the literals |
| Abort detection at `telegram_bridge.py:975` | **978** (`is_abort = text.strip().lower() in ABORT_KEYWORDS`) | Cosmetic |
| Enqueue sites at `2001` / `2500` | Those are kwarg lines; the calls are `dispatch_telegram_session(` at **1988** / **2484** | Cosmetic |
| `dict(_injection_ctx) or None` at `1978` / `2405` | Exact | — |
| `models/agent_session.py:189,192` | Exact (`chat_id = KeyField(null=True)`, `extra_context = DictField(null=True)`) | — |
| `agent/session_executor.py:1838-1844` prepends the injection banner | Exact (prepend at 1841-1842) | — |
| Injection banner spoof contract at `injection_inspection.py:206-209` | Contract comment at **206-210**. Critically, the banner is an **open-ended prefix with no closing delimiter** | **Material** — see Technical Approach |
| `agent/steering.py:113` abort, `:255` `SELF_DRAFT_MAX_ATTEMPTS = 2` | Exact. Abort is an **exact full-string match** after strip/lower | **Material** |
| Cold-path drain uses `steering_msgs[0]`, re-queues rest | Exact (`session_executor.py:1919`, re-queue at 1932-1933 to the **back**) | **Material** |
| Hot path joins all steers | Exact (`agent/session_runner/runner.py:1306-1313`) | — |
| `output_handler.py:677-699` is the transport branch after `draft_message` | Inverted — `677` is transport **resolution**, which runs *before* `draft_message` at `703` | Cosmetic |
| `output_handler.py:1222-1225` instruction composition | `promise_advisory` read at `1222`, appended at **1223-1225** | — |
| `output_handler.py:451-500` guards `chat_id == "0"` / `== session_id` | Those literals appear only in a **docstring** at `473-474`; enforcement is `_deliverable_telegram_peer` at `495` | **Material** — guard via the helper, not a literal comparison |
| `promise_gate.py:936-985` `cli_check_or_exit` exits 1 on block | Exact, at all 5 call sites | — |
| `read_the_room.py:112-123` default-off, `526-528` DM-excluded | `118` and `526-528`. Exact in substance | — |
| `valor_telegram.py:1316-1352` `read` flags | `1316-1353`; mutually-exclusive `--chat/-c`, `--chat-id`, `--user`, `--project`; plus `--limit/-n`, `--search/-s`, `--since`, `--json`, **and `--strict`** (omitted by the issue) | Minor |
| `valor_email.py:247,740` | `247` = `cmd_read`, `740` = `cmd_threads`; the `read` **parser** is at **798-810** | Cosmetic |
| No `mcp__telegram__*` MCP server | Confirmed — there is no `.mcp.json` in the repo at all | — |

**Cited sibling issues/PRs re-checked:** the issue cites no sibling issues. Prior art searched
separately (see below).

**Commits on main since issue was filed:** none.

**Active plans in `docs/plans/` overlapping this area:**
`durability-room-job-agentrun.md` (#2494, status `Ready`, Large) is the plan that introduced the
`MessageDraft.promise_advisory` field and moved the intake classifier onto granite `run_typed_local`.
Its Tasks 13-15 shipped in merged PR #2631. This is **precedent, not conflict** — this plan copies its
advisory-carrying pattern and extends the schema it introduced. No file contention: that work is merged.

## Prior Art

- **#1318** (closed): *classifier silently drops human action directives — add imperative fast-path and
  few-shot examples*. Same classifier, same failure mode (a class of short human messages the model
  mishandles), and the remedy was **few-shot examples**. Directly informs the tuning task below — and
  the spikes show few-shot alone is a double-edged fix here.
- **#130** (closed): *Merge coach and classifier into a single LLM pass*. A different classifier
  (`bridge/summarizer.py`), but establishes the repo's preference for **folding a new judgment into an
  existing LLM pass rather than adding a second call** — which is exactly the inbound design here.
- **#1182** (closed): *JSON sidecar cache for deterministic Haiku call sites (intent classification)*.
  Confirms intent classification has been treated as a hot, cost-sensitive path historically.
- **PR #2413** (merged): *Pre-execution prompt-injection inspection on untrusted bridge input (#1630)*.
  Introduced `build_risk_banner` and the ordering contract this plan must not break.
- **PR #2631** (merged): *Durability M3 — Job model, granite routing, advisory promises*. Introduced
  `MessageDraft.promise_advisory` and the `run_typed_local` intake path. The template for this work.

No prior attempt at a context-recall signal exists. This is additive, not a re-fix.

## Research

Purely internal: no new external libraries, APIs, or ecosystem patterns. The transports
(`agent.llm.run_typed` / `run_typed_local`), the model ids (`granite4.1:3b`, `claude-haiku-4-5`), and
the advisory channels all already exist in-repo and are documented in
`docs/features/nonharness-llm-wrapper.md`.

No relevant external findings — proceeding with codebase context and live in-repo spikes.

## Spike Results

### spike-1: Do the issue's ~30 file:line claims still hold?
- **Assumption**: "The issue's recon is accurate and can be built against directly."
- **Method**: code-read (three parallel `Explore` passes)
- **Finding**: All substantive claims hold; ~10 line numbers drifted and **four claims were materially
  wrong** (the third return site is not a literal; the injection banner has no closing delimiter; the
  synthetic-peer guard is a helper not a literal comparison; the transport branch precedes the drafter).
- **Confidence**: high
- **Impact on plan**: Drove the three design corrections in Technical Approach. Full table in Freshness Check.

### spike-2: Can `granite4.1:3b` judge context-recall as a fifth/sixth field on `IntentDecision`?
- **Assumption**: "Adding two fields to the existing granite pass gets the judgment for free."
- **Method**: prototype (live `run_typed_local` against the real model, 10 cases)
- **Finding**: **No — 5/10.** It missed *every* canonical positive the issue names: `"yes"`,
  `"go ahead"`, `"not that one"`, `"the second one"` all returned `context_recall_advised=False`, with
  reasons like *"self-contained and refers directly to the current…"*. Only `"nope, skip it"` hit.
  All four self-contained negatives were correct.
- **Confidence**: high
- **Impact on plan**: The judgment is **not** free. Promoted prompt tuning from an implementation
  detail to a first-class task with a measurable bar (task 6) and a named fallback.

### spike-3: Control — does extending the schema regress the shipped `intent` field?
- **Assumption**: "Adding output fields degrades the 3B model's primary classification."
- **Method**: prototype (same 8 messages through the unmodified 3-field `IntentDecision`)
- **Finding**: **No regression — the opposite.** The unmodified classifier returns `new_work` for
  *every* one of the 8 messages including `"yes"`, `"go ahead"`, `"do it"` (its documented
  fail-safe default). The extended schema produced `interjection` for `"yes"` and `"do it"`, i.e.
  strictly better. Extending the schema is safe.
- **Confidence**: high
- **Impact on plan**: Removes the main architectural objection to the inbound approach. Also revealed
  the model is **non-deterministic across runs** for identical input, which dictates how the fixture
  test must be written (task 6).

### spike-4: Does few-shot fix the judgment (the #1318 remedy)?
- **Assumption**: "Few-shot examples close the gap, as they did in #1318."
- **Method**: prototype (rewritten prompt with 5 positive + 4 negative exemplars, 14 cases)
- **Finding**: **Partially, and it inverts the failure mode. 8/14.** All 8 short referent-free
  positives now hit (perfect recall). But 5 of 6 self-contained negatives flipped to
  `advised=True` — including `"How does the auth system work?"` and
  `"Fix the login bug on the settings page"`. The exemplars anchored the model toward `true`.
- **Confidence**: high
- **Impact on plan**: Proves both failure modes are individually reachable but that hitting them
  *simultaneously* on a 3B model is genuine, uncertain work. This is why task 6 has an iteration
  cap, and why Decision D1 ships inbound dark rather than betting the feature on the tuning outcome.

## Data Flow

**Inbound — `new_work` branch (Telegram only):**
1. **Entry**: Telethon `handler(event)`, `bridge/telegram_bridge.py:1166`. `telegram_chat_id` bound at `:1620`.
2. **Injection inspection**: `_injection_ctx` seeded at `:1335` with `injection_risk_banner`.
2.5. **Scope gate**: `if active_sessions:` at `:2136`. No live-or-recent session in this chat → the
   classifier never runs, `_ctx_recall_advisory` stays `None`, and steps 3-4 are skipped. Everything
   downstream still runs exactly as today.
3. **Intake classifier**: `classify_message_intent_async` at `:2148` → dict now carrying
   `context_recall_advised` / `context_recall_reason`. Returns early with defaults (no model call) when
   `target_session.context_summary` is empty (`tools/classifier.py:330-335`).
4. **Advisory build**: on `advised`, `build_context_recall_advisory(chat_id, medium="telegram")` →
   text with the real command, assigned to `_ctx_recall_advisory` (declared **above** the
   `if not (is_reply_to_valor ...)` block at `:2104` so it is always bound at step 5).
5. **Context seed**: merged into `extra_overrides` at `:2405`. That line is
   `extra_overrides: dict | None = dict(_injection_ctx) or None` — it evaluates to `None` when no
   injection context exists, so the merge must be `{**(extra_overrides or {}), "context_recall_advisory": adv}`
   and must only run when `adv` is truthy (never turn a `None` override into an empty dict).
   **This seed is reachable under the narrowed scope**: an active session with context whose message
   classifies `new_work` falls straight through `:2178` to the enqueue path. Verified by reading the
   fall-through at `:2196-2197` ("intent == new_work or fallthrough: continue to enqueue").
6. **Enqueue**: `dispatch_telegram_session(...)` at `:2484`, `extra_context_overrides=extra_overrides`.
7. **Session pickup**: `agent/session_executor.py:1839-1842` reads `extra_context` and prepends
   **advisory first, then the injection banner, then the untrusted text**.
8. **Output**: PM's first turn opens with the advisory, outside the untrusted zone.

**Inbound — `interjection` branch:**
1-4 as above, then:
5. **Advisory handed to the shared helper as a parameter**: the `:2178` call site — and only that one —
   passes `context_advisory=_ctx_recall_advisory` to `_ack_steering_routed`.
6. **Human's message** pushed unmodified inside the helper:
   `is_abort = text.strip().lower() in ABORT_KEYWORDS` then
   `push_steering_message(session_id, text, sender_name, is_abort=is_abort)`
   (`bridge/telegram_bridge.py:978-979`). `is_abort` is computed from the raw (or media-enriched) text
   **before** anything advisory-related exists in scope, so abort detection cannot be corrupted.
6b. **Advisory** pushed immediately after as a **separate** message, `front=False`, `is_abort=False`,
   `sender="intake-classifier"` — only when `context_advisory` is non-`None`, which is never true for
   the other five call sites.
7. **Drain**: hot path `_drain_steering_boundary` (`agent/session_runner/runner.py:1290`) keeps both (`_steer_is_substantive`
   requires only non-empty text) and `_merge_steers` joins them with `\n\n`.
8. **Output**: PM's next turn carries the human's message followed by the advisory.

**Outbound (both media):**
1. **Entry**: `agent/output_handler.py`, PM turn produces text.
2. **Transport resolution**: `_resolve_transport` at `:677`; `drafter_medium` at `:699`.
3. **Drafter**: `draft_message(...)` at `:703` — unchanged, still LLM-free.
4. **Context-recall check** (new): structural prefilter → on pass, `bridge/context_recall.py` Haiku
   call → `ContextRecallVerdict`.
5. **Branch**: `if draft.needs_self_draft or verdict.advised:` → `_inject_self_draft_steering(...)`
   with the advisory passed explicitly.
6. **Steering**: instruction + advisory pushed back to the PM; original text **held** in
   `extra_context["deferred_self_draft_text"]` at `:794-832`.
7. **Output**: PM reads history and resends. On budget exhaustion (2 attempts), a pending-steer peek-guard
   hit (`:1162`), or any error, the message is sent to the human. On a **context-recall-only** bounce the
   text sent is `draft.text` (the clean drafter output) — *not* `_apply_narration_fallback(text)`. See
   Technical Approach item 9.

## Architectural Impact

- **New dependencies**: none. `agent.llm` and both model ids are already in use.
- **Interface changes**: `IntentDecision` gains two fields (additive, defaulted). The four
  `classify_message_intent_async` return dicts gain two keys. `_inject_self_draft_steering` gains one
  keyword-only parameter. No public CLI or model schema changes.
- **Coupling**: one new module, `bridge/context_recall.py`, owning *both* edges' advisory text. This is
  the deliberate coupling point — the "exact command" must have exactly one definition or the two edges
  will drift.
- **Data ownership**: unchanged. No new persisted state; the inbound advisory rides existing
  `extra_context` / steering, the outbound advisory rides the existing self-draft channel.
- **Reversibility**: high. Both edges are gated by their own kill-switch env var. Per Decision D1
  **`CONTEXT_RECALL_INBOUND_ENABLED` defaults to `false`** (inbound ships dark) and
  `CONTEXT_RECALL_OUTBOUND_ENABLED` defaults to `true`. Flipping either restores or enables behavior
  with no code revert.
- **Preserved invariant**: `bridge/message_drafter.py` stays LLM-free (`:205`). The outbound LLM call
  lives in `agent/output_handler.py`'s caller, not inside `draft_message`. This is a deliberate
  deviation from the issue's sketch — see Technical Approach.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 0 remaining (the granite-vs-Haiku cost call is settled by Decision D1)
- Review rounds: 2 (the injection-banner ordering and the outbound bounce semantics both warrant review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Ollama running with `granite4.1:3b` | `curl -sf --max-time 5 http://localhost:11434/api/tags -o /tmp/ollama_tags.json && grep -q granite4.1:3b /tmp/ollama_tags.json` | Inbound classifier transport (live tests only) |
| Anthropic subscription auth | `python -c "from config.settings import settings; assert settings"` | Outbound Haiku gate (live tests only) |

Both are needed only for the live-gated tests; the unit tests stub the LLM and run without either.

## Solution

### Key Elements

- **`context_recall_advised` + `context_recall_reason` on `IntentDecision`** — two additive fields on
  the existing granite intake pass. No second LLM call inbound.
- **`bridge/context_recall.py`** — one new module owning three things: the outbound LLM verdict, the
  structural prefilter that keeps it off the hot path, and `build_context_recall_advisory()`, the
  **single** definition of the copy-pasteable command for both edges.
- **Two inbound delivery paths** — `extra_context` for `new_work`, a separate steering message for
  `interjection`. They are genuinely different mechanisms because a running session never re-reads
  `extra_context`.
- **One outbound gate** — evaluated in `agent/output_handler.py` after the drafter, forcing the
  existing self-draft steering loop rather than inventing a new channel.
- **Two kill switches and universal fail-open** — every failure mode degrades to today's behavior.
  Inbound ships **dark** (default `false`) per Decision D1; outbound ships on.

### Flow

**Inbound:** Human sends `"go ahead"` → intake classifier judges `intent` *and*
`context_recall_advised` in one pass → advisory built with the real chat id → delivered via
`extra_context` (new session) or a second steering message (running session) → PM's turn opens with
*"This message may depend on earlier conversation. Read it with: `valor-telegram read --chat-id -1003449100931 -n 10`"* → PM reads, acts correctly.

**Outbound:** PM writes `"which PR do you mean?"` → prefilter passes (short, contains `?`) → Haiku
judges it a referent-clarification → message is **held**, not sent → PM receives the advisory with the
command through the self-draft channel → PM reads history, resends a real answer → answer delivered.
If the PM insists twice, the budget is spent and the question goes to the human anyway.

### Technical Approach

**1. Inbound schema and prompt (`tools/classifier.py`).**
Add `context_recall_advised: bool = False` and `context_recall_reason: str = ""` to `IntentDecision`
(fields at `:294-296`). Extend `INTENT_CLASSIFICATION_PROMPT` (`:249-280`) with a context-recall section whose
test is a *question about referents*, not a word list: **"reading only the message text, do you know
what thing it is talking about?"** No keyword allowlist (Development Principle 3).

Thread the two keys through **all four** return paths — and note the third is not a literal:
- `:327` `{"intent": "new_work", ..., "reason": "Empty message"}` → add defaults `False` / `""`
- `:331-335` `"No active session context"` → add defaults
- `:351-355` the **dict construction** from the `IntentDecision` → map `decision.context_recall_advised`
  and `decision.context_recall_reason`. The threshold clamp at `:363-364` must touch `intent` only and
  must not clobber the new keys. Returned at `:366`.
- `:370-374` the fail-open `except` path → add defaults `False` / `""`

**2. The advisory builder (`bridge/context_recall.py`) — the guard must be medium-aware.**
```
build_context_recall_advisory(*, chat_id, medium, reason=None) -> str | None
```
Returns `None` (no advisory, fail open) when the chat id is unusable for that medium.

**The guard branches on `medium` before it validates.** `utils.peer.deliverable_telegram_peer`
(`utils/peer.py:37-44`) returns `_numeric_peer(chat_id) not in (None, 0)` — it accepts only a nonzero
integer peer. Email sessions carry `chat_id = from_addr` (`bridge/email_bridge.py:1512`, literally
commented `# email address as chat_id`), so running the Telegram guard on an email session returns
`False` for **every** email session and the email leg would be dead on arrival. Therefore:

```
if medium == "telegram":
    ok = deliverable_telegram_peer(chat_id)
else:                       # email
    ok = bool(chat_id) and "@" in str(chat_id)
```

Never run the Telegram peer guard on an email `chat_id`, and never run the `"@"` check on a Telegram one.

Commands, taken from the verified argparse definitions (`N` = `CONTEXT_RECALL_HISTORY_DEPTH`, see item 10):
- Telegram (`tools/valor_telegram.py:1316-1353`): `valor-telegram read --chat-id <real id> -n N`
- Email (`tools/valor_email.py:798-810`): the `read` parser has **no per-peer flag** — only
  `--mailbox`, `--limit/-n`, `--search/-s`, `--since`, `--json`. So emit
  `valor-email read --search "<peer address>" -n N` when the address passes the `"@"` check, and fall
  back to `valor-email threads` when it does not. Never emit a placeholder.

The `chat_id == session_id → None` criterion holds **incidentally**, not by design: session ids are
non-numeric (`models/agent_session.py:155`), so `_numeric_peer` returns `None` for them and the Telegram
guard rejects them. Nothing compares the two values. The plan states it this way rather than implying a
deliberate session-id check that does not exist.

**3. Inbound delivery — `new_work` (scope-hoisting first, then ordering).**
`intent_result` is created at `:2148` **inside** the `if active_sessions:` block, which is itself inside
the `if not (is_reply_to_valor and message.reply_to_msg_id):` block opened at `:2104`. The seed site at
`:2405` is outside both. So declare `_ctx_recall_advisory: str | None = None` **above** `:2104`, assign
it inside the classifier branch, and merge at `:2405`:

```
if _ctx_recall_advisory:
    extra_overrides = {**(extra_overrides or {}), "context_recall_advisory": _ctx_recall_advisory}
```

The `or {}` is required: `:2405` is `extra_overrides: dict | None = dict(_injection_ctx) or None`, which
is `None` whenever there is no injection context. Guarding the whole merge on truthiness of the advisory
keeps `extra_overrides` at `None` in the no-advisory case, preserving today's behavior byte-for-byte.

Then read it at `agent/session_executor.py:1839-1842`.

The ordering is **advisory → injection banner → untrusted text**, and this is not arbitrary.
`build_risk_banner` returns an *open-ended prefix* ending in
`----- SCREEN DELIMITER (untrusted content follows) -----`; there is **no closing delimiter**, so the
untrusted zone runs to the end of the prompt. Placing the advisory *after* the banner would put
bridge-authored trusted text inside the zone the banner just declared untrusted — the PM would
rightly distrust it, and an attacker could forge an identical line. Placing it *before* the banner
keeps the banner's contract intact (it still precedes all untrusted content, `injection_inspection.py:206-210`)
because a trusted-bridge prefix is not attacker-authorable.

**4. Inbound delivery — `interjection` (three hazards).**
`bridge/telegram_bridge.py:979` is **not** a classifier-specific site. It lives inside the shared helper
`_ack_steering_routed` (defined `:904-913`), which has five callers: `:1809`, `:1835`, `:1868`, `:2070`,
and `:2178`. Only `:2178` is the intake-classifier branch; the other four are reached on paths that skip
the classifier entirely (`:2104`) and have no verdict in scope. Editing `:979` unconditionally would fire
the advisory on every steering route, or reference an undefined name.

The fix is a parameter, not a site edit:

- Add `context_advisory: str | None = None` as a **keyword-only** parameter to `_ack_steering_routed`
  (the signature at `:904-913` is already keyword-only after `message`, so this is additive and the
  other five call sites need no change at all).
- Immediately after the existing `push_steering_message(...)` at `:979`, emit:
  `if context_advisory: push_steering_message(session_id, context_advisory, "intake-classifier", is_abort=False, front=False)`
- Pass `context_advisory=` from `:2178` **only**.

- *Abort detection is provably safe*: `is_abort = text.strip().lower() in ABORT_KEYWORDS` is computed at
  `:978` from the raw (or media-enriched) `text`, one line before the human's push and before the
  advisory push exists. The advisory is never concatenated into `text`, so the exact-full-string match
  at `agent/steering.py:113` sees the same string it sees today. Appending would have destroyed abort —
  that is why this is a separate message.
- *`is_abort=False` on the advisory is not suppression*: `push_steering_message` auto-upgrades
  `is_abort` when the text itself matches `ABORT_KEYWORDS` (`agent/steering.py:113-114`, `if not is_abort
  and text.strip().lower() in ABORT_KEYWORDS`). Passing `False` only declines to *assert* abort; it
  cannot mask one.
- *Back, not front* — this reverses the issue's suggestion. The `session_health.py:3463` `front=True`
  precedent is unsafe here: the cold-path drain (`agent/session_executor.py:1919`) consumes only
  `steering_msgs[0]` and re-queues the remainder at the back, so a front-pushed advisory would
  **displace the human's own message** for that turn. At the back, the human's message is always
  consumed first and the advisory is at worst one turn late.

Both survive the hot path: `_steer_is_substantive` (`agent/session_runner/runner.py:1233`) admits any
non-empty text, and `_merge_steers` (`:1306`) joins all of them.

**5. Outbound gate — suppress-and-bounce (Decision D2), placement, and why it deviates from the issue.**
The gate **suppresses** the PM's clarifying question and bounces it back, rather than annotating and
sending. Advisory-only would still ask the human "which one?" — the exact round trip the issue exists to
eliminate — so it would satisfy the letter of the issue and none of its purpose. The suppression shape is
the `if ... or ctx_verdict.advised:` branch below, which returns `DeliveryOutcome.deferred_self_draft` at
`:833` and never writes the outbox.

The issue proposes wiring into `draft_message`'s two `needs_self_draft` return sites. **That cannot
work**: those sites fire only when another gate has already objected. A clean
`"which PR do you mean?"` returns `needs_self_draft=False` and is sent — the exact case this issue is
about would never fire. The check must *itself* trigger the bounce.

It also must not live inside `draft_message`: the drafter is deliberately LLM-free
(`bridge/message_drafter.py:205`), and putting a Haiku call there reverses a shipped decision and adds
latency to every outbound message.

Therefore: evaluate in `agent/output_handler.py` between `draft_message` (`:703`) and the
`needs_self_draft` branch (`:722`), and pass the advisory explicitly:
```
if getattr(draft, "needs_self_draft", False) or ctx_verdict.advised:
    steering_deferred = self._inject_self_draft_steering(
        session, draft, context_advisory=ctx_advisory
    )
```
No `MessageDraft` field is added and the draft is never mutated — a second deviation from the issue's
sketch, for the same reason (the drafter should not carry a signal it does not produce).

**6. The instruction text must not lie.** `SELF_DRAFT_INSTRUCTION`
(`bridge/message_drafter.py:884-892`) opens with *"flagged by the delivery validator for a wire-format
violation or an unsubstantiated promise"*. That is false for a context-recall bounce. Add a distinct
preamble constant used when the bounce is context-recall-only, and keep the existing text when a real
drafter violation is present.

**7. Transport choice, and why the two edges differ.**
- *Inbound: granite* (`run_typed_local`, existing pass, zero marginal cost). A false positive is
  cheap — the PM ignores an advisory line.
- *Outbound: Haiku* (`run_typed`, `sdk_timeout=3.0` mirroring `RTR_SDK_TIMEOUT`). A false positive is
  **expensive** — it suppresses a real message and costs a round trip. Precision matters, so it gets
  the better model. Kept off the hot path by a structural prefilter (short text **and** contains `?`),
  which is a cheapness gate on message *shape*, not a keyword match on intent.
- Use `agent.llm` for both. Do **not** copy `bridge/promise_gate.py`'s hand-rolled `AsyncAnthropic`
  client — it predates the wrapper.

**8. Fail-open, everywhere.** Every LLM call, advisory build, and delivery write is wrapped so that any
exception, timeout, missing chat id, or disabled kill switch degrades to exactly today's behavior.
Nothing in this plan can drop a message, and nothing in it can reach `cli_check_or_exit` —
`bridge/promise_gate.py` is not modified at all.

**9. Budget exhaustion must not corrupt a clean draft.** The reused path does **not** currently "send the
original question unchanged". At `agent/output_handler.py:720-728`, when `_inject_self_draft_steering`
returns `False` — budget exhausted, or the pending-steer peek-guard at `:1162` fired — it runs
`delivery_text = self._apply_narration_fallback(text)`, which discards `draft.text` (assigned at
`:711-712`) in favour of the **raw pre-drafter text**, and substitutes `NARRATION_FALLBACK_MESSAGE`
outright when that raw text is narration-shaped. That is correct today, because the only way to reach it
is a drafter violation. It is wrong for a context-recall-only bounce, where the draft was clean.

So the fallback stays keyed to its original trigger, not to the merged condition:

```
if getattr(draft, "needs_self_draft", False) or ctx_verdict.advised:
    steering_deferred = self._inject_self_draft_steering(
        session, draft, context_advisory=ctx_advisory
    )
    if not steering_deferred and getattr(draft, "needs_self_draft", False):
        delivery_text = self._apply_narration_fallback(text)
```

A context-recall-only bounce that fails to steer leaves `delivery_text` as `draft.text` and the human
receives the PM's question as drafted. The `else:` arm at `:730` (which resets the self-draft counter on
the clean path) is unchanged — it must **not** run when a context-recall bounce succeeded, or the budget
would reset every turn and the 2-attempt bound would be unenforceable.

**10. History depth is a named constant, not a literal (Decision D3).** `valor-telegram read` defaults to
`--limit 10` (`tools/valor_telegram.py:271`). Use that, via
`CONTEXT_RECALL_HISTORY_DEPTH = int(os.getenv("CONTEXT_RECALL_HISTORY_DEPTH", "10"))` in
`bridge/context_recall.py`, carrying a grain-of-salt comment marking it provisional and tunable per the
repo's magic-number convention. Both media use the same constant so the two edges cannot drift.

**11. Kill-switch registration follows the in-repo precedent, not `config/settings.py`.** The two closest
analogues — `READ_THE_ROOM_ENABLED` and `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` — are both non-secret
boolean toggles, both declared in `.env.example` with a description comment above the key, and **neither
has a `config/settings.py` field**: `READ_THE_ROOM_ENABLED` is read fresh per call via
`os.environ.get("READ_THE_ROOM_ENABLED", "false").strip().lower() in (...)` at `bridge/read_the_room.py:118`.
Fresh reads are the right shape for a kill switch — a `pydantic-settings` field is cached at import, so
flipping it would require a process restart precisely when you most want it not to.

**Named deviation:** the critique's implementation note and the routed revision instruction both called
for a `config/settings.py` field. The code contradicts that for this class of flag, so this plan follows
the two live precedents instead: `.env.example` entry (with the comment line above the key that
`tests/unit/test_env_completeness.py::test_description_extracted_from_comment` requires) plus a fresh
`os.environ.get` read mirroring `read_the_room.py:118`'s truthy-set parsing verbatim. `config/settings.py`
is not touched. Both keys must also be added to the vault `~/Desktop/Valor/.env` at rollout, or
`scripts/update/verify.py::check_env_completeness` (`:1088`) reports them missing on every machine, every
`/update`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every new `except Exception` (classifier field extraction, advisory build, outbound LLM call,
      both inbound delivery writes) must log at `warning`/`debug` and be covered by a test asserting
      the observable degradation — message still delivered, no advisory attached. No bare `pass`.
- [ ] Test that a raised `LLMCallError` from the outbound check results in the message being **sent**,
      not held.

### Empty/Invalid Input Handling
- [ ] `build_context_recall_advisory(medium="telegram")` with `chat_id` of `None`, `""`, `"0"`, and a
      session-id-shaped value → returns `None`, no advisory, message unaffected. The session-id case is
      rejected **incidentally**: session ids are non-numeric (`models/agent_session.py:155`), so
      `_numeric_peer` returns `None`. Nothing compares `chat_id` to `session_id`, and the test should say
      so rather than implying a deliberate check.
- [ ] `build_context_recall_advisory(medium="email")` with `chat_id="someone@example.com"` returns a real
      command — the Telegram peer guard must not be reachable on this path. This is the regression test
      for the dead-email-leg defect.
- [ ] Empty and whitespace-only inbound messages → the `:327` early return path carries the new keys
      with defaults.
- [ ] Empty PM output on the outbound edge → prefilter rejects, no LLM call, no bounce (guards against
      an empty-output self-draft loop).

### Error State Rendering
- [ ] Advisory text is asserted to contain the **literal chat id** and a runnable command — a test
      asserts the string contains no `<`, `{`, or the word `placeholder`.
- [ ] Budget-exhaustion path asserts the user receives the PM's question rather than silence, and that on
      a **context-recall-only** bounce the text sent is `draft.text` — not `_apply_narration_fallback(text)`.
      A companion case asserts a genuine drafter violation still *does* get the narration fallback, so the
      guard narrows the trigger without disabling it.
- [ ] No-session case: a chat with zero live-or-recent sessions produces no classifier call, no advisory,
      and `extra_overrides is None`. Pins the accepted scope limitation as behavior.

## Test Impact

- [ ] `tests/unit/test_intake_classifier.py` — UPDATE: existing assertions construct/compare
      `IntentDecision` and the returned dict. Add the two new keys to expected dicts; the
      `_fake_decision` helper (`:26-37`) must return an `IntentDecision` carrying the new fields.
      Patch target stays `agent.llm.run_typed_local` (works only because `tools/classifier.py:338`
      imports it lazily inside the function — preserve that).
- [ ] `tests/unit/test_intake_classifier.py` — UPDATE: the `_forbid_model_call` variant asserting fast
      paths never call granite must still hold after the field additions.
- [ ] `tests/unit/test_message_drafter.py` (1469 lines) — UPDATE only if `SELF_DRAFT_INSTRUCTION` is
      asserted byte-for-byte anywhere; the new preamble is a separate constant, so `draft_message`
      behavior is unchanged. Verify no test asserts the drafter is LLM-free by grepping imports.
- [ ] `tests/unit/test_promise_advisory.py` — UPDATE: add a case asserting `promise_advisory` and the
      new context advisory **coexist** in one instruction without clobbering each other.
- [ ] `tests/unit/test_context_recall.py` — CREATE: advisory builder (both media), prefilter, verdict
      fail-open, medium-branched chat-id guards, the history-depth constant, and the committed judgment
      fixture set.
- [ ] `tests/unit/test_env_completeness.py` — no change expected, but it must pass: it enforces the
      comment-line-above-key shape for each of the three new `.env.example` entries.
- [ ] `tests/unit/` coverage for `_ack_steering_routed` — UPDATE or CREATE: assert the four
      non-classifier call sites still push exactly one steering message after the signature change.
- [ ] Live-model tests must be gated exactly as the existing Ollama-reachability class in
      `test_intake_classifier.py` — spike-3 showed the model is non-deterministic across runs, so a
      live test asserting a single message's verdict would be flaky.

## Rabbit Holes

- **Prompt-tuning the 3B model to perfection.** Spikes 2 and 4 landed at 5/10 and 8/14 with opposite
  failure modes. Task 6 caps this at three iterations against a fixed fixture set; if the bar is not
  met, the inbound flag simply stays off (Decision D1) rather than iterating indefinitely.
- **Making the outbound check smart about *which* history to read.** Emit the recent-messages command
  and stop. Search terms, thread selection, and date ranges are the PM's job.
- **Refactoring the two inbound delivery paths into one.** They are different because a running
  session never re-reads `extra_context`. Any "unification" is a rewrite of the steering contract.
- **Fixing the intake classifier's `new_work` bias.** Spike-3 incidentally showed the shipped
  classifier returns `new_work` for `"yes"`, `"go ahead"`, and `"do it"` against a live session — that
  is a real accuracy problem, but it is the `intent` field's problem, not this flag's.
- **Adding a closing delimiter to the injection banner.** Tempting once you notice the zone is
  open-ended. It changes a shipped security contract (#1630/PR #2413) and belongs to that owner.
- **Threading `chat_id` through `draft_message`.** Unnecessary — `draft_message` already receives
  `session`, and `AgentSession.chat_id` is a real field.

## Risks

### Risk 1: The granite judgment never reaches usable accuracy
**Impact:** The inbound half of the feature ships as either noise (advisory on everything) or dead
weight (advisory on nothing). Spikes 2 and 4 make this the single most likely failure.
**Mitigation:** Decision D1 removes the risk from the critical path by shipping inbound dark
(`CONTEXT_RECALL_INBOUND_ENABLED=false`) regardless of the tuning outcome. Task 6 still makes accuracy a
measurable gate — a committed fixture set with a stated bar, capped at three tuning iterations — but the
bar now governs *when the flag gets flipped on*, not whether the PR can merge. A failed tuning pass ships
dark, which is exactly today's behavior.

### Risk 2: The outbound gate suppresses a question the PM legitimately needed to ask
**Impact:** The human waits while the PM re-reads history and concludes it genuinely does not know.
Worst case, two wasted PM turns before the question gets through.
**Mitigation:** Haiku instead of granite on this edge precisely because precision matters here; the
structural prefilter keeps it away from anything that isn't a short question; the existing
`SELF_DRAFT_MAX_ATTEMPTS = 2` budget bounds the delay and then sends the original text; and
`deferred_self_draft_text` guarantees the question survives even if the session dies mid-bounce.

### Risk 3: Advisory lands inside the injection banner's untrusted zone
**Impact:** Security regression — an attacker-authored message could forge a bridge advisory, and the
real advisory would sit in a zone the PM is told to distrust.
**Mitigation:** Ordering is pinned by an explicit unit test asserting the advisory's index in the
composed prompt is strictly less than the banner's, which is strictly less than the message's. The
banner's own contract comment (`injection_inspection.py:206-210`) is cited in the test.

### Risk 4: Steering advisory breaks abort or displaces the human's message
**Impact:** `"stop"` stops working, or the human's actual instruction is deferred a turn behind an
advisory.
**Mitigation:** Separate message (never appended) with `is_abort=False`, pushed at the back
(`front=False`). Tests cover both: a `"stop"` interjection that also trips the advisory must still
produce `is_abort=True` on the human's message, and the cold-path `steering_msgs[0]` must be the
human's message.

### Risk 5: Outbound Haiku call adds latency to every PM message
**Impact:** Perceptible delay on the reply path.
**Mitigation:** Structural prefilter runs first and rejects almost everything; `sdk_timeout=3.0`
matching the promise gate; failure and timeout both fail open to send. No coroutine-level
`asyncio.wait_for` around the Anthropic client — `bridge/promise_gate.py` documents that it leaks
httpx connections under cancellation.

## Race Conditions

### Race 1: `extra_context` written after the session is picked up
**Location:** `bridge/telegram_bridge.py:2405` → `:2484` → `agent/session_executor.py:1839`
**Trigger:** The advisory is seeded into `extra_overrides` after the session has already been enqueued
and claimed by the worker.
**Data prerequisite:** `context_recall_advisory` must be present in `extra_context` before the worker
reads it.
**State prerequisite:** Session not yet picked up.
**Mitigation:** No new race — the ordering already works and is verified: `telegram_chat_id` binds at
`:1620`, the classifier runs at `:2148`, the reseed is at `:2405`, and the enqueue is at `:2484`. The
advisory is seeded strictly before dispatch, on the same code path as the existing
`injection_risk_banner`. A test asserts the seed precedes the `dispatch_telegram_session` call.

### Race 2: Advisory steering push races the human's message push
**Location:** `bridge/telegram_bridge.py:979` (+ new push)
**Trigger:** Both pushes target the same Redis list; a turn boundary could drain between them.
**Data prerequisite:** The human's message must be consumed no later than the advisory.
**State prerequisite:** —
**Mitigation:** `front=False` on the advisory guarantees it lands behind the human's message in the
list. If a drain interleaves, the cold path takes the human's message (`steering_msgs[0]`) and
re-queues the advisory; the hot path takes both. In no interleaving does the advisory precede or
displace the human's message.

### Race 3: Concurrent self-draft bounces double-spend the budget
**Location:** `agent/output_handler.py:1179-1189`, `agent/steering.py:260-289`
**Trigger:** A drafter violation and a context-recall trigger on the same message.
**Data prerequisite:** One bounce = one budget bump.
**State prerequisite:** —
**Mitigation:** The single `if draft.needs_self_draft or ctx_verdict.advised:` branch makes exactly one
`_inject_self_draft_steering` call, so `bump_self_draft_attempts` fires once. The counter is a Redis
`INCR`, atomic across processes. A test asserts a doubly-flagged message bumps the counter by one.

## No-Gos (Out of Scope)

Nothing here is postponed work. The items below are permanent design boundaries — decisions about what
this feature deliberately does not do — and each is pinned by a Verification anti-criterion or a
Success Criterion where it is mechanically checkable.

The PM is never made to read history **automatically**. This plan raises a flag and hands over the
command; the PM decides. Auto-invoking a history read on the agent's behalf would make an advisory into
a control-flow change and is explicitly not the goal.

`READ_THE_ROOM_ENABLED` stays default-off and its DM exclusion is untouched. Recon confirmed it is
unusable for this purpose (`bridge/read_the_room.py:118`, `:526-528`); this plan neither enables nor
modifies it.

Inbound classification for **email** is not added, because the intake classifier has no email caller
today (`bridge/email_bridge.py` never references it). Adding one is a routing change to the email
bridge with its own risk surface. The *outbound* edge **does** cover email — the drafter path is shared
across both transports — and that coverage is genuine, not nominal: the advisory builder branches its
chat-id guard on `medium` (Technical Approach item 2) precisely so email sessions, whose `chat_id` is an
address rather than a numeric peer, are not rejected by the Telegram peer guard. An email case is pinned
in Success Criteria.

**Inbound coverage is scoped, and the uncovered case is accepted, not deferred.** The advisory fires only
when the chat has a live-or-recent session with a non-empty `context_summary` (Problem section, "Known
limitation"). A bare acknowledgment into a chat with no such session gets no inbound advisory, and this
plan does not add a session-independent judgment call to close that gap — doing so means a granite call
on every inbound Telegram message, which reverses #2494's cost rationale for exactly the residual case.

## Update System

No script or skill changes required. This feature adds no new dependencies, no config files, and no
migrations.

**Three env keys must be registered together** (owned by task 9, not left to "add the kill switch" prose
inside tasks 1 and 2):

| Key | Default | Read at |
|---|---|---|
| `CONTEXT_RECALL_INBOUND_ENABLED` | `false` (ships dark, D1) | `tools/classifier.py` |
| `CONTEXT_RECALL_OUTBOUND_ENABLED` | `true` | `bridge/context_recall.py` |
| `CONTEXT_RECALL_HISTORY_DEPTH` | `10` (D3) | `bridge/context_recall.py` |

Each gets a `.env.example` entry with a **description comment on the line immediately above the key** —
required by `tests/unit/test_env_completeness.py::test_description_extracted_from_comment`, following the
`READ_THE_ROOM_ENABLED` (`.env.example:278`) and `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` (`:284`)
precedents. Each is read fresh via `os.environ.get`, **not** through `config/settings.py` (Technical
Approach item 11 explains the deviation). All three must also be added to the vault
`~/Desktop/Valor/.env` at rollout, or `scripts/update/verify.py::check_env_completeness` (`:1088`, wired
at `:1154`) reports them as missing on every machine on every `/update` — recurring noise from a
half-registration.

An un-updated `.env` still behaves correctly: every key has a safe default and every read is fail-open.

The bridge and worker must be restarted to pick up the change (`./scripts/valor-service.sh restart`),
which is the standard post-merge step already covered by `/update`.

## Agent Integration

No new CLI entry point and no new MCP surface are required — this is a bridge-internal change on both
edges. The capability the advisory *points at* already exists and is already reachable by the agent:
`valor-telegram` and `valor-email` are registered console scripts (`pyproject.toml:78` confirms
`valor-telegram = "tools.valor_telegram:main"`), invoked through the agent's Bash tool.

The bridge does import the new code directly: `bridge/telegram_bridge.py` imports
`build_context_recall_advisory`, and `agent/output_handler.py` imports both the advisory builder and
the outbound verdict function from `bridge/context_recall.py`.

Integration coverage: a test asserts the exact command string emitted by
`build_context_recall_advisory` is executable by parsing it with `tools/valor_telegram.py`'s own
argparse parser — this catches drift if the `read` subcommand's flags ever change, which is the
realistic way this feature silently rots.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/context-recall-advisory.md` covering both edges, the advisory text
      contract, the two inbound delivery mechanisms and why they differ, the ordering rule relative to
      the injection banner, the transport split (granite inbound / Haiku outbound) with the
      cost-asymmetry rationale, the kill switches and their defaults, and every fail-open path.
- [ ] That doc must carry a **Scope and known limitation** section stating plainly that the inbound
      advisory fires only when the chat holds a live-or-recent session with a non-empty
      `context_summary`, naming both gates (`telegram_bridge.py:2136`, `classifier.py:330-335`), and
      recording why universal coverage was rejected (a granite call per inbound message). A reader must
      not be able to infer universal inbound coverage from the doc.
- [ ] Document that inbound ships **dark** (`CONTEXT_RECALL_INBOUND_ENABLED=false`) and what evidence
      would justify flipping it on.
- [ ] Document the three env keys and their defaults, and note they must be added to the vault `.env`.
- [ ] Add a `| [Context-Recall Advisory](context-recall-advisory.md) | … | Shipped |` row to
      `docs/features/README.md`, alphabetized (it sorts between "Config Timeout Catalog" and
      "Dashboard").
- [ ] Update `docs/features/intake-classifier.md` (106 lines): document the two new `IntentDecision`
      fields, the extended prompt, and the fact that the verdict now reaches the PM instead of being
      log-only.
- [ ] Update `docs/features/message-drafter.md` (289 lines): the drafter itself is unchanged and
      **remains LLM-free** — document that the context-recall gate lives in `output_handler`, and that
      the self-draft loop now has a second trigger sharing the same 2-attempt budget.
- [ ] Update the `README.md` index row for Message Drafter if its description mentions the self-draft
      loop's trigger conditions.

### Inline Documentation
- [ ] Comment the advisory-before-banner ordering at `agent/session_executor.py` citing
      `injection_inspection.py:206-210`, so a future refactor cannot silently reorder it.
- [ ] Comment the `front=False` choice at the interjection push site citing the cold-path
      `steering_msgs[0]` behavior.
- [ ] Docstring on `build_context_recall_advisory` stating the no-placeholder contract.

## Success Criteria

- [ ] `context_recall_advised` and `context_recall_reason` present on `IntentDecision` and on **all
      four** return paths of `classify_message_intent_async`, including the fail-open `except`.
- [ ] Inbound judgment is made by the LLM against a referent test, with no keyword allowlist in the code.
- [ ] Advisory reaches the PM on **both** inbound branches — `extra_context` for `new_work`, a separate
      back-pushed steering message for `interjection` — **within the scoped case**: a chat holding a
      live-or-recent session with a non-empty `context_summary`.
- [ ] With **no** live-or-recent session in the chat, behavior is byte-identical to today: no classifier
      call, no advisory, `extra_overrides` still `None`. A test asserts this explicitly, so the accepted
      limitation is pinned rather than assumed.
- [ ] `_ack_steering_routed`'s four non-classifier call sites (`:1809`, `:1835`, `:1868`, `:2070`) push
      exactly one steering message each; only `:2178` can push two.
- [ ] A `"stop"` message that also trips the advisory still produces `is_abort=True`.
- [ ] Composed `new_work` prompt orders advisory → injection banner → untrusted text.
- [ ] Outbound check fires on a clean referent-clarifying question (one that produces
      `needs_self_draft=False` from the drafter) and its advisory arrives via the self-draft channel.
- [ ] `bridge/promise_gate.py` is untouched; `cli_check_or_exit` behavior is byte-identical.
- [ ] `bridge/message_drafter.py` imports no LLM transport.
- [ ] Advisory text on both edges contains a fully-formed command with the real chat id and no placeholder.
- [ ] **Email outbound produces a real advisory.** A session with `chat_id="someone@example.com"` and
      `medium="email"` yields `valor-email read --search "someone@example.com" -n 10` — not `None`. The
      Telegram peer guard is never applied to an email chat id.
- [ ] A context-recall-only bounce whose steering push fails delivers `draft.text`, **not**
      `_apply_narration_fallback(text)`.
- [ ] All three env keys appear in `.env.example`, each with a description comment on the line
      immediately above it, and `tests/unit/test_env_completeness.py` passes.
- [ ] `CONTEXT_RECALL_INBOUND_ENABLED` defaults to `false` and `CONTEXT_RECALL_OUTBOUND_ENABLED` to
      `true`; with both unset, inbound is inert and outbound is live.
- [ ] History depth is read from `CONTEXT_RECALL_HISTORY_DEPTH` (default `10`) — no literal depth in
      either command string.
- [ ] Both edges fail open: classifier error, timeout, missing chat id, or disabled kill switch all
      degrade to current behavior and never block a message.
- [ ] Inbound tuning bar from task 6 is either met, or recorded as unmet with the flag left off (D1
      makes an unmet bar a non-blocker).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `agent/output_handler.py` references `bridge.context_recall`

## Team Orchestration

### Team Members

- **Builder (inbound classifier)** — Name: `classifier-builder`; Role: `IntentDecision` schema, prompt
  extension, four return paths; Agent Type: builder; Resume: true
- **Builder (advisory module)** — Name: `advisory-builder`; Role: `bridge/context_recall.py` — verdict,
  prefilter, advisory text; Agent Type: builder; Resume: true
- **Builder (inbound delivery)** — Name: `delivery-builder`; Role: bridge + session_executor wiring,
  ordering, steering; Agent Type: builder; Domain: async/concurrency + untrusted-input; Resume: true
- **Builder (outbound gate)** — Name: `outbound-builder`; Role: output_handler wiring, instruction
  preamble; Agent Type: builder; Resume: true
- **Test engineer** — Name: `recall-tester`; Role: fixture set, tuning harness, all new tests;
  Agent Type: test-engineer; Resume: true
- **Documentarian** — Name: `recall-docs`; Agent Type: documentarian; Resume: true
- **Validator** — Name: `recall-validator`; Agent Type: validator; Resume: true

### Available Agent Types

Standard Tier 1 pool. `delivery-builder` carries `Domain: untrusted-input` framing from
`DOMAIN_FRAMING.md` because it edits the injection-banner composition path.

## Step by Step Tasks

### 1. Extend the inbound classifier schema and prompt
- **Task ID**: build-classifier
- **Depends On**: none
- **Validates**: `tests/unit/test_intake_classifier.py`
- **Informed By**: spike-2 (granite misses the canonical positives), spike-3 (no regression to `intent`)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `context_recall_advised: bool = False`, `context_recall_reason: str = ""` to `IntentDecision`
  (`tools/classifier.py:294-296`).
- Extend `INTENT_CLASSIFICATION_PROMPT` (`:249-280`) with a referent-based context-recall section. No
  keyword allowlist.
- Add both keys to the literal returns at `:327`, `:331-335`, `:370-374` and to the **dict construction**
  at `:351-355` (mapping from the `IntentDecision`). Confirm the clamp at `:363-364` touches `intent` only.
- Add the `CONTEXT_RECALL_INBOUND_ENABLED` kill switch, **default `false`** (D1), read fresh via
  `os.environ.get` mirroring `bridge/read_the_room.py:118`; when off, the fields are always default and
  the prompt extension is not sent.
- Update `tests/unit/test_intake_classifier.py` `_fake_decision` and expected dicts.
- Do **not** relax either scope gate (`telegram_bridge.py:2136` `if active_sessions:`,
  `classifier.py:330-335` empty-`session_context` early return). They are the accepted scope boundary.

### 2. Build the advisory module
- **Task ID**: build-advisory
- **Depends On**: none
- **Validates**: `tests/unit/test_context_recall.py` (create)
- **Informed By**: spike-1 (the `read` flag sets and the docstring-only chat-id guards)
- **Assigned To**: advisory-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/context_recall.py` with `ContextRecallVerdict`, `check_outbound_context_recall()`,
  `_prefilter()`, and `build_context_recall_advisory()`.
- Define `CONTEXT_RECALL_HISTORY_DEPTH` (env-overridable, default `10`, provisional-tunable comment) and
  use it for both media. No literal depth anywhere.
- Telegram command `valor-telegram read --chat-id <id> -n <depth>`; email
  `valor-email read --search "<addr>" -n <depth>` with a `valor-email threads` fallback.
- **Branch the chat-id guard on `medium`**: `deliverable_telegram_peer` for telegram,
  `bool(chat_id) and "@" in str(chat_id)` for email. Never run the Telegram peer guard on an email
  `chat_id` — it rejects every one of them (`utils/peer.py:37-44` vs `bridge/email_bridge.py:1512`).
  Return `None` on any guard miss.
- Outbound verdict via `agent.llm.run_typed` (Haiku, `sdk_timeout=3.0`); no hand-rolled Anthropic
  client, no coroutine-level `asyncio.wait_for`. `CONTEXT_RECALL_OUTBOUND_ENABLED` kill switch,
  default `true`, fresh `os.environ.get` read.
- All entry points fail open (return `None` / `advised=False`) on any exception.
- Tests must cover an **email** session end to end (advisory built for a `chat_id` of the form
  `someone@example.com`), not only Telegram.

### 3. Wire inbound delivery (both branches)
- **Task ID**: build-inbound-delivery
- **Depends On**: build-classifier, build-advisory
- **Validates**: `tests/unit/test_intake_classifier.py`, `tests/unit/test_context_recall.py`
- **Informed By**: spike-1 (open-ended banner; cold-path `steering_msgs[0]`; exact-match abort)
- **Assigned To**: delivery-builder
- **Agent Type**: builder
- **Domain**: untrusted-input
- **Parallel**: false
- Declare `_ctx_recall_advisory: str | None = None` **above** the `if not (is_reply_to_valor ...)` block
  at `bridge/telegram_bridge.py:2104`; assign it inside the `if active_sessions:` branch after `:2148`.
- `new_work`: merge at `:2405` with
  `extra_overrides = {**(extra_overrides or {}), "context_recall_advisory": _ctx_recall_advisory}`,
  guarded on `if _ctx_recall_advisory:` so the `None` override is preserved when there is no advisory.
  Strictly before `dispatch_telegram_session` (`:2484`).
- `agent/session_executor.py:1839-1842`: prepend advisory **before** the injection banner. Comment the
  ordering, citing `injection_inspection.py:206-210`.
- `interjection`: add keyword-only `context_advisory: str | None = None` to `_ack_steering_routed`
  (`:904-913`); emit a **separate** `push_steering_message` guarded on that param right after `:979`,
  `sender="intake-classifier"`, `is_abort=False`, `front=False`. Never append to the human's text.
- Pass `context_advisory=` from the `:2178` call site **only**. Leave `:1809`, `:1835`, `:1868`, `:2070`
  untouched — a test must assert those four still push exactly one steering message.
- Wrap both writes so any failure leaves current behavior intact.

### 4. Wire the outbound gate
- **Task ID**: build-outbound
- **Depends On**: build-advisory
- **Validates**: `tests/unit/test_message_drafter.py`, `tests/unit/test_promise_advisory.py`
- **Informed By**: spike-1 (`needs_self_draft` sites fire only post-objection; drafter is LLM-free)
- **Assigned To**: outbound-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/output_handler.py`, evaluate the check between `:703` and `:722`; branch on
  `draft.needs_self_draft or ctx_verdict.advised` (suppress-and-bounce, D2). Do not modify `MessageDraft`
  and do not mutate `draft`.
- **Keep the narration fallback keyed to its original trigger**: `if not steering_deferred and
  getattr(draft, "needs_self_draft", False): delivery_text = self._apply_narration_fallback(text)`.
  A context-recall-only bounce that fails to steer must leave `delivery_text` as `draft.text`
  (Technical Approach item 9). A test must assert exactly this.
- Leave the `else:` counter-reset arm at `:730` reachable only on the genuinely clean path — it must not
  run after a successful context-recall bounce, or the 2-attempt budget never accumulates.
- Add `context_advisory` as a keyword-only parameter to `_inject_self_draft_steering`; append it beside
  `promise_advisory` at `:1223-1225`.
- Add a distinct instruction preamble constant for context-recall-only bounces; keep
  `SELF_DRAFT_INSTRUCTION` verbatim when a real drafter violation is present.
- Do not touch `bridge/promise_gate.py`. Do not add an LLM import to `bridge/message_drafter.py`.

### 5. Register the three env keys end to end
- **Task ID**: register-env
- **Depends On**: build-classifier, build-advisory
- **Validates**: `tests/unit/test_env_completeness.py`
- **Informed By**: the `READ_THE_ROOM_ENABLED` / `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` precedents
- **Assigned To**: advisory-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `CONTEXT_RECALL_INBOUND_ENABLED=false`, `CONTEXT_RECALL_OUTBOUND_ENABLED=true`, and
  `CONTEXT_RECALL_HISTORY_DEPTH=10` to `.env.example`, each with a description comment on the line
  **immediately above** the key (required by
  `tests/unit/test_env_completeness.py::test_description_extracted_from_comment`). Follow the block shape
  at `.env.example:278` and `:284`.
- Do **not** add `config/settings.py` fields — see Technical Approach item 11 for the named deviation and
  its precedent.
- Confirm each key is actually read by the code that claims it, with a fresh `os.environ.get` using the
  same truthy set as `bridge/read_the_room.py:118`.
- Note in the PR description that all three keys must be added to the vault `~/Desktop/Valor/.env` at
  rollout, or `/update`'s `check_env_completeness` reports them missing on every machine.

### 6. Tune the inbound judgment against a committed fixture set
- **Task ID**: tune-inbound
- **Depends On**: build-classifier
- **Validates**: `tests/unit/test_context_recall.py`
- **Informed By**: spike-2 (5/10, misses all positives), spike-4 (8/14, few-shot inverts the failure)
- **Assigned To**: recall-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Commit a fixture set of **≥20 referent-free positives and ≥20 self-contained negatives**, drawn from
  real message shapes, not restatements of the prompt's own exemplars.
- Build an offline harness that scores a prompt revision against the fixtures.
- **Bar: recall ≥ 0.90 on positives and false-positive rate ≤ 0.25 on negatives.**
- Cap at **three** tuning iterations. If the bar is unmet after the third, stop. Per Decision D1 the
  inbound flag is already default-off, so an unmet bar is a *flag-flip decision deferred*, not a merge
  blocker and not grounds for further iteration.
- Live-model tests gated on Ollama reachability, exactly like the existing gated class; spike-3 showed
  per-run non-determinism, so assert aggregate scores over the fixture set, never a single message's verdict.

### 7. Validate the wiring
- **Task ID**: validate-wiring
- **Depends On**: build-inbound-delivery, build-outbound, register-env, tune-inbound
- **Assigned To**: recall-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Verification rows below, including the anti-criteria.
- Confirm the ordering test, the abort test, and the single-budget-bump test all exist and pass.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-wiring
- **Assigned To**: recall-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Execute every item in the Documentation section.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recall-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands, confirm every Success Criterion, generate the final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit/test_context_recall.py tests/unit/test_intake_classifier.py tests/unit/test_message_drafter.py tests/unit/test_promise_advisory.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New module exists | `test -f bridge/context_recall.py` | exit code 0 |
| Output handler wired | `grep -c "context_recall" agent/output_handler.py` | output > 0 |
| Bridge wired | `grep -c "context_recall" bridge/telegram_bridge.py` | output > 0 |
| Both inbound branches carry the flag | `grep -c "context_recall_advised" tools/classifier.py` | output > 3 |
| Anti-criterion: promise gate untouched | `git diff --quiet main -- bridge/promise_gate.py` | exit code 0 |
| Anti-criterion: read_the_room untouched | `git diff --quiet main -- bridge/read_the_room.py` | exit code 0 |
| Anti-criterion: drafter stays LLM-free | `grep -cE -e run_typed -e AsyncAnthropic -e anthropic bridge/message_drafter.py; true` | prints `0` |
| Anti-criterion: no placeholder in advisory | `grep -cE -e "<chat_id>" -e "\{chat_id\}" -e CHAT_ID_HERE -e placeholder bridge/context_recall.py; true` | prints `0` |
| Anti-criterion: advisory never appended to steer text | `grep -cE -e "text \+= .*advisory" -e "advisory.*\+ text" bridge/telegram_bridge.py; true` | prints `0` |
| Anti-criterion: no automatic history read | `grep -cE -e "subprocess.*valor-telegram" -e "os\.system.*valor-" bridge/context_recall.py; true` | prints `0` |
| Anti-criterion: no settings.py field for the toggles | `grep -c context_recall config/settings.py; true` | prints `0` |
| Env keys registered | `grep -c "^CONTEXT_RECALL_" .env.example` | prints `3` |

> **Why these rows use repeated `-e` instead of `|` alternation.** The previous revision of this plan
> shipped `grep -cE "run_typed\|AsyncAnthropic\|anthropic" bridge/message_drafter.py`. Inside `grep -E`,
> `\|` matches a **literal pipe character**, not alternation — verified live: that command prints `0` and
> exits `1` regardless of file contents, so four of the plan's strongest guarantees passed vacuously. An
> unescaped `|` cannot be used either, because it terminates the markdown table cell. Repeated `-e`
> patterns are OR'd by `grep` and sidestep both problems, so these rows are copy-pasteable as written.
> Each row is also suffixed `; true` because `grep -c` exits `1` on a zero count — a bare "exit code 0"
> expectation would fail on the *passing* case. Assert on the printed count, never the exit status.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The outbound context-recall call is placed "between `:703` and `:722`", which is inside the drafter's outer `try:` at agent/output_handler.py:700 whose `except Exception` at :785 swallows and skips the remaining body -- including `self._persist_routing_fields(session, draft)` at :783-784, the only writer of `session.context_summary` (:1300-1303). That field is exactly what this plan's own inbound gate 2 reads (tools/classifier.py:330 `if not session_context: return`) and what the shipped intake classifier reads at telegram_bridge.py:2150. A silent outbound failure therefore degrades intent routing to `new_work` for the next message in that chat and disables the inbound advisory. Item 8 states fail-open generically but never names this collateral, and no Failure Path test pins it. | pending | Wrap the call at its own site, not only inside the module: `try: ctx_verdict = await check_outbound_context_recall(...) except Exception: ctx_verdict = ContextRecallVerdict(advised=False)`, placed so `_persist_routing_fields` at :783-784 is still reached. Add to Failure Path Test Strategy: "an outbound check that raises still reaches `_persist_routing_fields`; `session.context_summary` is written." Task 2's module-level fail-open is necessary but not sufficient, because the plan's placement instruction puts the call under a swallowing handler whose skipped tail has a second, unrelated side effect. |
| CONCERN | History & Consistency | The rewritten anti-criterion `grep -cE -e "<chat_id>" -e "\{chat_id\}" -e CHAT_ID_HERE -e placeholder bridge/context_recall.py; true` expecting `0` is mutually unsatisfiable with two other plan requirements: (1) the Inline Documentation item mandates a "Docstring on `build_context_recall_advisory` stating the no-placeholder contract", which contains the word `placeholder`; (2) `-e "\{chat_id\}"` matches the literal characters `{chat_id}`, exactly what an f-string interpolating a variable named `chat_id` contains in source. Verified live against a file holding a natural implementation plus the mandated docstring: the row prints `2`. The previous revision fixed the `\|` alternation defect but reintroduced a false failure in the opposite direction. | pending | Assert on emitted output, not source bytes: `.venv/bin/python -c "from bridge.context_recall import build_context_recall_advisory as b; s=b(chat_id='-1003449100931', medium='telegram'); assert '<' not in s and '{' not in s and 'placeholder' not in s.lower(); print(0)"` expecting `0`. If a grep row is kept, drop `-e placeholder` and `-e "\{chat_id\}"` and keep only `-e "<chat_id>" -e CHAT_ID_HERE`, which cannot occur in a correct f-string implementation. Verify the replacement live before committing -- that is the step the previous revision skipped. |
| CONCERN | Scope & Value | After D1 (inbound default `false`) and D4 (inbound scoped to chats with a live-or-recent session carrying non-empty `context_summary`), the inbound half ships zero observable behavior, yet still carries Tasks 1, 3 and 6. Task 6 alone demands >=20 positive and >=20 negative committed fixtures, an offline scoring harness, and up to three tuning iterations against a live 3B model whose bar is explicitly "not a merge blocker and not grounds for further iteration". It is the largest work item in a Medium-appetite plan and its only consumer is an undated, unowned future flag-flip decision. | pending | The split is dependency-free: Task 6's only dependency is `build-classifier` and its only downstream is Task 7's criterion sweep. Drop `tune-inbound` from Task 7's `Depends On`, and replace the Success Criterion "Inbound tuning bar from task 6 is either met, or recorded as unmet with the flag left off" with "the inbound prompt extension exists and `CONTEXT_RECALL_INBOUND_ENABLED` defaults to `false`". Nothing in Tasks 3, 4 or 5 reads the fixture set -- which is itself evidence the corpus is not load-bearing for this PR. |
| CONCERN | Risk & Robustness | With `CONTEXT_RECALL_INBOUND_ENABLED` defaulting to `false` (D1) and Task 1 specifying "when off, the fields are always default and the prompt extension is not sent", the configuration running on every production inbound message is extended `IntentDecision` schema + unextended prompt -- a cell no spike measured. spike-2 and spike-4 measured extended schema + extended prompt; spike-3's no-regression control measured the unmodified 3-field schema + unmodified prompt. The untested combination sits on the hot path for every inbound message with an active session. | pending | `context_recall_advised: bool = False` and `context_recall_reason: str = ""` must carry those defaults so a granite response omitting them still validates under `run_typed_local(prompt, IntentDecision)`; an undefaulted field raises into the fail-open `except` at classifier.py:368-374 and silently flips every message to `new_work`, killing shipped interjection routing. Preferably select the unextended model when the switch is off (`IntentDecision` vs a separate `IntentDecisionWithRecall`) so schema and prompt can never disagree, and add a control run to Task 6 covering `INBOUND_ENABLED=false`. |
| CONCERN | Scope & Value | The outbound prefilter is specified only as "short text and contains `?`". "Short" has no value, name or owner, while the adjacent history depth got a full decision record (D3) and a named env-overridable constant precisely because the previous draft's `15` "was an invented number with no evidence behind it". The prefilter threshold is the more consequential number: it alone decides how much PM outbound traffic reaches a paid Haiku call and how much of Risk 5's latency exposure is real. | pending | Mirror item 10 exactly: `CONTEXT_RECALL_PREFILTER_MAX_CHARS = int(os.getenv("CONTEXT_RECALL_PREFILTER_MAX_CHARS", "<value>"))` in `bridge/context_recall.py` with the same provisional/tunable comment; add it as a fourth row to the Update System env-key table and to Task 5's `.env.example` registration. The Verification row `grep -c "^CONTEXT_RECALL_" .env.example` then expects `4`, not `3`. |
| CONCERN | Risk & Robustness | D1 makes the tuning bar "a flag-flip criterion rather than a merge gate" and Documentation requires recording "what evidence would justify flipping it on", but no task plans instrumentation that could produce that evidence in production: no counter for how often the outbound gate fires, how often an advisory is built, or how often a bounce yields a real answer. Task 6's offline harness measures the model, not the feature, and nothing would tell an operator the outbound gate has begun over-suppressing. | pending | The precedent is in the same file: `self._count_promise_advisory_issued(session_id)` at agent/output_handler.py:1225, defined at :1245. Add a sibling counter on the context-recall bounce path and a `logger.info` on `build_context_recall_advisory` success carrying `medium` and which command shape was emitted. Name it in Tasks 2 and 4 and in the Documentation flip-criterion item; without it the D1 flip decision rests on anecdote and the advisory-blindness mode D1 rejects is unobservable. |
| NIT | History & Consistency | The Update System section says the three env keys are "owned by task 9, not left to 'add the kill switch' prose inside tasks 1 and 2", but task 9 is `validate-all`; the owner is task 5, `register-env`. Renumbering residue from the revision that inserted `register-env`, pointing a reader at the wrong owner for the exact defect cycle 1 raised. | pending | Text-only edit: change "owned by task 9" to "owned by task 5 (`register-env`)". No other cross-reference cites task 9 for env registration -- Technical Approach item 11 and Task 5 both describe it correctly. |
| NIT | History & Consistency | Two residual descriptive inaccuracies survive the revision. (a) Risk 2's mitigation still says the budget "bounds the delay and then sends the original text", while item 9 and a Success Criterion establish a context-recall-only bounce sends `draft.text` and specifically not the raw pre-drafter text -- the same conflation cycle 1 flagged, corrected in Technical Approach but not swept into Risks. (b) The Verification row labelled "Both inbound branches carry the flag" greps `tools/classifier.py`, which contains neither delivery branch; it verifies return-path threading, not two-branch delivery. | pending | Reword Risk 2 to say "sends `draft.text`", and relabel the grep row "All four classifier return paths carry the flag". Both are wording edits, but (a) matters because a builder reading only Risks would reimplement the defect item 9 exists to prevent; the two-branch guarantee is already covered by its own Success Criterion. |

---

## Decisions

The three questions the first draft left open are now decided. They are recorded here rather than
deleted, because each one is a live constraint on the build, not settled trivia.

**D1 — Inbound ships dark. `CONTEXT_RECALL_INBOUND_ENABLED` defaults to `false`.**
Spikes 2 and 4 scored 5/10 and 8/14 with opposite failure modes, so the granite judgment may well miss
the bar. Rejected alternatives: escalating inbound to Haiku puts a paid Anthropic call on *every* inbound
Telegram message and reverses #2494's deliberate move to local granite — the cost rationale that
motivated that work has not changed; and shipping recall-biased trades a real problem for advisory
blindness plus a wasted history read per message. Dark ships the code, keeps the outbound half (which
uses Haiku and is unaffected) live, and makes the tuning bar a flag-flip criterion rather than a merge
gate. Flipping it on later is a config change on one machine, reversible in seconds.

**D2 — The outbound gate suppresses and bounces; it does not merely annotate.**
Advisory-only would attach the command to the PM's next turn *and still send the question*, so the human
is still asked "which one?" — the exact round trip issue #2694 exists to eliminate. The cost is bounded
and already implemented: `SELF_DRAFT_MAX_ATTEMPTS = 2` (`agent/steering.py:255`) caps it at two PM turns,
`deferred_self_draft_text` preserves the question if the session dies mid-bounce, and after the budget
the question reaches the human anyway. Task 4 was already specified for suppression, so this decision
confirms the existing shape rather than changing it.

**D3 — History depth is `10`, via a named env-overridable constant.**
`valor-telegram read` already defaults to `--limit 10` (`tools/valor_telegram.py:271`). The previous
draft's `15` was an invented number with no evidence behind it. Matching the CLI default removes the
invention; `CONTEXT_RECALL_HISTORY_DEPTH` (default `10`, provisional-tunable comment) makes it adjustable
without a code change if 10 proves too shallow, per the repo's magic-number convention.

**D4 — Inbound coverage is scoped to sessions-with-context, and the gap is accepted.**
Closing it means a granite call on every inbound Telegram message. That is the same cost objection as D1,
for a strictly smaller payoff: the residual case is a human sending a bare acknowledgment into a chat
where nothing is running. The scope, both gates, and the uncovered case are documented in Problem,
No-Gos, Success Criteria (with a test pinning today's behavior in the uncovered case), and the feature
doc.

## Revision Notes (critique cycle 1)

Every finding in the Critique Results table above was **re-verified against the code before being
adopted** — the routed resolutions were treated as claims, not instructions. Two corrections to what was
handed down:

1. **Inbound scope is narrower than described.** The routed instruction scoped the advisory to "an
   active/running/dormant/recent-pending session exists in this chat". That is gate 1 only. Gate 2 —
   `tools/classifier.py:330-335`, `if not session_context: return` before any model call — means an active
   session with an **empty `context_summary`** also produces no verdict. The plan states the intersection
   of both gates.
2. **No `config/settings.py` field for the new toggles.** Both the critique note and the routed
   instruction called for one. The two nearest in-repo precedents contradict it: `READ_THE_ROOM_ENABLED`
   and `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` are `.env.example`-declared, non-secret toggles read via
   fresh `os.environ.get` with no settings field at all. A cached `pydantic-settings` field is the wrong
   shape for a kill switch. Following the code; deviation named in Technical Approach item 11.

Also confirmed rather than assumed: the `extra_context` seed at `bridge/telegram_bridge.py:2405` **is
still reachable** under the narrowed scope — a message in a chat with an active, context-carrying session
that classifies `new_work` falls through `:2178` to the enqueue path at `:2484`. The task was kept, not
deleted.

**Provenance caveat.** The critique that produced these findings ran without an Agent-spawn tool, so its
three war-room lenses were executed by a single runner rather than three independent critics. The
grounding gate passed (`ungrounded: []`) and every finding above was independently re-verified here, but
the usual cross-lens disagreement signal was absent — a second critique pass is more likely than usual to
surface something new.
