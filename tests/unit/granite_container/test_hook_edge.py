"""Unit tests for the granite hook edge channel (plan #1688, Task 1).

Covers the three pieces of ``agent/granite_container/hook_edge.py`` plus the
fail-silent forwarder in ``hook_forwarder.py``:

- ``generate_hook_settings`` — settings JSON registers every target hook to the
  forwarder; the edge path is reserved (Race 1).
- The forwarder — fail-silent (no env / bad payload / unwritable path → exit 0,
  no crash), atomic single-line append, envelope shape.
- ``HookEdgeConsumer`` — subagent filtering (Practice 5), compaction (Practice
  8), needs-human, corrupt/partial line skipping, level-triggered read (Race 1),
  and the durable cursor's idempotency + truncation reset (Practice 4 / Risk 3).

All deterministic, sub-second, no ollama, no real ``claude`` spawn.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.granite_container import hook_edge
from agent.granite_container.hook_edge import (
    COMPACTION,
    NEEDS_HUMAN,
    SUBAGENT_END,
    TURN_END,
    HookCursor,
    HookEdgeConsumer,
    generate_hook_settings,
)

_FORWARDER = str(Path(hook_edge.__file__).resolve().parent / "hook_forwarder.py")


def _envelope_line(event: str, **payload) -> str:
    payload.setdefault("hook_event_name", event)
    return json.dumps({"ts": 1.0, "event": event, "payload": payload}) + "\n"


class TestGenerateHookSettings(unittest.TestCase):
    def test_registers_all_target_hooks_to_forwarder(self) -> None:
        with TemporaryDirectory() as d:
            edge = Path(d) / "edges.ndjson"
            settings_path, edge_path = generate_hook_settings(d, edge)
            data = json.loads(Path(settings_path).read_text())
            hooks = data["hooks"]
            for event in (
                "Stop",
                "SubagentStop",
                "Notification",
                "PermissionRequest",
                "PreToolUse",
                "PreCompact",
                "SessionStart",
            ):
                self.assertIn(event, hooks, f"{event} hook must be registered")
                cmd = hooks[event][0]["hooks"][0]["command"]
                self.assertIn("hook_forwarder.py", cmd)
            # PreToolUse is narrowed to AskUserQuestion.
            self.assertEqual(hooks["PreToolUse"][0]["matcher"], "AskUserQuestion")

    def test_reserves_edge_path_before_first_write(self) -> None:
        """Race 1: the edge file exists after generate (before any PTY write)."""
        with TemporaryDirectory() as d:
            edge = Path(d) / "sub" / "edges.ndjson"
            _, edge_path = generate_hook_settings(Path(d) / "settings", edge)
            self.assertTrue(Path(edge_path).exists())

    def test_does_not_truncate_existing_edge_file(self) -> None:
        with TemporaryDirectory() as d:
            edge = Path(d) / "edges.ndjson"
            edge.write_text(_envelope_line("Stop"))
            generate_hook_settings(d, edge)
            self.assertTrue(edge.read_text(), "existing edges must survive")


class TestHookForwarder(unittest.TestCase):
    """The forwarder is a self-contained fail-silent script."""

    def _run_forwarder(self, stdin: str, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, _FORWARDER],
            input=stdin,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
        )

    def test_appends_envelope_for_valid_payload(self) -> None:
        with TemporaryDirectory() as d:
            edge = Path(d) / "edges.ndjson"
            payload = {"hook_event_name": "Stop", "transcript_path": "/x.jsonl"}
            r = self._run_forwarder(json.dumps(payload), {hook_edge.EDGE_FILE_ENV: str(edge)})
            self.assertEqual(r.returncode, 0)
            lines = edge.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            env = json.loads(lines[0])
            self.assertEqual(env["event"], "Stop")
            self.assertEqual(env["payload"]["transcript_path"], "/x.jsonl")

    def test_no_env_var_exits_zero_no_write(self) -> None:
        r = self._run_forwarder(json.dumps({"hook_event_name": "Stop"}), {})
        # No GRANITE_HOOK_EDGE_FILE — nothing written, clean exit.
        env = {k: v for k, v in os.environ.items()}
        env.pop(hook_edge.EDGE_FILE_ENV, None)
        r = subprocess.run(
            [sys.executable, _FORWARDER],
            input=json.dumps({"hook_event_name": "Stop"}),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0)

    def test_malformed_payload_exits_zero_records_unparseable(self) -> None:
        with TemporaryDirectory() as d:
            edge = Path(d) / "edges.ndjson"
            r = self._run_forwarder("this is not json{{{", {hook_edge.EDGE_FILE_ENV: str(edge)})
            self.assertEqual(r.returncode, 0)
            # A line is written but the consumer will ignore it (no event).
            self.assertTrue(edge.exists())

    def test_empty_stdin_exits_zero(self) -> None:
        with TemporaryDirectory() as d:
            edge = Path(d) / "edges.ndjson"
            r = self._run_forwarder("", {hook_edge.EDGE_FILE_ENV: str(edge)})
            self.assertEqual(r.returncode, 0)

    def test_unwritable_edge_path_exits_zero(self) -> None:
        # A path under a non-existent parent dir is unwritable; must not crash.
        r = self._run_forwarder(
            json.dumps({"hook_event_name": "Stop"}),
            {hook_edge.EDGE_FILE_ENV: "/nonexistent-dir-xyz/edges.ndjson"},
        )
        self.assertEqual(r.returncode, 0)


class TestHookEdgeConsumerClassification(unittest.TestCase):
    def _consumer_with(self, lines: list[str]) -> HookEdgeConsumer:
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        edge.write_text("".join(lines))
        return HookEdgeConsumer(edge, session_id="s1")

    def test_parent_stop_is_turn_end(self) -> None:
        c = self._consumer_with([_envelope_line("Stop", transcript_path="/t.jsonl")])
        edges = c.poll()
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].kind, TURN_END)
        self.assertEqual(edges[0].transcript_path, "/t.jsonl")

    def test_subagent_stop_is_not_turn_end(self) -> None:
        """Practice 5: SubagentStop must NOT end the parent turn."""
        c = self._consumer_with(
            [_envelope_line("SubagentStop", agent_id="a1", agent_type="general-purpose")]
        )
        edges = c.poll()
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].kind, SUBAGENT_END)
        self.assertEqual(edges[0].agent_id, "a1")
        self.assertNotEqual(edges[0].kind, TURN_END)

    def test_interleaved_subagent_and_parent_stop(self) -> None:
        """Race 3: a SubagentStop before the parent Stop yields exactly one turn_end."""
        c = self._consumer_with(
            [
                _envelope_line("SubagentStop", agent_id="a1"),
                _envelope_line("Stop", transcript_path="/t.jsonl"),
            ]
        )
        edges = c.poll()
        kinds = [e.kind for e in edges]
        self.assertEqual(kinds, [SUBAGENT_END, TURN_END])
        self.assertEqual(sum(k == TURN_END for k in kinds), 1)

    def test_notification_is_needs_human(self) -> None:
        c = self._consumer_with([_envelope_line("Notification")])
        self.assertEqual(c.poll()[0].kind, NEEDS_HUMAN)

    def test_permission_request_is_needs_human(self) -> None:
        c = self._consumer_with([_envelope_line("PermissionRequest")])
        self.assertEqual(c.poll()[0].kind, NEEDS_HUMAN)

    def test_ask_user_question_pretooluse_is_needs_human(self) -> None:
        c = self._consumer_with([_envelope_line("PreToolUse", tool_name="AskUserQuestion")])
        self.assertEqual(c.poll()[0].kind, NEEDS_HUMAN)

    def test_ordinary_pretooluse_is_ignored(self) -> None:
        c = self._consumer_with([_envelope_line("PreToolUse", tool_name="Bash")])
        self.assertEqual(c.poll(), [])

    def test_precompact_is_compaction_not_turn_end(self) -> None:
        """Practice 8: compaction is forwarded, never mistaken for completion."""
        c = self._consumer_with([_envelope_line("PreCompact")])
        edges = c.poll()
        self.assertEqual(edges[0].kind, COMPACTION)

    def test_compact_sourced_session_start_is_compaction(self) -> None:
        c = self._consumer_with([_envelope_line("SessionStart", source="compact")])
        self.assertEqual(c.poll()[0].kind, COMPACTION)

    def test_fresh_session_start_is_ignored(self) -> None:
        c = self._consumer_with([_envelope_line("SessionStart", source="startup")])
        self.assertEqual(c.poll(), [])


class TestHookEdgeConsumerFailSilent(unittest.TestCase):
    def test_garbage_line_skipped_cursor_advances(self) -> None:
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        edge.write_text("not json at all\n" + _envelope_line("Stop", transcript_path="/t.jsonl"))
        c = HookEdgeConsumer(edge, session_id="s1")
        edges = c.poll()
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].kind, TURN_END)
        # Both lines consumed (garbage skipped, valid parsed).
        self.assertEqual(c.cursor.event_cursor, 2)

    def test_partial_trailing_line_deferred(self) -> None:
        """A torn (non-newline-terminated) trailing line waits for the next poll."""
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        full = _envelope_line("Stop", transcript_path="/t.jsonl")
        partial = json.dumps({"ts": 2.0, "event": "SubagentStop", "payload": {}})  # no \n
        edge.write_text(full + partial)
        c = HookEdgeConsumer(edge, session_id="s1")
        edges = c.poll()
        self.assertEqual([e.kind for e in edges], [TURN_END])
        # Now complete the partial line; the next poll picks it up.
        with open(edge, "a") as f:
            f.write("\n")
        edges2 = c.poll()
        self.assertEqual([e.kind for e in edges2], [SUBAGENT_END])

    def test_missing_file_returns_empty(self) -> None:
        c = HookEdgeConsumer("/no/such/edge/file.ndjson", session_id="s1")
        self.assertEqual(c.poll(), [])


class TestHookCursorDurability(unittest.TestCase):
    def test_no_double_delivery_across_polls(self) -> None:
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        edge.write_text(_envelope_line("Stop", transcript_path="/t.jsonl"))
        c = HookEdgeConsumer(edge, session_id="s1")
        self.assertEqual(len(c.poll()), 1)
        # Second poll with no new bytes returns nothing (idempotent).
        self.assertEqual(c.poll(), [])
        # Append a new edge; only that one is delivered.
        with open(edge, "a") as f:
            f.write(_envelope_line("Notification"))
        edges = c.poll()
        self.assertEqual([e.kind for e in edges], [NEEDS_HUMAN])

    def test_cursor_restore_resumes_without_replay(self) -> None:
        """Restoring a persisted cursor replays only unseen edges (worker restart)."""
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        edge.write_text(_envelope_line("Stop", transcript_path="/t.jsonl"))
        c1 = HookEdgeConsumer(edge, session_id="s1")
        c1.poll()
        saved = c1.cursor.to_dict()
        # Simulate a restart: append a new edge, restore the cursor.
        with open(edge, "a") as f:
            f.write(_envelope_line("Notification"))
        c2 = HookEdgeConsumer(edge, session_id="s1", cursor=HookCursor.from_dict(saved))
        edges = c2.poll()
        self.assertEqual([e.kind for e in edges], [NEEDS_HUMAN], "must not re-deliver the Stop")

    def test_truncation_resets_cursor(self) -> None:
        """Risk 3: a truncated/replaced file is detected via the fingerprint."""
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        edge.write_text(
            _envelope_line("Stop", transcript_path="/a.jsonl") + _envelope_line("Notification")
        )
        c = HookEdgeConsumer(edge, session_id="s1")
        c.poll()
        old_offset = c.cursor.byte_offset
        self.assertGreater(old_offset, 0)
        # Replace the file with fresh, different content (log rotation / reuse).
        edge.write_text(_envelope_line("Stop", transcript_path="/fresh.jsonl"))
        edges = c.poll()
        # The fresh Stop is delivered (offset reset because head changed).
        self.assertEqual([e.kind for e in edges], [TURN_END])
        self.assertEqual(edges[0].transcript_path, "/fresh.jsonl")

    def test_drain_latest_returns_last_matching(self) -> None:
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        edge = Path(d.name) / "edges.ndjson"
        edge.write_text(
            _envelope_line("SubagentStop", agent_id="a1")
            + _envelope_line("Stop", transcript_path="/t.jsonl")
        )
        c = HookEdgeConsumer(edge, session_id="s1")
        te = c.drain_latest(TURN_END)
        self.assertIsNotNone(te)
        self.assertEqual(te.transcript_path, "/t.jsonl")


class TestForwarderImportable(unittest.TestCase):
    """The forwarder must import cleanly (no intra-package deps)."""

    def test_forwarder_has_no_package_imports(self) -> None:
        src = Path(_FORWARDER).read_text()
        self.assertNotIn("from agent", src)
        self.assertNotIn("import agent", src)

    def test_forwarder_build_envelope_smoke(self) -> None:
        mod = runpy.run_path(_FORWARDER)
        env = mod["_build_envelope"](json.dumps({"hook_event_name": "Stop"}))
        self.assertEqual(env["event"], "Stop")


if __name__ == "__main__":
    unittest.main()
