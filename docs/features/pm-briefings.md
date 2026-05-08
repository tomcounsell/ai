# PM Briefings (slot-driven)

One reflection — `pm-briefings` in the registry, callable
`reflections.pm_briefings.run` — owns ALL PM-facing slot-driven daily
content. Each project declares zero-or-more **briefing slots** in
`projects.json`; at every 5-minute tick the dispatcher fans out
(project × slot), runs the slot-specific `build()`, and delivers ONE
Telegram message per (project × slot) per day.

The dispatcher exposes three slot types under a single entry point:

| Slot type   | Output         | Default `skip_when_empty` |
|-------------|----------------|---------------------------|
| `morning`   | voice + text   | from `pm_briefing.skip_when_empty` |
| `daily_log` | voice + text   | True                      |
| `log_audit` | text only      | True                      |

## Why this exists

Multiple PM-facing reflections meant the same project owner could receive
2–3 Telegram deliveries per day per project, none of which respected each
project's local timezone or owner machine. The slot-driven dispatch model
unifies machine-ownership gating, SETNX idempotency, per-(project × slot)
Reflection records, and skip-when-empty silence into one code path.

## Slot types

| `type`      | Module path                              | Output       |
|-------------|------------------------------------------|--------------|
| `morning`   | `reflections.pm_briefings.morning`       | voice + text |
| `daily_log` | `reflections.pm_briefings.daily_log`     | voice + text |
| `log_audit` | `reflections.pm_briefings.log_audit`     | text only    |

Each slot's `build(project, slot_config)` returns
`(transcript, followup_markdown, raw_signals)`. Slot builders are pure —
they do NOT touch Redis, Telegram, or Reflection state. The dispatcher
(`reflections.pm_briefings.run` in `__init__.py`) owns lock acquire,
delivery, and the per-record `mark_completed()` call.

## Configuration

```json
"pm_briefing": {
  "enabled": true,
  "timezone": "America/Los_Angeles",
  "target_groups": ["PM: My Project"],
  "slots": [
    {
      "name": "morning",
      "type": "morning",
      "schedule": "08:00",
      "angles": {"include": ["merges", "open-bugs"]}
    },
    {
      "name": "evening_recap",
      "type": "daily_log",
      "schedule": "18:30",
      "vault_writer": true
    },
    {
      "name": "log_audit",
      "type": "log_audit",
      "schedule": "23:00",
      "target_groups": ["Dev: Valor"]
    }
  ]
}
```

A slot dict supports:

- `name` (required) — unique identifier within the project
- `type` (required) — one of the slot types above
- `schedule` (required) — `HH:MM` in the project's timezone; matches a
  5-minute window starting at that minute
- `target_groups` (optional) — Telegram groups to deliver to; falls back
  to `pm_briefing.target_groups`
- `voice` (optional) — TTS voice override
- `vault_writer` (optional, only for `daily_log`) — default `False`. Only
  ONE slot across all (machine × project) should set this to `True` to
  avoid iCloud conflict-copy races. The single-machine-ownership invariant
  ensures one machine owns this flag.
- `skip_when_empty` (optional) — default `True` for `daily_log` and
  `log_audit`; default `False` for `morning` (so the morning user always
  gets a message even on a quiet day, modulo the
  `pm_briefing.skip_when_empty` override).
- `angles` (optional, only for `morning`) — falls back to
  `pm_briefing.angles`.
- `fallback_message` (optional) — used when `skip_when_empty` is `False`
  and the collector returned nothing.

A project with `pm_briefing.enabled=true` but no `slots` (or an empty
list) is logged at warning level and skipped — operators must opt in
explicitly per slot.

## Lock-release policy (split between dispatcher and slot)

The dispatcher owns the SETNX lifecycle:

1. **Pre-dispatch**: SETNX `pm-briefings-lock:{slug}:{slot}:{today_iso}`
   with a 25-hour TTL. Skip if already held.
2. **Dispatch**: call `slot.build(project, slot_config)` — pure, no side
   effects.
3. **Post-build**: if both `transcript` and `followup` are empty AND
   `skip_when_empty` was honored, mark the per-(project × slot) Reflection
   record completed and return `status="noop"`. The lock stays held — the
   next tick within the same day won't retry.
4. **Side-effects**: enqueue Telegram payload(s). After this point any
   exception is "post-side-effect": the Reflection record is marked
   completed with the error AND the lock is HELD for the rest of the day
   (preventing duplicate deliveries on the next tick).
5. **Pre-side-effect failure**: builder raises before any enqueue ⇒ lock
   is RELEASED so the next tick can retry.

## Dashboard rendering

The `_PREFIX_EXPANDED_REFLECTIONS` tuple in `ui/data/reflections.py`
carries `pm-briefings`, the prefix for per-(project × slot) records named
`pm-briefings-{slug}-{slot}`. Records under that prefix surface as
per-record rows under the parent registry entry's group on the dashboard.

## Aggregate result shape

`run()` returns:

```python
{
    "status": "ok" | "partial" | "error",
    "projects": [
        {"slug": "alpha", "slot": "morning", "status": "ok",
         "duration": 1.42, "findings_count": 0, "error": None,
         "date_iso": "2026-04-30"},
        ...
    ],
    "results": {"alpha:morning": {...}, ...},
    "summary": {"considered": 1, "succeeded": 2, "failed": 0},
}
```

The per-record `date_iso` field is included so a Tuesday-LA row and a
Wednesday-LA row don't overwrite each other when projects span timezones.
`duration` is the wall-clock seconds spent in `_run_slot` for this
(project × slot). `findings_count` is the number of items in the slot's
`raw_signals["findings"]` (currently the `log_audit` slot — `morning` and
`daily_log` always report 0).

`summary.succeeded + summary.failed` may be less than `considered * slots`
because schedule-miss / lock-held / already-succeeded slots are reported
as `status: "skipped"` and intentionally excluded from both counters
(they aren't run-attempts).

## Rollback

If the dispatcher misbehaves, the rollback path is:

```bash
git revert <merge-sha>
./scripts/valor-service.sh worker-restart
```

The worker reload picks up the registry entry changes. No DB cleanup is
needed because per-(project × slot) Reflection records are additive — old
records remain in Redis and surface on the dashboard until they age out.

## See also

- `docs/features/reflections.md` — top-level reflections index
- `docs/features/single-machine-ownership.md` — vault-writer ownership
  invariant
