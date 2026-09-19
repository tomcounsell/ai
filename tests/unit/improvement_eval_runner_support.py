"""Shared fixtures and helpers for the runner test modules (#3216, task 6).

Both ``test_improvement_eval_runner.py`` and
``test_improvement_eval_runner_guards.py`` drive the real arms (two private
``redis-server`` processes reached only through ``arm_worker`` subprocesses)
against the same small seeded corpus. Every such test costs about 25 seconds,
so the suite is split across two modules: under ``--dist loadfile`` one file
lands on one xdist worker, and a lone worker running past ten minutes trips
``scripts/pytest-clean.sh``'s idle-controller wedge guard. The fixtures are
registered under their plain names (``charter``, ``corpus``) via
``@pytest.fixture(name=...)`` so a test parameter never shadows the import.

Rows land in a claimed test DB (autouse ``redis_test_db``, tests/conftest.py)
under the test-scoped ``project_key`` ``PK``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from tools.improvement_eval import runner

PK = "test3216runner"

APPROVING_JUDGE_DICT = {
    "judge_id": "fake-judge",
    "verdict": "APPROVED",
    "blockers": 0,
    "confidence": 0.9,
}


def _approving_judge(candidate_output, *, blinded_arm_id, trial_id):
    return {"status": "ok", "judge": dict(APPROVING_JUDGE_DICT)}


def _skipping_judge(candidate_output, *, blinded_arm_id, trial_id):
    return {"status": "skipped", "reason": "Judge provider call failed: ConnectionError"}


def _seed_memory(project_key, content):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3216",
        project_key=project_key,
        content=content,
        importance=5.0,
        source="agent",
    )
    assert record.save() is not False
    return record


@pytest.fixture(name="charter")
def charter_fixture(tmp_path):
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


@pytest.fixture(name="corpus")
def corpus_fixture():
    """Two memories, so a ranking has a second place to put the gold id."""
    _seed_memory(PK, "runner lighthouse beacon on the headland")
    _seed_memory(PK, "runner grocery errands for the week")
    from tools.improvement_eval.corpus import export_corpus

    return export_corpus(PK)


QUERIES = [
    {"trial_id": "trial-one", "query_text": "zxqvkw qvxj runner-absent"},
    {"trial_id": "trial-two", "query_text": "zxqvkw qvxj runner-absent again"},
]


def _protocol(corpus, *, incumbent, candidate, batch_size=2, **extra):
    baseline = runner.capture_baseline(PK, QUERIES, incumbent=incumbent, export=corpus)
    full = runner.capture_baseline(PK, QUERIES, incumbent={"limit": 10}, export=corpus)
    queries = []
    for query in QUERIES:
        ranked = full["ranked_ids"][query["trial_id"]]
        assert len(ranked) == 2, "the full ranking must place both seeded records"
        gold = ranked[-1]  # the second-ranked id: an arm limited to one misses it
        queries.append({**query, "gold_id": gold})
    protocol = {
        "batch_size": batch_size,
        "endpoints": ["recall_at_2", "mrr"],
        "holdout_partition": "epoch-test",
        "queries": queries,
        "baseline": baseline,
        "incumbent": incumbent,
        "candidate": candidate,
    }
    protocol.update(extra)
    return protocol


def _freeze(protocol, manifest_extra=None, **experiment_fields) -> ImprovementExperiment:
    ref = runner.freeze_protocol(protocol)
    manifest = {"protocol_ref": ref, "base_revision": "abc123", **(manifest_extra or {})}
    fields = {
        "project_key": PK,
        "created_at": datetime.now(UTC),
        "hypothesis": "a wider candidate finds the gold memory",
        "mechanism": "a larger limit admits the second-ranked record",
        "falsifier": "recall_at_2 does not rise",
        "candidate_surfaces": json.dumps(["tools/improvement_eval/"]),
        "manifest": json.dumps(manifest),
    }
    fields.update(experiment_fields)
    experiment = ImprovementExperiment(**fields)
    assert experiment.save() is not False
    experiment = _reload(experiment.id)
    experiment.contract_digest = runner.compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = datetime.now(UTC)
    assert experiment.save() is not False
    return _reload(experiment.id)


def _reload(experiment_id) -> ImprovementExperiment:
    row = ImprovementExperiment.query.filter(project_key=PK, id=experiment_id).first()
    assert row is not None
    return row


def _reload_evaluation(evaluation) -> ImprovementEvaluation:
    """The row as a later reader sees it, so typed-field hydration is what gets asserted."""
    row = ImprovementEvaluation.query.filter(project_key=PK, id=evaluation.id).first()
    assert row is not None
    return row


def _accepting_protocol(corpus):
    # Incumbent sees one record and misses the gold; the candidate sees both,
    # so recall_at_2 rises from 0 to 1 and mrr from 0 to 0.5 on every trial.
    return _protocol(corpus, incumbent={"limit": 1}, candidate={"limit": 10})


def _rejecting_protocol(corpus):
    # The candidate narrows to one record and loses the gold id every trial.
    return _protocol(corpus, incumbent={"limit": 10}, candidate={"limit": 1})


def _evaluate(experiment, **kwargs):
    kwargs.setdefault("judges", [_approving_judge])
    return runner.evaluate(str(experiment.id), PK, **kwargs)


@pytest.fixture(autouse=True)
def arm_child_embedding_parity(monkeypatch):
    """Give the arm subprocess the same (absent) embedding provider as the parent.

    The corpus identity gate in ``runner.py`` compares the digest the parent
    recorded at export time against the digest an arm reports after restoring
    and re-exporting. ``canonical_corpus_digest`` strips the provider
    *fingerprint* (``embedding_provenance``, ``state.<field>.provenance``) but
    deliberately keeps the vector bytes, so the two processes must resolve the
    same provider or the gate fires on a difference that is an artifact of the
    harness rather than of the corpus.

    Under pytest they do not. ``tests/unit/conftest.py``'s autouse
    ``_no_live_embedding_provider`` nulls popoto's process-global provider in
    the parent, while ``arena.build_child_env`` copies ``os.environ`` wholesale
    into the arm child -- so the child's ``config.memory_defaults.apply_defaults``
    sees ``OPENAI_API_KEY`` and installs a live 1536-dim ``OpenAIProvider``. The
    parent exports vectorless records, the arm exports vector-carrying ones, and
    every run in these modules aborts ``infra_failure`` at the gate (#3355).
    In production no such asymmetry exists: parent and arm both configure the
    provider from the same environment.

    Pinning the key empty in the environment the child inherits restores the
    symmetry at the harness layer, where the asymmetry was introduced. It also
    keeps these unit tests off the live OpenAI embedding endpoint, which the arm
    child was otherwise calling for real. The digest gate itself is untouched --
    it was reporting a true condition.

    The pin is empty-but-present, never ``delenv``. ``configure_embedding_provider``
    reacts to an absent key by re-reading it from ``REPO_ROOT/.env``
    (``agent/embedding_provider.py``), and ``scripts/pytest-clean.sh`` pins
    ``PYTHONPATH`` to the invoking checkout -- so a delete is silently undone in
    any checkout that has the vault ``.env`` symlink, which the main checkout
    does and this worktree does not. ``load_dotenv`` defaults to
    ``override=False`` and treats ``""`` as present, so the empty pin survives
    that fallback and the provider resolves to ``None`` in every checkout.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "")
