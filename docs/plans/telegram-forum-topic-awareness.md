---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-04
tracking: https://github.com/tomcounsell/ai/issues/2652
last_comment_id: none
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
(#2732, In Progress) also edits `bridge/context.py` (`fetch_reply_chain`). Coordination
signal: this plan's walk-termination change touches the session-root walk
(`_cache_walk_root`), a different function in the same file — merge order matters, no design
conflict.

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
- **Finding**: They key `tg_{project}_{chat}_{message.id}` directly (catchup.py:405, reconciler.py:321, agent_catchup.py:693) — immune to collapse, but topic-blind: recovered messages need the same topic capture at their persistence sites.
- **Confidence**: high
- **Impact on plan**: topic capture must be a shared helper the scanners call too, not inline bridge code.

## Data Flow

1. **Entry point**: Telethon `events.NewMessage` → `bridge/telegram_bridge.py` intake (live) or the three recovery scanners (backfill).
2. **Topic resolution (new)**: shared helper reads `message.reply_to` → `(topic_id, is_topic_root_reply)`; General/non-forum → `topic_id=None`.
3. **Storage**: `TelegramMessage` gains `topic_id` (nullable); `AgentSession` carries topic via existing extra-context/session fields (see Open Questions).
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
- PM check-ins: 1-2 (Open Questions below; empirical-confirmation scheduling)
- Review rounds: 1-2

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge-owning machine for live verification | `python -c "import json,os;cfg=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json')));print(any('cyndra' in k for k in cfg.get('projects',{})))"` | Tasks 7-8 need a host whose bridge owns the Cyndra forum group; THIS host (worker-only, no bridge) cannot run them — see the [EXTERNAL] No-Go. |

No other prerequisites — all remaining work is code + unit tests with synthetic `MessageReplyHeader` objects.

## Solution

### Key Elements

- **Topic resolver helper** (`bridge/topic.py` or inside `bridge/routing.py`): one function from a Telethon message to `(topic_id | None, is_top_level_topic_message)` implementing the header rules and the #3831 quirk defensively.
- **Capture + storage**: `TelegramMessage.topic_id`; populated by live intake and the three recovery scanners through the shared helper; Popoto migration registered in `MIGRATIONS`.
- **Keying correction**: continuation branch and chain walk treat top-level topic messages as fresh sessions; walk never crosses a topic root; root-cache hygiene for the semantics change.
- **Context rendering**: topic id + best-effort name (GetForumTopics, cached, fail-soft to id-only) in the agent's context; optional advisory subdirectory hint from config.
- **Outbound default topic**: `default_topic_id` per group in `projects.json`; producers of unsolicited sends resolve it; relay guard maps General/none to a plain send; reply-inside-topic uses raw `InputReplyToMessage` where both ids are known.

### Flow

Inbound topic message → resolver tags `topic_id`, flags top-level → fresh session keyed by its own message id, context says "topic: behring (id 123)" → agent replies (topic inherited) or later sends unsolicited → default-topic resolution → relay targets the topic instead of General.

### Technical Approach

- Topic id resolution: `header = message.reply_to; topic_id = header.reply_to_top_id or (header.reply_to_msg_id if header.forum_topic else None)`; `is_top_level = header.forum_topic and header.reply_to_top_id is None`. Non-forum and General yield `topic_id=None`.
- Session granularity stays per-conversation (recommended; see Open Question 1): topic id is context + storage, NOT part of the session key. The keying fix is purely "stop mistaking topic roots for replies".
- Root cache: key the cached roots under a bumped namespace (or include a semantics version) so pre-fix cached collapses cannot serve post-fix lookups.
- Name resolution: lazy `channels.GetForumTopics` from the bridge (never a second client), cached in Redis via ORM model or reuse of an existing cache pattern, always fail-soft to id-only.
- Config: `projects.json` group entry gains optional `default_topic_id` (int) and optional `topics: {name: subdir}` advisory map; `bridge/config_validation.py` accepts-and-validates both, absent keys keep today's behavior exactly.
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

- [ ] `tests/unit/test_bridge_routing.py` — UPDATE: continuation-branch cases gain forum variants (top-level topic message → fresh session; genuine in-topic reply → continuation).
- [ ] `tests/unit/test_context_helpers.py` — UPDATE: chain-walk tests gain topic-root termination cases and root-cache namespace assertions.
- [ ] `tests/unit/test_message_routing.py` — UPDATE: intake persistence asserts `topic_id` stored (None for non-forum fixtures, so existing cases assert the new field's default).
- [ ] `tests/unit/test_reply_delivery.py` — UPDATE: outbound cases assert General/none omits `reply_to` and default-topic resolution applies to unsolicited sends only.
- [ ] Recovery-scanner tests (`tests/unit/` files covering catchup/reconciler/agent_catchup enqueues) — UPDATE: assert the shared resolver is called and `topic_id` persists on recovery-path messages.

## Rabbit Holes

- **Backfilling topic ids onto historical TelegramMessage rows** — the migration adds the field; it does NOT re-read Telegram history. Old rows stay `topic_id=None`.
- **Topic-scoped write enforcement** — mapping a topic to a subdirectory is advisory context in this plan; building permission enforcement around it is a separate design (see Open Question 3).
- **A general topics-admin surface** (create/rename/list topics from the agent) — out of scope entirely.
- **Rebuilding session granularity** — resist folding topic id into the session key "while we're here"; that changes UX for every non-forum chat and is exactly the both-defensible fork Open Question 1 gives the owner.

## Risks

### Risk 1: The empirical pre-requisite invalidates the header model
**Impact:** If a real top-level topic message does NOT carry the predicted header shape, the disambiguation rule is wrong and the keying fix misfires.
**Mitigation:** Unit tests encode the documented shapes; Task 7 verifies live on a bridge-owning machine BEFORE the keying change ships to the fleet (build order puts capture/storage first, keying second); the #3831 quirk is handled defensively.

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

- [EXTERNAL] Live confirmation of the top-level-topic header shape and the "Cyndra Devs" forum flag needs a bridge-owning machine (Cowboy/Captain/Bald); this host runs no bridge and a second Telethon client on the shared session file is unsafe (#726). Tasks 7-8 name this constraint; they execute on a bridge host during build, not from here.
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
- [ ] `TelegramMessage.topic_id` persists through live intake AND all three recovery scanners (one test each).
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
- Resolver helper with the truth table + #3831 defense; `TelegramMessage.topic_id`; migration in `MIGRATIONS`; thread through live intake and the three scanners.

### 2. Keying correction + cache hygiene
- **Task ID**: build-keying
- **Depends On**: build-capture
- **Validates**: tests/unit/test_bridge_routing.py, tests/unit/test_context_helpers.py
- **Informed By**: spike-1 (two sites only), Risk 2
- **Assigned To**: topic-routing-builder — **Agent Type**: builder — **Parallel**: false
- Continuation branch + walk termination + cache namespace bump.

### 3. Outbound default topic + env plumbing
- **Task ID**: build-outbound
- **Depends On**: build-capture
- **Validates**: tests/unit/test_reply_delivery.py + new relay cases
- **Informed By**: spike-2 (reply_to suffices; General omit rule)
- **Assigned To**: topic-routing-builder — **Agent Type**: builder — **Parallel**: true (with build-keying only if file sets stay disjoint; otherwise serialize after it)
- Config keys + validation posture, producer resolution, relay General-guard, `TELEGRAM_TOPIC_ID`.

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
- **Depends On**: build-capture (can precede keying rollout; see Risk 1)
- **Assigned To**: topic-validator — **Agent Type**: validator — **Parallel**: true
- On a bridge-owning machine: post a top-level message in a non-General Cyndra topic; inspect stored `TelegramMessage` header capture and resulting session id; confirm the forum flag on the group. Record results in the PR.

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
| Resolver exists with truth table | `grep -c "reply_to_top_id" bridge/` -r | output > 0 |
| Topic field stored | `grep -c "topic_id" models/telegram.py` | output > 0 |
| Migration registered | `grep -c "topic_id" scripts/update/migrations.py` | output > 0 |
| No topic in session key (anti-criterion, Open Q1 default) | `grep -rn "topic" bridge/context.py \| grep -c "session_id = f"` | match count == 0 |
| General topic omit rule | `grep -rn "GENERAL_TOPIC_ID\|== 1" bridge/telegram_relay.py \| head -1` | output contains 1 |
| fetch_reply_chain untouched (No-Go #2732) | `git diff main -- bridge/context.py \| grep -c "def fetch_reply_chain"` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Session granularity for forums**: keep per-conversation sessions with topic as context only (recommended: preserves non-forum UX, smallest change, collapse fixed at the branch), or fold `topic_id` into the session key so each topic is one session? The issue calls both defensible; the recommendation above is the plan's default pending your ruling.
2. **`projects.json` config shape**: is `default_topic_id: int` plus an optional advisory `topics: {name: subdir}` map per group entry acceptable, given the file is private and hand-edited across the fleet? Any preference for names-as-keys vs ids-as-keys (names need GetForumTopics resolution and can be renamed by Telegram admins)?
3. **Subdirectory mapping semantics**: advisory context only (plan default), or should a topic's mapping constrain the Eng session's working scope (a much bigger enforcement design)?
4. **Poll sends** (#3080 surface): should polls also honor `default_topic_id` in this plan, or stay General until the poll umbrella (#3095) picks it up? Plan default: include them — it is the same one-line producer resolution.
5. **Scheduling the live verification**: Task 7 needs a bridge-owning machine (Cowboy/Captain/Bald). Run it during build (recommended, before the keying change merges), or accept documented-API-only confidence and verify post-deploy?
