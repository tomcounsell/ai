"""Writer kill switch for evaluation arms (#3216).

Two independent mechanisms, because a guard that can only fail one way is
trusted without evidence:

1. :func:`arm` wraps ``Memory.save``/``Memory.delete`` in the arm worker so
   a corpus-key write raises :class:`InfraFailure` at the call site. Armed
   after the corpus restore, inside the arm process only.
2. :func:`verify_digest_unchanged` re-checks the canonical corpus digest at
   arm teardown, so a write that reached the server by some path the
   wrapper did not cover still surfaces as ``infra_failure`` rather than
   as a result.
"""

from __future__ import annotations

from .errors import InfraFailure

_armed = False
_original_save = None
_original_delete = None


def is_armed() -> bool:
    """True when corpus writes are currently refused in this process."""
    return _armed


def arm() -> None:
    """Refuse ``Memory.save``/``Memory.delete`` in this process.

    Idempotent. Only ever called inside an arm worker after the corpus
    restore; the parent process is never armed.
    """
    global _armed, _original_save, _original_delete
    if _armed:
        return
    from models.memory import Memory

    _original_save = Memory.save
    _original_delete = Memory.delete

    def _blocked_save(self, *args, **kwargs):
        raise InfraFailure(
            "writer guard: Memory.save refused after the arm corpus restore; "
            "arms are read-only for the rest of the run"
        )

    def _blocked_delete(self, *args, **kwargs):
        raise InfraFailure(
            "writer guard: Memory.delete refused after the arm corpus restore; "
            "arms are read-only for the rest of the run"
        )

    Memory.save = _blocked_save
    Memory.delete = _blocked_delete
    _armed = True


def disarm() -> None:
    """Restore the original ``Memory.save``/``Memory.delete``."""
    global _armed, _original_save, _original_delete
    if not _armed:
        return
    from models.memory import Memory

    Memory.save = _original_save
    Memory.delete = _original_delete
    _original_save = None
    _original_delete = None
    _armed = False


def verify_digest_unchanged(
    expected_digest: str, actual_digest: str, *, context: str = "arm teardown"
) -> None:
    """Raise :class:`InfraFailure` when the corpus moved during arm execution.

    The independent second guard: even a write that slipped past the
    wrapper changes the canonical digest and is caught here.
    """
    if expected_digest != actual_digest:
        raise InfraFailure(
            f"writer guard: corpus digest changed during {context} "
            f"(expected {expected_digest}, got {actual_digest}); "
            "the arm wrote to its corpus, so this run is invalid"
        )
    return None
