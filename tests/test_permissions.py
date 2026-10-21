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
