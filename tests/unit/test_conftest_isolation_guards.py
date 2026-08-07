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
import subprocess
import sys
import types

import pytest
import redis

import tests.conftest as _conftest
import tests.db_claim as _db_claim

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
        test_db_num = _conftest._redis_test_db_num()
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

    def test_create_then_filter_split_brain_and_fix(self, request):
        from models.agent_session import AgentSession

        query_module = sys.modules["popoto.models.query"]
        original_query_binding = query_module.POPOTO_REDIS_DB

        base_test_db = _conftest._redis_test_db_num()
        # Different test db than the one redis_test_db set up for this
        # worker. Never db 0 / production. The local Redis server is
        # configured with only 16 logical databases (0-15), and
        # `-n auto` commonly claims dbs 1..N for N workers (bases run
        # low-to-high), so reserve the top of the range as scratch space
        # rather than a large offset that would overflow "DB index out of
        # range". Falls back to 14 in the vanishingly unlikely case this
        # worker's own base test db already IS 15.
        divergent_db = 15 if base_test_db != 15 else 14
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
    def _reset_claim_state(monkeypatch, tmp_path, *, pool_max: int | None = None):
        """Point the claim registry at ``tmp_path`` and start from an unclaimed
        state, tracking test-opened fds so ``finally`` can close only them.

        ``PYTEST_XDIST_WORKER`` is cleared so the claim's legacy fallback is the
        deterministic master branch (db1) rather than whatever worker this file
        happens to land on under ``-n auto``. A test that wants a specific
        worker id sets it back explicitly.
        """
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        monkeypatch.setattr(_db_claim, "_test_db_claim_dir", lambda: str(tmp_path))
        monkeypatch.setattr(_db_claim, "_CLAIMED_TEST_DB", None, raising=False)
        fresh_fds: list[int] = []
        monkeypatch.setattr(_db_claim, "_CLAIM_LOCK_FDS", fresh_fds, raising=False)
        if pool_max is not None:
            monkeypatch.setattr(_db_claim, "_TEST_DB_POOL_MAX", pool_max, raising=False)
        return fresh_fds

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
        fds = self._reset_claim_state(monkeypatch, tmp_path)
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
        fds = self._reset_claim_state(monkeypatch, tmp_path)
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
        fds = self._reset_claim_state(monkeypatch, tmp_path)
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
        fds = self._reset_claim_state(monkeypatch, tmp_path)
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
        the flock on death, so a crashed run never strands a db (criterion 2)."""
        fds = self._reset_claim_state(monkeypatch, tmp_path, pool_max=1)
        holder = self._spawn_flock_holder(str(tmp_path), [1])
        try:
            # Pool is [1..1] and slot 1 is held -> exhausted -> legacy fallback.
            assert _db_claim.claim_test_db() == 1  # legacy master fallback
            # Kill the holder: the kernel releases its flock on reap.
            holder.terminate()
            holder.wait(timeout=10)
            # Fresh process state -> slot 1 is now claimable.
            monkeypatch.setattr(_db_claim, "_CLAIMED_TEST_DB", None, raising=False)
            reclaimed = _db_claim.claim_test_db()
            assert reclaimed == 1, "dead holder's slot must be reclaimable"
            assert len(_db_claim._CLAIM_LOCK_FDS) == 1
        finally:
            if holder.poll() is None:
                holder.terminate()
                holder.wait(timeout=10)
            self._close_fds(fds)

    def test_pool_exhaustion_falls_back_with_warning(self, monkeypatch, tmp_path, caplog):
        """When every slot is held by a live process, the claim falls back to the
        legacy derivation and logs a WARNING — degraded, never silent, never
        worse than pre-#2060 (criterion 4)."""
        import logging

        fds = self._reset_claim_state(monkeypatch, tmp_path, pool_max=2)
        holder = self._spawn_flock_holder(str(tmp_path), [1, 2])
        try:
            monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")  # legacy would be db4
            with caplog.at_level(logging.WARNING, logger="tests.db_claim"):
                db = _db_claim.claim_test_db()
            assert db == 4, "exhausted pool must fall back to legacy gw3->db4"
            assert any("falling back to legacy" in r.getMessage() for r in caplog.records), (
                "fallback must log an observable WARNING"
            )
        finally:
            holder.terminate()
            holder.wait(timeout=10)
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
        _conftest._snapshot_shared_exceptions()
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

            repairs = _conftest._repair_shared_exception_identity()

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
        _conftest._snapshot_shared_exceptions()

        assert _conftest._repair_shared_exception_identity() == {}
        assert sl.StatusConflictError is original
