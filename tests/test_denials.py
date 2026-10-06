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
