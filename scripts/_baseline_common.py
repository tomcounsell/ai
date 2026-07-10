"""Shared helpers for the merge-gate baseline system.

Both `scripts/refresh_test_baseline.py` and `scripts/baseline_gate.py` parse
pytest junitxml and operate on `data/main_test_baseline.json`.  The parsing
rules and schema constants live here so the two scripts can't drift.

See `docs/features/merge-gate-baseline.md` for the feature overview and
`docs/plans/merge-gate-baseline-refresh.md` for the design plan (issue #1084).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Exact prefix emitted by pytest-timeout in `<failure message="...">`.
# We match this prefix and NOT a loose substring like "Timeout" because a
# regular assertion failure whose message contains the word "Timeout"
# (for example `assert response != "Timeout"`) must classify as `real`/`flaky`,
# not as `hung`.  See Data Flow section 4 Implementation Note in the plan.
PYTEST_TIMEOUT_PREFIX = "Failed: Timeout >"

# Four classification buckets used throughout the baseline system.
CATEGORY_REAL = "real"
CATEGORY_FLAKY = "flaky"
CATEGORY_HUNG = "hung"
CATEGORY_IMPORT_ERROR = "import_error"

VALID_CATEGORIES = frozenset({CATEGORY_REAL, CATEGORY_FLAKY, CATEGORY_HUNG, CATEGORY_IMPORT_ERROR})

# Schema version for the categorised baseline file.
SCHEMA_VERSION = 2


class JunitxmlParseError(Exception):
    """Raised when junitxml is unreadable (malformed, truncated, missing)."""


def parse_junitxml(xml_path: str | Path) -> dict[str, str]:
    """Parse a pytest junitxml file into a `{node_id: outcome}` map.

    Returns a mapping where each value is one of:
    - ``"pass"`` — testcase has no ``<failure>`` or ``<error>`` children
    - ``"fail"`` — testcase has a ``<failure>`` child whose ``message`` does NOT
      start with ``PYTEST_TIMEOUT_PREFIX``
    - ``"timeout"`` — testcase has a ``<failure>`` child whose ``message``
      starts with ``PYTEST_TIMEOUT_PREFIX`` (exact prefix match)
    - ``"collection_error"`` — testcase has an ``<error>`` child

    Wraps ``xml.etree.ElementTree.parse`` in ``try/except ParseError`` ONLY
    (never bare ``except``).  Truncated or malformed junitxml produces
    :class:`JunitxmlParseError` with the original ``ParseError`` chained.

    Raises:
        JunitxmlParseError: if the file is missing, unreadable, or truncated.
    """
    try:
        tree = ET.parse(str(xml_path))
    except FileNotFoundError as exc:
        raise JunitxmlParseError(f"junitxml not found: {xml_path}") from exc
    except ET.ParseError as exc:
        raise JunitxmlParseError(f"junitxml parse error at {xml_path}: {exc}") from exc

    root = tree.getroot()
    outcomes: dict[str, str] = {}

    for index, testcase in enumerate(root.iter("testcase")):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        error = testcase.find("error")

        if not name:
            # A testcase missing `name` is a known xdist/execnet worker-crash
            # artifact (issue #1853). Discarding the WHOLE run for one
            # nameless element is too costly -- a 3-run refresh silently
            # degrades to 1 usable run, which makes every transient flake
            # look "real". Instead: best-effort classify it if it carries
            # an <error> child (a real collection error), else skip just
            # this one element and keep parsing the rest of the run.
            if error is None:
                continue
            node_id = classname if classname else f"<unknown>::{index}"
            outcomes[node_id] = "collection_error"
            continue

        node_id = _build_node_id(classname, name)
        failure = testcase.find("failure")

        if error is not None:
            outcomes[node_id] = "collection_error"
        elif failure is not None:
            message = failure.get("message") or ""
            if message.startswith(PYTEST_TIMEOUT_PREFIX):
                outcomes[node_id] = "timeout"
            else:
                outcomes[node_id] = "fail"
        else:
            outcomes[node_id] = "pass"

    return outcomes


def _build_node_id(classname: str, name: str) -> str:
    """Reconstruct pytest's node ID form (`path::Class::test` or `path::test`).

    pytest junitxml encodes ``classname`` as dotted module path (e.g.
    ``tests.unit.test_foo.TestBar``), which we reshape back to
    ``tests/unit/test_foo.py::TestBar::test_quux``.  This matches how pytest
    emits node IDs on stdout and how the merge-gate/baseline store keys them.
    """
    if not classname:
        return name

    parts = classname.split(".")
    # Find the last segment that starts with ``test_`` or is a bare ``test``
    # file (pytest allows ``conftest.py``-style modules too, but test files
    # always start with ``test_``).  Everything up to and including that
    # segment is the file path.  Anything after is the class name.
    file_end = -1
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            file_end = i
            break

    if file_end == -1:
        # Fall back to treating the whole thing as dotted module -> path.
        file_path = "/".join(parts) + ".py"
        return f"{file_path}::{name}"

    file_parts = parts[: file_end + 1]
    class_parts = parts[file_end + 1 :]
    file_path = "/".join(file_parts) + ".py"
    if class_parts:
        return f"{file_path}::{'::'.join(class_parts)}::{name}"
    return f"{file_path}::{name}"


def failing_node_ids(outcomes: dict[str, str]) -> set[str]:
    """Extract node IDs from a ``{node_id: outcome}`` map whose outcome is not pass."""
    return {node_id for node_id, outcome in outcomes.items() if outcome != "pass"}


# ---------------------------------------------------------------------------
# ArtifactEnvelope: provenance + state stamped on the persisted baseline
# artifact (issue #2004, T1.3).
#
# The envelope carries provenance and state ONLY -- never threshold fields.
# Staleness thresholds live in ``scripts/baseline_gate.py`` module constants
# (``STALENESS_THRESHOLD``, ``STALE_COMMIT_DISTANCE``); :func:`staleness`
# reads those constants at call time so a stale artifact can never enforce
# its own old thresholds.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactEnvelope:
    """The five envelope fields stamped on ``data/main_test_baseline.json``.

    ``{generated_at, commit, generated_by, runs, degraded}`` -- nothing else.
    ``degraded`` marks an artifact written from fewer usable runs than flaky
    classification requires; the gate reads it later (the persisted artifact
    is the silent surface, not the refresh script's exit code).
    """

    generated_at: str | None = None
    commit: str | None = None
    generated_by: str | None = None
    runs: int | None = None
    degraded: bool = False

    @property
    def is_legacy(self) -> bool:
        """True when the artifact predates envelope stamping (core fields absent)."""
        return self.generated_at is None or self.runs is None

    def to_fields(self) -> dict:
        """Render the envelope as the five artifact top-level fields."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


def read_envelope(artifact: object) -> ArtifactEnvelope:
    """Defensively read envelope fields from an artifact dict.

    Never raises: a non-dict artifact or malformed field types coerce to
    ``None`` (legacy).  ``degraded`` is only honoured as the literal ``True``
    so a stray string like ``"no"`` can never flip the flag on.
    """
    if not isinstance(artifact, dict):
        return ArtifactEnvelope()

    def _str(key: str) -> str | None:
        value = artifact.get(key)
        return value if isinstance(value, str) and value else None

    runs = artifact.get("runs")
    if isinstance(runs, bool) or not isinstance(runs, int):
        runs = None

    return ArtifactEnvelope(
        generated_at=_str("generated_at"),
        commit=_str("commit"),
        generated_by=_str("generated_by"),
        runs=runs,
        degraded=artifact.get("degraded") is True,
    )


def parse_generated_at(generated_at: str | None) -> datetime | None:
    """Parse an ISO-8601 ``generated_at`` to a tz-aware datetime, or ``None``."""
    if not isinstance(generated_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def staleness(
    envelope: ArtifactEnvelope | dict,
    *,
    now: datetime | None = None,
    commits_behind: int | None = None,
) -> list[str]:
    """Return the list of staleness reasons for an artifact envelope.

    The ONE shared staleness definition used by both the merge gate
    (``scripts/baseline_gate.py``) and the weekly reflection
    (``reflections/housekeeping/test_baseline_refresh_check.py``).  An empty
    list means "fresh".  Three triggers:

    - ``generated_at`` older than ``baseline_gate.STALENESS_THRESHOLD``
    - ``commit`` ends with ``-dirty`` (irreproducible capture)
    - ``commits_behind`` past ``baseline_gate.STALE_COMMIT_DISTANCE``

    Thresholds are read from ``scripts.baseline_gate`` module attributes at
    call time (lazy import to avoid a circular import; the gate imports this
    module at its top level).  The envelope itself never carries thresholds.
    ``commits_behind=None`` (git unavailable / unknown commit) skips the
    commit-distance trigger.
    """
    import scripts.baseline_gate as baseline_gate  # noqa: PLC0415 -- lazy: avoids circular import

    env = envelope if isinstance(envelope, ArtifactEnvelope) else read_envelope(envelope)
    now = now or datetime.now(UTC)
    reasons: list[str] = []

    generated_at = parse_generated_at(env.generated_at)
    if generated_at is not None:
        age = now - generated_at
        if age > baseline_gate.STALENESS_THRESHOLD:
            reasons.append(
                f"generated_at is {age.days} days old (> {baseline_gate.STALENESS_THRESHOLD.days})"
            )

    if isinstance(env.commit, str) and env.commit.endswith("-dirty"):
        reasons.append(f"baseline captured against a dirty tree ({env.commit})")

    if isinstance(commits_behind, int) and commits_behind > baseline_gate.STALE_COMMIT_DISTANCE:
        reasons.append(
            f"baseline commit is {commits_behind} commits behind HEAD "
            f"(> {baseline_gate.STALE_COMMIT_DISTANCE})"
        )

    return reasons


def expire_stale_flaky_entries(
    baseline: dict,
    *,
    now: datetime | None = None,
    commits_behind: int | None = None,
) -> tuple[dict, list[str]]:
    """Drop ``flaky``-category entries when the artifact envelope is stale.

    A flaky allowance is only as good as the runs that observed it -- once
    the envelope is stale (per the shared :func:`staleness` definition), the
    entry stops suppressing failures instead of riding in the baseline
    forever.  Legacy artifacts (no envelope) have no freshness signal, so
    their entries are kept unchanged.

    Returns ``(new_baseline, expired_node_ids)``; never mutates the input.
    """
    if not isinstance(baseline, dict):
        return {}, []

    env = read_envelope(baseline)
    tests = baseline.get("tests")
    if env.is_legacy or not isinstance(tests, dict):
        return baseline, []

    if not staleness(env, now=now, commits_behind=commits_behind):
        return baseline, []

    kept: dict[str, dict] = {}
    expired: list[str] = []
    for node_id, record in tests.items():
        if isinstance(record, dict) and record.get("category") == CATEGORY_FLAKY:
            expired.append(node_id)
            continue
        kept[node_id] = record

    if not expired:
        return baseline, []

    logger.warning(
        "[baseline] expired %d stale flaky entr%s: %s",
        len(expired),
        "y" if len(expired) == 1 else "ies",
        ", ".join(sorted(expired)),
    )
    new_baseline = dict(baseline)
    new_baseline["tests"] = kept
    return new_baseline, sorted(expired)


def expire_stale_import_error_entries(
    baseline: dict,
    *,
    now: datetime | None = None,
    commits_behind: int | None = None,
) -> tuple[dict, list[str]]:
    """Drop ``import_error``-category entries past the fast-expiry window.

    An ``import_error`` is a whole-module outage, not an isolated flake -- it
    is either fixed within days or it silently masks every regression in that
    module (issue #2004 Task 4; #1933's month-riding entries). So it gets a
    much tighter window than the general :func:`staleness` rule: the envelope
    older than ``baseline_gate.IMPORT_ERROR_MAX_AGE`` OR more than
    ``baseline_gate.IMPORT_ERROR_MAX_COMMIT_DISTANCE`` commits behind HEAD
    expires the allowance, so the gate can never classify such a failure as
    pre-existing.  Thresholds live in the gate module (same lazy-import seam
    as :func:`staleness`); the artifact never carries them.
    ``commits_behind=None`` (git unavailable / unknown commit) skips the
    commit-distance trigger.  Legacy artifacts (no envelope) keep existing
    behavior -- there is no freshness signal to expire against.

    Returns ``(new_baseline, expired_node_ids)``; never mutates the input.
    """
    import scripts.baseline_gate as baseline_gate  # noqa: PLC0415 -- lazy: avoids circular import

    if not isinstance(baseline, dict):
        return {}, []

    env = read_envelope(baseline)
    tests = baseline.get("tests")
    if env.is_legacy or not isinstance(tests, dict):
        return baseline, []

    now = now or datetime.now(UTC)
    stale = False
    generated_at = parse_generated_at(env.generated_at)
    if generated_at is not None and (now - generated_at) > baseline_gate.IMPORT_ERROR_MAX_AGE:
        stale = True
    if (
        isinstance(commits_behind, int)
        and commits_behind > baseline_gate.IMPORT_ERROR_MAX_COMMIT_DISTANCE
    ):
        stale = True
    if not stale:
        return baseline, []

    kept: dict[str, dict] = {}
    expired: list[str] = []
    for node_id, record in tests.items():
        if isinstance(record, dict) and record.get("category") == CATEGORY_IMPORT_ERROR:
            expired.append(node_id)
            continue
        kept[node_id] = record

    if not expired:
        return baseline, []

    logger.warning(
        "[baseline] expired %d stale import_error entr%s: %s",
        len(expired),
        "y" if len(expired) == 1 else "ies",
        ", ".join(sorted(expired)),
    )
    new_baseline = dict(baseline)
    new_baseline["tests"] = kept
    return new_baseline, sorted(expired)
