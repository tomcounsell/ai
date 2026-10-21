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
"""

import ctypes
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time
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


def granted() -> dict[str, bool | None]:
    """Each grant's name: allowed, denied, or None when never decided.
    Reading the databases needs Full Disk Access."""
    found: dict[tuple[str, str], int] = {}
    for db in DATABASES:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT service, indirect_object_identifier, auth_value FROM access WHERE client = ?",
                (BUNDLE_ID,),
            )
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
