"""Unit tests for the `_warn_if_legacy_namespace` guard in `models/memory.py`.

Covers the legacy-namespace regression detector matrix per #1173 plan:
  - "dm"      -> WARNING with stack trace (retired namespace, #811)
  - "default" -> DEBUG with stack trace (legitimate but audit-tracked)
  - "valor"   -> no log (current namespace, expected)
  - None      -> no log
  - ""        -> no log

Also asserts the helper never raises (try/except wrapped) and that
`Memory.safe_save` invokes the helper only AFTER `save()` succeeds (#1173 C2).
"""

from __future__ import annotations

import logging

import pytest

from models.memory import _warn_if_legacy_namespace


class TestWarnIfLegacyNamespace:
    """Direct tests on the helper itself."""

    def test_dm_logs_warning_with_stack(self, caplog):
        with caplog.at_level(logging.WARNING, logger="models.memory"):
            _warn_if_legacy_namespace("dm")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "dm" in warnings[0].getMessage().lower()
        # logger.warning(..., stack_info=True) attaches a stack via record.stack_info
        assert warnings[0].stack_info is not None

    def test_default_logs_debug_with_stack(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="models.memory"):
            _warn_if_legacy_namespace("default")
        debugs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "default" in r.getMessage().lower()
        ]
        assert len(debugs) == 1
        assert debugs[0].stack_info is not None

    def test_default_does_not_log_at_warning(self, caplog):
        # Severity-differentiation guarantee from Risk 2 mitigation:
        # "default" must NOT emit at WARNING — only at DEBUG.
        with caplog.at_level(logging.WARNING, logger="models.memory"):
            _warn_if_legacy_namespace("default")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []

    def test_valor_is_noop(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="models.memory"):
            _warn_if_legacy_namespace("valor")
        # No record should mention legacy / retired / namespace
        relevant = [
            r
            for r in caplog.records
            if "retired" in r.getMessage() or "legitimate during bootstrap" in r.getMessage()
        ]
        assert relevant == []

    def test_none_is_noop(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="models.memory"):
            _warn_if_legacy_namespace(None)
        relevant = [
            r
            for r in caplog.records
            if "retired" in r.getMessage() or "legitimate during bootstrap" in r.getMessage()
        ]
        assert relevant == []

    def test_empty_string_is_noop(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="models.memory"):
            _warn_if_legacy_namespace("")
        relevant = [
            r
            for r in caplog.records
            if "retired" in r.getMessage() or "legitimate during bootstrap" in r.getMessage()
        ]
        assert relevant == []

    def test_helper_never_raises(self, monkeypatch):
        """Even if the logger blows up, the helper must not propagate."""
        import models.memory as mm

        class _ExplodingLogger:
            def warning(self, *a, **kw):
                raise RuntimeError("logger broken")

            def debug(self, *a, **kw):
                raise RuntimeError("logger broken")

        monkeypatch.setattr(mm, "logger", _ExplodingLogger())
        # Should not raise, even though the logger does
        _warn_if_legacy_namespace("dm")
        _warn_if_legacy_namespace("default")
        _warn_if_legacy_namespace("valor")
        _warn_if_legacy_namespace(None)


class TestSafeSaveInvokesGuardAfterSave:
    """Integration test: `Memory.safe_save` calls `_warn_if_legacy_namespace` only
    after `save()` succeeds (#1173 C2)."""

    def test_warn_called_after_successful_save(self, monkeypatch):
        from models import memory as mm

        calls = []

        def _stub_warn(pk):
            calls.append(("warn", pk))

        # Replace the helper with our stub
        monkeypatch.setattr(mm, "_warn_if_legacy_namespace", _stub_warn)

        # Stub save() to "succeed" (anything other than False) without touching Redis
        def _fake_save(self):
            calls.append(("save", self.project_key))
            return self

        monkeypatch.setattr(mm.Memory, "save", _fake_save, raising=False)

        result = mm.Memory.safe_save(agent_id="test", project_key="dm", content="x", source="human")
        # save() must run BEFORE warn (#1173 C2)
        assert calls == [("save", "dm"), ("warn", "dm")]
        assert result is not None

    def test_warn_not_called_when_writefilter_drops(self, monkeypatch):
        from models import memory as mm

        calls = []

        def _stub_warn(pk):
            calls.append(("warn", pk))

        monkeypatch.setattr(mm, "_warn_if_legacy_namespace", _stub_warn)

        # WriteFilterMixin returns False to indicate the record was filtered out
        def _filtered_save(self):
            calls.append(("save", self.project_key))
            return False

        monkeypatch.setattr(mm.Memory, "save", _filtered_save, raising=False)

        result = mm.Memory.safe_save(
            agent_id="test", project_key="dm", content="x", source="human", importance=0.01
        )
        # warn must NOT fire on filtered-out writes (#1173 C2)
        assert ("warn", "dm") not in calls
        assert result is None

    def test_warn_not_called_when_save_raises(self, monkeypatch):
        from models import memory as mm

        calls = []

        def _stub_warn(pk):
            calls.append(("warn", pk))

        monkeypatch.setattr(mm, "_warn_if_legacy_namespace", _stub_warn)

        def _raising_save(self):
            raise RuntimeError("simulated save failure")

        monkeypatch.setattr(mm.Memory, "save", _raising_save, raising=False)

        # safe_save must swallow the exception
        result = mm.Memory.safe_save(agent_id="test", project_key="dm", content="x", source="human")
        assert result is None
        # No warn fired because save() did not succeed
        assert ("warn", "dm") not in calls


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
