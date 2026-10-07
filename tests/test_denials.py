"""`tests/denials.py`: a failure is put down to a denial only when it shows
the denial's error and the denial is met here."""

import psycopg
import pytest

from tests import denials

pytestmark = pytest.mark.spend(usd=0)


@pytest.mark.parametrize(("failure", "probe"), [
    ("PermissionError: [Errno 1] Operation not permitted: '/bin/ps'", denials._ps),
    ("sandbox-exec: sandbox_apply: Operation not permitted", denials._nested),
    ("error while attempting to bind on address ('127.0.0.1', 0): [errno 1]", denials._port_zero),
    pytest.param("FileExistsError: [Errno 17] File exists: '/tmp'", denials._shared_tmp,
                 marks=pytest.mark.macos),  # /private/tmp is macOS's
    ("Unable to run because unable to use the DiskManagement framework.", denials._disks),
])  # fmt: skip
def test_a_failure_is_a_denial_only_where_the_denial_is_met(failure, probe):
    why = denials.reason(failure)
    assert (why is not None) == probe()
    assert denials.reason("AssertionError: assert 1 == 2") is None


class _Cluster:
    """A connection whose `SHOW data_directory` names `data`, or raises."""

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query):
        if isinstance(self.data, Exception):
            raise self.data
        return self

    def fetchone(self):
        return (self.data,)


@pytest.mark.parametrize(("data", "stat", "met"), [
    ("/x/pg/data", PermissionError(1, "Operation not permitted"), True),
    ("/x/pg/data", FileNotFoundError(2, "No such file or directory"), False),
    ("/x/pg/data", None, False),
    (psycopg.errors.InsufficientPrivilege('permission denied to examine "data_directory"'), None, False),
])  # fmt: skip
def test_the_cluster_data_denial_is_met_only_when_the_directory_itself_is_denied(
    monkeypatch, data, stat, met
):
    """A role refused `data_directory` is the suite's own failure, never a skip."""

    def fake_stat(path):
        if stat is not None:
            raise stat

    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: _Cluster(data))
    monkeypatch.setattr(denials.os, "stat", fake_stat)
    assert denials._cluster_data() is met


CONFTEST = """
from tests import denials
from tests.conftest import pytest_runtest_makereport  # noqa: F401

denials._met = lambda probe: True  # every denial is met, as in the check sandbox
"""

INNER = """
import subprocess


def test_names_a_denial():
    raise PermissionError(1, "Operation not permitted: '/bin/ps'")


def test_a_command_whose_stderr_names_a_denial():
    raise subprocess.CalledProcessError(
        1, ["diskutil", "image", "attach"], stderr="Unable to run because unable to use the DiskManagement framework."
    )


def test_a_source_line_names_sandbox_exec():
    argv = ["/usr/bin/sandbox-exec", "-f", "p.sb", "/usr/bin/true"]
    assert argv[0] == "/usr/bin/env"


def test_a_source_line_names_hdiutil_and_diskutil():
    cmd = "hdiutil attach"
    assert cmd.startswith("diskutil")


def test_a_source_line_names_tmp_and_the_error_another_path():
    scratch = '/tmp/x'
    raise FileExistsError(17, "File exists", "/home/me/x")


def test_fails_for_another_reason():
    assert 1 == 2, "the answer was wrong"


def test_fails_on_a_path_that_only_looks_alike():
    raise PermissionError(1, "Operation not permitted: '/home/me/notes'")
"""


def test_with_every_denial_met_only_a_failure_naming_one_is_skipped(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_inner.py").write_text(INNER)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    report = tmp_path / "r.xml"
    ran = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
         "-c", str(tmp_path / "pytest.ini"), f"--junitxml={report}", "test_inner.py"],
        cwd=tmp_path, env={**__import__("os").environ, "PYTHONPATH": str(root)}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    import xml.etree.ElementTree as ET

    outcome = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        kind = [c.tag for c in case]
        outcome[case.get("name")] = kind[0] if kind else "passed"
    assert outcome == {
        "test_names_a_denial": "skipped",
        "test_a_command_whose_stderr_names_a_denial": "skipped",
        "test_a_source_line_names_sandbox_exec": "failure",
        "test_a_source_line_names_hdiutil_and_diskutil": "failure",
        "test_a_source_line_names_tmp_and_the_error_another_path": "failure",
        "test_fails_for_another_reason": "failure",
        "test_fails_on_a_path_that_only_looks_alike": "failure",
    }, ran.stdout + ran.stderr
