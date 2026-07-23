# Agent-Judgment Catchup

Recovers sessioned-but-unanswered messages that the mechanical catchup and reconciler permanently skip, by reading the actual chat thread and using an LLM judge.

## Problem

The mechanical catchup (`bridge/catchup.py::scan_for_missed_messages`) and the periodic reconciler (`bridge/reconciler.py::reconcile_once`) both key recovery on **"did a session get enqueued"** — gated by `is_duplicate_message()` (a `DedupRecord` set of ~50 recent processed IDs per chat) plus the `LastProcessedRecord` cursor. Neither keys on **"did a reply actually reach the chat."**

A message whose session hung or was killed *without replying* is dedup-marked **processed** and skipped **forever** by both scanners — bookkeeping-indistinguishable from a message that was answered correctly. Recovery requires manual ORM surgery: clear the `DedupRecord` entry, rewind the `LastProcessedRecord` cursor, restart.

This layer answers a different question: *did Valor actually reply in the thread?*

### Motivating Incident

PR #1694 switched granite to `/granite:prime-pm-role` slash commands under `.claude/commands/granite/`, but `_sync_commands` globbed only top-level `*.md` and never recursed, so namespaced commands never reached `~/.claude/commands/granite/`. The result: `Unknown command` → no persona → no real turn → 600s startup ceiling → `startup_unresolved` → silent hang, `communicated=False`, no reply. Messages in the Cyndra Dev Team chat were enqueued fine (each got a `DedupRecord`), so the mechanical catchup returned "already processed" and could not recover them. (Root cause fixed separately by commit `3a3ff1ab` — `rglob` recursion.)

## How It Differs from Mechanical Catchup and Reconciler

All three are owner-scoped dialog scanners that reuse `enqueue_agent_session` and write through the same dedup path. The difference is the **failure stage they cover**:

| Component | File | Failure stage covered | Keyed on |
|-----------|------|-----------------------|----------|
| Mechanical catchup | `bridge/catchup.py` | **Ingestion gap** — message never enqueued (missed `pts`, startup gap) | `DedupRecord` + `LastProcessedRecord` cursor |
| Reconciler | `bridge/reconciler.py` | **Ingestion gap** — message missed during live connection | `DedupRecord` + `LastProcessedRecord` cursor |
| Agent-judgment catchup | `bridge/agent_catchup.py` | **Response failure** — message enqueued, session hung/killed, no reply | Thread read + LLM judge (source of truth: actual chat) |

The mechanical scanners can never recover a response failure because the `DedupRecord` entry already exists — from their perspective the message was handled. The agent-judgment layer reads the thread itself, making the dedup entry irrelevant to the judgment decision.

## Core Principle: "Answered Keys on the Thread, Not the Session"

The thread is the source of truth. Valor's own `out` messages are the ground truth for what has actually been said in the chat. The module reads the recent thread (including Valor's replies, marked by `m.out == True`, and Valor's own emoji reactions — see [Reaction Awareness](#reaction-awareness) below), builds a transcript, and asks an LLM judge to classify each inbound human message.

This approach dissolves the "failed-silently vs. correctly-silent" ambiguity that a mechanical replay cannot resolve. This layer never needed the mechanical scanners' reply-only heuristic (formerly `_check_if_handled` in `bridge/catchup.py`, deleted per #2204 — see [Dedup TTL Contract & Rollout Seed](#dedup-ttl-contract--rollout-seed) below) — the judgment layer reads the thread directly and independently supersedes it for this failure mode.

## LLM Judge

### Verdict Classes

The judge classifies each inbound human message (not Valor's own `out` messages) against the rendered thread transcript:

| Verdict | Meaning | Action |
|---------|---------|--------|
| `ANSWERED` | Valor already replied in the thread, OR no reply is warranted | No enqueue |
| `UNANSWERED_NEEDS_REPLY` | Genuine question/request with no Valor reply that clearly should be answered | Enqueue recovery session |
| `UNANSWERED_NO_REPLY_NEEDED` | No Valor reply yet, but none is warranted (acknowledgment, social chatter, directed elsewhere) | No enqueue |

### Conservative Contract

**Any error, ambiguity, or empty/garbage/None output maps to `ANSWERED` (no reply).** The function `judge_message` never raises — every failure path returns `ANSWERED`. The acceptance bar is: a thread whose recent messages were already answered produces NO recovery enqueue.

A missed reply is recoverable on the next sweep; a spurious double-reply to a customer is not.

### Backend

Routes through the [non-harness LLM wrapper](nonharness-llm-wrapper.md) (`agent.llm.run_typed`, Haiku/`MODEL_FAST`) with a typed `CatchupJudgeVerdict` output model, replacing the previous Ollama-first/Haiku-fallback pair. If the call fails or schema validation is exhausted, `ANSWERED` is returned.

## Double-Reply Guard (Race 1 Mitigation)

The judge loop runs one LLM call per message (up to `MAX_MESSAGES_PER_CHAT`), so many seconds can elapse between the snapshot read at the top of `sweep_chat` and an actual enqueue. The guard runs in two layers:

1. **Snapshot guard** (`_has_valor_reply_after`): checks whether a Valor `out` reply appears after the inbound message in the thread read at the start of the sweep. Position-based, not threaded-reply-based, because most replies are not threaded.

2. **Pre-enqueue re-read** (`_valor_replied_since`): immediately before enqueue, does a fresh targeted read of the last `MAX_MESSAGES_PER_CHAT` messages and checks for any Valor `out` message with `id > inbound_id`. Narrows the race window to near-zero.

If either layer sees a Valor reply after the message, the enqueue is skipped with a greppable `[agent-catchup]` WARNING.

## Idempotency

**Idempotency is provided by the landed-reply guard, NOT by a dedup read.** This module never reads the dedup set (`is_duplicate_message()`) to decide whether to enqueue — the thread is the source of truth. What keeps recovery to at most one reply per message is the two-layer landed-reply guard above.

The dedup write after enqueue is for the *mechanical* scanners' bookkeeping: once a recovery session's reply lands in the thread, every subsequent sweep sees it and skips. The mechanical scanners' next scan also sees the dedup entry and skips. No new watermark or store is created (per constraint established in #948).

## Reaction Awareness

`valor-catchup` treats a Valor emoji reaction on an inbound message as a
thread-native "handled" signal, closing the one blind spot the dedup-TTL fix
below does not reach: an emoji-reaction ack (the repo's preferred "I heard
you" signal, sent via `bridge/response.py::SendReactionRequest`) leaves no
reply message, so the reply-only judge previously saw an unanswered thread and
re-enqueued a recovery session for an already-acknowledged message.

`_valor_reacted(message)` (`bridge/agent_catchup.py`) inspects the Telethon
message object's `message.reactions.results[i].chosen_order` field, **not**
the bounded `recent_reactions` list or its `.my` flag. A live Telethon read
against a real chat (required verification step before building, per the plan
critique) found `recent_reactions`/`.my` unreliable for self-reactions in a
busy group — Valor's own reaction did not always appear in that capped list.
`chosen_order` on `MessageReactions.results` reliably reports the self
account's reaction regardless of how many other reactors are present. Any
missing/`None` reactions object, or an exception while reading it, defaults to
"not reacted" — conservative per the same contract as the LLM judge (a missed
reaction-only ack is recoverable on the next sweep; a spurious skip of a
genuine question is not).

`read_thread` captures this per message as `ThreadMessage.valor_reacted`. A
reacted-to inbound message is treated as `ANSWERED` before the LLM judge call
(no reply text exists to feed the judge, and no judgment is needed — the
reaction alone settles it). This is scoped strictly to `valor-catchup`; it
does not touch the dedup set or the mechanical scanners' skip decision (see
below), consistent with #948's "thread is source of truth" design for this
layer.

## Owner Scoping

Reuses the bridge's `ALL_MONITORED_GROUPS` (already filtered to this machine's owned groups via `ACTIVE_PROJECTS`) and the case-insensitive title match + duplicate-dialog guard from `scan_for_missed_messages`. Composes `find_project_for_chat` for project config lookup.

Single-machine-ownership invariant is preserved: the same set of chats that the bridge routes is the set that gets swept.

## Persona Correctness

For each recovery enqueue, persona is resolved via `resolve_persona(project, chat_title, is_dm=False)` → `persona_to_session_type(persona)` (the helpers introduced in #1708). On resolution failure, falls back to `SessionType.ENG` with a greppable WARNING.

The module **never composes reply text**. Only the original inbound message text is enqueued as `message_text`; the worker session produces the persona-correct reply through the normal relay → outbox path.

## Lookback Window

`min(last 20 messages, last 2 hours)` per chat. Constants: `MAX_MESSAGES_PER_CHAT = 20`, `LOOKBACK_HOURS = 2`. Mirrors the reconciler's bounded `get_messages` call (#1408). The `--lookback-hours` CLI flag overrides the time bound.

## `valor-catchup` CLI

```bash
valor-catchup                    # Sweep all owned chats, print summary
valor-catchup --lookback-hours 4 # Extend lookback window
```

Registered in `pyproject.toml [project.scripts]` as `valor-catchup = "bridge.agent_catchup:main"`. Propagated automatically via `pip install -e .` during the existing dependency-sync step.

The CLI prints a per-chat summary including chats that errored (never silently dropped):

```
[agent-catchup] sweep summary:
  Cyndra Dev Team (id=-1001234567): scanned=12, recovered=1
  Client Chat (id=-1009876543): scanned=5, recovered=0
  Errored Chat (id=-1001111111): ERROR — connection refused
  total: 3 chat(s), 1 recovered, 1 errored
```

**Always exits 0** — even on partial failure. This is the best-effort contract: the `/update` orchestrator ignores the exit code.

## `/update` Final-Step Integration

`run_catchup_step` in `scripts/update/run.py` runs as the **strictly last** step of `run_update`, after every service-management action and health check.

### Health Gate

Invoked only when BOTH `service.get_service_status(...).running` AND `service.get_worker_status(...).running` are true. If either is down, the step logs `catchup: skipped — ...` and returns. Also gated on `config.do_service_restart`, so verify-only and follower-skip runs never trigger recovery enqueues.

### Subprocess + Tight Timeout

`valor-catchup` runs as a subprocess (clean isolation, killable on expiry) with a `CATCHUP_STEP_TIMEOUT_SECONDS = 90` ceiling. A hung Telethon connect or stalled LLM call is killed on expiry and never stalls `/update`.

### Failure Swallowed

Any failure, non-zero exit, or timeout is logged (`catchup: ... (swallowed)`) and swallowed. `run_catchup_step` never raises and never flips `UpdateResult.success`. `/update` completion is wholly independent of `valor-catchup`'s outcome.

## Interaction with Mechanical Scanners

`valor-catchup` runs *after* `/update`'s restart, which already fires the mechanical catchup. The ordering boundary:

1. `/update` restarts bridge + worker → bridge fires `scan_for_missed_messages` (ingestion gaps claimed, dedup written).
2. `/update` health checks confirm both services running.
3. `run_catchup_step` → `valor-catchup` sweeps owned chats (response failures).

The mechanical layer has already claimed all ingestion-gap messages and written dedup. The agent-judgment layer only acts on messages that DID get a session but no reply, which the mechanical layer structurally cannot detect.

## Dedup TTL Contract & Rollout Seed

This section describes the fix for #2204 (re-handling of already-answered
messages) and applies to **startup catchup (`bridge/catchup.py`) only** — see
the scope note in [Message Reconciler](message-reconciler.md#scope-note-the-re-handling-bug-2204-never-touched-the-reconciler)
for why the reconciler's fixed 30-minute lookback was never a bug site.

### Unified dedup TTL contract

`DedupRecord` (`models/dedup.py`) is now the single authoritative "already
dispatched" record over the **entire** startup-catchup scan window, not just a
short fixed period. Its TTL is settings-backed
(`config.settings.timeouts.dedup_record_ttl_s`, env
`TIMEOUTS__DEDUP_RECORD_TTL_S`) and defaults to `last_processed_ttl_s` (~30
days) by design: `LastProcessedRecord`'s cursor determines the maximum
startup-catchup lookback (issue #1408's per-chat cutoff extension can reach
back to the cursor's own age), so the dedup membership set must remember every
dispatched message for that entire window. A shorter TTL here reopens the
re-handling bug — a message answered (by reply, non-reply message, emoji
reaction, or deliberate no-reply judgment) more than the TTL ago ages out of
dedup and gets treated as never-handled on the next restart.

The old reply-only heuristic (`_check_if_handled` in `bridge/catchup.py`,
which fetched the 10 messages after a candidate and matched only an explicit
threaded reply) is **deleted**. It is dead weight now that guard 1 (the dedup
set) is authoritative over the full window — dedup is written at *dispatch*
time regardless of how a message was eventually answered, so it structurally
covers every answer type the heuristic tried and failed to special-case. This
also unifies startup catchup with the reconciler, which never had this
heuristic.

### One-time rollout dedup-seed

Deleting the old TTL is a rollout hazard on its own: the old 2h TTL had
already deleted every dedup key for messages handled more than 2 hours before
the fix shipped. With the reply-only heuristic gone and the dedup TTL now
long, the very first post-fix startup catchup scan (with its up-to-30-day
lookback) would find those handled-but-forgotten messages absent from dedup
and re-enqueue the entire historical backlog — the exact duplicate-reply storm
this fix exists to prevent, fired once at rollout. An `EXPIRE`-refresh
migration cannot help; the keys are already deleted, nothing to refresh.

`bridge/dedup_seed.py` closes this gap with a one-time seeding pass that runs
during bridge startup, **before** `scan_for_missed_messages` and before the
live `NewMessage` handler begins dispatching (`bridge/telegram_bridge.py`, run
and awaited to completion). For each monitored/owned chat, it fetches the most
recent `MAX_MESSAGES_PER_CHAT` messages via a live Telethon read and writes a
`DedupRecord` entry for every inbound message whose id is `<=` that chat's
`LastProcessedRecord` cursor id — i.e., messages the cursor already advanced
past, and therefore messages that were demonstrably already dispatched. This
scopes the seed to messages provably handled rather than blanket-seeding the
whole window, so a genuine gap message *above* the cursor is never suppressed.
Running it before live dispatch begins also avoids a lost-update race on
`DedupRecord.add_message` (a read-modify-write, not an atomic `SADD`) between
the seed and a concurrent live write.

### Per-chat seed markers

The seed is guarded by a marker file **per chat** — `data/dedup-seeded.{chat_id}`
— written only after that chat's seed fully succeeds. It is deliberately never
a single global marker. A global marker would let a partial per-chat Telethon
failure (rate limit, transient error) finish the overall pass and still stamp
"done," permanently skipping that one chat's seed on every future restart —
that chat would then re-enqueue its entire aged-out handled backlog on the
first post-fix scan, silently reproducing the duplicate-reply storm with no
recovery path. Per-chat markers make the seed self-healing instead: an
unmarked chat (because it failed, or is newly added) simply re-seeds on the
next restart, while already-seeded chats are skipped without a Telethon read.
A chat with no `LastProcessedRecord` cursor yet seeds nothing but is still
marked done (a legitimate empty outcome, not a failure).

### Observability & Rollback

Because the original failure mode was silent (a duplicate reply reaching the
human with no error raised), the rollout carries an explicit detection signal
and a fast revert path:

- **Seed pass**: logs one structured `[dedup-seed] chat=... title=... seeded=N
  marker_written=True|False` INFO line per chat on completion, so a
  partial-failure chat is visible in `logs/bridge.log` instead of silently
  skipped.
- **Startup catchup**: logs one structured `[catchup] Scan decision counters:
  re_enqueued=N skipped_duplicate=N` INFO line per scan.
- **Reconciler**: logs the same `[reconciler] Scan decision counters:
  re_enqueued=N skipped_duplicate=N` line per scan, as a regression guard.

A post-rollout spike in `re_enqueued` for historical (pre-restart) message ids
is greppable in `logs/bridge.log` and is the signal to watch for a recurrence.

**Rollback lever**: `CATCHUP_DISABLED_FLAG` (`bridge/catchup.py`,
`data/catchup-disabled`) is the operator kill switch for the entire
recovery layer (startup catchup, reconciler, and `valor-catchup`). If
duplicate replies reappear after re-enabling recovery:

1. `touch data/catchup-disabled` on the affected machine — disables the
   entire recovery layer again within one scan cycle.
2. `./scripts/valor-service.sh restart` so the flag takes effect on the
   running process.
3. The per-chat seed markers, the longer dedup TTL, and the deleted
   `_check_if_handled` heuristic are all safe to leave in place while
   investigating — only the kill switch needs to be re-touched. Check the
   offending chat's `[dedup-seed]` log line before re-removing the flag.

## Error Handling

All errors are narrowly scoped:

- **Per-message failure** (judge call, enqueue): logs a greppable `[agent-catchup]` WARNING, continues to next message.
- **Per-chat failure** (thread read, unhandled exception in `sweep_chat`): logs a greppable WARNING, appends a `ChatResult` with `errored=True`, continues to next chat. Errored chats appear in the CLI summary.
- **Top-level failure** (aborted sweep): logs WARNING, prints abort message, returns 0.

Filter all sweep diagnostics with:
```bash
grep '\[agent-catchup\]' logs/bridge.log
```

## Data Flow

```
valor-catchup (CLI) or run_catchup_step (/update final step)
    |
    +-- resolve_owned_chats()
    |   -- get_dialogs() → filter by ALL_MONITORED_GROUPS → find_project_for_chat()
    |
    +-- for each OwnedChat:
    |   read_thread()
    |   -- client.get_messages(limit=20) → filter to LOOKBACK_HOURS
    |   -- m.out == True → Valor (ground truth)
    |   -- oldest-first for judge transcript
    |
    |   for each inbound human message (non-Valor, non-empty):
    |       judge_message(transcript, text, message_id)
    |       -- agent.llm.run_typed (Haiku) → ANSWERED on any failure
    |
    |       if UNANSWERED_NEEDS_REPLY:
    |           _has_valor_reply_after(thread, message_id)?  → skip (snapshot guard)
    |           _valor_replied_since(client, entity, id)?   → skip (pre-enqueue re-read)
    |           resolve_persona() → persona_to_session_type()
    |           enqueue_agent_session(message_text=inbound.text)
    |           record_message_processed() + record_last_processed()
    |
    +-- print format_summary(results)  → exits 0 always
    |
    (worker picks up recovery sessions → normal relay → outbox → reply delivered)
```

## Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `judge_message` | `bridge/agent_catchup.py` | Classify one inbound message; returns verdict; never raises |
| `sweep_chat` | `bridge/agent_catchup.py` | Judge one chat's recent thread; enqueue genuine misses |
| `run_sweep` | `bridge/agent_catchup.py` | Sweep all owned chats; best-effort, never aborts |
| `read_thread` | `bridge/agent_catchup.py` | Bounded thread read including Valor `out` replies |
| `resolve_owned_chats` | `bridge/agent_catchup.py` | Map live dialogs to owned chats via owner-scoping globals |
| `_has_valor_reply_after` | `bridge/agent_catchup.py` | Snapshot double-reply guard (position-based) |
| `_valor_replied_since` | `bridge/agent_catchup.py` | Pre-enqueue re-read (Race 1 mitigation) |
| `_valor_reacted` | `bridge/agent_catchup.py` | Detect Valor's own emoji reaction on a message via `chosen_order` (reaction awareness) |
| `_enqueue_recovery` | `bridge/agent_catchup.py` | Enqueue one recovery session + dedup write |
| `main` | `bridge/agent_catchup.py` | `valor-catchup` CLI entry point; always exits 0 |
| `run_catchup_step` | `scripts/update/run.py` | Best-effort `/update` final step wrapper |
| `seed_dedup_for_chats` | `bridge/dedup_seed.py` | One-time per-chat dedup-seed pass; runs at bridge startup before the catchup scan |
| `seed_dedup_for_chat` | `bridge/dedup_seed.py` | Seed one chat's dedup set from a live Telethon read; writes that chat's marker only on full success |
| `is_chat_seeded` | `bridge/dedup_seed.py` | Check a chat's `data/dedup-seeded.{chat_id}` marker |
| `catchup_disabled` | `bridge/catchup.py` | Operator kill switch check (`data/catchup-disabled`) |

## See Also

- [Bridge/Worker Architecture](bridge-worker-architecture.md) — catchup/reconciler overview and bridge/worker process separation
- [Message Reconciler](message-reconciler.md) — periodic ingestion-gap scanner (complement to this layer; see its scope note on why the #2204 re-handling bug never touched it)
- [Single-Machine Ownership](single-machine-ownership.md) — owner-scoping invariant reused by this layer
- [Headless Session Runner](headless-session-runner.md) — production session runner; its predecessor's startup failures motivated this feature (see the [PTY-fragility postmortem](../postmortems/2026-07-06-granite-pty-fragility.md))
