"""Arm-side entry point for the frozen-input harness (#3216).

Run as ``python -m tools.improvement_eval.arm_worker`` in a child process
whose env dict carries ``REDIS_URL=unix://<arm.sock>`` (plus the arm's own
``POPOTO_CONTENT_PATH``, ``VALOR_PROJECT_KEY``,
``POPOTO_EMBEDDING_INVALIDATION=none``, and ``RETRIEVAL_MODE=current`` so
retrieval ranks through the four-signal RRF path). Inside this process, and
only inside this process, popoto's canonical pool is the arm's private
server, so every Redis touch here goes through the ORM against the arm.

Protocol: one JSON job spec on stdin, one JSON response on stdout.

- ``{"mode": "restore", "jsonl": ..., "project_key": ...}``: restore the
  frozen corpus, arm the writer guard, and report the re-export digest.
- ``{"mode": "retrieve", "jsonl": ..., "project_key": ...,
  "query_text": ..., "limit": ...}``: restore, arm, retrieve, and report
  the ranked ids with the digests.
- ``{"mode": "digest", "jsonl": ..., "project_key": ...}``: restore, arm,
  and report the re-export digest plus the canonical remaining manifest
  (the runner asserts byte-equality between arms).

Every mode ends with the teardown digest re-check: the digest taken right
after restore must equal the digest taken after the job's reads, or a
write slipped past the wrapper and the arm is invalid.

An optional ``"clock_skew_s"`` in a retrieve job shifts this process's
``time.time`` during the retrieve step only. It exists so a test can query
an arm under a clock 30 days forward and prove the retrieval path does
not read the decay clock. It travels in the job spec rather than the
environment, and the runner's ``ARM_PARAM_KEYS`` allowlist keeps it out of
every job it builds from a protocol, so neither the ambient environment nor
a frozen contract can skew a real arm's clock; only
``run_arm_job(clock_skew_s=...)`` sets it.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager

from .errors import InfraFailure

JOB_MODES = ("restore", "retrieve", "digest")


@contextmanager
def _skewed_clock(skew: float):
    """Shift ``time.time`` for the retrieve step by ``skew`` seconds."""
    if not skew:
        yield
        return
    real_time = time.time
    time.time = lambda: real_time() + skew
    try:
        yield
    finally:
        time.time = real_time


def _restore_and_arm(jsonl_text: str) -> None:
    from . import writer_guard
    from .corpus import restore_corpus

    restore_corpus(jsonl_text)
    writer_guard.arm()
    return None


def _arm_digest(project_key: str):
    from models.memory import Memory

    from .corpus import canonical_corpus_digest, canonical_manifest

    reexport = Memory.export_records(project_key=project_key)
    jsonl_text = reexport.data or ""
    return canonical_corpus_digest(jsonl_text), canonical_manifest(jsonl_text)


def handle_job(job: dict) -> dict:
    """Execute one job spec and return the response payload (status excluded)."""
    from . import writer_guard

    mode = job.get("mode")
    if mode not in JOB_MODES:
        raise InfraFailure(f"unknown arm job mode {mode!r}; expected one of {JOB_MODES}")
    jsonl_text = job.get("jsonl")
    if not isinstance(jsonl_text, str) or not jsonl_text.strip():
        raise InfraFailure(f"arm job mode {mode!r} requires a non-empty 'jsonl' field")
    project_key = job.get("project_key")
    if not project_key:
        raise InfraFailure(f"arm job mode {mode!r} requires a 'project_key' field")

    _restore_and_arm(jsonl_text)
    from .corpus import canonical_corpus_digest

    expected_input_digest = canonical_corpus_digest(jsonl_text)
    digest_after_restore, _ = _arm_digest(project_key)

    response = {
        "restore_digest": digest_after_restore,
        "digest": digest_after_restore,
        "input_digest": expected_input_digest,
    }

    if mode == "retrieve":
        from .retrieval import retrieve_ranked_ids

        query_text = job.get("query_text", "")
        limit = int(job.get("limit", 10))
        try:
            skew = float(job.get("clock_skew_s") or 0.0)
        except (TypeError, ValueError) as exc:
            raise InfraFailure(f"arm job 'clock_skew_s' is not a number: {exc}") from exc
        with _skewed_clock(skew):
            response["ids"] = retrieve_ranked_ids(query_text, project_key, limit=limit)

    digest_final, manifest_final = _arm_digest(project_key)
    writer_guard.verify_digest_unchanged(
        digest_after_restore, digest_final, context=f"arm {mode} job"
    )
    response["digest"] = digest_final
    response["manifest"] = manifest_final
    return response


def main() -> int:
    """Read one job on stdin, write one response on stdout."""
    try:
        job = json.loads(sys.stdin.read())
    except ValueError as exc:
        sys.stdout.write(json.dumps({"status": "error", "error": f"unparseable job: {exc}"}))
        return 0
    if not isinstance(job, dict):
        sys.stdout.write(json.dumps({"status": "error", "error": "job must be a JSON object"}))
        return 0
    try:
        payload = handle_job(job)
    except Exception as exc:
        sys.stdout.write(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}))
        return 0
    payload["status"] = "ok"
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
