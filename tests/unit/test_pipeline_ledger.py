"""Tests for agent.pipeline_ledger.PipelineLedger (issue #2012).

Real Popoto/Redis integration -- no mocks, per this repo's testing philosophy
(see CLAUDE.md "Testing Philosophy"). Every test cleans up the records it
creates so the suite leaves no residue in the shared test Redis.
"""

from agent.pipeline_ledger import PipelineLedger

_TEST_REPO = "test-owner/test-repo"


def _cleanup(issue_number: int, target_repo: str = _TEST_REPO) -> None:
    for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
        record.delete()


class TestGetOrCreate:
    def setup_method(self):
        _cleanup(100001)
        _cleanup(100002)

    def teardown_method(self):
        _cleanup(100001)
        _cleanup(100002)

    def test_returns_empty_but_valid_record_when_absent(self):
        """get_or_create on a (repo, issue) with no ledger yet creates an
        empty-but-valid record rather than erroring."""
        ledger = PipelineLedger.get_or_create(_TEST_REPO, 100001)
        assert ledger.ledger_key == f"{_TEST_REPO}:100001"
        assert ledger.target_repo == _TEST_REPO
        assert ledger.issue_number == 100001
        assert ledger.stage_states_json == "{}"
        assert ledger.pr_number is None

    def test_returns_same_record_on_repeat_call(self):
        """get_or_create is idempotent -- it does not mint a second record
        for the same (repo, issue) pair."""
        first = PipelineLedger.get_or_create(_TEST_REPO, 100002)
        first.pr_number = 4242
        first.save()

        second = PipelineLedger.get_or_create(_TEST_REPO, 100002)
        assert second.pr_number == 4242
        assert second.ledger_key == first.ledger_key

    def test_different_issue_numbers_get_distinct_records(self):
        """Two different issue numbers under the same repo never collide."""
        a = PipelineLedger.get_or_create(_TEST_REPO, 100001)
        b = PipelineLedger.get_or_create(_TEST_REPO, 100002)
        assert a.ledger_key != b.ledger_key

    def test_different_repos_same_issue_number_get_distinct_records(self):
        """The same issue number under two different repos never collides --
        the repo is part of the key, not just the issue number."""
        a = PipelineLedger.get_or_create("owner-one/repo", 100001)
        b = PipelineLedger.get_or_create("owner-two/repo", 100001)
        try:
            assert a.ledger_key != b.ledger_key
            assert a.ledger_key == "owner-one/repo:100001"
            assert b.ledger_key == "owner-two/repo:100001"
        finally:
            a.delete()
            b.delete()


class TestPersistenceSurvivesIndependentOfSession:
    """The ledger's whole reason for existing: it is not tied to any
    AgentSession's lifecycle. A write persists and round-trips with no
    session involved at all."""

    def setup_method(self):
        _cleanup(100003)

    def teardown_method(self):
        _cleanup(100003)

    def test_write_persists_and_round_trips(self):
        ledger = PipelineLedger.get_or_create(_TEST_REPO, 100003)
        ledger.stage_states_json = '{"ISSUE": "completed", "PLAN": "in_progress"}'
        ledger.pr_number = 777
        ledger.save()

        reloaded = PipelineLedger.get_or_create(_TEST_REPO, 100003)
        assert reloaded.stage_states_json == '{"ISSUE": "completed", "PLAN": "in_progress"}'
        assert reloaded.pr_number == 777

    def test_ledger_has_no_ttl(self):
        """Unlike DedupRecord's 2h TTL, the pipeline ledger must be durable
        indefinitely -- it has to outlive every AgentSession lifecycle
        event (crash, completion, takeover)."""
        assert PipelineLedger._meta.ttl is None
