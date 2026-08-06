"""Deliberately-violating fixture for the fence census red-state proof."""


def _reprieve_like_consumer(entry) -> bool:
    """Exact shape of the six PR #2516 defects: pid rebound, create_time dropped."""
    fence = getattr(entry, "live_fence", None)
    pid = fence.get("pid") if isinstance(fence, dict) else None
    if pid is not None:
        return True
    return False


def _inline_consumer(entry) -> int | None:
    """The inline variant, also unguarded."""
    return (getattr(entry, "live_fence", None) or {}).get("pid")


def _properly_guarded(entry) -> bool:
    from agent.pid_fence import fence_is_live

    fence = getattr(entry, "live_fence", None)
    pid = fence.get("pid") if isinstance(fence, dict) else None
    ct = fence.get("create_time") if isinstance(fence, dict) else None
    return fence_is_live(pid, ct)


def _log_only(entry) -> str:
    # fence-census: log-only, not a decision consumer
    return f"pid={(getattr(entry, 'live_fence', None) or {}).get('pid')}"
