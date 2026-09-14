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

from tools.improvement_eval.errors import InfraFailure

PK_ARENA = "test3216arena"
PK_RETRIEVE = "test3216retrieve"

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
    def test_carries_the_four_arm_keys(self):
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


class TestArenaLifecycle:
    def test_socket_served_while_open_and_cleaned_after(self):
        import popoto.redis_db as rdb

        from tools.improvement_eval.arena import arm_redis_server

        parent_kwargs_before = dict(rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs)
        with arm_redis_server() as arm:
            assert os.path.exists(arm.sock_path)
            assert os.path.isdir(arm.content_dir)
        assert not os.path.exists(arm.tmpdir)
        assert dict(rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs) == parent_kwargs_before

    def test_teardown_survives_an_exception_inside(self):

        from tools.improvement_eval.arena import arm_redis_server

        with pytest.raises(RuntimeError):
            with arm_redis_server() as arm:
                child = arm
                raise RuntimeError("boom")
        assert not os.path.exists(child.tmpdir)
        assert not os.path.exists(child.sock_path)

    def test_overlong_tmpdir_fails_before_spawning(self, tmp_path):
        from tools.improvement_eval.arena import arm_redis_server

        long_dir = tmp_path / ("y" * 200)
        long_dir.mkdir()
        with pytest.raises(InfraFailure):
            with arm_redis_server(base_tmpdir=str(long_dir)):
                pass


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
    from tools.improvement_eval.retrieval import retrieve_ranked_ids

    with mock.patch("agent.memory_retrieval._retrieve_memories_hybrid", return_value=[]):
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

    def test_two_arms_rank_identically_across_a_clock_gap(self):
        from tools.improvement_eval.arena import arm_redis_server, run_arm_job
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK_RETRIEVE, "clock gap lighthouse beacon")
        _seed_memory(PK_RETRIEVE, "clock gap grocery errands")
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
            result_b = run_arm_job(arm_b, PK_RETRIEVE, job, clock_skew_s=30 * 86400)
        assert result_a["ids"] == result_b["ids"]
