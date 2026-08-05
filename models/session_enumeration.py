"""One enumeration seam for "what AgentSessions exist?" (issue #2519).

Three callers used to ask that question three different ways and got three
different answers against the same Redis, minutes apart: the dashboard scanned
``AgentSession.query.all()`` and filtered status in Python (22 pending),
``AgentSession.query.filter(status="pending")`` read the secondary index (0),
and ``valor-session list`` iterated the index per status then filtered
client-side (1). All 22 dashboard rows were real records with intact hashes
and the correct ``status`` field. The index simply did not know about them.

**The scan is the sanctioned path.** ``query.all()`` reads the class set, so a
record with an intact hash always appears regardless of what the ``status``
secondary index believes. Every caller routes through :func:`enumerate_sessions`
so all of them see the same superset.

**Disagreement is loud.** :func:`check_status_index_divergence` compares the
index count against the scan count per status and logs a warning naming each
status that disagrees. It runs on a per-process throttle (one cheap
``query.count()`` per status, at most once per
:data:`DIVERGENCE_CHECK_INTERVAL_S`) so the 5-second dashboard poll does not pay
for it on every request. A one-shot ``valor-session`` invocation therefore
audits the index once per run, which costs about what the scan it already pays
costs and surfaces the warning where an operator is looking.
"""

import logging
import time
from collections import Counter
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# How often the index-vs-scan consistency check actually issues its counts.
# Between checks the call is a no-op returning an empty dict.
DIVERGENCE_CHECK_INTERVAL_S = 300.0

# None until the first check runs. `time.monotonic()` counts from boot rather
# than from process start, so a zero here would swallow the first check of every
# process that opens within 5 minutes of the machine coming up.
_last_divergence_check_at: float | None = None


class SessionScanError(RuntimeError):
    """The class-set scan could not be read.

    Raised only for callers that pass ``strict=True``. An empty result and a
    failed read are the same value otherwise, and a caller that acts on the
    result (rather than rendering it) needs to tell them apart.
    """


def enumerate_sessions(
    statuses: Iterable[str] | None = None,
    *,
    check_divergence: bool = True,
    strict: bool = False,
) -> list:
    """Return AgentSession records, optionally narrowed to ``statuses``.

    Enumerates via the class-set scan and filters status in Python. This is a
    safe superset of what ``query.filter(status=...)`` returns: a record whose
    hash is intact appears here even when the status secondary index has lost
    it.

    A record left without an id by a partial write is dropped here, so no
    caller has to know about it: ``valor-session kill --all`` would otherwise
    call ``finalize_session`` on one, ``valor-session list`` would print it, and
    the dashboard would try to render it. It is also left out of the scan counts
    the divergence check reads, because an id-less hash sitting in the index is
    exactly the #2101 disagreement worth hearing about.

    Args:
        statuses: Status values to keep. ``None`` (the default) returns every
            session regardless of status.
        check_divergence: When True, feeds the observed scan counts to the
            throttled index-consistency check.
        strict: Raise :class:`SessionScanError` when the scan cannot be read,
            rather than returning an empty list. Callers that destroy or report
            on what they find want this; the dashboard wants the default.

    Returns:
        A list of AgentSession instances, unsorted. Empty on any query failure
        (the dashboard's never-crash contract; the failure is logged).

    Raises:
        SessionScanError: When ``strict`` is set and the scan fails.
    """
    from models.agent_session import AgentSession

    try:
        rows = list(AgentSession.query.all())
    except Exception as e:
        logger.warning("[session-enum] scan failed: %s", e)
        if strict:
            raise SessionScanError(f"AgentSession scan failed: {e}") from e
        return []

    wanted = frozenset(statuses) if statuses is not None else None
    scan_counts: Counter = Counter()
    kept = []
    for session in rows:
        status = getattr(session, "status", None)
        if not getattr(session, "id", None):
            logger.warning(
                "[session-enum] skipping a record with no id (partial write): status=%s",
                status,
            )
            continue
        scan_counts[status] += 1
        if wanted is None or status in wanted:
            kept.append(session)

    if check_divergence:
        check_status_index_divergence(scan_counts, wanted)

    return kept


def check_status_index_divergence(
    scan_counts: dict,
    statuses: Iterable[str] | None = None,
    *,
    force: bool = False,
) -> dict[str, tuple[int, int]]:
    """Compare the ``status`` secondary index against observed scan counts.

    Args:
        scan_counts: Mapping of status value to how many records the scan saw.
        statuses: Statuses to check. ``None`` checks every status in
            ``ALL_STATUSES`` plus anything the scan actually observed, so a
            status that has drifted out of the vocabulary is still covered.
        force: Bypass the throttle. Used by tests and by callers that want a
            deliberate one-off audit.

    Returns:
        ``{status: (index_count, scan_count)}`` for every status where the two
        disagree. Empty when they agree, when the throttle window is still
        open, or when the counts could not be read.

    Both directions are reported. ``index < scan`` is the #2519 hole: real
    records the index lost. ``index > scan`` is the #2101 shape: identity-less
    hashes re-SADDed into ``$IndexF:AgentSession:status:pending``, which inflate
    the raw key-set cardinality ``query.count()`` reads while never hydrating
    into a session. Either one means a reader keying off the index is reasoning
    about the wrong set.
    """
    global _last_divergence_check_at

    now = time.monotonic()
    throttled = (
        _last_divergence_check_at is not None
        and (now - _last_divergence_check_at) < DIVERGENCE_CHECK_INTERVAL_S
    )
    if not force and throttled:
        return {}
    _last_divergence_check_at = now

    from models.agent_session import AgentSession
    from models.session_lifecycle import ALL_STATUSES

    if statuses is None:
        targets = set(ALL_STATUSES) | {s for s in scan_counts if s}
    else:
        targets = {s for s in statuses if s}

    divergences: dict[str, tuple[int, int]] = {}
    for status in sorted(targets):
        try:
            index_count = AgentSession.query.count(status=status)
        except Exception as e:
            logger.debug("[session-enum] index count failed for status=%s: %s", status, e)
            continue
        if not isinstance(index_count, int):
            continue  # unreadable count: report nothing rather than a false alarm
        scan_count = scan_counts.get(status, 0)
        if index_count != scan_count:
            divergences[status] = (index_count, scan_count)

    if divergences:
        detail = ", ".join(
            f"{status}: index={index_count} scan={scan_count}"
            for status, (index_count, scan_count) in divergences.items()
        )
        logger.warning(
            "[session-enum] status index disagrees with the record scan (%s). "
            "The scan is authoritative; sessions missing from the index are still "
            "enumerated. AgentSession.rebuild_indexes() resyncs the index.",
            detail,
        )

    return divergences
