"""Reflection output sink delivery.

Routes a completed reflection's output to the configured sink. Sink kinds:

- ``log_only`` (default): log at INFO with reflection name + truncated output.
- ``dashboard_only``: persist a 500-char digest to ``ReflectionRun.output_summary``.
- ``memory:<importance>``: write a Memory record at the given importance
  (defaults to 5.0 if omitted, e.g. bare ``memory:``).
- ``telegram:<chat>``: resolve ``<chat>`` against ``projects.json`` and push a
  payload onto the bridge's Redis outbox queue.

Per Q5 of docs/plans/unify-recurring-tasks-into-reflections.md, every handler
swallows its own exceptions and emits a WARNING log on failure — sink delivery
must never crash the scheduler. On Telegram resolve / push failure, the
``ReflectionRun.delivery_error`` field is populated for dashboard surfacing,
but the run itself remains ``status="success"`` (the work happened).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from models.reflection import Reflection
from models.reflection_run import ReflectionRun

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point


def deliver(reflection: Reflection, run: ReflectionRun, output: Any) -> None:
    """Route ``output`` to the sink configured on ``reflection.output_sink``.

    Never raises — handler exceptions are caught and logged at WARNING with
    the reflection name and sink kind.
    """
    sink = (reflection.output_sink or "log_only").strip()
    name = reflection.name or "<unnamed>"

    try:
        if sink == "log_only" or sink == "":
            _deliver_log_only(name, output)
        elif sink == "dashboard_only":
            _deliver_dashboard_only(run, output)
        elif sink.startswith("memory:") or sink == "memory":
            _deliver_memory(name, sink, output)
        elif sink.startswith("telegram:"):
            _deliver_telegram(reflection, run, sink, output)
        else:
            logger.warning(
                "Reflection %s has unknown output_sink %r; falling back to log_only.",
                name,
                sink,
            )
            _deliver_log_only(name, output)
    except Exception as exc:  # pragma: no cover - defensive backstop
        logger.warning(
            "Reflection %s output_sink=%r delivery raised %r; swallowed.",
            name,
            sink,
            exc,
        )


# ---------------------------------------------------------------------------
# Sink handlers


def _truncate(value: Any, limit: int) -> str:
    text = str(value) if value is not None else ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _deliver_log_only(name: str, output: Any) -> None:
    logger.info("[reflection %s] %s", name, _truncate(output, 500))


def _deliver_dashboard_only(run: ReflectionRun, output: Any) -> None:
    try:
        run.output_summary = _truncate(output, 500)
        run.save()
    except Exception as exc:
        logger.warning(
            "Reflection run %s dashboard_only sink failed: %r",
            getattr(run, "name", "<unknown>"),
            exc,
        )


def _parse_memory_importance(sink: str) -> float:
    """Parse ``memory:<importance>`` -> float, defaulting to 5.0."""
    if sink == "memory" or sink == "memory:":
        return 5.0
    _, _, raw = sink.partition(":")
    raw = raw.strip()
    if not raw:
        return 5.0
    try:
        return float(raw)
    except ValueError:
        logger.warning("memory: sink importance %r is not a float; defaulting to 5.0.", raw)
        return 5.0


def _deliver_memory(name: str, sink: str, output: Any) -> None:
    importance = _parse_memory_importance(sink)
    digest = f"[reflection {name}] {_truncate(output, 1500)}"
    try:
        from models.memory import Memory

        # Prefer safe_save which handles WriteFilterMixin gracefully and never
        # raises. Category lives in ``metadata`` per Memory's contract; we also
        # surface a top-level ``category`` kwarg in case the underlying model
        # accepts it (matches existing usage in models/reflection.py).
        Memory.safe_save(
            content=digest,
            importance=importance,
            source="system",
            metadata={"category": "reflection", "reflection_name": name},
        )
    except Exception as exc:
        logger.warning(
            "Reflection %s memory: sink failed (importance=%s): %r",
            name,
            importance,
            exc,
        )


def _deliver_telegram(reflection: Reflection, run: ReflectionRun, sink: str, output: Any) -> None:
    name = reflection.name or "<unnamed>"
    _, _, chat = sink.partition(":")
    chat = chat.strip()

    try:
        chat_id = _resolve_telegram_chat(chat)
        if chat_id is None:
            _record_delivery_error(run, f"telegram_resolve_failed: {chat}")
            logger.warning(
                "Reflection %s telegram: sink could not resolve chat=%r (projects.json=%s).",
                name,
                chat,
                _projects_config_path(),
            )
            return

        payload = {
            "chat_id": chat_id,
            "reply_to": None,
            "text": f"[{name}] {_truncate(output, 1500)}",
            "session_id": f"reflection:{name}",
            "timestamp": time.time(),
        }
        _push_outbox(payload)
    except Exception as exc:
        _record_delivery_error(run, f"telegram_delivery_failed: {exc}")
        logger.warning(
            "Reflection %s telegram: sink failed for chat=%r: %r",
            name,
            chat,
            exc,
        )


# ---------------------------------------------------------------------------
# Telegram resolution + outbox push


def _projects_config_path() -> Path:
    """Resolve the canonical projects.json path (vault-synced)."""
    override = os.environ.get("PROJECTS_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    vault = Path("~/Desktop/Valor/projects.json").expanduser()
    if vault.exists():
        return vault
    # Fallback to in-repo copy (tests, fresh checkouts)
    return Path(__file__).resolve().parent.parent / "config" / "projects.json"


def _load_projects_config() -> dict:
    """Load projects.json via bridge.routing.load_config when available.

    Falls back to a direct json.load if the loader is unavailable for any
    reason (e.g. circular import in tests). Returns an empty config shape
    on missing-file rather than raising.
    """
    try:
        from bridge.routing import load_config

        return load_config() or {"projects": {}, "dms": {}}
    except Exception:
        path = _projects_config_path()
        try:
            with open(path) as fp:
                return json.load(fp)
        except FileNotFoundError:
            return {"projects": {}, "dms": {}}
        except Exception as exc:
            logger.warning("Failed to load %s: %r", path, exc)
            return {"projects": {}, "dms": {}}


def _resolve_telegram_chat(chat: str) -> int | None:
    """Resolve a ``telegram:<chat>`` token to a numeric chat_id.

    Resolution order (per Q5 cycle-4):
      1. Literal int — if ``chat`` parses as int, return it.
      2. ``projects.<key>.telegram.groups.<chat>`` — group display-name lookup
         in any project. Returns the group's ``chat_id`` field.
      3. ``dms.whitelist[].name == chat`` — DM contact name lookup. Returns
         the entry's ``id`` field.

    Returns ``None`` on no match.
    """
    if not chat:
        return None

    # 1. Literal int
    try:
        return int(chat)
    except (TypeError, ValueError):
        pass

    config = _load_projects_config()

    # 2. projects.<key>.telegram.groups.<chat>
    projects = config.get("projects") or {}
    for proj_cfg in projects.values():
        if not isinstance(proj_cfg, dict):
            continue
        groups = ((proj_cfg.get("telegram") or {}).get("groups")) or {}
        entry = groups.get(chat)
        if isinstance(entry, dict):
            chat_id = entry.get("chat_id")
            if isinstance(chat_id, int):
                return chat_id
            if isinstance(chat_id, str):
                try:
                    return int(chat_id)
                except ValueError:
                    continue
        elif isinstance(entry, int):
            # Older registry shape: groups: {name: chat_id}
            return entry

    # 3. dms.whitelist[].name
    for dm in (config.get("dms") or {}).get("whitelist") or []:
        if not isinstance(dm, dict):
            continue
        if dm.get("name") == chat:
            dm_id = dm.get("id")
            if isinstance(dm_id, int):
                return dm_id
            if isinstance(dm_id, str):
                try:
                    return int(dm_id)
                except ValueError:
                    continue

    return None


def _push_outbox(payload: dict) -> None:
    """Push a Telegram outbox payload onto the bridge's Redis queue.

    Matches the schema written by ``tools/send_telegram.py`` — key
    ``telegram:outbox:{session_id}`` with a JSON payload and a 1-hour TTL
    safety net.
    """
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = redis.Redis.from_url(redis_url, decode_responses=True)

    queue_key = f"telegram:outbox:{payload['session_id']}"
    client.rpush(queue_key, json.dumps(payload))
    client.expire(queue_key, 3600)


def _record_delivery_error(run: ReflectionRun, message: str) -> None:
    """Best-effort populate ``run.delivery_error`` without crashing."""
    try:
        run.delivery_error = message
        run.save()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to persist delivery_error %r: %r", message, exc)
