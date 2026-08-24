"""Deterministic regression tests locking in the two xdist test-isolation fixes.

This file is the falsifiable acceptance for `docs/plans/xdist-test-isolation-flakes.md`
(issue #1897). It reproduces both corrupt preconditions DIRECTLY (no reliance on
multi-file collection ordering, which is machine/collection-order dependent and
not worth chasing) and asserts the fixes in ``tests/conftest.py`` repair them.

Root cause 1 (Fix 1 — popoto db-cache invalidation):
    ``_popoto_modules_with_redis_db()`` memoized the set of popoto submodules
    holding a ``POPOTO_REDIS_DB`` symbol using a SOLE ``len(sys.modules)`` cache
    key. That key is non-monotonic under an equal-count eviction-then-reimport
    (e.g. ``mock_claude_sdk_cleanup`` evicting ``agent.*`` between tests, then a
    later import creating a NEW module object under the SAME dotted name): the
    total module count doesn't change, so the stale cache kept serving the OLD
    module object, whose ``POPOTO_REDIS_DB`` was never re-pointed to the test
    db. Writes and reads then split across db=0 (or whatever db was bound at
    import time) and the test db, a real "split-brain" that issue #2037
    observed as a create-then-``query.filter`` miss under
    ``--dist=loadfile`` co-scheduling. The fix adds a compound trigger: rebuild
    when EITHER ``len(sys.modules)`` changes (catches brand-new never-cached
    db-holders) OR any cached module's identity has diverged from
    ``sys.modules`` (catches the equal-count replacement). Neither branch alone
    is sufficient — see the docstring on ``_popoto_modules_with_redis_db`` in
    ``tests/conftest.py`` for the full accounting.

Root cause 2 (Fix 2 — agent-hooks consistency guard):
    CPython only rebinds a submodule as an attribute on its parent package the
    moment that submodule is freshly imported. If some other test/fixture
    replaces or partially rebuilds ``sys.modules["agent"]`` while
    ``sys.modules["agent.hooks"]`` survives from an earlier import, the parent
    never gets ``hooks`` re-bound onto it: ``"agent" in sys.modules and
    "agent.hooks" in sys.modules and not hasattr(sys.modules["agent"],
    "hooks")``. Any dotted-string ``monkeypatch.setattr("agent.hooks...",
    ...)`` then raises ``AttributeError`` during test setup, before the test
    body ever runs. The fix is a separate autouse fixture that detects this
    exact corrupt state and rebinds each cached ``agent.*`` submodule onto its
    parent, restoring the attribute chain while preserving module identity so a
    module-level ``from agent import X`` binding is never stranded (#2551).

Root cause 3 (Fix 3 — shared-exception identity guard, #2603):
    ``importlib.reload`` on a module whose exception classes are imported by
    name elsewhere splits each class in two. See
    ``TestSharedExceptionIdentityGuard`` below.

Every test below that mutates ``sys.modules``, ``tests.conftest`` module-level
caches, or popoto ``POPOTO_REDIS_DB`` bindings restores that state in a
``finally`` block so this file cannot poison other tests sharing its worker.
"""

from __future__ import annotations

import fcntl
import os
import pathlib
import subprocess
import sys
import types

import pytest
import redis

import tests.conftest as _conftest
import tests.db_claim as _db_claim


def _flush_probe_client(db: int, *, on_reach=None):
    """A client-shaped stub carrying a db number, a pool, and no connection.

    The flush guard reads only ``client.connection_pool.connection_kwargs``, so
    both its deny and its permit branch can be exercised without a socket. That
    is not fastidiousness: the pool is ``[1..15]`` and db 0 is production, so
    there is no "safe" number to aim a real ``flushdb()`` at. Driving the guard
    through a stub means no code path in these tests — on this branch, on
    ``main``, or in any intermediate state — can flush a database this process
    does not own (#2628, #2645).

    ``on_reach`` fires if a flush gets past the guard to the client layer, which
    is what makes the deny assertions independent of the guard's own reporting.
    """

    def _execute_command(*args, **kwargs):
        if on_reach is not None:
            on_reach()
        return "REACHED"

    return types.SimpleNamespace(
        connection_pool=types.SimpleNamespace(connection_kwargs={"db": db}),
        execute_command=_execute_command,
    )


# ---------------------------------------------------------------------------
# Test A — agent-hooks guard repair (Fix 2)
# ---------------------------------------------------------------------------


class TestAgentHooksGuardRepair:
    """Drive the ``agent_hooks_consistency_guard`` fixture's generator body
    directly (via ``__wrapped__``) so we can construct the corrupt precondition
    and observe the repair within a single test, without depending on pytest's
    inter-test collection order (the fragile approach the superseded smoke test
    used).
    """

    def _drive_guard_setup(self):
        """Run the guard fixture's setup phase (up to its ``yield``).

        Returns the generator so the caller can advance it past ``yield`` for
        the (no-op) teardown phase.
        """
        gen = _conftest.agent_hooks_consistency_guard.__wrapped__()
        next(gen)  # run setup-phase body
        return gen

    def _finish_guard(self, gen):
        try:
            next(gen)
        except StopIteration:
            pass

    def test_guard_repairs_corrupt_hooks_less_agent_state(self, monkeypatch):
        """Corrupt state (agent present, agent.hooks cached, parent link severed)
        is repaired by the guard: a dotted monkeypatch.setattr against
        agent.hooks.pre_tool_use resolves cleanly afterward.
        """
        import agent.hooks.pre_tool_use  # noqa: F401 - ensure real modules are cached

        real_agent = sys.modules["agent"]
        fake_agent = types.ModuleType("agent")
        sys.modules["agent"] = fake_agent
        try:
            # Precondition exactly as described in the guard's docstring.
            assert "agent" in sys.modules
            assert "agent.hooks" in sys.modules
            assert not hasattr(sys.modules["agent"], "hooks")

            gen = self._drive_guard_setup()
            try:
                # Guard rebinds each cached submodule onto its parent, so the
                # attribute chain the dotted walk needs is intact again.
                assert hasattr(sys.modules["agent"], "hooks")
                assert hasattr(sys.modules["agent"].hooks, "pre_tool_use")

                # The real regression: a dotted-string monkeypatch.setattr must
                # resolve without AttributeError now that the chain is intact.
                monkeypatch.setattr(
                    "agent.hooks.pre_tool_use.TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES",
                    (),
                    raising=False,
                )
            finally:
                self._finish_guard(gen)
        finally:
            sys.modules["agent"] = real_agent

    def test_guard_repair_preserves_module_identity(self):
        """Repair must not strand a module-level ``from agent import X`` binding.

        Issue #2551: the guard used to evict every ``agent.*`` key. A test
        module that bound a submodule at import time then called a stale object
        while ``patch("agent.X.seam")`` re-imported and patched a fresh one, so
        the seam under test was never patched and the test failed for reasons
        unrelated to its subject.
        """
        from agent import reap_killlist as bound_at_import

        real_agent = sys.modules["agent"]
        sys.modules["agent"] = types.ModuleType("agent")
        try:
            gen = self._drive_guard_setup()
            try:
                assert sys.modules["agent.reap_killlist"] is bound_at_import
            finally:
                self._finish_guard(gen)
        finally:
            sys.modules["agent"] = real_agent

    def test_guard_is_noop_on_healthy_agent_state(self):
        """A healthy agent (hooks properly bound) must be left untouched."""
        import agent.hooks.pre_tool_use  # noqa: F401 - ensure healthy state

        assert hasattr(sys.modules["agent"], "hooks")

        before = {
            key: sys.modules[key]
            for key in sys.modules
            if key == "agent" or key.startswith("agent.")
        }
        assert before  # sanity: agent.* is actually populated

        gen = self._drive_guard_setup()
        self._finish_guard(gen)

        after = {
            key: sys.modules[key]
            for key in sys.modules
            if key == "agent" or key.startswith("agent.")
        }
        # No-op: same key set, same module objects (identity preserved).
        assert set(before) == set(after)
        assert all(before[key] is after[key] for key in before)

    def test_guard_is_noop_when_agent_not_imported(self):
        """Per Failure Path Test Strategy: the guard must not raise when
        `agent` isn't imported at all -- it's a pure sys.modules membership /
        hasattr check.
        """
        saved = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "agent" or key.startswith("agent.")
        }
        try:
            assert "agent" not in sys.modules

            gen = self._drive_guard_setup()
            self._finish_guard(gen)

            # Still absent -- guard neither imports agent nor raises.
            assert "agent" not in sys.modules
        finally:
            sys.modules.update(saved)


# ---------------------------------------------------------------------------
# Test B — falsifiable binding gate for the popoto db-cache compound trigger
# (Fix 1)
# ---------------------------------------------------------------------------


class TestPopotoModuleCacheBindingGate:
    """This is the most important test in the file: it is engineered so that
    the pre-fix SOLE ``len(sys.modules)`` cache key would be RED (miss the
    equal-count module-identity swap and keep serving the stale object) while
    the compound trigger (len OR identity) is GREEN.

    We do NOT rely on "import a fresh popoto submodule mid-test" as the sole
    check -- that changes ``len(sys.modules)`` too, so even the pre-fix
    sole-len key would rebuild and the test would false-green without proving
    anything about the identity branch.
    """

    @pytest.fixture(autouse=True)
    def _snapshot_and_restore_cache_globals(self):
        """Hermeticity: every mutation this class makes to
        ``tests.conftest``'s module-private cache globals, or to
        ``sys.modules`` entries, is undone afterward so this file cannot
        poison other tests sharing the worker.
        """
        saved_cache = dict(_conftest._POPOTO_MODULE_CACHE)
        saved_len = _conftest._POPOTO_MODULE_CACHE_LEN
        saved_sys_modules_entries: dict[str, object | None] = {}
        yield saved_sys_modules_entries
        # Restore any sys.modules entries this test swapped or added.
        for name, original in saved_sys_modules_entries.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        _conftest._POPOTO_MODULE_CACHE = saved_cache
        _conftest._POPOTO_MODULE_CACHE_LEN = saved_len

    def test_identity_divergence_forces_rebuild_with_len_unchanged(
        self, _snapshot_and_restore_cache_globals, request
    ):
        tracked = _snapshot_and_restore_cache_globals

        # 1. Warm the cache against the real, currently-imported popoto state.
        warm = _conftest._popoto_modules_with_redis_db()
        assert warm, (
            "popoto must already be imported with db-holding submodules by this point in the suite"
        )

        target_name = "popoto.models.query"
        assert target_name in _conftest._POPOTO_MODULE_CACHE, (
            f"expected {target_name!r} to be a cached popoto db-holder; "
            f"cached names: {sorted(_conftest._POPOTO_MODULE_CACHE)}"
        )
        stale_module = _conftest._POPOTO_MODULE_CACHE[target_name]
        assert sys.modules[target_name] is stale_module

        # 2. Build a FRESH module object under the SAME name, carrying its own
        #    POPOTO_REDIS_DB -- an equal-count, equal-name-set replacement
        #    (mirrors mock_claude_sdk_cleanup's evict-then-reimport pattern).
        fresh_module = types.ModuleType(target_name)
        # A plain sentinel is enough here -- no redis command is ever issued
        # against it, we only need `POPOTO_REDIS_DB` to be a distinct object
        # from stale_module's binding so identity (`is`) is falsifiable.
        fresh_module.POPOTO_REDIS_DB = object()

        tracked[target_name] = sys.modules[target_name]  # remember for teardown
        sys.modules[target_name] = fresh_module

        # 3. Pre-seed the post-fix globals to the CURRENT state so the `len`
        #    branch does NOT fire -- only the identity branch can catch the
        #    swap. Swapping in place under the same name does not change
        #    len(sys.modules), so this reflects reality; we set it explicitly
        #    per the plan's instruction to make the non-firing of the len
        #    branch airtight regardless of import activity elsewhere.
        _conftest._POPOTO_MODULE_CACHE = dict(_conftest._POPOTO_MODULE_CACHE)
        _conftest._POPOTO_MODULE_CACHE[target_name] = stale_module  # still the OLD object
        _conftest._POPOTO_MODULE_CACHE_LEN = len(sys.modules)

        assert len(sys.modules) == _conftest._POPOTO_MODULE_CACHE_LEN, (
            "len branch must not fire: this proves any rebuild below is caused "
            "solely by the identity-divergence branch"
        )

        # 4. The load-bearing assertion: the rebuilt cache must return the
        #    NEW object by identity, not the stale cached one.
        rebuilt = _conftest._popoto_modules_with_redis_db()
        returned_for_target = [m for m in rebuilt if getattr(m, "__name__", None) == target_name]
        assert len(returned_for_target) == 1
        assert returned_for_target[0] is fresh_module, (
            "identity check failed to catch the equal-count module swap -- "
            "this is exactly the pre-fix sole-len-key failure mode (issue #2037)"
        )
        assert returned_for_target[0] is not stale_module

        # 5. Exercise the redis_test_db re-point loop's assignment directly:
        #    after applying the fix's result the way redis_test_db does, the
        #    FRESH module's POPOTO_REDIS_DB must end up pointed at a real test
        #    client (db != 0).
        test_db_num = _db_claim.claim_test_db()
        assert test_db_num != 0
        test_client = redis.Redis(db=test_db_num)
        try:
            for mod in rebuilt:
                mod.POPOTO_REDIS_DB = test_client
            assert sys.modules[target_name].POPOTO_REDIS_DB is test_client
            assert (
                sys.modules[target_name].POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"]
                != 0
            )
        finally:
            test_client.close()

    def test_len_branch_catches_a_brand_new_never_cached_holder(
        self, _snapshot_and_restore_cache_globals
    ):
        """Companion assertion (Success Criteria, Test B): a genuinely new
        popoto db-holder name that was never cached before must appear in the
        rebuilt cache -- this is the branch an identity-only check would miss
        (any() over an existing cache is vacuously False for unseen names).
        """
        tracked = _snapshot_and_restore_cache_globals

        # Warm first so we have a real baseline to diverge from.
        _conftest._popoto_modules_with_redis_db()

        new_name = "popoto._test_fake_db_holder_for_len_branch"
        assert new_name not in sys.modules
        fake_module = types.ModuleType(new_name)
        fake_module.POPOTO_REDIS_DB = object()
        tracked[new_name] = None  # wasn't present before; remove at teardown
        sys.modules[new_name] = fake_module

        rebuilt = _conftest._popoto_modules_with_redis_db()
        assert any(getattr(m, "__name__", None) == new_name for m in rebuilt)


# ---------------------------------------------------------------------------
# Test C — #2037 real-record create-then-query.filter split-brain regression
# ---------------------------------------------------------------------------


class TestPopotoSplitBrainRoundTrip:
    """Reproduces the exact #2037 mechanism directly: divert ONE read-path
    popoto module's local POPOTO_REDIS_DB binding to a different test db,
    prove a create-then-filter round trip misses the record, then apply the
    fixed re-point path and prove the identical round trip now succeeds.
    """

    def test_create_then_filter_split_brain_and_fix(self, request, scratch_test_db):
        from models.agent_session import AgentSession

        query_module = sys.modules["popoto.models.query"]
        original_query_binding = query_module.POPOTO_REDIS_DB

        base_test_db = _db_claim.claim_test_db()
        # A SECOND database this process owns, claimed from the same flock pool
        # as the primary one. It used to be a hardcoded 15 called "scratch
        # space" — but 15 is the top slot of the claim pool, so this test was
        # flushing whichever concurrent pytest run happened to hold it (#2628).
        # A db this process does not own is not scratch space; it is someone
        # else's dataset.
        divergent_db = scratch_test_db
        assert divergent_db != 0
        assert divergent_db != base_test_db

        divergent_client = redis.Redis(db=divergent_db)
        divergent_client.flushdb()  # keep this scratch db clean for a deterministic miss

        created = None
        correct_test_client = None
        project_key = f"test-xdist-split-brain-{id(self)}"
        try:
            # --- Step 1: reproduce the split-brain -----------------------
            # Read-path (query) binding diverted to a different db than the
            # write path (models.base), which stays on the correct test db
            # via the autouse redis_test_db fixture.
            query_module.POPOTO_REDIS_DB = divergent_client

            created = AgentSession.create(
                project_key=project_key,
                session_id=f"split-brain-{id(self)}",
                working_dir="/tmp",
                status="running",
            )

            # Filter on a STRING field (project_key), not a bool -- Popoto
            # stores bools as strings ("True"/"False"), a known filter
            # footgun that would confound this assertion.
            missed = list(AgentSession.query.filter(project_key=project_key))
            assert missed == [], (
                "expected the create-then-filter round trip to MISS the "
                "record while the query module's binding is diverted -- "
                "this demonstrates the #2037 split-brain mechanism"
            )

            # --- Step 2: apply the FIXED re-point path --------------------
            # Mirrors what the redis_test_db fixture does: walk every popoto
            # db-holding module and repoint it at the correct test client.
            correct_test_client = redis.Redis(db=base_test_db)
            for mod in _conftest._popoto_modules_with_redis_db():
                mod.POPOTO_REDIS_DB = correct_test_client

            # --- Step 3: identical round trip now succeeds ----------------
            found = list(AgentSession.query.filter(project_key=project_key))
            assert len(found) == 1
            assert found[0].session_id == created.session_id
        finally:
            # Restore the query module's binding before attempting cleanup so
            # the delete() below (and the autouse redis_test_db teardown)
            # operate against the correct test db.
            query_module.POPOTO_REDIS_DB = original_query_binding
            for mod in _conftest._popoto_modules_with_redis_db():
                mod.POPOTO_REDIS_DB = original_query_binding
            if created is not None:
                # ORM delete only -- never raw Redis on Popoto-managed keys.
                remaining = list(AgentSession.query.filter(project_key=project_key))
                for record in remaining:
                    record.delete()
            if correct_test_client is not None:
                correct_test_client.close()
            divergent_client.flushdb()
            divergent_client.close()


# ---------------------------------------------------------------------------
# Test C — per-process test-DB claim (Fix for #2060)
# ---------------------------------------------------------------------------
class TestPerProcessDbClaim:
    """Deterministic acceptance for the cross-process test-DB collision fix (#2060).

    Root cause: ``redis_test_db``/``_redis_test_db_num`` partitioned the test DB
    only by xdist worker id WITHIN one run (``gw{N}->db{N+1}``; master->db1), so
    two concurrent pytest PROCESSES both derived db1 and one's per-test
    ``flushdb()`` wiped the other's data mid-test — the intermittent
    ``test_cli_hook_denies_over_budget_exit_2`` fail-open. The fix has each
    process atomically claim a UNIQUE db from ``[1..TEST_DB_POOL_MAX]`` via a
    held ``fcntl.flock`` on a per-db lock file, with automatic OS release on
    process death and a graceful legacy fallback when the pool is exhausted.

    These tests isolate the claim registry to a ``tmp_path`` and reset the
    module-global claim state in ``finally`` so they never disturb the running
    session's own claim (this file's poisoning-safety rule).
    """

    @staticmethod
    def _spawn_flock_holder(claim_dir: str, slots: list[int]) -> subprocess.Popen:
        """Spawn a child that holds ``fcntl.flock`` on the given slot lock files.

        Real cross-process flock semantics — the child prints ``READY`` only
        after acquiring every lock, then sleeps until terminated. Killing it
        makes the kernel release the locks, exercising the auto-reclaim path.
        """
        code = (
            "import fcntl, os, sys, time\n"
            "d = sys.argv[1]; slots = [int(x) for x in sys.argv[2:]]\n"
            "fds = []\n"
            "for n in slots:\n"
            "    fd = os.open(os.path.join(d, f'{n}.lock'), os.O_CREAT | os.O_RDWR, 0o644)\n"
            "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    fds.append(fd)\n"
            "print('READY', flush=True)\n"
            "time.sleep(300)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code, claim_dir, *[str(s) for s in slots]],
            stdout=subprocess.PIPE,
            text=True,
        )
        line = proc.stdout.readline()
        assert "READY" in line, f"flock holder failed to start: {line!r}"
        return proc

    @staticmethod
    def _reset_claim_state(
        monkeypatch,
        tmp_path,
        *,
        pool_max: int | None = None,
        wait_s: int | None = None,
    ):
        """Point the claim registry at ``tmp_path`` and start from an unclaimed
        state, tracking test-opened fds so ``finally`` can close only them.

        ``PYTEST_XDIST_WORKER`` is cleared so the claim's legacy fallback is the
        deterministic master branch (db1) rather than whatever worker this file
        happens to land on under ``-n auto``. A test that wants a specific
        worker id sets it back explicitly.

        Every mutable piece of claim state is REBOUND to a fresh object, not
        merely reset: ``monkeypatch`` can undo a rebinding but not an in-place
        ``.clear()``. A test that called ``release_test_db_claim()`` against the
        live ``_CLAIMED_DB_NUMS`` would empty it permanently, and every later
        test in that worker would then have its own autouse ``redis_test_db``
        flush denied by the fail-closed ownership guard and error in setup.
        Leaking the other way is just as bad: pushing ``tmp_path`` slot numbers
        into the live set would make the guard PERMIT flushes on databases this
        process does not own. Which worker got hit would vary per run, so
        skipping this rebinding ships a rotating failure set of its own (#2628).

        Returns ``(fds, nums)`` — the fresh fd list and the fresh claimed-number
        set — so assertions can read them.
        """
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        # The synthetic registry hands back a db this process does not own, so the
        # real export would poison the live session env for every later test in this
        # worker. Suppress the write instead of undoing it: there is then no window
        # in which os.environ names a foreign db (#2805 Risk 1). This helper — not
        # each of its 23 call sites — owns the suppression, so the next direct
        # pytest_configure() caller is protected without anyone remembering.
        #
        # REDIS_URL itself is deliberately NOT touched here (no delenv, no setenv):
        # with the write suppressed at the source there is nothing to restore, and
        # a delenv would make REDIS_URL absent for the duration of all 23 tests that
        # use this helper -- at which point every one of the 20 lazy REDIS_URL
        # consumers in tests/ and production modules falls back to its hardcoded
        # "redis://localhost:6379/0" default, manufacturing the exact db0 exposure
        # #2805 exists to remove.
        monkeypatch.setattr(_conftest, "_export_claimed_redis_url", lambda: None)
        monkeypatch.delenv("POPOTO_TEST_DB", raising=False)
        monkeypatch.setattr(_db_claim, "_test_db_claim_dir", lambda: str(tmp_path))
        monkeypatch.setattr(_db_claim, "_CLAIMED_TEST_DB", None, raising=False)
        monkeypatch.setattr(_db_claim, "_CLAIMED_SCRATCH_DB", None, raising=False)
        monkeypatch.setattr(_db_claim, "_CLAIM_FAILURE", None, raising=False)
        fresh_fds: list[int] = []
        fresh_nums: set[int] = set()
        monkeypatch.setattr(_db_claim, "_CLAIM_LOCK_FDS", fresh_fds, raising=False)
        monkeypatch.setattr(_db_claim, "_CLAIMED_DB_NUMS", fresh_nums, raising=False)
        if pool_max is not None:
            monkeypatch.setattr(_db_claim, "_TEST_DB_POOL_MAX", pool_max, raising=False)
        if wait_s is not None:
            monkeypatch.setattr(_db_claim, "_TEST_DB_CLAIM_WAIT_S", wait_s, raising=False)
        return fresh_fds, fresh_nums

    @staticmethod
    def _close_fds(fds: list[int]) -> None:
        for fd in list(fds):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        fds.clear()

    def test_claim_is_in_pool_idempotent_and_releasable(self, monkeypatch, tmp_path):
        """A claim returns a db in the pool, is memoized, holds one lock, and
        ``release_test_db_claim`` frees it (criteria 1 + 5 groundwork)."""
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            db = _db_claim.claim_test_db()
            assert 1 <= db <= _db_claim._TEST_DB_POOL_MAX
            assert _db_claim.claim_test_db() == db, "claim must be memoized"
            assert len(_db_claim._CLAIM_LOCK_FDS) == 1, "exactly one lock held"
            assert os.path.exists(os.path.join(str(tmp_path), f"{db}.lock"))
            _db_claim.release_test_db_claim()
            assert _db_claim._CLAIMED_TEST_DB is None
            assert _db_claim._CLAIM_LOCK_FDS == []
        finally:
            self._close_fds(fds)

    def test_every_consumer_reads_the_same_claim(self, monkeypatch, tmp_path):
        """Every path that names a test db resolves to the SAME claimed number.

        ``redis_test_url``, the autouse fixture's ``_redis_test_db_num``, and
        the subprocess env must never diverge (criterion 5). The subprocess env
        is the one that did diverge in practice: it re-derived the db from
        ``PYTEST_XDIST_WORKER`` instead of reading the claim, so a process whose
        claim landed anywhere but ``worker_id + 1`` handed its own children a db
        owned — and flushed — by a different pytest run (#2605).
        """
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            db = _db_claim.claim_test_db()
            assert _db_claim.claim_test_db() == db, "claim must be memoized"
            assert _conftest._redis_test_db_num() == db
            assert _db_claim.redis_test_url().endswith(f"/{db}")
            assert _db_claim.subprocess_env()["REDIS_URL"].endswith(f"/{db}")
        finally:
            self._close_fds(fds)

    def test_subprocess_env_pins_the_checkout_under_test(self, monkeypatch, tmp_path):
        """``project_root`` lands on PYTHONPATH ahead of anything inherited.

        The shared venv's ``.pth`` names the main checkout, so a worktree's
        subprocess must say which tree it means rather than rely on cwd
        precedence (#2605).
        """
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
            env = _db_claim.subprocess_env(project_root="/my/checkout")
            assert env["PYTHONPATH"].split(os.pathsep)[0] == "/my/checkout"
            assert "/somewhere/else" in env["PYTHONPATH"]
        finally:
            self._close_fds(fds)

    def test_claim_skips_slot_held_by_live_process(self, monkeypatch, tmp_path):
        """A slot whose flock is held by another live process is NOT claimed —
        two live processes therefore never share a db (criterion 1, the fix)."""
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        holder = self._spawn_flock_holder(str(tmp_path), [1])
        try:
            db = _db_claim.claim_test_db()
            assert db != 1, "must skip the slot held by the live holder process"
            assert 2 <= db <= _db_claim._TEST_DB_POOL_MAX
        finally:
            holder.terminate()
            holder.wait(timeout=10)
            self._close_fds(fds)

    def test_dead_holder_slot_is_reclaimed(self, monkeypatch, tmp_path):
        """A slot whose holder process has DIED is reclaimable — the OS releases
        the flock on death, so a crashed run never strands a db (criterion 2).

        The property under test is purely "the kernel frees a dead holder's
        flock". This used to claim once while the pool was fully held and assert
        a prompt legacy fallback; under the wait-then-fail policy (#2628) that
        first call blocks for the whole window and then raises, so the pre-kill
        claim is gone rather than re-asserted.
        """
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path, pool_max=1, wait_s=0)
        holder = self._spawn_flock_holder(str(tmp_path), [1])
        try:
            # Kill the holder: the kernel releases its flock on reap.
            holder.terminate()
            holder.wait(timeout=10)
            reclaimed = _db_claim.claim_test_db()
            assert reclaimed == 1, "dead holder's slot must be reclaimable"
            assert len(_db_claim._CLAIM_LOCK_FDS) == 1
        finally:
            if holder.poll() is None:
                holder.terminate()
                holder.wait(timeout=10)
            self._close_fds(fds)

    def test_pool_exhaustion_raises_rather_than_colliding(self, monkeypatch, tmp_path):
        """An exhausted pool raises, naming the remedy — it never hands back a
        database another live process owns (#2628).

        The old behavior was to fall back to the legacy ``gw{N} -> db{N+1}``
        derivation with a WARNING, which is precisely how one run's per-test
        ``flushdb()`` came to wipe another's data mid-test. A blocked run is
        recoverable; a corrupted baseline is not.
        """
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path, pool_max=2, wait_s=0)
        holder = self._spawn_flock_holder(str(tmp_path), [1, 2])
        try:
            monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")  # legacy would be db4
            with pytest.raises(RuntimeError) as excinfo:
                _db_claim.claim_test_db()
            message = str(excinfo.value)
            assert "2 test-DB slots" in message, "must name the pool size"
            assert "scripts/reap-xdist.sh --apply" in message, "must name the remedy"
            assert _db_claim._CLAIMED_TEST_DB is None, "a failed claim owns nothing"
            assert _db_claim.claimed_test_dbs() == frozenset()
        finally:
            holder.terminate()
            holder.wait(timeout=10)
            self._close_fds(fds)

    def test_exhaustion_is_paid_once_per_process(self, monkeypatch, tmp_path):
        """The second claim against a held pool re-raises instantly (#2628).

        ``_CLAIMED_TEST_DB`` is assigned only on success paths, so without the
        sticky ``_CLAIM_FAILURE`` memo every later caller would re-enter the
        poll and pay the wait again. Under the function-scoped autouse fixture
        that is the wait times the number of tests in the worker — roughly ten
        hours, and structurally invisible to ``--timeout=420`` because no single
        test exceeds it.
        """
        import time

        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path, pool_max=2, wait_s=2)
        holder = self._spawn_flock_holder(str(tmp_path), [1, 2])
        try:
            with pytest.raises(RuntimeError) as first:
                _db_claim.claim_test_db()
            started = time.monotonic()
            with pytest.raises(RuntimeError) as second:
                _db_claim.claim_test_db()
            elapsed = time.monotonic() - started
            assert elapsed < 0.5, f"second claim re-entered the poll ({elapsed:.2f}s)"
            assert str(second.value) == str(first.value)
        finally:
            holder.terminate()
            holder.wait(timeout=10)
            self._close_fds(fds)

    def test_wait_budget_is_read_at_call_time(self, monkeypatch, tmp_path):
        """``_TEST_DB_CLAIM_WAIT_S`` is a module attribute, so tests can patch it.

        Read as a default argument or an import-time local it would be
        unpatchable, and every exhaustion test would not fail but HANG for the
        full window — at which point the failure looks like a stuck suite rather
        than a wrong constant.
        """
        import time

        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path, pool_max=1, wait_s=0)
        holder = self._spawn_flock_holder(str(tmp_path), [1])
        try:
            started = time.monotonic()
            with pytest.raises(RuntimeError):
                _db_claim.claim_test_db()
            elapsed = time.monotonic() - started
            assert elapsed < 1.0, f"wait_s=0 was not honored ({elapsed:.2f}s)"
        finally:
            holder.terminate()
            holder.wait(timeout=10)
            self._close_fds(fds)

    # -- claimed-set accessor, scratch claim, and their isolation --------------

    def test_claimed_set_is_empty_before_any_claim(self, monkeypatch, tmp_path):
        """Nothing is owned until something is claimed — the guard's fail-closed
        rule rests on this being literally true, not approximately."""
        fds, nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            assert _db_claim.claimed_test_dbs() == frozenset()
            db = _db_claim.claim_test_db()
            assert _db_claim.claimed_test_dbs() == frozenset({db})
            assert nums == {db}
        finally:
            self._close_fds(fds)

    def test_scratch_claim_is_a_second_owned_db(self, monkeypatch, tmp_path):
        """A scratch db is a real pool slot, owned and therefore flushable."""
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            primary = _db_claim.claim_test_db()
            scratch = _db_claim.claim_scratch_test_db()
            assert scratch is not None
            assert scratch != primary
            assert _db_claim.claimed_test_dbs() == frozenset({primary, scratch})
            assert os.path.exists(os.path.join(str(tmp_path), f"{scratch}.lock"))
        finally:
            self._close_fds(fds)

    def test_scratch_claim_is_memoized(self, monkeypatch, tmp_path):
        """N scratch calls consume ONE slot, not N.

        Without the memo, a function-scoped fixture handing out scratch dbs
        would walk the 15-slot pool monotonically — with no release path — into
        the fail-hard exhaustion error this same change introduces.
        """
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            _db_claim.claim_test_db()
            first = _db_claim.claim_scratch_test_db()
            for _ in range(5):
                assert _db_claim.claim_scratch_test_db() == first
            assert len(_db_claim.claimed_test_dbs()) == 2
        finally:
            self._close_fds(fds)

    def test_scratch_claim_returns_none_when_pool_is_exhausted(self, monkeypatch, tmp_path):
        """An exhausted pool yields ``None``, never a borrowed number.

        The fixture converts that into a skip. Silently borrowing an unowned db
        and calling it "scratch space" is the exact defect (#2628).
        """
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path, pool_max=1)
        try:
            primary = _db_claim.claim_test_db()
            assert primary == 1
            assert _db_claim.claim_scratch_test_db() is None
        finally:
            self._close_fds(fds)

    def test_release_clears_the_claimed_set_with_the_fds(self, monkeypatch, tmp_path):
        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)
        try:
            _db_claim.claim_test_db()
            _db_claim.claim_scratch_test_db()
            assert len(_db_claim.claimed_test_dbs()) == 2
            _db_claim.release_test_db_claim()
            assert _db_claim.claimed_test_dbs() == frozenset()
            assert _db_claim._CLAIM_LOCK_FDS == []
            assert _db_claim._CLAIMED_SCRATCH_DB is None
        finally:
            self._close_fds(fds)

    def test_reset_helper_never_touches_the_live_claimed_set(self, monkeypatch, tmp_path):
        """A claim/release cycle inside ``_reset_claim_state`` leaves the real
        process-wide claimed set untouched.

        ``monkeypatch`` can undo a rebinding but not an in-place ``.clear()``,
        so a helper that reset the number set by clearing it would permanently
        empty the live one. Every later test in that worker would then have its
        own autouse ``redis_test_db`` flush denied by the fail-closed guard and
        error in setup — a rotating failure set shipped inside the fix for the
        rotating failure set (#2628).
        """
        live_before = set(_db_claim._CLAIMED_DB_NUMS)
        assert live_before, "precondition: this process has claimed a db"
        with pytest.MonkeyPatch.context() as inner:
            fds, _nums = self._reset_claim_state(inner, tmp_path)
            try:
                _db_claim.claim_test_db()
                _db_claim.claim_scratch_test_db()
                _db_claim.release_test_db_claim()
            finally:
                self._close_fds(fds)
        assert set(_db_claim._CLAIMED_DB_NUMS) == live_before

    def test_registry_unreachable_fallback_is_owned_and_flushable(
        self, monkeypatch, tmp_path, caplog
    ):
        """The retained legacy fallback returns a db that IS claimed.

        No flock can be taken when the registry dir is unreachable, so this path
        deliberately degrades rather than refusing to run. But the NUMBER it
        returns must still land in the claimed set: skipping that composes with
        the fail-closed flush guard into a whole-process setup outage, where a
        documented graceful degradation errors every test in the worker.
        """
        import logging

        fds, _nums = self._reset_claim_state(monkeypatch, tmp_path)

        def _boom():
            raise OSError("registry unreachable")

        monkeypatch.setattr(_db_claim, "_test_db_claim_dir", _boom)
        try:
            with caplog.at_level(logging.WARNING, logger="tests.db_claim"):
                db = _db_claim.claim_test_db()
            assert db in _db_claim.claimed_test_dbs(), (
                "the fallback number must be owned, or its own flush is denied"
            )
            assert any("falling back to legacy" in r.getMessage() for r in caplog.records), (
                "the degraded path must stay observable"
            )
            # The permit is asserted through a client-layer stub rather than a
            # real client: this path takes no flock, so the number may in fact
            # belong to a live sibling run and flushing it for real would be the
            # very wipe this file exists to prevent (#2628, #2645).
            probe = _flush_probe_client(db)
            assert redis.Redis.flushdb(probe) == "REACHED", (
                "the guard must permit a flush on the fallback db"
            )
        finally:
            self._close_fds(fds)


# ---------------------------------------------------------------------------
# Test D — shared-exception identity guard (Fix for #2603)
# ---------------------------------------------------------------------------
class TestSharedExceptionIdentityGuard:
    """Deterministic acceptance for the reloaded-exception-class fix (#2603).

    Root cause: ``importlib.reload(models.session_lifecycle)`` keeps the module
    object but rebinds ``StatusConflictError`` to a NEW class. Every module that
    imported the name earlier — every test module, plus the function-local
    imports inside ``agent/agent_session_queue.py`` and
    ``agent/session_executor.py`` — then compares exceptions against a class they
    are no longer instances of, so ``except StatusConflictError`` and
    ``pytest.raises(StatusConflictError)`` silently miss. The damage lands in a
    different file from the one that reloaded, and randomized ordering decides
    whether it happens at all: four tests in
    ``tests/unit/test_teammate_cold_start_finalize.py``, green alone, red in a
    full randomized run.

    Both reloading writers are gone (``tests/unit/test_session_lifecycle.py``
    dropped a reload that recomputed a value it had already asserted unchanged;
    ``tests/unit/test_session_lifecycle_consolidation.py`` moved its fresh-import
    probe into a subprocess). This guard is what keeps the class of defect from
    coming back silently: it restores the original binding and names the test
    that broke it, in the same repair-in-place spirit as the ``agent.*`` guard
    above (#2551).
    """

    def test_reload_breaks_isinstance_and_the_guard_repairs_it(self):
        import importlib

        import models.session_lifecycle as sl

        original = sl.StatusConflictError
        _conftest._snapshot_shared_identity()
        try:
            importlib.reload(sl)

            # The corruption, reproduced directly: a fresh dotted-path import
            # (what every function-local `from models.session_lifecycle import
            # StatusConflictError` does at call time) now yields a class that
            # the exception raised by any earlier-bound caller is NOT an
            # instance of.
            assert sl.StatusConflictError is not original
            raised = original("sid", "pending", "failed", reason="held by an earlier import")
            assert not isinstance(raised, sl.StatusConflictError), (
                "precondition: the reload must have split the class in two"
            )

            repairs = _conftest._repair_shared_identity()

            assert repairs.get("models.session_lifecycle") == ["StatusConflictError"]
            assert sl.StatusConflictError is original
            assert isinstance(raised, sl.StatusConflictError), (
                "after repair, an exception from the shared class must once again "
                "be caught by `except StatusConflictError`"
            )
        finally:
            sl.StatusConflictError = original

    def test_guard_is_a_no_op_when_nothing_reloaded(self):
        """The common path costs a few identity comparisons and changes nothing."""
        import models.session_lifecycle as sl

        original = sl.StatusConflictError
        _conftest._snapshot_shared_identity()

        assert _conftest._repair_shared_identity() == {}
        assert sl.StatusConflictError is original


# ---------------------------------------------------------------------------
# Test E — flush ownership guard (writer 1 of the rotating failure set, #2628)
# ---------------------------------------------------------------------------
class TestFlushOwnershipGuard:
    """Deterministic acceptance for the ownership-enforcing flush guard.

    Root cause: db ownership was a convention every call site was trusted to
    re-derive correctly, and re-deriving it wrong was silent. #2606 replaced the
    ``gw{N} -> db{N+1}`` rule with an flock claim but two call sites never got
    the memo, so they began flushing databases owned by other live pytest
    processes. The victim was whichever process held that slot at that moment,
    which is why the unit suite's failure set changed between identical runs.

    The fix is enforcement at the point of damage: a flush against a db this
    process has not claimed raises at its own line.
    """

    def test_guard_intercepts_a_flush_aimed_at_a_live_holders_slot(self, monkeypatch, tmp_path):
        """THE binding measurement for writer 1, with a real competing owner.

        A child process holds ``flock`` on slot 1, so slot 1 is demonstrably
        another live process's — exactly the condition under which the old code
        wiped a stranger's data. The flush is driven through a client stub with
        no connection, so on **any** branch this test makes zero Redis contact.

        Red on ``main`` is the ABSENCE of a ``RuntimeError``: the db-0-only
        guard waves ``db=1`` through, the call falls into the real ``flushdb``
        implementation, and the ``on_reach`` spy fires. Read the red output as
        "the guard did not intercept", never as "a flush succeeded".
        """
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        holder = TestPerProcessDbClaim._spawn_flock_holder(str(tmp_path), [1])
        try:
            mine = _db_claim.claim_test_db()
            assert mine != 1, "precondition: the holder owns slot 1, not us"

            def _reached():
                pytest.fail("guard did not intercept: a flush reached the client layer")

            # Deliberately the FIRST thing asserted, and phrased without any
            # symbol this change introduces, so the red capture on `main` reports
            # the defect itself rather than a missing import.
            victim = _flush_probe_client(1, on_reach=_reached)
            with pytest.raises(RuntimeError, match="has not claimed") as excinfo:
                redis.Redis.flushdb(victim)

            message = str(excinfo.value)
            assert "db=1" in message, "the denial must name the attempted db"
            assert "scratch_test_db" in message, "the denial must name the remedy"
            assert 1 not in _db_claim.claimed_test_dbs()
            assert str(sorted(_db_claim.claimed_test_dbs())) in message, (
                "the denial must name the claimed set"
            )
        finally:
            holder.terminate()
            holder.wait(timeout=10)
            self._close_all(fds)

    @staticmethod
    def _close_all(fds):
        TestPerProcessDbClaim._close_fds(fds)

    def test_guard_permits_a_flush_on_this_processs_own_db(self):
        """The permitted half, against a REAL client on a genuinely owned db.

        This is the flush the autouse ``redis_test_db`` fixture performs at
        every test's setup and teardown, so denying it would error the whole
        suite. The db comes from the claim API — the process holds an flock on
        it, machine-wide.
        """
        client = redis.Redis(db=_db_claim.claim_test_db())
        try:
            assert client.flushdb() is True
        finally:
            client.close()

    def test_flush_outside_the_pool_is_denied(self, monkeypatch, tmp_path):
        """A db number outside ``[0..TEST_DB_POOL_MAX]`` is unowned like any other."""
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            _db_claim.claim_test_db()
            with pytest.raises(RuntimeError, match="has not claimed"):
                redis.Redis.flushdb(_flush_probe_client(99))
        finally:
            self._close_all(fds)

    def test_db0_keeps_its_own_message(self):
        """Ownership subsumes the db-0 rule, but db 0 keeps its 2026-06-03 message.

        One wrapper, not two idioms: two independently-maintained flush guards
        is how one of them drifts.
        """
        with pytest.raises(RuntimeError, match="db=0"):
            redis.Redis.flushdb(_flush_probe_client(0))

    def test_undeterminable_db_is_denied(self):
        """A client whose ``connection_pool`` raises is treated as db 0 and denied.

        Fail-closed: "I cannot tell which database this is" must never mean
        "flush it".
        """

        class _Hostile:
            @property
            def connection_pool(self):
                raise RuntimeError("no pool for you")

            def execute_command(self, *args, **kwargs):
                pytest.fail("guard did not intercept an undeterminable client")

        with pytest.raises(RuntimeError, match="db=0"):
            redis.Redis.flushdb(_Hostile())

    def test_flushall_is_denied_regardless_of_ownership(self):
        """``flushall`` ignores the selected db and wipes every one, including
        production, so ownership cannot license it."""
        with pytest.raises(RuntimeError, match="flushall"):
            redis.Redis.flushall(_flush_probe_client(_db_claim.claim_test_db()))

    def test_empty_claimed_set_denies_rather_than_permits(self, monkeypatch, tmp_path):
        """Fail-closed on empty is the whole point.

        After the session-start claim the only processes that can observe an
        empty set are the xdist controller (which runs no tests) and the window
        before ``pytest_configure``, so this denies nothing legitimate.
        """
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            assert _db_claim.claimed_test_dbs() == frozenset()
            with pytest.raises(RuntimeError, match="has not claimed"):
                redis.Redis.flushdb(_flush_probe_client(3))
        finally:
            self._close_all(fds)


# ---------------------------------------------------------------------------
# Test F — session-start claim and the popoto repoint (writer 3, #2628)
# ---------------------------------------------------------------------------
class TestSessionClaimHook:
    """``pytest_configure`` establishes the claim before any fixture runs.

    Claiming lazily in the autouse ``redis_test_db`` fixture leaves a real
    "owns nothing yet" state that a test can observe — and popoto's bundled
    plugin does observe it, because its autouse ``_popoto_flush_db`` sets up
    BEFORE ``redis_test_db`` and flushes on every test. The state is designed
    out rather than excused.

    Every case here drives the hook with a stub config. A nested real pytest run
    would claim further flock slots from the same 15-slot machine-global pool
    the outer suite is using, which under the fail-hard exhaustion policy makes
    the test contention-dependent — a new source of run-to-run variation inside
    the change that exists to remove it.
    """

    @staticmethod
    def _stub_config(*, worker: bool, numprocesses):
        config = types.SimpleNamespace(option=types.SimpleNamespace(numprocesses=numprocesses))
        if worker:
            config.workerinput = {"workerid": "gw0"}
            config.workeroutput = {}
        return config

    def test_controller_claims_nothing(self, monkeypatch, tmp_path):
        """The xdist controller runs no tests and must never burn a pool slot.

        It would hold one of only 15 machine-global slots for the entire
        session, raising a 10-worker run's demand to 11 in the same change that
        makes exhaustion fatal.
        """
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            _conftest.pytest_configure(self._stub_config(worker=False, numprocesses=2))
            assert _db_claim._CLAIMED_TEST_DB is None
            assert _db_claim.claimed_test_dbs() == frozenset()
            assert "POPOTO_TEST_DB" not in os.environ
        finally:
            TestPerProcessDbClaim._close_fds(fds)

    def test_worker_claims_and_exports_popoto_test_db(self, monkeypatch, tmp_path):
        """A worker claims, and points popoto's plugin at the same db."""
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            config = self._stub_config(worker=True, numprocesses=4)
            _conftest.pytest_configure(config)
            db = _db_claim._CLAIMED_TEST_DB
            assert db is not None
            assert os.environ["POPOTO_TEST_DB"] == str(db)
            assert config.workeroutput["test_db"] == db
        finally:
            TestPerProcessDbClaim._close_fds(fds)

    def test_n0_master_claims(self, monkeypatch, tmp_path):
        """Under ``-n0`` the master runs the tests, so it must claim.

        The ``numprocesses`` branch is load-bearing: without it a ``-n0`` run
        claims nothing and the fail-closed guard denies every flush.
        """
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            _conftest.pytest_configure(self._stub_config(worker=False, numprocesses=0))
            assert _db_claim._CLAIMED_TEST_DB is not None
            assert os.environ["POPOTO_TEST_DB"] == str(_db_claim._CLAIMED_TEST_DB)
        finally:
            TestPerProcessDbClaim._close_fds(fds)

    def test_popoto_plugin_targets_this_processs_claimed_db(self):
        """THE binding measurement for writer 3.

        popoto ships a ``pytest11`` plugin that this repo loads on every run.
        Left on its default it sits on db 15 — the top slot of the claim pool —
        and its autouse ``_popoto_flush_db`` flushes that db before EVERY test
        in EVERY pytest process on the machine. It is by a wide margin the
        highest-frequency writer, and it lives in installed library code where
        no review of ``tests/`` can see it.

        Asserted on the environment rather than on ``POPOTO_REDIS_DB``: inside a
        test body the autouse ``redis_test_db`` fixture has already replaced
        that client with one on the claimed db, so reading it here would measure
        this repo's own fixture and pass on ``main`` too. ``POPOTO_TEST_DB`` is
        the plugin's own documented resolution input, and it is unset on
        ``main`` — which is the red state.

        The ``== claim_test_db()`` form is the drift detector: if a future
        popoto changes its resolution order, this fails loudly instead of the
        suite silently resuming its rotation.
        """
        assert os.environ.get("POPOTO_TEST_DB") == str(_db_claim.claim_test_db())

    def test_client_ownership_check_skips_the_none_async_client(self, monkeypatch):
        """``_POPOTO_ASYNC_REDIS_DB`` is ``None`` when a session fixture can see it.

        ``None`` is its CORRECT state — the plugin nulls it at both setup and
        teardown of every test so no client binds to a stale event loop.
        Dereferencing it would raise ``AttributeError`` inside a session-scoped
        autouse fixture, erroring every test in the process during setup.
        """
        import popoto.redis_db as rdb

        monkeypatch.setattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None, raising=False)
        gen = _conftest._popoto_client_ownership_check.__wrapped__(_popoto_test_db=None)
        next(gen)
        try:
            next(gen)
        except StopIteration:
            pass

    def test_every_live_popoto_client_sits_on_a_claimed_db(self):
        """The plugin-agnostic half: whatever clients ``popoto.redis_db`` holds,
        each points at a db this process owns.

        This catches any future ``pytest11`` plugin that swaps the popoto
        globals, not just this popoto version — a class no static review of
        ``tests/`` can reach, because the code lives in site-packages.
        """
        import popoto.redis_db as rdb

        claimed = _db_claim.claimed_test_dbs()
        for client in (rdb.POPOTO_REDIS_DB, getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)):
            if client is None:
                continue
            assert client.connection_pool.connection_kwargs.get("db", 0) in claimed

    def test_subprocess_env_does_not_leak_popoto_test_db(self):
        """A nested pytest child claims its OWN slot, so it must not inherit ours.

        The mirror image of the ``REDIS_URL`` rule: that one is shared on
        purpose so a child reads the parent's db, while ``POPOTO_TEST_DB`` is
        read only by popoto's pytest plugin — i.e. only by a nested pytest run,
        which would then point its per-test flush at the parent's database.
        """
        env = _db_claim.subprocess_env()
        # Asserted on a precomputed boolean: a bare ``in env`` renders the whole
        # child environment into the failure report, and that environment
        # carries real credentials.
        leaked = "POPOTO_TEST_DB" in env
        assert not leaked, "a nested pytest child must claim its own db, not inherit ours"
        assert env["REDIS_URL"].endswith(f"/{_db_claim.claim_test_db()}")
        assert _db_claim.subprocess_env(POPOTO_TEST_DB="7")["POPOTO_TEST_DB"] == "7"


# ---------------------------------------------------------------------------
# Test G — run provenance (#2628)
# ---------------------------------------------------------------------------
class TestRunProvenance:
    """Each process reports which db it owned, so the next investigation starts
    from a log line instead of a fresh recon."""

    def test_worker_writes_its_claim_to_workeroutput(self, monkeypatch, tmp_path):
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            config = TestSessionClaimHook._stub_config(worker=True, numprocesses=4)
            _conftest.pytest_configure(config)
            assert config.workeroutput["test_db"] == _db_claim._CLAIMED_TEST_DB
        finally:
            TestPerProcessDbClaim._close_fds(fds)

    def test_controller_surfaces_every_workers_claim(self):
        """xdist discards worker headers, so the controller renders the collected
        values instead."""
        line = _conftest._format_test_db_provenance(
            [{"worker": "gw0", "test_db": 3}, {"worker": "gw1", "test_db": 7}]
        )
        assert "gw0=db3" in line
        assert "gw1=db7" in line

    def test_provenance_line_is_empty_when_nothing_was_collected(self):
        assert _conftest._format_test_db_provenance([]) == ""

    def test_report_header_never_claims(self, monkeypatch, tmp_path):
        """The header hook reads the already-claimed value; it never claims.

        ``pytest_report_header`` also runs in the controller, so calling
        ``claim_test_db()`` here would burn a pool slot for a process that runs
        no tests — the same rule as the ``pytest_configure`` controller branch,
        because it is a property of the process, not of any one hook.
        """
        fds, _nums = TestPerProcessDbClaim._reset_claim_state(monkeypatch, tmp_path)
        try:
            header = _conftest.pytest_report_header(
                TestSessionClaimHook._stub_config(worker=False, numprocesses=0)
            )
            assert "db=None" in header, "the header must not trigger a claim"
            assert _db_claim._CLAIMED_TEST_DB is None
            assert (
                _conftest.pytest_report_header(
                    TestSessionClaimHook._stub_config(worker=False, numprocesses=4)
                )
                is None
            ), "the controller owns no db and reports none"
        finally:
            TestPerProcessDbClaim._close_fds(fds)


# ---------------------------------------------------------------------------
# Test H — module-reload registry leak (writer 2, #2628)
# ---------------------------------------------------------------------------
class TestReloadedRegistryIdentity:
    """A reload orphans more than exception classes.

    ``importlib.reload(agent.index_drift)`` rebinds ``DRIFT_COVERED_MODELS`` to
    a brand-new dict and re-runs the registrations into it, while any module
    that bound the name at import time keeps the old one. A restore fixture
    holding the orphan then snapshots and restores the STALE dict while the test
    writes into the LIVE one: the cleanup silently no-ops and a fake in-memory
    model stays registered for the rest of that worker. ``--dist=loadfile``
    decides whether the reloader and the victim share a worker, so the damage
    lands only some runs — the same rotate-run-to-run signature as the flush
    writer, on a path no db-ownership rule can touch.
    """

    @pytest.fixture(autouse=True)
    def _registry_left_intact(self):
        """Fail the offending test, not a stranger, if the registry is not restored.

        These tests deliberately reload the module they are about to assert on,
        so a slip in their own teardown strands exactly the damage they exist to
        detect — and it lands in whatever file `--dist=loadfile` happens to put
        next on this worker. Ordered before the conftest identity guard's repair,
        so it grades the test's own cleanup rather than the repair's.
        """
        from agent import index_drift

        before = set(index_drift.DRIFT_COVERED_MODELS)
        assert before, "precondition: the registry is populated at test start"
        yield
        assert set(index_drift.DRIFT_COVERED_MODELS) == before, (
            "this test left agent.index_drift's registry altered; every later test "
            "on this worker that reads it now sees the wrong thing"
        )

    def test_reload_leaves_the_registry_identity_intact(self):
        """THE binding measurement for writer 2. Red on ``main``."""
        import importlib

        from agent import index_drift

        original_registry = index_drift.DRIFT_COVERED_MODELS
        before = set(index_drift.covered_model_names())
        _conftest._snapshot_shared_identity()
        try:
            importlib.reload(index_drift)
            assert index_drift.DRIFT_COVERED_MODELS is not original_registry, (
                "precondition: the reload must have orphaned the registry"
            )

            repairs = _conftest._repair_shared_identity()

            assert "DRIFT_COVERED_MODELS" in repairs.get("agent.index_drift", [])
            assert index_drift.DRIFT_COVERED_MODELS is original_registry
            assert set(index_drift.covered_model_names()) == before
        finally:
            index_drift.DRIFT_COVERED_MODELS = original_registry

    def test_registry_restore_fixture_survives_a_reload(self):
        """The victim's cleanup must not be defeated by someone else's reload.

        Registering through the module object means the restore fixture and the
        registration write to the same dict even when a reload has swapped it,
        so no ``FakeCoveredModel`` is stranded for the rest of the worker.
        """
        import importlib

        from agent import index_drift

        original_registry = index_drift.DRIFT_COVERED_MODELS
        # Captured BEFORE anything clears, and into a separate dict. Rebuilding
        # the contents from ``original_registry`` after clearing it reads back an
        # empty dict and strands the registry empty for the rest of the worker --
        # which is this very file's subject, committed by its own teardown.
        original_contents = dict(original_registry)
        before = set(index_drift.covered_model_names())
        _conftest._snapshot_shared_identity()
        try:
            snapshot = dict(index_drift.DRIFT_COVERED_MODELS)  # what restore_registry captures
            importlib.reload(index_drift)
            index_drift.register_drift_model(
                index_drift.ModelDriftSpec(
                    name="FakeCoveredModel",
                    model_loader=lambda: None,
                )
            )
            # The restore, written the way the coverage test writes it: through
            # the MODULE, so it reaches whichever dict is live.
            index_drift.DRIFT_COVERED_MODELS.clear()
            index_drift.DRIFT_COVERED_MODELS.update(snapshot)

            _conftest._repair_shared_identity()

            assert "FakeCoveredModel" not in index_drift.covered_model_names(), (
                "a fake in-memory model was stranded in the live registry"
            )
            assert set(index_drift.covered_model_names()) == before
        finally:
            index_drift.DRIFT_COVERED_MODELS = original_registry
            original_registry.clear()
            original_registry.update(original_contents)


# ---------------------------------------------------------------------------
# Test H — REDIS_URL export survives synthetic hook calls (#2805)
# ---------------------------------------------------------------------------
class TestExportedRedisUrlSurvivesSyntheticHookCalls:
    """The permanent regression detector for #2805.

    Placed in its own class at the END of this file, deliberately -- NOT inside
    ``TestPerProcessDbClaim`` (line ~452), which runs BEFORE the synthetic
    ``pytest_configure()`` calls in ``TestSessionClaimHook`` (line ~1080+). A
    probe collected earlier in the file is structurally incapable of observing
    a leak that a later class's synthetic hook calls would introduce, so this
    class runs last under ``-p no:randomly`` and reads ``os.environ`` after
    every synthetic hook call in the file has already run.

    Both of the first two assertions are phrased against THIS PROCESS's own
    claim rather than a sibling worker, so they fire under the DEFAULT
    ``--dist=load`` (``scripts/pytest-clean.sh`` never issues ``--dist=each`` on
    its own) -- after the AST guard's deletion, these are the only regression
    detectors left, and a detector that only fires under a flag nobody passes
    is not a detector.
    """

    def test_live_process_redis_url_names_its_own_claim(self):
        """The live session's REDIS_URL names this process's claimed db.

        This is the permanent regression test for #2805: it fails the instant
        the export in ``pytest_configure`` stops running or is shadowed.
        """
        assert os.environ["REDIS_URL"].endswith(f"/{_db_claim.claim_test_db()}")

    def test_unguarded_child_inherits_the_parents_claimed_redis_url(self):
        """A plain subprocess.run with NO env= resolves REDIS_URL to the same
        value as the parent's os.environ -- byte-identical, not merely present.

        This is what #2805 makes true by construction: no scanner, no
        allowlist, nothing for a test author to remember.
        """
        result = subprocess.run(
            [sys.executable, "-c", "import os; print(os.environ['REDIS_URL'])"],
            capture_output=True,
            text=True,
            check=True,
        )
        child_url = result.stdout.strip()
        assert child_url == os.environ["REDIS_URL"]

    def test_nested_pytest_child_claims_its_own_db(self, tmp_path):
        """A nested pytest child spawned WITHOUT env= inherits the parent's
        REDIS_URL, then overwrites it with its OWN claim (#2628 invariant).

        The nested target must live under the repo root: pytest run from /tmp
        picks a different rootdir, never loads tests/conftest.py, and this
        assertion would falsely pass by never exercising the child's own
        pytest_configure at all.
        """
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        nested_dir = repo_root / ".pytest_nested_probe_2805"
        nested_dir.mkdir(exist_ok=True)
        probe_file = nested_dir / "test_probe.py"
        out_file = tmp_path / "nested_redis_url.txt"
        try:
            probe_file.write_text(
                "import os\n"
                "def test_probe():\n"
                f"    open({str(out_file)!r}, 'w').write(os.environ['REDIS_URL'])\n"
            )
            parent_url = os.environ["REDIS_URL"]
            subprocess.run(
                [sys.executable, "-m", "pytest", str(probe_file), "-p", "no:randomly", "-n0", "-q"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            nested_url = out_file.read_text()
            assert nested_url != parent_url, (
                "a nested pytest child must claim its own db, not inherit the parent's "
                "(#2628) -- it overwrites the inherited REDIS_URL with its own claim"
            )
        finally:
            probe_file.unlink(missing_ok=True)
            try:
                nested_dir.rmdir()
            except OSError:
                pass

    def test_non_splatting_env_drops_redis_url(self):
        """DOCUMENTATION TEST, not a behavior the system provides.

        The retired 688-line AST guard (``test_subprocess_test_db_isolation.py``,
        deleted by #2805) used to flag any ``env=`` value that was not an
        ``ast.Call``/``ast.Name`` -- including a non-splatting dict literal like
        ``env={"PYTHONPATH": ...}``. That shape drops REDIS_URL entirely and the
        export CANNOT rescue it, because the child never inherits ``os.environ``
        at all. This is the coverage gap #2805's Risk 2 accepts: it is rarer
        than writing no ``env=`` (the shape the export now handles by
        construction), and the runtime backstops (tools/redis_flush_guard.py on
        a db0 flush; conftest's claimed-db flush guard) still fail closed
        underneath it.
        """
        result = subprocess.run(
            [sys.executable, "-c", "import os; print('REDIS_URL' in os.environ)"],
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False"
