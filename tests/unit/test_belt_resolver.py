"""Tests for the persona toolbelt resolver (plan #3081, Lane A — ships dark).

Covers, per the plan's Failure Path Test Strategy and task 2:

- Fail-closed refusals: missing manifest, malformed TOML, unknown persona,
  version mismatch — each raises ``BeltResolutionError`` whose message
  satisfies the Verification grep (``unresolvable|unknown persona``), and no
  ``claude -p`` subprocess spawns.
- Empty belt (zero tools) is valid — "conversation-only" is a legitimate
  belt (verified live on claude 2.1.236: ``--tools=`` exits 0).
- Reproducibility: resolving the same belt in two synthetic environments
  (different env vars, different fake host inventory) yields BYTE-IDENTICAL
  flag output, including argv ORDER.
- Race 2 (belt narrowing across a ``--resume`` boundary): pinned to the
  OBSERVED CLI behavior. Live experiment on claude 2.1.236 (2026-09-02):
  a session whose history contains a Bash ``tool_use`` block was resumed
  with ``--tools=Read`` (a strict subset omitting Bash) and the CLI
  degraded gracefully — exit 0, ``is_error: false``, correct result text.
  The resolver therefore keeps the narrowed belt on resumed turns (no
  union-widening) and logs the narrowing for observability.
- Flag-off byte-identity: with ``TOOLBELTS_ENFORCE`` off the resolver is
  never called and the harness argv is byte-identical to ambient behavior.
- Flag-on wiring: belt flags are injected inside ``harness_cmd`` — before
  the positional message and any ``--resume`` append — as single
  ``--flag=value`` argv elements. The equals form is load-bearing: the
  CLI's ``--tools`` / ``--mcp-config`` / ``--allowedTools`` options are
  variadic and a space-separated value would swallow the positional
  message (observed live: ``--tools Bash,Read "msg"`` fails with "Input
  must be provided").
- Race 3 stamp: ``check_and_stamp_belt_state`` stamps belt fields via the
  ORM and emits the WARNING skew telemetry event on an enforce-state
  mismatch (fail-quiet).
- Escalation: the marker instruction line exists in every role priming
  skill, and ``forward_capability_escalations`` tags, dedupes, records,
  and forwards marker lines non-blockingly.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_runner.belt_resolver import (
    BELT_SKEW_EVENT_TYPE,
    ESCALATION_MARKER,
    BeltResolutionError,
    ResolvedBelt,
    check_and_stamp_belt_state,
    forward_capability_escalations,
    resolve_belt,
)
from config.settings import settings as app_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

SYNTHETIC_PM_TOML = """\
belt_version = 3
claude_cli_validated = "2.1.236"

[builtin]
# why: synthetic narrow belt for resolver tests
tools = ["Bash", "Read", "Edit"]

[mcp_servers.memory]
# why: synthetic server entry
command = "python3"
args = ["-m", "mcp_servers.memory_server"]
env = { PYTHONPATH = "." }

[permissions]
allowed = ["Bash(git *)"]
disallowed = ["WebSearch"]
"""


@pytest.fixture
def belts_dir(tmp_path: Path) -> Path:
    (tmp_path / "pm.toml").write_text(SYNTHETIC_PM_TOML)
    return tmp_path


# ---------------------------------------------------------------------------
# Fail-closed refusals
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_unknown_persona_refuses(self):
        with pytest.raises(BeltResolutionError) as exc_info:
            resolve_belt("nonexistent")
        msg = str(exc_info.value).lower()
        assert "unresolvable" in msg or "unknown persona" in msg
        assert exc_info.value.reason == "unknown_persona"

    def test_missing_manifest_refuses(self, tmp_path: Path):
        with pytest.raises(BeltResolutionError) as exc_info:
            resolve_belt("pm", toolbelts_dir=tmp_path)
        assert "unresolvable" in str(exc_info.value).lower()
        assert exc_info.value.reason == "missing_manifest"

    def test_malformed_toml_refuses(self, tmp_path: Path):
        (tmp_path / "pm.toml").write_text("belt_version = [unclosed\n")
        with pytest.raises(BeltResolutionError) as exc_info:
            resolve_belt("pm", toolbelts_dir=tmp_path)
        assert "unresolvable" in str(exc_info.value).lower()
        assert exc_info.value.reason == "malformed_manifest"

    def test_version_mismatch_refuses(self, belts_dir: Path):
        with pytest.raises(BeltResolutionError) as exc_info:
            resolve_belt("pm", expected_belt_version=2, toolbelts_dir=belts_dir)
        assert "unresolvable" in str(exc_info.value).lower()
        assert exc_info.value.reason == "version_mismatch"

    @pytest.mark.parametrize(
        "body",
        [
            # belt_version absent
            '[builtin]\ntools = "default"\n',
            # belt_version wrong type
            'belt_version = "one"\n[builtin]\ntools = "default"\n',
            # builtin table absent
            "belt_version = 1\n",
            # tools wrong type
            "belt_version = 1\n[builtin]\ntools = 7\n",
            # unknown top-level key (typo protection fails closed)
            'belt_version = 1\n[builtin]\ntools = "default"\n[permisions]\nallowed = []\n',
            # mcp server missing command
            'belt_version = 1\n[builtin]\ntools = "default"\n[mcp_servers.x]\nargs = []\n',
        ],
    )
    def test_invalid_manifest_shape_refuses(self, tmp_path: Path, body: str):
        (tmp_path / "pm.toml").write_text(body)
        with pytest.raises(BeltResolutionError) as exc_info:
            resolve_belt("pm", toolbelts_dir=tmp_path)
        assert "unresolvable" in str(exc_info.value).lower()

    def test_persona_is_not_a_path(self, belts_dir: Path):
        """A persona string can never traverse outside the manifest dir."""
        with pytest.raises(BeltResolutionError):
            resolve_belt("../pm", toolbelts_dir=belts_dir)


# ---------------------------------------------------------------------------
# Valid resolution
# ---------------------------------------------------------------------------


class TestResolution:
    def test_flags_and_order(self, belts_dir: Path):
        resolved = resolve_belt("pm", toolbelts_dir=belts_dir)
        assert isinstance(resolved, ResolvedBelt)
        assert resolved.belt_version == 3
        mcp_json = json.dumps(
            {
                "mcpServers": {
                    "memory": {
                        "command": "python3",
                        "args": ["-m", "mcp_servers.memory_server"],
                        "env": {"PYTHONPATH": "."},
                    }
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        # Exact argv including ORDER — the canonical compile order is part of
        # the reproducibility contract.
        assert list(resolved.flags) == [
            "--tools=Bash,Read,Edit",
            f"--mcp-config={mcp_json}",
            "--strict-mcp-config",
            "--allowedTools=Bash(git *)",
            "--disallowedTools=WebSearch",
        ]

    def test_every_flag_is_single_argv_element(self, belts_dir: Path):
        """Variadic-swallow guard: each belt flag must be one ``--x=y`` argv
        element (or a bare boolean flag), never a separate value element the
        CLI's variadic option parsing could confuse with the positional
        message."""
        resolved = resolve_belt("pm", toolbelts_dir=belts_dir)
        for flag in resolved.flags:
            assert flag.startswith("--")

    def test_empty_belt_is_valid_conversation_only(self, tmp_path: Path):
        (tmp_path / "pm.toml").write_text("belt_version = 1\n[builtin]\ntools = []\n")
        resolved = resolve_belt("pm", toolbelts_dir=tmp_path)
        # Verified live on claude 2.1.236: `--tools=` (empty value) exits 0
        # and the turn completes conversation-only.
        assert resolved.flags[0] == "--tools="
        # Zero declared MCP servers still compiles a strict empty config so
        # ambient host servers cannot leak in.
        assert "--strict-mcp-config" in resolved.flags

    def test_committed_manifests_resolve(self):
        """The three shipped manifests are valid and resolve deterministically."""
        for persona in ("pm", "dev", "teammate"):
            first = resolve_belt(persona)
            second = resolve_belt(persona)
            assert first.flags == second.flags
            assert first.belt_version == 1
            assert first.flags[0] == "--tools=default"
            assert "--strict-mcp-config" in first.flags
            mcp_flag = next(f for f in first.flags if f.startswith("--mcp-config="))
            servers = json.loads(mcp_flag.split("=", 1)[1])["mcpServers"]
            assert set(servers) == {"memory", "byob"}


# ---------------------------------------------------------------------------
# Faithful-snapshot contract: the belts must reproduce what /update installs
# ---------------------------------------------------------------------------


def _committed_server(persona: str, name: str) -> dict:
    """Return one server entry as the resolver compiles it for a shipped belt."""
    flags = resolve_belt(persona).flags
    mcp_flag = next(f for f in flags if f.startswith("--mcp-config="))
    return json.loads(mcp_flag.split("=", 1)[1])["mcpServers"][name]


def _sh_expand(persona: str, name: str) -> list[str]:
    """Run the entry's ``sh -c`` script with ``exec`` swapped for ``echo``.

    Returns the argv the entry would have exec'd, with ``$HOME`` and any
    layout probing resolved exactly as it would be at server launch.
    """
    entry = _committed_server(persona, name)
    assert entry["command"] == "/bin/sh"
    script = entry["args"][1]
    assert "exec " in script, "the entry must exec its server, not fork a child shell"
    probe = subprocess.run(
        ["/bin/sh", "-c", script.replace("exec ", "echo ", 1)],
        capture_output=True,
        text=True,
        cwd="/",  # a neutral cwd: no session ever runs from the repo by luck
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    return probe.stdout.split()


class TestManifestsMatchInstalledSurface:
    """Lane A's premise is that a belt is a faithful snapshot of the ambient
    surface, so flipping ``TOOLBELTS_ENFORCE`` later changes nothing. These
    pin each MCP entry against what ``scripts/update/mcp_*.py`` actually
    installs — a belt that only works from the repo's own working directory,
    or on one of two BYOB layouts, breaks that premise silently.
    """

    @pytest.mark.parametrize("persona", ("pm", "dev", "teammate"))
    def test_memory_pythonpath_is_absolute_not_cwd_relative(self, persona):
        """``mcp_memory._expected_entry`` installs the ABSOLUTE repo root.

        Most sessions run inside another project's checkout (projects.json
        maps a dozen repos), where a cwd-relative PYTHONPATH holds no
        ``mcp_servers`` package and the memory tools would vanish.
        """
        entry = _committed_server(persona, "memory")
        declared = json.dumps(entry)
        assert '"PYTHONPATH": "."' not in declared
        assert 'PYTHONPATH="."' not in declared

        argv = _sh_expand(persona, "memory")
        # The launch line is `PYTHONPATH=<root> exec python3 -m <module>`, so
        # swapping exec for echo prints the interpreter + module argv; the
        # assignment prefix is applied to that command, not printed.
        assert argv[:3] == ["python3", "-m", "mcp_servers.memory_server"]

        script = _committed_server(persona, "memory")["args"][1]
        root = subprocess.run(
            ["/bin/sh", "-c", script.split(" exec ", 1)[0] + '; printf %s "$PYTHONPATH"'],
            capture_output=True,
            text=True,
            cwd="/",
            timeout=10,
        ).stdout
        assert Path(root).is_absolute()
        assert (Path(root) / "mcp_servers" / "memory_server.py").is_file(), (
            f"belt PYTHONPATH {root!r} holds no mcp_servers package"
        )

    @pytest.mark.parametrize("persona", ("pm", "dev", "teammate"))
    def test_byob_entry_covers_both_installer_layouts(self, persona):
        """``mcp_byob._resolve_tsx_bin`` prefers the workspace-root tsx and
        falls back to the package-local one; ``_byob_binaries_present``
        accepts either. A belt pinning one layout spawns nothing on a host
        that has the other."""
        from scripts.update import mcp_byob

        script = _committed_server(persona, "byob")["args"][1]
        home = str(Path.home())
        root_rel = str(mcp_byob.BYOB_TSX_BIN).replace(home, "$HOME", 1)
        pkg_rel = str(mcp_byob.BYOB_TSX_BIN_PKG).replace(home, "$HOME", 1)

        assert root_rel in script, "workspace-root tsx layout is unreachable from this belt"
        assert pkg_rel in script, "package-local tsx layout is unreachable from this belt"
        assert script.index(root_rel) < script.index(pkg_rel), (
            "the belt must try the layouts in the installer's preference order"
        )

        if not mcp_byob._byob_binaries_present():
            pytest.skip("BYOB not installed on this machine")
        argv = _sh_expand(persona, "byob")
        assert argv == [str(mcp_byob._resolve_tsx_bin()), str(mcp_byob.BYOB_MCP_SERVER_TS)]


# ---------------------------------------------------------------------------
# Reproducibility across synthetic environments
# ---------------------------------------------------------------------------


class TestReproducibility:
    def _resolve_in_env(self, monkeypatch, belts_dir: Path, *, env: dict, host: str, inventory):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(socket, "gethostname", lambda: host)
        monkeypatch.setattr(shutil, "which", inventory)
        return resolve_belt("pm", toolbelts_dir=belts_dir)

    def test_byte_identical_across_environments(self, monkeypatch, belts_dir: Path):
        env_a = {"HOME": "/Users/alpha", "PATH": "/usr/bin", "BELT_NOISE": "aaa"}
        env_b = {"HOME": "/home/beta", "PATH": "/opt/other/bin:/bin", "BELT_NOISE": "zzz"}
        with monkeypatch.context() as mp:
            flags_a = self._resolve_in_env(
                mp, belts_dir, env=env_a, host="host-alpha", inventory=lambda _n: "/usr/bin/x"
            ).flags
        with monkeypatch.context() as mp:
            flags_b = self._resolve_in_env(
                mp, belts_dir, env=env_b, host="host-beta", inventory=lambda _n: None
            ).flags
        assert flags_a == flags_b
        assert list(flags_a) == list(flags_b)  # order, not just set equality


# ---------------------------------------------------------------------------
# Race 2 — belt narrowing across a --resume boundary
# ---------------------------------------------------------------------------


class TestResumeNarrowing:
    def test_resume_with_strict_subset_keeps_narrow_belt(self, belts_dir: Path, caplog):
        """Pinned OBSERVED behavior (claude 2.1.236, live experiment
        2026-09-02): resuming a session whose replayed history contains
        ``tool_use`` blocks for a tool absent from the current ``--tools``
        list degrades gracefully (exit 0, ``is_error: false``). The resolver
        therefore does NOT widen to the union; it keeps the narrowed belt
        and logs the narrowing."""
        history_tools = frozenset({"Bash", "Read", "Write"})  # Write not in belt
        with caplog.at_level("INFO"):
            resumed = resolve_belt(
                "pm", resumed_history_tools=history_tools, toolbelts_dir=belts_dir
            )
        fresh = resolve_belt("pm", toolbelts_dir=belts_dir)
        assert resumed.flags == fresh.flags  # no union-widening
        assert any("narrower than resumed history" in r.message for r in caplog.records)

    def test_resume_with_superset_history_logs_nothing(self, belts_dir: Path, caplog):
        with caplog.at_level("INFO"):
            resolve_belt("pm", resumed_history_tools=frozenset({"Bash"}), toolbelts_dir=belts_dir)
        assert not any("narrower than resumed history" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Harness wiring — flag-off byte identity, flag-on injection, refusal
# ---------------------------------------------------------------------------

_SUBPROCESS_TUPLE = ("done", None, 0, None, None, "", 1, 0, None)


def _make_capture():
    calls: list[list[str]] = []

    async def _fake(cmd, working_dir, proc_env, *, on_exit_status=None, **_kw):
        calls.append(list(cmd))
        if on_exit_status is not None:
            on_exit_status(0, True)
        return _SUBPROCESS_TUPLE

    return _fake, calls


class TestHarnessWiring:
    async def _run(self, *, role, prior_uuid=None):
        from agent.session_runner.harness.claude import get_response_via_harness

        fake, calls = _make_capture()
        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            AsyncMock(side_effect=fake),
        ):
            await get_response_via_harness(
                "hello",
                "/tmp",
                role=role,
                prior_uuid=prior_uuid,
            )
        return calls

    @pytest.mark.asyncio
    async def test_flag_off_is_byte_identical_to_ambient(self, monkeypatch):
        monkeypatch.setattr(app_settings, "toolbelts_enforce", False)
        ambient = await self._run(role=None)
        with_role = await self._run(role="pm")
        assert ambient == with_role  # byte-identical argv, flag off

    @pytest.mark.asyncio
    async def test_flag_on_injects_belt_flags_before_positional(self, monkeypatch):
        monkeypatch.setattr(app_settings, "toolbelts_enforce", True)
        (calls,) = [await self._run(role="pm")]
        cmd = calls[0]
        tools_idx = next(i for i, a in enumerate(cmd) if a.startswith("--tools="))
        assert cmd[tools_idx] == "--tools=default"
        assert "--strict-mcp-config" in cmd
        # Belt flags precede the positional message (last element).
        assert cmd[-1] == "hello"
        assert tools_idx < len(cmd) - 1
        # bypassPermissions untouched (plan #2000 / #3081 Open Question 2).
        assert "bypassPermissions" in cmd

    @pytest.mark.asyncio
    async def test_flag_on_resume_keeps_belt_before_resume_flag(self, monkeypatch):
        monkeypatch.setattr(app_settings, "toolbelts_enforce", True)
        uuid = "36514af3-c4e9-455d-9087-f5850101990e"
        calls = await self._run(role="pm", prior_uuid=uuid)
        cmd = calls[0]
        tools_idx = next(i for i, a in enumerate(cmd) if a.startswith("--tools="))
        resume_idx = cmd.index("--resume")
        assert tools_idx < resume_idx
        assert cmd[-1] == "hello"
        assert cmd[resume_idx + 1] == uuid

    @pytest.mark.asyncio
    async def test_unresolvable_belt_refuses_turn_no_spawn(self, monkeypatch):
        """A refused turn raises the structured error BEFORE any subprocess
        spawn; the runner's terminal classification (ExitReason.EXCEPTION)
        carries ``exit_message = "BeltResolutionError: ..."`` to the session
        output path, so the reason is user-visible, not just logged."""
        from agent.session_runner.harness.claude import get_response_via_harness

        monkeypatch.setattr(app_settings, "toolbelts_enforce", True)
        spawn = AsyncMock()
        with patch("agent.session_runner.harness.claude._run_harness_subprocess", spawn):
            with pytest.raises(BeltResolutionError) as exc_info:
                await get_response_via_harness("hello", "/tmp", role="not-a-persona")
        assert spawn.await_count == 0
        assert "unresolvable" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_flag_off_never_calls_resolver(self, monkeypatch):
        monkeypatch.setattr(app_settings, "toolbelts_enforce", False)
        with patch(
            "agent.session_runner.belt_resolver.resolve_belt",
            side_effect=AssertionError("resolver must not be called when flag is off"),
        ):
            await self._run(role="pm")


# ---------------------------------------------------------------------------
# Race 3 — enforce-state stamp + skew telemetry
# ---------------------------------------------------------------------------


class TestBeltStateStamp:
    @pytest.fixture
    def session(self):
        from models.agent_session import AgentSession

        record = AgentSession.create(
            session_id="test-belt-stamp-session",
            project_key="test-belt-resolver",
            session_type="eng",
        )
        yield record
        for row in AgentSession.query.filter(project_key="test-belt-resolver"):
            row.delete()

    def test_first_stamp_emits_no_skew_event(self, session, monkeypatch):
        events = []
        monkeypatch.setattr(
            "agent.session_runner.belt_resolver.record_telemetry_event",
            lambda sid, event: events.append((sid, event)),
        )
        check_and_stamp_belt_state("test-belt-stamp-session", enforce=False, belt_version=None)
        assert events == []  # pre-belt legacy read (None) is not a mismatch

        from models.agent_session import AgentSession

        stamped = next(iter(AgentSession.query.filter(session_id="test-belt-stamp-session")))
        assert stamped.belt_enforce_state == "off"
        assert stamped.belt_version is None

    def test_mismatch_emits_warning_skew_event(self, session, monkeypatch):
        events = []
        monkeypatch.setattr(
            "agent.session_runner.belt_resolver.record_telemetry_event",
            lambda sid, event: events.append((sid, event)),
        )
        check_and_stamp_belt_state("test-belt-stamp-session", enforce=False, belt_version=None)
        check_and_stamp_belt_state("test-belt-stamp-session", enforce=True, belt_version=1)
        assert len(events) == 1
        sid, event = events[0]
        assert sid == "test-belt-stamp-session"
        assert event["type"] == BELT_SKEW_EVENT_TYPE
        assert event["level"] == "WARNING"
        assert event["prior_enforce_state"] == "off"
        assert event["current_enforce_state"] == "on"

        from models.agent_session import AgentSession

        stamped = next(iter(AgentSession.query.filter(session_id="test-belt-stamp-session")))
        assert stamped.belt_enforce_state == "on"
        assert stamped.belt_version == 1

    def test_stamp_is_fail_quiet(self, monkeypatch):
        """Never raises — telemetry and persistence failures are swallowed."""
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query",
            property(lambda self: (_ for _ in ()).throw(RuntimeError("redis down"))),
            raising=False,
        )
        check_and_stamp_belt_state("test-belt-anything", enforce=True, belt_version=1)


# ---------------------------------------------------------------------------
# Escalation — priming instruction + runner tag-and-forward
# ---------------------------------------------------------------------------


class TestEscalation:
    @pytest.mark.parametrize(
        "prime_file",
        [
            ".claude/commands/roles/prime-pm-role.md",
            ".claude/commands/roles/prime-dev-role.md",
            ".claude/commands/roles/prime-teammate-role.md",
        ],
    )
    def test_priming_skill_carries_escalation_instruction(self, prime_file: str):
        body = (REPO_ROOT / prime_file).read_text()
        assert ESCALATION_MARKER in body

    def test_forwards_tagged_lines_non_blocking(self):
        delivered, recorded = [], []
        forwarded: set[str] = set()
        text = (
            "Working on it.\n"
            f"{ESCALATION_MARKER} gh CLI unavailable — cannot query the PR\n"
            "Continuing with local state.\n"
        )
        lines = forward_capability_escalations(
            text, forwarded=forwarded, deliver=delivered.append, record=recorded.append
        )
        assert lines == [f"{ESCALATION_MARKER} gh CLI unavailable — cannot query the PR"]
        assert delivered == lines
        assert recorded[0]["type"] == "belt_escalation"

    def test_dedupes_across_turns(self):
        delivered = []
        forwarded: set[str] = set()
        line = f"{ESCALATION_MARKER} missing valor-tts"
        forward_capability_escalations(
            line, forwarded=forwarded, deliver=delivered.append, record=lambda _e: None
        )
        forward_capability_escalations(
            line, forwarded=forwarded, deliver=delivered.append, record=lambda _e: None
        )
        assert delivered == [line]

    def test_never_raises(self):
        """Non-blocking means fail-quiet: a broken deliver callback must not
        disturb the turn."""

        def _boom(_line: str) -> None:
            raise RuntimeError("channel down")

        forward_capability_escalations(
            f"{ESCALATION_MARKER} x",
            forwarded=set(),
            deliver=_boom,
            record=lambda _e: None,
        )

    def test_no_marker_no_side_effects(self):
        delivered = []
        forward_capability_escalations(
            "all good", forwarded=set(), deliver=delivered.append, record=lambda _e: None
        )
        assert delivered == []
