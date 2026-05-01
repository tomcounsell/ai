"""TTFT (time-to-first-token) measurement for harness cold-start (issue #1227).

Every first-turn harness invocation appends a JSON line to
``logs/cold_start_metrics.jsonl`` so we can track the before/after
distribution of PM session cold-start latency.

All writes are best-effort: any failure is silently ignored so that a
permissions error or a full disk NEVER blocks the worker or the user.

Schema of each JSONL line:

.. code-block:: json

    {
        "timestamp": "2026-04-30T12:34:56.789Z",
        "session_id": "tg_valor_-5051653062_9413",
        "session_type": "pm",
        "working_dir": "/path/to/project",
        "prompt_chars": 74769,
        "model": "opus",
        "ttft_seconds": 12.345,
        "cache_read_input_tokens": 0
    }

The ``cache_read_input_tokens`` field defaults to 0 ("no cache hit"); a
positive value confirms the server-side prompt cache was hit and is the
most reliable indicator that Direction A is working.  Populating it
retroactively from the ``result`` event's usage dict is a follow-up
enhancement; today the field is recorded as 0 at first-stdout-byte time.

Usage::

    # At first-stdout-byte (in _run_harness_subprocess):
    record_ttft(
        ttft_seconds=elapsed,
        session_id="...",
        session_type="pm",
        working_dir="/path/to/project",
        prompt_chars=74769,
        model="opus",
    )
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the JSONL metrics file.  Relative to CWD (normally the repo root).
_METRICS_FILE = Path("logs/cold_start_metrics.jsonl")


def record_ttft(
    *,
    ttft_seconds: float,
    session_id: str,
    session_type: str,
    working_dir: str,
    prompt_chars: int,
    model: str,
    cache_read_input_tokens: int = 0,
) -> None:
    """Append one TTFT measurement to ``logs/cold_start_metrics.jsonl``.

    All failures are swallowed.  The caller (``_run_harness_subprocess``) is
    in a hot async path and must not be blocked by I/O errors.

    Args:
        ttft_seconds: Elapsed seconds from subprocess spawn to first non-empty
            stdout line.
        session_id: Bridge/Telegram session ID (may be empty string for local
            dev invocations).
        session_type: ``"pm"``, ``"dev"``, ``"teammate"``, or ``"other"``.
        working_dir: Absolute path of the working directory used for the
            subprocess.  For cross-project analysis (which project pays which
            TTFT).
        prompt_chars: Character count of the system prompt passed to
            ``--append-system-prompt``, or 0 if no system prompt was used.
        model: Model alias (e.g. ``"opus"``, ``"sonnet"``) or ``"default"``
            when the caller did not set an explicit model.
        cache_read_input_tokens: Tokens served from Anthropic's server-side
            prompt cache for this turn (from the ``result`` event's usage dict).
            Defaults to 0; a non-zero value confirms a cache hit.  Retroactive
            population from the ``result`` event is a follow-up enhancement.
    """
    try:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "session_type": session_type,
            "working_dir": working_dir,
            "prompt_chars": prompt_chars,
            "model": model,
            "ttft_seconds": round(ttft_seconds, 3),
            "cache_read_input_tokens": cache_read_input_tokens,
        }
        _metrics_file = _METRICS_FILE
        _metrics_file.parent.mkdir(parents=True, exist_ok=True)
        with _metrics_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            os.fsync(fh.fileno())
        logger.debug(
            "[TTFT] session_id=%s session_type=%s ttft=%.1fs prompt_chars=%d model=%s",
            session_id,
            session_type,
            ttft_seconds,
            prompt_chars,
            model,
        )
    except Exception as exc:  # noqa: BLE001
        # Instrumentation MUST NOT crash the caller.
        logger.warning("[TTFT] metric write failed (non-fatal): %s", exc)
