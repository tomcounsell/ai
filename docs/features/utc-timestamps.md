# UTC Timestamp Normalization

All timestamps in the system are stored and logged as **tz-aware UTC datetimes**. Conversion to local time happens only at the display boundary (e.g., formatting timestamps for Telegram messages to humans).

## Why UTC Everywhere

- **Log correlation**: Telethon reports message timestamps in UTC. Bridge, agent, and monitoring logs now match, eliminating mental timezone conversion during incident investigation.
- **Cross-machine consistency**: Deployed instances in different timezones produce identical timestamp formats.
- **No naive datetimes**: Every `datetime` object in the system carries timezone info, preventing `TypeError` from mixed naive/aware comparisons.

## The `bridge/utc` Module

Central utilities for timestamp handling:

```python
from bridge.utc import utc_now, to_local, utc_iso, to_unix_ts
```

### `utc_now() -> datetime`

Returns the current time as a tz-aware UTC datetime. Drop-in replacement for `datetime.now()`.

### `to_local(ts: datetime) -> datetime`

Converts a tz-aware UTC datetime to the machine's local timezone for display. Raises `ValueError` if given a naive datetime (catches missed conversions early).

### `utc_iso() -> str`

Returns the current UTC time as an ISO 8601 string with `Z` suffix (e.g., `2026-03-26T14:30:00Z`). Convenience for JSON serialization.

### `to_unix_ts(val) -> float | None`

Canonical datetime → Unix timestamp converter for **read-path** code. Accepts `datetime` (naive or aware), `int`/`float`, ISO 8601 strings (with or without `Z`), or `None`.

Naive datetimes are treated as UTC before `.timestamp()` is called. This is the defense against the most common bug in age math: calling `val.timestamp()` directly on a naive datetime returns a value offset by the machine's UTC offset (e.g., +420 min on UTC+7), because Python interprets naive datetimes as local time.

Always use this helper when reading a Popoto-stored datetime and comparing it to `time.time()` or `utc_now().timestamp()`. Returns `None` when the input cannot be coerced — callers handle `None` explicitly.

## JSON Log Format

The `StructuredJsonFormatter` in `bridge/log_format.py` emits UTC timestamps with a `Z` suffix and includes `"utc": true` as an explicit marker:

```json
{
  "timestamp": "2026-03-26T14:30:00.123456Z",
  "utc": true,
  "level": "INFO",
  "logger": "bridge.telegram_bridge",
  "message": "Message processed"
}
```

## Display Layer

Display surfaces fall into two categories:

**Conversational output** (Telegram messages, UI relative times): convert to local time with `to_local()`:

```python
from bridge.utc import utc_now, to_local

ts = utc_now()  # Store this
display = to_local(ts).strftime("%H:%M")  # Show this to humans
```

**CLI and log output** (operator-facing tools): display UTC explicitly with a ` UTC` label so operators can safely compare timestamps from different sources without timezone confusion:

```
# python -m tools.valor_session status
Created:  2026-04-07 05:49:00 UTC

# logs/worker.log
2026-04-07 13:03:54 UTC worker INFO ...
```

The CLI uses `_format_ts()` in `tools/valor_session.py` (appends ` UTC` to all outputs). The worker uses `_UTCFormatter` with `converter = time.gmtime` so log lines always reflect UTC regardless of the machine's local timezone.

## Migration Notes

- All `datetime.now()` calls in `bridge/`, `agent/`, `monitoring/`, `scripts/`, `tools/`, and `ui/` have been replaced with `utc_now()` or `utc_iso()`.
- The deprecated `datetime.utcnow()` (which returns naive datetimes) has been eliminated.
- `time.time()` calls are unchanged -- epoch timestamps are timezone-neutral.
- Telethon message timestamps were already UTC and are unchanged.
- Popoto/Redis model timestamps (`created_at`/`updated_at`) are managed by the ORM and are out of scope for construction-site normalization. However, `SortedField` / `DatetimeField` deserialization can return naive datetimes — code that calls `.timestamp()` on values read from Popoto must treat them as UTC. **Use `bridge.utc.to_unix_ts(val)` for all read-path conversions.** It normalizes naive datetimes to UTC before `.timestamp()`, handles `None`/float/ISO-string inputs, and is the single source of truth for this coercion. Rewire sites include `scripts/update/run.py::_cleanup_stale_sessions`, `tools/agent_session_scheduler._to_ts`, `agent/sustainability`, `reflections/memory_management`, `reflections/behavioral_learning`, `tools/telegram_history._parse_ts`, and `models.agent_session.cleanup_expired`. Three older helpers (`monitoring/session_watchdog._to_timestamp`, `agent/session_health._to_ts`, `ui/data/sdlc._safe_float`) already implement the same `val.tzinfo is None` guard inline — they pre-date the shared helper and are intentionally left untouched. New code must import `to_unix_ts` rather than add a fourth copy (issues #777, hotfix 9e3a64f5).

## Related

- Issue: [#542](https://github.com/tomcounsell/ai/issues/542) — UTC normalization (internal storage)
- Issue: [#792](https://github.com/tomcounsell/ai/issues/792) — Timestamp display labels (CLI/log surfaces)
- Plan: `docs/plans/542-utc-timestamp-normalization.md`
- Plan: `docs/plans/timestamp-timezone-labels.md`
