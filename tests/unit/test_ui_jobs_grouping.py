"""Tests for Job-level grouping in the dashboard data layer (issue #2519).

A Job is a unit of work (an issue, a PR, a planned slug). One Job is served by
one or more AgentSession runs: an original run, a recovery respawn, a local
anchor session, a spawned dev sub-session. The dashboard groups sessions into
Jobs for display; nothing is persisted.

The load-bearing invariant is the last class here: every session in the source
list lands in exactly one Job. #1379 is the precedent: gating on ``slug``
dropped conversational sessions from tracking entirely.
"""

import time
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]

# What projects.json says on a machine that has it. `_project_repo` reads this
# to scope an issue number, and the real file is private and iCloud-synced, so
# every test here owns the config instead of inheriting the machine's.
_PROJECT_CONFIGS = {"valor": {"github": {"org": "tomcounsell", "repo": "ai"}}}


@pytest.fixture(autouse=True)
def project_configs():
    """Pin the projects.json lookup for the whole module."""
    with patch("ui.data.jobs._load_project_configs", return_value=_PROJECT_CONFIGS):
        yield


def _pipeline(**overrides):
    """Build a PipelineProgress with only the fields Job grouping reads."""
    from ui.data.sdlc import PipelineProgress

    defaults = {
        "agent_session_id": f"as-{time.time_ns()}",
        "session_id": "sess-1",
        "status": "completed",
        "project_key": "valor",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    defaults.update(overrides)
    return PipelineProgress(**defaults)


class TestJobKeyPrecedence:
    """Issue identity outranks slug, slug outranks the thread fallback.

    Real-data driver: session ``0_1784286827622`` carries ``slug="sdlc-2137"``
    and session ``sdlc-local-2137`` carries ``issue_number=2137``. Both served
    issue #2137. Keying on slug alone leaves them as two unrelated rows.
    """

    def test_issue_number_wins_over_slug(self):
        from ui.data.jobs import self_job_key

        key, kind = self_job_key(_pipeline(slug="dashboard-jobs", issue_number=2519))
        assert kind == "issue"
        assert "2519" in key

    def test_slug_of_sdlc_shape_resolves_to_its_issue(self):
        """``sdlc-{N}`` is minted from an issue number by valor_session.py."""
        from ui.data.jobs import self_job_key

        slug_key, slug_kind = self_job_key(_pipeline(slug="sdlc-2137"))
        field_key, field_kind = self_job_key(_pipeline(issue_number=2137))
        assert slug_kind == field_kind == "issue"
        assert slug_key == field_key

    def test_local_anchor_session_id_resolves_to_its_issue(self):
        """``sdlc-local-{N}`` is the local /do-sdlc anchor session id."""
        from ui.data.jobs import self_job_key

        anchor_key, _ = self_job_key(_pipeline(session_id="sdlc-local-2158"))
        field_key, _ = self_job_key(_pipeline(issue_number=2158))
        assert anchor_key == field_key

    def test_issue_url_repo_scopes_the_key(self):
        """Two repos may both have an issue #665. They are different Jobs."""
        from ui.data.jobs import self_job_key

        ours, _ = self_job_key(_pipeline(issue_url="https://github.com/tomcounsell/ai/issues/665"))
        theirs, _ = self_job_key(
            _pipeline(issue_url="https://github.com/yudame/psyoptimal/issues/665")
        )
        assert ours != theirs

    def test_project_repo_backfills_the_scope_when_url_is_absent(self):
        """``sdlc-2158`` (issue_url set) and ``sdlc-local-2158`` (issue_number
        only) served one Job. The project's configured repo closes the gap."""
        from ui.data.jobs import self_job_key

        with_url, _ = self_job_key(
            _pipeline(
                project_key="valor",
                issue_url="https://github.com/tomcounsell/ai/issues/2158",
            )
        )
        without_url, _ = self_job_key(_pipeline(project_key="valor", issue_number=2158))
        assert with_url == without_url

    def test_issue_url_outranks_pr_url_for_the_scope(self):
        """A run can carry both URLs. The issue key follows the issue URL."""
        from ui.data.jobs import self_job_key

        key, _ = self_job_key(
            _pipeline(
                issue_number=2519,
                issue_url="https://github.com/tomcounsell/ai/issues/2519",
                pr_url="https://github.com/yudame/psyoptimal/pull/7",
            )
        )
        assert key == "issue:tomcounsell/ai#2519"

    def test_pr_url_scopes_an_issue_key_when_it_is_the_only_url(self):
        """A PR is opened against the repo its issue lives in, so the PR URL is
        better evidence of the repo than the project's default."""
        from ui.data.jobs import self_job_key

        key, _ = self_job_key(
            _pipeline(
                issue_number=665,
                pr_url="https://github.com/yudame/psyoptimal/pull/7",
            )
        )
        assert key == "issue:yudame/psyoptimal#665"

    def test_pr_number_used_when_no_issue(self):
        from ui.data.jobs import self_job_key

        key, kind = self_job_key(_pipeline(pr_number=2516))
        assert kind == "pr"
        assert "2516" in key

    def test_slug_used_when_no_issue_or_pr(self):
        from ui.data.jobs import self_job_key

        key, kind = self_job_key(_pipeline(slug="durability-m1-fence-canary"))
        assert kind == "slug"
        assert "durability-m1-fence-canary" in key

    def test_no_identity_returns_none(self):
        from ui.data.jobs import self_job_key

        assert self_job_key(_pipeline(message_text="what is the worker doing?")) is None


class TestGrouping:
    def test_slug_run_and_anchor_run_collapse_into_one_job(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="bridge-run", slug="sdlc-2137", status="completed"),
            _pipeline(agent_session_id="anchor", session_id="sdlc-local-2137", issue_number=2137),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1
        assert jobs[0].run_count == 2

    def test_recovery_respawn_joins_the_original_job(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="run-1", slug="sdlc-2519", status="failed"),
            _pipeline(agent_session_id="run-2", slug="sdlc-2519", status="running"),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1
        assert jobs[0].run_count == 2

    def test_child_without_identity_joins_its_parents_job(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="pm", slug="sdlc-2519"),
            _pipeline(agent_session_id="dev", parent_agent_session_id="pm"),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1
        assert {s.agent_session_id for s in jobs[0].sessions} == {"pm", "dev"}

    def test_parentless_adhoc_session_becomes_its_own_job(self):
        from ui.data.jobs import group_into_jobs

        jobs = group_into_jobs([_pipeline(agent_session_id="chat-1", message_text="hi")])
        assert len(jobs) == 1
        assert jobs[0].kind == "thread"
        assert jobs[0].run_count == 1

    def test_orphaned_child_still_groups_with_siblings(self):
        """Parent aged out of the retention window. The siblings stay together."""
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="c1", parent_agent_session_id="gone"),
            _pipeline(agent_session_id="c2", parent_agent_session_id="gone"),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1

    def test_parent_cycle_groups_into_one_job(self):
        """A malformed parent chain loops. Both members still read as one piece
        of work: walking from ``a`` and walking from ``b`` stop on the same node.
        The walk is capped, so a hang fails this test by timing out."""
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="a", parent_agent_session_id="b"),
            _pipeline(agent_session_id="b", parent_agent_session_id="a"),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1
        assert jobs[0].run_count == 2

    def test_sessions_ordered_newest_first_within_a_job(self):
        from ui.data.jobs import group_into_jobs

        now = time.time()
        pipelines = [
            _pipeline(agent_session_id="old", slug="sdlc-1", created_at=now - 900),
            _pipeline(agent_session_id="new", slug="sdlc-1", created_at=now),
        ]
        jobs = group_into_jobs(pipelines)
        assert [s.agent_session_id for s in jobs[0].sessions] == ["new", "old"]


class TestJobRollup:
    def test_active_run_makes_the_job_active(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="a", slug="sdlc-9", status="failed"),
            _pipeline(agent_session_id="b", slug="sdlc-9", status="running"),
        ]
        job = group_into_jobs(pipelines)[0]
        assert job.is_active
        assert job.status == "running"
        assert job.active_run_count == 1

    def test_all_terminal_reports_the_newest_outcome(self):
        from ui.data.jobs import group_into_jobs

        now = time.time()
        pipelines = [
            _pipeline(agent_session_id="a", slug="sdlc-9", status="failed", updated_at=now - 600),
            _pipeline(agent_session_id="b", slug="sdlc-9", status="completed", updated_at=now),
        ]
        job = group_into_jobs(pipelines)[0]
        assert not job.is_active
        assert job.status == "completed"

    def test_started_at_is_the_earliest_run(self):
        from ui.data.jobs import group_into_jobs

        now = time.time()
        pipelines = [
            _pipeline(agent_session_id="a", slug="sdlc-9", created_at=now - 3600),
            _pipeline(agent_session_id="b", slug="sdlc-9", created_at=now),
        ]
        job = group_into_jobs(pipelines)[0]
        assert job.started_at == pytest.approx(now - 3600)

    def test_cost_and_turns_sum_across_runs(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(agent_session_id="a", slug="sdlc-9", total_cost_usd=1.5, turn_count=4),
            _pipeline(agent_session_id="b", slug="sdlc-9", total_cost_usd=2.25, turn_count=6),
        ]
        job = group_into_jobs(pipelines)[0]
        assert job.total_cost_usd == pytest.approx(3.75)
        assert job.turn_count == 10

    def test_stages_come_from_the_run_that_has_them(self):
        from ui.data.jobs import group_into_jobs
        from ui.data.sdlc import StageState

        stages = [StageState(name="plan", status="completed")]
        pipelines = [
            _pipeline(agent_session_id="a", slug="sdlc-9", stages=[]),
            _pipeline(agent_session_id="b", slug="sdlc-9", stages=stages),
        ]
        job = group_into_jobs(pipelines)[0]
        assert [s.name for s in job.stages] == ["plan"]

    def test_links_surface_from_any_run(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(
                agent_session_id="a",
                slug="sdlc-9",
                issue_url="https://github.com/tomcounsell/ai/issues/9",
            ),
            _pipeline(
                agent_session_id="b",
                slug="sdlc-9",
                pr_url="https://github.com/tomcounsell/ai/pull/10",
            ),
        ]
        job = group_into_jobs(pipelines)[0]
        assert job.issue_url.endswith("/issues/9")
        assert job.pr_url.endswith("/pull/10")

    def test_display_name_prefers_the_slug(self):
        from ui.data.jobs import group_into_jobs

        job = group_into_jobs([_pipeline(slug="durability-m1-fence-canary")])[0]
        assert job.display_name == "durability-m1-fence-canary"

    def test_display_name_falls_back_to_the_newest_run(self):
        from ui.data.jobs import group_into_jobs

        job = group_into_jobs([_pipeline(message_text="check the worker queue")])[0]
        assert "check the worker queue" in job.display_name


class TestScopeWithoutProjectConfig:
    """projects.json is absent on a fresh machine and in CI, and
    ``_load_project_configs`` caches ``{}`` for the TTL after any read failure.
    The repo scope then has to come from the runs themselves, or one Job splits
    into two: the run carrying ``issue_url`` keys to ``issue:tomcounsell/ai#N``
    while its sibling carrying only ``issue_number`` keys to ``issue:valor#N``.
    """

    @pytest.fixture(autouse=True)
    def no_project_config(self):
        with patch("ui.data.jobs._load_project_configs", return_value={}):
            yield

    def test_url_run_and_number_only_run_stay_one_job(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(
                agent_session_id="bridge-run",
                issue_url="https://github.com/tomcounsell/ai/issues/2158",
            ),
            _pipeline(agent_session_id="anchor", session_id="sdlc-local-2158"),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1
        assert jobs[0].run_count == 2
        assert jobs[0].repo == "tomcounsell/ai"

    def test_pr_url_run_and_pr_number_run_stay_one_job(self):
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(
                agent_session_id="a",
                pr_url="https://github.com/tomcounsell/ai/pull/2530",
            ),
            _pipeline(agent_session_id="b", pr_number=2530),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 1
        assert jobs[0].repo == "tomcounsell/ai"

    def test_two_repos_claiming_one_number_stay_apart(self):
        """Both runs state their repo, so neither has anything to adopt."""
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(
                agent_session_id="ours",
                issue_url="https://github.com/tomcounsell/ai/issues/665",
            ),
            _pipeline(
                agent_session_id="theirs",
                issue_url="https://github.com/yudame/psyoptimal/issues/665",
            ),
        ]
        assert len(group_into_jobs(pipelines)) == 2

    def test_an_ambiguous_scope_is_not_adopted(self):
        """Two repos claim issue #665 and a third run carries only the number.
        Nothing distinguishes the candidates, so the bare run keeps the project
        scope rather than guessing a repo."""
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(
                agent_session_id="ours",
                issue_url="https://github.com/tomcounsell/ai/issues/665",
            ),
            _pipeline(
                agent_session_id="theirs",
                issue_url="https://github.com/yudame/psyoptimal/issues/665",
            ),
            _pipeline(agent_session_id="bare", issue_number=665),
        ]
        assert len(group_into_jobs(pipelines)) == 3

    def test_scope_is_adopted_within_a_project_only(self):
        """Issue #700 in two projects is two Jobs, and the run without a URL
        adopts only from a run sharing its project."""
        from ui.data.jobs import group_into_jobs

        pipelines = [
            _pipeline(
                agent_session_id="a",
                project_key="valor",
                issue_url="https://github.com/tomcounsell/ai/issues/700",
            ),
            _pipeline(agent_session_id="b", project_key="valor", issue_number=700),
            _pipeline(agent_session_id="c", project_key="psyoptimal", issue_number=700),
        ]
        jobs = group_into_jobs(pipelines)
        assert len(jobs) == 2
        merged = next(j for j in jobs if j.run_count == 2)
        assert {s.agent_session_id for s in merged.sessions} == {"a", "b"}


class TestJobLivenessRollup:
    """The Job row answers "is this healthy" without being expanded.

    Every liveness signal on a Job describes its representative run: the live
    one, or the newest outcome once every run is terminal. That is the same run
    the Job's status, duration and click-through modal already speak for, so the
    row reads as one coherent statement about one run.
    """

    def test_ghosted_live_run_marks_the_job(self):
        from ui.data.jobs import group_into_jobs

        job = group_into_jobs(
            [_pipeline(agent_session_id="x", slug="sdlc-9", status="running", process_alive=False)]
        )[0]
        assert job.process_alive is False

    def test_signals_track_the_live_run_not_the_newest(self):
        from ui.data.jobs import group_into_jobs

        now = time.time()
        pipelines = [
            _pipeline(
                agent_session_id="live",
                slug="sdlc-9",
                status="running",
                updated_at=now - 900,
                is_stale=True,
                process_alive=False,
                stall_advisory="stalled",
                stall_advisory_reason="no evidence for 30m",
            ),
            _pipeline(agent_session_id="done", slug="sdlc-9", status="completed", updated_at=now),
        ]
        job = group_into_jobs(pipelines)[0]
        assert job.primary_agent_session_id == "live"
        assert job.is_stale
        assert job.process_alive is False
        assert job.stall_advisory == "stalled"
        assert job.stall_advisory_reason == "no evidence for 30m"

    def test_freshness_and_health_reason_surface(self):
        from ui.data.jobs import group_into_jobs

        evidence = time.time() - 120
        job = group_into_jobs(
            [
                _pipeline(
                    agent_session_id="x",
                    slug="sdlc-9",
                    status="running",
                    last_evidence_at=evidence,
                    unhealthy_reason="no heartbeat",
                )
            ]
        )[0]
        assert job.last_evidence_at == pytest.approx(evidence)
        assert job.unhealthy_reason == "no heartbeat"

    def test_settled_job_carries_the_newest_outcomes_signals(self):
        from ui.data.jobs import group_into_jobs

        now = time.time()
        pipelines = [
            _pipeline(
                agent_session_id="first",
                slug="sdlc-9",
                status="failed",
                updated_at=now - 600,
                is_stale=True,
            ),
            _pipeline(agent_session_id="last", slug="sdlc-9", status="completed", updated_at=now),
        ]
        job = group_into_jobs(pipelines)[0]
        assert job.primary_agent_session_id == "last"
        assert not job.is_stale


class TestNoSessionIsDropped:
    """Acceptance criterion: ad-hoc / jobless sessions are still represented.

    #1379: gating on ``slug`` dropped conversational sessions from tracking.
    Every session in the source list appears in exactly one Job group.
    """

    @staticmethod
    def _mixed_population():
        now = time.time()
        return [
            # Planned SDLC work, two runs, one Job.
            _pipeline(agent_session_id="s1", slug="sdlc-2137", created_at=now - 100),
            _pipeline(agent_session_id="s2", session_id="sdlc-local-2137", issue_number=2137),
            # A different issue.
            _pipeline(agent_session_id="s3", issue_number=2519),
            # A PR-only session.
            _pipeline(agent_session_id="s4", pr_number=2516),
            # A named plan slug with no issue.
            _pipeline(agent_session_id="s5", slug="durability-m1-fence-canary"),
            # A PM/dev pair.
            _pipeline(agent_session_id="s6", slug="sdlc-2500"),
            _pipeline(agent_session_id="s7", parent_agent_session_id="s6"),
            # Ad-hoc conversation, no slug, no issue, no parent.
            _pipeline(agent_session_id="s8", message_text="how is the bridge?"),
            _pipeline(agent_session_id="s9", message_text="thanks"),
            # A cross-repo issue that must not merge with s3.
            _pipeline(
                agent_session_id="s10",
                issue_url="https://github.com/yudame/psyoptimal/issues/2519",
            ),
        ]

    def test_every_session_appears_exactly_once(self):
        from ui.data.jobs import group_into_jobs

        pipelines = self._mixed_population()
        jobs = group_into_jobs(pipelines)

        seen = [s.agent_session_id for job in jobs for s in job.sessions]
        assert sorted(seen) == sorted(p.agent_session_id for p in pipelines)
        assert len(seen) == len(set(seen)), "a session landed in two Jobs"

    def test_run_counts_reconcile_with_the_source_list(self):
        from ui.data.jobs import group_into_jobs

        pipelines = self._mixed_population()
        jobs = group_into_jobs(pipelines)
        assert sum(job.run_count for job in jobs) == len(pipelines)

    def test_jobless_sessions_are_present_as_their_own_jobs(self):
        from ui.data.jobs import group_into_jobs

        jobs = group_into_jobs(self._mixed_population())
        adhoc = {s.agent_session_id for job in jobs if job.kind == "thread" for s in job.sessions}
        assert {"s8", "s9"} <= adhoc

    def test_empty_input_yields_no_jobs(self):
        from ui.data.jobs import group_into_jobs

        assert group_into_jobs([]) == []
