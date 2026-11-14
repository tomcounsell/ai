"""Signing in a Mac's call profile (docs/plans/meet-sign-in.md): the
profile counts as signed in only with an account and Google's session
cookie; `meet-sign-in` signs in by itself from 1Password where it can,
else waits for Tom to quit its Chrome and answers from where Meet
settles; Valor passes Google's sign-in with its own login
(`bridges/meet/google.py`); a call quits a sign-in window left open on the profile and
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
        sign()  # mid-way: Google's cookies come before its passkey step ends
        time.sleep(1)
        (profile / "outlived").write_text("")  # Tom quits it
    elif mode == "quits signed in":
        sign()  # what a ⌘Q's clean exit saves
    """
)


def chrome(tmp_path, mode):
    path = tmp_path / f"chrome-{mode.replace(' ', '-')}"
    path.write_text(f"#!{sys.executable}\n" + FAKE.format(mode=mode, email=EMAIL))
    path.chmod(0o755)
    return str(path)


VERIFY = "https://accounts.google.com/v3/signin/confirmidentifier?continue=https://meet.google.com/"


NOT_SIGNED = "https://workspace.google.com/products/meet/"


@pytest.fixture(autouse=True)
def meet_at(monkeypatch):
    """Where Meet settles for a call's Chrome, faked: for a profile with no
    account, Meet's page for strangers; else each `settled` takes the next
    URL in the list (the last one repeats); its calls in `.asked`. One
    asked to `renew` reaches Meet when `.renews`, as Valor signing itself
    in from 1Password does; each such ask counted in `.renewing`."""

    class At(list):
        asked = renewing = 0
        renews = False

    at = At([meet.MEET_HOME])

    async def settled(profile, renew=False):
        assert meet.holder(profile) is None  # never while a Chrome holds it
        at.asked += 1
        url = NOT_SIGNED if call.account(profile) is None else at.pop(0) if len(at) > 1 else at[0]
        if renew and not meet.on_meet(url):
            at.renewing += 1
            if at.renews:
                return meet.MEET_HOME
        return url

    monkeypatch.setattr(meet, "settled", settled)
    return at


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


def test_sign_in_waits_for_tom_to_quit_chrome_even_once_googles_cookies_are_in(tmp_path, meet_at):
    """The Captain, 2026-11-13: a sign-in ended at the first session cookie
    stopped at Google's passkey step."""
    profile = tmp_path / "meet-chrome"
    code, said = sign_in(profile, chrome(tmp_path, "signs"))
    argv = json.loads((profile / "argv").read_text())
    assert code == 0 and f"signed in as {EMAIL}" in said[-1]
    assert f"--user-data-dir={profile}" in argv and meet.MEET_HOME in argv
    assert (profile / "outlived").exists() and meet.holder(str(profile)) is None
    assert "⌘Q" in said[0] and meet_at.renewing == 1


def test_a_sign_in_valor_passes_itself_from_1password_opens_no_chrome(tmp_path, meet_at):
    for profile in (tmp_path / "never", tmp_path / "lapsed"):
        if profile.name == "lapsed":
            signed_in(profile)
            meet_at[:] = [VERIFY]
        meet_at.renews = True
        code, said = sign_in(profile, chrome(tmp_path, "signs"))
        assert code == 0 and said[-1].startswith("signed in as") and not (profile / "argv").exists()
    assert meet_at.renewing == 2


def test_a_sign_in_saved_only_as_chrome_quits_counts(tmp_path):
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits signed in"))
    assert code == 0 and f"signed in as {EMAIL}" in said[-1]


def test_chrome_quit_without_signing_in_says_so(tmp_path):
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits"))
    assert code == 1 and "not signed in" in said[-1] and "meet-sign-in" in said[-1]


def test_a_profile_already_signed_in_starts_no_chrome(tmp_path):
    signed_in(tmp_path / "p")
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "signs"))
    assert code == 0 and said == [f"signed in as {EMAIL}; {call.settings.machine} can join calls"]
    assert not (tmp_path / "p" / "argv").exists()


def test_a_sign_in_google_wants_verified_waits_for_tom_to_quit_and_checks_meet_again(tmp_path, meet_at):
    """The Cowboy, 2026-11-13: account and cookies in place, Meet at "Verify it's you"."""
    signed_in(tmp_path / "p")
    meet_at[:] = [VERIFY, meet.MEET_HOME]
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits"))  # Tom verifies and quits
    assert json.loads((tmp_path / "p" / "argv").read_text())[-1] == meet.MEET_HOME
    assert "verified again" in said[0] and "⌘Q" in said[0] and meet_at.asked == 2
    assert code == 0 and f"signed in as {EMAIL}" in said[-1]


def test_a_sign_in_still_unverified_when_chrome_quits_says_where_meet_stayed(tmp_path, meet_at):
    signed_in(tmp_path / "p")
    meet_at[:] = [VERIFY]
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits"))
    assert code == 1 and f"Meet stayed on {VERIFY}" in said[-1] and call.SIGN_IN in said[-1]


def test_a_new_sign_in_meet_still_refuses_is_not_signed_in(tmp_path, meet_at):
    meet_at[:] = [VERIFY]
    code, said = sign_in(tmp_path / "p", chrome(tmp_path, "quits signed in"))
    assert code == 1 and "not signed in" in said[-1]


def test_a_profile_open_in_a_sign_in_window_says_sign_in_there_and_leaves_it(tmp_path, meet_at):
    profile = tmp_path / "p"
    with held(profile) as pid:
        code, said = sign_in(profile, chrome(tmp_path, "signs"))
        assert code == 1 and len(said) == 1 and f"pid {pid}" in said[0]
        assert "sign in as valor@yuda.me there" in said[0] and "may be left open" in said[0]
        assert not (profile / "argv").exists() and meet_at.asked == 0
        os.kill(pid, 0)  # still running


def test_a_profile_a_call_holds_is_named_and_left_running(tmp_path, meet_at):
    profile = tmp_path / "p"
    with held(profile, call_=True) as pid:
        code, said = sign_in(profile, chrome(tmp_path, "signs"))
        assert code == 1 and said == [
            f"the call profile is in use by a call's Chrome (pid {pid}); run this again after the call"
        ]
        os.kill(pid, 0)
    assert meet_at.asked == 0


@pytest.mark.parametrize("lock", ["stale", "another profile"])
def test_a_lock_no_chrome_on_this_profile_holds_lets_sign_in_go_ahead(tmp_path, lock):
    profile = tmp_path / "p"
    if lock == "stale":
        profile.mkdir()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        os.symlink(f"Mac.local-{dead.pid}", profile / "SingletonLock")
        code, _ = sign_in(profile, chrome(tmp_path, "quits signed in"))
    else:
        with held(profile, on=tmp_path / "other"):
            code, _ = sign_in(profile, chrome(tmp_path, "quits signed in"))
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


# -- Google's sign-in in place of Meet's join screen (the Cowboy, 2026-11-13) ----------


class Landing:
    """A Playwright page at each of `urls` for one try of the join, the last
    one kept; Meet's join button shown once it is on Meet with `button`."""

    def __init__(self, urls, button=None, refused=False):
        self.urls, self.button, self.gotos, self.tries = list(urls), button, [], 0
        self.refused = refused  # Meet's "You can't join this video call" while on Meet

    @property
    def url(self):
        """The page's URL, read once a try; the next read is the next try's."""
        return self.urls.pop(0) if len(self.urls) > 1 else self.urls[0]

    async def evaluate(self, _expression, _arg=None):
        self.tries += 1
        if not meet.on_meet(self.urls[0]):
            return None
        return meet.CANT_JOIN if self.refused and not self.gotos else self.button

    async def goto(self, url, wait_until):
        self.gotos.append(url)

    def locator(self, _selector):
        return self

    async def count(self):
        return 0


@pytest.fixture
def quick(monkeypatch):
    sleep = asyncio.sleep
    monkeypatch.setattr(meet.asyncio, "sleep", lambda _s: sleep(0))


def test_a_join_that_stays_on_googles_sign_in_says_so_at_once(quick):
    page = meet.Page("unused")
    page.page = Landing([VERIFY])
    with pytest.raises(meet.SignInNeeded, match="confirmidentifier"):
        asyncio.run(page.join(timeout_s=60))
    assert page.page.tries == meet.SETTLE_TRIES


def test_a_join_through_googles_passing_redirects_still_joins(quick):
    passing = (
        "https://accounts.google.com/v3/signin/identifier?passive=1209600&continue=https://meet.google.com/"
    )
    page = meet.Page("unused")
    page.page = Landing(["https://accounts.google.com/ServiceLogin?passive=true", "https://accounts.google.com/CheckCookie",
                         *[passing] * (meet.SETTLE_TRIES - 1), URL], button="Join now")  # fmt: skip
    assert asyncio.run(page.join()) == "Join now"


def test_a_join_with_no_button_names_the_page_it_ended_on(quick):
    page = meet.Page("unused")
    page.page = Landing([URL])
    with pytest.raises(TimeoutError, match=f"no join button at {URL}"):
        asyncio.run(page.join(timeout_s=2))


@pytest.mark.parametrize(("urls", "at"), [([meet.MEET_HOME, "https://meet.google.com/landing"], "https://meet.google.com/landing"),
                                          (["https://accounts.google.com/ServiceLogin", VERIFY], VERIFY)])  # fmt: skip
def test_where_meet_settles_for_a_calls_chrome(monkeypatch, quick, urls, at):
    pages = []

    async def launch(self):
        self.page = Landing(urls)
        pages.append(self.page)

    async def close(self):
        pages.append("closed")

    monkeypatch.undo()  # the autouse fake of `settled`, not the one under test
    monkeypatch.setattr(meet.asyncio, "sleep", lambda _s, sleep=asyncio.sleep: sleep(0))
    monkeypatch.setattr(meet.Page, "launch", launch)
    monkeypatch.setattr(meet.Page, "close", close)
    assert asyncio.run(meet.settled("unused")) == at
    assert pages[0].gotos == [meet.MEET_HOME] and pages[-1] == "closed"


# -- Meet's "You can't join this video call" (the Captain, 2026-11-13) -----------------

SERVICE_LOGIN = "https://accounts.google.com/ServiceLogin?continue="


def test_meets_refusal_with_googles_sign_in_waiting_is_a_sign_in_on_the_calls_own_page(quick):
    """A profile Google signed out: its sign-in for the meeting stays put."""
    page = meet.Page("unused")
    page.page = Landing([URL, URL, VERIFY], refused=True)
    with pytest.raises(meet.SignInNeeded, match="confirmidentifier"):
        asyncio.run(page.join())
    assert page.page.gotos == [SERVICE_LOGIN + "https%3A%2F%2Fmeet.google.com%2Fabc-defg-hij"]


def test_meets_refusal_with_google_letting_valor_through_is_meets_own(quick):
    passing = "https://accounts.google.com/v3/signin/identifier?passive=1"
    page = meet.Page("unused")
    page.page = Landing([URL, URL, "https://accounts.google.com/CheckCookie",
                         *[passing] * (meet.SETTLE_TRIES - 1), URL], refused=True)  # fmt: skip
    with pytest.raises(meet.Refused, match="abc-defg-hij"):
        asyncio.run(page.join())


@pytest.mark.parametrize("renews", [True, False])
def test_settling_with_renew_signs_in_on_the_page_it_opened_only_off_meet(monkeypatch, quick, renews):
    """`meet-sign-in`'s probe: Valor's own sign-in runs on the headless
    page `settled` launched, and only when Meet did not open."""
    renewed, pages = [], []

    async def launch(self):
        self.page = Landing([VERIFY])
        pages.append(self)

    async def renew(self, back):
        renewed.append((self, back))
        if renews:
            self.page.urls = [meet.MEET_HOME]
        return renews

    async def close(self):
        pass

    monkeypatch.undo()  # the autouse fake of `settled`, not the one under test
    monkeypatch.setattr(meet.asyncio, "sleep", lambda _s, sleep=asyncio.sleep: sleep(0))
    for name, fake in (("launch", launch), ("renew", renew), ("close", close)):
        monkeypatch.setattr(meet.Page, name, fake)
    assert asyncio.run(meet.settled("unused", renew=True)) == (meet.MEET_HOME if renews else VERIFY)
    assert renewed == [(pages[0], meet.MEET_HOME)]
    renewed.clear()

    async def at_meet(self):
        self.page = Landing([meet.MEET_HOME])

    monkeypatch.setattr(meet.Page, "launch", at_meet)
    assert asyncio.run(meet.settled("unused", renew=True)) == meet.MEET_HOME and renewed == []


# -- Valor's own sign-in from 1Password (bridges/meet/google.py) ----------------------

ACC = "https://accounts.google.com/v3/signin"


class Google:
    """Google's sign-in pages in `flow`, each answered moving to the next,
    but `stuck`, which stays; what was done to them in `done`."""

    def __init__(self, flow, stuck=None):
        self.flow, self.stuck, self._url, self.done = flow, stuck, None, []
        self.keyboard = self

    @property
    def url(self):
        """Google's own redirects pass by themselves, a read after."""
        at = self._url
        if at is not None and "SetSID" in at:
            self._next()
        return at

    @url.setter
    def url(self, value):
        self._url = value

    async def goto(self, url, wait_until):
        self.done.append(("goto", url))
        self.url = self.flow[0]

    def _next(self):
        if self._url != self.stuck:
            self._url = self.flow[self.flow.index(self._url) + 1]

    async def click(self, selector, timeout):
        self.done.append(("click", selector))
        self._next()

    async def fill(self, selector, value, timeout):
        self.done.append(("fill", selector, value))

    async def press(self, key):
        self.done.append(("press", key))
        self._next()


@pytest.fixture
def login(monkeypatch, quick):
    from bridges.meet import google

    monkeypatch.setattr(google.asyncio, "sleep", lambda _s, sleep=asyncio.sleep: sleep(0))
    monkeypatch.setattr(google, "secret", {"password": "pw-1", "otp": "123456"}.get)
    return google


FLOW = [f"{ACC}/confirmidentifier?continue=x", f"{ACC}/challenge/pk?x", f"{ACC}/challenge/selection?n=1",
        f"{ACC}/challenge/pwd?x", f"{ACC}/challenge/selection?n=2", f"{ACC}/challenge/totp?x",
        "https://accounts.google.co.th/accounts/SetSID?x", URL]  # fmt: skip


def test_valor_passes_verify_its_you_with_its_password_then_its_code(login, capsys):
    page = Google(FLOW)
    assert asyncio.run(login.renew(page, SERVICE_LOGIN + "x")) is True
    assert page.done == [
        ("goto", SERVICE_LOGIN + "x"), ("click", 'button:has-text("Next")'),
        ("click", 'button:has-text("Try another way")'), ("click", "text=/Enter your password/i"),
        ("fill", login.PASSWORD_FIELD, "pw-1"), ("press", "Enter"), ("click", "text=/Authenticator/i"),
        ("fill", login.CODE_FIELD, "123456"), ("press", "Enter"),
    ]  # fmt: skip
    out = capsys.readouterr()
    assert "pw-1" not in out.out + out.err and "123456" not in out.out + out.err


def test_a_never_signed_in_profile_gives_the_email_first(login):
    page = Google([f"{ACC}/identifier?x", f"{ACC}/challenge/pwd?x", f"{ACC}/challenge/totp?x", URL])
    assert asyncio.run(login.renew(page, SERVICE_LOGIN + "x")) is True
    assert ("fill", login.EMAIL_FIELD, login.EMAIL) in page.done


def test_a_code_google_refuses_ends_the_sign_in(login):
    page = Google(FLOW, stuck=FLOW[5])
    assert asyncio.run(login.renew(page, SERVICE_LOGIN + "x")) is False
    assert page.done.count(("fill", login.CODE_FIELD, "123456")) == 1


def test_without_the_login_in_1password_nothing_touches_the_page(login, monkeypatch):
    monkeypatch.setattr(login, "secret", lambda field: None)
    page = Google(FLOW)
    assert asyncio.run(login.renew(page, SERVICE_LOGIN + "x")) is False and page.done == []
    meet_page = meet.Page("unused")
    meet_page.page = page
    assert asyncio.run(meet_page.renew(URL)) is False and page.done == []


def test_the_login_is_read_from_1password_by_the_items_name(monkeypatch):
    from bridges.meet import google
    from core import credentials

    asked = []
    monkeypatch.undo()  # the autouse fake of `secret`, not the one under test
    monkeypatch.setattr(credentials, "op", lambda args, vault_env: asked.append(args) or "v")
    assert google.secret("password") == google.secret("otp") == "v"
    assert asked == [
        ["item", "get", "Google valor@yuda.me", "--vault", "m-valor", "--fields", "label=password", "--reveal"],
        ["item", "get", "Google valor@yuda.me", "--vault", "m-valor", "--otp"],
    ]


def test_a_renewed_page_goes_back_to_the_meeting(login):
    meet_page = meet.Page("unused")
    meet_page.page = Google([f"{ACC}/challenge/pwd?x", f"{ACC}/challenge/totp?x", URL])
    assert asyncio.run(meet_page.renew(URL)) is True
    assert meet_page.page.done[0] == ("goto", SERVICE_LOGIN + "https%3A%2F%2Fmeet.google.com%2Fabc-defg-hij")
    assert meet_page.page.done[-1] == ("goto", URL)


def test_a_media_connections_state_from_the_page_is_written_to_the_calls_log(capsys):
    """A call that saw who was in it but heard no one (2026-11-05) left no
    trace of Meet's media connection: the page reports each state change."""
    socket = meet.Socket(lambda *a: None, lambda *a: None, lambda *a: None)
    socket.read({"type": "ValorMedia", "connection": 2, "state": "failed"})
    assert capsys.readouterr().err == "media connection 2: failed\n"
