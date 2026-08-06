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
import re
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


def _exploding_detection(instance):
    raise RuntimeError("hkeys exploded")


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
    """A zero-record scan forks on a raw SCAN of ``AgentSession:*`` hashes.

    Blinded by an index rebuild (hashes present) fails closed; a genuinely
    empty keyspace records complete. Both directions are faked here rather
    than raced against a real rebuild (#2543).
    """

    def test_the_guard_fires_and_says_why(self, engine_logger, caplog):
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            stats = shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert stats["total_records"] == 0
        assert stats["keyspace_confirmed_empty"] is False
        assert any("ZERO RECORDS SCANNED" in r.getMessage() for r in caplog.records), (
            "the guard must state its reason, not just return"
        )

    def test_the_guard_reports_how_many_hashes_it_saw(self, engine_logger, caplog):
        """The count is the evidence. Without it the operator cannot tell a
        rebuild window from a keyspace of phantom bookkeeping hashes (#2207)."""
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert any("4006" in r.getMessage() for r in caplog.records)

    def test_a_genuinely_empty_keyspace_is_not_an_error(self, engine_logger, caplog):
        """Nothing to strip and nothing hidden: the migration is complete (#2543)."""
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (0, True)),
            caplog.at_level(logging.INFO, logger=engine_logger.name),
        ):
            stats = shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert stats["total_records"] == 0
        assert stats["keyspace_confirmed_empty"] is True
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
            "a fresh install is not an error condition"
        )

    @pytest.mark.parametrize(
        "scan_result",
        [(0, False), (4006, False), (4006, True)],
        ids=["truncated-and-zero", "truncated-and-populated", "exhaustive-and-populated"],
    )
    def test_only_an_exhaustive_zero_confirms_emptiness(self, engine_logger, scan_result, caplog):
        """Anything short of a completed SCAN returning 0 must fail closed.

        A truncated SCAN hit its iteration cap, so its count is a partial
        undercount -- a 0 from it proves nothing.
        """
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: scan_result),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            stats = shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert stats["keyspace_confirmed_empty"] is False

    def test_a_raising_scan_fails_closed(self, engine_logger, caplog):
        """The discriminator must never turn a Redis blip into a recorded success."""

        def _boom():
            raise RuntimeError("scan exploded")

        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", _boom),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            stats = shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert stats["keyspace_confirmed_empty"] is False
        assert any("hash SCAN failed" in r.getMessage() for r in caplog.records)

    def test_the_guard_logs_through_the_callers_logger(self, engine_logger, caplog):
        """Otherwise ``logs/update.log`` cannot say WHICH migration went blind."""
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
            caplog.at_level(logging.ERROR, logger=engine_logger.name),
        ):
            shared.run_strip_migration(
                {"anything"}, apply=False, logger=engine_logger, field_names=lambda i: set()
            )

        assert [r for r in caplog.records if r.name == engine_logger.name]

    def test_a_blinded_scan_does_no_index_work(self, engine_logger, caplog):
        """A blinded scan touches no indexes and says so. Read the caveat.

        HONEST SCOPE: this pins the observable OUTCOME, not the early ``return``.
        With zero records ``stripped`` is necessarily 0, so the
        ``if apply and stripped:`` gate already skips the sweep -- deleting the
        guard's early return leaves this test green (verified by mutation). The
        early return is defense in depth against a future edit that adds
        unconditional work after the guard; nothing in the current shape of the
        function can distinguish its presence, so no assertion here claims to.

        What IS pinned: the sweep does not run, the operator gets the reason, and
        no index-cleanup line appears in the log.
        """
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
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
            f"the operator must be told why the run refused. Got: {messages}"
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

    def test_a_detection_failure_counts_as_an_error_not_as_clean(self, engine_logger):
        """Fail closed. A swallowed HKEYS error is recorded complete forever.

        If detection failure returned an empty set, the record would be counted
        ``clean``, ``errors`` would stay 0, the exit would be 0, and
        ``run_pending_migrations`` would record the migration permanently
        complete -- manufacturing the very "proof of cleanliness" log line the
        re-run exists to produce, out of a transient Redis blip.
        """
        with patch.object(AgentSession, "query", _query_of(_fake_row())):
            stats = shared.run_strip_migration(
                {"stale"},
                apply=False,
                logger=engine_logger,
                field_names=_exploding_detection,
            )

        assert stats["errors"] == 1
        assert stats["clean"] == 0, "a record whose detection failed is NOT clean"

    def test_the_shared_detection_helper_propagates_hkeys_failures(self, engine_logger):
        """Pinned at the helper, since that is where the swallow used to live."""
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as db:
            db.hkeys.side_effect = RuntimeError("redis blip")
            with pytest.raises(RuntimeError):
                shared.raw_field_names(
                    SimpleNamespace(_redis_key="AgentSession:probe"), engine_logger
                )

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

    def test_it_logs_to_stdout_not_stderr(self, module_name):
        """A stderr default makes the update-log capture record an empty string."""
        mod = importlib.import_module(module_name)
        assert "stream=sys.stdout" in inspect.getsource(mod)

    def test_it_declares_a_non_empty_field_set(self, module_name):
        mod = importlib.import_module(module_name)
        assert mod.STALE_FIELDS, f"{module_name} strips nothing"

    def test_the_guard_fires_for_it_too(self, module_name, caplog):
        """Per-script proof, not just engine-level: the wiring must actually reach it.

        ``total_records == 0`` on its own proves nothing -- it holds for ANY
        implementation under an empty query, guard or no guard, so the earlier
        shape of this test stayed green with the guard deleted from the engine
        (#2564). What is per-script is the LOGGER: the script hands its own
        logger to the engine, so the guard's message arriving on that logger is
        proof this script's wiring reached the guard, and it is what makes
        ``logs/update.log`` name WHICH migration went blind.

        The hash SCAN is faked to the BLINDED answer (#2543). Without that the
        branch taken would depend on whether the test db happens to hold
        ``AgentSession:*`` hashes, and on an empty one the engine would
        correctly take the fresh-install path and never fire the guard at all --
        turning #2564's assertion into a flake rather than a check.
        """
        mod = importlib.import_module(module_name)
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
            caplog.at_level(logging.ERROR, logger=mod.logger.name),
        ):
            stats = mod.migrate(apply=False)

        assert stats["total_records"] == 0
        assert stats["keyspace_confirmed_empty"] is False
        fired = [
            r
            for r in caplog.records
            if r.name == mod.logger.name and "ZERO RECORDS SCANNED" in r.getMessage()
        ]
        assert fired, (
            f"{module_name} returned an empty scan as a silent success: the guard "
            f"did not fire through its own logger. Saw: {[r.getMessage() for r in caplog.records]}"
        )

    def test_its_blinded_scan_exits_two_so_it_is_not_recorded_complete(
        self, module_name, monkeypatch
    ):
        """The whole point of the guard: exit non-zero so the migration re-runs.

        Exit 2 is what stops ``scripts/update/migrations.py`` recording the
        migration permanently complete off a scan blinded by an index rebuild
        (#1720). Asserted per script and end-to-end through ``main`` because the
        engine-level exit-code tests feed ``strip_migration_main`` a literal
        stats dict -- they never prove a real script's scan reaches that path.
        """
        mod = importlib.import_module(module_name)
        monkeypatch.setattr("sys.argv", [module_name])
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
        ):
            assert mod.main() == 2, (
                f"{module_name} reported success on an empty scan; "
                "run_pending_migrations would record it permanently complete"
            )

    def test_its_empty_keyspace_exits_zero_so_a_fresh_install_completes(
        self, module_name, monkeypatch
    ):
        """The other half of the fork, per script and end-to-end through ``main``.

        A fresh install has nothing to strip, so the migration IS complete and
        must be recorded. Before #2543 every script failed here on every
        ``/update``, forever. Same reason as the sibling above for asserting
        through ``main`` rather than on the stats dict: only ``main`` proves a
        real scan reaches the exit-code path.
        """
        mod = importlib.import_module(module_name)
        monkeypatch.setattr("sys.argv", [module_name])
        with (
            patch.object(AgentSession, "query", _empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (0, True)),
        ):
            assert mod.main() == 0, (
                f"{module_name} failed on a genuinely empty keyspace; a fresh "
                "install would retry it on every /update forever (#2543)"
            )


def test_omitting_the_detection_function_is_an_error_not_a_fallback():
    """A default would let a caller silently detach the patched module-level name.

    A property of the engine, so it is asserted once — not once per script.
    """
    with pytest.raises(TypeError):
        shared.run_strip_migration({"stale"}, apply=False, logger=logging.getLogger("x"))


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
            match = re.search(r'"(migrate_\w+\.py)"', v1_src)
            assert match, f"could not find the script name {v1} runs in its source"
            script = match.group(1)
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
        """Reading order should match causal order: the guard fix, then its re-run.

        Correctness does NOT depend on this. ``run_pending_migrations`` skips by
        NAME and the two names differ, so both run exactly once in either order.
        The constraint that actually bites is the phantom-purge one below.
        """
        from scripts.update.migrations import MIGRATIONS

        order = list(MIGRATIONS)
        for v1, v2 in (
            ("strip_pty_session_fields", "strip_pty_session_fields_v2"),
            ("schema_diet_fields", "schema_diet_fields_v2"),
        ):
            assert order.index(v1) < order.index(v2)

    @pytest.mark.parametrize(
        "migration_name",
        [
            "strip_pty_session_fields",
            "schema_diet_fields",
            "strip_pid_fields",
            "strip_pty_session_fields_v2",
            "schema_diet_fields_v2",
            "strip_pid_fields_v2",
        ],
    )
    def test_every_strip_registration_actually_passes_apply(self, migration_name, tmp_path):
        """Without ``--apply`` the strip is a dry run that exits 0 and is recorded complete.

        That is this issue's own failure mode wearing a different hat: the
        migration is marked done in ``data/migrations_completed.json``, the
        reclaim never happens on any machine, and nothing anywhere says so.
        Verified functionally against the argv the helper builds, because the
        generic ``test_extra_args_are_forwarded`` only proves the runner
        forwards whatever it is handed -- it never touches the registry.
        """
        from scripts.update import migrations as migrations_mod
        from scripts.update.migrations import MIGRATIONS

        (tmp_path / "scripts").mkdir()
        for script in (
            "migrate_strip_pty_fields.py",
            "migrate_schema_diet_fields.py",
            "migrate_strip_pid_fields.py",
        ):
            (tmp_path / "scripts" / script).write_text("")

        recorded = {}

        def fake_run(argv, **kwargs):
            recorded["argv"] = argv
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(migrations_mod.subprocess, "run", fake_run):
            MIGRATIONS[migration_name][0](tmp_path)

        assert "--apply" in recorded["argv"], (
            f"{migration_name} runs its script without --apply, so it would be "
            f"recorded complete having written nothing. argv={recorded['argv']}"
        )

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
        """Migration output is already verbose; do not double it with empties.

        Asserted on the RECORD COUNT, not on the joined string. Each record is
        formatted ``[migration:probe] stdout: <line>``, so a logged blank line
        still yields a message whose ``.strip()`` is truthy -- a "no blank lines
        in the output" assertion passes whether or not the suppression exists.
        Four lines in, exactly two non-blank, so exactly two records.
        """
        from scripts.update import migrations as migrations_mod

        name = self._write(fake_repo, "gappy.py", "print('a\\n\\n\\nb')\n")
        with caplog.at_level(logging.INFO, logger=migrations_mod.__name__):
            migrations_mod._run_migration_script(fake_repo, name, label="probe")

        records = [r for r in caplog.records if "[migration:probe]" in r.getMessage()]
        assert len(records) == 2, (
            "expected one record per NON-BLANK line; the two blank lines must be "
            f"suppressed. Got {len(records)}: {[r.getMessage() for r in records]}"
        )

    def test_extra_args_are_forwarded(self, fake_repo, caplog):
        """``--apply`` is how five of the six helpers do their actual work."""
        name = self._write(fake_repo, "argecho.py", "import sys; print('ARGS', sys.argv[1:])\n")
        _, captured = self._run(fake_repo, name, caplog, args=("--apply",))
        assert "ARGS ['--apply']" in captured


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


class TestTheDiscriminatorSeamItself:
    """The one thing every other test in this file fakes.

    Sixteen sites patch ``agent_session_hash_count`` so the fork's branch is
    deterministic, which is right for testing the fork. The cost is that
    nothing exercises the function the fork actually consults -- and that
    function is what decides whether #2543 is fixed or whether a fresh install
    still fails forever. If it returned ``(0, True)`` on a populated keyspace
    the guard would fail OPEN in exactly the #1720 case it exists to catch, and
    every faked test here would stay green.

    So these run against a real Redis. Rows are created and removed through the
    ORM under a recognizable ``dbg-`` project key, never raw Redis.
    """

    _PROJECT_KEY = "dbg-hash-count-seam"

    @pytest.fixture
    def seam_rows(self, redis_test_db):
        created = []

        def _make(n):
            import time

            for i in range(n):
                created.append(
                    AgentSession.create(
                        project_key=self._PROJECT_KEY,
                        status="completed",
                        session_id=f"{self._PROJECT_KEY}-{time.time_ns()}-{i}",
                        chat_id=f"{self._PROJECT_KEY}-chat",
                        message_text="hash count seam",
                        sender_name="Seam",
                    )
                )
            return created

        yield _make

        for row in created:
            try:
                row.delete()
            except Exception:  # swallow-ok: teardown must not mask the result
                pass

    def test_it_reports_zero_on_a_genuinely_empty_keyspace(self, redis_test_db):
        """The fresh-install answer. A wrong non-zero here reinstates #2543."""
        hash_count, exhaustive = shared.agent_session_hash_count()

        assert exhaustive is True
        assert hash_count == 0

    def test_it_sees_hashes_that_exist(self, seam_rows):
        """The blinded-scan answer, and the half that must never read as empty."""
        seam_rows(3)

        hash_count, exhaustive = shared.agent_session_hash_count()

        assert exhaustive is True
        assert hash_count == 3

    def test_it_agrees_with_the_queryable_count_when_the_index_is_healthy(self, seam_rows):
        """Apples-to-apples: the counts may only diverge when the index is blinded.

        If the raw count ran high on a healthy keyspace (companion ``::`` keys
        or non-hash keys leaking in) the fork would read every empty scan as
        blinded and a fresh install would keep failing.
        """
        seam_rows(4)

        hash_count, exhaustive = shared.agent_session_hash_count()

        assert exhaustive is True
        assert hash_count == len(AgentSession.query.all())
