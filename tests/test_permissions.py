"""Valor.app (`core/permissions.py`): the launcher every launchd job runs through."""

import importlib
import plistlib
import signal
import subprocess
import time
from pathlib import Path

import pytest

from core import permissions, routines
from core.settings import settings

ROOT = Path(__file__).resolve().parent.parent
JOBS = [
    "core.serve",
    "core.backup",
    "tools.voice",
    "bridges.telegram.__main__",
    "bridges.slack.__main__",
    "bridges.email.__main__",
    "bridges.local.__main__",
    "ui.__main__",
]


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    exe = tmp_path_factory.mktemp("apps") / "Valor.app" / "Contents" / "MacOS" / "valor-run"
    permissions.build(exe)
    return exe


def test_build_signs_the_bundle_as_valor_and_never_rebuilds(exe):
    signed = subprocess.run(["codesign", "-dv", exe.parents[2]], capture_output=True, text=True, check=False)
    assert f"Identifier={permissions.BUNDLE_ID}" in signed.stderr
    info = plistlib.loads((exe.parents[1] / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == exe.name and info["LSUIElement"] is True
    before = exe.stat().st_mtime_ns
    permissions.build(exe)
    assert exe.stat().st_mtime_ns == before


def test_the_launcher_runs_its_command_and_exits_as_it_did(exe):
    done = subprocess.run([exe, "/bin/sh", "-c", 'echo "$0 $PWD"; exit 3', "hi"], cwd="/private/tmp",
                          capture_output=True, text=True, check=False)  # fmt: skip
    assert (done.returncode, done.stdout) == (3, "hi /private/tmp\n")
    assert subprocess.run([exe, "/no/such/program"], capture_output=True, check=False).returncode == 127


def test_the_launcher_forwards_term_and_dies_of_it_as_its_command_did(exe):
    proc = subprocess.Popen([exe, "/bin/sleep", "30"])
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=5) == -signal.SIGTERM


@pytest.mark.parametrize("module", JOBS)
def test_every_launchd_job_runs_through_the_launcher(module):
    job = plistlib.loads(importlib.import_module(module).plist())
    assert job["ProgramArguments"][0] == settings.launcher


def test_every_routine_runs_through_the_launcher():
    job = plistlib.loads(routines.plist(routines.load("verify", ROOT / "routines")))
    assert job["ProgramArguments"][0] == settings.launcher


def test_ensure_puts_every_python_job_on_the_launcher_and_reloads_its_own_last(exe, tmp_path):
    jobs = {
        "com.valor.kernel": ["/opt/homebrew/bin/python3.14", "-m", "core.serve"],
        "com.valor.voice": ["/opt/homebrew/bin/python3.14", "-m", "tools.voice"],
        "com.valor.done": [str(exe), "/opt/homebrew/bin/python3.14", "-m", "core.backup"],
        "com.valor.brew-nightly": ["/bin/bash", "brew-nightly-update.sh"],
    }
    for label, argv in jobs.items():
        (tmp_path / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": argv}))
    reloaded = []

    def reload(label, plist, own):
        reloaded.append((label, own))
        return "5: Input/output error" if label == "com.valor.voice" else None

    done = permissions.ensure("com.valor.kernel", tmp_path, exe, reload)
    assert done == {
        "fixed": ["com.valor.voice", "com.valor.kernel"],
        "failed": ["com.valor.voice: 5: Input/output error"],
    }
    assert reloaded == [("com.valor.voice", False), ("com.valor.kernel", True)]
    argv = {p.stem: plistlib.loads(p.read_bytes())["ProgramArguments"] for p in tmp_path.glob("*.plist")}
    assert argv["com.valor.kernel"] == [str(exe), *jobs["com.valor.kernel"]]
    assert argv["com.valor.done"] == jobs["com.valor.done"]
    assert argv["com.valor.brew-nightly"] == jobs["com.valor.brew-nightly"]
    assert permissions.ensure("com.valor.kernel", tmp_path, exe, reload) == {}
