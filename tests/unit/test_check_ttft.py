"""Unit tests for ``scripts/check_ttft.py`` — TTFT regression gate (issue #1227).

Per ``docs/plans/sdlc-1227.md``:

- Reads ``logs/cold_start_metrics.jsonl``, filters by ``--session-type``, takes
  the last ``--last N`` entries, computes median ``ttft_seconds``.
- Exits 0 if median < threshold; exits 1 if median >= threshold (regression).
- Prints ``median=XX.Xs N=N threshold=Ts [PASS|FAIL]``.

Tests cover happy path, regression, filtering, missing/empty file, and
malformed lines (best-effort skip).
"""

from __future__ import annotations

import json

import pytest


def _write_jsonl(path, rows):
    """Helper: write a list of dicts as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class TestCheckTtftMain:
    """Tests for ``scripts.check_ttft.main`` (the CLI entry point)."""

    def test_pass_when_median_below_threshold(self, tmp_path, capsys):
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "pm", "ttft_seconds": 30.0},
                {"session_type": "pm", "ttft_seconds": 45.0},
                {"session_type": "pm", "ttft_seconds": 60.0},
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "PASS" in out
        assert "median=45" in out
        assert "N=3" in out
        assert "threshold=90" in out

    def test_fail_when_median_at_or_above_threshold(self, tmp_path, capsys):
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "pm", "ttft_seconds": 100.0},
                {"session_type": "pm", "ttft_seconds": 120.0},
                {"session_type": "pm", "ttft_seconds": 140.0},
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL" in out
        assert "median=120" in out

    def test_filters_by_session_type(self, tmp_path, capsys):
        """Only entries matching --session-type contribute to the median."""
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "dev", "ttft_seconds": 10.0},  # ignored
                {"session_type": "pm", "ttft_seconds": 50.0},
                {"session_type": "pm", "ttft_seconds": 70.0},
                {"session_type": "teammate", "ttft_seconds": 9999.0},  # ignored
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "N=2" in out
        assert "median=60" in out

    def test_takes_only_last_n(self, tmp_path, capsys):
        """--last N selects the most recent N matching entries."""
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        # First entries are slow (would fail), but the most recent 3 are fast
        _write_jsonl(
            log,
            [
                {"session_type": "pm", "ttft_seconds": 1000.0},
                {"session_type": "pm", "ttft_seconds": 1000.0},
                {"session_type": "pm", "ttft_seconds": 10.0},
                {"session_type": "pm", "ttft_seconds": 20.0},
                {"session_type": "pm", "ttft_seconds": 30.0},
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "3",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "N=3" in out
        assert "median=20" in out

    def test_missing_log_file_exits_nonzero(self, tmp_path, capsys):
        """A missing JSONL file is a configuration error → exit 1 with a message."""
        from scripts import check_ttft

        missing = tmp_path / "does_not_exist.jsonl"
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(missing),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 1
        # Must be human-readable — caller piping to a script expects a clear cause
        assert "not found" in out.lower() or "no such file" in out.lower()

    def test_empty_after_filter_exits_nonzero(self, tmp_path, capsys):
        """Zero matching entries cannot validate a threshold → exit 1."""
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "dev", "ttft_seconds": 5.0},
                {"session_type": "teammate", "ttft_seconds": 5.0},
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert "N=0" in out or "no" in out.lower()

    def test_skips_malformed_lines(self, tmp_path, capsys):
        """Malformed JSONL lines must be skipped, not crash the gate."""
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "\n".join(
                [
                    '{"session_type": "pm", "ttft_seconds": 30.0}',
                    "this is not valid json",
                    '{"session_type": "pm", "ttft_seconds": 50.0}',
                    "",  # blank line
                    '{"session_type": "pm", "ttft_seconds": 70.0}',
                ]
            )
            + "\n"
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "N=3" in out
        assert "median=50" in out

    def test_skips_entries_missing_ttft_field(self, tmp_path, capsys):
        """Entries lacking ``ttft_seconds`` are skipped (best-effort schema)."""
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "pm", "ttft_seconds": 30.0},
                {"session_type": "pm"},  # missing field — skipped
                {"session_type": "pm", "ttft_seconds": 50.0},
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
                "--log-file",
                str(log),
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "N=2" in out
        assert "median=40" in out

    def test_default_log_file_is_repo_relative(self, tmp_path, monkeypatch, capsys):
        """When --log-file is omitted, the script reads logs/cold_start_metrics.jsonl."""
        from scripts import check_ttft

        # Run from a temp dir with a logs/ subdir we control
        monkeypatch.chdir(tmp_path)
        log = tmp_path / "logs" / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "pm", "ttft_seconds": 30.0},
                {"session_type": "pm", "ttft_seconds": 50.0},
            ],
        )
        rc = check_ttft.main(
            [
                "--session-type",
                "pm",
                "--last",
                "10",
                "--threshold",
                "90",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "N=2" in out


class TestCheckTtftHelpers:
    """Direct tests for the helper functions if exposed."""

    def test_load_entries_reads_only_matching_session_type(self, tmp_path):
        from scripts import check_ttft

        log = tmp_path / "cold_start_metrics.jsonl"
        _write_jsonl(
            log,
            [
                {"session_type": "pm", "ttft_seconds": 1.0},
                {"session_type": "dev", "ttft_seconds": 2.0},
                {"session_type": "pm", "ttft_seconds": 3.0},
            ],
        )
        entries = check_ttft.load_entries(log, session_type="pm")
        assert [e["ttft_seconds"] for e in entries] == [1.0, 3.0]

    def test_compute_median_handles_even_and_odd_counts(self):
        from scripts import check_ttft

        assert check_ttft.compute_median([10.0]) == 10.0
        assert check_ttft.compute_median([10.0, 20.0]) == 15.0
        assert check_ttft.compute_median([10.0, 20.0, 30.0]) == 20.0
        assert check_ttft.compute_median([10.0, 20.0, 30.0, 40.0]) == 25.0

    def test_compute_median_empty_raises(self):
        from scripts import check_ttft

        with pytest.raises(ValueError):
            check_ttft.compute_median([])
