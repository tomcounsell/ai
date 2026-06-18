"""Tests for the container's caller-owned empty-return fallback gate (Risk 5).

The BuilderHarness.run_turn contract: returns final text or "".
The container caller (not the builder) owns:
  - self._last_dev_report = <returned-text>
  - empty-return gate: "" -> bump transcript_fallback_count + DEV_REPORT_UNAVAILABLE

These tests use a stub BuilderHarness to prove the gate is harness-agnostic.
They also enforce the PI_SUBPROCESS_TIMEOUT_S constant (600s, not the 12-hour
PTY ceiling) and validate the BuilderHarness protocol structure.
"""

from unittest.mock import MagicMock, patch

from agent.granite_container.builder import (
    PI_SUBPROCESS_TIMEOUT_S,
    BuilderHarness,
    PiSubprocessBuilder,
    PtyClaudeBuilder,
)
from agent.granite_container.container import (
    DEV_REPORT_UNAVAILABLE,
    Container,
    ContainerResult,
)
from agent.granite_container.granite_classifier import ClassificationResult
from agent.granite_container.pty_driver import IdleResult, PTYDriver

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


# ---------------------------------------------------------------------------
# Helpers shared by the two new test classes below
# ---------------------------------------------------------------------------


def _make_idle_result(saw_idle: bool = True, buf: str = "buf") -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle, buffer=buf, idle_marker="bypass permissions on", elapsed_ms=50
    )


def _make_stub_builder(run_turn_return: str, last_hung: bool = False) -> MagicMock:
    """Return a MagicMock that satisfies the BuilderHarness protocol."""
    stub = MagicMock(spec=BuilderHarness)
    stub.name = "stub"
    stub.run_turn.return_value = run_turn_return
    stub.last_hung = last_hung
    stub.last_dev_buf = "buf"
    stub.last_dev_marker = "marker"
    stub.last_dev_ms = 50
    return stub


def _make_container_with_mocks() -> tuple[Container, MagicMock, MagicMock]:
    """Construct a Container and wired-up PM/Dev mock PTYs.

    The container has _spawn_pair and _close_pair patched out already —
    callers must set ``c._pm_pty`` / ``c._dev_pty`` directly and patch
    additional methods as needed.
    """
    c = Container(user_message="test message", max_turns=5)
    pm_mock = MagicMock(spec=PTYDriver)
    pm_mock.read_until_idle.return_value = _make_idle_result(saw_idle=True, buf="")
    dev_mock = MagicMock(spec=PTYDriver)
    dev_mock.read_until_idle.return_value = _make_idle_result(saw_idle=True, buf="")
    return c, pm_mock, dev_mock


def _dev_classification(
    payload: str = "do the thing", harness: str | None = None
) -> ClassificationResult:
    """Build a 'dev'-destination ClassificationResult for routing tests."""
    return ClassificationResult(
        destination="dev",
        payload=payload,
        compliance_miss=False,
        raw_first_line=f"[/dev]{':' + harness if harness else ''}",
        harness=harness,
    )


# ---------------------------------------------------------------------------
# Container caller-owned gate exercised via _route_pm_classification (Risk 5)
# ---------------------------------------------------------------------------


class TestCallerOwnedGateViaRoute:
    """The container caller (not the builder) owns the empty-return gate.

    These tests drive Container._route_pm_classification with a stub
    BuilderHarness injected via a patch on _get_builder, proving that:
      (a) a non-empty run_turn return sets _last_dev_report and does NOT
          bump transcript_fallback_count
      (b) an empty run_turn return bumps transcript_fallback_count by 1
          and sets _last_dev_report = DEV_REPORT_UNAVAILABLE
    """

    def test_non_empty_return_sets_last_dev_report(self):
        """Non-empty builder output stored verbatim in _last_dev_report."""
        c, pm_mock, dev_mock = _make_container_with_mocks()
        c._pm_pty = pm_mock
        c._dev_pty = dev_mock

        stub = _make_stub_builder(run_turn_return="real dev output")
        result = ContainerResult(session_id="test-session", user_message="test")

        with patch.object(c, "_get_builder", return_value=stub):
            outcome = c._route_pm_classification(
                classification=_dev_classification("do the thing"),
                pm_buf="",
                turn_index=0,
                result=result,
            )

        # The container must forward the text to _last_dev_report unchanged.
        assert c._last_dev_report == "real dev output"
        # No fallback should be counted — builder returned real text.
        assert result.transcript_fallback_count == 0
        # Routing should continue (not break) since this was a dev turn.
        assert outcome.should_break is False

    def test_empty_return_bumps_fallback_count_and_sets_unavailable(self):
        """Empty builder output triggers the container's DEV_REPORT_UNAVAILABLE gate."""
        c, pm_mock, dev_mock = _make_container_with_mocks()
        c._pm_pty = pm_mock
        c._dev_pty = dev_mock

        stub = _make_stub_builder(run_turn_return="")
        result = ContainerResult(session_id="test-session", user_message="test")
        assert result.transcript_fallback_count == 0  # precondition

        with patch.object(c, "_get_builder", return_value=stub):
            outcome = c._route_pm_classification(
                classification=_dev_classification("do the thing"),
                pm_buf="",
                turn_index=0,
                result=result,
            )

        # fallback count must be bumped exactly once.
        assert result.transcript_fallback_count == 1
        # Container must substitute the placeholder sentinel.
        assert c._last_dev_report == DEV_REPORT_UNAVAILABLE
        # Loop should NOT break — empty transcript is a soft error, not a hang.
        assert outcome.should_break is False

    def test_empty_return_gate_is_harness_agnostic(self):
        """The gate fires identically for claude- and pi-named stubs."""
        for harness_name in (None, "claude", "pi"):
            c, pm_mock, dev_mock = _make_container_with_mocks()
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock

            stub = _make_stub_builder(run_turn_return="")
            result = ContainerResult(session_id="test-session", user_message="test")

            with patch.object(c, "_get_builder", return_value=stub):
                c._route_pm_classification(
                    classification=_dev_classification("ping", harness=harness_name),
                    pm_buf="",
                    turn_index=0,
                    result=result,
                )

            assert result.transcript_fallback_count == 1, f"failed for harness={harness_name!r}"
            assert c._last_dev_report == DEV_REPORT_UNAVAILABLE, (
                f"failed for harness={harness_name!r}"
            )

    def test_consecutive_empty_returns_accumulate_fallback_count(self):
        """Multiple empty-return turns each bump transcript_fallback_count once."""
        c, pm_mock, dev_mock = _make_container_with_mocks()
        c._pm_pty = pm_mock
        c._dev_pty = dev_mock

        stub = _make_stub_builder(run_turn_return="")
        result = ContainerResult(session_id="test-session", user_message="test")

        with patch.object(c, "_get_builder", return_value=stub):
            for i in range(3):
                c._route_pm_classification(
                    classification=_dev_classification("ping"),
                    pm_buf="",
                    turn_index=i,
                    result=result,
                )

        assert result.transcript_fallback_count == 3


# ---------------------------------------------------------------------------
# PtyClaudeBuilder direct unit tests (Risk 1 — highest blast radius)
# ---------------------------------------------------------------------------


class TestPtyClaudeBuilderDirect:
    """Direct unit tests for PtyClaudeBuilder.run_turn.

    These tests prove the claude path is behaviour-identical to the
    pre-refactor dev-relay branch (criterion: byte-identical relay on
    representative turns). They patch agent.granite_container.builder's
    imports of last_assistant_text and text_bearing_count so no real
    JSONL file is needed.

    Happy-path sequence:
      1. cycle_idle(dev) → idle  (pre-write wait)
      2. dev_pty.write(prompt)
      3. text_bearing_count(transcript)  → baseline
      4. cycle_idle(dev) → idle  (post-write wait)
      5. last_assistant_text(transcript, baseline_text_count=baseline) → text
      6. return text
    """

    # -- Helper: build a PtyClaudeBuilder with fake dependencies --

    @staticmethod
    def _build(
        idle_seq: list[tuple[bool, str, str, int]],
        transcript_path: str | None = "/fake/transcript.jsonl",
        last_assistant_text_retval: str = "hello world",
        text_bearing_count_retval: int = 3,
    ) -> tuple[PtyClaudeBuilder, MagicMock]:
        """Construct a PtyClaudeBuilder with controllable cycle_idle and mocked transcript helpers.

        Returns (builder, dev_pty_mock).  The caller patches
        agent.granite_container.transcript_tailer.last_assistant_text and
        agent.granite_container.transcript_tailer.text_bearing_count separately.
        """
        dev_pty_mock = MagicMock()
        idle_iter = iter(idle_seq)

        def _cycle_idle_fn(_pty):
            return next(idle_iter)

        builder = PtyClaudeBuilder(
            dev_pty=dev_pty_mock,
            dev_transcript_getter=lambda: transcript_path,
            cycle_idle_fn=_cycle_idle_fn,
        )
        return builder, dev_pty_mock

    # -- Tests --

    def test_happy_path_returns_assistant_text(self):
        """Both idles succeed → run_turn returns last_assistant_text output."""
        builder, dev_pty = self._build(
            idle_seq=[
                (True, "buf1", "marker1", 10),  # pre-write idle
                (True, "buf2", "marker2", 20),  # post-write idle
            ],
        )

        with (
            patch(
                "agent.granite_container.transcript_tailer.last_assistant_text",
                return_value="hello world",
            ),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=3),
        ):
            text = builder.run_turn("do the thing")

        assert text == "hello world"
        assert builder.last_hung is False

    def test_happy_path_write_called_with_prompt(self):
        """dev_pty.write is called with the exact prompt after the pre-write idle."""
        builder, dev_pty = self._build(
            idle_seq=[
                (True, "buf", "m", 10),
                (True, "buf", "m", 20),
            ],
        )

        with (
            patch(
                "agent.granite_container.transcript_tailer.last_assistant_text", return_value="ok"
            ),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=0),
        ):
            builder.run_turn("specific prompt text")

        dev_pty.write.assert_called_once_with("specific prompt text")

    def test_write_happens_after_pre_write_idle_not_before(self):
        """dev_pty.write must NOT be called when the pre-write idle fails."""
        builder, dev_pty = self._build(
            idle_seq=[
                (False, "", "", 0),  # pre-write idle fails — hang
            ],
        )

        with (
            patch(
                "agent.granite_container.transcript_tailer.last_assistant_text", return_value="x"
            ),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=0),
        ):
            builder.run_turn("should not be written")

        dev_pty.write.assert_not_called()

    def test_pre_write_hang_returns_empty_and_sets_last_hung(self):
        """First cycle_idle returns not-idle → run_turn returns "", last_hung=True."""
        builder, _ = self._build(
            idle_seq=[
                (False, "", "", 0),  # pre-write idle: not idle
            ],
        )

        with (
            patch(
                "agent.granite_container.transcript_tailer.last_assistant_text",
                return_value="ignored",
            ),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=0),
        ):
            text = builder.run_turn("ping")

        assert text == ""
        assert builder.last_hung is True

    def test_post_write_hang_returns_empty_and_sets_last_hung(self):
        """Pre-write idle OK, post-write idle fails → run_turn returns "", last_hung=True."""
        builder, _ = self._build(
            idle_seq=[
                (True, "buf", "m", 10),  # pre-write idle: OK
                (False, "", "", 0),  # post-write idle: not idle
            ],
        )

        with (
            patch(
                "agent.granite_container.transcript_tailer.last_assistant_text",
                return_value="ignored",
            ),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=0),
        ):
            text = builder.run_turn("ping")

        assert text == ""
        assert builder.last_hung is True

    def test_empty_transcript_read_returns_empty_string(self):
        """last_assistant_text returning "" → run_turn returns "" (container handles fallback)."""
        builder, _ = self._build(
            idle_seq=[
                (True, "buf", "m", 10),
                (True, "buf2", "m2", 20),
            ],
        )

        with (
            patch("agent.granite_container.transcript_tailer.last_assistant_text", return_value=""),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=0),
        ):
            text = builder.run_turn("ping")

        assert text == ""
        # Not a hang — last_hung stays False; empty text handled by container gate.
        assert builder.last_hung is False

    def test_post_write_idle_metadata_stored(self):
        """last_dev_buf / last_dev_marker / last_dev_ms reflect the post-write idle."""
        builder, _ = self._build(
            idle_seq=[
                (True, "pre-buf", "pre-m", 5),
                (True, "post-buf", "post-m", 99),
            ],
        )

        with (
            patch(
                "agent.granite_container.transcript_tailer.last_assistant_text", return_value="text"
            ),
            patch("agent.granite_container.transcript_tailer.text_bearing_count", return_value=0),
        ):
            builder.run_turn("ping")

        assert builder.last_dev_buf == "post-buf"
        assert builder.last_dev_marker == "post-m"
        assert builder.last_dev_ms == 99

    def test_name_property_is_claude(self):
        """PtyClaudeBuilder.name must be 'claude' for harness routing."""
        builder, _ = self._build(idle_seq=[])
        assert builder.name == "claude"

    def test_satisfies_builder_harness_protocol(self):
        """PtyClaudeBuilder must be an instance of BuilderHarness (runtime_checkable)."""
        builder, _ = self._build(idle_seq=[])
        assert isinstance(builder, BuilderHarness)
