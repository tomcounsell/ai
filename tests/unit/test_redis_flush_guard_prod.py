"""Unit tests for tools/redis_flush_guard.py -- the ambient, interpreter-scope
production-Redis flush guard (Layer 1 of #2645).

Ground rule (D7): no test in this file may construct a real, connected
db=0 client (bare construction or via `from_url` pointed at db 0) and call
`.flushdb()`/`.flushall()` on it. The guard reads only `_db_of(client)` and
raises before touching a socket, so every "does the guard block this"
assertion drives the *unbound* patched function with a `SimpleNamespace`
stub client instead -- if the guard were ever missing, that raises
`AttributeError` on the stub rather than executing a real flush. This repo's
db=0 is live production; a real client would risk repeating the 2026-06-03 /
2026-08-07 incidents from inside a unit test.

Where a test needs to observe delegation (the guard permits the call and
"real" flushdb/flushall gets invoked), the `guarded` fixture below
temporarily replaces `redis.Redis`/`redis.asyncio.Redis`'s `flushdb`/
`flushall` with call-tracking stubs *before* `install()` runs, so whatever
the guard delegates to is a tracker, never the network-touching
implementation -- and restores the originals afterward, since these class
attributes are shared, mutable, global state touched by every other test
file in the same pytest process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import redis
import redis.asyncio as aioredis

import tools.redis_flush_guard as rfg

PYTHON = sys.executable
REPO_ROOT = Path(__file__).resolve().parents[2]


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def _run(code: str, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = _subprocess_env()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PYTHON, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# Anti-criterion (D7): this file must never construct a real, connected db-0
# client. Enforced externally by the plan's Verification row, which greps
# this file for the two forbidden call shapes and requires no match -- not
# by a self-referential in-file test, since spelling out those shapes here
# to check for them would itself contain them.


class _CallTracker:
    """Stands in for the real bound flushdb/flushall. Records invocations,
    never touches a socket."""

    def __init__(self, label: str):
        self.label = label
        self.calls: list[tuple] = []

    def __call__(self, self_client, *args, **kwargs):
        self.calls.append((self_client, args, kwargs))
        return f"{self.label}-ok"


@pytest.fixture
def guarded(monkeypatch):
    """Install the guard onto the REAL redis.Redis / redis.asyncio.Redis
    classes, with flushdb/flushall pre-replaced by call-tracking stubs so a
    delegated call can never reach the network. Fully restores class
    attributes, `_INSTALLED`, and meta-path state afterward.
    """
    orig_sync_flushdb = redis.Redis.flushdb
    orig_sync_flushall = redis.Redis.flushall
    orig_async_flushdb = aioredis.Redis.flushdb
    orig_async_flushall = aioredis.Redis.flushall
    orig_installed = set(rfg._INSTALLED)
    orig_meta_path = list(sys.meta_path)
    orig_finder = rfg._finder_instance

    sync_flushdb = _CallTracker("sync-flushdb")
    sync_flushall = _CallTracker("sync-flushall")
    async_flushdb_tracker = _CallTracker("async-flushdb")
    async_flushall_tracker = _CallTracker("async-flushall")

    async def async_flushdb(self, *args, **kwargs):
        return async_flushdb_tracker(self, *args, **kwargs)

    async def async_flushall(self, *args, **kwargs):
        return async_flushall_tracker(self, *args, **kwargs)

    redis.Redis.flushdb = sync_flushdb
    redis.Redis.flushall = sync_flushall
    aioredis.Redis.flushdb = async_flushdb
    aioredis.Redis.flushall = async_flushall
    monkeypatch.setattr(rfg, "_INSTALLED", set())

    try:
        yield SimpleNamespace(
            sync_flushdb=sync_flushdb,
            sync_flushall=sync_flushall,
            async_flushdb=async_flushdb_tracker,
            async_flushall=async_flushall_tracker,
        )
    finally:
        redis.Redis.flushdb = orig_sync_flushdb
        redis.Redis.flushall = orig_sync_flushall
        aioredis.Redis.flushdb = orig_async_flushdb
        aioredis.Redis.flushall = orig_async_flushall
        sys.meta_path[:] = orig_meta_path
        rfg._finder_instance = orig_finder
        rfg._INSTALLED.clear()
        rfg._INSTALLED.update(orig_installed)


def _stub(db) -> SimpleNamespace:
    return SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs={"db": db}))


# ---------------------------------------------------------------------------
# Core blocking / delegation behavior
# ---------------------------------------------------------------------------
def test_flushdb_db0_is_blocked(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    assert rfg.install() is True
    with pytest.raises(RuntimeError, match="db=0"):
        redis.Redis.flushdb(_stub(0))
    assert guarded.sync_flushdb.calls == []


@pytest.mark.parametrize("db", [1, 5, 15])
def test_flushdb_nonzero_db_delegates(guarded, monkeypatch, db):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    stub = _stub(db)
    assert redis.Redis.flushdb(stub) == "sync-flushdb-ok"
    assert guarded.sync_flushdb.calls == [(stub, (), {})]


@pytest.mark.parametrize("db", [0, 1, 15])
def test_flushall_blocked_at_any_db(guarded, monkeypatch, db):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    with pytest.raises(RuntimeError, match="flushall"):
        redis.Redis.flushall(_stub(db))
    assert guarded.sync_flushall.calls == []


async def test_async_flushdb_db0_is_blocked(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    with pytest.raises(RuntimeError, match="db=0"):
        await aioredis.Redis.flushdb(_stub(0))
    assert guarded.async_flushdb.calls == []


async def test_async_flushdb_nonzero_db_delegates(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    stub = _stub(7)
    assert await aioredis.Redis.flushdb(stub) == "async-flushdb-ok"
    assert guarded.async_flushdb.calls == [(stub, (), {})]


async def test_async_flushall_is_blocked(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    with pytest.raises(RuntimeError, match="flushall"):
        await aioredis.Redis.flushall(_stub(3))
    assert guarded.async_flushall.calls == []


@pytest.mark.parametrize(
    "client",
    [
        SimpleNamespace(),
        SimpleNamespace(connection_pool=SimpleNamespace()),
        SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs={})),
        SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs={"db": "not-a-number"})),
        SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs=None)),
    ],
    ids=[
        "no-connection-pool",
        "no-connection-kwargs",
        "no-db-key",
        "non-integer-db",
        "none-kwargs",
    ],
)
def test_malformed_client_treated_as_db0(guarded, monkeypatch, client):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    with pytest.raises(RuntimeError, match="db=0"):
        redis.Redis.flushdb(client)


# ---------------------------------------------------------------------------
# REDIS_PRODUCTION_FLUSH_OK override -- exact-equality only
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("falsy_value", ["", "0", "false", "no"])
def test_override_falsy_values_leave_guard_armed(guarded, monkeypatch, falsy_value):
    monkeypatch.setenv("REDIS_PRODUCTION_FLUSH_OK", falsy_value)
    rfg.install()
    with pytest.raises(RuntimeError, match="db=0"):
        redis.Redis.flushdb(_stub(0))
    with pytest.raises(RuntimeError, match="flushall"):
        redis.Redis.flushall(_stub(0))


def test_override_1_disarms_flushdb_and_flushall(guarded, monkeypatch):
    monkeypatch.setenv("REDIS_PRODUCTION_FLUSH_OK", "1")
    rfg.install()
    stub = _stub(0)
    assert redis.Redis.flushdb(stub) == "sync-flushdb-ok"
    assert redis.Redis.flushall(stub) == "sync-flushall-ok"


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------
def test_flushdb_error_names_attempted_db_override_and_incidents(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    with pytest.raises(RuntimeError) as exc_info:
        redis.Redis.flushdb(_stub(0))
    message = str(exc_info.value)
    assert "db=0" in message
    assert "REDIS_PRODUCTION_FLUSH_OK=1" in message
    assert "2026-06-03" in message
    assert "2026-08-07" in message
    assert "test db" in message


def test_flushall_error_states_it_wipes_every_db(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    with pytest.raises(RuntimeError) as exc_info:
        redis.Redis.flushall(_stub(5))
    message = str(exc_info.value)
    assert "REDIS_PRODUCTION_FLUSH_OK=1" in message
    assert "2026-06-03" in message
    assert "2026-08-07" in message
    assert "every" in message.lower() and "db" in message.lower()


# ---------------------------------------------------------------------------
# install() contract: idempotence, unimportable redis, never raises
# ---------------------------------------------------------------------------
def test_install_idempotent(guarded):
    assert rfg.install() is True
    first_flushdb = redis.Redis.flushdb
    first_flushall = redis.Redis.flushall
    assert rfg.install() is True
    assert redis.Redis.flushdb is first_flushdb
    assert redis.Redis.flushall is first_flushall


def test_install_returns_false_when_redis_unimportable(monkeypatch):
    # sys.modules[name] = None is the documented way to force `import name`
    # to raise ImportError without touching the real module elsewhere.
    monkeypatch.setitem(sys.modules, "redis", None)
    assert rfg.install() is False


# ---------------------------------------------------------------------------
# D6a: idempotence keyed on _INSTALLED, not the sentinel attribute -- a
# conftest-shaped wrapper on top must not cause a re-wrap / chain growth.
# ---------------------------------------------------------------------------
def test_conftest_shaped_wrapper_on_top_does_not_grow_the_chain(guarded, monkeypatch):
    monkeypatch.delenv("REDIS_PRODUCTION_FLUSH_OK", raising=False)
    rfg.install()
    layer1_flushdb = redis.Redis.flushdb

    call_count = 0

    def _conftest_shaped(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return layer1_flushdb(self, *args, **kwargs)

    _conftest_shaped._db0_guarded = True
    redis.Redis.flushdb = _conftest_shaped

    rfg.install()  # redis.Redis already in _INSTALLED -> must be a no-op

    assert redis.Redis.flushdb is _conftest_shaped  # not re-wrapped
    redis.Redis.flushdb(_stub(5))
    assert call_count == 1  # delegation chain did not grow


# ---------------------------------------------------------------------------
# D2a: arm() is lazy and does not import redis; the finder arms on first
# real import, import-order-agnostic.
# ---------------------------------------------------------------------------
def test_arm_does_not_import_redis():
    result = _run(
        "import sys\n"
        "import tools.redis_flush_guard as rfg\n"
        "rfg.arm()\n"
        "assert 'redis' not in sys.modules, sorted(sys.modules)\n"
        "assert 'redis.asyncio' not in sys.modules\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_arm_then_later_import_redis_still_arms():
    result = _run(
        "import tools.redis_flush_guard as rfg\n"
        "rfg.arm()\n"
        "import redis\n"
        "assert getattr(redis.Redis.flushdb, '_prod_flush_guarded', False) is True\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_import_redis_asyncio_first_also_arms():
    result = _run(
        "import tools.redis_flush_guard as rfg\n"
        "rfg.arm()\n"
        "import redis.asyncio\n"
        "import redis\n"
        "assert getattr(redis.Redis.flushdb, '_prod_flush_guarded', False) is True\n"
        "assert getattr(redis.asyncio.Redis.flushdb, '_prod_flush_guarded', False) is True\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_broken_install_does_not_break_import_redis():
    result = _run(
        "import tools.redis_flush_guard as rfg\n"
        "rfg.arm()\n"
        "rfg.install = lambda: (_ for _ in ()).throw(RuntimeError('boom'))\n"
        "import redis\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Finding B: the installer is loaded by FILE PATH, never by package import
# of `scripts.update` (which drags in ~30 submodules and mutates sys.path).
# ---------------------------------------------------------------------------
def test_self_heal_loads_installer_by_path_not_package_import(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    calls_file = tmp_path / "calls.txt"

    stub_installer = tmp_path / "stub_installer.py"
    stub_installer.write_text(
        "from pathlib import Path\n"
        f"CALLS_FILE = Path(r'{calls_file}')\n"
        "def install_into(venv_path):\n"
        "    CALLS_FILE.write_text(str(venv_path))\n"
    )

    monkeypatch.setattr(rfg, "_PTH_INSTALLER_PATH", stub_installer)
    monkeypatch.setattr(rfg, "_current_site_packages", lambda: str(site_packages))
    fake_venv = str(tmp_path / "fake-venv")
    monkeypatch.setattr(sys, "prefix", fake_venv)
    monkeypatch.setattr(sys, "base_prefix", "/definitely-not-the-prefix")

    sys_path_before = list(sys.path)
    # Delta, not absolute presence: a sibling test in the same xdist worker
    # (tests/unit/test_redis_acl.py) legitimately imports scripts.update.*, and
    # `scripts/update/__init__.py` does `from .run import ...`. Asserting
    # absolute absence would make this test fail on the sibling's import rather
    # than on the property under test, which is that THE SELF-HEAL ITSELF pulls
    # in no part of the update system (round-3 Finding B).
    modules_before = set(sys.modules)

    rfg._self_heal()

    added = set(sys.modules) - modules_before
    assert calls_file.read_text() == fake_venv
    assert sys.path[0] == sys_path_before[0]
    assert not [name for name in added if name.startswith("scripts.update")]
    assert "_rfg_pth_installer" not in added


def test_self_heal_skipped_on_readonly_site_packages(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    site_packages.chmod(0o555)
    if os.access(site_packages, os.W_OK):
        pytest.skip("current user bypasses filesystem permission bits (e.g. root)")

    stub_installer = tmp_path / "stub_installer.py"
    stub_installer.write_text(
        "def install_into(venv_path):\n"
        "    raise AssertionError('must not be called on read-only site-packages')\n"
    )

    monkeypatch.setattr(rfg, "_PTH_INSTALLER_PATH", stub_installer)
    monkeypatch.setattr(rfg, "_current_site_packages", lambda: str(site_packages))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "fake-venv"))
    monkeypatch.setattr(sys, "base_prefix", "/definitely-not-the-prefix")

    try:
        rfg._self_heal()  # must not raise, must not call install_into
    finally:
        site_packages.chmod(0o755)


def test_self_heal_not_a_venv_is_skipped(monkeypatch):
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)  # looks like the system interpreter
    calls = []
    monkeypatch.setattr(
        rfg, "_current_site_packages", lambda: calls.append("called") or "/nonexistent"
    )
    rfg._self_heal()
    assert calls == []  # short-circuited before ever resolving site-packages


def test_self_heal_exceptions_never_escape_callers(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    stub_installer = tmp_path / "stub_installer.py"
    stub_installer.write_text(
        "def install_into(venv_path):\n    raise RuntimeError('simulated installer failure')\n"
    )

    monkeypatch.setattr(rfg, "_PTH_INSTALLER_PATH", stub_installer)
    monkeypatch.setattr(rfg, "_current_site_packages", lambda: str(site_packages))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "fake-venv"))
    monkeypatch.setattr(sys, "base_prefix", "/definitely-not-the-prefix")

    # _self_heal() itself does not swallow -- proves the failure path is
    # actually exercised, not silently absorbed lower down.
    with pytest.raises(RuntimeError, match="simulated installer failure"):
        rfg._self_heal()

    # arm() wraps _self_heal() in try/except and must swallow it cleanly.
    rfg.arm()


def test_import_tools_succeeds_when_redis_flush_guard_unimportable(monkeypatch):
    result = _run(
        "import sys\nsys.modules['tools.redis_flush_guard'] = None\nimport tools\nprint('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Finding C: the sys.meta_path finder swap is lock-guarded and finally-safe.
# ---------------------------------------------------------------------------
def test_finder_survives_find_spec_exception_and_later_import_still_arms():
    result = _run(
        "import sys\n"
        "import importlib.util as iu\n"
        "import tools.redis_flush_guard as rfg\n"
        "rfg._ensure_finder_installed()\n"
        "finder = rfg._finder_instance\n"
        "assert finder in sys.meta_path\n"
        "real_find_spec = iu.find_spec\n"
        "def _raising_find_spec(name, *a, **kw):\n"
        "    if name in ('redis', 'redis.asyncio'):\n"
        "        raise RuntimeError('simulated broken finder chain')\n"
        "    return real_find_spec(name, *a, **kw)\n"
        "iu.find_spec = _raising_find_spec\n"
        "result = finder.find_spec('redis', None, None)\n"
        "assert result is None, result\n"
        "assert finder in sys.meta_path, 'finder must survive the exception'\n"
        "iu.find_spec = real_find_spec\n"
        "import redis\n"
        "assert getattr(redis.Redis.flushdb, '_prod_flush_guarded', False) is True\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_finder_concurrent_find_spec_never_duplicates():
    rfg._ensure_finder_installed()
    finder = rfg._finder_instance
    errors: list[Exception] = []

    def _call():
        try:
            finder.find_spec("redis", None, None)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert sys.meta_path.count(finder) == 1


# ---------------------------------------------------------------------------
# Finding D / D2a-ii: startup budget, measured from the real `-X importtime`
# hot path -- no REDIS_FLUSH_GUARD_DISABLE env var exists.
# ---------------------------------------------------------------------------
def _parse_cumulative_us(importtime_output: str, module_name: str) -> int | None:
    for line in importtime_output.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = line[len("import time:") :].split("|")
        if len(parts) != 3:
            continue
        _self_us, cumulative_us, name = parts
        if name.strip() == module_name:
            try:
                return int(cumulative_us.strip())
            except ValueError:
                continue
    return None


def _measure_boot_shim_cumulative_us() -> float | None:
    """Exactly and only this guard's startup cost, on the real hot path.

    Leg 1: this venv, IF already healed by the `.pth` installer (task
    build-propagation) -- `python -X importtime -c pass` traces `.pth`-driven
    imports with their own `import time:` line, so `_redis_flush_guard_boot`
    shows up there with a genuine cumulative cost.

    Leg 2 (fallback, used while this venv is unhealed -- the installer is a
    separate task and may not exist yet): write a fresh copy of the shim into
    an isolated temp dir and measure `python -X importtime -c "import
    _redis_flush_guard_boot"` via PYTHONPATH. Same module name, same parse
    helper, same quantity -- non-vacuous in both cases, unlike diffing
    against `python -c pass` on an already-healed venv (that diff is ~0,
    since the `.pth` already imported the shim during `site` in both legs).
    """
    healed = subprocess.run(
        [PYTHON, "-X", "importtime", "-c", "pass"],
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    cumulative = _parse_cumulative_us(healed.stderr, "_redis_flush_guard_boot")
    if cumulative is not None:
        return float(cumulative)

    shim_body = (
        "try:\n"
        "    import tools.redis_flush_guard\n"
        "    tools.redis_flush_guard.arm()\n"
        "except Exception:\n"
        "    pass\n"
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        shim_path = Path(tmp_dir) / "_redis_flush_guard_boot.py"
        shim_path.write_text(shim_body)
        env = _subprocess_env()
        env["PYTHONPATH"] = f"{tmp_dir}{os.pathsep}{env['PYTHONPATH']}"
        unhealed = subprocess.run(
            [PYTHON, "-X", "importtime", "-c", "import _redis_flush_guard_boot"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return (
            float(cum)
            if (cum := _parse_cumulative_us(unhealed.stderr, "_redis_flush_guard_boot")) is not None
            else None
        )


# Trials for the startup-budget measurement. `-X importtime` reports wall-clock
# import time, so a single trial measures machine contention as much as it
# measures this guard: on a box running several parallel agents and an xdist
# suite, the same import that costs ~6 ms idle has been observed at ~107 ms.
# The MINIMUM across trials is the standard estimator for the uncontended cost,
# which is the quantity `_STARTUP_BUDGET_MS` is actually about. Taking a mean
# or a single sample would make this assertion a load sensor and a flaky gate.
_STARTUP_TRIALS = 5


@pytest.mark.slow
def test_startup_budget():
    samples = [
        us for _ in range(_STARTUP_TRIALS) if (us := _measure_boot_shim_cumulative_us()) is not None
    ]
    if not samples:
        pytest.skip(
            "Neither the healed venv nor a fresh tmp_path shim produced an "
            "'import time:' line for _redis_flush_guard_boot."
        )
    measured_ms = min(samples) / 1000.0
    print(
        f"redis_flush_guard startup cost: {measured_ms:.3f} ms "
        f"(best of {len(samples)}, budget {rfg._STARTUP_BUDGET_MS} ms)"
    )
    assert measured_ms < rfg._STARTUP_BUDGET_MS


# ---------------------------------------------------------------------------
# D2b-i: the `import tools` self-heal trigger (#2645 round-1 review, blocker 1)
# ---------------------------------------------------------------------------
def test_import_tools_heals_an_unguarded_venv(tmp_path):
    """`tools/__init__.py`'s `arm()` call must actually reach an unhealed venv.

    This is the ONLY Layer 1 propagation path into harness-created
    `.claude/worktrees/{agent}/` checkouts. Those reach neither `/update`
    Step 3.05 nor the worktree-venv bootstrap, and they are the checkout class
    the 2026-08-07 incident script ran from.

    The negative half is already covered by
    `test_import_tools_succeeds_when_redis_flush_guard_unimportable`, which
    passes just as happily with the trigger deleted. This is the positive half:
    replace the import and `arm()` in `tools/__init__.py` with `pass` and this
    goes red. Without it a future "no import side effects in `__init__`"
    cleanup silently disarms Layer 1 for the riskiest checkouts, with a green
    suite.

    Uses a REAL venv rather than a monkeypatched `sys.prefix`: the self-heal
    resolves site-packages through `sysconfig.get_paths()` in the running
    interpreter, so only a genuinely separate interpreter exercises the path
    end to end. `--without-pip` keeps it fast, and `import tools` needs nothing
    beyond the stdlib.
    """
    venv_dir = tmp_path / "unhealed-venv"
    subprocess.run(
        [PYTHON, "-m", "venv", "--without-pip", str(venv_dir)],
        check=True,
        capture_output=True,
        timeout=120,
    )

    site_packages = list(venv_dir.glob("lib/python*/site-packages"))
    assert len(site_packages) == 1, f"expected one site-packages, got {site_packages}"
    pth = site_packages[0] / "zzz_redis_flush_guard.pth"
    shim = site_packages[0] / "_redis_flush_guard_boot.py"

    # Precondition: a fresh venv is unguarded. If this ever fails the test
    # below proves nothing, so assert it rather than assume it.
    assert not pth.exists()
    assert not shim.exists()

    venv_python = venv_dir / "bin" / "python"
    proc = subprocess.run(
        [str(venv_python), "-c", "import tools"],
        cwd=REPO_ROOT,
        env={**_subprocess_env(), "VIRTUAL_ENV": str(venv_dir)},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, f"`import tools` failed: {proc.stderr}"
    assert pth.exists(), (
        "`import tools` did not install the .pth into an unguarded venv — the "
        "D2b-i self-heal trigger in tools/__init__.py is not reaching arm(). "
        f"stderr: {proc.stderr}"
    )
    assert shim.exists(), "the .pth landed without its boot shim"


def test_a_class_that_fails_to_patch_is_not_recorded_as_installed():
    """`_INSTALLED.add()` runs only after both assignments succeed.

    Registering up front meant a class that failed to patch looked installed
    forever: the early return in `_install_on_class` stopped `install()` ever
    retrying it, and `is_installed()` answered True for an unpatched class.
    Not reachable with real redis-py classes, but "the guard reports itself
    healthy while inert" is the one lie this module must never tell, so it is
    pinned rather than left to inspection.
    """

    class _Frozen:
        """Refuses attribute assignment, so patching it raises."""

        def flushdb(self):  # pragma: no cover - never invoked
            raise AssertionError("real flushdb must never run")

        def flushall(self):  # pragma: no cover - never invoked
            raise AssertionError("real flushall must never run")

        def __init_subclass__(cls):  # pragma: no cover - defensive
            raise AssertionError("not subclassed")

    class _FrozenMeta(type):
        def __setattr__(cls, name, value):
            raise TypeError(f"{cls.__name__} refuses attribute assignment: {name}")

    # noqa N806: this is a class, built dynamically so the metaclass applies;
    # a lowercase name would misrepresent what it is.
    Frozen = _FrozenMeta("Frozen", (), dict(vars(_Frozen)))  # noqa: N806

    assert rfg.is_installed(Frozen) is False

    with pytest.raises(TypeError):
        rfg._install_on_class(Frozen, is_async=False)

    assert rfg.is_installed(Frozen) is False, (
        "a class that failed to patch was recorded as installed — install() "
        "will never retry it and is_installed() now lies about it"
    )
    assert getattr(Frozen.flushdb, "_prod_flush_guarded", False) is False
