"""Valor.app, and the macOS privacy grants asked for once, at the Mac.

macOS keys a privacy (TCC) grant to a launchd job's executable, and for a
bare binary to its resolved path. Every job ran the Homebrew Python, whose
path changes with each upgrade, so each upgrade asked again for every
grant: thirteen rows for `python@3.14/3.14.8_1` alone on one Mac
(2026-10-21), some denied because nobody was there. Every job now runs
its command through Valor.app's `valor-run` (`settings.launcher`,
`valor_run.c`), which is built once and never rebuilt, since a rebuild
changes its code hash and drops every grant made to it.

`python -m core permissions`, run once at the Mac, builds Valor.app when
it is missing and runs `probe` inside it through LaunchServices, so Valor,
not the terminal, is the one asking. The grants are the ones the kernel's
Python was granted before. Full Disk Access covers its folder, volume,
iCloud and app-data rows, so only four are asked for.

Every update checks this Mac's setup (`ensure`): it builds Valor.app when
it is missing, puts the launcher first in every `com.valor.*` job that
still runs a bare Python, and reloads those jobs, the kernel's own last,
from a one-shot job of its own, since a job cannot reload itself. Any
other `com.valor.*` job whose plist is in place but is not loaded is
loaded: a turn's sandbox cannot bootstrap one (the Cowboy's
`com.valor.brew-nightly`, put back by a turn on 2026-10-28), and launchd
would otherwise read it only at the next login.

It also makes sure Slack is served where this Mac's projects list a
Slack chat (`slack`, Tom, 2026-10-25): `python -m bridges.slack keys`
copies this Mac's own app's tokens from the vault (none: `slack: no keys`,
and nothing is started), a missing `com.valor.kernel.slack` job is written
from `python -m bridges.slack --plist` with the settings of this Mac's
Telegram job, and loaded, and a job whose keys changed or that is not
loaded is enabled and (re)loaded. Each
Mac has its own app, so this never starts a second bridge on one token.
"""

import ctypes
import os
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from core.settings import settings

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ID = "com.valor.app"
DATABASES = (
    Path("/Library/Application Support/com.apple.TCC/TCC.db"),
    Path.home() / "Library/Application Support/com.apple.TCC/TCC.db",
)
# (service, Automation target or "UNUSED", the Settings pane, its name there)
GRANTS = (
    ("kTCCServiceSystemPolicyAllFiles", "UNUSED", "Privacy_AllFiles", "Full Disk Access"),
    ("kTCCServiceSystemPolicyAppBundles", "UNUSED", "Privacy_AppBundles", "App Management"),
    ("kTCCServiceScreenCapture", "UNUSED", "Privacy_ScreenCapture", "Screen & System Audio Recording"),
    ("kTCCServiceAppleEvents", "com.apple.MobileSMS", "Privacy_Automation", "Automation: Messages"),
)
WAIT_S = 900
AGENTS = Path.home() / "Library/LaunchAgents"
SLACK = "com.valor.kernel.slack"  # `bridges/slack/__main__.py`'s LABEL
TELEGRAM = "com.valor.kernel.telegram"


def build(exe: Path) -> None:
    """Valor.app around `exe`, ad-hoc signed as `BUNDLE_ID`, when `exe` is missing."""
    if exe.exists():
        return
    app = exe.parents[2]
    exe.parent.mkdir(parents=True)
    subprocess.run(["clang", "-O2", "-o", exe, Path(__file__).with_name("valor_run.c")], check=True)
    info = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "Valor",
        "CFBundleExecutable": exe.name,
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "-s", "-", "-f", "-i", BUNDLE_ID, app], check=True, capture_output=True)


def ensure(
    own: str,
    agents: Path = AGENTS,
    exe: Path | None = None,
    reload: Callable[[str, Path, bool], str | None] | None = None,
    loaded: Callable[[str], bool] | None = None,
) -> dict[str, list[str]]:
    """Every update's check of this Mac's setup (module docstring). `own` is
    the label of the job running this, reloaded by a one-shot job of its
    own. Returns `fixed`, each job put on the launcher, and `failed`, each
    job not reloaded with why; empty ones left out."""
    exe = exe or Path(settings.launcher)
    reload = reload or _reload
    loaded = loaded or _loaded
    fixed, failed = [], []
    if not exe.exists():
        build(exe)
        fixed.append(exe.parents[2].name)
    slack(agents, reload, fixed, failed)
    jobs = sorted(agents.glob("com.valor.*.plist"), key=lambda p: p.stem == own)
    for plist in jobs:
        job = plistlib.loads(plist.read_bytes())
        argv = job.get("ProgramArguments", [])
        if len(argv) >= 2 and Path(argv[0]).name.startswith("python") and argv[1] == "-m":
            job["ProgramArguments"] = [str(exe), *argv]
            plist.write_bytes(plistlib.dumps(job))
            fixed.append(plist.stem)
        elif loaded(plist.stem):
            continue
        else:
            fixed.append(f"{plist.stem}: not loaded")
        if why := reload(plist.stem, plist, plist.stem == own):
            failed.append(f"{plist.stem}: {why}")
    return {k: v for k, v in (("fixed", fixed), ("failed", failed)) if v}


def slack(
    agents: Path,
    reload: Callable[[str, Path, bool], str | None],
    fixed: list[str],
    failed: list[str],
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """The Slack bridge served on this Mac when a spec placed here lists a
    Slack chat (module docstring); each change or failure appended to
    `fixed` or `failed`."""
    from core import workspace

    if not any(c.startswith("slack:") for spec in workspace.Spec.here() for c in spec.chats):
        return
    plist = agents / f"{SLACK}.plist"
    telegram = agents / f"{TELEGRAM}.plist"
    env = dict(os.environ)
    if telegram.exists():
        job = plistlib.loads(telegram.read_bytes()).get("EnvironmentVariables", {})
        env |= {k: v for k, v in job.items() if k not in ("PATH", "HOME")}
    keys = run([sys.executable, "-m", "bridges.slack", "keys"], cwd=ROOT, env=env, capture_output=True, text=True)
    if keys.returncode != 0:
        why = (keys.stderr.strip() or keys.stdout.strip()).splitlines()[-1:] or [f"status {keys.returncode}"]
        failed.append(f"slack: no keys ({why[0]})")
        return
    target = f"gui/{os.getuid()}/{SLACK}"
    if plist.exists():
        if ": written" in keys.stdout:
            fixed.append(f"{SLACK}: new keys")
        elif run(["launchctl", "print", target], capture_output=True).returncode == 0:
            return
        else:
            fixed.append(f"{SLACK}: not loaded")
    else:
        made = run([sys.executable, "-m", "bridges.slack", "--plist"], cwd=ROOT, env=env, capture_output=True)
        if made.returncode != 0:
            failed.append(f"{SLACK}: --plist exited with status {made.returncode}")
            return
        plist.write_bytes(made.stdout)
        fixed.append(SLACK)
    # A disabled job's bootstrap fails (the Pirate's, 2026-10-16 to 10-27).
    run(["launchctl", "enable", target], capture_output=True)
    if why := reload(SLACK, plist, False):
        failed.append(f"{SLACK}: {why}")


def _loaded(label: str) -> bool:
    done = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, check=False)
    return done.returncode == 0


def _reload(label: str, plist: Path, own: bool) -> str | None:
    """launchd reads a plist only at bootstrap. The bootout returns before
    the job's process has exited, so the bootstrap is tried until it takes."""
    domain = f"gui/{os.getuid()}"
    if own:
        helper = Path(tempfile.gettempdir()) / f"{label}.reload.plist"
        script = (
            f"launchctl bootout {domain}/{label}; "
            f"until launchctl bootstrap {domain} '{plist}'; do sleep 1; done; "
            f"launchctl bootout {domain}/{label}.reload"
        )
        job = {"Label": f"{label}.reload", "ProgramArguments": ["/bin/sh", "-c", script], "RunAtLoad": True}
        helper.write_bytes(plistlib.dumps(job))
        argv = [["launchctl", "bootstrap", domain, str(helper)]]
    else:
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, check=False)
        argv = [["launchctl", "bootstrap", domain, str(plist)]] * 20
    for a in argv:
        done = subprocess.run(a, capture_output=True, text=True, check=False)
        if done.returncode == 0:
            return None
        time.sleep(0.5)
    return done.stderr.strip() or f"launchctl exited with status {done.returncode}"


def granted() -> dict[str, bool | None]:
    """Each grant's name: allowed, denied, or None when never decided.
    Reading the databases needs Full Disk Access."""
    found: dict[tuple[str, str], int] = {}
    for db in DATABASES:
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                rows = conn.execute(
                    "SELECT service, indirect_object_identifier, auth_value FROM access WHERE client = ?",
                    (BUNDLE_ID,),
                ).fetchall()
        except sqlite3.OperationalError:
            continue  # absent on this Mac, or Full Disk Access not yet on
        found |= {(s, target): value for s, target, value in rows}
    return {
        name: None if (service, target) not in found else found[service, target] == 2
        for service, target, _, name in GRANTS
    }


def _pane(anchor: str) -> None:
    subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?{anchor}"], check=False)


def probe() -> int:
    """Inside Valor.app: wait for Full Disk Access, ask for what has a
    prompt, open the pane of what has none, and report each grant."""
    deadline = time.monotonic() + WAIT_S
    _pane("Privacy_AllFiles")
    while not _readable(DATABASES[0]) and time.monotonic() < deadline:
        time.sleep(2)
    if not _readable(DATABASES[0]):
        print("Full Disk Access: not granted")
        return 1
    ctypes.CDLL(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    ).CGRequestScreenCaptureAccess()
    subprocess.run(
        ["osascript", "-e", 'tell application "Messages" to count of accounts'],
        capture_output=True,
        check=False,
    )
    _pane("Privacy_AppBundles")
    while not all(granted().values()) and time.monotonic() < deadline:
        time.sleep(2)
    state = granted()
    for name, ok in state.items():
        print(f"{name}: {'allowed' if ok else 'denied' if ok is False else 'not granted'}")
    return 0 if all(state.values()) else 1


def _readable(path: Path) -> bool:
    try:
        path.open("rb").close()
    except PermissionError:
        return False
    return True


def run() -> int:
    """Build Valor.app if missing, then run `probe` inside it and print its report."""
    exe = Path(settings.launcher)
    build(exe)
    print(
        f"At this Mac, in System Settings > Privacy & Security, for {exe.parents[2]}:\n"
        "  Full Disk Access and App Management: + then the app, or turn on Valor\n"
        "  Screen Recording and Messages: allow when asked\n"
        f"Waiting up to {WAIT_S // 60} minutes...",
        flush=True,
    )
    with tempfile.NamedTemporaryFile("r", suffix=".log") as out:
        script = 'cd "$0" && "$1" -m core permissions --probe; echo "exit $?"'
        subprocess.run(
            ["open", "-W", "-n", "-a", exe.parents[2], "--stdout", out.name, "--stderr", out.name,
             "--args", "/bin/sh", "-c", script, ROOT, sys.executable],
            check=True,
        )  # fmt: skip
        report = out.read()
    print(report, end="")
    return 0 if report.rstrip().endswith("exit 0") else 1
