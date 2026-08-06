"""Popoto version-floor interlock (issue #2536).

Popoto at and above the floor declared in ``pyproject.toml`` stores a
server-authoritative index-pointer field inside every model hash for each
``IndexedField``. Those pointers are raw Redis Set-key strings, deliberately
not msgpack-encoded, and their names carry an embedded NUL byte. A popoto at
or above the floor skips them when decoding a hash (``popoto/models/encoding.py``
tests ``if b"\\x00" not in key_b``); a below-floor popoto has no such skip and
runs ``msgpack.unpackb`` over them, raising ``ExtraData: unpack(b) received
extra data.``

That would be a harmless read error except for WHERE it fires.
``Model.rebuild_indexes`` (``popoto/models/base.py:2707``) deletes the class set
and every secondary index key FIRST, then scans and eagerly decodes. Under a
below-floor popoto it therefore destroys the index and rebuilds nothing: the
hashes survive, ``query.all()`` returns 0, and every observability surface
reports "zero sessions" with no exception anywhere. That is the 2026-07-14
incident recorded in ``agent/index_drift.py``; that module is the alarm, this
one is the interlock.

Two guard points are required, because there are two distinct teardowns:

1. :func:`install_rebuild_interlock` wraps popoto's ``Model.rebuild_indexes``
   itself, covering every caller in the repo (and any future one) at the seam.
2. ``AgentSession.repair_indexes()`` calls :func:`assert_popoto_floor` at entry,
   because it deletes ``$IndexF:AgentSession:*`` keys BEFORE delegating to
   popoto -- teardown the seam wrapper never sees.

Failure policy is deliberately asymmetric:

- **Runtime fails open.** An unresolvable floor (unreadable ``pyproject.toml``,
  no popoto requirement, no ``>=`` bound, missing/unparseable version) does NOT
  block. ``repair_indexes()`` runs on worker startup and an hourly reflection;
  a false positive there would block index repair fleet-wide, which is a worse
  incident than the one being prevented.
- **Observability fails loud.** Every unresolvable branch emits ``logger.error``
  plus a Sentry capture from inside this module (mirroring
  ``agent/index_drift.py::_report_loud``), and ``tools.doctor`` renders the same
  condition as a FAIL. A silently-disabled interlock must never look healthy.

The floor itself is never written down here. It is parsed out of
``pyproject.toml`` at runtime, because a version predicate drifting out of sync
with reality is the entire class of bug this module exists to prevent.

POPOTO COUPLING POINT -- re-verify on any popoto upgrade:
``popoto.models.base.Model.rebuild_indexes`` (``popoto/models/base.py:2707``)
must still exist and still be the single entry point for index rebuilds. This
module MONKEYPATCHES A THIRD-PARTY CLASSMETHOD. That is a new pattern in this
repo, not an established precedent -- ``models/session_lifecycle.py:502-504``
is a comment convention over popoto-internals reads, and only the
re-verify-on-upgrade note is borrowed from it.
"""

from __future__ import annotations

import logging
import types
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Repo root, resolved relative to this module (same idiom as tools/doctor.py).
PROJECT_DIR = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_DIR / "pyproject.toml"

#: Attribute stamped on the patched classmethod so the install is idempotent
#: and so tests / the doctor check can assert the seam is actually live.
INTERLOCK_SENTINEL = "__popoto_floor_guarded__"

#: Set by :func:`install_rebuild_interlock` to record whether the last install
#: attempt succeeded. False means the seam is NOT live -- which the
#: ``popoto_floor`` doctor check renders as a FAIL, because installing the seam
#: is the one failure this module handles by staying silent at runtime.
INTERLOCK_INSTALLED = False

SATISFIED = "satisfied"
VIOLATED = "violated"
UNRESOLVABLE = "unresolvable"


@dataclass(frozen=True)
class FloorStatus:
    """Outcome of comparing the running popoto against the declared floor."""

    state: str
    installed: str | None
    floor: str | None
    reason: str

    @property
    def ok(self) -> bool:
        """True when the floor is known to be satisfied."""
        return self.state == SATISFIED


def _report_unresolvable(reason: str) -> None:
    """Loudly record that the interlock could not evaluate (and is thus off).

    ERROR-level, not WARNING: this is the state in which the guard is silently
    disabled. The Sentry capture is exception-isolated -- a Sentry outage must
    never propagate into a caller, and the ERROR log above is already the
    signal of record.
    """
    logger.error("[popoto-floor] interlock DISABLED: %s", reason)
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"[popoto-floor] interlock DISABLED: {reason}",
            level="error",
        )
    except Exception:
        logger.warning("[popoto-floor] Sentry capture failed", exc_info=True)


def declared_floor(pyproject_path: Path | None = None) -> str | None:
    """Return the ``>=`` lower bound declared for popoto in ``pyproject.toml``.

    Read at runtime rather than hardcoded: a literal would rot the moment the
    pin moves. Returns ``None`` when the floor cannot be determined -- a missing
    file, malformed TOML, no popoto requirement, or a requirement with no lower
    bound -- which callers classify as "unresolvable" and fail open on.
    """
    import tomllib

    from packaging.requirements import Requirement

    path = pyproject_path or PYPROJECT_PATH
    # The one broad handler in this module's resolver path. Every failure mode
    # of reading and parsing pyproject.toml collapses to the same verdict
    # (unresolvable -> fail open, loudly), so enumerating them adds no signal.
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        for raw in data.get("project", {}).get("dependencies", []):
            req = Requirement(raw)
            if req.name.lower() != "popoto":
                continue
            for spec in req.specifier:
                if spec.operator in (">=", "=="):
                    return spec.version
            return None
        return None
    except Exception as e:
        logger.debug("[popoto-floor] could not parse %s: %r", path, e)
        return None


def installed_popoto_version() -> str | None:
    """Return ``popoto.__version__`` for the popoto this process actually imported.

    Deliberately NOT a package-metadata lookup. Metadata is a static string over
    mutable source and can disagree with reality in both directions -- on the
    machine that motivated #2536, metadata reported a version from a stale
    editable-install ``.pth`` while ``import popoto`` raised
    ``ModuleNotFoundError``. A wrong answer here either blocks index repair
    fleet-wide or lets the index be destroyed, so the oracle must be the module
    object itself.

    Returns ``None`` if popoto is not importable or exposes no ``__version__``;
    callers treat that as unresolvable rather than falling back to metadata.
    """
    try:
        import popoto
    except ImportError as e:
        logger.debug("[popoto-floor] popoto is not importable: %r", e)
        return None
    return getattr(popoto, "__version__", None)


def _popoto_module_path() -> str:
    """Filesystem location of the imported popoto, for the violation message.

    Under an editable install the path is the only way an operator can tell
    which checkout is actually live.
    """
    import sys

    module = sys.modules.get("popoto")
    return getattr(module, "__file__", None) or "<unknown>"


def popoto_floor_satisfied() -> FloorStatus:
    """Classify the running popoto against the floor declared in pyproject.toml.

    Never raises. Returns a :class:`FloorStatus` whose ``state`` is one of
    ``satisfied`` / ``violated`` / ``unresolvable``. Every ``unresolvable``
    outcome is reported loudly (``logger.error`` + Sentry) before returning, so
    a silently-disabled interlock always leaves a trace.
    """
    from packaging.version import InvalidVersion, Version

    floor = declared_floor()
    if floor is None:
        reason = f"no popoto '>=' lower bound found in {PYPROJECT_PATH}"
        _report_unresolvable(reason)
        return FloorStatus(UNRESOLVABLE, None, None, reason)

    installed = installed_popoto_version()
    if installed is None:
        reason = "popoto.__version__ is unavailable in this interpreter"
        _report_unresolvable(reason)
        return FloorStatus(UNRESOLVABLE, None, floor, reason)

    try:
        ok = Version(installed) >= Version(floor)
    except InvalidVersion as e:
        reason = f"unparseable version ({e})"
        _report_unresolvable(reason)
        return FloorStatus(UNRESOLVABLE, installed, floor, reason)

    if ok:
        return FloorStatus(SATISFIED, installed, floor, "")
    return FloorStatus(
        VIOLATED,
        installed,
        floor,
        f"popoto {installed} is below the required floor {floor}",
    )


def _violation_message(status: FloorStatus) -> str:
    """The operator-facing text for a floor violation.

    This message is the entire user-visible surface of the interlock, so it
    names every fact someone needs to act: which interpreter is running, which
    popoto source it actually loaded, what version that is, what is required,
    and how to fix it.
    """
    import sys

    return (
        f"Refusing to rebuild Popoto indexes: {status.reason}.\n"
        f"  interpreter: {sys.executable}\n"
        f"  popoto module: {_popoto_module_path()}\n"
        f"  popoto installed: {status.installed}\n"
        f"  popoto required: >={status.floor}\n"
        f"A below-floor popoto cannot decode the internal index-pointer fields "
        f"written by popoto >={status.floor}, and rebuild_indexes() DELETES every "
        f"index before it discovers that -- so it would destroy the index and "
        f"rebuild nothing (issue #2536).\n"
        f"Re-run under the project venv: {PROJECT_DIR / '.venv' / 'bin' / 'python'}"
    )


def assert_popoto_floor() -> None:
    """Raise ``RuntimeError`` if the running popoto is below the declared floor.

    Raises ONLY on an unambiguous ``violated`` verdict, with both the installed
    version and the floor successfully parsed. Fails open (returns normally) on
    ``unresolvable`` -- see the module docstring for why blocking on an unknown
    is the worse failure. The unresolvable branch is still reported loudly by
    :func:`popoto_floor_satisfied`.
    """
    status = popoto_floor_satisfied()
    if status.state == VIOLATED:
        raise RuntimeError(_violation_message(status))


class _FloorGuardedClassMethod:
    """A ``classmethod`` replacement with a stable per-class bound object.

    Behaves exactly like ``classmethod`` for callers -- ``cls`` binds to the
    CONCRETE subclass, which the generic ``model_class.rebuild_indexes()`` form
    in ``scripts/popoto_index_cleanup.py`` depends on.

    It exists instead of a plain ``classmethod`` for one reason: a
    ``classmethod`` mints a fresh ``MethodType`` on every attribute access, so
    ``Model.rebuild_indexes is Model.rebuild_indexes`` is False even when
    nothing has been touched. That makes the idempotency of this install
    unverifiable from outside -- a double-wrap and a clean re-import look
    identical. Caching the bound object per class makes identity meaningful, so
    a test (and the ``## Verification`` row) can prove a re-import did not
    re-patch.

    The cache is a plain dict, not a ``WeakKeyDictionary``: the bound method it
    stores holds a strong reference to the class anyway, so a weak mapping
    would never collect. Popoto model classes are module-level and live for the
    process lifetime, so this retains nothing that was not already permanent.
    """

    def __init__(self, func):
        self.__func__ = func
        self.__name__ = getattr(func, "__name__", "rebuild_indexes")
        self.__doc__ = getattr(func, "__doc__", None)
        self._bound: dict[type, object] = {}

    def __get__(self, instance, owner=None):
        cls = owner if owner is not None else type(instance)
        bound = self._bound.get(cls)
        if bound is None:
            bound = types.MethodType(self.__func__, cls)
            self._bound[cls] = bound
        return bound


def install_rebuild_interlock() -> bool:
    """Wrap popoto's ``Model.rebuild_indexes`` with the floor guard.

    Idempotent: a second call (or a re-import / reload of ``models``) is a
    no-op, so the original is never double-wrapped and the wrapper object
    stays identical.

    Never raises. If popoto's shape has changed and the classmethod cannot be
    resolved, this reports loudly, leaves ``INTERLOCK_INSTALLED`` False, and
    returns False -- ``models`` is imported by the bridge, the worker, and every
    repo script, so raising here would turn a popoto rename into a fleet
    outage. The missing seam is surfaced by the ``popoto_floor`` doctor check
    and by tests instead.

    Returns:
        True if the interlock is live after this call, False otherwise.
    """
    global INTERLOCK_INSTALLED
    # Broad by contract: this function must never raise on any import path.
    try:
        from popoto.models.base import Model

        existing = Model.__dict__.get("rebuild_indexes")
        if existing is None:
            _report_unresolvable(
                "popoto.models.base.Model has no 'rebuild_indexes' -- seam not installed"
            )
            INTERLOCK_INSTALLED = False
            return False

        if getattr(Model.rebuild_indexes, INTERLOCK_SENTINEL, False):
            INTERLOCK_INSTALLED = True
            return True

        original = getattr(existing, "__func__", existing)

        def guarded_rebuild_indexes(cls, *args, **kwargs):
            assert_popoto_floor()
            return original(cls, *args, **kwargs)

        guarded_rebuild_indexes.__name__ = getattr(original, "__name__", "rebuild_indexes")
        guarded_rebuild_indexes.__doc__ = getattr(original, "__doc__", None)
        guarded_rebuild_indexes.__wrapped__ = original
        setattr(guarded_rebuild_indexes, INTERLOCK_SENTINEL, True)

        Model.rebuild_indexes = _FloorGuardedClassMethod(guarded_rebuild_indexes)
        INTERLOCK_INSTALLED = True
        return True
    except Exception as e:
        _report_unresolvable(f"could not install the rebuild interlock: {e!r}")
        INTERLOCK_INSTALLED = False
        return False


def interlock_installed() -> bool:
    """Whether the seam interlock is currently live on popoto's ``Model``.

    Checks the live sentinel rather than trusting the module flag alone, so a
    later reassignment of ``Model.rebuild_indexes`` by anything else is caught.
    """
    try:
        from popoto.models.base import Model
    except ImportError:
        return False
    return INTERLOCK_INSTALLED and bool(getattr(Model.rebuild_indexes, INTERLOCK_SENTINEL, False))
