"""Autouse conftest fixtures must not request the shared ``monkeypatch`` (#3147).

pytest sets autouse fixtures up before any fixture a test names in its own
signature, and tears everything down in reverse. When an autouse fixture in
``tests/unit/conftest.py`` requested ``monkeypatch``, the shared fixture was
promoted to the front of every unit test's setup order, so its ``undo()`` ran
*after* every test-declared fixture's teardown. A test shaped like
``def test_x(redis_cleanup, monkeypatch)`` that sabotaged an ORM call then ran
its cleanup against the still-live sabotage and ERRORed at teardown while its
body passed (#3147 through #3152, introduced by 649a3bd9b).

The behavioral probe below is the demonstrated proof from #3147: a fixture
whose teardown looks at a patched object. With the inversion present, teardown
sees ``"patched"``; with the conftest fixtures on private
``pytest.MonkeyPatch.context()`` instances, it sees ``"real"``. The structural
check names the offender directly so the next autouse fixture that reaches
for ``monkeypatch`` fails with a message instead of six scattered teardown
errors in the nightly run.
"""

from __future__ import annotations

import pytest


class _Box:
    def f(self) -> str:
        return "real"


BOX = _Box()


@pytest.fixture
def cleanup_that_observes_the_object():
    """Stands in for a Redis-touching cleanup fixture such as
    ``tests/unit/test_job_model.py::scratch_room_id``."""
    yield
    assert BOX.f() == "real", (
        "a test-declared fixture's teardown ran while the test's own monkeypatch "
        "was still live: an autouse conftest fixture is requesting the shared "
        "`monkeypatch` fixture and inverting teardown order (#3147)"
    )


def test_test_declared_monkeypatch_is_undone_before_earlier_fixtures_tear_down(
    cleanup_that_observes_the_object, monkeypatch, request
):
    monkeypatch.setattr(BOX, "f", lambda: "patched")
    assert BOX.f() == "patched"
    # Setup order is the teardown order reversed: the cleanup fixture must be
    # set up before `monkeypatch` so that `monkeypatch` is torn down first.
    names = list(request.fixturenames)
    assert names.index("cleanup_that_observes_the_object") < names.index("monkeypatch"), names


def test_no_autouse_fixture_requests_the_shared_monkeypatch(request):
    manager = request.session._fixturemanager
    offenders = []
    for name in request.fixturenames:
        for fixturedef in manager.getfixturedefs(name, request.node) or ():
            if not getattr(fixturedef, "_autouse", False):
                continue
            if "monkeypatch" in fixturedef.argnames:
                offenders.append(f"{name} ({fixturedef.func.__module__})")
    assert not offenders, (
        "autouse fixture(s) request the shared `monkeypatch` fixture, which promotes "
        "it to the front of every test's setup and makes its undo run after every "
        f"test-declared fixture's teardown (#3147): {offenders}. Use a private "
        "`pytest.MonkeyPatch.context()` inside the fixture instead."
    )
