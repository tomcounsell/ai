"""The test Dovecot ends with the process that started it."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

START = """
import sys
from pathlib import Path
from tests import mailserver

root = Path(sys.argv[1])
root.mkdir(exist_ok=True)
d = mailserver.Dovecot(root, mailserver.Ports().take())
d.start()
print((d.base / "master.pid").read_text().strip(), flush=True)
sys.stdin.read()
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_dovecot_does_not_outlive_a_pytest_process_that_was_killed(tmp_path):
    owner = subprocess.Popen(
        [sys.executable, "-c", START, str(tmp_path / "dovecot")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=Path(__file__).parent.parent,
    )
    try:
        pid = int(owner.stdout.readline())
        assert alive(pid)
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait()
        deadline = time.monotonic() + 30
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not alive(pid)
    finally:
        if owner.poll() is None:
            os.kill(owner.pid, signal.SIGKILL)
            owner.wait()
