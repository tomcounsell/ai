"""Per-arm private Redis processes for the frozen-input harness (#3216).

Each arm gets its own ``redis-server`` on a unix socket in a per-arm
tmpdir, with ``--port 0`` (no TCP listener, so no port for a concurrent
agent to collide on: issue #2799 is the failure this avoids) and no
persistence. The arm is then reached ONLY by a
child Python process (``arm_worker.py``) whose env dict carries
``REDIS_URL=unix://<arm.sock>``: inside that child, and only inside that
child, popoto's canonical pool IS the arm's private server.

Parent-side properties, each pinned by a test:

- This module never imports the test-suite claim pool and never participates in
  the test-suite db-claim registry.
- The parent's ``os.environ`` is never assigned: the arm's ``REDIS_URL``
  is a key in the ``subprocess.run(env=...)`` dict built by
  :func:`build_child_env`, never an assignment into the parent.
- The parent's canonical pool is never re-pointed and no pool-rebinding helper
  is ever called; no bare ``redis.Redis`` client answers
  ``Memory.query`` on either side.
- This module opens no Redis client of its own, not even for a readiness
  ping: readiness is the socket file plus a live server process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import InfraFailure

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

AF_UNIX_SUN_PATH_LIMIT = 104
"""Platform ``AF_UNIX`` ``sun_path`` size in bytes.

redis-py surfaces an over-long socket path as a bare ``ConnectionError``
inside the child at import, which would otherwise reach the runner as an
opaque subprocess failure. The path is asserted against this limit before
spawning so the failure arrives as a named :class:`InfraFailure`.
"""

ARM_SOCKET_NAME = "arm.sock"
REDIS_STARTUP_WAIT_S = 10.0
ARM_WORKER_TIMEOUT_S = 600

#: The ranking path every arm runs. ``current`` forces
#: ``agent.memory_retrieval.retrieve_memories`` onto the four-signal RRF
#: path, whose every input is persisted state. The default ``auto`` routes
#: through popoto's hybrid ``ContextAssembler`` first, whose post-retrieve
#: effects write confidence and access-tracker updates through a raw
#: pipeline; inside an arm that write trips the digest re-check and every
#: real query ends as ``infra_failure``. The harness measures the RRF path.
ARM_RETRIEVAL_MODE = "current"


@dataclass
class Arm:
    """A live private Redis process and the paths that reach it."""

    sock_path: str
    tmpdir: str
    content_dir: str
    pid: int


def assert_socket_path_fits(sock_path: str) -> None:
    """Raise :class:`InfraFailure` when the socket path exceeds the limit."""
    measured = len(os.fsencode(sock_path))
    if measured > AF_UNIX_SUN_PATH_LIMIT:
        raise InfraFailure(
            f"arm socket path is {measured} bytes, exceeding the AF_UNIX "
            f"sun_path limit of {AF_UNIX_SUN_PATH_LIMIT} bytes; "
            "redis-py would fail with a bare ConnectionError inside the arm"
        )
    return None


def build_child_env(*, sock_path: str, content_dir: str, project_key: str) -> dict:
    """Build the arm worker subprocess env dict (never assigned to the parent).

    ``RETRIEVAL_MODE`` is pinned to :data:`ARM_RETRIEVAL_MODE` so the arm
    ranks through the four-signal RRF path whatever the ambient environment
    says; ``config.settings.HybridEvalSettings.retrieval_mode`` reads that
    key inside the child.
    """
    env = dict(os.environ)
    env["REDIS_URL"] = f"unix://{sock_path}"
    env["POPOTO_CONTENT_PATH"] = content_dir
    env["VALOR_PROJECT_KEY"] = project_key
    env["POPOTO_EMBEDDING_INVALIDATION"] = "none"
    env["RETRIEVAL_MODE"] = ARM_RETRIEVAL_MODE
    return env


def _wait_for_socket(sock_path: str, proc: subprocess.Popen) -> None:
    deadline = time.time() + REDIS_STARTUP_WAIT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            raise InfraFailure(
                f"arm redis-server exited during startup (returncode {proc.returncode})"
            )
        if os.path.exists(sock_path):
            return
        time.sleep(0.05)
    raise InfraFailure(
        f"arm redis-server did not create {sock_path} within {REDIS_STARTUP_WAIT_S}s"
    )


@contextmanager
def arm_redis_server(*, base_tmpdir: str | None = None):
    """Spawn one private arm Redis; terminate it and remove the tmpdir on exit.

    The ``finally`` terminates the known child process and removes the
    tmpdir (socket included) even when the arm body raised, and also when
    the socket-path guard or the ``redis-server`` spawn itself failed. A
    missing or unlaunchable ``redis-server`` binary arrives as the named
    "arm that would not spawn" :class:`InfraFailure`.
    """
    tmpdir = tempfile.mkdtemp(prefix="improve-arm-", dir=base_tmpdir)
    proc: subprocess.Popen | None = None
    try:
        sock_path = os.path.join(tmpdir, ARM_SOCKET_NAME)
        assert_socket_path_fits(sock_path)
        content_dir = os.path.join(tmpdir, "content")
        os.makedirs(content_dir, exist_ok=True)
        try:
            proc = subprocess.Popen(
                [
                    "redis-server",
                    "--port",
                    "0",
                    "--unixsocket",
                    sock_path,
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    tmpdir,
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise InfraFailure(f"arm redis-server would not spawn: {exc}") from exc
        _wait_for_socket(sock_path, proc)
        yield Arm(sock_path=sock_path, tmpdir=tmpdir, content_dir=content_dir, pid=proc.pid)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_arm_job(
    arm: Arm,
    project_key: str,
    job: dict,
    *,
    timeout_s: float = ARM_WORKER_TIMEOUT_S,
    clock_skew_s: float = 0.0,
) -> dict:
    """Run one job in an arm worker subprocess and return its response dict.

    The worker reads the JSON job spec on stdin and writes JSON on stdout.
    Any transport failure or worker-reported error arrives here as
    :class:`InfraFailure`. ``clock_skew_s`` travels inside the job spec as
    ``clock_skew_s`` (the clock-gap test's lever; the runner never sets it),
    so nothing in the ambient environment can skew a real arm's clock.
    """
    child_env = build_child_env(
        sock_path=arm.sock_path,
        content_dir=arm.content_dir,
        project_key=project_key,
    )
    if clock_skew_s:
        job = {**job, "clock_skew_s": float(clock_skew_s)}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.improvement_eval.arm_worker"],
            input=json.dumps(job),
            capture_output=True,
            text=True,
            env=child_env,
            timeout=timeout_s,
            cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired as exc:
        raise InfraFailure(
            f"arm worker timed out after {timeout_s}s on job mode {job.get('mode')!r}"
        ) from exc
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip()[-2000:]
        raise InfraFailure(
            f"arm worker exited with returncode {completed.returncode} on job "
            f"mode {job.get('mode')!r}; stderr tail: {tail}"
        )
    try:
        response = json.loads(completed.stdout)
    except ValueError as exc:
        raise InfraFailure(
            "arm worker wrote unparseable stdout on job mode "
            f"{job.get('mode')!r}: {completed.stdout[-500:]!r}"
        ) from exc
    if not isinstance(response, dict) or response.get("status") != "ok":
        detail = response.get("error") if isinstance(response, dict) else response
        raise InfraFailure(f"arm worker reported an error: {detail}")
    return response
