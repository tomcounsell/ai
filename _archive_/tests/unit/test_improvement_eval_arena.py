"""Tests for tools/improvement_eval/arena.py, arm_worker.py, writer_guard.py (#3216).

Covers the isolation substrate: the AF_UNIX socket-path guard, the child-env
dict that never touches the parent environment, the source-hygiene
anti-criteria (no db_claim, no parent REDIS_URL assignment, no pool
re-pointing, no decay-clock ranking, no raw Redis commands), the writer kill
switch, the arena lifecycle, and the two-arm byte-identical corpus proof run
through the real ``arm_worker`` subprocess.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from tests.unit.improvement_eval_runner_support import (  # noqa: F401 -- fixture
    arm_child_embedding_parity,
)
from tools.improvement_eval.errors import InfraFailure

PK_ARENA = "test3216arena"
PK_RETRIEVE = "test3216retrieve"
PK_BM25 = "test3216bm25"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestSocketPathGuard:
    def test_short_path_passes(self):
        from tools.improvement_eval.arena import assert_socket_path_fits

        assert assert_socket_path_fits("/tmp/improve-arm-abc/arm.sock") is None

    def test_overlong_path_raises_naming_length_and_limit(self):
        from tools.improvement_eval.arena import (
            AF_UNIX_SUN_PATH_LIMIT,
            assert_socket_path_fits,
        )

        long_path = "/tmp/" + "x" * 200 + "/arm.sock"
        with pytest.raises(InfraFailure) as exc_info:
            assert_socket_path_fits(long_path)
        message = str(exc_info.value)
        assert str(len(long_path.encode())) in message
        assert str(AF_UNIX_SUN_PATH_LIMIT) in message


class TestChildEnv:
    def test_carries_the_five_arm_keys(self):
        from tools.improvement_eval.arena import build_child_env

        env = build_child_env(
            sock_path="/tmp/arm.sock",
            content_dir="/tmp/content",
            project_key=PK_ARENA,
        )
        assert env["REDIS_URL"] == "unix:///tmp/arm.sock"
        assert env["POPOTO_CONTENT_PATH"] == "/tmp/content"
        assert env["VALOR_PROJECT_KEY"] == PK_ARENA
        assert env["POPOTO_EMBEDDING_INVALIDATION"] == "none"
        assert env["RETRIEVAL_MODE"] == "current"

    def test_pins_the_rrf_path_over_an_ambient_hybrid_setting(self):
        """An ambient RETRIEVAL_MODE=auto never reaches the arm; the harness measures RRF."""
        from tools.improvement_eval.arena import build_child_env

        with mock.patch.dict(os.environ, {"RETRIEVAL_MODE": "auto"}):
            env = build_child_env(
                sock_path="/tmp/arm.sock", content_dir="/tmp/c", project_key=PK_ARENA
            )
        assert env["RETRIEVAL_MODE"] == "current"

    def test_parent_environment_is_untouched(self):
        from tools.improvement_eval.arena import build_child_env

        before = dict(os.environ)
        build_child_env(sock_path="/tmp/arm.sock", content_dir="/tmp/c", project_key=PK_ARENA)
        assert dict(os.environ) == before


class TestSourceHygiene:
    """The Risk 1 properties as source assertions, using the plan's corrected
    anti-criterion forms (a subprocess env-dict key is required; only parent
    re-pointing is forbidden)."""

    def _sources(self):
        package = REPO_ROOT / "tools" / "improvement_eval"
        return list(package.rglob("*.py"))

    def test_no_parent_redis_url_assignment(self):
        import re

        pattern = re.compile(
            r"os\.environ\[[\"']REDIS_URL[\"']\][\s]*="
            r"|os\.environ\.setdefault\([\s]*[\"']REDIS_URL"
            r"|putenv\([\s]*[\"']REDIS_URL"
        )
        hits = [p for p in self._sources() if pattern.search(p.read_text())]
        assert hits == []

    def test_no_db_claim_import(self):
        hits = [p for p in self._sources() if "db_claim" in p.read_text()]
        assert hits == []

    def test_no_canonical_pool_repointing(self):
        hits = [p for p in self._sources() if "set_REDIS_DB_settings" in p.read_text()]
        assert hits == []

    def test_no_decay_clock_ranking(self):
        hits = [p for p in self._sources() if "top_by_decay" in p.read_text()]
        assert hits == []

    def test_no_clock_skew_env_read(self):
        """The clock-gap lever travels in the job spec; no env var can skew a real arm."""
        hits = [p for p in self._sources() if "CLOCK_SKEW" in p.read_text()]
        assert hits == []

    def test_no_raw_redis_commands(self):
        import re

        pattern = re.compile(r"\.(hgetall|hget|hmget|hscan|scan_iter|zadd|zrem|sadd|srem)\(")
        hits = [p for p in self._sources() if pattern.search(p.read_text())]
        assert hits == []

    def test_arm_subprocess_carries_its_own_content_path(self):
        arena = (REPO_ROOT / "tools" / "improvement_eval" / "arena.py").read_text()
        assert "POPOTO_CONTENT_PATH" in arena

    def test_corpus_transfer_goes_through_the_orm_api(self):
        package_text = "".join(p.read_text() for p in self._sources())
        assert "export_records" in package_text
        assert "import_records" in package_text


class TestWriterGuard:
    def test_arm_write_is_refused(self):
        from models.memory import Memory
        from tools.improvement_eval import writer_guard

        record = Memory(
            agent_id="test-3216",
            project_key=PK_ARENA,
            content="writer guard probe",
            importance=5.0,
            source="agent",
        )
        try:
            writer_guard.arm()
            assert writer_guard.is_armed()
            with pytest.raises(InfraFailure):
                record.save()
            with pytest.raises(InfraFailure):
                record.delete()
        finally:
            writer_guard.disarm()
        assert not writer_guard.is_armed()

    def test_disarm_restores_writes(self):
        from models.memory import Memory
        from tools.improvement_eval import writer_guard

        record = Memory(
            agent_id="test-3216",
            project_key=PK_ARENA,
            content="writer guard release probe",
            importance=5.0,
            source="agent",
        )
        writer_guard.arm()
        writer_guard.disarm()
        assert record.save() is not False

    def test_escaped_write_surfaces_as_infra_failure(self):
        """A write the wrapper never saw still fails the job at the digest re-check.

        The wrapper is disabled for the job (the "path the wrapper did not
        cover") and the retrieval step writes a record. Only the independent
        digest re-check in ``handle_job`` can turn that into an
        ``InfraFailure``; remove it and this test goes green on a corrupted arm.
        """
        from tools.improvement_eval import arm_worker, writer_guard
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK_ARENA, "escaped write probe")
        export = export_corpus(PK_ARENA)

        def _writing_retrieval(query_text, project_key, *, limit=10):
            _seed_memory(project_key, "escaped write landed")
            return []

        job = {
            "mode": "retrieve",
            "jsonl": export.jsonl_text,
            "project_key": PK_ARENA,
            "query_text": "anything",
        }
        with (
            mock.patch.object(writer_guard, "arm", lambda: None),
            mock.patch("tools.improvement_eval.retrieval.retrieve_ranked_ids", _writing_retrieval),
        ):
            with pytest.raises(InfraFailure, match="corpus digest changed"):
                arm_worker.handle_job(job)
        assert not writer_guard.is_armed()

    def test_digest_recheck_passes_on_match_and_fails_on_drift(self):
        from tools.improvement_eval import writer_guard

        assert writer_guard.verify_digest_unchanged("abc", "abc", context="probe") is None
        with pytest.raises(InfraFailure):
            writer_guard.verify_digest_unchanged("abc", "def", context="probe")


def _assert_process_gone(pid: int) -> None:
    """The redis-server child is terminated, not merely unreachable."""
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


class TestArenaLifecycle:
    def test_socket_served_while_open_and_cleaned_after(self):
        import popoto.redis_db as rdb

        from tools.improvement_eval.arena import arm_redis_server

        parent_kwargs_before = dict(rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs)
        with arm_redis_server() as arm:
            assert os.path.exists(arm.sock_path)
            assert os.path.isdir(arm.content_dir)
            os.kill(arm.pid, 0)  # alive while the context is open
        assert not os.path.exists(arm.tmpdir)
        _assert_process_gone(arm.pid)
        assert dict(rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs) == parent_kwargs_before

    def test_teardown_survives_an_exception_inside(self):
        from tools.improvement_eval.arena import arm_redis_server

        with pytest.raises(RuntimeError):
            with arm_redis_server() as arm:
                child = arm
                raise RuntimeError("boom")
        assert not os.path.exists(child.tmpdir)
        assert not os.path.exists(child.sock_path)
        _assert_process_gone(child.pid)

    def test_overlong_tmpdir_fails_before_spawning(self, tmp_path):
        from tools.improvement_eval.arena import arm_redis_server

        long_dir = tmp_path / ("y" * 200)
        long_dir.mkdir()
        with pytest.raises(InfraFailure):
            with arm_redis_server(base_tmpdir=str(long_dir)):
                pass
        assert list(long_dir.iterdir()) == []  # the guard's tmpdir is cleaned too

    def test_missing_redis_binary_is_a_named_spawn_failure(self):
        """An unlaunchable redis-server is the "arm would not spawn" condition, tmpdir cleaned."""
        import shutil
        import tempfile

        from tools.improvement_eval.arena import arm_redis_server

        # a short base dir: pytest's tmp_path would trip the socket-length guard first
        base = tempfile.mkdtemp(prefix="arm-spawn-", dir="/tmp")
        try:
            with mock.patch(
                "tools.improvement_eval.arena.subprocess.Popen",
                side_effect=FileNotFoundError("redis-server"),
            ):
                with pytest.raises(InfraFailure, match="would not spawn"):
                    with arm_redis_server(base_tmpdir=base):
                        pass
            assert os.listdir(base) == []
        finally:
            shutil.rmtree(base, ignore_errors=True)


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


def _retrieve_ids(query_text, project_key, limit=10):
    """Rank in-process the way an arm does: RRF path, no query-embedding provider.

    An arm subprocess never has an embedding provider configured, so its RRF
    fusion carries no cosine signal. The parent may or may not have one
    depending on which test imported what first; neutralizing it here keeps
    the in-process ranking comparable to the arm's.
    """
    from tools.improvement_eval.retrieval import retrieve_ranked_ids

    with (
        mock.patch("agent.memory_retrieval._retrieve_memories_hybrid", return_value=[]),
        mock.patch("popoto.fields.embedding_field._default_embedding_provider", None),
    ):
        return retrieve_ranked_ids(query_text, project_key, limit=limit)


class TestTwoArmsReadAByteIdenticalCorpus:
    def test_two_arms_read_a_byte_identical_corpus(self):
        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import canonical_corpus_digest, export_corpus

        _seed_memory(PK_ARENA, "two-arm lighthouse beacon")
        _seed_memory(PK_ARENA, "two-arm grocery errands")
        export = export_corpus(PK_ARENA)

        with arm_redis_server() as arm_a, arm_redis_server() as arm_b:
            job = {"mode": "digest", "jsonl": export.jsonl_text, "project_key": PK_ARENA}
            result_a = run_arm_job(arm_a, PK_ARENA, job)
            result_b = run_arm_job(arm_b, PK_ARENA, job)

        assert result_a["digest"] == result_b["digest"] == export.digest
        assert result_a["manifest"] == result_b["manifest"] == export.manifest_canon
        assert result_a["digest"] == canonical_corpus_digest(export.jsonl_text)

    def test_parent_pool_kwargs_survive_an_arena_context(self):
        import popoto.redis_db as rdb

        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK_ARENA, "parent pool probe")
        export = export_corpus(PK_ARENA)
        before = dict(rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs)
        with arm_redis_server() as arm:
            run_arm_job(
                arm,
                PK_ARENA,
                {"mode": "digest", "jsonl": export.jsonl_text, "project_key": PK_ARENA},
            )
        assert dict(rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs) == before


class TestArmRetrieve:
    def test_arm_ranking_matches_in_process_ranking(self):
        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK_RETRIEVE, "retrieval lighthouse beacon")
        _seed_memory(PK_RETRIEVE, "retrieval grocery errands")
        export = export_corpus(PK_RETRIEVE)
        query = "zxqvkw qvxj retrieval-absent"

        expected = _retrieve_ids(query, PK_RETRIEVE)
        with arm_redis_server() as arm:
            result = run_arm_job(
                arm,
                PK_RETRIEVE,
                {
                    "mode": "retrieve",
                    "jsonl": export.jsonl_text,
                    "project_key": PK_RETRIEVE,
                    "query_text": query,
                    "limit": 10,
                },
            )
        assert result["ids"] == expected
        assert result["digest"] == export.digest

    def test_worker_reports_errors_as_infra_failure(self):
        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK_RETRIEVE, "error path probe")
        export_corpus(PK_RETRIEVE)
        with arm_redis_server() as arm:
            with pytest.raises(InfraFailure):
                run_arm_job(arm, PK_RETRIEVE, {"mode": "no-such-mode"})

    def test_bm25_hit_beyond_the_assembler_pool_runs_ok(self):
        """A real query whose hits outnumber the assembler's pool completes through the shipped arm.

        Under the ambient default ``RETRIEVAL_MODE=auto`` the hybrid path
        requests ``2 * limit`` candidates; any BM25 hit past that pool is
        never selected, and the post-retrieve effects write a competitive
        suppression onto it inside the arm, so the digest re-check fails the
        job. The arm env pins ``RETRIEVAL_MODE=current`` so the four-signal
        RRF path runs and the job reports ``ok``. The test proves it is
        sensitive first: with the pin patched back to ``auto`` the same job
        must fail on the digest, or the guard would reach no code.
        """
        from tools.improvement_eval import arena
        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import export_corpus

        for i in range(3):
            _seed_memory(PK_BM25, f"lighthouse harbor beacon guides the pilot boat {i}")
            _seed_memory(PK_BM25, f"grocery errands and the weekly budget {i}")
        export = export_corpus(PK_BM25)
        assert export.record_count == 6
        job = {
            "mode": "retrieve",
            "jsonl": export.jsonl_text,
            "project_key": PK_BM25,
            "query_text": "lighthouse harbor beacon",
            "limit": 1,  # three hits, a pool of two: one hit is left unselected
        }

        with mock.patch.object(arena, "ARM_RETRIEVAL_MODE", "auto"):
            with arm_redis_server() as arm:
                with pytest.raises(InfraFailure, match="digest changed"):
                    run_arm_job(arm, PK_BM25, dict(job))

        with arm_redis_server() as arm:
            result = run_arm_job(arm, PK_BM25, dict(job))
        assert result["status"] == "ok"
        assert len(result["ids"]) == 1
        assert result["digest"] == export.digest

    def test_two_arms_rank_identically_across_a_clock_gap(self):
        """The shipped ranking path reads persisted state, so a clock gap moves nothing.

        Sensitivity first: the fixture spaces an old, important record and a
        fresh, unimportant one so that decay ranking (``base_score *
        elapsed_days ** -rate``) puts the fresh one first today and the old
        one first 30 days on. The test proves that flip through the decay
        query in-process before asserting the arms agree across the same
        gap; a ranking path that read the decay clock would reorder in
        ``arm_b`` and go red here.
        """
        import time

        from models.memory import Memory
        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import export_corpus

        skew = 30 * 86400
        with mock.patch("time.time", return_value=time.time() - 60 * 86400):
            old = Memory(
                agent_id="test-3216",
                project_key=PK_RETRIEVE,
                content="clock gap lighthouse beacon",
                importance=9.0,
                source="agent",
            )
            assert old.save() is not False
        fresh = Memory(
            agent_id="test-3216",
            project_key=PK_RETRIEVE,
            content="clock gap grocery errands",
            importance=1.0,
            source="agent",
        )
        assert fresh.save() is not False

        def _decay_order():
            rows = Memory.query.filter(project_key=PK_RETRIEVE).top_by_decay("relevance", n=10)
            return [str(r.memory_id) for r in rows]

        now_order = _decay_order()
        real_time = time.time
        with mock.patch("time.time", side_effect=lambda: real_time() + skew):
            later_order = _decay_order()
        assert now_order[0] == str(fresh.memory_id)
        assert later_order[0] == str(old.memory_id)
        assert now_order != later_order, "fixture is insensitive to the clock gap"

        export = export_corpus(PK_RETRIEVE)
        query = "zxqvkw qvxj retrieval-absent"
        job = {
            "mode": "retrieve",
            "jsonl": export.jsonl_text,
            "project_key": PK_RETRIEVE,
            "query_text": query,
            "limit": 10,
        }
        with arm_redis_server() as arm_a, arm_redis_server() as arm_b:
            result_a = run_arm_job(arm_a, PK_RETRIEVE, job)
            result_b = run_arm_job(arm_b, PK_RETRIEVE, job, clock_skew_s=skew)
        assert len(result_a["ids"]) == 2
        assert result_a["ids"] == result_b["ids"]
