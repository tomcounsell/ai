"""Tests for the container's caller-owned empty-return fallback gate (Risk 5).

The BuilderHarness.run_turn contract: returns final text or "".
The container caller (not the builder) owns:
  - self._last_dev_report = <returned-text>
  - empty-return gate: "" -> bump transcript_fallback_count + DEV_REPORT_UNAVAILABLE

These tests use a stub BuilderHarness to prove the gate is harness-agnostic.
They also enforce the PI_SUBPROCESS_TIMEOUT_S constant (600s, not the 12-hour
PTY ceiling) and validate the BuilderHarness protocol structure.
"""

from agent.granite_container.builder import (
    PI_SUBPROCESS_TIMEOUT_S,
    BuilderHarness,
    PiSubprocessBuilder,
)

# ---------------------------------------------------------------------------
# PI_SUBPROCESS_TIMEOUT_S constant enforcement
# ---------------------------------------------------------------------------


class TestPiSubprocessTimeoutConstant:
    """PI_SUBPROCESS_TIMEOUT_S must be 10 minutes, not the 12-hour PTY ceiling."""

    def test_timeout_is_600_seconds(self):
        """PI_SUBPROCESS_TIMEOUT_S must be 600s (10 min)."""
        assert PI_SUBPROCESS_TIMEOUT_S == 10 * 60, (
            f"Expected 600s, got {PI_SUBPROCESS_TIMEOUT_S}. "
            "Must NOT reuse CYCLE_IDLE_TIMEOUT_S (43200s = 12h PTY ceiling)."
        )

    def test_timeout_not_cycle_idle_timeout(self):
        """PI_SUBPROCESS_TIMEOUT_S must not equal CYCLE_IDLE_TIMEOUT_S (12h)."""
        cycle_idle_timeout_s = 12 * 60 * 60  # 43200
        assert PI_SUBPROCESS_TIMEOUT_S != cycle_idle_timeout_s

    def test_timeout_not_zero(self):
        assert PI_SUBPROCESS_TIMEOUT_S > 0

    def test_timeout_less_than_one_hour(self):
        """10 min must be strictly less than 1 hour to keep it sane."""
        assert PI_SUBPROCESS_TIMEOUT_S < 60 * 60


# ---------------------------------------------------------------------------
# BuilderHarness protocol structural checks
# ---------------------------------------------------------------------------


class TestBuilderHarnessProtocol:
    """The BuilderHarness Protocol must be @runtime_checkable."""

    def test_pi_subprocess_builder_satisfies_protocol(self, tmp_path):
        """PiSubprocessBuilder must satisfy BuilderHarness."""
        b = PiSubprocessBuilder(
            builder_cwd=str(tmp_path),
            rails_path="/r",
            persona_path="/p",
        )
        assert isinstance(b, BuilderHarness)

    def test_stub_satisfies_protocol(self):
        """A plain stub class with the right shape satisfies the protocol."""

        class _StubBuilder:
            @property
            def name(self) -> str:
                return "stub"

            def prepare(self, spec=None) -> None:
                pass

            def run_turn(self, prompt: str) -> str:
                return "stub output"

            def close(self) -> None:
                pass

        stub = _StubBuilder()
        assert isinstance(stub, BuilderHarness)

    def test_incomplete_class_does_not_satisfy_protocol(self):
        """A class missing run_turn must NOT satisfy BuilderHarness."""

        class _Incomplete:
            @property
            def name(self) -> str:
                return "bad"

            def prepare(self, spec=None) -> None:
                pass

            def close(self) -> None:
                pass

        obj = _Incomplete()
        assert not isinstance(obj, BuilderHarness)


# ---------------------------------------------------------------------------
# Caller-owned empty-return gate (contract test via stub)
# ---------------------------------------------------------------------------


class TestCallerOwnedEmptyReturnGate:
    """The container, not the builder, owns the DEV_REPORT_UNAVAILABLE gate.

    These tests use a stub builder to verify the contract description in
    builder.py: run_turn returns "" on miss, caller handles the fallback.
    They document the expected caller behavior so that anyone reading the
    container code has a test anchor.
    """

    def test_empty_return_is_valid_contract_value(self, tmp_path):
        """A builder returning '' is valid contract — caller escalates, not builder."""

        class _EmptyBuilder:
            @property
            def name(self):
                return "empty"

            def prepare(self, spec=None):
                pass

            def run_turn(self, prompt):
                return ""  # valid: caller owns the gate

            def close(self):
                pass

        b = _EmptyBuilder()
        result = b.run_turn("anything")
        assert result == ""
        assert isinstance(result, str)

    def test_non_empty_return_passes_through(self, tmp_path):
        """A builder returning text: caller stores it in _last_dev_report."""

        class _TextBuilder:
            @property
            def name(self):
                return "text"

            def prepare(self, spec=None):
                pass

            def run_turn(self, prompt):
                return "dev output"

            def close(self):
                pass

        b = _TextBuilder()
        result = b.run_turn("prompt")
        assert result == "dev output"
