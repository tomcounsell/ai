"""Sentry reporting (docs/plans/sentry.md): `init` starts only with a DSN in
the key file, tags the component, and a step that raises in the kernel is
captured once."""

import asyncio
import dataclasses

import pytest
import sentry_sdk

from core import sentry, serve
from core.machine import State
from core.settings import settings
from tests import scripted
from tests.bridges import new_task
from tests.test_serve import only, ticks

pytestmark = pytest.mark.spend(usd=0)


def keyfile(monkeypatch, tmp_path, text):
    path = tmp_path / "sentry-dsn"
    if text is not None:
        path.write_text(text)
    monkeypatch.setattr(
        sentry, "settings", dataclasses.replace(settings, pg_passfile=str(tmp_path / "pg-pass"))
    )


@pytest.mark.parametrize("text", [None, "", "\n"])
def test_init_without_a_dsn_calls_nothing(monkeypatch, tmp_path, text):
    keyfile(monkeypatch, tmp_path, text)
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert sentry.init("kernel") is False
    assert calls == []


def test_init_with_a_dsn_passes_it_and_tags_the_component(monkeypatch, tmp_path):
    keyfile(monkeypatch, tmp_path, "https://key@example.invalid/1\n")
    calls, tags = [], {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", tags.__setitem__)
    assert sentry.init("voice") is True
    assert calls[0]["dsn"] == "https://key@example.invalid/1"
    assert calls[0]["server_name"] == settings.machine
    assert tags == {"component": "voice"}


def test_a_step_that_raises_in_the_kernel_is_captured_once(fresh, monkeypatch):
    captured = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", captured.append)

    async def raising(ctx):
        raise RuntimeError("the step raised")

    async def go():
        a = await new_task(fresh)
        await scripted.run_judge(fresh, a, "precise")
        await ticks(only(serve.Kernel(None, {State.PLAN: raising}, None, fresh), a), fresh, 1)

    asyncio.run(go())
    assert [str(e) for e in captured] == ["the step raised"]


def test_the_sentry_routine_loads_like_the_others():
    from core import routines

    r = routines.load("sentry")
    assert r.runner == "task" and r.project == "valor"
