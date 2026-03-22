"""Tests for the autoexperiment framework.

Tests the core ExperimentRunner, target extraction/injection,
result logging, budget enforcement, STOP sentinel, and dry-run mode.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import will fail until implementation exists — that's expected in RED phase
from scripts.autoexperiment import (
    ExperimentResult,
    ExperimentRunner,
    ExperimentTarget,
    call_openrouter,
    extract_summarizer_prompt,
    inject_summarizer_prompt,
    load_jsonl,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SUMMARIZER_FILE = '''\
"""Summarizer module."""

SUMMARIZER_SYSTEM_PROMPT = """\
You condense messages into Telegram-length updates.

FORMAT RULES:
- Be concise
- No preamble"""

async def summarize():
    pass
'''


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_target(tmp_dir):
    """Create a minimal ExperimentTarget for testing."""
    eval_file = tmp_dir / "eval.jsonl"
    eval_file.write_text(
        json.dumps({"input": "test", "expected": "STEER"})
        + "\n"
        + json.dumps({"input": "done", "expected": "DELIVER"})
        + "\n"
    )

    return ExperimentTarget(
        name="test-target",
        file_path=str(tmp_dir / "summarizer.py"),
        extract_fn=extract_summarizer_prompt,
        inject_fn=inject_summarizer_prompt,
        eval_fn=lambda: 0.75,
        metric_direction="higher",
        description="Test target",
    )


# ---------------------------------------------------------------------------
# ExperimentTarget dataclass
# ---------------------------------------------------------------------------


class TestExperimentTarget:
    def test_create_target(self, sample_target):
        assert sample_target.name == "test-target"
        assert sample_target.metric_direction == "higher"
        assert sample_target.description == "Test target"

    def test_default_model(self, sample_target):
        # Should use MODEL_EXPERIMENT by default
        from config.models import MODEL_EXPERIMENT

        assert sample_target.model == MODEL_EXPERIMENT


# ---------------------------------------------------------------------------
# Extract / Inject functions
# ---------------------------------------------------------------------------


class TestExtractInject:
    def test_extract_summarizer_prompt(self):
        prompt = extract_summarizer_prompt(SAMPLE_SUMMARIZER_FILE)
        assert "condense messages" in prompt
        assert "FORMAT RULES" in prompt

    def test_inject_summarizer_prompt(self):
        new_prompt = "New summarizer instructions."
        result = inject_summarizer_prompt(SAMPLE_SUMMARIZER_FILE, new_prompt)
        assert "New summarizer instructions" in result
        assert "async def summarize():" in result

    def test_roundtrip_summarizer(self):
        """Extract then inject should preserve the prompt."""
        prompt = extract_summarizer_prompt(SAMPLE_SUMMARIZER_FILE)
        result = inject_summarizer_prompt(SAMPLE_SUMMARIZER_FILE, prompt)
        assert extract_summarizer_prompt(result) == prompt


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------


class TestLoadJsonl:
    def test_load_jsonl(self, tmp_dir):
        f = tmp_dir / "test.jsonl"
        f.write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"b": 2}) + "\n")
        data = load_jsonl(str(f))
        assert len(data) == 2
        assert data[0]["a"] == 1
        assert data[1]["b"] == 2

    def test_load_jsonl_empty(self, tmp_dir):
        f = tmp_dir / "empty.jsonl"
        f.write_text("")
        data = load_jsonl(str(f))
        assert data == []


# ---------------------------------------------------------------------------
# Result logging
# ---------------------------------------------------------------------------


class TestResultLogging:
    def test_result_dataclass(self):
        r = ExperimentResult(
            iteration=1,
            hypothesis="test hypothesis",
            diff="- old\n+ new",
            baseline_score=0.5,
            new_score=0.7,
            kept=True,
            cost_usd=0.001,
            timestamp="2026-03-14T02:00:00",
        )
        assert r.kept is True
        assert r.new_score > r.baseline_score

    def test_result_to_jsonl(self, tmp_dir):
        """Results should serialize to JSONL correctly."""
        from dataclasses import asdict

        r = ExperimentResult(
            iteration=1,
            hypothesis="test",
            diff="",
            baseline_score=0.5,
            new_score=0.6,
            kept=True,
            cost_usd=0.001,
            timestamp="2026-03-14T02:00:00",
        )
        log_file = tmp_dir / "results.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(asdict(r)) + "\n")

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["kept"] is True
        assert parsed["iteration"] == 1


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    @patch("scripts.autoexperiment.call_openrouter")
    def test_budget_ceiling_stops_loop(self, mock_call, tmp_dir, sample_target):
        """Runner should stop when budget is exhausted."""
        # Make eval_fn and hypothesis generation work
        mock_call.return_value = ("No change needed.", 0.50)

        # Write a fake source file
        source = Path(sample_target.file_path)
        source.write_text(SAMPLE_SUMMARIZER_FILE)

        runner = ExperimentRunner(
            target=sample_target,
            dry_run=True,
            results_dir=str(tmp_dir),
        )
        # Set a very tight budget
        results = runner.run_loop(n=100, budget_usd=0.0)
        # Should stop immediately — zero budget
        assert len(results) == 0

    @patch("scripts.autoexperiment.call_openrouter")
    def test_budget_tracks_cost(self, mock_call, tmp_dir, sample_target):
        """Runner should accumulate cost across iterations."""
        mock_call.return_value = ("No change.", 0.001)

        source = Path(sample_target.file_path)
        source.write_text(SAMPLE_SUMMARIZER_FILE)

        runner = ExperimentRunner(
            target=sample_target,
            dry_run=True,
            results_dir=str(tmp_dir),
        )
        # Budget allows ~2 iterations at $0.001 each
        results = runner.run_loop(n=5, budget_usd=0.003)
        # Should have run at most 3 iterations
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# STOP sentinel
# ---------------------------------------------------------------------------


class TestStopSentinel:
    @patch("scripts.autoexperiment.call_openrouter")
    def test_stop_file_halts_loop(self, mock_call, tmp_dir, sample_target):
        """STOP file should halt the experiment loop."""
        mock_call.return_value = ("test", 0.001)

        source = Path(sample_target.file_path)
        source.write_text(SAMPLE_SUMMARIZER_FILE)

        # Create STOP sentinel
        stop_file = tmp_dir / "STOP"
        stop_file.touch()

        runner = ExperimentRunner(
            target=sample_target,
            dry_run=True,
            results_dir=str(tmp_dir),
            stop_file=str(stop_file),
        )
        results = runner.run_loop(n=100, budget_usd=10.0)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    @patch("scripts.autoexperiment.call_openrouter")
    def test_dry_run_no_git_operations(self, mock_call, tmp_dir, sample_target):
        """Dry-run should not perform git operations."""
        mock_call.return_value = ("Improved prompt text here.", 0.001)

        source = Path(sample_target.file_path)
        source.write_text(SAMPLE_SUMMARIZER_FILE)

        runner = ExperimentRunner(
            target=sample_target,
            dry_run=True,
            results_dir=str(tmp_dir),
        )

        # Should run without errors even outside a git repo
        result = runner.run_one()
        assert result is not None
        assert isinstance(result, ExperimentResult)


# ---------------------------------------------------------------------------
# OpenRouter call helper
# ---------------------------------------------------------------------------


class TestCallOpenRouter:
    @patch("requests.post")
    def test_call_openrouter_returns_content_and_cost(self, mock_post):
        """call_openrouter should return (content, cost)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "test response"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            content, cost = call_openrouter("test prompt", model="test/model")
        assert content == "test response"
        assert cost >= 0
