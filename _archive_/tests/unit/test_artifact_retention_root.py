"""Cross-process retention-root acceptance tests (issue #3274, lane 7 task 7).

The artifact retention root lives off the repo checkout so a container image
rebuild cannot destroy it. These tests prove the property the way a sandbox
would observe it: two separate OS processes, started in different working
directories, resolving the same durable root and round-tripping an artifact
through it with hash verification on load.

No provider account, no Redis, no network. ``subprocess`` with an explicit
``PYTHONPATH`` is the whole harness: ``_default_base_path`` reads
``POPOTO_IMPROVEMENT_CONTENT_PATH`` fresh from the environment on every call,
so a second local process is a faithful stand-in for the container.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PRINT_DEFAULT = (
    "from models.verifying_artifact_store import _default_base_path;"
    "print(_default_base_path(), flush=True)"
)

WRITE_ARTIFACT = (
    "import sys;"
    "from models.verifying_artifact_store import VerifyingArtifactStore;"
    "store = VerifyingArtifactStore();"
    "ref = store.save(sys.stdin.buffer.read(), key='lane7-proof', model_class_name='Exp');"
    "print(ref, flush=True)"
)

LOAD_ARTIFACT = (
    "import sys;"
    "from models.verifying_artifact_store import VerifyingArtifactStore;"
    "store = VerifyingArtifactStore();"
    "sys.stdout.buffer.write(store.load(sys.argv[1]))"
)


def _run(script, cwd, env, **kwargs):
    full_env = {"PYTHONPATH": REPO_ROOT, **os.environ, **env}
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        timeout=120,
        **kwargs,
    )


@pytest.mark.unit
class TestRetentionRootCrossProcess:
    def test_retention_root_default_is_off_the_checkout(self, tmp_path):
        """Both processes resolve the same default, and it is not in the checkout.

        Pre-supersession this default was ``data/improvement_content`` inside
        the repo, which an image rebuild destroys on every redeploy.
        """
        proc_a = tmp_path / "proc_a"
        proc_b = tmp_path / "proc_b"
        proc_a.mkdir()
        proc_b.mkdir()
        env = {"POPOTO_IMPROVEMENT_CONTENT_PATH": ""}

        first = _run(PRINT_DEFAULT, cwd=str(proc_a), env=env)
        second = _run(PRINT_DEFAULT, cwd=str(proc_b), env=env)

        assert first.returncode == 0, first.stderr.decode()
        assert second.returncode == 0, second.stderr.decode()

        default_a = first.stdout.decode().strip()
        default_b = second.stdout.decode().strip()
        assert default_a == default_b
        assert os.path.isabs(default_a)
        inside_checkout = default_a.startswith(os.path.abspath(REPO_ROOT) + os.sep)
        message = f"retention root {default_a} would not survive an image rebuild"
        assert not inside_checkout, message

    def test_retention_root_cross_process_round_trip(self, tmp_path):
        """Write from one process, load hash-verified from a second elsewhere.

        ``POPOTO_IMPROVEMENT_CONTENT_PATH`` points at the durable root; the
        writer runs in one directory, the reader in another, and the store
        re-hashes on every load, so a match proves the bytes survived the trip
        intact rather than merely present.
        """
        durable = tmp_path / "durable" / "improvement_content"
        proc_a = tmp_path / "writer"
        proc_b = tmp_path / "reader"
        proc_a.mkdir()
        proc_b.mkdir()
        env = {"POPOTO_IMPROVEMENT_CONTENT_PATH": str(durable)}
        content = b"lane-7 cross-process proof bytes"

        written = _run(WRITE_ARTIFACT, cwd=str(proc_a), env=env, input=content)
        assert written.returncode == 0, written.stderr.decode()
        ref = written.stdout.decode().strip()
        assert ref.startswith("$CF:")

        assert any(durable.rglob("*")), "artifact landed under the durable root"

        loaded = subprocess.run(
            [sys.executable, "-c", LOAD_ARTIFACT, ref],
            cwd=str(proc_b),
            env={"PYTHONPATH": REPO_ROOT, **os.environ, **env},
            capture_output=True,
            timeout=120,
        )
        assert loaded.returncode == 0, loaded.stderr.decode()
        assert loaded.stdout == content
