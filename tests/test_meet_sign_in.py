"""Signing in a Mac's call profile (docs/plans/meet-sign-in.md): the
profile counts as signed in only with an account and Google's session
cookie; `meet-sign-in` closes its own Chrome and answers from the
profile; a Chrome holding the profile is named, never ended."""

import asyncio
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap

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
def held(profile, on=None):
    """A live process started on `on` (default `profile`) whose pid
    `profile`'s `SingletonLock` names, as a Chrome holding it."""
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={on or profile}"]
    )
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


def test_a_profile_another_chrome_holds_is_named_and_left_running(tmp_path):
    profile = tmp_path / "p"
    with held(profile) as pid:
        code, said = sign_in(profile, chrome(tmp_path, "signs"))
        assert code == 1 and f"pid {pid}" in said[0] and len(said) == 1
        assert not (profile / "argv").exists()
        os.kill(pid, 0)  # still running


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
    """Playwright whose Chrome fails to start, as on a held profile."""

    def __init__(self):
        self.chromium = self

    async def start(self):
        return self

    async def stop(self):
        pass

    async def launch_persistent_context(self, *_a, **_kw):
        raise RuntimeError("Failed to create a ProcessSingleton for your profile directory")


@pytest.fixture
def failing_launch(monkeypatch):
    import playwright.async_api

    monkeypatch.setattr(playwright.async_api, "async_playwright", Launch)


def test_a_call_on_a_held_profile_names_the_chrome_and_leaves_it(tmp_path, failing_launch):
    with held(tmp_path) as pid:
        page = meet.Page(str(tmp_path))
        with pytest.raises(RuntimeError, match=rf"open in another Chrome \(pid {pid}\); quit it"):
            asyncio.run(page.open("https://meet.google.com/abc-defg-hij", 0))
        os.kill(pid, 0)  # still running


def test_a_call_whose_chrome_fails_with_no_holder_gives_playwrights_error(tmp_path, failing_launch):
    with pytest.raises(RuntimeError, match="ProcessSingleton"):
        asyncio.run(meet.Page(str(tmp_path)).open("https://meet.google.com/abc-defg-hij", 0))
