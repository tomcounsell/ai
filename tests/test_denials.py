"""`tests/denials.py`: a failure is put down to a denial only when it shows
the denial's error and the denial is met here."""

import pytest

from tests import denials

pytestmark = pytest.mark.spend(usd=0)


@pytest.mark.parametrize(("failure", "probe"), [
    ("PermissionError: [Errno 1] Operation not permitted: '/bin/ps'", denials._ps),
    ("sandbox-exec: sandbox_apply: Operation not permitted", denials._nested),
    ("error while attempting to bind on address ('127.0.0.1', 0): [errno 1]", denials._port_zero),
    ("FileExistsError: [Errno 17] File exists: '/tmp'", denials._shared_tmp),
    ("Command '['diskutil', 'image', 'attach']' returned non-zero exit status 1.", denials._disks),
])  # fmt: skip
def test_a_failure_is_a_denial_only_where_the_denial_is_met(failure, probe):
    why = denials.reason(failure)
    assert (why is not None) == probe()
    assert denials.reason("AssertionError: assert 1 == 2") is None


CONFTEST = """
from tests import denials
from tests.conftest import pytest_runtest_makereport  # noqa: F401

denials._met = lambda probe: True  # every denial is met, as in the check sandbox
"""

INNER = """
def test_names_a_denial():
    raise PermissionError(1, "Operation not permitted: '/bin/ps'")


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
    report = tmp_path / "r.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
         "-c", "/dev/null", f"--junitxml={report}", str(tmp_path / "test_inner.py")],
        cwd=root, env={**__import__("os").environ, "PYTHONPATH": str(root)}, capture_output=True, check=False,
    )  # fmt: skip
    import xml.etree.ElementTree as ET

    outcome = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        kind = [c.tag for c in case]
        outcome[case.get("name")] = kind[0] if kind else "passed"
    assert outcome == {
        "test_names_a_denial": "skipped",
        "test_fails_for_another_reason": "failure",
        "test_fails_on_a_path_that_only_looks_alike": "failure",
    }
