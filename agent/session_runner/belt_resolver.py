"""Persona toolbelt resolution — plan #3081, Lane A (ships dark).

Compiles a persona's committed belt manifest (``config/toolbelts/
{persona}.toml``) into Claude CLI flags. Pure: given the same manifest
bytes, :func:`resolve_belt` returns byte-identical flags in a fixed argv
order on any host — no env reads, no host inventory probes, no network.

Fail-closed: an unknown persona, a missing manifest, malformed TOML, an
invalid manifest shape, or a belt-version mismatch each raise
:class:`BeltResolutionError`, and the harness refuses the turn before any
``claude -p`` subprocess spawns. The error message always contains
"unresolvable" (and "unknown persona" for that case) — pinned by the plan's
Verification table.

Flag form is load-bearing: the CLI's ``--tools`` / ``--mcp-config`` /
``--allowedTools`` / ``--disallowedTools`` options are VARIADIC
(``<tools...>``), so a space-separated value would swallow the positional
message the harness appends last (observed live on claude 2.1.236:
``--tools Bash,Read "msg"`` fails with "Input must be provided"). Every
compiled flag is therefore a single ``--flag=value`` argv element.

Race 2 (belt narrowing across a ``--resume`` boundary) is pinned to the
OBSERVED CLI behavior, verified live on claude 2.1.236 (2026-09-02): a
session whose replayed history contained a Bash ``tool_use`` block resumed
with ``--tools=Read`` (a strict subset) degraded gracefully — exit 0,
``is_error: false``, correct result. The resolver keeps the narrowed belt
on resumed turns and logs the narrowing instead of widening to the union.

Race 3 (fleet-skewed activation window): :func:`check_and_stamp_belt_state`
stamps the resolved enforce-state and belt version onto the AgentSession
(ORM only) at turn start and emits a WARNING-level telemetry event when the
prior-turn stamp disagrees with the current host's resolved state.
Fail-quiet — it never raises.
"""

from __future__ import annotations

import json
import logging
import socket
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from agent.session_telemetry import record_telemetry_event

logger = logging.getLogger(__name__)

# Race 3 skew event type — the string tools/belt_skew_report.py filters on.
# Pinned as a shared contract in the lane's PROGRESS.md; keep the two in sync
# by importing this constant rather than re-typing the literal.
BELT_SKEW_EVENT_TYPE = "belt_enforce_skew"

# Escalation marker for missing-capability lines (plan #3081 Phase 1
# escalation path). The role priming skills instruct the agent to state a
# missing capability plainly on its own line starting with this marker; the
# runner tags and forwards such lines on the existing open-question channel
# (non-blocking) via :func:`forward_capability_escalations`.
ESCALATION_MARKER = "[missing-capability]"

# Telemetry event type recorded for each forwarded escalation line.
BELT_ESCALATION_EVENT_TYPE = "belt_escalation"

# The personas that have belts. Mirrors the role vocabulary in
# agent/session_runner/role_driver.py (``_PRIME_SLASH_BY_ROLE``); kept as a
# local literal so this module's resolution path stays import-light and
# cycle-free. Any other persona string fails closed as unknown.
KNOWN_PERSONAS: tuple[str, ...] = ("pm", "dev", "teammate")

# Manifest schema vocabulary — unknown keys fail closed (typo protection: a
# misspelled section silently widening the belt is exactly the failure mode
# fail-closed resolution exists to prevent).
_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {"belt_version", "claude_cli_validated", "builtin", "mcp_servers", "permissions"}
)
_ALLOWED_MCP_SPEC_KEYS = frozenset({"command", "args", "env", "type"})
_ALLOWED_PERMISSION_KEYS = frozenset({"allowed", "disallowed"})


class BeltResolutionError(Exception):
    """A persona's belt could not be resolved; the turn must be refused.

    ``reason`` is a stable machine-readable code: ``unknown_persona`` |
    ``missing_manifest`` | ``malformed_manifest`` | ``version_mismatch``.
    The human-readable message always contains "unresolvable" so the
    refusal is greppable end-to-end (plan #3081 Verification table), and it
    is what the runner's terminal exception classification surfaces to the
    session output path (``exit_message`` in
    agent/session_runner/runner.py) — the reason reaches the user, not
    just the logs.
    """

    def __init__(self, persona: object, reason: str, detail: str = "") -> None:
        self.persona = persona
        self.reason = reason
        label = "unknown persona" if reason == "unknown_persona" else reason.replace("_", " ")
        message = f"unresolvable belt for persona {persona!r} ({label})"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedBelt:
    """The compiled result of one belt resolution — immutable for the turn.

    ``flags`` is the exact argv fragment to splice into ``harness_cmd``
    before the positional message (Race 1: resolved once at turn start,
    never re-read mid-turn).
    """

    persona: str
    belt_version: int
    flags: tuple[str, ...]


def _default_toolbelts_dir() -> Path:
    # agent/session_runner/belt_resolver.py -> repo root / config / toolbelts
    return Path(__file__).resolve().parents[2] / "config" / "toolbelts"


def _require(condition: bool, persona: str, detail: str) -> None:
    if not condition:
        raise BeltResolutionError(persona, "malformed_manifest", detail)


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item for item in value
    )


def _validate_manifest(persona: str, manifest: dict) -> None:
    """Validate the manifest shape; raise ``malformed_manifest`` on any
    deviation. Runs BEFORE compilation so a partially-valid manifest can
    never half-compile."""
    unknown = set(manifest) - _ALLOWED_TOP_LEVEL_KEYS
    _require(not unknown, persona, f"unknown top-level keys {sorted(unknown)}")

    version = manifest.get("belt_version")
    _require(
        isinstance(version, int) and not isinstance(version, bool) and version >= 1,
        persona,
        f"belt_version must be a positive integer, got {version!r}",
    )

    cli = manifest.get("claude_cli_validated")
    _require(
        cli is None or isinstance(cli, str),
        persona,
        "claude_cli_validated must be a string",
    )

    builtin = manifest.get("builtin")
    _require(isinstance(builtin, dict), persona, "[builtin] table is required")
    _require(
        set(builtin) == {"tools"},
        persona,
        "[builtin] must contain exactly the 'tools' key",
    )
    tools = builtin["tools"]
    _require(
        tools == "default" or _is_str_list(tools),
        persona,
        "[builtin].tools must be \"default\" or a list of tool names",
    )

    servers = manifest.get("mcp_servers", {})
    _require(isinstance(servers, dict), persona, "[mcp_servers] must be a table")
    for name, spec in servers.items():
        _require(isinstance(spec, dict), persona, f"[mcp_servers.{name}] must be a table")
        unknown_spec = set(spec) - _ALLOWED_MCP_SPEC_KEYS
        _require(
            not unknown_spec,
            persona,
            f"[mcp_servers.{name}] unknown keys {sorted(unknown_spec)}",
        )
        _require(
            isinstance(spec.get("command"), str) and spec["command"],
            persona,
            f"[mcp_servers.{name}] requires a non-empty 'command' string",
        )
        _require(
            "args" not in spec or _is_str_list(spec["args"]) or spec["args"] == [],
            persona,
            f"[mcp_servers.{name}].args must be a list of strings",
        )
        env = spec.get("env", {})
        _require(
            isinstance(env, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()),
            persona,
            f"[mcp_servers.{name}].env must be a string-to-string table",
        )

    permissions = manifest.get("permissions", {})
    _require(isinstance(permissions, dict), persona, "[permissions] must be a table")
    unknown_perm = set(permissions) - _ALLOWED_PERMISSION_KEYS
    _require(not unknown_perm, persona, f"[permissions] unknown keys {sorted(unknown_perm)}")
    for key in _ALLOWED_PERMISSION_KEYS:
        value = permissions.get(key, [])
        _require(
            value == [] or _is_str_list(value),
            persona,
            f"[permissions].{key} must be a list of strings",
        )


def _load_manifest(persona: str, toolbelts_dir: Path) -> dict:
    path = toolbelts_dir / f"{persona}.toml"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise BeltResolutionError(
            persona, "missing_manifest", f"no manifest at {path}"
        ) from None
    except OSError as exc:
        raise BeltResolutionError(persona, "missing_manifest", str(exc)) from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise BeltResolutionError(persona, "malformed_manifest", str(exc)) from exc


def resolve_belt(
    persona: str,
    *,
    expected_belt_version: int | None = None,
    resumed_history_tools: Iterable[str] | None = None,
    toolbelts_dir: Path | None = None,
) -> ResolvedBelt:
    """Compile ``persona``'s belt manifest into CLI flags. Pure; fail-closed.

    Args:
        persona: One of :data:`KNOWN_PERSONAS`; anything else refuses.
        expected_belt_version: When set, the manifest's ``belt_version``
            must equal it exactly or the turn is refused
            (``version_mismatch``) — the discrete comparable pin resolved
            by plan #3081 Open Question 1.
        resumed_history_tools: Tool names referenced by ``tool_use`` blocks
            in a resumed transcript, when this turn resumes one. Purely
            observational (Race 2 pinned graceful — see module docstring):
            the narrowed belt is kept, and the narrowing is logged.
        toolbelts_dir: Manifest directory override for tests; defaults to
            the committed ``config/toolbelts/``.

    Returns:
        A :class:`ResolvedBelt` whose ``flags`` splice into ``harness_cmd``
        verbatim, in canonical order: ``--tools=`` then ``--mcp-config=``
        then ``--strict-mcp-config`` then ``--allowedTools=`` then
        ``--disallowedTools=``.

    Raises:
        BeltResolutionError: On any of the four fail-closed conditions.
    """
    if persona not in KNOWN_PERSONAS:
        raise BeltResolutionError(persona, "unknown_persona")

    manifest = _load_manifest(persona, toolbelts_dir or _default_toolbelts_dir())
    _validate_manifest(persona, manifest)

    belt_version: int = manifest["belt_version"]
    if expected_belt_version is not None and belt_version != expected_belt_version:
        raise BeltResolutionError(
            persona,
            "version_mismatch",
            f"manifest declares belt_version={belt_version}, expected {expected_belt_version}",
        )

    tools = manifest["builtin"]["tools"]
    tools_value = tools if isinstance(tools, str) else ",".join(tools)
    flags: list[str] = [f"--tools={tools_value}"]

    # Always compile a strict MCP config — even an empty one — so ambient
    # host servers (~/.claude.json extras) can never leak into an enforced
    # turn. sort_keys + compact separators make the JSON byte-deterministic.
    servers = {}
    for name, spec in manifest.get("mcp_servers", {}).items():
        entry: dict[str, object] = {"command": spec["command"]}
        if "args" in spec:
            entry["args"] = list(spec["args"])
        if "env" in spec:
            entry["env"] = dict(spec["env"])
        if "type" in spec:
            entry["type"] = spec["type"]
        servers[name] = entry
    mcp_json = json.dumps({"mcpServers": servers}, sort_keys=True, separators=(",", ":"))
    flags.append(f"--mcp-config={mcp_json}")
    flags.append("--strict-mcp-config")

    permissions = manifest.get("permissions", {})
    allowed = permissions.get("allowed", [])
    if allowed:
        flags.append("--allowedTools=" + ",".join(allowed))
    disallowed = permissions.get("disallowed", [])
    if disallowed:
        flags.append("--disallowedTools=" + ",".join(disallowed))

    if resumed_history_tools is not None and isinstance(tools, list):
        missing = sorted(set(resumed_history_tools) - set(tools))
        if missing:
            logger.info(
                "[belt] %s belt v%d is narrower than resumed history "
                "(history references %s, absent from the belt); keeping the "
                "narrow belt — resume degrades gracefully (verified on "
                "claude 2.1.236)",
                persona,
                belt_version,
                missing,
            )

    return ResolvedBelt(persona=persona, belt_version=belt_version, flags=tuple(flags))


def check_and_stamp_belt_state(
    session_id: str | None, *, enforce: bool, belt_version: int | None
) -> None:
    """Race 3 turn-start stamp: compare and record belt enforce-state.

    Compares the prior-turn ``belt_enforce_state`` stamp on the newest
    AgentSession record for ``session_id`` against the current host's
    resolved state; on mismatch emits the WARNING-level
    :data:`BELT_SKEW_EVENT_TYPE` telemetry event (fail-quiet), then stamps
    the current state and belt version via the ORM. A ``None`` prior stamp
    is a pre-belt legacy read, never a mismatch. NEVER raises — belt
    observability must not crash a turn.
    """
    if not session_id:
        return
    try:
        # Deferred import: models pull in config.settings and Redis wiring;
        # the resolver's pure compile path must stay import-light.
        from models.agent_session import AgentSession  # noqa: PLC0415

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        session = sessions[0]

        current = "on" if enforce else "off"
        prior = getattr(session, "belt_enforce_state", None)
        if prior in ("on", "off") and prior != current:
            event = {
                "type": BELT_SKEW_EVENT_TYPE,
                "level": "WARNING",
                "prior_enforce_state": prior,
                "current_enforce_state": current,
                "prior_belt_version": getattr(session, "belt_version", None),
                "current_belt_version": belt_version,
                "host": socket.gethostname(),
            }
            record_telemetry_event(session_id, event)
            logger.warning(
                "[belt] enforce-state skew for session %s: prior turn ran %s, "
                "this host resolves %s (fleet activation window — see plan "
                "#3081 Race 3)",
                session_id,
                prior,
                current,
            )

        session.belt_enforce_state = current
        session.belt_version = belt_version
        session.save(update_fields=["belt_enforce_state", "belt_version", "updated_at"])
    except Exception as exc:  # noqa: BLE001 — observability must never crash a turn
        logger.debug(
            "check_and_stamp_belt_state(%r) swallowed exception: %r", session_id, exc
        )


def forward_capability_escalations(
    text: str,
    *,
    forwarded: set[str],
    deliver: Callable[[str], None],
    record: Callable[[dict], None],
    role: str | None = None,
) -> list[str]:
    """Tag and forward missing-capability lines from one turn's text.

    Scans ``text`` for lines beginning with :data:`ESCALATION_MARKER`
    (the plainly-stated missing-capability lines the role priming skills
    instruct the agent to emit). Each NEW line (not already in
    ``forwarded``) is recorded as a :data:`BELT_ESCALATION_EVENT_TYPE`
    telemetry event and delivered on the caller's open-question channel.
    Non-blocking by contract: never raises, never influences turn routing;
    ``forwarded`` is mutated in place so a repeated line is forwarded once
    per run.

    Returns the list of newly forwarded lines (for the caller's report).
    """
    lines_out: list[str] = []
    try:
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line.startswith(ESCALATION_MARKER) or line in forwarded:
                continue
            forwarded.add(line)
            lines_out.append(line)
            try:
                record({"type": BELT_ESCALATION_EVENT_TYPE, "line": line, "role": role})
            except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
                logger.debug("belt escalation record failed: %r", exc)
            try:
                deliver(line)
            except Exception as exc:  # noqa: BLE001 — delivery is best-effort
                logger.debug("belt escalation delivery failed: %r", exc)
    except Exception as exc:  # noqa: BLE001 — never disturb the turn
        logger.debug("forward_capability_escalations swallowed exception: %r", exc)
    return lines_out
