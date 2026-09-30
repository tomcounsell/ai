"""Unit tests for the cuttlefish subprocess wrapper (tools/email_cs/cuttlefish.py).

asyncio.create_subprocess_exec is stubbed so no real manage.py is invoked.
Covers: argv construction (--email scoping, --json append), success JSON parse,
timeout -> raise, non-zero exit -> raise, malformed JSON -> raise, non-object
JSON -> raise, empty-arg guards.
"""

from __future__ import annotations

import asyncio

import pytest

from tools.email_cs import cuttlefish
from tools.email_cs.cuttlefish import (
    CuttlefishCommandError,
    resolve_venv_python,
    run_manage_command,
)


class _FakeProc:
    def __init__(self, *, stdout=b"", stderr=b"", returncode=0, hang=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _patch_exec(monkeypatch, proc, capture=None):
    async def fake_exec(*argv, **kwargs):
        if capture is not None:
            capture["argv"] = list(argv)
            capture["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(cuttlefish.asyncio, "create_subprocess_exec", fake_exec)


def test_resolve_venv_python_expands_home():
    p = resolve_venv_python("~/src/cuttlefish")
    assert p.endswith("/src/cuttlefish/.venv/bin/python")
    assert "~" not in p


@pytest.mark.asyncio
async def test_success_parses_json_object(monkeypatch):
    capture: dict = {}
    _patch_exec(monkeypatch, _FakeProc(stdout=b'{"status": "active"}'), capture)
    result = await run_manage_command(["customer", "show"], "cust@example.com", "~/src/cuttlefish")
    assert result == {"status": "active"}
    argv = capture["argv"]
    assert argv[1] == "manage.py"
    assert "customer" in argv and "show" in argv
    assert "--email" in argv and "cust@example.com" in argv
    assert argv[-1] == "--json"


@pytest.mark.asyncio
async def test_email_scoping_uses_passed_customer_not_body(monkeypatch):
    capture: dict = {}
    _patch_exec(monkeypatch, _FakeProc(stdout=b"{}"), capture)
    await run_manage_command(["customer", "show"], "trusted@id.com", "~/src/cuttlefish")
    argv = capture["argv"]
    idx = argv.index("--email")
    assert argv[idx + 1] == "trusted@id.com"


@pytest.mark.asyncio
async def test_timeout_raises_and_kills(monkeypatch):
    proc = _FakeProc(hang=True)
    _patch_exec(monkeypatch, proc)
    with pytest.raises(CuttlefishCommandError, match="timed out"):
        await run_manage_command(["customer", "show"], "c@e.com", "~/src/cuttlefish", timeout=0.05)
    assert proc.killed is True


@pytest.mark.asyncio
async def test_nonzero_exit_raises(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(returncode=2, stderr=b"boom"))
    with pytest.raises(CuttlefishCommandError, match="exited 2"):
        await run_manage_command(["customer", "show"], "c@e.com", "~/src/cuttlefish")


@pytest.mark.asyncio
async def test_malformed_json_raises(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"not json"))
    with pytest.raises(CuttlefishCommandError, match="malformed JSON"):
        await run_manage_command(["customer", "show"], "c@e.com", "~/src/cuttlefish")


@pytest.mark.asyncio
async def test_non_object_json_raises(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"[1, 2, 3]"))
    with pytest.raises(CuttlefishCommandError, match="not an object"):
        await run_manage_command(["customer", "show"], "c@e.com", "~/src/cuttlefish")


@pytest.mark.asyncio
async def test_launch_failure_raises(monkeypatch):
    async def boom(*a, **k):
        raise FileNotFoundError("no python")

    monkeypatch.setattr(cuttlefish.asyncio, "create_subprocess_exec", boom)
    with pytest.raises(CuttlefishCommandError, match="failed to launch"):
        await run_manage_command(["customer", "show"], "c@e.com", "~/src/cuttlefish")


@pytest.mark.asyncio
async def test_empty_verb_raises():
    with pytest.raises(CuttlefishCommandError, match="empty verb"):
        await run_manage_command([], "c@e.com", "~/src/cuttlefish")


@pytest.mark.asyncio
async def test_empty_email_raises():
    with pytest.raises(CuttlefishCommandError, match="empty customer_email"):
        await run_manage_command(["customer", "show"], "", "~/src/cuttlefish")
