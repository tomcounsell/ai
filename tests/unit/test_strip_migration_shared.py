"""The shared strip-migration engine and the invariants all three scripts share (#2524).

Three migrations reclaim orphaned AgentSession hash fields. They were
near-verbatim clones; #2518 hardened one of them and the other two drifted,
which is how ``migrate_strip_pty_fields`` and ``migrate_schema_diet_fields``
ended up shipping without the zero-record guard and still calling popoto's raw
index rebuild -- the very call that opens the #1720 class-set window the guard
insures against.

So the tests here come in two shapes:

- **Engine tests** exercise ``scripts/_strip_migration.py`` once, since there is
  now exactly one copy of the scan, the guard, and the sweep.
- **Cross-script invariants** are parametrized over all three scripts. Those are
  the ones that would have caught the original drift: any future script that
  clones the pattern instead of importing the engine fails them.

``tests/unit/test_migrate_strip_pid_fields.py`` remains the behavioral
regression gate for the pid migration specifically -- it must keep passing
across this refactor.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts._strip_migration as shared
from models.agent_session import AgentSession

#: Every script that must route through the shared engine rather than clone it.
STRIP_SCRIPTS = (
    "scripts.migrate_strip_pid_fields",
    "scripts.migrate_strip_pty_fields",
    "scripts.migrate_schema_diet_fields",
)


@pytest.fixture
def engine_logger():
    return logging.getLogger("test_strip_migration_shared.engine")


def _empty_query():
    return SimpleNamespace(all=lambda: iter([]))


def _query_of(*instances):
    return SimpleNamespace(all=lambda: iter(instances))


def _fake_row(status="completed", session_id="shared-engine-row"):
    """A stand-in record.

    The engine reads ``status`` and the id, and on the strip path calls
    ``delete(pipeline=...)`` and hands the instance to ``popoto.Model.save``.
    ``delete`` returns the pipeline it was given, matching popoto's chaining
    contract. Deliberately NOT a real AgentSession: the guard/sweep/exit-code
    behavior is what is under test, and a fake keeps this file off Redis.
    """
    return SimpleNamespace(
        status=status,
        agent_session_id=session_id,
        delete=lambda pipeline=None: pipeline,
    )


class TestZeroRecordGuard:
    """An empty scan is indistinguishable from one blinded by an index rebuild."""

    def test_the_guard_fires_and_says_why(self, engine_logger, caplog):
        with (
            patch.object(AgentSession, "query", _empty_query()),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            stats = shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert stats["total_records"] == 0
        assert any("ZERO RECORDS SCANNED" in r.getMessage() for r in caplog.records), (
            "the guard must state its reason, not just return"
        )

    def test_the_guard_logs_through_the_callers_logger(self, engine_logger, caplog):
        """Otherwise ``logs/update.log`` cannot say WHICH migration went blind."""
        with (
            patch.object(AgentSession, "query", _empty_query()),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert [r for r in caplog.records if r.name == engine_logger.name]

    def test_the_guard_returns_before_the_index_sweep(self, engine_logger):
        """A blinded scan must not touch indexes on its way out."""
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(AgentSession, "clean_indexes") as sweep,
        ):
            shared.run_strip_migration(
                {"anything"}, apply=True, logger=engine_logger, field_names=lambda i: set()
            )
        sweep.assert_not_called()


class TestPerRecordIsolation:
    def test_one_exploding_record_does_not_abort_the_scan(self, engine_logger):
        rows = [_fake_row(session_id="a"), _fake_row(session_id="b")]
        calls = {"n": 0}

        def flaky(instance):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("hkeys exploded")
            return set()

        with patch.object(AgentSession, "query", _query_of(*rows)):
            stats = shared.run_strip_migration(
                {"stale"}, apply=False, logger=engine_logger, field_names=flaky
            )

        assert stats["errors"] == 1
        assert stats["clean"] == 1, "the scan continued past the failing record"
        assert stats["total_records"] == 2

    def test_non_terminal_rows_are_deferred_never_rewritten(self, engine_logger):
        """The [DESTRUCTIVE] No-Go: a live row is not rewritten under the worker."""
        row = _fake_row(status="in_progress")

        with (
            patch.object(AgentSession, "query", _query_of(row)),
            patch("popoto.Model.save") as save,
        ):
            stats = shared.run_strip_migration(
                {"stale"},
                apply=True,
                logger=engine_logger,
                field_names=lambda i: {"stale"},
            )

        assert stats["deferred_non_terminal"] == 1
        assert stats["stripped"] == 0
        save.assert_not_called()

    def test_dry_run_writes_nothing(self, engine_logger):
        row = _fake_row()

        with (
            patch.object(AgentSession, "query", _query_of(row)),
            patch("popoto.Model.save") as save,
            patch.object(AgentSession, "clean_indexes") as sweep,
        ):
            stats = shared.run_strip_migration(
                {"stale"},
                apply=False,
                logger=engine_logger,
                field_names=lambda i: {"stale"},
            )

        assert stats["stripped"] == 1, "a dry run still REPORTS what it would do"
        save.assert_not_called()
        sweep.assert_not_called()


class TestIndexSweep:
    def test_sweep_is_skipped_when_nothing_was_stripped(self, engine_logger):
        with (
            patch.object(AgentSession, "query", _query_of(_fake_row())),
            patch.object(AgentSession, "clean_indexes") as sweep,
        ):
            stats = shared.run_strip_migration(
                {"stale"}, apply=True, logger=engine_logger, field_names=lambda i: set()
            )

        assert stats["stripped"] == 0
        sweep.assert_not_called()

    def test_sweep_failure_is_swallowed(self, engine_logger):
        """Best-effort: the per-record rewrites already committed."""
        with (
            patch.object(AgentSession, "query", _query_of(_fake_row())),
            patch.object(AgentSession, "clean_indexes", side_effect=RuntimeError("boom")) as sweep,
            patch("popoto.redis_db.POPOTO_REDIS_DB"),
            patch("popoto.Model.save"),
        ):
            stats = shared.run_strip_migration(
                {"stale"},
                apply=True,
                logger=engine_logger,
                field_names=lambda i: {"stale"},
            )

        assert stats["stripped"] == 1
        assert sweep.called


class TestExitCodes:
    """Exit 2 must stay separable from exit 1 in ``logs/update.log``."""

    def _main(self, stats, engine_logger, monkeypatch):
        monkeypatch.setattr("sys.argv", ["migrate_x.py"])
        return shared.strip_migration_main(
            script_name="migrate_x",
            description="test",
            migrate=lambda apply=False: stats,
            logger=engine_logger,
        )

    def test_zero_records_exits_two(self, engine_logger, monkeypatch):
        stats = {"total_records": 0, "clean": 0, "stripped": 0, "errors": 0}
        assert self._main(stats, engine_logger, monkeypatch) == 2

    def test_per_record_errors_exit_one(self, engine_logger, monkeypatch):
        stats = {"total_records": 5, "clean": 4, "stripped": 0, "errors": 1}
        assert self._main(stats, engine_logger, monkeypatch) == 1

    def test_a_clean_scan_exits_zero(self, engine_logger, monkeypatch):
        stats = {"total_records": 5, "clean": 5, "stripped": 0, "errors": 0}
        assert self._main(stats, engine_logger, monkeypatch) == 0

    def test_zero_records_wins_over_errors(self, engine_logger, monkeypatch):
        """A blinded scan is the more useful diagnosis; report it, not the count."""
        stats = {"total_records": 0, "clean": 0, "stripped": 0, "errors": 3}
        assert self._main(stats, engine_logger, monkeypatch) == 2


@pytest.mark.parametrize("module_name", STRIP_SCRIPTS)
class TestEveryStripScriptSharesTheEngine:
    """The invariants that would have caught the original three-way drift."""

    def test_it_does_not_clone_the_engine(self, module_name):
        mod = importlib.import_module(module_name)
        src = inspect.getsource(mod)
        assert "AgentSession.query.all()" not in src, (
            f"{module_name} reimplements the scan instead of importing the engine — "
            "that is exactly how the guard drifted between the three copies"
        )
        assert "def run_strip_migration" not in src

    def test_it_never_calls_the_raw_index_rebuild(self, module_name):
        mod = importlib.import_module(module_name)
        assert "rebuild_indexes" not in inspect.getsource(mod)

    def test_its_migrate_routes_through_the_engine(self, module_name):
        mod = importlib.import_module(module_name)
        assert "run_strip_migration" in inspect.getsource(mod.migrate)

    def test_it_logs_to_stdout_not_stderr(self, module_name):
        """A stderr default makes the update-log capture record an empty string."""
        mod = importlib.import_module(module_name)
        assert "stream=sys.stdout" in inspect.getsource(mod)

    def test_it_declares_a_non_empty_field_set(self, module_name):
        mod = importlib.import_module(module_name)
        assert mod.STALE_FIELDS, f"{module_name} strips nothing"

    def test_the_guard_fires_for_it_too(self, module_name):
        """Per-script proof, not just engine-level: the wiring must actually reach it."""
        mod = importlib.import_module(module_name)
        with patch.object(AgentSession, "query", _empty_query()):
            stats = mod.migrate(apply=False)
        assert stats["total_records"] == 0


class TestStaleFieldSetsAreDisjoint:
    """Overlapping sets would mean two migrations racing to rewrite the same rows."""

    def test_no_field_is_claimed_by_two_migrations(self):
        seen: dict[str, str] = {}
        for name in STRIP_SCRIPTS:
            mod = importlib.import_module(name)
            for field_name in mod.STALE_FIELDS:
                assert field_name not in seen, (
                    f"{field_name!r} is claimed by both {seen.get(field_name)} and {name}"
                )
                seen[field_name] = name


class TestRegistry:
    """Both siblings get the rename-and-rerun treatment (#2524, Decision 1)."""

    def test_both_v2_entries_are_registered(self):
        from scripts.update.migrations import MIGRATIONS

        assert "strip_pty_session_fields_v2" in MIGRATIONS
        assert "schema_diet_fields_v2" in MIGRATIONS

    def test_each_v2_points_at_the_same_function_as_its_v1(self):
        """A rename is the auditable re-run mechanism — not a forked script."""
        from scripts.update.migrations import MIGRATIONS

        for v1, v2 in (
            ("strip_pty_session_fields", "strip_pty_session_fields_v2"),
            ("schema_diet_fields", "schema_diet_fields_v2"),
            ("strip_pid_fields", "strip_pid_fields_v2"),
        ):
            assert MIGRATIONS[v1][0] is MIGRATIONS[v2][0], (
                f"{v2} must re-run {v1}'s function, not a copy of it"
            )

    def test_the_v2_entries_run_after_their_v1(self):
        """``MIGRATIONS`` is ordered; a v2 ahead of its v1 would run twice needlessly."""
        from scripts.update.migrations import MIGRATIONS

        order = list(MIGRATIONS)
        for v1, v2 in (
            ("strip_pty_session_fields", "strip_pty_session_fields_v2"),
            ("schema_diet_fields", "schema_diet_fields_v2"),
        ):
            assert order.index(v1) < order.index(v2)

    def test_every_subprocess_helper_captures_output(self):
        """The #2524 generalization: no helper discards its subprocess's record."""
        from scripts.update import migrations as migrations_mod

        for helper_name in (
            "_migrate_agent_session_keyfield_rename",
            "_migrate_unify_parent_session_field",
            "_migrate_steering_queue_drain",
            "_migrate_strip_pty_session_fields",
            "_migrate_schema_diet_fields",
            "_migrate_strip_pid_fields",
        ):
            src = inspect.getsource(getattr(migrations_mod, helper_name))
            assert "_run_migration_script" in src, (
                f"{helper_name} still shells out on its own, so its output is discarded"
            )

    def test_the_shared_runner_logs_both_streams_on_success(self):
        src = inspect.getsource(
            importlib.import_module("scripts.update.migrations")._run_migration_script
        )
        assert "result.stdout" in src
        assert "result.stderr" in src
        # The logging must not sit behind the returncode check.
        assert src.index("logger.info") < src.index("if result.returncode != 0")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
