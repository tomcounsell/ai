"""Popoto version-floor interlock (issue #2536).

The bug this guards: a below-floor popoto cannot decode the internal
index-pointer fields an at-or-above-floor popoto writes into each model hash.
``Model.rebuild_indexes`` deletes every index BEFORE it discovers that, so a
below-floor interpreter destroys the index and rebuilds nothing -- silently,
because reads use the lazy decoder and keep working right up to detonation.

Every test here drives the guard by monkeypatching the *reported* popoto
version, never by installing a real below-floor popoto. That is load-bearing:
it means the guard raises before any Redis call, so these tests can assert the
no-mutation property without ever risking a real rebuild.
"""

from __future__ import annotations

import logging

import pytest

from config import popoto_floor
from config.popoto_floor import (
    SATISFIED,
    UNRESOLVABLE,
    VIOLATED,
    FloorStatus,
    assert_popoto_floor,
    declared_floor,
    install_rebuild_interlock,
    installed_popoto_version,
    interlock_installed,
    popoto_floor_satisfied,
)

BELOW = "1.7.1"


def _write_pyproject(tmp_path, body: str):
    p = tmp_path / "pyproject.toml"
    p.write_text(body)
    return p


# --------------------------------------------------------------------------
# Floor resolution
# --------------------------------------------------------------------------


def test_declared_floor_reads_the_real_pyproject():
    """The floor comes from pyproject.toml, never a hardcoded literal."""
    floor = declared_floor()
    assert floor is not None, "the repo's own pyproject.toml must declare a popoto floor"


def test_declared_floor_parses_lower_bound(tmp_path):
    path = _write_pyproject(
        tmp_path,
        '[project]\nname = "x"\ndependencies = ["httpx>=1.0", "popoto>=9.9.9"]\n',
    )
    assert declared_floor(path) == "9.9.9"


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("no popoto requirement", '[project]\nname = "x"\ndependencies = ["httpx>=1.0"]\n'),
        ("no lower bound", '[project]\nname = "x"\ndependencies = ["popoto"]\n'),
        ("no dependencies", '[project]\nname = "x"\n'),
        ("malformed toml", "this is not = valid toml ["),
    ],
)
def test_declared_floor_unresolvable_inputs(tmp_path, label, body):
    """Every unresolvable shape returns None rather than raising."""
    assert declared_floor(_write_pyproject(tmp_path, body)) is None, label


def test_declared_floor_missing_file(tmp_path):
    assert declared_floor(tmp_path / "nope.toml") is None


# --------------------------------------------------------------------------
# The version oracle
# --------------------------------------------------------------------------


def test_oracle_is_the_imported_module_not_metadata(monkeypatch):
    """The oracle must be popoto.__version__, not package metadata.

    Regression guard for the round-2 critique finding: on the machine that
    motivated #2536, metadata reported a version for a popoto that could not
    even be imported (stale editable-install .pth). Metadata is a static string
    over mutable source; a wrong answer here either blocks index repair
    fleet-wide or lets the index be destroyed.
    """
    import popoto

    monkeypatch.setattr(popoto, "__version__", "4.2.0", raising=False)
    assert installed_popoto_version() == "4.2.0"


def test_oracle_returns_none_when_version_attribute_absent(monkeypatch):
    import popoto

    monkeypatch.delattr(popoto, "__version__", raising=False)
    assert installed_popoto_version() is None


def test_source_has_no_metadata_version_lookup():
    """importlib.metadata must not be the version source (see the test above)."""
    import inspect

    src = inspect.getsource(popoto_floor)
    assert "importlib.metadata" not in src
    assert "metadata.version" not in src


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_status_satisfied_on_this_interpreter():
    """The repo venv satisfies its own floor -- the guard must not fire here."""
    assert popoto_floor_satisfied().state == SATISFIED


def test_status_violated_below_floor(monkeypatch):
    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: BELOW)
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: "1.8.0")
    status = popoto_floor_satisfied()
    assert status.state == VIOLATED
    assert status.installed == BELOW
    assert status.floor == "1.8.0"


def test_version_comparison_is_semantic_not_lexical(monkeypatch):
    """'1.10.0' > '1.8.0' semantically, even though it sorts lower as a string."""
    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: "1.10.0")
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: "1.8.0")
    assert popoto_floor_satisfied().state == SATISFIED


@pytest.mark.parametrize(
    ("label", "floor", "installed"),
    [
        ("floor unresolvable", None, "1.8.0"),
        ("version unavailable", "1.8.0", None),
        ("unparseable version", "1.8.0", "not-a-version"),
    ],
)
def test_unresolvable_branches_fail_open_but_loud(monkeypatch, caplog, label, floor, installed):
    """Every uncertainty is ERROR-level, not a WARNING nobody reads.

    An unresolvable floor means the interlock is DISABLED. Runtime still fails
    open (blocking index repair fleet-wide would be the worse incident), so the
    log is the only signal that the guard is off.
    """
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: floor)
    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: installed)

    with caplog.at_level(logging.ERROR, logger=popoto_floor.__name__):
        status = popoto_floor_satisfied()

    assert status.state == UNRESOLVABLE, label
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        f"{label}: unresolvable must emit ERROR, not WARNING"
    )
    # Fails OPEN: an unresolvable verdict must never raise.
    assert_popoto_floor()


def test_sentry_failure_cannot_crash_the_caller(monkeypatch):
    """A Sentry outage must not propagate -- the ERROR log is the signal of record."""
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: None)

    import sentry_sdk

    def _boom(*a, **k):
        raise RuntimeError("sentry down")

    monkeypatch.setattr(sentry_sdk, "capture_message", _boom)
    assert popoto_floor_satisfied().state == UNRESOLVABLE


# --------------------------------------------------------------------------
# The raising assertion
# --------------------------------------------------------------------------


def test_assert_raises_with_all_four_actionable_facts(monkeypatch):
    """The message is the whole user-facing surface; a vague one is barely a fix."""
    import sys

    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: BELOW)
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: "1.8.0")

    with pytest.raises(RuntimeError) as exc:
        assert_popoto_floor()

    msg = str(exc.value)
    assert sys.executable in msg, "must name the running interpreter"
    assert BELOW in msg, "must name the installed version"
    assert "1.8.0" in msg, "must name the required floor"
    assert ".venv/bin/python" in msg, "must name the remedy"


def test_assert_is_silent_when_satisfied():
    assert_popoto_floor()


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------


def test_interlock_is_installed_by_importing_models():
    """A silently-missing seam must fail CI, not pass unnoticed in production."""
    import models  # noqa: F401

    assert interlock_installed()


def test_install_is_idempotent():
    """Re-import must not double-wrap and corrupt the __wrapped__ chain."""
    from popoto.models.base import Model

    import models  # noqa: F401

    # Identity on the bound object is meaningful HERE and nowhere else in the
    # stdlib: `_FloorGuardedClassMethod` caches one bound object per class
    # precisely so a re-install is distinguishable from a double-wrap. A plain
    # `classmethod` mints a fresh MethodType per access and both cases would
    # look identical. Do not "simplify" the descriptor away on the assumption
    # that this comparison is meaningless -- it is what makes the assertion real.
    before_bound = Model.rebuild_indexes
    before_func = Model.__dict__["rebuild_indexes"].__func__

    assert install_rebuild_interlock() is True

    assert Model.rebuild_indexes is before_bound, "re-install replaced the bound object"
    assert Model.__dict__["rebuild_indexes"].__func__ is before_func, "double-wrapped"


def test_install_never_raises_when_popoto_shape_changes(monkeypatch):
    """models/__init__ is on the bridge/worker import path -- raising here is an outage."""
    import popoto.models.base as base

    class Bare:
        pass

    monkeypatch.setattr(base, "Model", Bare)
    assert install_rebuild_interlock() is False


def test_interlock_installed_reports_the_live_sentinel_not_a_stale_flag(monkeypatch):
    """A failed install attempt against a fake Model must not veto ground truth.

    `install_rebuild_interlock()` latches `INTERLOCK_INSTALLED = False` when it
    cannot resolve popoto's shape. Nothing restores that flag -- and nothing can,
    since a later `import models` is a no-op once the module is in `sys.modules`.
    If the flag were allowed to veto the sentinel, one such attempt anywhere in
    the process would make every later caller (including the doctor check)
    report the seam as missing while it is in fact live on the real `Model`.
    """
    import popoto.models.base as base

    import models  # noqa: F401

    class Bare:
        pass

    monkeypatch.setattr(base, "Model", Bare)
    assert install_rebuild_interlock() is False
    assert popoto_floor.INTERLOCK_INSTALLED is False
    monkeypatch.undo()

    assert interlock_installed(), (
        "the seam is live on the real Model -- a stale install flag must not hide it"
    )


def test_interlock_installed_is_false_when_the_live_seam_is_gone():
    """Ground truth in the other direction: no sentinel means not installed.

    Exercised by actually replacing `Model.rebuild_indexes` with an unguarded
    classmethod, not by poking the bookkeeping flag -- the doctor's seam-missing
    FAIL must key off the real condition.
    """
    from popoto.models.base import Model

    import models  # noqa: F401
    from tools.doctor import _check_popoto_floor

    guarded = Model.__dict__["rebuild_indexes"]
    original = getattr(guarded, "__func__", guarded)
    Model.rebuild_indexes = classmethod(getattr(original, "__wrapped__", original))
    try:
        assert not interlock_installed()
        result = _check_popoto_floor()
    finally:
        Model.rebuild_indexes = guarded

    assert not result.passed, "a missing seam must render as a doctor FAIL"
    assert "NOT installed" in result.message
    assert interlock_installed(), "the seam must be restored for the rest of the suite"


def test_interception_covers_subclasses_and_generic_form(monkeypatch):
    """Named, aliased, and generic `model_class.rebuild_indexes()` all traverse the seam.

    This is what makes a single seam viable instead of editing ten call sites.
    """
    import models  # noqa: F401
    from models.agent_session import AgentSession
    from models.telegram import TelegramMessage

    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: BELOW)
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: "1.8.0")

    for model_class in (AgentSession, TelegramMessage):
        with pytest.raises(RuntimeError, match="Refusing to rebuild"):
            model_class.rebuild_indexes()


# --------------------------------------------------------------------------
# The no-mutation property (the load-bearing regression test)
# --------------------------------------------------------------------------


def test_repair_indexes_deletes_nothing_when_below_floor(monkeypatch):
    """`repair_indexes()` must raise BEFORE its own $IndexF teardown.

    This is the bug in miniature. `repair_indexes()` deletes every
    `$IndexF:AgentSession:*` key before it ever delegates to popoto, so the seam
    interlock alone fires too late -- hence the entry guard. If that guard is
    removed or relocated below the scan, this test fails with indexes missing.

    Safe by construction: the monkeypatched version makes the guard raise before
    any Redis call, so no real rebuild is ever attempted.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    import models  # noqa: F401
    from models.agent_session import AgentSession

    class_set_key = AgentSession._meta.db_class_set_key.redis_key
    index_pattern = f"$IndexF:{AgentSession.__name__}:*"

    before_members = POPOTO_REDIS_DB.scard(class_set_key)
    before_indexes = sorted(POPOTO_REDIS_DB.keys(index_pattern))

    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: BELOW)
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: "1.8.0")

    with pytest.raises(RuntimeError, match="Refusing to rebuild"):
        AgentSession.repair_indexes()

    assert POPOTO_REDIS_DB.scard(class_set_key) == before_members, (
        "the class set was modified -- the entry guard fired too late"
    )
    assert sorted(POPOTO_REDIS_DB.keys(index_pattern)) == before_indexes, (
        "$IndexF keys were deleted -- the entry guard fired too late"
    )


def test_no_indexed_field_shim_leaks_after_a_guarded_raise(monkeypatch):
    """The guard raises before any shim is installed, so none can be left behind."""
    from popoto.fields.indexed_field_mixin import IndexedFieldMixin

    import models  # noqa: F401
    from models.agent_session import AgentSession

    indexed = [f for f in AgentSession._meta.fields.values() if isinstance(f, IndexedFieldMixin)]
    assert indexed, "AgentSession must have at least one IndexedField for this to mean anything"

    # Bound methods are rebuilt per access; compare the underlying functions.
    def _fn(m):
        return getattr(m, "__func__", m)

    originals = {id(f): _fn(f.on_save) for f in indexed}

    monkeypatch.setattr(popoto_floor, "installed_popoto_version", lambda: BELOW)
    monkeypatch.setattr(popoto_floor, "declared_floor", lambda *a, **k: "1.8.0")

    with pytest.raises(RuntimeError):
        AgentSession.repair_indexes()

    for f in indexed:
        assert _fn(f.on_save) is originals[id(f)], "an on_save shim leaked past the guard"


# --------------------------------------------------------------------------
# Doctor rendering
# --------------------------------------------------------------------------


def test_doctor_check_passes_on_this_machine():
    from tools.doctor import _check_popoto_floor

    result = _check_popoto_floor()
    assert result.passed, result.message
    assert result.name == "popoto_floor"
    assert result.category == "Environment"


def test_doctor_check_fails_loudly_below_floor(monkeypatch):
    from tools.doctor import _check_popoto_floor

    monkeypatch.setattr(
        popoto_floor,
        "popoto_floor_satisfied",
        lambda: FloorStatus(VIOLATED, BELOW, "1.8.0", "below floor"),
    )
    result = _check_popoto_floor()
    assert not result.passed
    assert result.fix and ".venv/bin/python" in result.fix


def test_doctor_check_fails_on_unresolvable_not_pass_with_note(monkeypatch):
    """CheckResult has no degraded state, so PASS-with-note collapses to passed=True.

    A silently-disabled interlock must not render as healthy. Runtime still
    fails open; only the diagnostic fails loud.
    """
    from tools.doctor import _check_popoto_floor

    monkeypatch.setattr(
        popoto_floor,
        "popoto_floor_satisfied",
        lambda: FloorStatus(UNRESOLVABLE, None, None, "cannot resolve"),
    )
    result = _check_popoto_floor()
    assert not result.passed, "unresolvable must FAIL, not pass with a note"
    assert result.fix


def test_doctor_check_is_registered():
    from tools.doctor import get_checks

    names = {getattr(fn, "__name__", "") for fn in get_checks()}
    assert "_check_popoto_floor" in names
