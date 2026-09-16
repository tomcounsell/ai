---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-04
tracking: https://github.com/tomcounsell/ai/issues/2652
last_comment_id: 5695879375
revision_applied: true
revision_applied_at: 2026-09-16T10:26:43Z
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
   `if message.reply_to_msg_id:`, and `bridge/context.py:569-610`'s chain walk builds
   `tg_{project_key}_{chat_id}_{root_msg_id}` — terminating at the topic root means every
   top-level message in that topic resolves to one unbounded session. Reasoned from code and
   Telegram API docs; not yet observed live (see Prerequisites).

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

**Notes:** recovery scanners (`bridge/catchup.py:405`, `bridge/reconciler.py:321`,
`bridge/agent_catchup.py:693`) key sessions per-message and never walk the reply chain, so
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
- **Finding**: They key `tg_{project}_{chat}_{message.id}` directly (catchup.py:409, reconciler.py:316, agent_catchup.py:688) — immune to collapse, but topic-blind. Re-verified 2026-09-16: they do **not** write `TelegramMessage` rows at all. The only production callers of `tools/telegram_history.store_message()` are `bridge/telegram_bridge.py:1567` (live intake), `bridge/telegram_bridge.py:3218` (outbound record) and `bridge/telegram_relay.py:383`. The scanners call `enqueue_agent_session`.
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
- Env plumbing for agent-invoked sends: sessions created from a topic export `TELEGRAM_TOPIC_ID` beside `TELEGRAM_REPLY_TO`; `tools/send_message.py` uses it when `TELEGRAM_REPLY_TO` is unset.

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
  `bridge/reconciler.py:316`, `bridge/agent_catchup.py:688`). The only production callers of
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
- [ ] Migration runs idempotently; second run is a no-op.
- [ ] Tests pass (`/do-test`); Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (resolver + storage)** — Name: `topic-capture-builder` — Role: resolver helper, TelegramMessage field, migration, scanner threading — Agent Type: builder — Resume: true
- **Builder (keying + outbound)** — Name: `topic-routing-builder` — Role: continuation branch, walk termination, cache bump, default-topic outbound, env plumbing — Agent Type: builder — Resume: true (Domain: Redis/Popoto data + async — paste matching DOMAIN_FRAMING rules)
- **Test engineer** — Name: `topic-test-engineer` — Role: synthetic-header unit suites, recovery-scanner parity tests, relay send-path tests — Agent Type: test-engineer — Resume: true
- **Validator** — Name: `topic-validator` — Role: run Verification table, confirm Success Criteria, byte-identical non-forum regression check — Agent Type: validator — Resume: true
- **Documentarian** — Name: `topic-documentarian` — Role: feature doc + index — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### 1. Topic resolver + storage
- **Task ID**: build-capture
- **Depends On**: none
- **Validates**: tests/unit/test_topic_resolver.py (create)
- **Informed By**: spike-1, spike-3, Research header truth table
- **Assigned To**: topic-capture-builder — **Agent Type**: builder — **Parallel**: true
- Resolver helper with the truth table + #3831 defense; `TelegramMessage.topic_id`; `store_message(topic_id=...)` kwarg; migration in `MIGRATIONS`; bump the field-count assertion in `tests/unit/test_model_relationships.py:110` (20 → 21) in the same commit; thread the resolver through live intake (row persistence) and the three scanners (session context only).

### 2. Keying correction + cache hygiene
- **Task ID**: build-keying
- **Depends On**: build-capture
- **Validates**: tests/unit/test_config_driven_routing.py, tests/unit/test_context_helpers.py
- **Informed By**: spike-1 (two sites only), Risk 2
- **Assigned To**: topic-routing-builder — **Agent Type**: builder — **Parallel**: false
- Continuation branch + walk termination + cache namespace bump.

### 3. Outbound default topic + env plumbing
- **Task ID**: build-outbound
- **Depends On**: build-capture
- **Validates**: tests/unit/test_bridge_relay.py, tests/unit/test_send_message.py
- **Informed By**: spike-2 (reply_to suffices; General omit rule)
- **Assigned To**: topic-routing-builder — **Agent Type**: builder — **Parallel**: true (with build-keying only if file sets stay disjoint; otherwise serialize after it)
- Config keys + validation posture, producer resolution (PM briefings, send_message, poll sends), relay General-guard, `TELEGRAM_TOPIC_ID`.

### 4. Context rendering + name resolution
- **Task ID**: build-context
- **Depends On**: build-capture
- **Validates**: context snapshot test
- **Assigned To**: topic-capture-builder — **Agent Type**: builder — **Parallel**: true
- Topic line in agent context; lazy GetForumTopics cache, fail-soft; advisory subdir hint when configured.

### 5. Test suites
- **Task ID**: test-suites
- **Depends On**: build-keying, build-outbound, build-context
- **Assigned To**: topic-test-engineer — **Agent Type**: test-engineer — **Parallel**: false
- All Test Impact + Failure Path items; synthetic MessageReplyHeader fixtures.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: test-suites
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

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: topic-validator — **Agent Type**: validator — **Parallel**: false

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
| No topic in session key (anti-criterion, owner ruling) | `grep -rn "topic" bridge/context.py \| grep -c "session_id = f"` | match count == 0 |
| General topic omit rule | `grep -rn "GENERAL_TOPIC_ID\|== 1" bridge/telegram_relay.py \| head -1` | output contains 1 |
| fetch_reply_chain untouched (No-Go #2732) | `git diff main -- bridge/context.py \| grep -c "def fetch_reply_chain"` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Structural check | Verification row "General topic omit rule" (`grep -rn "GENERAL_TOPIC_ID\\|== 1" bridge/telegram_relay.py \| head -1`, expected "output contains 1") already PASSES on unmodified origin/main: `bridge/telegram_relay.py:656` carries `len(available) == 1` and `:719` carries `len(available) == 1`. The row is vacuous — it proves nothing about the General-topic send guard and would report green before any work is done. | pending | Replace with a row that is RED on current main, e.g. `grep -c "GENERAL_TOPIC_ID" bridge/telegram_relay.py` expected `> 0` (the constant does not exist today — verified zero matches), and require the builder to introduce a named `GENERAL_TOPIC_ID = 1` constant rather than a bare literal. Drop the `\|\| == 1` alternation entirely; it collides with `len(available) == 1`. |
| CONCERN | Risk & Robustness | The resolver is specified against `message.reply_to` (Telethon raw `MessageReplyHeader`), but one of the three scanner threading sites, `bridge/agent_catchup.py:680-700`, works on `inbound: ThreadMessage` — a wrapper type whose preservation of `reply_to`/`forum_topic`/`reply_to_top_id` is unverified. The shared-resolver plan may not be callable at that site. | pending | Call the resolver at the point `ThreadMessage` is CONSTRUCTED (raw Telethon message still in scope), not inside the `enqueue_agent_session` call chain at `bridge/agent_catchup.py:680-700`. If `ThreadMessage` must carry it, add a `topic_id: int \| None = None` field to the dataclass and populate it at construction; do not attempt `getattr(inbound, "reply_to", None)` at the enqueue site. |
| CONCERN | Risk & Robustness | The Tasks 1/3/4-merge / Task 2-held split leaves an open-ended window where Task 4 renders a confident topic name/id into agent context while `bridge/routing.py:1323` and `bridge/context.py:761` (`_cache_walk_root`) still collapse every top-level topic message into the topic root's session. That is a more convincing-looking failure than today's flat-chat baseline, with no kill-switch or deadline. | pending | Gate Task 4's context line behind a flag read alongside the existing `default_topic_id` resolution (default OFF), flipped ON in the same commit that merges Task 2 — one deploy, flag lifetime bounded to the hold period. Alternative accepted by the owner: render the topic line with an explicit "(session keying not yet topic-aware)" caveat until Task 2 merges. | 
| CONCERN | Scope & Value | All eight Success Criteria are technical assertions (session_id shape, field round-trip, migration idempotency, relay `reply_to` value). None validates the user-facing harm the Problem section names: cross-topic context bleed through `resolve_root_session_id`/`_cache_walk_root` (`bridge/context.py:664-758`). | pending | Add one criterion from the user's vantage point and back it with a fixture in the planned `tests/unit/test_context_helpers.py` additions: two synthetic `TelegramMessage` rows sharing a `chat_id` with different `topic_id`; assert the rendered context block for topic B's message does not contain topic A's `content` string. Assert on rendered context, not on `session_id` inequality. |
| CONCERN | History & Consistency | The anti-criterion `grep -rn "topic" bridge/context.py \| grep -c "session_id = f"` only fires when both substrings land on the SAME line. The real assignments sit on their own lines (`bridge/context.py:705`, `:715`, `:737`), so a two-line topic-conditional branch would violate Owner Ruling 1 while the check still reports 0 / "pass". | pending | Scope the check to the two function bodies Task 2 edits rather than a single matched line: `sed -n '/def resolve_root_session_id/,/^async def \\\|^def /p' bridge/context.py \| grep -ci topic` expected `0`, and the same range check over `_cache_walk_root` (`bridge/context.py:761`). |
| CONCERN | History & Consistency | Task 2 (build-keying) is "Parallel: false" and Task 3 (build-outbound) is "Parallel: true (with build-keying...)", yet BOTH are assigned to the same builder `topic-routing-builder`. One agent instance cannot run two tasks concurrently with itself, so the parallel clause is incoherent and the file-set-disjointness condition cannot apply. | pending | Either reassign build-outbound to a distinct builder, or change Task 3's line to "Assigned To: topic-routing-builder — Parallel: false (serializes after build-keying, same builder)" and delete the disjoint-file-set conditional. Note the split-merge constraint makes serializing Task 3 behind held Task 2 undesirable — prefer the distinct-builder option so Task 3 can merge while Task 2 is held. |
| NIT | History & Consistency | Problem item 3 still cites `bridge/context.py:569-610` and `_set_cached_root:514`; on current main those are `resolve_root_session_id` (:664), `_set_cached_root` (:642), `_cache_walk_root` (:761). The Freshness Check flags the file moved but the Problem section's own citations remain stale. | pending | n/a (NIT) |
| NIT | Scope & Value | The advisory `topics: {id: subdir}` map is additive capability, not a fix for any of the three costed defects under "Current behavior". Owner-ruled and enforcement-free, so no change required — flagged only so a later scope audit does not mistake it for a defect fix. | pending | n/a (NIT) |

---

## Owner Rulings (2026-09-05, via /ask-me)

All five plan questions are settled; the sections above already incorporate them.

1. **Session granularity**: topic as context only. Sessions stay per-conversation; topic id is captured, stored, rendered, and used for outbound targeting, never folded into the session key.
2. **Config shape**: `default_topic_id: int` plus optional advisory `topics: {id: subdir}` map, keyed by topic **id**. Ids survive admin renames; names-as-keys was rejected for the rename hazard and the extra `GetForumTopics` resolution it would require.
3. **Mapping semantics**: advisory context only. Enforcement (topic mapping constraining a session's writable scope) is out of scope and would start as its own issue.
4. **Poll sends**: included. The #3080 poll surface resolves `default_topic_id` like every other unsolicited-send producer.
5. **Live verification**: during build, before the keying change merges. Task 7 executes from a machine that **owns** a Topics-enabled group. Re-scoped 2026-09-16 (see the OPEN QUESTION under Prerequisites): it needs no code and no Cyndra access, because `TelegramMessage.reply_to_msg_id` and `AgentSession.session_id` are already persisted — any owned forum group answers it. It is now an owner action, and only Task 2 is gated on it.
