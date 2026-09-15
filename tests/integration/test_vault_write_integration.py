"""Integration test: tools/vault_write.py against the real `op` CLI (Task 9).

Skipped with a named reason unless `OP_CACHE=false op whoami` succeeds
non-interactively -- the `valor-local` service account (`~/.claude/CLAUDE.md`)
must be present on this machine for this test to mean anything. When it
does run, it creates one item titled `test-lane3-<uuid>` in `m-valor` and
deletes it in a `finally`, leaving no residue in the real vault.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from tools.vault_write import write_credential


def _op_available() -> bool:
    try:
        completed = subprocess.run(
            ["op", "whoami"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "OP_CACHE": "false"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


pytestmark = pytest.mark.skipif(
    not _op_available(),
    reason="op CLI not authenticated non-interactively (OP_CACHE=false op whoami failed); "
    "this test needs the valor-local service account (~/.claude/CLAUDE.md)",
)


def test_write_credential_creates_and_this_test_deletes_one_real_item():
    title = f"test-lane3-{uuid.uuid4().hex[:8]}"
    result = write_credential(title, "integration-test-value-not-a-real-secret", vault="m-valor")
    try:
        assert result.state == "created"
        assert result.fingerprint.startswith("sha256:")
    finally:
        subprocess.run(
            ["op", "item", "delete", title, "--vault", "m-valor"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "OP_CACHE": "false"},
        )
