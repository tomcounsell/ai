"""What the sandbox the suite runs under denies, found by trying it.

In a task's workspace the kernel runs the suite under the check profile,
which denies some of what tests do on the host. A test that fails on one of
these is reported skipped, its reason naming the denial
(`tests/conftest.py`, `pytest_runtest_makereport`), only when the failure
shows the denial's own error and trying the operation here meets the same
denial. On the host nothing is denied and nothing is skipped.
"""

import functools
import os
import re
import socket
import subprocess


def _ps() -> bool:
    try:
        subprocess.run(["/bin/ps", "-p", str(os.getpid())], capture_output=True, check=False)
    except PermissionError:
        return True
    return False


def _nested() -> bool:
    profile = '(version 1)(allow default)(deny file-read* (subpath "/nonexistent-valor-probe"))'
    try:
        out = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/true"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return "sandbox_apply: Operation not permitted" in out.stderr


def _port_zero() -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", 0))
        except PermissionError:
            return True
    return False


def _shared_tmp() -> bool:
    try:
        os.listdir("/private/tmp")
    except PermissionError:
        return True
    except FileNotFoundError:  # off macOS
        pass
    return False


def _disks() -> bool:
    try:
        out = subprocess.run(["/usr/sbin/diskutil", "info", "/"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    return "unable to use the DiskManagement framework" in out.stdout + out.stderr


NESTED = "the sandbox the suite runs under denies applying a sandbox inside it"
DISKS = "the sandbox the suite runs under denies DiskArbitration, so no disk image attaches"


def _shows(pattern: str):
    return lambda text: re.search(pattern, text) is not None


# (the denial's own error in the failure, probe, reason)
DENIALS = (
    (_shows(r"Operation not permitted: \\?'/bin/ps"), _ps,
     "the sandbox the suite runs under denies running /bin/ps, a setuid program"),
    (_shows(r"sandbox_apply: Operation not permitted"), _nested, NESTED),
    (_shows(r"bind on address \(\\?'127\.0\.0\.1\\?', 0\)"), _port_zero,
     "the sandbox the suite runs under denies listening on a port the OS chooses"),
    (_shows(r"(Operation not permitted|File exists): \\?'/(private/)?tmp(/|\\?')"), _shared_tmp,
     "the sandbox the suite runs under denies the shared temp directory /private/tmp"),
    (_shows(r"unable to use the DiskManagement framework"), _disks, DISKS),
)  # fmt: skip


@functools.cache
def _met(probe) -> bool:
    return probe()


def met(probe) -> bool:
    """Whether trying the operation here meets the denial: for a test whose
    failure under it carries no error of its own, skipped up front."""
    return _met(probe)


def reason(failure: str) -> str | None:
    """The denial a failure met, when its text shows the denial's error and
    trying the operation here meets it too; else None."""
    for shows, probe, why in DENIALS:
        if shows(failure) and _met(probe):
            return why
    return None
