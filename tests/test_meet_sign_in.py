"""Signing in a Mac's call profile (docs/plans/meet-sign-in.md): the
profile counts as signed in only with an account and Google's session
cookie; `meet-sign-in` closes its own Chrome and answers from the
profile; a call quits a sign-in window left open on the profile and
refuses only when another call's Chrome holds it."""

import asyncio
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from typing import ClassVar

import pytest

from bridges import meet
from core import call

pytestmark = pytest.mark.spend(usd=0)
EMAIL = "valor@example.com"


def signed_in(profile, email=EMAIL, host=".google.com", sid=True):
    """`profile` as Chrome leaves it signed in: the account in its
    preferences, the session cookie in its cookies."""
    default = profile / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Preferences").write_text(json.dumps({"account_info": [{"email": email}] if email else []}))
    with contextlib.closing(sqlite3.connect(default / "Cookies")) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cookies (host_key TEXT, name TEXT, value TEXT)")
        db.execute("INSERT INTO cookies VALUES (?, ?, '')", (host, "SID" if sid else "NID"))
        db.commit()


FAKE = textwrap.dedent(
    """\
    import json, signal, sqlite3, sys, time, pathlib
    profile = pathlib.Path(next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")))
    mode = {mode!r}

    def sign():
        d = profile / "Default"; d.mkdir(parents=True, exist_ok=True)
        (d / "Preferences").write_text(json.dumps({{"account_info": [{{"email": "{email}"}}]}}))
        db = sqlite3.connect(d / "Cookies")
        db.execute("CREATE TABLE IF NOT EXISTS cookies (host_key TEXT, name TEXT, value TEXT)")
        db.execute("INSERT INTO cookies VALUES ('.google.com', 'SID', '')"); db.commit(); db.close()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    (profile / "argv").write_text(json.dumps(sys.argv))
    time.sleep(0.3)
    if mode == "signs":
        sign()
        time.sleep(30)  # until ended
        (profile / "outlived").write_text("")
    elif mode == "quits signed in":
        sign()  # what a ⌘Q's clean exit saves
    """
)


def chrome(tmp_path, mode):
    path = tmp_path / f"chrome-{mode.replace(' ', '-')}"
    path.write_text(f"#!{sys.executable}\n" + FAKE.format(mode=mode, email=EMAIL))
    path.chmod(0o755)
    return str(path)


def sign_in(profile, path):
    said = []
    code = call.sign_in(str(profile), chrome=path, poll_s=0.05, say=said.append)
    return code, said


@contextlib.contextmanager
def held(profile, on=None, *, call_=False, stubborn=False):
    """A live process started on `on` (default `profile`) whose pid
    `profile`'s `SingletonLock` names, as a Chrome holding it: a call's
    (`--remote-debugging-pipe`) with `call_`; one that ignores SIGTERM with
    `stubborn`."""
    profile.mkdir(parents=True, exist_ok=True)
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(flush=True); time.sleep(60)"
    proc = subprocess.Popen(
        [sys.executable, "-c", code if stubborn else "print(flush=True); import time; time.sleep(60)",
         f"--user-data-dir={on or profile}", *(["--remote-debugging-pipe"] if call_ else [])],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    proc.stdout.readline()  # its handlers are set
    os.symlink(f"Mac.local-{proc.pid}", profile / "SingletonLock")
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


# -- profile_ready ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "host", "sid", "ready"),
    [(EMAIL, ".google.com", True, True), (EMAIL, ".google.com", False, False),
     (None, ".google.com", True, False), (EMAIL, ".example.com", True, False)],
    ids=["account-and-sid", "no-sid", "no-account", "sid-on-another-host"],
)  # fmt: skip
def test_the_profile_is_ready_only_with_an_account_and_googles_session_cookie(
    tmp_path, email, host, sid, ready
):
    signed_in(tmp_path, email=email, host=host, sid=sid)
    assert call.profile_ready(str(tmp_path)) is ready


def test_preferences_naming_the_account_without_cookies_are_not_signed_in(tmp_path):
    signed_in(tmp_path)
    (tmp_path / "Default" / "Cookies").unlink()
    assert call.account(str(tmp_path)) is None


def test_a_session_cookie_chrome_has_not_merged_into_its_cookies_yet_counts(tmp_path):
    signed_in(tmp_path, sid=False)
    with contextlib.closing(sqlite3.connect(tmp_path / "Default" / "Cookies")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO cookies VALUES ('.google.com', 'SID', '')")
        writer.commit()
        assert (tmp_path / "Default" / "Cookies-wal").stat().st_size > 0
        assert call.account(str(tmp_path)) == EMAIL


# -- meet-sign-in --------------------------------------------------------------------


def test_sign_in_ends_its_own_chrome_once_signed_in(tmp_path):
    profile = tmp_path / "meet-chrome"
    code, said = sign_in(profile, chrome(tmp_path, "signs"))
    argv = json.loads((profile / "argv").read_text())
    assert code == 0 and f"signed in as {EMAIL}" in said[-1]
    assert f"--user-data-dir={profile}" in argv and "https://accounts.google.com/" in argv
    assert not (profile / "outlived").exists() and meet.holder(str(profile)) is None
    assert "⌘Q" in said[0]


def test_a_sign_in_saved_only_as_chrome_quits_counts(tmp_path):
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits signed in"))
    assert code == 0 and f"signed in as {EMAIL}" in said[-1]


def test_chrome_quit_without_signing_in_says_so(tmp_path):
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits"))
    assert code == 1 and "not signed in" in said[-1] and "meet-sign-in" in said[-1]


def test_a_profile_already_signed_in_starts_no_chrome(tmp_path):
    signed_in(tmp_path / "p")
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "signs"))
    assert code == 0 and said == [f"already signed in as {EMAIL}; {call.settings.machine} can join calls"]
    assert not (tmp_path / "p" / "argv").exists()


def test_a_profile_open_in_a_sign_in_window_says_sign_in_there_and_leaves_it(tmp_path):
    profile = tmp_path / "p"
    with held(profile) as pid:
        code, said = sign_in(profile, chrome(tmp_path, "signs"))
        assert code == 1 and len(said) == 1 and f"pid {pid}" in said[0]
        assert "sign in as valor@yuda.me there" in said[0] and "may be left open" in said[0]
        assert not (profile / "argv").exists()
        os.kill(pid, 0)  # still running


def test_a_profile_a_call_holds_is_named_and_left_running(tmp_path):
    profile = tmp_path / "p"
    with held(profile, call_=True) as pid:
        code, said = sign_in(profile, chrome(tmp_path, "signs"))
        assert code == 1 and said == [
            f"the call profile is in use by a call's Chrome (pid {pid}); run this again after the call"
        ]
        os.kill(pid, 0)


@pytest.mark.parametrize("lock", ["stale", "another profile"])
def test_a_lock_no_chrome_on_this_profile_holds_lets_sign_in_go_ahead(tmp_path, lock):
    profile = tmp_path / "p"
    if lock == "stale":
        profile.mkdir()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        os.symlink(f"Mac.local-{dead.pid}", profile / "SingletonLock")
        code, _ = sign_in(profile, chrome(tmp_path, "signs"))
    else:
        with held(profile, on=tmp_path / "other"):
            code, _ = sign_in(profile, chrome(tmp_path, "signs"))
    assert code == 0


# -- the call's Chrome ---------------------------------------------------------------


class Launch:
    """Playwright whose Chrome starts unless the profile is held, as Chrome
    refuses a held one; each launch's profile in `launched`."""

    launched: ClassVar[list[str]] = []

    def __init__(self):
        self.chromium = self

    async def start(self):
        return self

    async def stop(self):
        pass

    async def launch_persistent_context(self, profile, **_kw):
        if meet.holder(profile) is not None:
            raise RuntimeError("Failed to create a ProcessSingleton for your profile directory")
        Launch.launched.append(profile)
        raise LookupError("launched")  # the stand-in ends here: no page to drive


@pytest.fixture
def launch(monkeypatch):
    import playwright.async_api

    monkeypatch.setattr(playwright.async_api, "async_playwright", Launch)
    monkeypatch.setattr(Launch, "launched", [])
    return Launch.launched


URL = "https://meet.google.com/abc-defg-hij"


def test_a_call_quits_a_sign_in_window_left_open_and_launches(tmp_path, launch):
    profile = tmp_path / "Application Support" / "meet-chrome"
    with held(profile) as pid:
        with pytest.raises(LookupError, match="launched"):
            asyncio.run(meet.Page(str(profile)).open(URL, 0))
        _, status = os.waitpid(pid, 0)
        assert os.WTERMSIG(status) == 15  # quit by SIGTERM, Chrome's clean quit
    assert launch == [str(profile)]


def test_a_call_on_a_profile_another_call_holds_is_refused_and_leaves_it(tmp_path, launch):
    with held(tmp_path, call_=True) as pid:
        with pytest.raises(RuntimeError, match=rf"in use by another call's Chrome \(pid {pid}\)"):
            asyncio.run(meet.Page(str(tmp_path)).open(URL, 0))
        os.kill(pid, 0)  # still running
    assert launch == []


def test_a_chrome_that_will_not_quit_is_named(tmp_path, launch, monkeypatch):
    monkeypatch.setattr(meet.quit_holder, "__defaults__", (0.3,))
    with held(tmp_path, stubborn=True) as pid:
        with pytest.raises(RuntimeError, match=rf"open in another Chrome \(pid {pid}\); quit it"):
            asyncio.run(meet.Page(str(tmp_path)).open(URL, 0))
        os.kill(pid, 0)
    assert launch == []


@pytest.mark.parametrize("lock", ["none", "stale", "another profile"])
def test_a_lock_no_chrome_on_this_profile_holds_signals_nothing_and_launches(tmp_path, launch, lock):
    profile = tmp_path / "p"
    profile.mkdir()
    with contextlib.ExitStack() as stack:
        if lock == "stale":
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()
            os.symlink(f"Mac.local-{dead.pid}", profile / "SingletonLock")
        elif lock == "another profile":
            other = tmp_path / "other"
            pid = stack.enter_context(held(other))
            os.symlink(f"Mac.local-{pid}", profile / "SingletonLock")
        with pytest.raises(LookupError, match="launched"):
            asyncio.run(meet.Page(str(profile)).open(URL, 0))
        if lock == "another profile":
            os.kill(pid, 0)  # left running
    assert launch == [str(profile)]


# -- a navigation under the call's page (a rejoin, 2026-11-05) ----------------------


class Navigating:
    """A Playwright page whose document is replaced under the first
    `navigations` evaluations, as Meet did on a rejoin."""

    def __init__(
        self,
        navigations,
        error="Page.evaluate: Execution context was destroyed, most likely because of a navigation",
    ):
        self.navigations, self.error, self.loads, self.ran = navigations, error, 0, []

    async def evaluate(self, expression, arg=None):
        from playwright.async_api import Error

        if self.navigations:
            self.navigations -= 1
            raise Error(self.error)
        self.ran.append(expression)
        return {"status": "in", "people": []} if expression == "window.valorState()" else "Join now"

    def locator(self, _selector):
        return self  # no camera button

    async def count(self):
        return 0

    async def wait_for_load_state(self, state):
        assert state == "domcontentloaded"
        self.loads += 1


def test_the_join_and_the_reads_after_it_survive_meet_navigating():
    page = meet.Page("unused")
    page.page = Navigating(2)
    assert asyncio.run(page.join()) == "Join now" and page.page.loads == 2
    page.page = Navigating(1)
    assert asyncio.run(page.state())["status"] == "in" and page.page.ran == ["window.valorState()"]


def test_a_page_that_keeps_navigating_or_fails_otherwise_still_fails():
    from playwright.async_api import Error

    page = meet.Page("unused")
    page.page = Navigating(meet.NAVIGATIONS + 1)
    with pytest.raises(Error, match="Execution context was destroyed"):
        asyncio.run(page.state())
    page.page = Navigating(1, error="Page.evaluate: TypeError: window.valorState is not a function")
    with pytest.raises(Error, match="TypeError"):
        asyncio.run(page.state())
    assert page.page.loads == 0


def test_a_media_connections_state_from_the_page_is_written_to_the_calls_log(capsys):
    """A call that saw who was in it but heard no one (2026-11-05) left no
    trace of Meet's media connection: the page reports each state change."""
    socket = meet.Socket(lambda *a: None, lambda *a: None, lambda *a: None)
    socket.read({"type": "ValorMedia", "connection": 2, "state": "failed"})
    assert capsys.readouterr().err == "media connection 2: failed\n"
