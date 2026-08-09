---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-09
tracking: https://github.com/tomcounsell/ai/issues/2694
last_comment_id: none
---

# Context-Recall Advisory Flag (inbound intake classifier + outbound PM-message gate)

## Problem

The PM session acts on messages it does not have the context to understand, on both edges of the
conversation, and nothing in the system ever tells it to go read the recent chat history.

**Inbound.** A human sends `"yes"`, `"go ahead"`, `"not that one"`, `"the second one"`. The intake
classifier already inspects every one of these messages and forms a judgment about them — and then
throws that judgment away. Only `intent` is branched on (`bridge/telegram_bridge.py:2164`);
`confidence` and `reason` are consumed by a single `logger.info` at `:2158-2162` and never reach the
session. The PM receives the literal string `yes` with no hint that context is missing.

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
| Hot path joins all steers | Exact (`runner.py:1305-1313`) | — |
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
  detail to a first-class task with a measurable bar (task 5) and a named fallback.

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
  test must be written (task 5).

### spike-4: Does few-shot fix the judgment (the #1318 remedy)?
- **Assumption**: "Few-shot examples close the gap, as they did in #1318."
- **Method**: prototype (rewritten prompt with 5 positive + 4 negative exemplars, 14 cases)
- **Finding**: **Partially, and it inverts the failure mode. 8/14.** All 8 short referent-free
  positives now hit (perfect recall). But 5 of 6 self-contained negatives flipped to
  `advised=True` — including `"How does the auth system work?"` and
  `"Fix the login bug on the settings page"`. The exemplars anchored the model toward `true`.
- **Confidence**: high
- **Impact on plan**: Proves both failure modes are individually reachable but that hitting them
  *simultaneously* on a 3B model is genuine, uncertain work. This is why task 5 has an iteration
  cap and an escalation path, and why Open Question 1 exists.

## Data Flow

**Inbound — `new_work` branch (Telegram only):**
1. **Entry**: Telethon `handler(event)`, `bridge/telegram_bridge.py:1166`. `telegram_chat_id` bound at `:1620`.
2. **Injection inspection**: `_injection_ctx` seeded at `:1335` with `injection_risk_banner`.
3. **Intake classifier**: `classify_message_intent_async` at `:2148` → dict now carrying
   `context_recall_advised` / `context_recall_reason`.
4. **Advisory build**: on `advised`, `build_context_recall_advisory(chat_id, medium="telegram")` →
   text with the real command.
5. **Context seed**: written into `extra_overrides` alongside the injection reseed at `:2405`.
6. **Enqueue**: `dispatch_telegram_session(...)` at `:2484`, `extra_context_overrides=extra_overrides`.
7. **Session pickup**: `agent/session_executor.py:1839-1842` reads `extra_context` and prepends
   **advisory first, then the injection banner, then the untrusted text**.
8. **Output**: PM's first turn opens with the advisory, outside the untrusted zone.

**Inbound — `interjection` branch:**
1-4 as above, then:
5. **Human's message** pushed unmodified: `push_steering_message(session_id, text, ..., is_abort=...)`
   at `bridge/telegram_bridge.py:979`.
6. **Advisory** pushed as a **separate** message, `front=False`, `is_abort=False`,
   `sender="intake-classifier"`.
7. **Drain**: hot path `_drain_steering_boundary` (`runner.py:1290`) keeps both (`_steer_is_substantive`
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
7. **Output**: PM reads history and resends. On budget exhaustion (2 attempts) or any error, the
   original question is sent to the human unchanged.

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
- **Reversibility**: high. Both edges are gated by their own kill-switch env var
  (`CONTEXT_RECALL_INBOUND_ENABLED`, `CONTEXT_RECALL_OUTBOUND_ENABLED`, both default-on); flipping
  either to `false` restores current behavior exactly with no code revert.
- **Preserved invariant**: `bridge/message_drafter.py` stays LLM-free (`:205`). The outbound LLM call
  lives in `agent/output_handler.py`'s caller, not inside `draft_message`. This is a deliberate
  deviation from the issue's sketch — see Technical Approach.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (Open Question 1 — the granite-vs-Haiku cost call — needs a human answer)
- Review rounds: 2 (the injection-banner ordering and the outbound bounce semantics both warrant review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Ollama running with `granite4.1:3b` | `curl -s --max-time 5 http://localhost:11434/api/tags \| grep -q granite4.1:3b` | Inbound classifier transport (live tests only) |
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

### Flow

**Inbound:** Human sends `"go ahead"` → intake classifier judges `intent` *and*
`context_recall_advised` in one pass → advisory built with the real chat id → delivered via
`extra_context` (new session) or a second steering message (running session) → PM's turn opens with
*"This message may depend on earlier conversation. Read it with: `valor-telegram read --chat-id -1003449100931 -n 15`"* → PM reads, acts correctly.

**Outbound:** PM writes `"which PR do you mean?"` → prefilter passes (short, contains `?`) → Haiku
judges it a referent-clarification → message is **held**, not sent → PM receives the advisory with the
command through the self-draft channel → PM reads history, resends a real answer → answer delivered.
If the PM insists twice, the budget is spent and the question goes to the human anyway.

### Technical Approach

**1. Inbound schema and prompt (`tools/classifier.py`).**
Add `context_recall_advised: bool = False` and `context_recall_reason: str = ""` to `IntentDecision`
(`:295-297`). Extend `INTENT_CLASSIFICATION_PROMPT` (`:249-280`) with a context-recall section whose
test is a *question about referents*, not a word list: **"reading only the message text, do you know
what thing it is talking about?"** No keyword allowlist (Development Principle 3).

Thread the two keys through **all four** return paths — and note the third is not a literal:
- `:327` `{"intent": "new_work", ..., "reason": "Empty message"}` → add defaults `False` / `""`
- `:331-335` `"No active session context"` → add defaults
- `:351-355` the **dict construction** from the `IntentDecision` → map `decision.context_recall_advised`
  and `decision.context_recall_reason`. The threshold clamp at `:363-364` must touch `intent` only and
  must not clobber the new keys. Returned at `:366`.
- `:370-374` the fail-open `except` path → add defaults `False` / `""`

**2. The advisory builder (`bridge/context_recall.py`).**
```
build_context_recall_advisory(*, chat_id, medium, reason=None) -> str | None
```
Returns `None` (no advisory, fail open) when the chat id is unusable. Usability is decided by the
existing helper, **not** by literal comparison — `utils.peer.deliverable_telegram_peer` is what
`agent/output_handler.py:495` actually enforces; the `"0"` / `== session_id` literals live only in a
docstring. Also return `None` for empty/`None` chat ids.

Commands, taken from the verified argparse definitions:
- Telegram (`tools/valor_telegram.py:1316-1353`): `valor-telegram read --chat-id <real id> -n 15`
- Email (`tools/valor_email.py:798-810`): the `read` parser has **no per-peer flag** — only
  `--mailbox`, `--limit/-n`, `--search/-s`, `--since`, `--json`. So emit
  `valor-email read --search "<peer address>" -n 15` when the peer address is resolvable from the
  session, and fall back to `valor-email threads` when it is not. Never emit a placeholder.

**3. Inbound delivery — `new_work` (ordering is the hazard).**
Seed `extra_context["context_recall_advisory"]` alongside the existing injection reseed at
`bridge/telegram_bridge.py:1978` / `:2405`, then read it at `agent/session_executor.py:1839-1842`.

The ordering is **advisory → injection banner → untrusted text**, and this is not arbitrary.
`build_risk_banner` returns an *open-ended prefix* ending in
`----- SCREEN DELIMITER (untrusted content follows) -----`; there is **no closing delimiter**, so the
untrusted zone runs to the end of the prompt. Placing the advisory *after* the banner would put
bridge-authored trusted text inside the zone the banner just declared untrusted — the PM would
rightly distrust it, and an attacker could forge an identical line. Placing it *before* the banner
keeps the banner's contract intact (it still precedes all untrusted content, `injection_inspection.py:206-210`)
because a trusted-bridge prefix is not attacker-authorable.

**4. Inbound delivery — `interjection` (two hazards).**
Push the advisory as a **separate** `push_steering_message` call after the human's message at
`bridge/telegram_bridge.py:979`, with `sender="intake-classifier"`, `is_abort=False`, `front=False`.

- *Separate, never appended*: `agent/steering.py:113` matches `ABORT_KEYWORDS` by **exact full string**
  after strip/lower. Appending an advisory to a bare `"stop"` silently destroys abort.
- *Back, not front* — this reverses the issue's suggestion. The `session_health.py:3463` `front=True`
  precedent is unsafe here: the cold-path drain (`agent/session_executor.py:1919`) consumes only
  `steering_msgs[0]` and re-queues the remainder at the back, so a front-pushed advisory would
  **displace the human's own message** for that turn. At the back, the human's message is always
  consumed first and the advisory is at worst one turn late.

Both survive the hot path: `_steer_is_substantive` (`runner.py:1232-1235`) admits any non-empty text,
and `_merge_steers` joins all of them.

**5. Outbound gate — placement, and why it deviates from the issue.**
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
exception, timeout, missing chat id, or disabled kill switch degrades to exactly today's behavior. The
outbound path additionally inherits the existing budget: after `SELF_DRAFT_MAX_ATTEMPTS = 2`,
`_inject_self_draft_steering` returns `False` and the original message is sent
(`agent/output_handler.py:722-728`). Nothing in this plan can drop a message, and nothing in it can
reach `cli_check_or_exit` — `bridge/promise_gate.py` is not modified at all.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every new `except Exception` (classifier field extraction, advisory build, outbound LLM call,
      both inbound delivery writes) must log at `warning`/`debug` and be covered by a test asserting
      the observable degradation — message still delivered, no advisory attached. No bare `pass`.
- [ ] Test that a raised `LLMCallError` from the outbound check results in the message being **sent**,
      not held.

### Empty/Invalid Input Handling
- [ ] `build_context_recall_advisory` with `chat_id` of `None`, `""`, `"0"`, and `chat_id == session_id`
      → returns `None`, no advisory, message unaffected.
- [ ] Empty and whitespace-only inbound messages → the `:327` early return path carries the new keys
      with defaults.
- [ ] Empty PM output on the outbound edge → prefilter rejects, no LLM call, no bounce (guards against
      an empty-output self-draft loop).

### Error State Rendering
- [ ] Advisory text is asserted to contain the **literal chat id** and a runnable command — a test
      asserts the string contains no `<`, `{`, or the word `placeholder`.
- [ ] Budget-exhaustion path asserts the user receives the PM's original question rather than silence.

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
- [ ] `tests/unit/test_context_recall.py` — CREATE: advisory builder, prefilter, verdict fail-open,
      chat-id guards, and the committed judgment fixture set.
- [ ] Live-model tests must be gated exactly as the existing Ollama-reachability class in
      `test_intake_classifier.py` — spike-3 showed the model is non-deterministic across runs, so a
      live test asserting a single message's verdict would be flaky.

## Rabbit Holes

- **Prompt-tuning the 3B model to perfection.** Spikes 2 and 4 landed at 5/10 and 8/14 with opposite
  failure modes. Task 5 caps this at three iterations against a fixed fixture set; if the bar is not
  met, escalate per Open Question 1 rather than iterating indefinitely.
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
**Mitigation:** Task 5 makes it a measurable gate, not a hope: a committed fixture set with a stated
bar, capped at three tuning iterations. Escalation is pre-decided (Open Question 1), and the inbound
kill switch means a failed tuning pass ships dark rather than shipping noise.

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

Nothing deferred — every relevant item is in scope for this plan. The three items below are permanent
design boundaries, not work being postponed, and each is pinned by a Verification anti-criterion where
it is mechanically checkable.

The PM is never made to read history **automatically**. This plan raises a flag and hands over the
command; the PM decides. Auto-invoking a history read on the agent's behalf would make an advisory into
a control-flow change and is explicitly not the goal.

`READ_THE_ROOM_ENABLED` stays default-off and its DM exclusion is untouched. Recon confirmed it is
unusable for this purpose (`bridge/read_the_room.py:118`, `:526-528`); this plan neither enables nor
modifies it.

Inbound classification for **email** is not added, because the intake classifier has no email caller
today (`bridge/email_bridge.py` never references it). Adding one is a routing change to the email
bridge with its own risk surface. The *outbound* edge does cover email, because the drafter path is
shared across both transports.

## Update System

No update system changes required. This feature adds no new dependencies, no config files, and no
migrations. The two new env vars (`CONTEXT_RECALL_INBOUND_ENABLED`, `CONTEXT_RECALL_OUTBOUND_ENABLED`)
are optional kill switches that default to enabled, so an un-updated `.env` behaves correctly; they are
read via `config/settings.py` conventions and need no `.env.example` secret entry (they are not
secrets, but add them to `.env.example` as documented toggles).

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
      cost-asymmetry rationale, the kill switches, and every fail-open path.
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
      back-pushed steering message for `interjection`.
- [ ] A `"stop"` message that also trips the advisory still produces `is_abort=True`.
- [ ] Composed `new_work` prompt orders advisory → injection banner → untrusted text.
- [ ] Outbound check fires on a clean referent-clarifying question (one that produces
      `needs_self_draft=False` from the drafter) and its advisory arrives via the self-draft channel.
- [ ] `bridge/promise_gate.py` is untouched; `cli_check_or_exit` behavior is byte-identical.
- [ ] `bridge/message_drafter.py` imports no LLM transport.
- [ ] Advisory text on both edges contains a fully-formed command with the real chat id and no placeholder.
- [ ] Both edges fail open: classifier error, timeout, missing chat id, or disabled kill switch all
      degrade to current behavior and never block a message.
- [ ] Inbound judgment meets the accuracy bar in task 5, or Open Question 1 has been answered and the
      chosen path implemented.
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
  (`tools/classifier.py:295-297`).
- Extend `INTENT_CLASSIFICATION_PROMPT` (`:249-280`) with a referent-based context-recall section. No
  keyword allowlist.
- Add both keys to the literal returns at `:327`, `:331-335`, `:370-374` and to the **dict construction**
  at `:351-355` (mapping from the `IntentDecision`). Confirm the clamp at `:363-364` touches `intent` only.
- Add the `CONTEXT_RECALL_INBOUND_ENABLED` kill switch; when off, the fields are always default.
- Update `tests/unit/test_intake_classifier.py` `_fake_decision` and expected dicts.

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
- Telegram command `valor-telegram read --chat-id <id> -n 15`; email
  `valor-email read --search "<peer>" -n 15` with a `valor-email threads` fallback.
- Guard unusable chat ids via `utils.peer.deliverable_telegram_peer`, not literal comparisons; return
  `None` on any guard hit.
- Outbound verdict via `agent.llm.run_typed` (Haiku, `sdk_timeout=3.0`); no hand-rolled Anthropic
  client, no coroutine-level `asyncio.wait_for`. `CONTEXT_RECALL_OUTBOUND_ENABLED` kill switch.
- All entry points fail open (return `None` / `advised=False`) on any exception.

### 3. Wire inbound delivery (both branches)
- **Task ID**: build-inbound-delivery
- **Depends On**: build-classifier, build-advisory
- **Validates**: `tests/unit/test_intake_classifier.py`, `tests/unit/test_context_recall.py`
- **Informed By**: spike-1 (open-ended banner; cold-path `steering_msgs[0]`; exact-match abort)
- **Assigned To**: delivery-builder
- **Agent Type**: builder
- **Domain**: untrusted-input
- **Parallel**: false
- `new_work`: seed `extra_context["context_recall_advisory"]` alongside the injection reseed at
  `bridge/telegram_bridge.py:1978` / `:2405`, strictly before `dispatch_telegram_session`.
- `agent/session_executor.py:1839-1842`: prepend advisory **before** the injection banner. Comment the
  ordering, citing `injection_inspection.py:206-210`.
- `interjection`: a **separate** `push_steering_message` after `:979`, `sender="intake-classifier"`,
  `is_abort=False`, `front=False`. Never append to the human's text.
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
  `draft.needs_self_draft or ctx_verdict.advised`. Do not modify `MessageDraft` and do not mutate `draft`.
- Add `context_advisory` as a keyword-only parameter to `_inject_self_draft_steering`; append it beside
  `promise_advisory` at `:1223-1225`.
- Add a distinct instruction preamble constant for context-recall-only bounces; keep
  `SELF_DRAFT_INSTRUCTION` verbatim when a real drafter violation is present.
- Do not touch `bridge/promise_gate.py`. Do not add an LLM import to `bridge/message_drafter.py`.

### 5. Tune the inbound judgment against a committed fixture set
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
- Cap at **three** tuning iterations. If the bar is unmet after the third, stop and escalate per Open
  Question 1 — do not keep iterating.
- Live-model tests gated on Ollama reachability, exactly like the existing gated class; spike-3 showed
  per-run non-determinism, so assert aggregate scores over the fixture set, never a single message's verdict.

### 6. Validate the wiring
- **Task ID**: validate-wiring
- **Depends On**: build-inbound-delivery, build-outbound, tune-inbound
- **Assigned To**: recall-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Verification rows below, including the anti-criteria.
- Confirm the ordering test, the abort test, and the single-budget-bump test all exist and pass.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-wiring
- **Assigned To**: recall-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Execute every item in the Documentation section.

### 8. Final validation
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
| Anti-criterion: drafter stays LLM-free | `grep -cE "run_typed\|AsyncAnthropic\|anthropic" bridge/message_drafter.py` | match count == 0 |
| Anti-criterion: no placeholder in advisory | `grep -cE "<chat_id>\|\{chat_id\}\|CHAT_ID_HERE" bridge/context_recall.py` | match count == 0 |
| Anti-criterion: advisory never appended to steer text | `grep -cE "text \+= .*advisory\|advisory.*\+ text" bridge/telegram_bridge.py` | match count == 0 |
| Anti-criterion: no automatic history read | `grep -cE "subprocess.*valor-telegram\|os.system.*valor-" bridge/context_recall.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **If granite cannot hit the accuracy bar, what do we do?** This is the plan's one genuinely open
   decision, and the spikes make it likely to come up: two prompt attempts scored 5/10 and 8/14 with
   opposite failure modes. Three options, all implementable:
   (a) **Escalate inbound to Haiku** via `run_typed` — reliable, but adds an Anthropic call to *every*
   inbound Telegram message, reversing the #2494 move to local granite and its cost rationale;
   (b) **Ship recall-biased** — accept over-triggering, so the PM sees an advisory on most messages
   and learns to skim past it (advisory blindness, and a wasted history read per message);
   (c) **Ship inbound dark** — merge behind `CONTEXT_RECALL_INBOUND_ENABLED=false`, land the outbound
   half (which uses Haiku and is unaffected), and revisit inbound separately.
   My recommendation is (c) then (a) if the signal proves valuable, because it never degrades the
   current experience. This needs your call on the cost tradeoff.

2. **Is holding the PM's clarifying question the behavior you want?** The outbound gate does not merely
   annotate — it *suppresses* the message and bounces it back to the PM, which is what the issue's
   desired outcome ("instead of guessing or asking") implies. The cost is up to two extra PM turns
   before a genuinely-necessary question reaches you. The alternative is advisory-only (attach the
   command to the PM's next turn but send the question now), which never delays you but also never
   prevents the wasted round trip. I have planned for the suppressing version.

3. **`-n 15` as the default history depth** — the `read` default is 10. Fifteen is a guess that a
   referent is usually within the last handful of messages. Happy to change it, or to make it an env-tunable
   constant per the repo's provisional-magic-number convention.
