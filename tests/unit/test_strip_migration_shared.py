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

    def test_a_blinded_scan_does_no_index_work(self, engine_logger, caplog):
        """The guard's early return, pinned by ORDERING rather than by absence.

        Asserting only ``clean_indexes.assert_not_called()`` is a tautology: with
        zero records ``stripped`` is necessarily 0, so the ``if apply and
        stripped:`` gate already skips the sweep. Deleting the guard's early
        return entirely would leave that assertion green. So this checks that the
        guard's message is the LAST thing emitted -- nothing runs after it.
        """
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(AgentSession, "clean_indexes") as sweep,
            caplog.at_level(logging.INFO, logger=engine_logger.name),
        ):
            shared.run_strip_migration(
                {"anything"}, apply=True, logger=engine_logger, field_names=lambda i: set()
            )

        sweep.assert_not_called()
        messages = [r.getMessage() for r in caplog.records if r.name == engine_logger.name]
        assert messages, "the guard emitted nothing at all"
        assert "ZERO RECORDS SCANNED" in messages[-1], (
            "the guard must be the last word -- anything logged after it means "
            f"execution continued past the early return. Got: {messages}"
        )
        assert not any("Cleaning AgentSession index orphans" in m for m in messages)


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

    def test_it_passes_its_own_detection_function_by_name(self, module_name):
        """Otherwise every ``patch.object(mod, "_raw_field_names")`` goes vacuous.

        The engine takes ``field_names`` as a REQUIRED argument so omitting it
        is a TypeError rather than a silent fallback, but a script could still
        pass some other callable and quietly detach the module-level name the
        test suite patches. This pins the wiring itself.
        """
        mod = importlib.import_module(module_name)
        assert "field_names=_raw_field_names" in inspect.getsource(mod.migrate)

    def test_omitting_the_detection_function_is_an_error_not_a_fallback(self, module_name):
        del module_name  # the property is the engine's, asserted once per script
        with pytest.raises(TypeError):
            shared.run_strip_migration({"stale"}, apply=False, logger=logging.getLogger("x"))

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

    def test_each_v2_reruns_the_same_script_as_its_v1(self):
        """A rename is the auditable re-run mechanism — not a forked script.

        Identity of the function OBJECT is the wrong property to pin: the two
        new v2 helpers are deliberately separate functions so their captured
        output carries a distinguishable label. What must hold is that they
        invoke the same migration SCRIPT.
        """
        from scripts.update.migrations import MIGRATIONS

        for v1, v2 in (
            ("strip_pty_session_fields", "strip_pty_session_fields_v2"),
            ("schema_diet_fields", "schema_diet_fields_v2"),
            ("strip_pid_fields", "strip_pid_fields_v2"),
        ):
            v1_src = inspect.getsource(MIGRATIONS[v1][0])
            v2_src = inspect.getsource(MIGRATIONS[v2][0])
            script = next(
                line.strip().strip('",')
                for line in v1_src.splitlines()
                if line.strip().startswith('"migrate_') and line.strip().endswith('.py",')
            )
            assert script in v2_src, f"{v2} must re-run {script}, the script {v1} runs"

    def test_each_v2_is_separately_attributable_in_the_update_log(self):
        """A re-run whose log lines cannot be told from the original audits nothing.

        ``strip_pid_fields_v2`` is exempt: it is already recorded complete
        fleet-wide and its captured output is #2518's canary gate artifact, so
        its label must not move.
        """
        from scripts.update.migrations import MIGRATIONS

        for v2 in ("strip_pty_session_fields_v2", "schema_diet_fields_v2"):
            assert f'label="{v2}"' in inspect.getsource(MIGRATIONS[v2][0])

    def test_the_v2_entries_run_after_their_v1(self):
        """``MIGRATIONS`` is ordered; a v2 ahead of its v1 would run twice needlessly."""
        from scripts.update.migrations import MIGRATIONS

        order = list(MIGRATIONS)
        for v1, v2 in (
            ("strip_pty_session_fields", "strip_pty_session_fields_v2"),
            ("schema_diet_fields", "schema_diet_fields_v2"),
        ):
            assert order.index(v1) < order.index(v2)

    def test_the_v2_entries_run_before_the_phantom_purge(self):
        """The purge deletes index-bookkeeping hashes the strip's sweep expects.

        ``test_migrations.py`` already pins this for ``strip_pid_fields_v2``;
        the constraint is identical for the two siblings.
        """
        from scripts.update.migrations import MIGRATIONS

        order = list(MIGRATIONS)
        purge = order.index("purge_phantom_agent_sessions")
        for v2 in ("strip_pty_session_fields_v2", "schema_diet_fields_v2"):
            assert order.index(v2) < purge

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


class TestSharedSubprocessRunner:
    """Functional coverage of ``_run_migration_script`` — not source greps.

    ``tests/unit/test_migrate_strip_pid_fields.py``'s module docstring spells out
    why: a ``grep -c 'result.stdout'`` assertion goes green against the exact
    broken state the capture exists to prevent (a capture that faithfully
    records an empty string). So these run a throwaway script and read what
    actually reached the logger.
    """

    @pytest.fixture
    def fake_repo(self, tmp_path):
        """A project dir shaped like the real one: ``scripts/`` + ``.venv/bin/python``."""
        import sys

        (tmp_path / "scripts").mkdir()
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(sys.executable)
        return tmp_path

    def _write(self, repo, name, body):
        path = repo / "scripts" / name
        path.write_text(body)
        return name

    def _run(self, repo, script_name, caplog, **kwargs):
        from scripts.update import migrations as migrations_mod

        with caplog.at_level(logging.INFO, logger=migrations_mod.__name__):
            error = migrations_mod._run_migration_script(repo, script_name, label="probe", **kwargs)
        captured = "\n".join(
            r.getMessage() for r in caplog.records if "[migration:probe]" in r.getMessage()
        )
        return error, captured

    def test_stdout_reaches_the_log_on_the_success_path(self, fake_repo, caplog):
        """The original bug: output logged only on failure, so a good run was blind."""
        name = self._write(fake_repo, "ok.py", "print('did the thing')\n")
        error, captured = self._run(fake_repo, name, caplog)

        assert error is None
        assert "did the thing" in captured
        assert "stdout" in captured

    def test_stderr_reaches_the_log_too(self, fake_repo, caplog):
        """A script reverting to Python's stderr logging default must not go dark."""
        name = self._write(fake_repo, "onstderr.py", "import sys; print('oops', file=sys.stderr)\n")
        error, captured = self._run(fake_repo, name, caplog)

        assert error is None
        assert "oops" in captured

    def test_a_failure_reason_carries_both_tails(self, fake_repo, caplog):
        """A stderr-only reason is empty for every script that logs to stdout."""
        name = self._write(
            fake_repo,
            "boom.py",
            "import sys\nprint('out-side')\nprint('err-side', file=sys.stderr)\nsys.exit(3)\n",
        )
        error, _ = self._run(fake_repo, name, caplog)

        assert error is not None
        assert "exit code 3" in error
        assert "out-side" in error, "the stdout tail is missing from the reason"
        assert "err-side" in error

    def test_a_missing_script_is_reported_not_raised(self, fake_repo, caplog):
        error, _ = self._run(fake_repo, "nope.py", caplog)
        assert error == "migration script not found"

    def test_a_timeout_is_reported_with_its_budget(self, fake_repo, caplog):
        name = self._write(fake_repo, "slow.py", "import time; time.sleep(30)\n")
        error, _ = self._run(fake_repo, name, caplog, timeout=1)
        assert error == "migration timed out after 1s"

    def test_blank_lines_are_not_logged(self, fake_repo, caplog):
        """Migration output is already verbose; do not double it with empties."""
        name = self._write(fake_repo, "gappy.py", "print('a\\n\\n\\nb')\n")
        _, captured = self._run(fake_repo, name, caplog)
        assert [line for line in captured.splitlines() if not line.strip()] == []

    def test_extra_args_are_forwarded(self, fake_repo, caplog):
        """``--apply`` is how five of the six helpers do their actual work."""
        name = self._write(fake_repo, "argecho.py", "import sys; print('ARGS', sys.argv[1:])\n")
        _, captured = self._run(fake_repo, name, caplog, args=("--apply",))
        assert "ARGS ['--apply']" in captured


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
