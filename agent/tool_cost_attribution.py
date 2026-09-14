"""Per-tool context-cost attribution from the stream-json parse path (issue #3081).

Answers "which tools are eating the context window" so the persona-toolbelt
work can rank tools by what they actually cost, not by intuition.

What the numbers mean
---------------------
``claude -p --output-format stream-json`` reports usage per *assistant
message*, never per tool call. There is no per-tool ledger to read. So this
module derives one:

    method ``assistant-usage-delta/v1``

    * The prompt prefix size at an assistant message is
      ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``.
      Summing all three is deliberate: a turn that writes the cache and the
      next turn that reads it describe the same prefix, so the sum stays
      continuous across a caching boundary where any single field jumps.
    * The GROWTH of that prefix between two consecutive assistant messages is
      almost entirely (a) the tool results returned in between and (b) the
      ``tool_use`` blocks that requested them. That growth is attributed to
      the tools the PREVIOUS assistant message invoked, split evenly when it
      invoked several.
    * The ``output_tokens`` of the message issuing the ``tool_use`` blocks is
      attributed to those same tools — the cost of asking.

This is an approximation and is meant to be one. Context compaction, cache
eviction, and the model's own prose between tool calls all leak into the
delta; a shrinking prefix clamps to zero rather than going negative. The plan
(#3081, Rabbit Hole "Perfect token attribution") sets the bar at *consistent
enough to RANK tools*, and this clears that bar: a tool whose results dominate
the prefix outranks one whose results are small, run after run. Do not read
the absolute numbers as an accounting of billed tokens — ``total_cost_usd``
on the ``result`` event remains the only authority there.

Fail-quiet contract
-------------------
Every entry point swallows its own exceptions, logs a WARNING, and increments
``dropped_samples``. A turn must never fail because an approximation could not
be computed. ``dropped_samples`` is carried into the telemetry event so a
silently degraded stream is visible instead of looking like a quiet one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Version tag stamped on every snapshot. Bump when the arithmetic changes so
#: a baseline comparison cannot silently mix two methods.
ATTRIBUTION_METHOD = "assistant-usage-delta/v1"

#: Usage keys that together describe the prompt prefix size.
_PREFIX_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

#: How many tools the compact session summary carries.
_SUMMARY_TOP_N = 3


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_tool_row() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _split_evenly(total: int, names: list[str]) -> dict[str, int]:
    """Split *total* across *names*, handing the remainder to the first name.

    Nothing is rounded away — ``sum(result.values()) == total`` always holds,
    which is what makes the per-tool numbers add back up to the turn total.
    """
    if not names or total <= 0:
        return {}
    share, remainder = divmod(total, len(names))
    out: dict[str, int] = {}
    for i, name in enumerate(names):
        out[name] = out.get(name, 0) + share + (remainder if i == 0 else 0)
    return out


class ToolCostAttributor:
    """Accumulates per-tool context cost across one stream-json subprocess.

    Feed it every parsed event with :meth:`observe_event`; read the aggregate
    with :meth:`snapshot`. One instance per subprocess invocation — the caller
    merges across invocations with :func:`merge_tool_cost_snapshots`.
    """

    def __init__(self) -> None:
        self._per_tool: dict[str, dict[str, int]] = {}
        self._tool_call_count = 0
        self._dropped = 0
        self._pending_tools: list[str] = []
        self._prev_prefix: int | None = None
        self._initial_prefix: int | None = None
        self._tool_surface_size: int | None = None

    # -- ingestion ---------------------------------------------------------

    def observe_event(self, data: Any) -> None:
        """Fold one parsed stream-json event into the running attribution.

        Never raises. A malformed event costs one ``dropped_samples`` and a
        WARNING; the attributor stays usable for the rest of the stream.
        """
        try:
            event_type = data.get("type")
            if event_type == "system" and data.get("subtype") == "init":
                self._observe_init(data)
            elif event_type == "assistant":
                self._observe_assistant(data)
        except Exception as exc:  # noqa: BLE001 — attribution is best-effort by contract
            self._dropped += 1
            logger.warning(
                "[tool-cost] dropped an attribution sample (event unusable): %r",
                exc,
            )

    def _observe_init(self, data: dict) -> None:
        tools = data.get("tools")
        if isinstance(tools, list):
            self._tool_surface_size = len(tools)

    def _observe_assistant(self, data: dict) -> None:
        message = data.get("message") or {}
        usage = message.get("usage") or {}
        prefix = sum(_int(usage.get(k)) for k in _PREFIX_KEYS)
        output_tokens = _int(usage.get("output_tokens"))

        if self._initial_prefix is None and prefix > 0:
            self._initial_prefix = prefix

        # Growth since the previous assistant message belongs to the tools that
        # previous message invoked. Clamped at zero: a compaction or cache
        # eviction shrinks the prefix and is not a negative tool cost.
        if self._pending_tools and self._prev_prefix is not None and prefix > 0:
            delta = max(0, prefix - self._prev_prefix)
            for name, tokens in _split_evenly(delta, self._pending_tools).items():
                row = self._per_tool.setdefault(name, _empty_tool_row())
                row["input_tokens"] += tokens
                row["total_tokens"] += tokens

        content = message.get("content") or []
        names = [
            b.get("name") or "<unnamed>"
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        for name in names:
            row = self._per_tool.setdefault(name, _empty_tool_row())
            row["calls"] += 1
        self._tool_call_count += len(names)

        # The output tokens spent emitting these tool_use blocks are part of
        # what the tools cost.
        for name, tokens in _split_evenly(output_tokens, names).items():
            row = self._per_tool.setdefault(name, _empty_tool_row())
            row["output_tokens"] += tokens
            row["total_tokens"] += tokens

        self._pending_tools = names
        if prefix > 0:
            self._prev_prefix = prefix

    # -- readout -----------------------------------------------------------

    def snapshot(self) -> dict:
        """Return the aggregate so far. Never raises."""
        try:
            per_tool = {name: dict(row) for name, row in self._per_tool.items()}
            return {
                "method": ATTRIBUTION_METHOD,
                "per_tool": per_tool,
                "tool_call_count": self._tool_call_count,
                "attributed_input_tokens": sum(r["input_tokens"] for r in per_tool.values()),
                "attributed_output_tokens": sum(r["output_tokens"] for r in per_tool.values()),
                "initial_context_tokens": self._initial_prefix,
                "tool_surface_size": self._tool_surface_size,
                "dropped_samples": self._dropped,
            }
        except Exception as exc:  # noqa: BLE001 — a readout must never break a turn
            self._dropped += 1
            logger.warning("[tool-cost] snapshot failed, reporting empty: %r", exc)
            return {
                "method": ATTRIBUTION_METHOD,
                "per_tool": {},
                "tool_call_count": 0,
                "attributed_input_tokens": 0,
                "attributed_output_tokens": 0,
                "initial_context_tokens": None,
                "tool_surface_size": None,
                "dropped_samples": self._dropped,
            }


def merge_tool_cost_snapshots(snapshots: list[dict] | tuple[dict, ...]) -> dict:
    """Sum several snapshots into one.

    ``get_response_via_harness`` may run up to three subprocesses for a single
    turn (primary plus the stale-UUID fallback), and their costs belong to the
    same turn. Never raises.
    """
    merged_tools: dict[str, dict[str, int]] = {}
    tool_calls = 0
    dropped = 0
    initial: int | None = None
    surface: int | None = None
    try:
        for snap in snapshots or []:
            if not isinstance(snap, dict):
                continue
            for name, row in (snap.get("per_tool") or {}).items():
                target = merged_tools.setdefault(name, _empty_tool_row())
                for key in ("calls", "input_tokens", "output_tokens", "total_tokens"):
                    target[key] += _int(row.get(key))
            tool_calls += _int(snap.get("tool_call_count"))
            dropped += _int(snap.get("dropped_samples"))
            if initial is None and snap.get("initial_context_tokens") is not None:
                initial = _int(snap.get("initial_context_tokens"))
            if snap.get("tool_surface_size") is not None:
                surface = max(surface or 0, _int(snap.get("tool_surface_size")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[tool-cost] snapshot merge failed, reporting partial: %r", exc)
    return {
        "method": ATTRIBUTION_METHOD,
        "per_tool": merged_tools,
        "tool_call_count": tool_calls,
        "attributed_input_tokens": sum(r["input_tokens"] for r in merged_tools.values()),
        "attributed_output_tokens": sum(r["output_tokens"] for r in merged_tools.values()),
        "initial_context_tokens": initial,
        "tool_surface_size": surface,
        "dropped_samples": dropped,
    }


def session_tool_cost_summary(session: Any) -> dict | None:
    """Compact per-session tool-cost aggregate for stamping on a stage transition.

    Reads the cumulative snapshot the harness persists on
    ``AgentSession.tool_cost_json``. Returns ``None`` — never raises, never
    partially fills — when the session is absent, pre-belt (field never
    written), or the stored JSON is unreadable, so a stage transition on a
    legacy record is indistinguishable from one on a session that used no
    tools only by the absence of the key.
    """
    try:
        raw = getattr(session, "tool_cost_json", None)
        if not raw:
            return None
        stored = json.loads(raw)
        per_tool = stored.get("per_tool") or {}
        ranked = sorted(
            ((name, _int(row.get("total_tokens"))) for name, row in per_tool.items()),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return {
            "method": stored.get("method", ATTRIBUTION_METHOD),
            "tool_calls": _int(stored.get("tool_call_count")),
            "attributed_tokens": sum(tokens for _, tokens in ranked),
            "top_tools": [[name, tokens] for name, tokens in ranked[:_SUMMARY_TOP_N]],
        }
    except Exception as exc:  # noqa: BLE001 — a stage transition must never fail on telemetry
        logger.warning("[tool-cost] session summary unavailable: %r", exc)
        return None
