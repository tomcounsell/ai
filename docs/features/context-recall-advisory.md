# Context-Recall Advisory

The PM session routinely acts on a message without the conversation context needed to understand it, and before #2694 nothing in the system ever told it to go read the recent history. This feature raises an advisory on both edges of the conversation and hands the PM a **fully-formed, copy-pasteable command with the real chat id already interpolated** — never a placeholder, never a description of a tool.

The PM decides whether to run it. Nothing reads history on its behalf.

## Why it exists

The gap is symmetric.

**Inbound.** A human sends `"yes"`, `"go ahead"`, `"not that one"`, `"the second one"`. These are short, referent-free, and meaningful only against the preceding conversation. The intake classifier already inspected every one of them and formed a judgment — then threw it away. Only `intent` was branched on; `confidence` and `reason` reached a single `logger.info` and stopped there. The PM received the literal string `yes`.

**Outbound.** The PM writes `"which PR do you mean?"` and sends it. That burns a full human round trip asking for context sitting in a message store the PM could have read itself. Nothing inspected outbound text for this.

## Scope and known limitation

**The inbound advisory does not fire on every incoming message, and this is a deliberate boundary rather than an oversight.** It fires only when the chat already holds a live-or-recent session carrying a non-empty `context_summary`. Two gates enforce that, and both sit upstream of any model call:

| Gate | Where | Effect |
|------|-------|--------|
| `if active_sessions:` | `bridge/telegram_bridge.py` (encloses both the `classify_message_intent_async` call and the `_build_context_recall_advisory_for_intent` call that follows it) | A chat with no `running` / `active` / `dormant` session — and no `pending` session inside `PENDING_MERGE_WINDOW_SECONDS`, currently 8 — never reaches the classifier at all, so no inbound advisory is possible |
| `if not session_context:` | `tools/classifier.py` | Hard-returns `{"intent": "new_work", "reason": "No active session context", "context_recall_advised": False, ...}` before any model call, so a session whose `context_summary` is empty yields no context-recall verdict either |

So the canonical motivating case — a bare `"yes"` arriving into a chat with a live session — is covered, while the same `"yes"` arriving cold into a quiet chat is not.

**Why universal coverage was rejected.** Removing either gate would put a granite call on *every* inbound message rather than only those with a session to interject into. That is a per-message cost on the bridge's hot path bought for a judgment that, in the cold case, has no conversation context to reason against anyway. The gates predate this feature and are load-bearing for the intake classifier's own latency budget; #2694 rides the existing pass rather than widening it (plan decision D4).

## Architecture

```
INBOUND (Telegram only)                    OUTBOUND (Telegram + email)
        |                                           |
intake classifier                          TelegramRelayOutputHandler.send()
(granite, one pass —                               |
 no second LLM call)                        draft_message() -> MessageDraft
        |                                           |
 context_recall_advised?                    _prefilter(): non-empty,
        |                                   <=200 chars, contains "?"
        |                                           |
        |                                   check_outbound_context_recall()
        |                                   (Haiku via agent.llm.run_typed)
        |                                           |
        +----------> build_context_recall_advisory() <----------+
                     (bridge/context_recall.py — the SINGLE
                      definition, so the two edges cannot drift)
                                    |
        +---------------------------+---------------------------+
        |                           |                           |
  new_work:                  interjection:               outbound:
  extra_context              separate steering           self-draft steering
                             message at the back         (needs_self_draft=True)
```

## The advisory

`build_context_recall_advisory(*, chat_id, medium="telegram", reason=None) -> str | None` owns the only definition of the advisory text.

**No-placeholder contract.** The returned string always carries a runnable command with the real chat id interpolated. When the chat id is unusable for the medium it returns `None` — an advisory pointing at a command that cannot run is worse than no advisory.

| Medium | Emitted command |
|--------|-----------------|
| `telegram` | `valor-telegram read --chat-id <peer> -n <depth>` |
| `email` | `valor-email read --search <address> -n <depth>` |

`CONTEXT_RECALL_HISTORY_DEPTH` (default 10) matches `valor-telegram read`'s own `--limit` default, so the advisory never invents a depth the CLI disagrees with. Both media read the same constant.

### Chat-id validation is medium-aware

The guard branches on `medium` **before** it validates. `utils.peer.deliverable_telegram_peer` accepts only a nonzero integer peer, but email sessions carry `chat_id = from_addr`, so running the Telegram guard on an email session would reject every one of them and the email leg would be dead on arrival. Telegram session ids are rejected only incidentally: session ids are non-numeric, so the numeric peer parse fails. Nothing compares `chat_id` to `session_id`.

### Two attacker-influenceable values, both neutralized

Both are worth naming because both reach a trusted position.

**The `reason` string** is written by an LLM while reading an attacker's message, and on the inbound edge the advisory carrying it is prepended *ahead of* the injection-screen banner — the outermost authoritative zone of the PM's prompt. It is sanitized through `bridge.injection_inspection._sanitize_reason`, which collapses whitespace and newlines and clamps length, exactly as `build_risk_banner` does for its own verdict. That sanitization, not mere bridge authorship, is what earns the pre-banner position. A whitespace-only reason is treated as absent so the sanitizer's injection-flavored fallback wording cannot leak onto an edge that has nothing to do with prompt injection.

**The `chat_id`** is interpolated into a shell command that the advisory explicitly tells a Bash-capable PM to run, and on the email leg that value is the sender's `From` address. A backtick is a valid RFC 5322 `atext` character, so `` bob`id`@example.com `` is an ordinary deliverable address that `email.utils.parseaddr` preserves verbatim; quoted-local-part addresses can break out of the argument entirely. Routing keys only on the domain, and the email bridge performs no SPF/DKIM/DMARC check, so the local part is attacker-controlled. Every interpolation therefore goes through `_shell_safe`, which collapses whitespace and applies `shlex.quote`. The whitespace collapse also stops a newline-bearing address from splitting the advisory across lines into a forged authoritative line. `_looks_like_a_bare_email_address` adds a conservative shape check on top; it is defense in depth, not the sole line of defense.

## Inbound edge

Ships **dark**. See [Intake Classifier §Context-recall advisory](intake-classifier.md#context-recall-advisory-2694) for the delivery mechanics and the per-branch table.

**What would justify flipping `CONTEXT_RECALL_INBOUND_ENABLED` on.** A scored fixture corpus, at least 20 positives and 20 negatives drawn from real traffic, on which the granite prompt clears the bar in both directions at once. Both failure modes are already observed and they trade against each other: the first prompt scored 5/10 and missed every canonical positive, and a few-shot revision fixed all 8 positives but then over-triggered on 5 of 6 self-contained messages. Recall alone is not sufficient evidence — an over-triggering advisory trains the PM to ignore it, which is worse than silence. Building that corpus is descoped from #2694 (plan Task 6); nothing in the shipped code reads it, and its only consumer is this decision. A named fallback if granite cannot be tuned to acceptable precision: move the inbound judgment to Haiku, as the outbound edge already does.

## Outbound edge

`check_outbound_context_recall(text) -> ContextRecallVerdict` judges whether the text asks the human to supply context recoverable from history. On a positive verdict `agent/output_handler.py` suppresses the send and bounces the message back through the existing self-draft steering loop.

**Haiku rather than granite here.** A false positive on this edge suppresses a real message and costs a human round trip, so precision is worth the paid call. A structural prefilter keeps that call off the hot path: the text must be non-empty, at most `CONTEXT_RECALL_PREFILTER_MAX_CHARS` (default 200), and contain a `?`. The gate is on *shape*, never on intent keywords. Anything longer is carrying its own context and does not need history.

The check **sets** `needs_self_draft` rather than riding an existing one, because a clean clarifying question returns `needs_self_draft=False` and would otherwise be sent — the exact case the feature exists for. See [Message Drafter §Context-recall instruction](message-drafter.md#context-recall-instruction-issue-2694).

### What it deliberately is not built on

| Rejected | Why |
|----------|-----|
| Extending the promise gate's LLM pass | Its verdict is consumed by `cli_check_or_exit`, which calls `sys.exit(1)` on a block across five CLI call sites. A context-recall block would hard-fail a legitimate `python -m tools.send_message "which one do you mean?"`. There is also no shared call to piggyback on: the drafter path uses the regex-only `_evaluate_promise_heuristic`, never the LLM. |
| `bridge/read_the_room.py` | Default-off, and it excludes DMs outright — precisely where "which one?" is most likely. It has no PM feedback path at all. |
| An LLM call inside `draft_message` | The drafter is deliberately LLM-free; putting a model call inside it would reverse a shipped architectural decision. |

Both modules are byte-identical to `main`.

## Failing open

Every failure mode on both edges degrades to exactly the pre-#2694 behavior and never blocks, drops, or delays a message: kill switch off, prefilter reject, unusable chat id, timeout, provider error, schema exhaustion, or any unexpected exception. On the inbound edge every classifier return path — including the fail-open `except` branch — carries safe defaults, so callers never `KeyError`.

Self-draft budget exhaustion is also safe. `SELF_DRAFT_MAX_ATTEMPTS` is 2, and on exhaustion the original message **is sent**. It is never dropped.

## Configuration

Four keys in `.env.example`, with **no `config/settings.py` field** — matching the `READ_THE_ROOM_ENABLED` and `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` precedents. The two switches are read fresh from the environment on every call rather than cached at import, so either can be flipped without a process restart.

| Key | Default | Purpose |
|-----|---------|---------|
| `CONTEXT_RECALL_INBOUND_ENABLED` | `false` | Inbound edge. Ships dark pending accuracy work on the local model. |
| `CONTEXT_RECALL_OUTBOUND_ENABLED` | `true` | Outbound edge. Set `false` to restore pre-#2694 send-everything behavior. |
| `CONTEXT_RECALL_HISTORY_DEPTH` | `10` | Messages the emitted command asks for. |
| `CONTEXT_RECALL_PREFILTER_MAX_CHARS` | `200` | Longest PM output still eligible for the paid outbound call. This alone decides how much traffic reaches a billed request. |

All four must also be added to the vault `~/Desktop/Valor/.env`, or `check_env_completeness` reports them missing on every `/update`.

## Testing

`tests/unit/test_context_recall.py` covers the builder and the outbound verdict: the no-placeholder contract, medium-aware chat-id validation, reason sanitization including the whitespace-only case, shell-metacharacter neutralization, and fail-open for a provider error, kill-switch-off, prefilter reject, and an unusable chat id.

`tests/unit/test_context_recall_wiring.py` covers the seams: both inbound delivery branches, banner ordering, abort survival, budget exhaustion, a failing advisory steering push, and fail-open for a raising check and a failing import (`TestPersistRoutingFieldsSurvivesAFailingCheck`, plus `test_raising_check_sends_the_message`). Two pins are worth knowing about. `TestEmittedCommandIsRunnable` parses the emitted command with `valor_telegram`'s own argparse via `build_parser()`, so the advisory cannot silently rot if `read`'s flags change. `TestCallSitesWireUpBeforeDispatch` is a source-index pin asserting both `bridge/telegram_bridge.py::main()` call sites exist and precede the dispatch that consumes them — earlier rounds of this feature had helpers that were unit-tested while the call sites themselves could be deleted with the suite still green.

**Pytest guard.** `tests/conftest.py` carries an autouse fixture that stubs the outbound check inert, so the suite makes no live Haiku calls. Without it, any `send()` test whose drafted text is a short question issued a real billed request, and the multi-second network await reordered a concurrency test into a coin flip. It mirrors the `SENTRY_DSN=""` pre-seed in the same file, which exists for the identical class of problem — a module-import side effect leaking real traffic out of the suite. Production code is untouched and the feature still defaults to enabled in production. Set `CONTEXT_RECALL_OUTBOUND_STUB=0` to run the real check and reproduce the original symptom.

## Out of scope

- Making the PM automatically read history. This feature raises a flag and hands over the command.
- Inbound classification for email. The intake classifier has no email caller.
- Enabling `READ_THE_ROOM_ENABLED` or changing its DM exclusion.

## Files

| File | Purpose |
|------|---------|
| `bridge/context_recall.py` | Advisory builder, `_shell_safe`, kill switches, prefilter, outbound verdict |
| `tools/classifier.py` | `IntentDecisionWithRecall` and the prompt extension |
| `bridge/telegram_bridge.py` | `_build_context_recall_advisory_for_intent`, `_merge_context_recall_into_extra_overrides`, interjection steering push |
| `agent/session_executor.py` | Prepends the `new_work` advisory ahead of the injection banner |
| `agent/output_handler.py` | Outbound check, bounce, and the `metrics:context_recall_bounces` counter |
| `bridge/message_drafter.py` | `context_recall_advisory` field and `CONTEXT_RECALL_SELF_DRAFT_INSTRUCTION` |
| `tools/valor_telegram.py` | `build_parser()`, extracted so tests can validate the emitted command against the real flags |
