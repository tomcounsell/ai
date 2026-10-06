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
    return False


def _disks() -> bool:
    try:
        out = subprocess.run(["/usr/sbin/diskutil", "info", "/"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    return "unable to use the DiskManagement framework" in out.stdout + out.stderr


def _tmp_signature(text: str) -> bool:
    return re.search(r"\\?'/tmp(/|\\?')|/private/tmp", text) is not None


NESTED = "the sandbox the suite runs under denies applying a sandbox inside it"

# (signature in the failure, probe, reason)
DENIALS = (
    (lambda t: re.search(r"Operation not permitted: \\?'/bin/ps", t) is not None, _ps,
     "the sandbox the suite runs under denies running /bin/ps, a setuid program"),
    # Every sandbox-exec fails here, so a failure that ran one met it.
    (lambda t: "sandbox_apply: Operation not permitted" in t or "sandbox-exec" in t, _nested, NESTED),
    (lambda t: re.search(r"bind on address \(\\?'127\.0\.0\.1\\?', 0\)", t) is not None, _port_zero,
     "the sandbox the suite runs under denies listening on a port the OS chooses"),
    (lambda t: _tmp_signature(t) and ("Operation not permitted" in t or "File exists" in t), _shared_tmp,
     "the sandbox the suite runs under denies the shared temp directory /private/tmp"),
    (lambda t: "diskutil" in t or "hdiutil" in t, _disks,
     "the sandbox the suite runs under denies DiskArbitration, so no disk image attaches"),
)  # fmt: skip


@functools.cache
def _met(probe) -> bool:
    return probe()


def reason(failure: str) -> str | None:
    """The denial a failure met, when its text shows the denial's error and
    trying the operation here meets it too; else None."""
    for shows, probe, why in DENIALS:
        if shows(failure) and _met(probe):
            return why
    return None
