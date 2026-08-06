"""The shared guarded index reconstruction for the rename migrations (#2544).

Five migrations write AgentSession KeyFields through raw Redis and must then
reconstruct the indexes. Before this module they carried five hand-copies of the
same fail-closed branch, with independently worded messages and one of them
ordering ``logger.error`` before ``stats["errors"] += 1``. Only one of the five
was tested, and it was one of the three that ``/update`` never invokes.

That is the same drift hazard #2524 consolidated the strip family to avoid, so
the tests come in the same two shapes:

- **Helper tests** exercise every branch of the single implementation.
- **Cross-script invariants** are parametrized over all five call sites, so a
  future script that hand-copies the guard instead of importing it fails them.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from unittest.mock import MagicMock, patch

import pytest

from models.agent_session import AgentSession
from scripts._migration_index_repair import reconstruct_agent_session_indexes

#: Every migration that must route through the shared helper rather than clone it.
RENAME_SCRIPTS = (
    "scripts.migrate_agent_session_keyfield_rename",
    "scripts.migrate_unify_parent_session_field",
    "scripts.migrate_parent_session_field",
    "scripts.migrate_session_type_pm_to_eng",
    "scripts.migrate_session_type_chat_to_pm",
)

#: The two that `/update` actually invokes (`scripts/update/migrations.py`),
#: with their entrypoint name (they do not share one). Called out because the
#: pre-#2544 coverage tested only an unregistered script.
LIVE_REGISTRY_SCRIPTS = (
    ("scripts.migrate_agent_session_keyfield_rename", "migrate_keys"),
    ("scripts.migrate_unify_parent_session_field", "migrate"),
)


@pytest.fixture
def repair_logger():
    return logging.getLogger("test_migration_index_repair.helper")


def _stats():
    return {"errors": 0}


class TestReconstructFailsClosed:
    """The renames have already landed by the time this runs, so anything short
    of a completed reconstruction must be recorded as an error."""

    def test_a_completed_rebuild_is_clean(self, repair_logger):
        """Positive control -- without it the failure assertions pass vacuously."""
        stats = _stats()
        with patch.object(AgentSession, "repair_indexes", MagicMock(return_value=(3, 42))):
            reconstruct_agent_session_indexes(stats, repair_logger, wrote="Keys were renamed")

        assert stats["errors"] == 0

    def test_a_skipped_repair_is_an_error(self, repair_logger, caplog):
        """``(0, 0)`` means the lock was held and no rebuild ran."""
        stats = _stats()
        with (
            patch.object(AgentSession, "repair_indexes", MagicMock(return_value=(0, 0))),
            caplog.at_level(logging.ERROR, logger=repair_logger.name),
        ):
            reconstruct_agent_session_indexes(stats, repair_logger, wrote="Keys were renamed")

        assert stats["errors"] == 1
        assert any("SKIPPED" in r.getMessage() for r in caplog.records)

    def test_a_raising_repair_is_an_error(self, repair_logger, caplog):
        """Covers ``assert_popoto_floor()`` (#2536), which raises before teardown."""
        stats = _stats()
        with (
            patch.object(
                AgentSession,
                "repair_indexes",
                MagicMock(side_effect=RuntimeError("popoto below floor")),
            ),
            caplog.at_level(logging.ERROR, logger=repair_logger.name),
        ):
            reconstruct_agent_session_indexes(stats, repair_logger, wrote="Keys were renamed")

        assert stats["errors"] == 1
        assert any("Failed to repair indexes" in r.getMessage() for r in caplog.records)

    def test_the_failure_message_names_what_was_written(self, repair_logger, caplog):
        """``logs/update.log`` must say which records are now unreachable."""
        stats = _stats()
        with (
            patch.object(AgentSession, "repair_indexes", MagicMock(return_value=(0, 0))),
            caplog.at_level(logging.ERROR, logger=repair_logger.name),
        ):
            reconstruct_agent_session_indexes(stats, repair_logger, wrote="Fields were written")

        assert any("Fields were written" in r.getMessage() for r in caplog.records)

    def test_it_logs_through_the_callers_logger(self, repair_logger, caplog):
        """Otherwise the update log cannot say WHICH migration failed to reindex."""
        stats = _stats()
        with (
            patch.object(AgentSession, "repair_indexes", MagicMock(return_value=(0, 0))),
            caplog.at_level(logging.ERROR, logger=repair_logger.name),
        ):
            reconstruct_agent_session_indexes(stats, repair_logger, wrote="Keys were renamed")

        assert [r for r in caplog.records if r.name == repair_logger.name]

    def test_errors_accumulate_rather_than_reset(self, repair_logger):
        """Per-record errors counted earlier in the scan must survive this call."""
        stats = {"errors": 2}
        with patch.object(AgentSession, "repair_indexes", MagicMock(return_value=(0, 0))):
            reconstruct_agent_session_indexes(stats, repair_logger, wrote="Keys were renamed")

        assert stats["errors"] == 3


@pytest.mark.parametrize("module_name", RENAME_SCRIPTS)
class TestEveryRenameScriptRoutesThroughTheHelper:
    """The invariants that would have caught the original five-copy drift."""

    def test_it_imports_the_shared_helper(self, module_name):
        mod = importlib.import_module(module_name)
        assert hasattr(mod, "reconstruct_agent_session_indexes"), (
            f"{module_name} does not import the shared guard -- a hand-copied "
            "fail-closed branch is exactly the drift #2544 consolidated away"
        )

    def test_it_calls_the_helper(self, module_name):
        mod = importlib.import_module(module_name)
        src = inspect.getsource(mod)
        assert "reconstruct_agent_session_indexes(" in src

    def test_it_does_not_hand_roll_the_repair(self, module_name):
        """No script may call ``repair_indexes()`` directly and re-implement the check."""
        mod = importlib.import_module(module_name)
        src = inspect.getsource(mod)
        assert "repair_indexes()" not in src.replace(
            "AgentSession.repair_indexes() after all", ""
        ), f"{module_name} calls repair_indexes() itself instead of using the shared helper"

    def test_it_does_not_call_the_raw_rebuild(self, module_name):
        """The raw rebuild skips the version-floor assert, $IndexF cleanup, and A1 shim."""
        mod = importlib.import_module(module_name)
        assert "rebuild_indexes()" not in inspect.getsource(mod)


@pytest.mark.parametrize(("module_name", "entrypoint"), LIVE_REGISTRY_SCRIPTS)
def test_the_live_registry_entries_reach_the_guard(module_name, entrypoint):
    """Wiring proof for the two scripts `/update` actually runs.

    The helper tests above pin the guard's behavior; this pins that these two
    reach it. Both gate the call on their own "did I change anything" counter,
    so a dry run over an empty scan must return clean WITHOUT invoking the
    guard -- otherwise a fresh install would pay a rebuild it does not need.
    """
    mod = importlib.import_module(module_name)

    fake_redis = MagicMock()
    fake_redis.scan.return_value = (0, [])
    fake_popoto = MagicMock()
    fake_popoto.redis_db.get_REDIS_DB.return_value = fake_redis

    with (
        patch.dict("sys.modules", {"popoto": fake_popoto}),
        patch.object(mod, "reconstruct_agent_session_indexes") as guard,
    ):
        stats = getattr(mod, entrypoint)()

    assert stats["errors"] == 0, "an empty scan must not manufacture an error"
    guard.assert_not_called()


@pytest.mark.parametrize(("module_name", "entrypoint"), LIVE_REGISTRY_SCRIPTS)
def test_the_live_registry_entries_fail_closed_on_a_skipped_repair(module_name, entrypoint):
    """The gap the pre-#2544 coverage left: these two had no repair-path test at all.

    Drives each live script through a real rename so its guard fires, with
    ``repair_indexes()`` returning the skipped ``(0, 0)``. The recorded error is
    what each ``main()`` turns into a non-zero exit, which is what makes
    ``run_pending_migrations`` withhold the completion record.
    """
    mod = importlib.import_module(module_name)
    # Seven segments: migrate_keys() skips any other shape outright.
    key = b"AgentSession:99:abc123:seg3:seg4:test-project:eng"

    fake_redis = MagicMock()
    fake_redis.scan.return_value = (0, [key])
    # Present the shape each script treats as "needs migrating": the legacy
    # field present, the canonical one absent.
    fake_redis.hexists.side_effect = lambda k, f: f in ("job_id", "parent_session_id")
    fake_redis.hget.return_value = b"legacy-value"
    fake_popoto = MagicMock()
    fake_popoto.redis_db.get_REDIS_DB.return_value = fake_redis

    with (
        patch.dict("sys.modules", {"popoto": fake_popoto}),
        patch.object(AgentSession, "repair_indexes", MagicMock(return_value=(0, 0))),
    ):
        stats = getattr(mod, entrypoint)(
            **(
                {"dry_run": False}
                if "dry_run" in entrypoint_kwargs(mod, entrypoint)
                else {"apply": True}
            )
        )

    assert stats["errors"] > 0, (
        "a skipped reconstruction leaves records unqueryable; reporting success "
        "would let /update record this migration complete"
    )


def entrypoint_kwargs(mod, entrypoint) -> set[str]:
    """Parameter names of a script's migrate entrypoint (they differ across scripts)."""
    return set(inspect.signature(getattr(mod, entrypoint)).parameters)
