"""Tests for the forked ``_write_promise_audit`` helper (cycle-2 C-NEW-2).

The promise gate uses a forked audit helper (``_write_promise_audit``)
instead of reusing ``bridge.message_drafter._write_classification_audit``
because ``PromiseVerdict`` does not have ``output_type`` or ``confidence``
fields. The forked helper writes JSONL entries with verdict-specific
fields and ``kind="promise_gate"`` to the SAME file
(``logs/classification_audit.jsonl``) for unified observability.

Plan: docs/plans/sdlc-1219.md (issue #1219).
"""

from __future__ import annotations

import json

import pytest

import bridge.promise_gate as promise_gate
from bridge.promise_gate import PromiseVerdict, _write_promise_audit

pytestmark = [pytest.mark.unit, pytest.mark.sdlc]


class TestWritePromiseAuditShape:
    def test_writes_jsonl_with_kind_promise_gate(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        verdict = PromiseVerdict(
            action="block", reason="forward-deferral", class_="forward_deferral"
        )
        _write_promise_audit(
            "I'll come back with X",
            verdict,
            transport="telegram",
            session_id="cli-123",
            source="promise_gate_llm",
        )

        assert log_path.exists()
        line = log_path.read_text().strip()
        entry = json.loads(line)
        assert entry["kind"] == "promise_gate"
        assert entry["action"] == "block"
        assert entry["reason"] == "forward-deferral"
        assert entry["class_"] == "forward_deferral"
        assert entry["transport"] == "telegram"
        assert entry["session_id"] == "cli-123"
        assert entry["source"] == "promise_gate_llm"
        assert "ts" in entry
        assert entry["text_preview"] == "I'll come back with X"

    def test_does_not_contain_output_type_or_confidence(self, tmp_path, monkeypatch):
        """Cycle-2 C-NEW-2: forked helper avoids the misleading reuse of drafter audit fields."""
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        verdict = PromiseVerdict(action="allow", reason="test")
        _write_promise_audit(
            "any",
            verdict,
            transport="telegram",
            session_id=None,
            source="promise_gate_llm",
        )
        entry = json.loads(log_path.read_text().strip())
        assert "output_type" not in entry
        assert "confidence" not in entry

    def test_text_preview_truncated_to_200_chars(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        long_text = "x" * 500
        _write_promise_audit(
            long_text,
            PromiseVerdict(action="allow", reason="test"),
            transport="telegram",
            session_id=None,
            source="promise_gate_llm",
        )
        entry = json.loads(log_path.read_text().strip())
        assert len(entry["text_preview"]) == 200

    def test_class_field_is_none_for_allow(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        verdict = PromiseVerdict(action="allow", reason="ok")
        _write_promise_audit(
            "honest",
            verdict,
            transport="telegram",
            session_id=None,
            source="promise_gate_llm",
        )
        entry = json.loads(log_path.read_text().strip())
        assert entry["class_"] is None

    def test_audit_helper_is_independent_of_drafter(self):
        """The promise-gate audit helper stands alone — no drafter dependency.

        History: this helper was originally forked from the drafter's
        ``_write_classification_audit``. Commit ef452704 (#1685) repositioned
        the drafter as a verbatim pass-through and deleted its entire
        classification cluster, including that audit helper. The fork now IS
        the sole audit writer: assert it lives in ``bridge.promise_gate``
        without importing anything from ``bridge.message_drafter``, and that
        the drafter no longer exposes a classification-audit helper.
        """
        import ast
        import inspect

        import bridge.message_drafter as message_drafter

        assert callable(_write_promise_audit)
        assert _write_promise_audit.__module__ == "bridge.promise_gate"
        # Removed by #1685 — the promise gate's fork is the only audit writer.
        assert not hasattr(message_drafter, "_write_classification_audit")

        # The promise gate module must not import the drafter (independence).
        tree = ast.parse(inspect.getsource(promise_gate))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert "bridge.message_drafter" not in imported


class TestWritePromiseAuditFailureSwallowing:
    def test_failure_is_silent(self, monkeypatch, tmp_path):
        # Point at a path inside a non-writable location; ensure we don't raise.
        bad_path = tmp_path / "nonexistent_subdir" / "audit.jsonl"
        # Make the parent unwriteable by making it a file instead of a dir.
        (tmp_path / "nonexistent_subdir").write_text("not a directory")
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", bad_path)

        # Must not raise even though the path is unwritable.
        _write_promise_audit(
            "any",
            PromiseVerdict(action="allow", reason="test"),
            transport="telegram",
            session_id=None,
            source="promise_gate_llm",
        )
