# Pipeline Dead Letters

## Overview

Every place work leaves the pipeline without being delivered writes a
`DeadLetter` row naming the stage it left from. One model, one dashboard tile,
one replay reflection — so "what was lost yesterday, and can it be replayed?"
has a single answer.

Before this, the system had four terminal sinks and one replayer. The Telegram
relay dead-lettered into a Telegram-shaped model; the outbox reader discarded a
malformed entry with a warning; the session archive quarantined poison rows into
its own SQLite table; a session finalized at its recovery cap kept no replayable
input at all.

## The model

`models/dead_letter.py`. The row is TERMINAL: it records what was lost and,
where replay is meaningful, what to re-send. Live retry state (an attempt
schedule, a next-attempt time) belongs on
[`SideEffectJob`](side-effect-jobs.md) instead.

| Field | Purpose |
|---|---|
| `stage` | Which sink wrote it (`IndexedField`) |
| `payload_json` | The lost payload; a raw string when that is what failed to parse |
| `reason` | Why it died, in the writer's own words |
| `replayable` | Whether the stage's handler can meaningfully re-run it |
| `first_seen` / `last_seen` | Written, and last touched by a replay |
| `attempts` | Replay attempts spent, seeded from whatever counter the writer exhausted |
| `chat_id` / `reply_to` / `text` | The Telegram send payload, in their original columns |

`Meta.ttl` is 30 days.

## Stages

| Stage | Written by | Replayable |
|---|---|---|
| `telegram_send` | Relay retry cap (`bridge/telegram_relay.py`) | yes |
| `outbox_parse` | Malformed JSON or an unknown `type` in the Telegram or email outbox | no |
| `steering_parse` | A steering entry that failed validation on a destructive drain | no |
| `notify_parse` | A session-notify payload the listener could not parse | no |
| `email_send` | Email relay terminal failure | yes |
| `extraction` | A `SideEffectJob` that exhausted its attempts | yes |
| `session_recovery_cap` | A session finalized at `MAX_RECOVERY_ATTEMPTS` | yes |
| `session_init_hang` | A session whose runner produced zero output | no |
| `session_corrupt_row` | The corrupted-pop reaper, before it deletes the row | no |
| `archive_restore` | The session archive's quarantine cap — observability mirror | no |
| `improve_intent` | Reserved for the improvement control plane (#3177) | — |

`session_init_hang` is deliberately not replayable: the #2181 circuit breaker
exists because re-spawning that identical zero-output input reproduces the
identical hang. The row is evidence for a human, never something to auto-retry.

`archive_restore` mirrors only. `agent/session_archive.py`'s
`_restore_quarantine` SQLite table stays authoritative — it is the cold-start
skip list AND the per-row attempt counter, and it lives on disk precisely to
survive an emptied Redis, which is the event the archive exists to recover from.
The mirror is wrapped so a Redis failure can never abort the SQLite transaction.

## Replay

`bridge/dead_letters.py` holds a per-stage handler registry. The
`dead-letter-replay` reflection (300s) walks the replayable rows of every stage
that has a handler, hands each to it, and deletes the row on success. A handler
that raises bumps `attempts`; at `MAX_REPLAY_ATTEMPTS` (3) `replayable` flips
false and the row ages out on its TTL.

`telegram_send` is not in the registry: its replay needs a live Telethon client,
so the bridge connect sequence drives it as an eager first pass on every
restart.

Manual replay for an operator reading the dashboard tile:

```bash
python -m tools.dead_letters replay --stage extraction
```

It is a rare, destructive-adjacent break-glass (it re-sends messages that
already failed), so it is a `python -m` operator tool with no MCP registration.
The dashboard tile is the agent-visible surface.

## Bounding the volume

A misbehaving relay could otherwise fill Redis, so each stage is capped at
`DEAD_LETTER_STAGE_CAP` (10,000) rows. The cap costs O(1) on the write path,
which matters because `record()` runs on the relay's per-message failure branch:
under the flood the cap exists for, a census would make the mitigation the
bottleneck.

Two plain (non-Popoto) keys, both through `utils.redis_client.text_redis()`:

- `{project}:dead_letters:count` — a hash of stage → count, bumped once per
  `record()`.
- `{project}:dead_letters:{stage}` — a sorted set of `letter_id` scored by
  first-seen epoch.

Eviction runs in the replay reflection, never in `record()`. The counter is
advisory: TTL expiry removes a row without touching it, so the eviction pass
reconciles it from the index each time rather than trusting it. The sorted set
is an eviction *index* over ids; rows themselves are only ever removed through
the ORM.

## Dashboard

`/_partials/pipeline-integrity/` renders dead-letter counts by stage beside the
[lock degradation counts](bridge-worker-architecture.md#redis-pop-lock). Every
stage renders, including the ones at zero — a stage that vanishes from the tile
reads as a stage nobody watches.

## Files

| File | Role |
|---|---|
| `models/dead_letter.py` | The row |
| `bridge/dead_letters.py` | `record`, the handler registry, replay, eviction |
| `reflections/housekeeping/dead_letter_replay.py` | The 300s replay + eviction pass |
| `ui/data/dead_letters.py` | Dashboard counts |
| `scripts/update/migrations.py` | `dead_letter_stage_backfill` |

## Related

- [Side-Effect Jobs](side-effect-jobs.md) — the live-state counterpart
- [Wire Schemas](wire-schemas.md) — what produces the `*_parse` stages
- [Bridge/Worker Architecture](bridge-worker-architecture.md)
