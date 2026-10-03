"""`look`, the workspace's headless browser: real Chromium, real sandbox-exec
profiles, no model. A tiny local server stands in for a workspace's dev
server; the profile is told its port as a service port, since the dev ports
may be in use on a shared machine.

`test_memory` runs only with `VALOR_MEASURE=1` and prints the peak resident
memory of the browser's process tree.
"""

import asyncio
import http.server
import json
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from core import db, ledger, signals
from core import workspace as kws
from core.settings import settings
from tests import scripted
from tests.test_pipeline import drive

pytestmark = pytest.mark.spend(usd=0)

LOOK = Path(__file__).resolve().parent.parent / "tools" / "look"
PORTS = range(6451, 6460)

PAGE = b"<html><head><title>t</title></head><body><h1>hello look</h1><p id=late></p></body></html>"
LATE = (
    b"<html><body><p id=late></p>"
    b"<script>setTimeout(function(){document.getElementById('late').textContent='arrived late'},1000)"
    b"</script></body></html>"
)

needs_browser = pytest.mark.skipif(
    not Path(settings.browser).exists(), reason="the Playwright headless shell is not installed"
)


class Server:
    def __init__(self, status=200):
        pages = {"/": PAGE, "/late": LATE}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = pages.get(self.path, PAGE)
                self.send_response(status)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        for port in PORTS:
            try:
                self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        else:
            pytest.skip("no port from 6451 to 6459 is free")
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def free_port() -> int:
    for port in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    pytest.skip("no port from 6451 to 6459 is free")


def provision(tmp_path):
    src = scripted.toy_repo(tmp_path)
    made = kws.provision(
        ledger.new_id(),
        kws.Spec.from_dict({"name": "toy", "repo": str(src), "kind": "plain", "suite": "true"}),
        {},
        work=tmp_path / "work",
    )
    return kws.Layout(Path(made.mirror).parent), made


def look_under(profile: Path, cwd: Path, tmpdir: Path, bin_look: Path, *args, browser=None):
    env = {
        "HOME": os.environ["HOME"],
        "PATH": f"{bin_look.parent}:/usr/bin:/bin",
        "TMPDIR": str(tmpdir),
        "VALOR_BROWSER": browser if browser is not None else settings.browser,
    }
    argv = kws.sandboxed(profile, "look-test", str(bin_look), *args)
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False)


def turn_profile(lay, tmp_path, port) -> Path:
    path = lay.profiles / "look-turn.sb"
    path.write_text(kws.turn_profile(lay, [port]))
    return path


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


@needs_browser
def test_look_under_the_turn_profile_writes_a_png_and_the_page(tmp_path):
    lay, _ = provision(tmp_path)
    with Server() as server:
        profile = turn_profile(lay, tmp_path, server.port)
        done = look_under(
            profile, lay.repo, lay.work_state / "tmp", lay.root.parent / "bin" / "look",
            server.url + "/", "home", "--size", "900x600",
        )  # fmt: skip
    assert done.returncode == 0, done.stderr
    shots = lay.repo / ".valor" / "screens"
    assert png_size(shots / "home.png") == (900, 600)
    assert "hello look" in (shots / "home.html").read_text()
    assert ".valor/screens/home.png" in done.stdout and "status 200" in done.stdout


@needs_browser
def test_a_port_with_nothing_listening_exits_non_zero_and_says_so(tmp_path):
    lay, _ = provision(tmp_path)
    port = free_port()
    profile = turn_profile(lay, tmp_path, port)
    done = look_under(
        profile,
        lay.repo,
        lay.work_state / "tmp",
        lay.root.parent / "bin" / "look",
        f"http://127.0.0.1:{port}/",
    )
    assert done.returncode != 0
    assert "nothing answered" in done.stderr
    assert not (lay.repo / ".valor" / "screens").exists()


@needs_browser
def test_a_server_error_is_rendered_and_kept_and_exits_non_zero(tmp_path):
    lay, _ = provision(tmp_path)
    with Server(status=500) as server:
        profile = turn_profile(lay, tmp_path, server.port)
        done = look_under(
            profile, lay.repo, lay.work_state / "tmp", lay.root.parent / "bin" / "look", server.url + "/"
        )
    assert done.returncode != 0
    assert "status 500" in done.stdout
    assert (
        "hello look"
        in (
            lay.repo
            / ".valor"
            / "screens"
            / next(p.name for p in (lay.repo / ".valor" / "screens").glob("*.html"))
        ).read_text()
    )
    assert list((lay.repo / ".valor" / "screens").glob("*.png"))


@needs_browser
def test_text_set_after_a_timer_is_in_the_page_with_a_long_enough_wait(tmp_path):
    lay, _ = provision(tmp_path)
    with Server() as server:
        profile = turn_profile(lay, tmp_path, server.port)
        done = look_under(
            profile, lay.repo, lay.work_state / "tmp", lay.root.parent / "bin" / "look",
            server.url + "/late", "late", "--wait", "2500",
        )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "arrived late" in (lay.repo / ".valor" / "screens" / "late.html").read_text()


@pytest.mark.parametrize("name", ["a/b", "../x", "..", ".hidden", "x/../y"])
def test_a_name_that_is_not_a_plain_file_name_is_refused(tmp_path, name):
    cwd = tmp_path / "clone"
    cwd.mkdir()
    done = subprocess.run(
        [str(LOOK), "http://127.0.0.1:1/", name],
        cwd=cwd, env={"PATH": "/usr/bin:/bin", "VALOR_BROWSER": "/bin/true"},
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode != 0 and "plain file name" in done.stderr
    assert not (cwd / ".valor").exists()
    assert not (tmp_path / "x").exists()


@needs_browser
def test_look_under_the_fresh_sessions_profile_runs_with_its_own_tmp(tmp_path):
    lay, _ = provision(tmp_path)
    with Server() as server:
        check_dir = kws.fresh_dir(lay.checks / "look")
        harness = kws.check_harness(lay, check_dir, [server.port], {"PATH": "/usr/bin:/bin"}, services=True)
        (check_dir / "checkout").mkdir()
        subprocess.run(["git", "init", "-q", str(check_dir / "checkout")], check=True)
        done = look_under(
            Path(harness["sandbox_profile"]), check_dir / "checkout", Path(harness["tmpdir"]),
            lay.root.parent / "bin" / "look", server.url + "/", "fresh",
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert (check_dir / "checkout" / ".valor" / "screens" / "fresh.png").is_file()
        # Chromium's own cache directory resolves under /private/var/folders,
        # which the fresh profile denies; the run still works without it.
        assert "/private/var/folders" not in done.stderr
        assert not list(Path(harness["tmpdir"]).glob("look-profile.*"))  # cleaned up


def test_a_missing_browser_exits_non_zero_with_the_reason(tmp_path):
    lay, _ = provision(tmp_path)
    profile = turn_profile(lay, tmp_path, 6451)
    done = look_under(
        profile, lay.repo, lay.work_state / "tmp", lay.root.parent / "bin" / "look",
        "http://127.0.0.1:6451/", browser=str(tmp_path / "nowhere" / "chrome"),
    )  # fmt: skip
    assert done.returncode != 0
    assert "missing or not executable" in done.stderr


def test_provisioning_writes_look_into_the_shared_bin_and_hands_it_the_browser(tmp_path):
    lay, made = provision(tmp_path)
    bin_look = lay.root.parent / "bin" / "look"
    assert bin_look.read_bytes() == LOOK.read_bytes()
    assert os.access(bin_look, os.X_OK)
    assert not list((lay.root.parent / "bin").glob(".*.tmp"))
    harness = json.loads(json.dumps(made.harness))
    assert harness["env"]["VALOR_BROWSER"] == settings.browser
    assert harness["env"]["PATH"].startswith(str(lay.root.parent / "bin") + ":")


def test_the_browser_is_the_fixed_build_and_a_turn_cannot_write_its_cache(tmp_path):
    assert "chromium_headless_shell-1208" in settings.browser
    assert "newest" not in settings.browser
    lay, _ = provision(tmp_path)
    home = tmp_path / "home"
    cache = home / "Library" / "Caches" / "ms-playwright" / "chromium_headless_shell-9999"
    cache.mkdir(parents=True)
    profile = lay.profiles / "probe.sb"
    profile.write_text(kws.turn_profile(lay, [], home=home))
    done = subprocess.run(
        kws.sandboxed(profile, "probe", "/bin/sh", "-c",
                      f"ls {cache.parent} >/dev/null && echo read; "
                      f"touch {cache}/chrome 2>/dev/null && echo wrote || echo denied"),
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.stdout.split() == ["read", "denied"], done.stderr


# -- what the kernel records ----------------------------------------------------------------


def plant(root: Path) -> Path:
    screens = root / ".valor" / "screens"
    screens.mkdir(parents=True)
    return screens


def test_screens_are_recorded_with_their_size_and_moved_aside(tmp_path):
    screens = plant(tmp_path)
    (screens / "a.png").write_bytes(b"png bytes")
    (screens / "a.html").write_text("<p>a</p>")
    found = signals.collect(tmp_path, "t1")
    by_name = {e["name"]: e for e in found.screens}
    assert by_name["a.png"] == {"name": "a.png", "bytes": 9}
    assert by_name["a.html"] == {"name": "a.html", "bytes": 8}
    assert set(by_name) == {"a.png", "a.html"}
    assert list(screens.iterdir()) == []
    assert (tmp_path / ".valor" / "handled" / "t1" / "screens" / "a.png").read_bytes() == b"png bytes"
    assert signals.collect(tmp_path, "t2").screens == []  # a later turn records none of them


def test_a_screen_that_cannot_be_moved_aside_is_refused_and_removed(tmp_path):
    screens = plant(tmp_path)
    (screens / "a.png").write_bytes(b"png")
    (tmp_path / ".valor" / "handled" / "t1" / "screens" / "a.png").mkdir(parents=True)  # blocks the move
    found = signals.collect(tmp_path, "t1")
    assert found.screens == [{"name": "a.png", "refused": "could not be moved aside"}]
    assert not (screens / "a.png").exists()


def test_an_absent_or_empty_screens_directory_records_nothing(tmp_path):
    assert signals.collect(tmp_path, "t").screens == []
    plant(tmp_path)
    assert signals.collect(tmp_path, "t").screens == []


def test_a_link_a_hard_link_a_fifo_and_a_directory_are_refused_unread(tmp_path):
    outside = tmp_path / "outside-secret"
    outside.write_text("secret")
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "tracked.txt").write_text("tracked")
    screens = plant(clone)
    os.symlink(outside, screens / "link.png")
    os.link(clone / "tracked.txt", screens / "hard.png")
    os.mkfifo(screens / "pipe.png")
    (screens / "dir.png").mkdir()
    (screens / "ok.png").write_bytes(b"fine")
    started = time.monotonic()
    found = signals.collect(clone, "t1")
    assert time.monotonic() - started < 5  # the FIFO did not block the read
    by_name = {e["name"]: e for e in found.screens}
    for name in ("link.png", "hard.png", "pipe.png", "dir.png"):
        assert set(by_name[name]) == {"name", "refused"}, by_name[name]
    assert by_name["ok.png"]["bytes"] == 4
    assert outside.read_text() == "secret"
    assert "secret" not in json.dumps(found.screens)


def test_a_sparse_screen_is_sized_without_being_read(tmp_path):
    screens = plant(tmp_path)
    with open(screens / "huge.png", "wb") as f:
        f.truncate(1 << 50)
    started = time.monotonic()
    found = signals.collect(tmp_path, "t1")
    assert time.monotonic() - started < 5
    assert found.screens == [{"name": "huge.png", "bytes": 1 << 50}]


def test_a_screens_directory_that_is_a_link_is_not_followed(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.png").write_bytes(b"x")
    clone = tmp_path / "clone"
    (clone / ".valor").mkdir(parents=True)
    os.symlink(outside, clone / ".valor" / "screens")
    assert signals.collect(clone, "t").screens == []
    assert (outside / "x.png").exists()


@needs_browser
def test_a_scripted_turn_that_runs_look_has_its_screen_on_turn_collected(dsn, tmp_path):
    ws, _origin = scripted.workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("outside the clone")
    with Server() as server:
        scripted.steer(
            ws,
            run=[
                f"VALOR_BROWSER={settings.browser} {LOOK} {server.url}/ shot",
                f"ln -s {outside} .valor/screens/escape.png",
            ],
        )

        async def go():
            task = await scripted.start(dsn, ws)
            await drive(dsn, task)
            await scripted.critique(dsn, task)
            await drive(dsn, task)
            async with await db.connect(dsn) as conn:
                return await ledger.read(conn, task)

        events = asyncio.run(go())
    builds = [
        e["payload"] for e in events if e["type"] == "turn.collected" and e["payload"]["state"] == "build"
    ]
    assert builds, [e["type"] for e in events]
    screens = {s["name"]: s for s in builds[0]["screens"]}
    handled = next((ws / ".valor" / "handled").glob("*/screens/shot.png"))
    assert screens["shot.png"]["bytes"] == handled.stat().st_size
    assert set(screens["escape.png"]) == {"name", "refused"}


# -- memory ----------------------------------------------------------------------------------


def tree_rss_kb(root_pid: int) -> int:
    out = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, check=False
    ).stdout
    rows = [tuple(int(x) for x in line.split()) for line in out.splitlines() if line.strip()]
    children: dict[int, list[int]] = {}
    rss = {}
    for pid, ppid, kb in rows:
        children.setdefault(ppid, []).append(pid)
        rss[pid] = kb
    total, stack = 0, [root_pid]
    while stack:
        pid = stack.pop()
        total += rss.get(pid, 0)
        stack.extend(children.get(pid, []))
    return total


@needs_browser
@pytest.mark.skipif(os.environ.get("VALOR_MEASURE") != "1", reason="a measurement, run with VALOR_MEASURE=1")
def test_memory(tmp_path):
    """Peak resident memory of the browser's whole process tree over five
    renders of a page (the admin login page of a Django app when
    `VALOR_MEASURE_URL` names one, else a stand-in page), sampled every 50 ms."""
    url = os.environ.get("VALOR_MEASURE_URL")
    peaks = []
    with Server() as server:
        for i in range(5):
            cwd = tmp_path / f"run{i}"
            cwd.mkdir()
            env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path),
                   "VALOR_BROWSER": settings.browser}  # fmt: skip
            proc = subprocess.Popen([str(LOOK), url or server.url + "/"], cwd=cwd, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
            peak = 0
            while proc.poll() is None:
                peak = max(peak, tree_rss_kb(proc.pid))
                time.sleep(0.05)
            assert proc.returncode == 0
            peaks.append(peak / 1024)
    print(f"peak resident MB over five renders: {[round(p) for p in peaks]}; max {round(max(peaks))}")
    assert max(peaks) > 0


# -- live: a real build turn ----------------------------------------------------------------

DJANGO_FILES = {
    "pyproject.toml": '[project]\nname = "site"\nversion = "0"\nrequires-python = ">=3.10"\n'
    'dependencies = ["django>=5,<6"]\n[tool.uv]\npackage = false\n',
    "manage.py": "import os, sys\nfrom django.core.management import execute_from_command_line\n"
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')\n"
    "execute_from_command_line(sys.argv)\n",
    "mysite/__init__.py": "",
    "mysite/settings.py": "from pathlib import Path\nBASE_DIR = Path(__file__).resolve().parent.parent\n"
    "SECRET_KEY = 'x'\nDEBUG = True\nALLOWED_HOSTS = ['*']\nROOT_URLCONF = 'mysite.urls'\n"
    "INSTALLED_APPS = []\nMIDDLEWARE = []\n"
    "TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',"
    " 'DIRS': [BASE_DIR / 'templates']}]\n",
    "mysite/urls.py": "from django.urls import path\nfrom django.views.generic import TemplateView\n"
    "urlpatterns = [path('', TemplateView.as_view(template_name='home.html'))]\n",
    "templates/home.html": "<html><body><h1>Hello</h1></body></html>\n",
}


@pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1")
@pytest.mark.spend(usd=1.00)
def test_a_live_build_turn_opens_its_page_and_names_the_screenshot(dsn, tmp_path):
    """A provisioned Django workspace whose plan is a one-line template change:
    the turn serves the app on a dev port, runs `look`, and names the
    screenshot in `done.md`; `turn.collected` holds its name and size."""
    from tests.test_live_session import core, sh

    root = tmp_path.resolve()
    src = root / "src" / "toy"
    sh("git", "init", "-q", "-b", "main", str(src))
    for name, text in DJANGO_FILES.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(text)
    git = ["git", "-c", "user.name=Tom", "-c", "user.email=tom@example.com"]
    sh(*git, "add", ".", cwd=src)
    sh(*git, "commit", "-qm", "base", cwd=src)
    spec = root / "toy.toml"
    spec.write_text(
        f'name = "toy"\nrepo = "{src}"\nkind = "django"\nsuite = "true"\ntarget_branch = "main"\n'
        'setup = ["uv sync"]\nmax_output_tokens = 2048\n'
    )
    task = core(
        "start",
        "Change the heading in templates/home.html to 'Hello, Tom' and commit it. The change is only "
        "checked by looking at the page: when you build, start the app with `uv run python manage.py "
        "runserver 127.0.0.1:8003 --noreload` in the background, run `look` on http://127.0.0.1:8003/, "
        "open the screenshot, stop the server, and name the screenshot in .valor/done.md with what "
        "you saw. The plan stage only writes the plan.",
        "--ceiling", "act", "--project", str(spec), "--model", "light",
    )  # fmt: skip
    core("run", task)

    async def collected():
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
        return [
            r["payload"] for r in rows if r["type"] == "turn.collected" and r["payload"]["state"] == "build"
        ]

    builds = asyncio.run(collected())
    assert builds, "no build turn was collected"
    shots = [s for b in builds for s in b["screens"] if s["name"].endswith(".png")]
    assert shots and all(s["bytes"] > 0 for s in shots), [
        {k: b.get(k) for k in ("state", "verdict", "question", "errors", "done")} for b in builds
    ]
    assert any(s["name"] in (b["done"] or "") for b in builds for s in b["screens"])
