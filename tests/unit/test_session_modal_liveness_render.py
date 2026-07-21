"""Smoke render tests for the session modal Liveness sub-table (#1269).

The modal template at `ui/templates/_partials/session_modal_content.html` adds
a "Liveness" sub-table between Timing and SDLC. It must render gracefully when:
  * harness_pid is None (no PID row);
  * process_alive is True (alive chip);
  * process_alive is False (ghost badge);
  * process_alive is None (unknown chip);
  * every liveness signal is absent (section is omitted entirely).

Smoke tests render via Jinja2 directly (no FastAPI request handler) so we can
assert HTML substrings without spinning up the full app.
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

pytestmark = [pytest.mark.unit, pytest.mark.webui]


@pytest.fixture
def env():
    """Isolated Jinja env with the production filters registered."""
    template_dir = Path(__file__).resolve().parent.parent.parent / "ui" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))

    # Mirror the filters registered in ui/app.py::create_app
    def _format_timestamp(ts):
        if ts is None:
            return "-"
        return datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime("%H:%M")

    def _format_duration(seconds):
        if seconds is None:
            return "-"
        return f"{int(seconds)}s"

    def _freshness_age(ts):
        if ts is None:
            return None
        age = time.time() - float(ts)
        return age if age >= 0 else None

    env.filters["format_timestamp"] = _format_timestamp
    env.filters["format_duration"] = _format_duration
    env.filters["freshness_age"] = _freshness_age
    return env


def _make_pipeline(**overrides):
    """Construct a minimal pipeline namespace for template rendering."""
    base = dict(
        agent_session_id="x",
        session_id="thread-1",
        session_type=None,
        status="running",
        slug=None,
        message_text=None,
        message_user_text=None,
        message_system_prompt=None,
        project_key="proj",
        project_name="proj",
        branch_name=None,
        created_at=time.time(),
        started_at=time.time(),
        completed_at=None,
        duration=None,
        tool_call_count=None,
        issue_url=None,
        plan_url=None,
        pr_url=None,
        expectations=None,
        stages=[],
        events=[],
        is_stale=False,
        display_name="x",
        claude_session_uuid=None,
        # Liveness fields (#1269)
        harness_pid=None,
        last_heartbeat_at=None,
        last_sdk_heartbeat_at=None,
        last_stdout_at=None,
        last_evidence_at=None,
        last_tool_use_at=None,
        last_turn_at=None,
        recovery_attempts=0,
        reprieve_count=0,
        current_tool_name=None,
        unhealthy_reason=None,
        process_alive=None,
        # Metadata surfaced in the modal (mirrors PipelineProgress defaults)
        turn_count=None,
        priority=None,
        classification_type=None,
        context_summary=None,
        stall_advisory=None,
        stall_advisory_reason=None,
        recent_thinking_excerpt=None,
        requires_real_chrome=False,
        pm_pid=None,
        dev_agent_id=None,
        runner_cwd=None,
        claude_version=None,
        exit_reason=None,
        user_facing_routed=True,
        is_ledger=False,
        updated_at=None,
        initiator=None,
        total_input_tokens=0,
        total_output_tokens=0,
        total_cache_read_tokens=0,
        total_cost_usd=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestModalLivenessSection:
    def test_no_liveness_section_when_all_fields_absent(self, env):
        """Old sessions with no liveness data → section is omitted entirely."""
        tmpl = env.get_template("_partials/session_modal_content.html")
        pipeline = _make_pipeline()
        html = tmpl.render(pipeline=pipeline)
        assert "<h3>Liveness</h3>" not in html

    def test_pid_alive_renders_alive_chip(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        pipeline = _make_pipeline(harness_pid=12345, process_alive=True)
        html = tmpl.render(pipeline=pipeline)
        # Timing + Liveness were merged into one compact table (one <h3>).
        assert "<h3>Timing &amp; Liveness</h3>" in html
        assert "12345" in html
        assert "freshness-fresh" in html
        assert "alive" in html

    def test_pid_ghost_renders_ghost_badge(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        pipeline = _make_pipeline(harness_pid=99999, process_alive=False)
        html = tmpl.render(pipeline=pipeline)
        assert "<h3>Timing &amp; Liveness</h3>" in html
        assert "ghost-badge" in html
        assert "ghost" in html

    def test_pid_unknown_renders_unknown_chip(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        pipeline = _make_pipeline(harness_pid=12345, process_alive=None)
        html = tmpl.render(pipeline=pipeline)
        assert "<h3>Timing &amp; Liveness</h3>" in html
        assert "unknown" in html

    def test_no_pid_row_when_harness_pid_none(self, env):
        """When harness_pid is None but other liveness signals are present,
        the merged Timing & Liveness section renders WITHOUT a PID row
        (graceful degradation)."""
        tmpl = env.get_template("_partials/session_modal_content.html")
        pipeline = _make_pipeline(
            harness_pid=None,
            current_tool_name="Bash",
            recovery_attempts=1,
        )
        html = tmpl.render(pipeline=pipeline)
        assert "<h3>Timing &amp; Liveness</h3>" in html
        assert '<td class="text-secondary">PID</td>' not in html
        assert "Bash" in html
        # Recovery count row label is "Recoveries".
        assert "Recoveries" in html

    def test_renders_recovery_and_reprieve_counts(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        pipeline = _make_pipeline(
            recovery_attempts=3,
            reprieve_count=5,
        )
        html = tmpl.render(pipeline=pipeline)
        assert "Recoveries" in html
        assert "Reprieves" in html
        assert ">3<" in html
        assert ">5<" in html

    def test_renders_timestamps_via_format_filter(self, env):
        """Evidence + heartbeat are merged into a single "Last active" row
        (max of the two), rendered through the format_timestamp filter."""
        tmpl = env.get_template("_partials/session_modal_content.html")
        ts = time.time() - 60
        pipeline = _make_pipeline(
            last_evidence_at=ts,
            last_heartbeat_at=ts,
        )
        html = tmpl.render(pipeline=pipeline)
        assert "Last active" in html
        # The merged value renders via the format_timestamp filter (HH:MM).
        expected = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime("%H:%M")
        assert expected in html


class TestModalPersonaBadge:
    """Persona badge tracks _resolve_persona_display() output — "Engineer" and
    "Teammate" are the only live values (the old "Developer"/"Project Manager"
    vocabulary was removed when session types collapsed to eng/teammate)."""

    def test_engineer_renders_blue_eng_badge(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(pipeline=_make_pipeline(session_type="Engineer"))
        assert "badge-blue" in html
        assert ">eng<" in html

    def test_teammate_renders_green_teammate_badge(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(pipeline=_make_pipeline(session_type="Teammate"))
        assert "badge-green" in html
        assert ">teammate<" in html

    def test_legacy_value_falls_through_to_purple_raw(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(pipeline=_make_pipeline(session_type="granite"))
        assert "badge-purple" in html
        assert ">granite<" in html


class TestModalMetadataSections:
    """New metadata surfaced in the modal: token/cost strip, thinking excerpt,
    stall advisory, and runner identity."""

    def test_token_cost_strip_renders_when_present(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(
            pipeline=_make_pipeline(
                total_cost_usd=1.2345,
                total_input_tokens=12000,
                total_output_tokens=3400,
            )
        )
        assert "token-strip" in html
        assert "$1.2345" in html
        assert "12,000" in html

    def test_token_strip_omitted_when_all_zero(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(pipeline=_make_pipeline())
        # The <style> rule always ships; assert the rendered div is absent.
        assert '<div class="token-strip' not in html

    def test_thinking_excerpt_renders(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(
            pipeline=_make_pipeline(recent_thinking_excerpt="weighing the migration path")
        )
        assert "thinking-excerpt" in html
        assert "weighing the migration path" in html

    def test_stall_advisory_badge_renders_for_stalled(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(
            pipeline=_make_pipeline(
                stall_advisory="stalled", stall_advisory_reason="no evidence 30m"
            )
        )
        assert "badge-error" in html
        assert "stalled" in html

    def test_healthy_stall_advisory_not_shown(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(pipeline=_make_pipeline(stall_advisory="healthy"))
        assert ">healthy<" not in html

    def test_runner_identity_rows_render(self, env):
        tmpl = env.get_template("_partials/session_modal_content.html")
        html = tmpl.render(
            pipeline=_make_pipeline(
                pm_pid=4242,
                dev_agent_id="dev-abcdef123456789",
                claude_version="1.2.3",
            )
        )
        assert "PM pid" in html
        assert "4242" in html
        assert "Dev agent" in html
        assert "CLI version" in html


class TestRowFreshnessChip:
    """The row template (`sessions_table.html`) renders a freshness chip
    inside the status <td> for non-terminal sessions with last_evidence_at.
    Smoke tests assert the chip renders and uses the right color tier."""

    def test_chip_omitted_for_terminal_status(self, env):
        tmpl = env.get_template("_partials/sessions_table.html")
        pipeline = _make_pipeline(
            status="completed",
            last_evidence_at=time.time() - 10,
        )
        html = tmpl.render(sessions=[pipeline])
        assert "freshness-chip" not in html

    def test_chip_renders_fresh_for_recent_evidence(self, env):
        tmpl = env.get_template("_partials/sessions_table.html")
        pipeline = _make_pipeline(
            status="running",
            last_evidence_at=time.time() - 10,  # 10s ago → fresh
            duration=42,
        )
        html = tmpl.render(sessions=[pipeline])
        assert "freshness-chip" in html
        assert "freshness-fresh" in html

    def test_chip_renders_warm_for_minute_old_evidence(self, env):
        tmpl = env.get_template("_partials/sessions_table.html")
        pipeline = _make_pipeline(
            status="running",
            last_evidence_at=time.time() - 120,  # 2m ago → warm
            duration=42,
        )
        html = tmpl.render(sessions=[pipeline])
        assert "freshness-chip" in html
        assert "freshness-warm" in html

    def test_chip_renders_stale_for_old_evidence(self, env):
        tmpl = env.get_template("_partials/sessions_table.html")
        pipeline = _make_pipeline(
            status="running",
            last_evidence_at=time.time() - 1200,  # 20m ago → red
            duration=42,
        )
        html = tmpl.render(sessions=[pipeline])
        assert "freshness-chip" in html
        assert "freshness-stale" in html

    def test_paused_circuit_glyph_distinct_from_paused(self, env):
        """#1269: paused_circuit gets ⛌; paused gets ⏸."""
        tmpl = env.get_template("_partials/sessions_table.html")
        circuit_html = tmpl.render(sessions=[_make_pipeline(status="paused_circuit", duration=42)])
        assert "⛌" in circuit_html

    def test_ghost_badge_renders_when_process_alive_false(self, env):
        tmpl = env.get_template("_partials/sessions_table.html")
        pipeline = _make_pipeline(
            status="running",
            harness_pid=99999,
            process_alive=False,
            last_evidence_at=time.time() - 60,
            duration=42,
        )
        html = tmpl.render(sessions=[pipeline])
        assert "ghost-badge" in html
