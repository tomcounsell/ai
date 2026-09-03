"""
Shared test fixtures for Valor AI tests.
"""

import atexit
import gc
import logging
import os
import subprocess
import sys
import time
import warnings
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from tests import db_claim
from tests.db_claim import (
    claim_scratch_test_db,
    claim_test_db,
    claimed_test_dbs,
    release_test_db_claim,
)

# --- Un-awaited-coroutine leak guardrail (#2120) --------------------------
# A test that hands an eagerly-created coroutine to a seam that drops it (never
# awaited, never closed) leaks it: CPython finalizes it later and emits
# `coroutine '...' was never awaited`. When the coroutine is held alive inside
# an event-loop / task reference cycle, that finalization is deferred to a
# session-level gc.collect(), where the whole batch finalizes at once and — on a
# contended machine — wedges teardown before junitxml is written (the ~99%
# full-suite hang, #2118/#2120).
#
# This guardrail runs one gc.collect() at each test's teardown inside a warning
# recorder. Any captured "never awaited" RuntimeWarning is RE-EMITTED as a loud,
# test-attributed RuntimeWarning, so a silent session-teardown wedge becomes an
# attributable per-test signal — and, under `-W error::RuntimeWarning`, a
# per-test teardown failure (fail-fast). It SURFACES leaks; it never silences
# them. Set COROUTINE_LEAK_GUARD=0 to disable (e.g. to isolate its own cost).
_COROUTINE_LEAK_GUARD_ENABLED = os.environ.get("COROUTINE_LEAK_GUARD", "1") != "0"


def pytest_runtest_teardown(item, nextitem):
    """Surface un-awaited-coroutine leaks as a loud, test-attributed warning.

    Cycle-held leaked coroutines are only finalized by gc.collect(); running one
    here inside a warning recorder attributes the leak to the finishing test
    instead of letting it accumulate into a silent session-teardown wedge. The
    re-emitted RuntimeWarning is fatal under `-W error::RuntimeWarning`.

    Attribution is best-effort: a coroutine created in test A but not collected
    until B's teardown is attributed to B. That is acceptable — the goal is to
    make the *class* of leak loud and locatable, not forensically perfect.
    """
    if not _COROUTINE_LEAK_GUARD_ENABLED:
        return
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gc.collect()
    except Exception:
        # The guardrail must never itself break a teardown.
        return
    leaks = [
        str(w.message)
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
    for msg in leaks:
        warnings.warn(
            f"un-awaited coroutine leak surfaced at teardown of {item.nodeid}: {msg}",
            RuntimeWarning,
            stacklevel=2,
        )


_logger = logging.getLogger("tests.conftest")

# ---------------------------------------------------------------------------
# Production-Sentry guard
# ---------------------------------------------------------------------------
# bridge/telegram_bridge.py calls sentry_sdk.init() at module import time,
# gated only on SENTRY_DSN — which its dotenv load pulls from the real .env.
# 31 test files import that module (directly or transitively), so every
# pytest process was shipping deliberate failure-path logger.error() calls
# into production Sentry as real events (issue #1460: 1,650+ events/period
# across VALOR-2M/6/1Y/2J, all traced to test payloads). Pre-seed an empty
# DSN at conftest import time — before any test module import — so the
# bridge's `if _sentry_dsn:` guard stays falsy. load_dotenv(override=False)
# never replaces a key already present in os.environ, so the real .env
# cannot re-pollute it. Production code is untouched.
os.environ["SENTRY_DSN"] = ""


# ---------------------------------------------------------------------------
# Live-Haiku guard on the outbound context-recall check (#2694)
# ---------------------------------------------------------------------------
# Same class of problem as the Sentry guard above: production code on a hot
# path reaches a real external service, and the suite pays for it.
#
# `agent/output_handler.py::TelegramRelayOutputHandler.send` calls
# `bridge.context_recall.check_outbound_context_recall(delivery_text)` on its
# MAIN outbound path. That function's prefilter passes for any non-empty text
# under 200 characters containing "?", and on a pass it issues a real
# claude-haiku-4-5 request via `agent.llm.run_typed`. Every `send()` test whose
# drafted text happens to be a short question therefore makes a live, billed
# API call. Measured before this guard: one run of
# tests/unit/output_handler/ issued 5 requests to /v1/messages
# (reproducible even under `env -u ANTHROPIC_API_KEY`, because importing
# bridge.telegram_bridge calls load_dotenv() and repopulates the key from the
# real .env).
#
# The multi-second network await is also a yield point inside `send()`, between
# the drafter call and the Redis self-draft budget bump. That reordered the
# interleaving in `test_output_handler_drafter.py::TestDrafterFailureRecovery::
# test_self_draft_attempts_bound_terminates_loop`, which gathers two concurrent
# `send()` calls and asserts exactly two atomic bumps -- taking it from
# deterministic-green to 6 failures in 12 runs on the review machine. That flake
# is latency-dependent, so it does not reproduce on every host; the billed
# request count above does, and removing the call removes both.
#
# So: stub the check inert by default. `send()` still runs its real
# advisory/bounce branch logic against a genuine ContextRecallVerdict; only the
# paid call is removed. A test that wants a live-shaped verdict monkeypatches
# the same attribute itself in its body, which runs AFTER this fixture and
# therefore wins (tests/unit/test_context_recall_wiring.py does exactly that).
# tests/unit/test_context_recall.py binds the function by name at module import
# and drives it through its own `agent.llm.run_typed` fake, so it is unaffected
# by this rebinding -- which is correct: that file owns the function's units.
#
# Production code is untouched; the feature still defaults to ENABLED in
# production. Escape hatch: set CONTEXT_RECALL_OUTBOUND_STUB=0 to run the real
# check, so the #2694 negative control stays reproducible.
_CONTEXT_RECALL_OUTBOUND_STUB_DISABLED = os.environ.get("CONTEXT_RECALL_OUTBOUND_STUB", "1") == "0"


@pytest.fixture(autouse=True)
def stub_outbound_context_recall():
    """Make the outbound context-recall check inert (no live Haiku call) by default.

    Rebinds the module attribute rather than a call site, so it covers every
    existing and future `send()` test, plus any other caller. See the block
    comment above for the measured symptoms this prevents (#2694).
    """
    if _CONTEXT_RECALL_OUTBOUND_STUB_DISABLED:
        yield
        return

    try:
        import bridge.context_recall as _ctx
    except Exception:  # pragma: no cover - module unimportable in some envs
        yield
        return

    async def _inert_check(_text=None):
        return _ctx.ContextRecallVerdict(advised=False, reason="stubbed by tests/conftest.py")

    original = _ctx.check_outbound_context_recall
    _ctx.check_outbound_context_recall = _inert_check
    try:
        yield
    finally:
        _ctx.check_outbound_context_recall = original


# ---------------------------------------------------------------------------
# Redis flush OWNERSHIP guard (db=0 is the case that motivated it)
# ---------------------------------------------------------------------------
# A flushdb() wipes an entire logical database. Against db=0 that is the
# production dataset (memories, Telegram history, chats, knowledge docs) -- on
# 2026-06-03 exactly that footgun flushed production, which is why this guard
# exists at all. Against db>=1 it is some pytest process's whole test dataset,
# and up to #2628 that was permitted unconditionally: two call sites still
# computed their db the pre-#2606 way (``gw{N} -> db{N+1}``) instead of asking
# the flock claim, so they wiped whichever unrelated live run happened to hold
# that slot. The victim differed every run, which is what made the unit suite's
# failure set rotate between identical runs.
#
# So the rule is OWNERSHIP, of which db=0 is one case: flushdb() is permitted
# only against a database THIS process has claimed (tests/db_claim.py), and db 0
# is simply a database no test process can ever claim. One wrapper, not two
# idioms -- two independently-maintained flush guards is how one of them drifts.
# The db=0 branch is kept for its message and its 2026-06-03 rationale.
#
# Fail-closed on an empty claimed set is deliberate and safe: tests/conftest.py's
# pytest_configure claims this process's db before collection, so the only
# processes that can observe an empty set are the xdist controller (which runs no
# tests) and the window before that hook. No test can ever see it.
#
# We monkeypatch flushdb/flushall on the sync and async Redis classes at conftest
# import time (before collection), so the guard covers clients built by installed
# plugins too, not just this repo's own. The claimed set is read on EVERY call --
# never captured at install time, when it is necessarily still empty. This patch
# lives in conftest.py, so it only affects pytest runs; production is untouched.
def _install_redis_flush_ownership_guard() -> None:
    try:
        import redis
        import redis.asyncio as aioredis
    except Exception:
        return

    def _db_of(client) -> int:
        try:
            return int(client.connection_pool.connection_kwargs.get("db", 0) or 0)
        except Exception:
            # If we cannot determine the db, assume the dangerous one (db=0).
            return 0

    def _make_guarded_flushdb(orig):
        def _guarded_flushdb(self, *args, **kwargs):
            db = _db_of(self)
            if db == 0:
                raise RuntimeError(
                    "Refusing flushdb() on Redis db=0 (production) during tests. "
                    "Use the autouse redis_test_db fixture, or build clients on this "
                    "process's claimed test db (tests.db_claim.claim_test_db). "
                    "This guard exists because a db=0 flush wiped production on 2026-06-03."
                )
            claimed = db_claim.claimed_test_dbs()
            if db not in claimed:
                raise RuntimeError(
                    f"Refusing flushdb() on Redis db={db}: this process has not claimed "
                    f"it (claimed={sorted(claimed)}). That database belongs to another "
                    "live pytest process and flushing it wipes its data mid-test (#2628). "
                    "Use tests.db_claim.claim_test_db() for your own db, or request the "
                    "scratch_test_db fixture if you genuinely need a second one."
                )
            return orig(self, *args, **kwargs)

        _guarded_flushdb._flush_guarded = True
        return _guarded_flushdb

    def _make_guarded_flushall(orig):
        def _guarded_flushall(self, *args, **kwargs):
            raise RuntimeError(
                "Refusing flushall() during tests -- it wipes ALL Redis dbs, including "
                "production db=0. Flush this process's claimed test db with flushdb() "
                "instead. See tests/conftest.py."
            )

        _guarded_flushall._flush_guarded = True
        return _guarded_flushall

    for mod in (redis, aioredis):
        cls = mod.Redis
        if not getattr(cls.flushdb, "_flush_guarded", False):
            cls.flushdb = _make_guarded_flushdb(cls.flushdb)
        if not getattr(cls.flushall, "_flush_guarded", False):
            cls.flushall = _make_guarded_flushall(cls.flushall)


_install_redis_flush_ownership_guard()


# ---------------------------------------------------------------------------
# Session-start test-DB claim (#2628)
# ---------------------------------------------------------------------------
# The claim used to happen lazily, in the function-scoped autouse redis_test_db
# fixture. That leaves a real "this process owns nothing yet" state that a test
# can observe -- and popoto's bundled pytest plugin observes it, because its
# autouse _popoto_flush_db sets up BEFORE redis_test_db and calls flushdb() on
# every test. A fail-closed ownership guard meeting that state would deny each
# worker's first flush; excusing it ("permit a flush when we do not know who owns
# the target") is the pre-#2606 status quo restated.
#
# So the state is designed out rather than excused: pytest_configure runs before
# collection and therefore before EVERY fixture, autouse or installed-plugin.
# "Claimed" is the only state a test can observe, and the same hook exports
# POPOTO_TEST_DB so popoto's plugin is pointed at this process's own db from its
# very first flush -- no commit window in which the guard is live and the plugin
# still targets its db-15 default (db 15 is the top slot of the claim pool, so
# every flush of it was a cross-process wipe).


# --- Process-wide REDIS_URL export (#2805) ---------------------------------
def _export_claimed_redis_url() -> None:
    """Publish THIS process's claimed db as the process-wide ``REDIS_URL``.

    A module-level seam rather than an inline assignment so that tests which
    call ``pytest_configure()`` directly against a synthetic claim registry can
    stub it out (via ``monkeypatch.setattr``) and never write a foreign db into
    the live session env -- see ``_reset_claim_state`` in
    ``test_conftest_isolation_guards.py`` (#2805 Risk 1).

    Why this line exists: every consumer that resolves ``os.environ["REDIS_URL"]``
    lazily -- inside a function body, at call time -- now sees this process's
    claimed test db instead of falling through to the hardcoded production
    default (``redis://localhost:6379/0``). That includes a plain
    ``subprocess.run(...)`` with no ``env=`` (the child inherits ``os.environ``
    by construction) and every in-process production module that reads
    ``REDIS_URL`` per call rather than at import time.

    Honest limitation: this is NOT defense-in-depth for THIS process's own
    popoto client. popoto's ``pytest11`` plugin resolves ``REDIS_URL`` and
    builds ``POPOTO_REDIS_DB`` before ``tests/conftest.py`` is even imported,
    so the parent process's in-process popoto client is unaffected by this line
    -- it is already pointed at the claimed db via the existing
    ``POPOTO_TEST_DB`` export and the autouse ``redis_test_db`` fixture swap.

    Hostname is deliberately ``127.0.0.1``, not ``localhost``: this matches
    what ``tests.db_claim.subprocess_env`` has always passed to children, so
    the two paths agree rather than silently diverging on host spelling.
    """
    os.environ["REDIS_URL"] = db_claim.redis_test_url()


def pytest_configure(config):
    """Claim this process's test db, and point popoto's plugin at it (#2628)."""
    is_worker = getattr(config, "workerinput", None) is not None
    if not is_worker and getattr(config.option, "numprocesses", None):
        # The xdist controller runs no tests, so a claim here would hold one of
        # only 15 machine-global slots for the whole session and never use it.
        # The `numprocesses` test is load-bearing: under -n0 the master DOES run
        # the tests and must claim.
        return
    try:
        db = claim_test_db()
    except RuntimeError as exc:
        # One line of output instead of a setup error on every collected test.
        pytest.exit(str(exc), returncode=3)
        return
    os.environ["POPOTO_TEST_DB"] = str(db)
    _export_claimed_redis_url()
    if is_worker:
        # xdist does not surface a worker's pytest_report_header to the terminal,
        # so provenance travels back through workeroutput instead (see
        # pytest_report_header / pytest_terminal_summary below).
        config.workeroutput["test_db"] = db


# ---------------------------------------------------------------------------
# Centralized claude_agent_sdk mock
# ---------------------------------------------------------------------------
# Several test files need ``import agent.*`` which transitively imports
# ``claude_agent_sdk``.  When the real SDK is not installed the import
# would fail during pytest collection.  Previously each test file had its
# own module-level ``sys.modules["claude_agent_sdk"] = MagicMock()`` which
# persisted across the pytest session and contaminated later tests.
#
# Centralizing the mock here (conftest.py is always imported before test
# modules are collected) means:
# 1. Only one place manages the mock -- no 7 scattered copies
# 2. The autouse fixture below restores sys.modules after each test
# 3. Tests that need the real SDK (e.g. test_cross_wire_fixes.py) get
#    a clean sys.modules state
# ---------------------------------------------------------------------------
# Check if the real SDK is importable (installed), not just loaded.
# If it's installed, don't inject a mock -- let tests use the real SDK.
# If it's NOT installed, inject a MagicMock so that ``import agent.*``
# succeeds during test collection.
try:
    import claude_agent_sdk  # noqa: F401

    _SDK_IMPORTABLE = True
except ImportError:
    _SDK_IMPORTABLE = False

_SDK_PRESENT_AT_STARTUP = "claude_agent_sdk" in sys.modules
_SDK_ORIGINAL_VALUE = sys.modules.get("claude_agent_sdk")

if not _SDK_IMPORTABLE:
    sys.modules["claude_agent_sdk"] = MagicMock()


@pytest.fixture(autouse=True)
def mock_claude_sdk_cleanup():
    """Restore sys.modules["claude_agent_sdk"] to pre-collection state after each test.

    Problem: Seven test files previously injected a MagicMock into
    sys.modules at module level (during collection, before any fixture
    runs).  The mock persisted for the entire pytest session, contaminating
    later tests (e.g. test_cross_wire_fixes.py) that expect the real SDK.

    Solution: At conftest import time (before test files are collected) we
    snapshot whether the real SDK exists.  After each test function we
    restore that original state.  If the SDK entry was swapped during the
    test (i.e. a mock was injected where the real SDK was, or vice versa),
    we also evict cached ``agent.*`` modules so they get re-imported
    cleanly against the restored SDK.
    """
    sdk_before_test = sys.modules.get("claude_agent_sdk")

    yield

    sdk_after_test = sys.modules.get("claude_agent_sdk")

    # Restore the SDK entry to its pre-collection state
    if _SDK_PRESENT_AT_STARTUP:
        sys.modules["claude_agent_sdk"] = _SDK_ORIGINAL_VALUE
    else:
        sys.modules.pop("claude_agent_sdk", None)

    # Only evict agent.* modules if the SDK entry was swapped during the
    # test.  Blanket eviction after every test is too aggressive and
    # breaks module-level state for unrelated tests.
    if sdk_after_test is not sdk_before_test:
        agent_modules = [key for key in sys.modules if key == "agent" or key.startswith("agent.")]
        for mod_key in agent_modules:
            del sys.modules[mod_key]


# ---------------------------------------------------------------------------
# Operator kill-switch isolation (#2552)
# ---------------------------------------------------------------------------
# `bridge/catchup.py` anchors CATCHUP_DISABLED_FLAG to its own source tree
# (`Path(__file__).parent.parent / "data" / "catchup-disabled"`), which is
# correct for production — the bridge runs under launchd with an unpredictable
# cwd — but means the test suite reads live operator control state. On a host
# where an operator has paused message recovery, ~17 unit tests that have
# nothing to do with the kill switch go red, and the same nodes pass from a
# worktree. That is a false red with no explanation anywhere in the diff.
#
# All three production readers (`bridge/catchup.py`, `bridge/reconciler.py`,
# `bridge/agent_catchup.py`) import the symbol FROM `bridge.catchup` rather than
# re-deriving the path, so patching this one module attribute covers every
# reader. The fixture imports `bridge.catchup` itself rather than relying on the
# test having imported it.
#
# The flag is REPOINTED at a per-test temp path, not stubbed: `catchup_disabled()`
# still runs its real `Path.exists()` against a real filesystem path. A test that
# wants genuine disabled behavior can `touch` the redirected path.
_CATCHUP_FLAG_ISOLATION_DISABLED = os.environ.get("CATCHUP_FLAG_ISOLATION", "1") == "0"


@pytest.fixture(scope="session")
def _catchup_flag_redirect_dir(tmp_path_factory):
    """One directory for the whole session to hold per-test kill-switch paths.

    Session-scoped on purpose. ``tmp_path_factory.mktemp`` is numbered, so it
    scans the basetemp for the next free suffix and its cost grows with the
    number of directories already there. Calling it once per test from an
    autouse fixture makes that aggregate quadratic and leaves one empty
    directory per test; the per-test fixture below instead varies only the
    filename inside this single directory, which costs a string format and no
    filesystem write at all.
    """
    return tmp_path_factory.mktemp("catchup-flag")


@pytest.fixture(autouse=True)
def isolate_catchup_kill_switch(_catchup_flag_redirect_dir):
    """Repoint the catchup operator kill-switch flag at a per-test temp path.

    Each test gets a unique path no other test can touch, so a test that wants
    genuine disabled behavior can create it without leaking into its neighbors.

    Escape hatch: set ``CATCHUP_FLAG_ISOLATION=0`` to skip the redirect. That
    exists so the negative control for #2552 stays permanently reproducible —
    running the suite with it set must reproduce the flag-caused failures on a
    host where the real ``data/catchup-disabled`` exists.
    """
    if _CATCHUP_FLAG_ISOLATION_DISABLED:
        yield
        return

    import bridge.catchup

    original = bridge.catchup.CATCHUP_DISABLED_FLAG
    bridge.catchup.CATCHUP_DISABLED_FLAG = (
        _catchup_flag_redirect_dir / f"catchup-disabled-{uuid4().hex}"
    )
    try:
        yield
    finally:
        bridge.catchup.CATCHUP_DISABLED_FLAG = original


@pytest.fixture(autouse=True)
def agent_hooks_consistency_guard():
    """Detect and repair a corrupt `agent` package/submodule cache state.

    Problem: ``monkeypatch.setattr("agent.hooks.pre_tool_use.SOME_ATTR", ...)``
    (a dotted-string target) resolves via attribute-walk: import ``agent``,
    then ``getattr(agent, "hooks")``, then ``getattr(hooks, "pre_tool_use")``,
    etc. CPython only rebinds a submodule as an attribute on its parent
    package the moment that submodule is freshly imported -- ``sys.modules``
    is just a flat name->module cache and does not, by itself, keep the
    attribute tree in sync.

    If some other test (or fixture, e.g. ``mock_claude_sdk_cleanup`` above,
    which selectively evicts ``agent.*`` keys) replaces or partially rebuilds
    ``sys.modules["agent"]`` while ``sys.modules["agent.hooks"]`` survives
    from an earlier import, the new ``agent`` module object never gets
    ``hooks`` re-bound onto it. The cache then reports both modules as
    "loaded" while the parent-child link between them is severed:
    ``"agent" in sys.modules and "agent.hooks" in sys.modules and not
    hasattr(sys.modules["agent"], "hooks")``. Any dotted-string
    ``monkeypatch.setattr`` that walks through ``agent.hooks`` then raises
    ``AttributeError: 'module' object at agent.hooks has no attribute
    'hooks'`` during test setup, before the test body ever runs.

    This is a distinct corruption vector from the one ``mock_claude_sdk_cleanup``
    guards against (SDK entry swaps specifically), so it needs its own
    independent, always-on check rather than being folded into that fixture.

    Fix: rebind every cached ``agent.*`` submodule as an attribute of its
    parent package, in place. The severed link is purely a missing attribute
    on the parent module object, so restoring the attribute restores the
    exact invariant the attribute-walk needs.

    This repair deliberately preserves module identity, which evicting the
    ``agent.*`` keys does not (issue #2551). A test module that binds a
    submodule at import time -- ``from agent import reap_killlist`` -- keeps
    calling the object it bound, while ``patch("agent.reap_killlist._redis")``
    resolves the dotted string through ``sys.modules`` at fixture-setup time.
    After an eviction those are two different module objects: the patch lands
    on a freshly imported module nobody calls, the seam in the module under
    test stays live, and the test fails for reasons that have nothing to do
    with the code under test. Rebinding keeps one object per name, so a
    module-level ``from agent import X`` binding stays the object the patch
    targets.

    No-op (untouched) when ``agent`` isn't imported at all, or when it *is*
    imported and its ``hooks`` attribute is intact -- only the corrupt state
    triggers a repair.
    """
    if (
        "agent" in sys.modules
        and "agent.hooks" in sys.modules
        and not hasattr(sys.modules["agent"], "hooks")
    ):
        # Shortest names first, so a parent is repaired before its children.
        for name in sorted(
            key for key in sys.modules if key == "agent" or key.startswith("agent.")
        ):
            parent_name, _, child_name = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            child = sys.modules.get(name)
            if parent is None or child is None:
                continue
            if getattr(parent, child_name, None) is not child:
                setattr(parent, child_name, child)

    yield


# ---------------------------------------------------------------------------
# Shared module-object identity guard (#2603, widened by #2628)
# ---------------------------------------------------------------------------
# ``importlib.reload(models.session_lifecycle)`` keeps the module OBJECT but
# rebinds every class defined in it to a brand-new class object. Every other
# module that did ``from models.session_lifecycle import StatusConflictError``
# at import time — including every test module — keeps the OLD class. From then
# on ``except StatusConflictError`` and ``pytest.raises(StatusConflictError)``
# compare an exception against a class it is no longer an instance of, and the
# handler silently misses. The failures land in whichever file imported the
# class first, never in the file that reloaded, and randomized ordering decides
# whether they happen at all (#2603: four tests in
# tests/unit/test_teammate_cold_start_finalize.py, green alone, red in a full
# randomized run).
#
# This is the same doctrine as the ``agent.*`` repair above (#2551): restore the
# invariant in place, preserving one object per name, rather than pinning test
# order or evicting. Rebinding the original class onto the module makes every
# later ``from models.session_lifecycle import StatusConflictError`` — including
# the function-local ones inside ``agent/agent_session_queue.py`` and
# ``agent/session_executor.py`` — resolve to the object the rest of the process
# already holds.
#
# The repair is announced as a RuntimeWarning attributed to the test that caused
# it, so a reload never hides: under ``-W error::RuntimeWarning`` it is a
# teardown failure on the offending test, exactly like the coroutine leak guard.
# Exception classes are not the only objects a reload orphans, and #2628 found
# the second shape by the same rotate-run-to-run signature. A module-level
# REGISTRY -- ``agent.index_drift.DRIFT_COVERED_MODELS`` -- is a plain dict that
# other modules and test files bind by name at import time. Reloading rebinds it
# to a brand-new dict and re-runs the registrations into that one, while
# tests/unit/test_index_drift_coverage.py's ``restore_registry`` fixture holds
# the orphaned old dict: the fixture then snapshots and restores the STALE dict
# while ``register_drift_model`` writes into the LIVE one, so the cleanup
# silently no-ops and a fake in-memory model stays registered for the rest of
# that worker's life. `--dist=loadfile` decides whether the reloader and the
# victim land on the same worker, so the damage appears only some runs.
#
# So the guard covers two kinds of shared object, per module:
#   "exceptions" -- BaseException subclasses (the #2603 case)
#   "registry"   -- module-level containers (dict/set/list) and the classes
#                   defined in the module, which registry entries are instances
#                   of and which must stay identity-consistent with them
_SHARED_IDENTITY_MODULES: dict[str, str] = {
    "models.session_lifecycle": "exceptions",
    "agent.index_drift": "registry",
    "monitoring.bridge_watchdog": "registry",
    "monitoring.worker_watchdog": "registry",
}

# name -> {attr: original object}, captured the first time each module is seen.
_SHARED_IDENTITY_SNAPSHOT: dict[str, dict[str, object]] = {}


def _is_shared_identity_object(kind: str, mod_name: str, obj) -> bool:
    """True when ``obj`` is one another module can hold by name across a reload."""
    if kind == "exceptions":
        return isinstance(obj, type) and issubclass(obj, BaseException)
    if isinstance(obj, type):
        # Only classes DEFINED here: an imported one is the defining module's to
        # restore, and rebinding it from this module would fight that owner.
        return getattr(obj, "__module__", None) == mod_name
    return isinstance(obj, (dict, set, list))


def _snapshot_shared_identity() -> None:
    """Record the identity of module-level objects other modules bind by name."""
    for mod_name, kind in _SHARED_IDENTITY_MODULES.items():
        mod = sys.modules.get(mod_name)
        if mod is None or mod_name in _SHARED_IDENTITY_SNAPSHOT:
            continue
        _SHARED_IDENTITY_SNAPSHOT[mod_name] = {
            attr: obj
            for attr, obj in vars(mod).items()
            if not attr.startswith("__") and _is_shared_identity_object(kind, mod_name, obj)
        }


def _repair_shared_identity() -> dict[str, list[str]]:
    """Rebind reloaded module objects to the identity the process shares.

    Returns ``{module_name: [repaired_attr, ...]}`` for every module that needed
    repair; empty when nothing was reloaded.
    """
    repairs: dict[str, list[str]] = {}
    for mod_name, originals in _SHARED_IDENTITY_SNAPSHOT.items():
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        repaired = sorted(
            attr for attr, original in originals.items() if getattr(mod, attr, None) is not original
        )
        if not repaired:
            continue
        for attr in repaired:
            setattr(mod, attr, originals[attr])
        repairs[mod_name] = repaired
    return repairs


@pytest.fixture(autouse=True)
def shared_module_identity_guard(request):
    """Restore reloaded module objects to the identity the process shares.

    No-op (a handful of ``is`` comparisons) unless a test actually reloaded one
    of the modules in ``_SHARED_IDENTITY_MODULES``.
    """
    _snapshot_shared_identity()
    yield
    _snapshot_shared_identity()
    for mod_name, repaired in _repair_shared_identity().items():
        warnings.warn(
            f"{request.node.nodeid} reloaded {mod_name}, rebinding "
            f"{', '.join(repaired)} to new objects that no other module holds; "
            "identity restored. Reload a shared module in a subprocess, not in-process "
            "(see tests/conftest.py, issues #2603 and #2628).",
            RuntimeWarning,
            stacklevel=2,
        )


# ---------------------------------------------------------------------------
# Per-process test-DB claim (issue #2060)
# ---------------------------------------------------------------------------
# The claim itself lives in ``tests/db_claim.py`` so that helper code and test
# modules can import it as an ordinary module. Keeping the memoized claim state
# out of conftest is what stops a second copy of it existing (issue #2605): a
# process holding two claims would point its own subprocesses at a db it is not
# using, which is precisely how ``TestKillCommandIntegration`` lost rows it had
# just pushed. See that module's docstring for the full rationale.
#
# A subprocess inherits the claimed db through ``tests.db_claim.subprocess_env``,
# which is the single sanctioned route. Reading it back out of
# ``POPOTO_REDIS_DB.connection_pool.connection_kwargs['db']`` is a second
# derivation of the same fact and is not used anywhere: ``claim_test_db()`` is
# the one authority for the number (#2628).


@pytest.fixture(scope="session", autouse=True)
def _test_db_claim_release():
    """Release the process's claimed test db at session end (atexit backstops)."""
    yield
    release_test_db_claim()


@pytest.fixture(scope="session", autouse=True)
def _popoto_client_ownership_check(_popoto_test_db):
    """Assert every live popoto client points at a db this process owns (#2628).

    A ``tests/``-scoped review cannot see this class of writer at all: popoto
    ships its own ``pytest11`` plugin, which this repo loads on every run, and
    before #2628 that plugin sat on its db-15 default -- the top slot of the
    claim pool -- flushing it before every test in every pytest process on the
    machine. The fix is an env export from ``pytest_configure``, so this check is
    the thing that notices if a future popoto (or any other plugin that swaps the
    popoto globals) stops honouring it: it asserts on whatever clients
    ``popoto.redis_db`` holds, whatever version put them there.

    Declaring ``_popoto_test_db`` as a dependency orders this after the plugin's
    own db swap by construction, rather than by fixture-collection accident.

    ``_POPOTO_ASYNC_REDIS_DB`` is ``None`` at every moment a session-scoped
    fixture can observe it, and ``None`` is its CORRECT state -- the plugin nulls
    it at both setup and teardown of every test so no client binds to a stale
    event loop. Skip it; never assert it is non-``None``, because an
    ``AttributeError`` raised here errors every test in the process in setup.
    """
    import popoto.redis_db as rdb

    claimed = claimed_test_dbs()
    for client in (rdb.POPOTO_REDIS_DB, getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)):
        if client is None:
            continue
        db = client.connection_pool.connection_kwargs.get("db", 0)
        assert db in claimed, (
            f"a popoto client is pointed at db {db} which this process has not "
            f"claimed (claimed={sorted(claimed)}); flushing it would wipe another "
            "live pytest process's data (#2628)"
        )
    yield


@pytest.fixture(scope="session")
def scratch_test_db():
    """A SECOND owned test db, for a test whose subject is db divergence.

    Session-scoped and memoized on purpose: ``claim_scratch_test_db()`` takes an
    additional pool slot with no per-test release, so a function-scoped fixture
    would consume one slot per requesting test and walk the 15-slot pool into the
    claim's exhaustion error -- manufacturing the very contention this change
    makes fatal. One scratch slot per process, released with the primary claim.

    Skips (never falls back to a borrowed number) when the pool is exhausted: an
    unowned scratch db is the exact defect this fixture exists to remove.
    """
    db = claim_scratch_test_db()
    if db is None:
        pytest.skip(
            "no free Redis db slot for a scratch test db; too many concurrent "
            "pytest runs (try `scripts/reap-xdist.sh --apply`)"
        )
    return db


# ---------------------------------------------------------------------------
# Run provenance: which process owned which db (#2628)
# ---------------------------------------------------------------------------
# When a suite result looks wrong, the first question is "did another process
# own my db". One line per process answers it from the log instead of a fresh
# recon. Neither hook may CALL claim_test_db(): both also run in the xdist
# controller, which owns no db and must never burn a pool slot -- that rule is a
# property of the process, not of any one hook.
_WORKER_DB_PROVENANCE: list[dict] = []


def _format_test_db_provenance(collected) -> str:
    """Render collected per-worker claims as one terminal line."""
    if not collected:
        return ""
    parts = " ".join(f"{item.get('worker', '?')}=db{item.get('test_db')}" for item in collected)
    return f"test-db claims (#2628): {parts}"


def pytest_report_header(config):
    """Name this process's claimed db when it is the one running the tests."""
    if getattr(config, "workerinput", None) is not None:
        return None  # xdist discards worker headers; workeroutput carries it
    if getattr(config.option, "numprocesses", None):
        return None  # controller owns no db; workers report via the summary
    return f"test-db claim: worker=master db={db_claim._CLAIMED_TEST_DB}"


def pytest_testnodedown(node, error):
    """Collect each xdist worker's claimed db as it shuts down."""
    output = getattr(node, "workeroutput", None) or {}
    if "test_db" in output:
        _WORKER_DB_PROVENANCE.append(
            {"worker": getattr(node.gateway, "id", "?"), "test_db": output["test_db"]}
        )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Surface the workers' claimed dbs, which xdist otherwise never prints."""
    line = _format_test_db_provenance(_WORKER_DB_PROVENANCE)
    if line:
        terminalreporter.write_line(line)


@pytest.fixture(autouse=True)
def no_calendar_subprocess_in_tests():
    """Stop `_execute_agent_session` spawning a real `valor-calendar` subprocess.

    This is the mechanism behind the #2574 tail wedge, and it is a live
    unit-test hazard on its own.

    `agent/session_executor.py` fires ``_schedule_calendar_heartbeat(...)`` at
    session start (owned task with a shutdown drain since #2590, but still
    fire-and-forget from the test's perspective), and `_calendar_heartbeat` runs
    ``asyncio.create_subprocess_exec`` against the real `valor-calendar` CLI --
    which talks to a real Google Calendar. Ten unit test files call
    `_execute_agent_session`, so ten files could spawn it.

    The wedge follows from the leak. The test finishes while that task is still
    inside ``BaseSubprocessTransport._connect_pipes``. pytest-asyncio then tears
    the loop down, ``asyncio.runners._cancel_all_tasks`` cancels the task and
    waits on ``gather(...)``, and a task blocked connecting subprocess pipes
    never answers the cancellation -- so ``run_until_complete`` spins in
    ``selector.select()`` forever. Under xdist the per-test timeout eventually
    fires and pytest-timeout calls ``os._exit(1)``, which is not a graceful
    worker shutdown: the controller reports "node down: Not properly
    terminated", respawns, and the replacement dies on the same test. Observed
    four workers deep before the controller wedged at 0% CPU with no summary.

    Patched as a no-op coroutine rather than a MagicMock so the unawaited
    ``create_task`` still receives an awaitable and no "coroutine was never
    awaited" warning appears. Nothing asserts on the executor's heartbeat;
    `tools/valor_calendar.py` keeps its own direct tests.
    """
    try:
        import agent.session_executor as _executor
    except Exception:  # pragma: no cover - executor unimportable in some envs
        yield
        return

    async def _noop_calendar_heartbeat(*_args, **_kwargs):
        return None

    original = _executor._calendar_heartbeat
    _executor._calendar_heartbeat = _noop_calendar_heartbeat
    try:
        yield
    finally:
        _executor._calendar_heartbeat = original


def _assert_client_matches_claim_registry(client, expected_host: str, expected_port: int) -> None:
    """Fail loudly if the client is not on the server the claim registry keys.

    The claim registry that guarantees db uniqueness is machine-global and keyed
    by port (``/tmp/valor-pytest-db-claims-{port}``). If a client ever ends up on
    a DIFFERENT server than the registry names, the flock that is supposed to
    make this process's db exclusive is protecting a database on a server nobody
    is writing to, and the writes land somewhere unprotected — which is #2799
    exactly, and where the "private redis" runs were flushing production db N.

    Host/port now flow from one resolver, so divergence is prevented by
    construction; this assertion exists so that if a future edit reintroduces a
    bare client, the suite says so on the first test instead of silently
    operating on the wrong server.
    """
    kwargs = client.connection_pool.connection_kwargs
    actual_host, actual_port = kwargs.get("host"), kwargs.get("port")
    if (actual_host, actual_port) != (expected_host, expected_port):
        raise RuntimeError(
            "Test Redis client is not on the server the db-claim registry is keyed to: "
            f"client={actual_host}:{actual_port} registry={expected_host}:{expected_port}. "
            "The flock guaranteeing this process's db is exclusive protects the registry's "
            "server, so writes to any other server are unprotected (#2799). Resolve the "
            "client through tests.db_claim.redis_test_host()/redis_test_port()."
        )


@pytest.fixture(scope="session", autouse=True)
def _popoto_pool_install(_popoto_test_db):
    """Point popoto's canonical client at the claim registry's server, once (#2771).

    Ownership is split by fact, and this fixture owns exactly one of them:

    * ``tests/db_claim.py`` owns the db NUMBER and the SERVER (host/port).
    * popoto's bundled ``pytest11`` plugin owns APPLYING the db number — it reads
      the ``POPOTO_TEST_DB`` that ``pytest_configure`` exports and calls its own
      ``_swap_db``. Declaring ``_popoto_test_db`` as a dependency orders this
      fixture after that swap by construction rather than by collection accident.
    * this fixture owns APPLYING the server. It cannot be delegated to
      ``_swap_db``: that helper preserves whatever host/port the pool already
      carries, and that pool was built at popoto import time from the ambient
      ``REDIS_URL``. Under ``REDIS_PORT=641x`` the claim registry is keyed to the
      private port while the client sits on 6379 — the run opts OUT of the
      machine-global pool and flushes production db N (#2799).

    The swap is IN PLACE on the existing client object: only
    ``client.connection_pool`` is reassigned, never ``rdb.POPOTO_REDIS_DB``.
    Popoto submodules capture the symbol with ``from ..redis_db import
    POPOTO_REDIS_DB`` at import time, so replacing the object strands every one
    of those bindings on a stale client — the #2037 split-brain. Mutating the
    object every binding already points at makes them all follow for free, and
    is why no submodule re-pointing walk exists any more.

    The pool is rebuilt rather than adjusted because ``max_connections`` is a
    POOL constructor argument and is absent from ``connection_kwargs``: anything
    that rebuilds from those kwargs — including popoto's own ``_swap_db``, which
    runs before this fixture — silently downgrades a ``BlockingConnectionPool``
    to a plain unbounded ``ConnectionPool``. Reconstructing popoto's own pool
    type restores the connection ceiling the suite is supposed to run under.

    SELECT is still not used: with a connection pool a recycled connection
    defaults back to db 0, and the next ``flushdb()`` wipes production.
    """
    import popoto.redis_db as rdb
    import redis

    test_db = claim_test_db()
    test_host = db_claim.redis_test_host()
    test_port = int(db_claim.redis_test_port())

    client = rdb.POPOTO_REDIS_DB
    old_pool = client.connection_pool
    old_kwargs = dict(old_pool.connection_kwargs)

    # Idempotence guard. Host/port are type-normalized on both sides: a pool
    # built from a URL carries ``port`` as int while ``redis_test_port()``
    # returns a str, and comparing those raw would rebuild the pool on every
    # call instead of recognising the steady state.
    already_installed = (
        isinstance(old_pool, redis.BlockingConnectionPool)
        and str(old_kwargs.get("host")) == str(test_host)
        and int(old_kwargs.get("port") or 0) == test_port
        and int(old_kwargs.get("db") or 0) == test_db
    )
    if already_installed:
        yield
        return

    new_pool_kwargs = {
        "host": test_host,
        "port": test_port,
        "db": test_db,
        "socket_timeout": 5,
        "socket_connect_timeout": 5,
        # Read from popoto rather than hardcoded: it honours
        # POPOTO_SYNC_MAX_CONNECTIONS. #2770 is the upstream coordination point
        # if this name ever moves.
        "max_connections": getattr(rdb, "_SYNC_MAX_CONNECTIONS", 128),
    }
    # An authenticated REDIS_URL must keep working; mirror what _swap_db carries.
    for auth_key in ("password", "username"):
        if old_kwargs.get(auth_key) is not None:
            new_pool_kwargs[auth_key] = old_kwargs[auth_key]

    new_pool = redis.BlockingConnectionPool(**new_pool_kwargs)
    client.connection_pool = new_pool
    # Disconnecting immediately is safe ONLY because this runs in pytest's
    # synchronous session-setup phase, with no test body executing and therefore
    # no checked-out connection in flight. disconnect() tears down every
    # connection the pool hosts, so a caller mid-command would get a broken
    # socket. Never move this into a test body or an async context.
    old_pool.disconnect()

    yield

    # Session finalizer, and the only place the pool this fixture installed is
    # unwound. Runs after the last test's teardown, again with nothing in
    # flight. Not client.close(): the client does not own its pool (it was built
    # with redis.Redis(connection_pool=...)), and the object is shared.
    client.connection_pool = old_pool  # session-finalizer-only
    new_pool.disconnect()


@pytest.fixture(autouse=True)
def redis_test_db(_popoto_pool_install):
    """Per-test Redis hygiene on popoto's canonical client (#2771).

    autouse=True ensures this runs for every test, even those that don't
    explicitly request the fixture. This prevents accidental writes to db=0
    if a test imports a popoto model without requesting isolation.

    This fixture does NO pool work. Which server and which db the client is on
    is settled once per session by ``_popoto_pool_install``; all that is left
    per test is the flush pair and the registry assertion.

    The db number is this PROCESS's flock claim (tests/db_claim.py), exported as
    ``POPOTO_TEST_DB`` in ``pytest_configure`` and applied by popoto's own
    plugin. It is never derived from the xdist worker id: that is unique within
    one run but not across the several pytest processes this machine runs at
    once, which is how one run's flushdb() used to wipe another's data mid-test
    (#2060, #2628).

    The registry assertion stays function-scoped on purpose. It is the check
    that catches drift introduced DURING a test — a test that rebinds the global
    or swaps the pool fails on the very next test rather than silently flushing
    an unprotected server (#2799).

    ``rdb._POPOTO_ASYNC_REDIS_DB`` is deliberately untouched. The plugin nulls it
    at setup and teardown of every test so that ``get_async_redis_db()`` builds
    the async client lazily INSIDE the test's own event loop, mirroring the
    canonical sync client's kwargs. Binding it here — synchronously, in fixture
    setup — produces a client attached to the wrong loop, which is the one thing
    the plugin's reset exists to prevent.
    """
    import popoto.redis_db as rdb

    client = rdb.POPOTO_REDIS_DB
    test_host = db_claim.redis_test_host()
    test_port = int(db_claim.redis_test_port())
    _assert_client_matches_claim_registry(client, test_host, test_port)

    client.flushdb()
    yield
    client.flushdb()


# ---------------------------------------------------------------------------
# Shared helper: per-process Redis URL for tests that need raw Redis clients
# ---------------------------------------------------------------------------
# Tests that point a non-popoto Redis client (or set REDIS_URL for code under
# test) must use the SAME db that `redis_test_db` picks, otherwise concurrent
# pytest processes collide. Hardcoding `db=1` — or re-deriving it from
# `PYTEST_XDIST_WORKER` — breaks as soon as this process's claim lands anywhere
# else in the pool (#2605). Subprocesses want `tests.db_claim.subprocess_env`,
# which also pins the checkout under test.
# ---------------------------------------------------------------------------


def _redis_test_db_num(request=None):
    """Return the per-process claimed test db number (matches redis_test_db, #2060)."""
    return claim_test_db()


@pytest.fixture
def redis_test_url(request):
    """Return the claimed-db ``redis://localhost:6379/N`` URL for tests.

    Use this in any fixture that constructs a raw ``redis.Redis`` client or
    sets ``REDIS_URL`` for code under test. Matches the db number chosen by
    the autouse ``redis_test_db`` fixture so concurrent runs are safe.
    """
    return f"redis://localhost:6379/{_redis_test_db_num()}"


# ---------------------------------------------------------------------------
# Test helper: create AgentSession with backward-compatible field names
# ---------------------------------------------------------------------------


def create_test_session(**kwargs):
    """Create an AgentSession with backward-compatible field names.

    Accepts the old individual field names (message_text, sender_name, sender_id,
    telegram_message_id, chat_title, revival_context, classification_type,
    classification_confidence, work_item_slug) and maps them into the new
    consolidated DictFields.
    """
    from datetime import UTC, datetime

    from models.agent_session import AgentSession

    # Extract property-based fields that map to initial_telegram_message
    msg_text = kwargs.pop("message_text", None)
    sender_name = kwargs.pop("sender_name", None)
    sender_id = kwargs.pop("sender_id", None)
    telegram_message_id = kwargs.pop("telegram_message_id", None)
    chat_title = kwargs.pop("chat_title", None)

    # Extract property-based fields that map to extra_context
    revival_context = kwargs.pop("revival_context", None)
    classification_type = kwargs.pop("classification_type", None)
    classification_confidence = kwargs.pop("classification_confidence", None)

    # Extract property-based fields that map to slug
    work_item_slug = kwargs.pop("work_item_slug", None)

    # Build initial_telegram_message if any telegram fields provided
    if "initial_telegram_message" not in kwargs:
        itm = {}
        if msg_text is not None:
            itm["message_text"] = msg_text
        if sender_name is not None:
            itm["sender_name"] = sender_name
        if sender_id is not None:
            itm["sender_id"] = sender_id
        if telegram_message_id is not None:
            itm["telegram_message_id"] = telegram_message_id
        if chat_title is not None:
            itm["chat_title"] = chat_title
        if itm:
            kwargs["initial_telegram_message"] = itm

    # Build extra_context if any context fields provided
    if "extra_context" not in kwargs:
        ec = {}
        if revival_context is not None:
            ec["revival_context"] = revival_context
        if classification_type is not None:
            ec["classification_type"] = classification_type
        if classification_confidence is not None:
            ec["classification_confidence"] = classification_confidence
        if ec:
            kwargs["extra_context"] = ec

    # Map work_item_slug to slug
    if work_item_slug is not None and "slug" not in kwargs:
        kwargs["slug"] = work_item_slug

    # Ensure created_at uses datetime
    if "created_at" not in kwargs:
        kwargs["created_at"] = datetime.now(tz=UTC)

    return AgentSession.create(**kwargs)


# ---------------------------------------------------------------------------
# Auto-apply feature markers based on test filename
# ---------------------------------------------------------------------------
# Centralised here so it applies to ALL test directories (unit, integration,
# e2e, tools, performance, ai_judge).  Run a specific feature's tests with:
#     pytest -m sdlc
#     pytest -m "messaging or sessions"
# ---------------------------------------------------------------------------
FEATURE_MAP = {
    "bridge": "messaging",
    "messenger": "messaging",
    "telegram": "messaging",
    "duplicate_delivery": "messaging",
    "transcript": "messaging",
    "dedup": "messaging",
    "markdown": "messaging",
    "media_handling": "messaging",
    "routing": "messaging",
    "pm_channels": "messaging",
    "unthreaded": "messaging",
    "file_extraction": "messaging",
    "message_pipeline": "messaging",
    "reply_delivery": "messaging",
    "pipeline": "sdlc",
    "sdlc": "sdlc",
    "observer": "sdlc",
    "stop_hook": "sdlc",
    "stop_reason": "sdlc",
    "post_tool_use": "sdlc",
    "pre_tool_use": "sdlc",
    "skill_outcome": "sdlc",
    "skills_audit": "sdlc",
    "steering": "sdlc",
    "cross_repo_build": "sdlc",
    "session_status": "sessions",
    "session_stuck": "sessions",
    "session_watchdog": "sessions",
    "stall_detection": "sessions",
    "pending_stall": "sessions",
    "pending_recovery": "sessions",
    "escape_hatch": "sessions",
    "lifecycle": "sessions",
    "session_continuity": "sessions",
    "goal_gates": "sessions",
    "open_question": "sessions",
    "agent_session": "sessions",
    # Execution-fence family (#2494 / #2518): the (pid, create_time) identity
    # guard and the reapers that consume it. Placed after "agent_session" so
    # ``agent_session_*`` filenames keep their existing marker.
    "fence": "sessions",
    "orphan_reap": "sessions",
    "agent_session_hierarchy": "jobs",
    "agent_session_scheduler": "jobs",
    "agent_session_queue": "jobs",
    "agent_session_health": "jobs",
    "enqueue": "jobs",
    "reflection": "reflections",
    "config": "config",
    "context_modes": "context",
    "session_tags": "context",
    "auto_continue": "classifiers",
    "intake_classifier": "classifiers",
    "work_request_classifier": "classifiers",
    "message_quality": "classifiers",
    "stage_aware_auto_continue": "classifiers",
    "validate_commit": "validation",
    "validate_verification": "validation",
    "validate_test_impact": "validation",
    "validate_sdlc": "validation",
    "verification_parser": "validation",
    "features_readme": "validation",
    "build_validation": "validation",
    "checkpoint": "validation",
    "docs_auditor": "validation",
    "branch_manager": "git",
    "worktree_manager": "git",
    "git_state": "git",
    "workspace_safety": "git",
    "symlinks": "git",
    "sdk_client": "sdk",
    "sdk_permissions": "sdk",
    "workflow_sdk": "sdk",
    "code_impact": "impact",
    "doc_impact": "impact",
    "cross_repo_gh": "impact",
    "cross_wire": "impact",
    "model_relationships": "models",
    "redis_models": "models",
    "summarizer": "summarizer",
    "telemetry": "monitoring",
    "health_check": "monitoring",
    "bridge_watchdog": "monitoring",
    "connectivity": "monitoring",
    "silent_failures": "monitoring",
    "remote_update": "config",
    "benchmarks": "monitoring",
    "classifier": "classifiers",
    "code_execution": "tools",
    "link_analysis": "tools",
    "doc_summary": "tools",
    "image_analysis": "tools",
    "search": "tools",
    "test_judge": "tools",
    "ai_judge": "tools",
    "telegram_history": "tools",
}


def pytest_collection_modifyitems(items):
    """Auto-apply feature markers based on test file name."""
    for item in items:
        filename = item.nodeid.split("::")[0].split("/")[-1].replace("test_", "").replace(".py", "")
        for pattern, marker_name in FEATURE_MAP.items():
            if pattern in filename:
                item.add_marker(getattr(pytest.mark, marker_name))
                break


@pytest.fixture
def sample_config():
    """Sample project configuration matching ~/Desktop/Valor/projects.json structure."""
    return {
        "projects": {
            "valor": {
                "name": "Valor AI",
                "description": "AI coworker system",
                "machine": "TestMachine",
                "telegram": {
                    "groups": ["Dev: Valor"],
                    "respond_to_all": False,
                    "respond_to_mentions": True,
                    "respond_to_dms": True,
                    "mention_triggers": ["@valor", "valor", "hey valor"],
                    # Registered bot peer (issue #1574): recorded to history,
                    # never spawns a session; home of the settle_profile.
                    "bots": [
                        {
                            "id": 8837490628,
                            "username": "cyndra_staff_bot",
                            "name": "Bruce @ Internal Staff",
                            "under_test": True,
                            "settle_profile": {
                                "cleanup_progress": False,
                                "quiet_window_seconds": 5,
                                "default_timeout_seconds": 600,
                                "status_patterns": [
                                    "^⏳",
                                    "^(💻|🔎|🔧|📖|⚙️|📝) \\w+:",
                                ],
                            },
                        }
                    ],
                },
                "github": {"org": "tomcounsell", "repo": "ai"},
                "context": {
                    "tech_stack": ["Python", "Claude Agent SDK", "Telethon"],
                    "description": "Focus on agentic systems",
                },
            },
            "popoto": {
                "name": "Popoto",
                "description": "Redis ORM for Python",
                "telegram": {
                    "groups": ["Dev: Popoto"],
                    "respond_to_all": False,
                    "respond_to_mentions": True,
                    "respond_to_dms": False,
                },
                "github": {"org": "tomcounsell", "repo": "popoto"},
                "context": {
                    "tech_stack": ["Python", "Redis"],
                    "description": "Focus on Redis data modeling",
                },
            },
            "django-project-template": {
                "name": "Django Project Template",
                "description": "Modern Django template",
                "telegram": {
                    "groups": ["Dev: Django Template"],
                    "respond_to_all": True,  # Responds to all messages
                    "respond_to_mentions": True,
                    "respond_to_dms": False,
                },
                "github": {"org": "tomcounsell", "repo": "django-project-template"},
                "context": {
                    "tech_stack": ["Django", "PostgreSQL", "Redis"],
                    "description": "Focus on Django best practices",
                },
            },
        },
        "defaults": {
            "telegram": {
                "respond_to_all": False,
                "respond_to_mentions": True,
                "respond_to_dms": True,
                "mention_triggers": ["@valor", "valor", "hey valor"],
            },
            "response": {
                "typing_indicator": True,
                "max_response_length": 4000,
                "timeout_seconds": 300,
            },
        },
    }


@pytest.fixture
def valor_project(sample_config):
    """Extract Valor project config with _key added."""
    project = sample_config["projects"]["valor"].copy()
    project["_key"] = "valor"
    return project


@pytest.fixture
def popoto_project(sample_config):
    """Extract Popoto project config with _key added."""
    project = sample_config["projects"]["popoto"].copy()
    project["_key"] = "popoto"
    return project


@pytest.fixture
def django_project(sample_config):
    """Extract Django project config with _key added."""
    project = sample_config["projects"]["django-project-template"].copy()
    project["_key"] = "django-project-template"
    return project


# ---------------------------------------------------------------------------
# xdist worker reaper
# ---------------------------------------------------------------------------
# pytest-xdist workers run via
#   `python -c "import sys; exec(eval(sys.stdin.readline()))"`
# which installs no signal handlers. If the parent pytest process dies
# (timeouts, agent tooling interrupting, a keyboard interrupt racing
# with teardown) the workers get reparented to init and stay alive
# consuming memory. On a 10-CPU box each leaked worker is ~15-25MB of
# RAM, and one crash loop can leave 60+ zombies.
#
# The shell-level `scripts/pytest-clean.sh` covers the happy path. The
# controller-level reaper below covers the case where the controller
# itself exits without the wrapper's trap firing (e.g. SIGKILL of the
# wrapper, or a pytest crash).
#
# IMPORTANT: this reaper runs on the CONTROLLER (xdist master), not in
# the workers. It kills workers by matching the standard xdist worker
# argv regex.
XDIST_WORKER_RE = r"exec\(eval\(sys\.stdin\.readline\(\)\)\)"


def _ppid_of(pid: int) -> int | None:
    """Parent PID via `ps` (no psutil). None on any failure."""
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = result.stdout.strip()
        return int(out) if out else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None


def _ours_or_orphan(pid: int) -> bool:
    """True if this process is an ancestor of pid, or pid is orphaned.

    On a shared machine two pytest controllers can run concurrently; a
    machine-wide reap from one run kills the other run's live workers
    (mass `node down: Not properly terminated`). So only reap workers we
    own (our pid appears in the ancestry chain) or workers already
    re-parented to init (direct PPID 1 — their controller is gone, no
    live run owns them). Anything else belongs to someone else's run.
    """
    me = os.getpid()
    current = pid
    for _ in range(32):  # ancestry depth cap; chains are short in practice
        parent = _ppid_of(current)
        if parent is None:
            return False
        if current == pid and parent == 1:
            return True  # orphaned worker, controller already gone
        if parent == me:
            return True
        if parent <= 1:
            return False  # walked past init without meeting us: not ours
        current = parent
    return False


def _reap_xdist_workers() -> None:
    """Find and kill xdist worker processes owned by this run.

    Uses `pgrep` so we don't need psutil. Scoped to our own descendants
    plus init-orphaned workers (see _ours_or_orphan). Idempotent. Catches
    every exception so a reap failure never blocks pytest teardown. For a
    deliberate machine-wide sweep, use scripts/reap-xdist.sh.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", XDIST_WORKER_RE],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return

    pids = [p for p in result.stdout.split() if p.isdigit() and _ours_or_orphan(int(p))]
    if not pids:
        return

    for pid in pids:
        try:
            os.kill(int(pid), 15)  # SIGTERM
        except (OSError, ValueError):
            pass
    time.sleep(0.5)
    for pid in pids:
        try:
            os.kill(int(pid), 9)  # SIGKILL survivors
        except (OSError, ValueError):
            pass


def pytest_unconfigure(config):
    """Run the reap on pytest's normal teardown path.

    Only the controller runs this hook; workers have `workerinput`
    set on their config. The wrap in a try/except keeps the reap
    out of the way on non-xdist runs and on import failures.
    """
    try:
        import xdist  # noqa: F401
    except ImportError:
        return
    if getattr(config, "workerinput", None):
        # We are a worker; workers have no business reaping siblings.
        return
    _reap_xdist_workers()


# atexit covers the case where pytest's unconfigure hook didn't fire
# (e.g. the controller segfaulted, or the test runner killed the
# process group). atexit runs on the normal Python interpreter exit
# path, which is the strongest hook we can install at module load
# time.
#
# Gated on PYTEST_XDIST_WORKER being unset so the worker processes
# (which also import this conftest via xdist's path resolution) don't
# re-register. The controller sets this env var to the worker name
# (e.g. "gw0") once it forks; before that, it's unset, so this code
# only runs in the controller and in non-xdist runs.
if "PYTEST_XDIST_WORKER" not in os.environ:
    atexit.register(_reap_xdist_workers)
