---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-04
tracking: https://github.com/tomcounsell/ai/issues/2652
last_comment_id: 5695879375
revision_applied: true
revision_applied_at: 2026-09-18T06:22:07Z
---

# Telegram Forum-Topic Awareness

## Problem

A Telegram forum groups many parallel conversations under one `chat_id`, distinguished only by
a topic id. The bridge treats the whole forum as one flat chat. The `cyndra` project's group
"Cyndra Devs" uses topics heavily, and each topic corresponds to a distinct body of work in a
monorepo, so this costs real routing correctness, not polish.

**Current behavior:**

1. Topic identity is never captured: `TelegramMessage` has no topic field, and nothing in
   `bridge/`, `agent/`, or `tools/` reads `reply_to_top_id`, `forum_topic`, or `top_msg_id`
   (sole mentions are CLI help prose in `tools/valor_telegram.py`).
2. Proactive sends silently land in General: `reflections/pm_briefings/delivery.py:91,:106`
   hardcode `"reply_to": None`; `tools/send_message.py` reads only `TELEGRAM_CHAT_ID` /
   `TELEGRAM_REPLY_TO`.
3. Session keying can collapse a topic into one session: a top-level message in a topic
   carries a reply header pointing at the topic root, `bridge/routing.py:1323` branches on
   `if message.reply_to_msg_id:`, and `bridge/context.py`'s chain walk builds
   `tg_{project_key}_{chat_id}_{root_msg_id}` — terminating at the topic root means every
   top-level message in that topic resolves to one unbounded session. Reasoned from code and
   Telegram API docs; not yet observed live (see Prerequisites).

**Canonical `bridge/context.py` anchors (re-verified 2026-09-16 on `origin/main`, post-critique).**
The file has moved twice during this lane's life; cite these symbol names, not line numbers:
`_set_cached_root` (**:720**), `resolve_root_session_id` (**:742**, with the three
`session_id = f"tg_{project_key}_{chat_id}_..."` assignments at **:783**, **:793**, **:815**),
`_cache_walk_root` (**:839**), and the context renderer `build_context_prefix` (**:110**).
The critique table below cites the pre-drift positions (:664/:642/:761); those are the same
symbols. `bridge/routing.py:1310` / `:1323` are unchanged.

**Desired outcome:** every inbound forum message carries its topic id (and best-effort name)
through ingest, storage, and agent context; a top-level topic message is distinguishable from
a genuine reply so session identity stays correct; unsolicited sends can target a configured
default topic; a topic can be mapped to a working subdirectory as advisory context.

## Freshness Check

**Baseline commit:** `c99cb231d` (2026-09-04)
**Issue filed at:** 2026-08-07T08:21:11Z (verification comment 2026-08-07T17:20:29Z)
**Disposition:** Minor drift

**File:line references re-verified (all hold on baseline):**
- `models/telegram.py:23-52` — field list still has no topic field — holds.
- `reflections/pm_briefings/delivery.py:91,:106` — still `"reply_to": None` — holds.
- `tools/send_message.py:186-187` — still only `TELEGRAM_CHAT_ID`/`TELEGRAM_REPLY_TO` — holds.
- `bridge/routing.py:1323` — `if message.reply_to_msg_id:` continuation branch — holds (`:1310` `is_reply` for DMs).
- `bridge/context.py:571-605` — chain walk to root, `tg_..._{root_msg_id}` — holds at `:569-610`, PLUS a detail the issue predates: resolved roots are cached in Redis (`_set_cached_root`, `:514`), so a topic-root mis-termination would be cached and persist.
- Grep for `top_msg_id|topic_id|reply_to_top_id|forum` across `bridge/ agent/ tools/ models/` — still only `tools/valor_telegram.py` help text.

**Cited sibling issues/PRs re-checked:**
- #726 (closed) — added `--reply-to` to the send CLI; still the only outbound topic mechanism; its finding that a second Telethon client cannot share the bridge session file still constrains live probing.
- #1191 (closed) — default `--reply-to` principle; unchanged.

**Commits on main since filing (touching referenced files):** poll feature landed in
`bridge/telegram_relay.py` (#3080, #3092) — polls are a NEW outbound surface with the same
General-topic default; `bridge/utc` moved to `utils/` (#2900) — cosmetic for this plan.

**Active plans overlapping this area:** `reply-chain-media-renders-as-literal-string.md`
(#2732). **Resolved 2026-09-16:** PR #3146 **merged** 2026-09-05 (`9ebcb5754`), and
`bridge/context.py` has moved again since (`9b25ffd0e`, email attachments). `fetch_reply_chain`'s
signature and both bridge call sites are unchanged, so this plan's No-Go boundary and the
`def fetch_reply_chain` verification row remain valid as written. The lane worktree must be
rebased onto current main before editing `bridge/context.py` — the file no longer matches the
`c99cb231d` baseline. This plan's walk-termination change touches the session-root walk
(`_cache_walk_root`), a different function in the same file — no design conflict.

**Notes:** recovery scanners (`bridge/catchup.py:409`, `bridge/reconciler.py:333`,
`bridge/agent_catchup.py:699`) key sessions per-message and never walk the reply chain, so
they do not share defect 3 — but they also capture no topic, so recovery-dispatched messages
lose topic identity like everything else.

## Prior Art

- **#726**: send CLI forum support via `--reply-to` — outbound-only workaround, still the only topic mechanism; also documents the single-Telethon-client constraint.
- **#1191**: default `--reply-to` on agent sends — the "a send without a reply target decouples from its thread" principle this plan generalizes.
- **#996**: reply-to session continuation for DMs — origin of the `is_reply` branch this plan must make forum-aware.
- **#3080/#3092** (merged): poll sends through the relay — a new unsolicited-send surface that inherits the General-topic default this plan fixes.

## Research

**Queries used:**
- "Telethon forum topics send_message reply_to top_msg_id InputReplyToMessage reply_to_top_id how to send to topic"

**Key findings:**
- Inbound topic identity lives on `MessageReplyHeader`: `forum_topic` flag (flags.3) and `reply_to_top_id` (flags.1). For a reply inside a topic, `reply_to_top_id` is the topic root and `reply_to_msg_id` is the replied message; for a TOP-LEVEL topic message, `reply_to_top_id` is absent and `reply_to_msg_id` IS the topic root with `forum_topic=True`. Disambiguation rule (matterbridge precedent): genuine reply iff the resolved topic id differs from `reply_to_msg_id`. Source: https://tl.telethon.dev/constructors/message_reply_header.html
- Known quirk: `reply_to_top_id` can be `None` on a plain comment but set when quoting (https://github.com/LonamiWebs/Telethon/issues/3831) — resolution must treat `reply_to_top_id or (reply_to_msg_id if forum_topic else None)` as the topic id, never require `reply_to_top_id`.
- Outbound: `client.send_message(chat, text, reply_to=topic_id)` posts into a topic (Telethon converts to `InputReplyToMessage(reply_to_msg_id=topic_id, top_msg_id=topic_id)`); replying to a message inside a topic uses the raw `InputReplyToMessage(reply_to_msg_id=msg, top_msg_id=topic)`. Sources: https://tl.telethon.dev/methods/messages/send_message.html, https://tl.telethon.dev/constructors/input_reply_to_message.html
- The General topic (id=1) must be sent as a plain send — Telegram rejects thread id 1; omit `reply_to` for General.
- Topic name→id resolution requires `channels.GetForumTopics`; nothing in the codebase calls it today.

(Memory-store save attempted per the addendum; the store filtered it — findings preserved here.)

## Spike Results

### spike-1: Where does session keying actually branch on reply state?
- **Assumption**: "The collapse defect lives in one reply-continuation branch plus one chain walk."
- **Method**: code-read
- **Finding**: Confirmed two sites and only two: `bridge/routing.py:1310/:1323` (DM/group continuation branch) and `bridge/context.py:569-610` (walk + session-id build + Redis root cache `_set_cached_root:514`, `_cache_walk_root:629`, max 20 hops). Recovery scanners key per-message and never walk.
- **Confidence**: high
- **Impact on plan**: the fix is a terminating condition at the walk/branch level plus cache hygiene; no session-model change needed.

### spike-2: Is the outbound path one chokepoint or many?
- **Assumption**: "All sends funnel through the relay payload `{chat_id, reply_to, text}`."
- **Method**: code-read
- **Finding**: The relay consumes `message.get("reply_to")` (`bridge/telegram_relay.py:1264`) and passes `reply_to=` to Telethon at ~9 send sites in that file; producers are `agent/output_handler.py` (replies — already topic-correct via reply inheritance), `reflections/pm_briefings/delivery.py` (hardcoded None), `tools/send_message.py` (env-driven), `tools/valor_telegram.py` (`--reply-to`), poll sends (#3080). Since `reply_to=topic_id` is exactly how Telethon targets a topic, the existing payload field is sufficient — no relay schema change strictly required for topic targeting; a `topic_id` field is additive clarity for reply-inside-topic correctness.
- **Confidence**: high
- **Impact on plan**: outbound work = default-topic resolution at the producers plus optional raw `InputReplyToMessage` in the relay for reply+topic pairs; General(id=1)→omit guard in one relay helper.

### spike-3: Do recovery scanners need the same fix?
- **Assumption**: "Catchup/reconciler re-enqueues share the keying defect."
- **Method**: code-read
- **Finding**: They key `tg_{project}_{chat}_{message.id}` directly (catchup.py:409, reconciler.py:333, agent_catchup.py:699) — immune to collapse, but topic-blind. Re-verified 2026-09-16: they do **not** write `TelegramMessage` rows at all. The only production callers of `tools/telegram_history.store_message()` are `bridge/telegram_bridge.py:1567` (live intake), `bridge/telegram_bridge.py:3218` (outbound record) and `bridge/telegram_relay.py:383`. The scanners call `enqueue_agent_session`.
- **Confidence**: high
- **Impact on plan**: topic capture must be a shared helper, not inline bridge code — but it lands in two distinct shapes: (a) row persistence via one new `store_message` kwarg threaded from live intake, and (b) session-context threading at the three scanners' `enqueue_agent_session` sites. Do not look for a scanner-side `TelegramMessage` write; there isn't one.

## Data Flow

1. **Entry point**: Telethon `events.NewMessage` → `bridge/telegram_bridge.py` intake (live) or the three recovery scanners (backfill).
2. **Topic resolution (new)**: shared helper reads `message.reply_to` → `(topic_id, is_topic_root_reply)`; General/non-forum → `topic_id=None`.
3. **Storage**: `TelegramMessage` gains `topic_id` (nullable); `AgentSession` carries topic via existing extra-context/session fields (topic-as-context per owner ruling; never part of the session key).
4. **Session keying**: `bridge/routing.py` continuation branch treats a top-level topic message as fresh; `bridge/context.py` walk terminates before crossing a topic root; root cache entries versioned or keyed to survive the semantics change.
5. **Agent context**: topic id + best-effort name rendered into the session's context block so the agent can state which topic it is in; optional configured subdirectory hint rides along as advisory text.
6. **Output**: replies inherit topic via `reply_to` (unchanged); unsolicited sends resolve `default_topic_id` from `projects.json` group config → relay sends `reply_to=topic_id` (omit for General/none).

## Architectural Impact

- **New dependencies**: none (Telethon already present; `channels.GetForumTopics` is an existing-API call).
- **Interface changes**: `TelegramMessage` +1 nullable field (Popoto migration required); relay payload optionally +`topic_id`; `projects.json` group entries optionally +`default_topic_id` (absent = today's behavior).
- **Coupling**: topic resolution becomes a shared bridge helper used by live intake and all three recovery scanners — reduces per-site drift.
- **Data ownership**: bridge owns topic capture; agent receives it read-only via context.
- **Reversibility**: high — nullable field, optional config, guarded branch changes.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (owner rulings on session granularity + mapping semantics)

**Interactions:**
- PM check-ins: settled — all five plan questions ruled by the owner on 2026-09-05 (see Owner Rulings)
- Review rounds: 1-2

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| A forum group owned by the executing machine | `scutil --get ComputerName` compared against `projects.<key>.machine` in `~/Desktop/Valor/projects.json` | Task 7 needs a host that **owns** (not merely receives updates for) a group with Telegram Topics enabled. See OPEN QUESTION below. |

No other prerequisites — all remaining work is code + unit tests with synthetic `MessageReplyHeader` objects.

### OPEN QUESTION (owner) — the live observation, re-scoped

**Corrects an earlier premise.** This plan previously recorded that the planning host "runs no bridge" and that Cyndra Devs was "not configured on this machine at all". Re-verified on **Valor the Cowboy** (2026-09-16): `com.valor.bridge` **is** live here, `projects.cyndra.telegram.groups` **does** contain `"Cyndra Devs" (-1004385743413)`, and `logs/bridge.log` shows this client receiving that channel's updates. The real blocker is narrower: `projects.cyndra.machine` is **"Valor the Bald"**, so under single-machine ownership this host receives those updates but never persists them — `TelegramMessage.query.filter(chat_id=-1004385743413)` returns **0 rows**.

No live read can substitute. The bridge's only externally-driven consumer is the relay, whose payload `type` is a closed `Literal["reaction", "custom_emoji_message", "poll"] | None` (`bridge/wire_schemas.py:52`) dispatching four **send** branches (`bridge/telegram_relay.py:1377-1412`); `bridge/history_fetch.py:24` does reuse the bridge's own client but only from internally-scheduled call sites (`telegram_bridge.py:3362`, `:3402`) with fixed arguments. Reaching `channels.GetForumTopics` would mean adding a new job type to the hot I/O path purely to answer a prerequisite — rejected. `tools/valor_telegram.py:247-266` builds a second client on `data/valor_bridge` and is unsafe while the bridge runs (#726). Historical records cannot answer it either: `models/telegram.py:47` stores only the flattened `reply_to_msg_id`, and Telethon's property discards `forum_topic` and `reply_to_top_id` (`telethon/tl/custom/message.py:715-722`).

**But the observation needs no code and no Cyndra access.** Both values the acceptance criterion asks for are already persisted today (`TelegramMessage.reply_to_msg_id`, `AgentSession.session_id`), so *any* owned forum group answers it identically.

**Action requested of the owner — either resolves the criterion:**
- **(A) On Valor the Cowboy, preferred:** in `Eng: Valor` (-1003449100931), enable Telegram **Topics**, create one topic, post a single **top-level** message in it (a new message, not a reply). Reversible. Then this lane reads back the stored `reply_to_msg_id` and `session_id`.
- **(B) On Valor the Bald:** post one top-level message in an existing non-General topic of `Cyndra Devs` and hand back the `TelegramMessage`/`AgentSession` read.

**Verdict rule, fixed in advance:** `session_id` ending in the **topic root's** message id ⇒ item 3 **confirmed**. Ending in the **new message's own** id ⇒ item 3 **refuted**, and scope shrinks to inbound identity plus the outbound default topic.

**Build split while this is open:** Tasks 1, 3, 4 (capture/storage, outbound default topic, context rendering) proceed and merge. **Task 2 (keying correction) is held** — it does not merge until the observation lands, per Risk 1 and Owner Ruling 5.

**Mitigation for the split window (critique CONCERN 3 / Note N3):** Task 4 ships the topic line
with an explicit `(session keying not yet topic-aware — messages from sibling topics may share
this session)` caveat, so the visible topic label never reads as a promise that routing is
topic-correct. Task 2 deletes the caveat in the same commit that lands the keying fix. The
alternative (a default-OFF feature flag) was considered and rejected — see Note N3 for the
reasoning. This is a required build behavior with a named owner, not a noted risk.

Recorded on the issue: https://github.com/tomcounsell/ai/issues/2652#issuecomment-5695879375

## Solution

### Key Elements

- **Topic resolver helper** (`bridge/topic.py` or inside `bridge/routing.py`): one function from a Telethon message to `(topic_id | None, is_top_level_topic_message)` implementing the header rules and the #3831 quirk defensively.
- **Capture + storage**: `TelegramMessage.topic_id`; `tools/telegram_history.store_message()` gains a `topic_id` kwarg populated by live intake (`bridge/telegram_bridge.py:1567`) through the shared helper; the three recovery scanners carry the topic into the enqueued session context instead (they write no rows — see spike-3); Popoto migration registered in `MIGRATIONS`.
- **Keying correction**: continuation branch and chain walk treat top-level topic messages as fresh sessions; walk never crosses a topic root; root-cache hygiene for the semantics change.
- **Context rendering**: topic id + best-effort name (GetForumTopics, cached, fail-soft to id-only) in the agent's context; optional advisory subdirectory hint from config.
- **Outbound default topic**: `default_topic_id` per group in `projects.json`; ALL producers of unsolicited sends resolve it — PM briefings, `tools/send_message.py`, and poll sends (#3080 surface, included per owner ruling: same one-line producer resolution); relay guard maps General/none to a plain send; reply-inside-topic uses raw `InputReplyToMessage` where both ids are known.

### Flow

Inbound topic message → resolver tags `topic_id`, flags top-level → fresh session keyed by its own message id, context says "topic: behring (id 123)" → agent replies (topic inherited) or later sends unsolicited → default-topic resolution → relay targets the topic instead of General.

### Technical Approach

- Topic id resolution: `header = message.reply_to; topic_id = header.reply_to_top_id or (header.reply_to_msg_id if header.forum_topic else None)`; `is_top_level = header.forum_topic and header.reply_to_top_id is None`. Non-forum and General yield `topic_id=None`.
- Session granularity stays per-conversation (owner ruling, 2026-09-05): topic id is context + storage, NOT part of the session key. The keying fix is purely "stop mistaking topic roots for replies".
- Root cache: key the cached roots under a bumped namespace (or include a semantics version) so pre-fix cached collapses cannot serve post-fix lookups.
- Name resolution: lazy `channels.GetForumTopics` from the bridge (never a second client), cached in Redis via ORM model or reuse of an existing cache pattern, always fail-soft to id-only.
- Config: `projects.json` group entry gains optional `default_topic_id` (int) and optional `topics: {id: subdir}` advisory map, keyed by topic **id** (owner-ratified default: ids survive admin renames; names-as-keys was considered and rejected because it needs `GetForumTopics` resolution and breaks silently on rename). `bridge/config_validation.py` accepts-and-validates both, absent keys keep today's behavior exactly.
- Env plumbing for agent-invoked sends: sessions created from a topic export `TELEGRAM_TOPIC_ID` beside `TELEGRAM_REPLY_TO` (injection site: `agent/sdk_client.py:495-500`, where `TELEGRAM_REPLY_TO` is set today); `tools/send_message.py` uses it when `TELEGRAM_REPLY_TO` is unset.

#### Implementation Notes from Critique (2026-09-16)

These are binding on the build, not advisory.

- **N1 — Named General-topic constant (critique CONCERN 1).** The relay must introduce a named
  module-level `GENERAL_TOPIC_ID = 1` in `bridge/telegram_relay.py` and branch on it, never a
  bare `== 1` literal. Verified 2026-09-16: `grep -c "GENERAL_TOPIC_ID" bridge/telegram_relay.py`
  is **0** on `origin/main`, while `len(available) == 1` already appears at `:656` and `:719` —
  which is why the old verification row was vacuously green. The replacement row below is RED on
  current main.
- **N2 — Resolve the topic at `ThreadMessage` construction (critique CONCERN 2).** In
  `bridge/agent_catchup.py`, the raw Telethon message `m` and its `reply_header = m.reply_to`
  are in scope at the construction site (**:425-445**, `ThreadMessage(...)` at **:435**); the
  dataclass is at **:98**. Call the shared resolver there and add
  `topic_id: int | None = None` to the `ThreadMessage` dataclass. Do **not** attempt
  `getattr(inbound, "reply_to", None)` inside the `enqueue_agent_session` call chain
  (`:680-700`) — `ThreadMessage` does not carry the raw header and never will.
- **N3 — Topic line ships with an explicit keying caveat (critique CONCERN 3).**
  **Option taken: the written caveat, owned by Task 4; the feature flag is rejected.** While
  Task 2 is held, `build_context_prefix` renders the topic line as
  `topic: <name-or-id> (session keying not yet topic-aware — messages from sibling topics may
  share this session)`. Rationale for preferring the caveat over a default-OFF flag: (a) the
  consumer of this context line is the agent itself, so the caveat lands exactly where the
  misleading signal would otherwise be read; (b) a flag whose only job is to stay OFF until a
  sibling task merges is a temporary bridge, which this repo's Development Principle 1 forbids,
  and it carries dead-flag-removal debt on top of the same removal work; (c) the caveat degrades
  the surface honestly instead of going dark, so Task 4's value (topic identity visible in
  context) still ships during the hold. **Removal is a gating checkbox on Task 2**: the commit
  that lands the keying correction deletes the caveat clause in the same commit, and the
  Verification table carries a row that fails while both the caveat string and the keying fix
  are present.
- **N4 — Cross-topic bleed is asserted on the surface that actually carries message content
  (critique CONCERN 4; re-pointed 2026-09-18 per round-2 BLOCKER).** The round-1 closure aimed
  the fixture at `build_context_prefix` (`bridge/context.py:110`), whose verified signature is
  `(project, session_type, sender_id=None, persona=None, email_from=None, sender_name=None)
  -> str` — it ingests no `TelegramMessage`, no chat history and no `content`, so "assert topic
  A's content is absent from the rendered prefix" passes identically with and without the
  defect. That closure was vacuous and is withdrawn.
  **The real bleed surface is `build_conversation_history` (`bridge/context.py:319`)**, which
  calls `get_recent_messages(str(chat_id), limit=limit)`
  (`tools/telegram_history/__init__.py:544`). That query is `TelegramMessage.query.filter(
  chat_id=str(chat_id))` — chat-scoped and topic-blind — so in a forum every topic's messages
  land in one `RECENT CONVERSATION:` block. The fix and the fixture both move there:
  `get_recent_messages` gains `topic_id: int | None = None` narrowing the query, and
  `build_conversation_history(chat_id, limit=5, topic_id=None)` threads it. The fixture is then
  **RED on current main**, which the `build_context_prefix` version can never be.
- **N5 — Anti-criterion is range-scoped, not single-line (critique CONCERN 5).** See the
  rewritten Verification rows; the old `grep ... | grep -c "session_id = f"` form could only
  match when both substrings shared a line, and the real assignments (`:783`, `:793`, `:815`)
  sit on their own lines.
- **N6 — Task 3 gets its own builder (critique CONCERN 6).** See Team Orchestration and the
  Parallelism Contract under Step by Step Tasks.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The resolver and name-resolution paths are fail-soft by design: each `except` must log at WARNING with chat/msg ids and degrade to `topic_id=None` / id-only naming — one test per handler asserting the log + degraded value.
- [ ] GetForumTopics failure (network, permissions, non-forum group) → id-only context, no crash, no retry storm (single attempt per cache TTL).

### Empty/Invalid Input Handling
- [ ] Messages with no reply header, DM messages, and non-forum group replies → `topic_id=None`, `is_top_level=False`, byte-identical behavior to today (regression tests on existing fixtures).
- [ ] Malformed/partial headers (forum_topic set, both ids None) → `topic_id=None`, warning logged.

### Error State Rendering
- [ ] When a topic name cannot be resolved, context renders "topic id 123" rather than omitting topic identity or rendering a placeholder lie.
- [ ] A configured `default_topic_id` pointing at a deleted topic: Telegram send error surfaces through the relay's existing retry/dead-letter path with the topic id named in the log — test via relay unit fixture.

## Test Impact

All UPDATE paths below were verified to exist on `origin/main` at plan-revision time
(2026-09-16). CREATE paths were verified NOT to exist. A build must not invent new suites
that shadow these.

- [ ] `tests/unit/test_config_driven_routing.py` — UPDATE: this is the real home of
  `should_respond_async` reply-continuation coverage (its `_make_event(reply_to_msg_id=...)`
  helper drives the `is_reply` assertions at `:103-115`, and `:227` carries the #996 case).
  Add forum variants: top-level topic message → `is_reply is False` (fresh session); genuine
  in-topic reply → `is_reply is True`. The `_make_event` helper gains a reply-header stub so
  `forum_topic` / `reply_to_top_id` can be set.
- [ ] `tests/unit/test_routing.py` — UPDATE only if the resolver lands in `bridge/routing.py`
  rather than a new `bridge/topic.py`; this file is the general routing-predicate suite and
  does NOT currently exercise `should_respond_async`. Do not put continuation-branch forum
  cases here.
- [ ] `tests/unit/test_context_helpers.py` — UPDATE: chain-walk tests gain topic-root
  termination cases and root-cache namespace assertions. (It already constructs
  `TelegramMessage(...)` at `:650`, so the new nullable field surfaces here.)
- [ ] `tests/unit/test_context_helpers.py` — UPDATE (**cross-topic bleed fixture, critique
  CONCERN 4 / Note N4**): build two synthetic `TelegramMessage` rows sharing one `chat_id` with
  distinct `topic_id` values and distinct `content` strings, then call
  `build_conversation_history(chat_id, topic_id=<B>)` (`bridge/context.py:319`) and assert
  topic A's `content` string is **absent** from the returned history block. Name the test
  `test_cross_topic_history_does_not_bleed`. Assert on the rendered history text, not on
  `session_id` inequality — session-id inequality is already covered by the keying tests and
  does not prove the user-visible harm (context bleed) is gone.
  **RED-on-main requirement:** before the filter lands, this test must be demonstrated failing
  against `main` (topic A's content IS present, because `get_recent_messages` filters on
  `chat_id` only). A build that cannot show it red has not built the criterion this Note
  exists for. This fixture is owned by **Task 4** (build-context), which owns the history
  filter; Task 2 is not a prerequisite for it, because the bleed is a query-scope defect, not
  a keying defect.
- [ ] `tools/telegram_history/tests/test_telegram_history.py` and
  `tests/tools/test_telegram_history.py` — UPDATE (**history filter, Note N4**): assert
  `get_recent_messages(chat_id, topic_id=N)` returns only rows whose `topic_id == N`, that
  `topic_id=None` (the default) returns every row in the chat unchanged, and that rows stored
  before the field existed (`topic_id=None`) are not dropped by an unfiltered call.
#### `build_context_prefix` — full swept inventory (round-3, 2026-09-18)

This is a **replicated-value defect**, so it closes on a sweep, never on an enumerated list.
Round 2 named one mirror; the round-3 sweep
(`grep -rn "def build_context_prefix" --include="*.py" .`, excluding `.venv`/`.worktrees`)
found **three definitions** and one consumer nobody had listed. Re-run the sweep before
writing the disposition; do not trust this list to still be complete.

| Site | Kind | Effect of the Task 4 signature change |
|------|------|----------------------------------------|
| `bridge/context.py:110` | the real implementation | changes |
| `agent/session_runner/harness/claude.py:1696` (import at `:1694`) | **the only production call site** | must be updated |
| `bridge/telegram_bridge.py:114` | re-export, `noqa: F401` | no change |
| `tests/unit/test_bridge_logic.py:87` | mirror #1, 6-param, used `:398-620` | goes stale silently |
| `tests/integration/test_message_routing.py:92` | mirror #2, **2-param** `(project, session_type=None)`, used `:174`, `:367`, `:368` | goes stale silently |
| `tests/e2e/test_message_pipeline.py:10` | imports the **real** function, calls at `:214`, `:218`, `:222`, `:226` | real coverage; new params must default |
| `tests/unit/test_cross_repo_gh_resolution.py:51`, `tests/unit/test_sdk_client.py:46`/`:73`, `tests/unit/test_pm_channels.py:88`/`:115`/`:150` | `patch(..., return_value=...)`, **no `autospec`** | neither breaks nor covers |

- [ ] `tests/unit/test_bridge_logic.py` — UPDATE (**stale mirror #1**): `:87`, docstring at
  `:97` says "Local mirror of bridge.context.build_context_prefix for unit tests". Delete it and
  import the real function; the docstring's stated reason (import-time side effects requiring a
  live bridge) must be re-tested before the mirror is kept. Owned by Task 4.
- [ ] `tests/integration/test_message_routing.py` — UPDATE (**stale mirror #2, found only by the
  sweep**): `:92` is a second, *divergent* mirror with a shorter signature
  `(project: dict | None, session_type: str | None = None)` and, unlike mirror #1, **no "Local
  mirror" docstring** — so any check keyed on that docstring string would miss it. This is why
  the Verification row is a tree-wide definition count, not a docstring grep. Delete and import.
  Owned by Task 4.
- [ ] `tests/e2e/test_message_pipeline.py` — VERIFY (not a mirror): imports the real function at
  `:10`. It is genuine coverage and will exercise the new signature, so `topic_id` and
  `topic_name` MUST be keyword params with `None` defaults or these four call sites break.
- [ ] The six `patch("bridge.context.build_context_prefix", return_value=...)` sites use no
  `autospec`, so a signature change neither breaks nor is caught by them. No action required;
  listed so the next reader does not re-derive it.
- [ ] `tests/unit/test_model_relationships.py` — UPDATE (**hard break**): `:110` asserts
  `len(TelegramMessage._meta.field_names) == 20`. Adding `topic_id` makes it 21; the count
  must be bumped in the same commit as the model change or the suite goes red. Add a
  `topic_id in field_names` assertion beside the existing `reply_to_msg_id` one (`:76-79`).
- [ ] `tests/tools/test_telegram_history.py` — UPDATE: intake persistence runs through
  `tools/telegram_history.store_message()` (`tools/telegram_history/__init__.py:346`,
  `TelegramMessage.create(...)` at `:396`), which this suite covers directly
  (`test_store_basic_message` at `:40` and ~22 `store_message` call sites). Assert
  `topic_id` round-trips and defaults to `None` for non-forum callers.
- [ ] `tools/telegram_history/tests/test_telegram_history.py` — UPDATE: a second, co-located
  suite that also calls `store_message` (`:55`, `:70`, `:98`, `:156`, `:183`). It must move
  with the signature change or it drifts from the `tests/tools/` copy.
- [ ] `tests/unit/test_bridge_relay.py` — UPDATE: relay send-path cases assert the General
  topic (id 1) and the no-config case omit `reply_to`, and that a resolved `default_topic_id`
  reaches Telethon as `reply_to=<topic_id>`.
- [ ] `tests/unit/test_send_message.py` — UPDATE: `tools/send_message.py` env resolution gains
  `TELEGRAM_TOPIC_ID`; assert `TELEGRAM_REPLY_TO` still wins when both are set.
- [ ] `tests/unit/test_agent_catchup.py`, `tests/unit/test_reconciler.py`,
  `tests/unit/test_catchup_seed.py` — UPDATE: assert the shared resolver is invoked and the
  topic rides into the enqueued `AgentSession` context. **Note the correction:** the three
  scanners do NOT persist `TelegramMessage` rows — they build `tg_{project}_{chat}_{msg_id}`
  session ids and call `enqueue_agent_session` (`bridge/catchup.py:409`,
  `bridge/reconciler.py:333`, `bridge/agent_catchup.py:699`). The only production callers of
  `store_message` are `bridge/telegram_bridge.py:1567` (live intake), `:3218` (outbound
  record) and `bridge/telegram_relay.py:383`. So scanner-path topic work is
  session-context threading, not row persistence.
- [ ] `tests/unit/test_topic_resolver.py` — CREATE: synthetic `MessageReplyHeader` truth-table
  cases including the #3831 quirk and the malformed-header degradation.

## Rabbit Holes

- **Backfilling topic ids onto historical TelegramMessage rows** — the migration adds the field; it does NOT re-read Telegram history. Old rows stay `topic_id=None`.
- **Topic-scoped write enforcement** — mapping a topic to a subdirectory is advisory context in this plan (owner ruling, 2026-09-05); building permission enforcement around it is a separate design that would start as its own issue.
- **A general topics-admin surface** (create/rename/list topics from the agent) — out of scope entirely.
- **Rebuilding session granularity** — resist folding topic id into the session key "while we're here"; that changes UX for every non-forum chat, and the owner ruled for topic-as-context (2026-09-05).

## Risks

### Risk 1: The empirical pre-requisite invalidates the header model
**Impact:** If a real top-level topic message does NOT carry the predicted header shape, the disambiguation rule is wrong and the keying fix misfires.
**Mitigation:** Unit tests encode the documented shapes; Task 7 (an owner action on any owned Topics-enabled group, not a code path) verifies live BEFORE the keying change ships to the fleet — build order puts capture/storage/outbound/context first and holds keying alone; the #3831 quirk is handled defensively.

### Risk 2: Root-cache poisoning across the semantics change
**Impact:** Pre-fix cached walk roots keep collapsing sessions after the fix.
**Mitigation:** Namespace/version bump on the cache key (Technical Approach); test asserts old-namespace entries are not read.

### Risk 3: Config drift in projects.json across the fleet
**Impact:** A malformed `default_topic_id`/`topics` entry could block bridge restart via config validation.
**Mitigation:** validation treats both keys as optional with type checks only; malformed values warn-and-ignore rather than hard-fail (matching the last-known-good posture in `docs/features/single-machine-ownership.md`).

## Race Conditions

### Race 1: Concurrent first-messages in one topic during the fix rollout
**Location:** `bridge/context.py` walk + `_set_cached_root`
**Trigger:** Two top-level topic messages arrive before either caches a root.
**Data prerequisite:** none (both key fresh by their own message ids post-fix).
**State prerequisite:** cache namespace already bumped.
**Mitigation:** post-fix, top-level messages never enter the walk, so the cache is only written for genuine replies; the bump prevents mixed-semantics reads.

### Race 2: Name-resolution cache stampede
**Location:** new GetForumTopics cache
**Trigger:** Burst of messages in a freshly-seen forum.
**Data prerequisite:** cache entry absent.
**State prerequisite:** bridge event loop must not block on the RPC.
**Mitigation:** single-flight guard or accept N duplicate RPCs bounded by burst size; RPC is issued from the bridge's own client (no second client, per #726); id-only rendering while unresolved.

## No-Gos (Out of Scope)

- [EXTERNAL] Live confirmation of the top-level-topic header shape needs a machine that **owns** a Topics-enabled group. Cowboy runs a bridge but does not own the Cyndra group (owner is Bald), so it stores none of its messages; a second Telethon client on the shared session file is unsafe (#726) and the bridge exposes no external read job. Resolution is an owner action, not a code path — see the OPEN QUESTION under Prerequisites. Tasks 1/3/4 proceed without it; Task 2 is held behind it.
- [SEPARATE-SLUG #2732] Reply-chain media rendering in `bridge/context.py` — active sibling lane; this plan does not touch `fetch_reply_chain`.
- [SEPARATE-SLUG #2494] Recovery-path durability semantics — this plan only threads topic capture through the scanners' existing persistence sites; their dispatch/durability behavior is #2494's.
- Historical backfill of `topic_id` — deliberately not done (Rabbit Holes); old rows remain None and the field is nullable precisely so absence is honest.

## Update System

- Popoto migration for `TelegramMessage.topic_id` added to `scripts/update/migrations.py` and registered in `MIGRATIONS` (idempotent; recorded in `data/migrations_completed.json`) — propagates via the normal `/update` cron.
- `projects.json` is private/iCloud-synced and hand-edited; new keys are optional, so no update-script changes and no fleet edit required. Document the keys in the feature doc.
- Bridge restart required after merge (standard `./scripts/valor-service.sh restart` via deploy flow); no plist or service changes.

## Agent Integration

- Sessions created from a topic export `TELEGRAM_TOPIC_ID` alongside the existing `TELEGRAM_CHAT_ID`/`TELEGRAM_REPLY_TO`, and `tools/send_message.py` resolves it (reply target wins when both set). `tools/valor_telegram.py` send already accepts `--reply-to`, which Telethon interprets as a topic target — document that `--reply-to <topic_id>` posts into a topic; no new CLI flag required.
- The bridge imports the resolver internally (live intake + scanners); no MCP surface changes.
- Integration test: a session env carrying `TELEGRAM_TOPIC_ID` produces a relay payload whose `reply_to` is the topic id (and omits it for General), via the existing send-path test idioms.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/telegram-forum-topics.md`: header semantics (incl. the #3831 quirk), the disambiguation rule, session-granularity ruling, config keys (`default_topic_id`, `topics` advisory map), General-topic send rule, and the recovery-scanner parity note.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-steering.md` / `docs/features/reply-thread-context-hydration.md` only if their described flows gain topic-visible behavior (verify at docs stage).

### Inline Documentation
- [ ] Resolver docstring carries the header truth table verbatim (it is the load-bearing knowledge).
- [ ] `projects.json` key documentation in the feature doc, not in code comments.

## Success Criteria

- [ ] A synthetic top-level topic message (forum_topic=True, reply_to_top_id absent) creates a session keyed by its OWN message id, never the topic root's.
- [ ] A synthetic in-topic reply continues its conversation session and stores the correct `topic_id`.
- [ ] Non-forum and DM behavior is byte-identical to today (existing routing/context suites green without semantic edits beyond the new-field assertions).
- [ ] `TelegramMessage.topic_id` persists through live intake via `store_message` (round-trip test), and the three recovery scanners carry the topic into their enqueued session context (one test each) — they write no message rows.
- [ ] An unsolicited send in a group with `default_topic_id` configured produces `reply_to=<topic_id>`; with General or no config, `reply_to` is omitted.
- [ ] Agent context for a topic message names the topic (name or id) — snapshot test.
- [ ] **No cross-topic context bleed (user-vantage criterion, critique CONCERN 4 / Note N4):**
  given two `TelegramMessage` rows in the same `chat_id` under different topics,
  `build_conversation_history(chat_id, topic_id=B)` returns a history block containing none of
  topic A's `content`. Asserted in `tests/unit/test_context_helpers.py::
  test_cross_topic_history_does_not_bleed`, which **must be shown RED on `main`** before the
  filter lands. Owned by Task 4; NOT held behind Task 2.
- [ ] `build_context_prefix` gains `topic_id: int | None = None` and
  `topic_name: str | None = None` parameters (nothing in its current arg list can carry a
  topic), and `tests/unit/test_bridge_logic.py` no longer covers a mirror that has drifted
  from it.
- [ ] While Task 2 is held, the rendered topic line carries the keying caveat (Note N3); once
  Task 2 merges, the caveat string is gone from `bridge/context.py` — one test per state.
- [ ] Migration runs idempotently; second run is a no-op.
- [ ] Tests pass (`/do-test`); Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (resolver + storage)** — Name: `topic-capture-builder` — Role: resolver helper, TelegramMessage field, migration, scanner threading — Agent Type: builder — Resume: true
- **Builder (keying)** — Name: `topic-routing-builder` — Role: continuation branch, walk termination, root-cache namespace bump, cross-topic-bleed fixture, caveat removal — Agent Type: builder — Resume: true (Domain: Redis/Popoto data + async — paste matching DOMAIN_FRAMING rules)
- **Builder (outbound)** — Name: `topic-outbound-builder` — Role: `projects.json` keys + validation posture, producer default-topic resolution (PM briefings, `tools/send_message.py`, poll sends), relay `GENERAL_TOPIC_ID` guard, `TELEGRAM_TOPIC_ID` env plumbing — Agent Type: builder — Resume: true (**added 2026-09-16 per critique CONCERN 6 / Note N6**: splitting this off `topic-routing-builder` is what lets Task 3 merge while Task 2 is held)
- **Test engineer** — Name: `topic-test-engineer` — Role: synthetic-header unit suites, recovery-scanner parity tests, relay send-path tests — Agent Type: test-engineer — Resume: true
- **Validator** — Name: `topic-validator` — Role: run Verification table, confirm Success Criteria, byte-identical non-forum regression check — Agent Type: validator — Resume: true
- **Documentarian** — Name: `topic-documentarian` — Role: feature doc + index — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### Parallelism Contract (revised 2026-09-16, critique CONCERN 6 / Note N6)

`do-build`'s `Parallel: true` means *may run concurrently with its wave peers*, and concurrent
builders must write **disjoint file sets**. The waves and their file sets:

| Wave | Tasks | Builder | Files written |
|------|-------|---------|---------------|
| 1 | Task 1 (build-capture) | `topic-capture-builder` | `bridge/topic.py` (new), `models/telegram.py`, `tools/telegram_history/__init__.py`, `scripts/update/migrations.py`, `bridge/telegram_bridge.py` (intake), `bridge/catchup.py`, `bridge/reconciler.py`, `bridge/agent_catchup.py`, `tests/unit/test_model_relationships.py` |
| 2 | Task 3 (build-outbound) **‖** Task 4 (build-context) | `topic-outbound-builder` **‖** `topic-capture-builder` | Task 3: `bridge/telegram_relay.py`, `bridge/config_validation.py`, `reflections/pm_briefings/delivery.py`, `tools/send_message.py`, `agent/sdk_client.py` — Task 4: `bridge/context.py`, `bridge/topic.py` |
| 3 | Task 2 (build-keying) — **held** behind Task 7 | `topic-routing-builder` | `bridge/routing.py`, `bridge/context.py`, `tests/unit/test_context_helpers.py`, `tests/unit/test_config_driven_routing.py` |

Two collisions the critique surfaced, and how this resolves them:

1. **Tasks 2 and 3 shared `topic-routing-builder`** while being marked parallel — one agent
   cannot run concurrently with itself. Resolved by the new `topic-outbound-builder`
   (distinct-builder option, explicitly preferred by the critique over serializing Task 3 behind
   the held Task 2, because serializing would strand outbound work behind an owner action).
2. **Tasks 2 and 4 both write `bridge/context.py`** — found while verifying the disjointness of
   the new assignment. Task 4 edits `build_context_prefix` (`:110`); Task 2 edits
   `resolve_root_session_id` (`:742`) and `_cache_walk_root` (`:839`). Same file, so they cannot
   be a parallel wave. Resolved by putting Task 4 in wave 2 and Task 2 alone in wave 3, with
   Task 2 gaining `Depends On: build-context`. This costs nothing: Task 2 is held behind Task 7
   regardless, and it makes Task 2's caveat-removal edit a trivial delete of a string Task 4
   already landed.

With that, every `Parallel: true` pair writes disjoint files and no builder is scheduled against
itself.

### 1. Topic resolver + storage
- **Task ID**: build-capture
- **Depends On**: none
- **Validates**: tests/unit/test_topic_resolver.py (create)
- **Informed By**: spike-1, spike-3, Research header truth table
- **Assigned To**: topic-capture-builder — **Agent Type**: builder — **Parallel**: true (sole task in wave 1)
- Resolver helper with the truth table + #3831 defense; `TelegramMessage.topic_id`; `store_message(topic_id=...)` kwarg; migration in `MIGRATIONS`; bump the field-count assertion in `tests/unit/test_model_relationships.py:110` (20 → 21) in the same commit; thread the resolver through live intake (row persistence) and the three scanners (session context only).
- **Note N2 applies**: in `bridge/agent_catchup.py`, call the resolver at the `ThreadMessage(...)` construction site (`:435`, raw `m`/`reply_header` in scope) and add `topic_id: int | None = None` to the dataclass at `:98`. Never sniff the header at `:680-700`.

### 2. Keying correction + cache hygiene
- **Task ID**: build-keying
- **Depends On**: build-capture, build-context (shares `bridge/context.py` with Task 4 — see the Parallelism Contract), **verify-live** (the owner-action hold is in the graph, not only in prose — `do-build` must not dispatch this task before Task 7 resolves)
- **Validates**: tests/unit/test_config_driven_routing.py, tests/unit/test_context_helpers.py
- **Informed By**: spike-1 (two sites only), Risk 2, critique CONCERNs 3/5
- **Assigned To**: topic-routing-builder — **Agent Type**: builder — **Parallel**: false (wave 3, alone)
- Continuation branch (`bridge/routing.py:1310`/`:1323`) + walk termination (`resolve_root_session_id` `:742`, `_cache_walk_root` `:839`) + root-cache namespace bump.
- **Root-cache bump covers the READER too (critique CONCERN, 2026-09-18).** `_get_cached_root`
  (`:697`) and `_set_cached_root` (`:720`) each build the key literal `session_root:{chat_id}:{msg_id}`
  independently (`:710` and `:733`), and `resolve_root_session_id` calls the **reader first**
  at `:781` to short-circuit the walk. Bumping only the writer leaves every pre-fix poisoned
  entry served as authoritative at that short-circuit — reproducing the exact collapse Risk 2
  claims to close — while post-fix writes are never read back. Introduce one module-level
  `SESSION_ROOT_KEY_VERSION = "v2"` and a single shared key-builder used by **both** functions;
  remove both key literals in the same commit.
- **Gating checkboxes on this task (all in the same commit as the keying change):**
  - [ ] Delete the `(session keying not yet topic-aware — ...)` caveat clause Task 4 landed in `build_context_prefix` (Note N3). The plan is not done while both the caveat and the fix exist.
  - [ ] `_get_cached_root` and `_set_cached_root` both route through the shared versioned key-builder; no bare `session_root:{` literal remains in `bridge/context.py`.
  - [ ] Both range-scoped anti-criterion Verification rows pass (Note N5).

### 3. Outbound default topic + env plumbing
- **Task ID**: build-outbound
- **Depends On**: build-capture
- **Validates**: tests/unit/test_bridge_relay.py, tests/unit/test_send_message.py
- **Informed By**: spike-2 (reply_to suffices; General omit rule)
- **Assigned To**: topic-outbound-builder — **Agent Type**: builder — **Parallel**: true (wave 2, with build-context; file sets are disjoint — see the Parallelism Contract)
- Config keys + validation posture, producer resolution (PM briefings, send_message, poll sends), relay General-guard, `TELEGRAM_TOPIC_ID` (inject beside `TELEGRAM_REPLY_TO` at `agent/sdk_client.py:495-500`).
- **Note N1 applies**: introduce a named `GENERAL_TOPIC_ID = 1` in `bridge/telegram_relay.py` and branch on it; no bare `== 1` in the topic guard.
- This task is **not** held behind Task 7 and merges independently of Task 2.

### 4. Context rendering + name resolution
- **Task ID**: build-context
- **Depends On**: build-capture
- **Validates**: context snapshot test, tests/unit/test_context_helpers.py (cross-topic bleed), tests/unit/test_bridge_logic.py
- **Assigned To**: topic-capture-builder — **Agent Type**: builder — **Parallel**: true (wave 2, with build-outbound)
- Topic line in agent context (`bridge/context.py::build_context_prefix`, `:110`); lazy GetForumTopics cache, fail-soft; advisory subdir hint when configured.
- **Explicit signature change (critique CONCERN 4 / round-2 BLOCKER).** `build_context_prefix`'s
  current arg list carries nothing that can express a topic, so it MUST gain
  `topic_id: int | None = None` and `topic_name: str | None = None`. Every call site updates in
  the same commit. A topic line that renders without these params is rendering a constant.
- **History topic filter (Note N4) — this task owns it.** `get_recent_messages`
  (`tools/telegram_history/__init__.py:544`) gains `topic_id: int | None = None` narrowing the
  `TelegramMessage.query.filter(...)`; `build_conversation_history` (`bridge/context.py:319`)
  gains `topic_id: int | None = None` and threads it into that call. Default `None` preserves
  today's chat-wide behavior byte-for-byte for non-forum callers. Land
  `test_cross_topic_history_does_not_bleed` with it and record its RED-on-`main` run in the PR.
- **Caveat presence is a Task 4 REVIEW check, not a Verification row (round-3 CONCERN,
  2026-09-18).** Task 4's reviewer confirms the rendered topic line carries the Note N3 caveat
  before approving this task. It is deliberately NOT a row in the Verification table: the table
  is run by validators (Tasks 8/9) whose dependency sets permit running after Task 2 has
  merged and deleted the caveat, at which point a "caveat present" row fails on a correctly
  shipped feature. The two N3 states are mutually exclusive by construction, so only the
  "caveat removed" row survives in the table, scoped to Task 9. A row whose precondition lives
  only in prose is not a row.
- **Mirror resolution — THREE definitions exist, not two (round-3 sweep, 2026-09-18).** See
  Test Impact for the full swept inventory. Task 4 must reduce
  `grep -rn "def build_context_prefix" --include="*.py"` to exactly ONE definition
  (`bridge/context.py:110`) in the same commit as the signature change, deleting both test
  mirrors and importing the real function. The production call site is
  `agent/session_runner/harness/claude.py:1696` (imported at `:1694`) — **not** in `bridge/`;
  `bridge/telegram_bridge.py:114` is only a re-export carrying `noqa: F401`.
- **Wave-2 file set additions** (disjoint from `build-outbound`, which owns
  `bridge/telegram_relay.py`, `tools/send_message.py`, `agent/sdk_client.py`):
  `tools/telegram_history/__init__.py`, `tests/unit/test_bridge_logic.py`,
  `tests/unit/test_context_helpers.py`, `tools/telegram_history/tests/test_telegram_history.py`,
  `tests/tools/test_telegram_history.py`.
- **Note N3 applies — this is the mitigation for the split-merge window, not a comment about it.** While Task 2 is held, the topic line MUST render as `topic: <name-or-id> (session keying not yet topic-aware — messages from sibling topics may share this session)`. This task owns writing the caveat; Task 2 owns deleting it. Shipping the bare topic line before Task 2 merges is a blocker for this task's review.

### 5. Test suites (unheld scope)
- **Task ID**: test-suites-unheld
- **Depends On**: build-outbound, build-context
- **Assigned To**: topic-test-engineer — **Agent Type**: test-engineer — **Parallel**: false
- Every Test Impact + Failure Path item EXCEPT the Task-2-scoped ones listed in Task 10;
  synthetic MessageReplyHeader fixtures; the cross-topic-bleed fixture (Task 4 owns the filter).
- **Split from the old single `test-suites` (round-3 BLOCKER, 2026-09-18).** This task must not
  declare `build-keying` in its `Depends On`, directly or by adding any dependency that reaches
  it. It is the node Tasks 6 and 9 consume, and the whole point of the Task 2 hold is that those
  three can complete while Task 2 waits on the owner.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: test-suites-unheld
- **Assigned To**: topic-documentarian — **Agent Type**: documentarian — **Parallel**: false

### 7. Live verification on a bridge host [EXTERNAL constraint]
- **Task ID**: verify-live
- **Depends On**: build-capture (can precede keying rollout; see Risk 1). Owner ruling 2026-09-05: runs DURING BUILD, before the keying change merges — never deferred to post-deploy.
- **Assigned To**: **owner action** (no agent can perform the post); `topic-validator` reads back the result — **Agent Type**: validator — **Parallel**: true
- **This task needs no code and no Cyndra access.** Both values the criterion reads
  (`TelegramMessage.reply_to_msg_id`, `AgentSession.session_id`) are already persisted today,
  so any forum group **owned by a bridge host** answers it identically. Owner performs (A) or
  (B) from the OPEN QUESTION under Prerequisites; this lane then reads back the stored row and
  the resulting session id and applies the pre-fixed verdict rule. Record results in the PR.
- Gating: **only Task 2 (build-keying) is held behind this.** Tasks 1, 3, 4 proceed and merge.

### 8. Final validation (unheld scope)
- **Task ID**: validate-all
- **Depends On**: build-capture, build-outbound, build-context, test-suites-unheld, document-feature
- **Assigned To**: topic-validator — **Agent Type**: validator — **Parallel**: false
- **Deliberately excludes `build-keying`** (critique CONCERN 2026-09-16; corrected 2026-09-18).
  Task 8 originally declared `Depends On: all previous`, which blocked final validation of
  Tasks 1/3/4 on the indefinite owner action the split was designed to escape. Round 2 dropped
  the direct edge — and that was not enough, because the edge returned transitively through
  `test-suites`. Round 3 split Task 5 to remove it for real.
- **Transitive closure of Task 8 (round-3 BLOCKER — verify by walking this, not by reading the
  one line above).** Every path, fully expanded:
  - `validate-all -> build-capture -> (none)`
  - `validate-all -> build-outbound -> build-capture -> (none)`
  - `validate-all -> build-context -> build-capture -> (none)`
  - `validate-all -> test-suites-unheld -> {build-outbound, build-context} -> build-capture -> (none)`
  - `validate-all -> document-feature -> test-suites-unheld -> {build-outbound, build-context} -> build-capture -> (none)`
  **No path reaches `build-keying`, and therefore none reaches `verify-live`.** `build-keying`
  and `verify-live` appear in the closure of Tasks 9 and 10 only. Any future edit that adds a
  dependency to Task 5, 6 or 8 must re-walk this list; checking only the edge you changed is
  what produced the round-3 blocker.
- Runs every Verification row except the four Task-2-scoped ones listed in Task 9.

### 9. Keying validation (held scope)
- **Task ID**: validate-keying
- **Depends On**: build-keying, test-suites-keying
- **Assigned To**: topic-validator — **Agent Type**: validator — **Parallel**: false
- Runs only the Task-2-scoped Verification rows: the two N5 anti-criterion rows, the
  caveat-removal row, and the versioned-root-cache row. Confirms the Task 2 gating checkboxes.

### 10. Keying test suite (held scope)
- **Task ID**: test-suites-keying
- **Depends On**: build-keying
- **Assigned To**: topic-test-engineer — **Agent Type**: test-engineer — **Parallel**: false
- The Task-2-scoped test items only: the continuation-branch forum cases in
  `tests/unit/test_config_driven_routing.py`, the walk-termination and root-cache-namespace
  assertions in `tests/unit/test_context_helpers.py`, and the caveat-removal assertion.
- **Created by the round-3 BLOCKER split (2026-09-18).** These tests cannot exist before Task 2
  lands, which is precisely why they must not sit in the same node as the tests that can. Task 9
  consumes this; Tasks 6 and 8 must never depend on it.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resolver exists with truth table | `grep -rc "reply_to_top_id" bridge/ \| grep -v ":0"` | at least one file with a non-zero count |
| Field-count assertion updated | `grep -n "field_names) == 21" tests/unit/test_model_relationships.py` | one match |
| Topic field stored | `grep -c "topic_id" models/telegram.py` | output > 0 |
| Migration registered | `grep -c "topic_id" scripts/update/migrations.py` | output > 0 |
| No topic in session key — `resolve_root_session_id` (anti-criterion, owner ruling 1; Note N5) | `! sed -n '/^async def resolve_root_session_id/,/^async def _cache_walk_root/p' bridge/context.py \| grep -qi topic` | exit code 0 |
| No topic in session key — `_cache_walk_root` (anti-criterion, owner ruling 1; Note N5) | `! sed -n -E '/^async def _cache_walk_root/,/^(async def\|def) [a-z_]+\(/p' bridge/context.py \| grep -qi topic` | exit code 0 |
| General topic omit rule uses a named constant (Note N1) | `grep -c "GENERAL_TOPIC_ID" bridge/telegram_relay.py` | `> 0` (**RED on current main: 0 matches**) |
| No bare General literal in the topic guard (Note N1) | `! grep -n "reply_to" bridge/telegram_relay.py \| grep -q "== 1"` | exit code 0 |
| Topic resolved at ThreadMessage construction (Note N2) | `grep -c "topic_id" bridge/agent_catchup.py` | `> 0` |
| No header sniffing at the enqueue site (Note N2) | `! grep -q 'getattr(inbound, "reply_to"' bridge/agent_catchup.py` | exit code 0 |
| Keying caveat removed once Task 2 lands (Note N3; Task 9 scope) | `! grep -q "session keying not yet topic-aware" bridge/context.py` | exit code 0 — **run only in and after the Task 2 commit** |
| History query is topic-aware (Note N4, round-2 BLOCKER) | `grep -q "topic_id" tools/telegram_history/__init__.py && grep -A6 -E "^def get_recent_messages" tools/telegram_history/__init__.py \| grep -q "topic_id"` | exit code 0 — **RED on current main** |
| `build_conversation_history` threads the topic (Note N4) | `grep -A2 -E "^def build_conversation_history" bridge/context.py \| grep -q "topic_id"` | exit code 0 — **RED on current main** |
| Cross-topic bleed fixture exists and targets history (Note N4) | `grep -q "test_cross_topic_history_does_not_bleed" tests/unit/test_context_helpers.py && grep -q "build_conversation_history" tests/unit/test_context_helpers.py` | exit code 0 — **RED on current main** |
| Cross-topic bleed fixture is genuinely RED pre-fix (Note N4) | run `test_cross_topic_history_does_not_bleed` on `main` via `scripts/pytest-clean.sh` | test FAILS on `main`, passes on the branch; transcript recorded in the PR |
| `build_context_prefix` can carry a topic (round-2 BLOCKER) | `grep -A8 -E "^def build_context_prefix" bridge/context.py \| grep -q "topic_name"` | exit code 0 — **RED on current main** |
| Exactly one `build_context_prefix` definition tree-wide (round-3 sweep) | `[ "$(grep -rn 'def build_context_prefix' --include='*.py' . \| grep -v '/.venv/\|/.worktrees/' \| wc -l \| tr -d ' ')" = "1" ]` | exit code 0 — **RED on current main** (3 definitions). A docstring-keyed check would miss mirror #2, which has no "Local mirror" docstring. |
| Production call site updated (round-3 sweep) | `grep -A8 "build_context_prefix(" agent/session_runner/harness/claude.py \| grep -q "topic_id"` | exit code 0 — **RED on current main** |
| Root cache is versioned on BOTH sides (critique CONCERN) | `! grep -q 'f"session_root:{chat_id}' bridge/context.py && grep -q "SESSION_ROOT_KEY_VERSION" bridge/context.py` | exit code 0 — **RED on current main** (two bare literals at `:710`, `:733`) |
| fetch_reply_chain untouched (No-Go #2732) | `! git diff main -- bridge/context.py \| grep -q "def fetch_reply_chain"` | exit code 0 |

## Critique Results

### Round 2 (2026-09-16)

**Round 2 (2026-09-16, FULL roster, independent).** Round 1's six CONCERNs and two NITs were
re-checked against the revision: **N1, N2, N5, N6 and both NITs are confirmed closed** (the N1
rows are RED on current main at 0 matches; both N5 `sed` ranges are non-empty at 98 and 57 lines
and return 0; the N6 Parallelism Contract's wave file sets are disjoint and the `bridge/context.py`
anchors :110/:526/:720/:742/:839 are exact). **N3 is closed in form** — the caveat text is a
required Task 4 behavior with a Task 2 deletion checkbox. **N4 is NOT closed** — see the BLOCKER
below. The table is the round-2 findings.

**Round-2 revision applied 2026-09-18.** All five round-2 findings are addressed; every
coordinate the round-2 critique cited was independently re-verified against `origin/main`
(`2eaa4c73d`, 15 commits past the SHA the critique used) before editing — `_get_cached_root:697`
/ `:710`, `_set_cached_root:720` / `:733`, the `test_bridge_logic.py:87` mirror, and the three
scanner assignments at `catchup.py:409` / `reconciler.py:333` / `agent_catchup.py:699` all still
hold. The six new positive Verification rows were **executed on `main` and confirmed RED**; the
five anti-criterion rows were rewritten from `grep -c ... | 0` to `! grep -q` exit-status form,
because `grep -c` exits 1 on a zero count and so fails on exit status before its output is ever
compared — the old rows could not distinguish "absent" from "command errored".

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Scope & Value + structural check | Note N4's cross-topic-bleed fixture and its Success Criterion both target `bridge/context.py::build_context_prefix` (`:110`), whose verified signature is `(project, session_type, sender_id=None, persona=None, email_from=None, sender_name=None) -> str`. It ingests **no** `TelegramMessage`, chat history, or `content` — so "render the context block for topic B's message and assert topic A's `content` is absent" is vacuously true with or without the defect, and would ship green while certifying a harm it cannot detect. The real bleed surface is `build_conversation_history(chat_id, limit)` (`bridge/context.py:319`), which calls `get_recent_messages(str(chat_id), ...)` — **chat-scoped and topic-blind**, so in a forum it mixes every topic's messages into one `RECENT CONVERSATION:` block. Neither `build_conversation_history` nor `get_recent_messages` appears anywhere in the plan, so Task 2's keying fix would not stop the bleed the Problem section costs. Task 4's topic line has the same target problem: `build_context_prefix` has no topic or message parameter to render from. | **ADDRESSED 2026-09-18.** Note N4 rewritten: the `build_context_prefix` closure is explicitly withdrawn as vacuous. Fixture and Success Criterion now target `build_conversation_history` (`:319`) / `get_recent_messages` (`tools/telegram_history/__init__.py:544`), both gaining `topic_id: int \| None = None`. History-filter work added to Task 4's scope and wave-2 file set. `build_context_prefix`'s `topic_id`/`topic_name` params are an explicit Task 4 deliverable and a Success Criterion. Six Verification rows added, all executed and confirmed RED on `main`, plus a row requiring the fixture's RED-on-`main` transcript in the PR. | Two edits, both required. (1) Re-point the N4 fixture and Success Criterion at `build_conversation_history` (`bridge/context.py:319`) and give it a topic filter: `get_recent_messages` must gain a `topic_id: int \| None = None` kwarg that narrows the `TelegramMessage` query, and `build_conversation_history(chat_id, limit, topic_id=None)` must thread it. The fixture is then RED on current main (two rows, one `chat_id`, distinct `topic_id` and `content`; assert topic A's `content` is absent from `build_conversation_history(chat_id, topic_id=B)`), which the `build_context_prefix` version can never be. Add the history-filter work to Task 4's scope and to the wave-2 file set. (2) State Task 4's `build_context_prefix` signature change explicitly — it must gain `topic_id: int \| None = None` and `topic_name: str \| None = None` params, since nothing in its current arg list can carry a topic. |
| CONCERN | Risk & Robustness | Risk 2's cache-poisoning mitigation and Task 2's scope name only `_set_cached_root` (`bridge/context.py:720`) for the namespace bump. The **reader**, `_get_cached_root` (`:697`), builds the same key `session_root:{chat_id}:{msg_id}` (`:710`) and is called first by `resolve_root_session_id` at `:781` to short-circuit the walk. Bumping only the writer leaves every pre-fix poisoned entry still served as authoritative at that short-circuit — reproducing the exact collapse Risk 2 claims to close — while post-fix writes are never read back. | **ADDRESSED 2026-09-18.** Task 2 now names `_get_cached_root` (`:697`) beside `_set_cached_root` and mandates one `SESSION_ROOT_KEY_VERSION` with a shared key-builder used by both, removing both literals (`:710`, `:733`) in the same commit. Gating checkbox + Verification row added; row confirmed RED on `main`. | Both functions build the key literal independently (`:710` and `:733`); the bump must change both in the same commit. Prefer a single module-level `SESSION_ROOT_KEY_VERSION = "v2"` with one shared key-builder used by `_get_cached_root` and `_set_cached_root`, and add a Verification row `grep -c "session_root:v2:" bridge/context.py` expecting `2` (or `1` for a shared builder) so a writer-only bump cannot ship green. Name `_get_cached_root` in Task 2's scope line beside `_set_cached_root`. |
| CONCERN | Risk & Robustness + structural check | The Task 2 hold — the plan's central constraint — exists only in prose (`Prerequisites`, Task 7's "Gating" line). The machine-readable graph contradicts it in both directions: Task 2's `Depends On: build-capture, build-context` omits `verify-live` entirely, so `do-build` may dispatch the held task; and Task 8's `Depends On: all previous` includes the held `build-keying`, so final validation blocks on the same indefinite owner action the Tasks 1/3/4 split was designed to escape. | **ADDRESSED 2026-09-18.** `verify-live` added to Task 2's `Depends On`. Task 8 (`validate-all`) re-scoped to `build-capture, build-outbound, build-context, test-suites, document-feature` — `build-keying` dropped — and new Task 9 (`validate-keying`, `Depends On: build-keying`) runs the four Task-2-scoped rows. | Add `verify-live` to Task 2's `Depends On` so the hold is in the graph, not just the prose. Then split Task 8: `validate-all` gets `Depends On: build-capture, build-outbound, build-context, test-suites, document-feature` (drop `build-keying`), and a new Task 9 `validate-keying` gets `Depends On: build-keying` and runs only the Task-2-scoped rows (the two N5 anti-criterion rows, the caveat-removal row, the cross-topic-bleed row). Without this the PR carrying Tasks 1/3/4 has no validator that can complete before the owner acts. |
| CONCERN | Structural check | `tests/unit/test_bridge_logic.py:87` defines a **local mirror** of `build_context_prefix` (its own docstring at `:97` says "Local mirror of bridge.context.build_context_prefix for unit tests") and exercises it at `:398-421`. Task 4 changes the real function's output and signature; the mirror will not change, so that suite keeps passing against a stale copy and silently stops covering the shipped behavior. Test Impact lists neither this file nor the mirror. | **ADDRESSED 2026-09-18.** `tests/unit/test_bridge_logic.py` added to Test Impact as UPDATE, owned by Task 4, with deletion-plus-direct-import preferred and same-commit update as fallback. Verification row asserts the mirror docstring is gone (or the mirror's params match). | Add `tests/unit/test_bridge_logic.py` to Test Impact as UPDATE and make Task 4 own it: either update the mirror at `:87` in the same commit as the real signature change, or delete the mirror and import `bridge.context.build_context_prefix` directly. A mirror that drifts from its original is the failure mode here, so prefer deletion + direct import; if the mirror exists to avoid an import cost, the update must land in the same commit or the suite is green-but-blind. |
| CONCERN | History & Consistency | The three recovery-scanner session-id sites are cited with two mutually inconsistent, partly stale line sets: `Freshness Check / Notes` gives `catchup.py:405, reconciler.py:321, agent_catchup.py:693`; spike-3 and the Test Impact scanner bullet give `catchup.py:409, reconciler.py:316, agent_catchup.py:688`. Verified on `origin/main` @ `3b30830f6`: `catchup.py:409` is correct, `reconciler.py:316` is stale (actual `:333`), and `agent_catchup.py:688` is a docstring line, not the assignment (actual `:699`). The revision built a canonical anchor block for `bridge/context.py` but never ran the same pass over the scanner citations. | **ADDRESSED 2026-09-18.** All three sections reconciled to the re-verified `bridge/catchup.py:409`, `bridge/reconciler.py:333`, `bridge/agent_catchup.py:699`; the stale `:405/:321/:693` and `:316/:688` sets deleted. Re-confirmed on `2eaa4c73d`. Note N2's `ThreadMessage(...)` site at `:435` untouched. | Reconcile all three sections to the re-verified values `bridge/catchup.py:409`, `bridge/reconciler.py:333`, `bridge/agent_catchup.py:699`, and delete the older `:405/:321/:693` set rather than leaving two sets in the doc. The load-bearing one is `agent_catchup.py:699` — `session_id = f"tg_{project_key}_{chat.chat_id}_{inbound.message_id}"` — which is where the scanner-path topic threading assertion belongs; `:688` is inside the docstring that starts near `:685`, and a builder navigating by it would misplace the test. Note this is a different site from Note N2's `ThreadMessage(...)` construction at `:435`, which stays as written. |

### Round 3 (2026-09-18, FULL roster, independent)

Roster: Risk & Robustness, Scope & Value, History & Consistency. Mode: independent roster
(3 critics), dispatched concurrently with no shared context. **Scope & Value returned
`No findings.`** after re-checking every Success Criterion for the round-2 vacuousness shape
and finding none that reproduce it. Risk & Robustness returned no Skeptic or Adversary
findings — Risk 1's header assumption is gated on the owner action rather than asserted, and
Races 1/2 already carry concrete mitigations.

**Revision applied 2026-09-18.** Both findings are addressed; the Task 8 closure was re-walked
mechanically across all ten tasks rather than by re-reading the edges that changed. A third
item surfaced during the revision that no critic could have cited, because it lay outside the
bridge-scoped source bundle the critics were given: a tree-wide sweep for
`def build_context_prefix` found **three** definitions, not the two on record, plus the only
production call site at `agent/session_runner/harness/claude.py:1696` — outside `bridge/`
entirely. Mirror #2 (`tests/integration/test_message_routing.py:92`) has a *different, shorter*
signature and no "Local mirror" docstring, so the docstring-keyed Verification row written in
round 2 would have missed it. That row is replaced by a tree-wide definition count. Full
inventory in Test Impact.

**Independent convergence on Task 8.** History & Consistency (transitive dependency closure)
and Risk & Robustness (validator row-set ordering) reached the same region from different
lenses without seeing each other's work, and the skill's own automated dependency check
reached it a third time. Two distinct mechanisms, two distinct fixes.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency (+ automated dependency check) | The round-2 fix to Task 8 removed the **direct** `build-keying` edge but the edge returns transitively: Task 8 `Depends On: ... test-suites, document-feature` (`:576`); Task 6 `document-feature` `Depends On: test-suites` (`:560`); Task 5 `test-suites` `Depends On: build-keying, build-outbound, build-context` (`:554`); Task 2 `build-keying` `Depends On: ... verify-live`. The closure `validate-all -> document-feature -> test-suites -> build-keying -> verify-live` means Task 8 still cannot complete until the owner acts — the exact indefinite block the round-2 CONCERN existed to remove. The edit satisfied the letter of that fix and not its purpose, because the edge was re-introduced one and two hops away. | **ADDRESSED 2026-09-18.** Task 5 split: `test-suites` becomes `test-suites-unheld` (`Depends On: build-outbound, build-context`), consumed by Tasks 6 and 8; new Task 10 `test-suites-keying` (`Depends On: build-keying`) feeds Task 9, which now declares `Depends On: build-keying, test-suites-keying`. Task 8 fully expanded under "Transitive closure of Task 8" — **no path reaches `build-keying` or `verify-live`**. Verified by walking the closure of all ten tasks mechanically, not by re-reading the edited edges: only `build-keying`, `test-suites-keying` and `validate-keying` reach the hold, which is correct. | The guard a builder needs is "Task 8's **transitive** closure resolves without passing through `build-keying`", and as written it does not. Split Task 5 the way Tasks 8/9 were split: a `test-suites-unheld` (`Depends On: build-outbound, build-context`) covering the Task 1/3/4 test items, which Task 6 and Task 8 consume, and a `test-suites-keying` (`Depends On: build-keying`) feeding Task 9. Verify the fix by walking the closure, not the direct edges — a dispatcher that walks only direct edges will avoid dispatching Task 8 early but then stall on Task 5, which relocates the stall rather than removing it. |
| CONCERN | Risk & Robustness | Task 8's row set is "every Verification row except the four Task-2-scoped ones", which leaves it running the Note N3 row "Keying caveat present while Task 2 is held" (`:608`, expected exit 0). That row's precondition — "run only before Task 2 merges" — exists only in prose. Nothing in the graph orders Task 8 against Task 9, so if the owner resolves `verify-live` quickly and Task 2 lands first, Task 2's gating checkbox (`:512`) will have deleted the caveat string before Task 8 evaluates the row, and Task 8 fails on a correctly-shipped, fully-merged feature. A false negative produced purely by task-completion order, with no defect in the code. | **ADDRESSED 2026-09-18.** The "Keying caveat present while Task 2 is held" row is **deleted from the Verification table** and re-homed as a Task 4 review check, where Task 2 does not yet exist. Only the "caveat removed" row survives, scoped to Task 9. Rationale recorded in Task 4: the table is run by validators whose dependency sets permit running after Task 2 merged, and a row whose precondition lives only in prose is not a row. | The two N3 rows are mutually exclusive by construction, so they must never both be live. Drop "Keying caveat present while Task 2 is held" from Task 8's automatic set entirely — it is not one of the four rows named as Task-2-scoped, so as written Task 8 runs it unconditionally. Enforce "caveat present pre-hold" in Task 4's own review instead (before Task 2 exists at all), and leave the "caveat removed" row to Task 9. A validator whose dependency set permits running after Task 2 has merged must not assert pre-merge state. |


---

## Owner Rulings (2026-09-05, via /ask-me)

All five plan questions are settled; the sections above already incorporate them.

1. **Session granularity**: topic as context only. Sessions stay per-conversation; topic id is captured, stored, rendered, and used for outbound targeting, never folded into the session key.
2. **Config shape**: `default_topic_id: int` plus optional advisory `topics: {id: subdir}` map, keyed by topic **id**. Ids survive admin renames; names-as-keys was rejected for the rename hazard and the extra `GetForumTopics` resolution it would require.
3. **Mapping semantics**: advisory context only. Enforcement (topic mapping constraining a session's writable scope) is out of scope and would start as its own issue.
4. **Poll sends**: included. The #3080 poll surface resolves `default_topic_id` like every other unsolicited-send producer.
5. **Live verification**: during build, before the keying change merges. Task 7 executes from a machine that **owns** a Topics-enabled group. Re-scoped 2026-09-16 (see the OPEN QUESTION under Prerequisites): it needs no code and no Cyndra access, because `TelegramMessage.reply_to_msg_id` and `AgentSession.session_id` are already persisted — any owned forum group answers it. It is now an owner action, and only Task 2 is gated on it.
