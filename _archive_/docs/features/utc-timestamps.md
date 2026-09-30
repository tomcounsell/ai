# UTC Timestamp Normalization

All timestamps in the system are stored and logged as **tz-aware UTC datetimes**. Conversion to local time happens only at the display boundary (e.g., formatting timestamps for Telegram messages to humans).

## Why UTC Everywhere

- **Log correlation**: Telethon reports message timestamps in UTC. Bridge, agent, and monitoring logs now match, eliminating mental timezone conversion during incident investigation.
- **Cross-machine consistency**: Deployed instances in different timezones produce identical timestamp formats.
- **No naive datetimes**: Every `datetime` object in the system carries timezone info, preventing `TypeError` from mixed naive/aware comparisons.

## The `utils/utc` Module

Central utilities for timestamp handling:

```python
from utils.utc import utc_now, to_local, utc_iso, to_unix_ts
```

The module lives in `utils/` because it is dependency-free (stdlib `datetime` only) and standalone `tools/` packages import it, so it must not chain them to the harness (#2867).

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
from utils.utc import utc_now, to_local

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
- `AgentSession.updated_at` is explicitly UTC-stamped in `AgentSession.save()` (issue #1645 — Popoto's `auto_now=True` historically minted naive local wall-clock time). `created_at` and other `DatetimeField` timestamps are set explicitly at write time using `utc_now()`. As of **popoto >= 1.7.1** (issue #1653, popoto#421) `auto_now`/`auto_now_add` now stamp `datetime.now(timezone.utc)`, so the upstream producer bug is fixed and `auto_now` is safe to use. We nonetheless keep the explicit `utc_now()` stamp in `AgentSession.save()` for now: it also covers save paths that bypass `format_value_pre_save` (e.g. raw/pipeline updates). Removing the override is gated on confirming `auto_now` fires on every save path — until then, prefer explicit `utc_now()` stamps on `AgentSession`.
- popoto >= 1.9.0 (popoto#537) decodes even a legacy offset-free row as aware UTC, and a `SortedField`'s score has been a pure function of the stored value since popoto#519/1.8.2 regardless of tzinfo — but ISO strings, floats, and other non-popoto producers can still hand a read-path helper a naive datetime. **Use `utils.utc.to_unix_ts(val)` for all read-path conversions.** It normalizes naive datetimes to UTC before `.timestamp()`, handles `None`/float/ISO-string inputs, and is the single source of truth for this coercion. Rewire sites include `scripts/update/run.py::_cleanup_stale_sessions`, `tools/agent_session_scheduler._to_ts`, `reflections/agents/{failure_loop_detector,session_recovery_drip,session_count_throttle}.py`, `reflections/memory_management`, `tools/telegram_history._parse_ts`, and `models.agent_session.cleanup_expired`. Issue #3181 classified every remaining naive-tzinfo guard outside `models/job.py` (settled by #3173) against this rule: **delete** where popoto is provably the only inbound source, **keep** where inputs are genuinely mixed. Four popoto-only guards were deleted (`models/agent_session.py::_heal_future_updated_at`, `reflections/pm_briefings/daily_log.py::_collect_sessions`, `reflections/crash_recovery.py`'s resumable-session filter, and a dead `isinstance(_ua, datetime)` branch in `reflections/audits/redis_quality_audit.py` — `Chat.updated_at` is `SortedField(type=float)`, never a datetime). The rest — including the older helpers `monitoring/session_watchdog._to_timestamp`, `agent/session_health._ts`, and `ui/data/sdlc._safe_float`, plus `to_unix_ts` itself — **keep** their inline `val.tzinfo is None` guard: every one of them accepts an ISO string, a raw-Redis or file-read value, an epoch float, or a `datetime` from a non-popoto producer, not exclusively a popoto read. New code must import `to_unix_ts` rather than add a fourth copy (issues #777, hotfix 9e3a64f5).

### The two escape hatches this contract depends on

- **`Defaults.DATETIME_KEY_LEGACY` must stay falsy.** popoto 1.9.0 decodes both the modern (`isoformat()`) and the pre-#521 legacy stored shape as aware UTC — but the legacy re-attach is gated on this class attribute (`popoto/models/encoding.py`, `popoto/fields/constants.py:387`), which is directly assignable at runtime and not only via its normal `POPOTO_DATETIME_KEY_LEGACY` env-var setter. Neither is set in this repo or in `.env.example`. If either is ever set fleet-wide, legacy rows decode naive again and all four deleted guards become load-bearing at once.
- **A deliberately-naive write still round-trips naive.** popoto's forward encoder writes `obj.isoformat()`, which for a naive value renders without the anchored legacy shape, so it decodes back through `fromisoformat` and stays naive. The four deletions above rely on every current writer producing an aware value or a float (see the writer-audit Verification rows in `docs/plans/naive-tzinfo-guard-classification.md`), not on popoto rejecting a naive write outright. A future writer that constructs a naive `datetime` and assigns it as a constructor kwarg — bypassing `AgentSession.__setattr__`, which only coerces `_DATETIME_FIELDS` — would land naive with nothing to catch it. `created_at` (`SortedField`, outside `_DATETIME_FIELDS`) has no `__setattr__` coercion at all, which is why `models/agent_session.py::log_lifecycle_transition`'s `created_at` fallback keeps its guard.

## Related

- Issue: [#542](https://github.com/tomcounsell/ai/issues/542) — UTC normalization (internal storage)
- Issue: [#792](https://github.com/tomcounsell/ai/issues/792) — Timestamp display labels (CLI/log surfaces)
- Issue: [#1645](https://github.com/tomcounsell/ai/issues/1645) — Fix `AgentSession.updated_at` producer bug (`auto_now` minted naive-local time)
- Issue: [#1653](https://github.com/tomcounsell/ai/issues/1653) — Upstream popoto fix for `auto_now` UTC, shipped in popoto 1.7.1 (popoto#421); ai pins `popoto>=1.9.0`, which retains the 1.7.1 auto_now UTC fix
- Plan: `docs/plans/542-utc-timestamp-normalization.md`
- Plan: `docs/plans/timestamp-timezone-labels.md`
