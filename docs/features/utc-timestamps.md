# UTC Timestamp Normalization

All timestamps in the system are stored and logged as **tz-aware UTC datetimes**. Conversion to local time happens only at the display boundary (e.g., formatting timestamps for Telegram messages to humans).

## Why UTC Everywhere

- **Log correlation**: Telethon reports message timestamps in UTC. Bridge, agent, and monitoring logs now match, eliminating mental timezone conversion during incident investigation.
- **Cross-machine consistency**: Deployed instances in different timezones produce identical timestamp formats.
- **No naive datetimes**: Every `datetime` object in the system carries timezone info, preventing `TypeError` from mixed naive/aware comparisons.

## The `bridge/utc` Module

Central utilities for timestamp handling:

```python
from bridge.utc import utc_now, to_local, utc_iso
```

### `utc_now() -> datetime`

Returns the current time as a tz-aware UTC datetime. Drop-in replacement for `datetime.now()`.

### `to_local(ts: datetime) -> datetime`

Converts a tz-aware UTC datetime to the machine's local timezone for display. Raises `ValueError` if given a naive datetime (catches missed conversions early).

### `utc_iso() -> str`

Returns the current UTC time as an ISO 8601 string with `Z` suffix (e.g., `2026-03-26T14:30:00Z`). Convenience for JSON serialization.

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

When presenting timestamps to humans (Telegram messages, UI), use `to_local()`:

```python
from bridge.utc import utc_now, to_local

ts = utc_now()  # Store this
display = to_local(ts).strftime("%H:%M")  # Show this to humans
```

The principle: **store UTC, display local -- never the reverse**.

## Migration Notes

- All `datetime.now()` calls in `bridge/`, `agent/`, `monitoring/`, `scripts/`, `tools/`, and `ui/` have been replaced with `utc_now()` or `utc_iso()`.
- The deprecated `datetime.utcnow()` (which returns naive datetimes) has been eliminated.
- `time.time()` calls are unchanged -- epoch timestamps are timezone-neutral.
- Telethon message timestamps were already UTC and are unchanged.
- Popoto/Redis model timestamps (`created_at`/`updated_at`) are managed by the ORM and are out of scope.

## Related

- Issue: [#542](https://github.com/tomcounsell/ai/issues/542)
- Plan: `docs/plans/542-utc-timestamp-normalization.md`
