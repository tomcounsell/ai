"""Job-level grouping for the dashboard (issue #2519).

A **Job** is a unit of work: a GitHub issue, a pull request, or a planned slug.
One Job is served by one or more **AgentSession** runs over its lifetime: an
original run, a recovery respawn, the local ``sdlc-local-{N}`` anchor, a dev
sub-session spawned by a PM. The dashboard's top-level list is Jobs; the runs
that served each one nest underneath.

This is presentation-level grouping. Nothing here is persisted and no schema
changes: the Job key is derived from fields already on ``AgentSession``. The
durable Job read-model is #2494's work.

Identity precedence
-------------------
1. ``issue:{repo}#{n}``: the strongest identity the model carries. ``n`` comes
   from the ``issue_number`` field, the ``issue_url``, a ``sdlc-{N}`` slug, or a
   ``sdlc-local-{N}`` session id, in that order.
2. ``pr:{repo}#{n}``: from ``pr_number`` or ``pr_url``.
3. ``slug:{project}:{slug}``: a named plan slug with no issue yet.
4. ``thread:{root}``: the root of the ``parent_agent_session_id`` chain. A
   session with no work-item identity of its own inherits the nearest ancestor
   that has one; if no ancestor has one either, the thread root is the Job.

Rule 4 is what keeps ad-hoc and conversational sessions on the board. Gating on
``slug`` is exactly how #1379 dropped conversational sessions from tracking, so
every session resolves to a key and lands in exactly one Job.

Why issue outranks slug
-----------------------
Real data from this machine, 2026-08-04: session ``0_1784286827622`` carries
``slug="sdlc-2137"`` while session ``sdlc-local-2137`` carries
``issue_number=2137``. Both served issue #2137. Four such pairs were present in
a 24-session population (#2137, #2143, #2147, #2158). Keying on slug first
leaves each pair as two unrelated rows, which is the problem this issue opens
with. Normalizing the ``sdlc-{N}`` slug shape back to its issue number collapses
them, because ``tools/valor_session.py`` mints that slug *from* the issue number.

Why the repo scopes the key
---------------------------
The same population held ``yudame/psyoptimal#665`` and ``yudame/cuttlefish#620``
under ``project_key="valor"`` (cross-repo SDLC work). Issue numbers collide
across repos, so the key carries a repo scope, resolved in this order:

1. The owner/repo parsed from the run's own ``issue_url`` / ``pr_url``. An issue
   key follows the issue URL first; a PR is opened against the repo its issue
   lives in, so a lone ``pr_url`` is still better evidence than a default.
2. The project's configured ``github.org``/``github.repo`` from projects.json.
   ``sdlc-2158`` carries an ``issue_url`` and ``sdlc-local-2158`` does not, and
   both must resolve to ``tomcounsell/ai``.
3. The scope a sibling run of the same work item recorded, when exactly one of
   them recorded a URL. projects.json is private and iCloud-synced, so a fresh
   machine or a CI checkout reads nothing and ``_load_project_configs`` caches
   ``{}`` for the TTL. Tier 3 keeps the two runs of one issue together on that
   machine anyway. Two runs naming two different repos leave the scope ambiguous,
   so nothing is adopted and each keeps its own.
4. The project key, then ``"unscoped"``.
"""

import logging
import re
import time
from collections import defaultdict

from pydantic import BaseModel

from ui.data.sdlc import (
    ACTIVE_STATUSES,
    PipelineProgress,
    StageState,
    _fetch_github_title,
    _load_project_configs,
    _number_from_github_url,
    best_timestamp,
    load_pipelines,
)

logger = logging.getLogger(__name__)

# `sdlc-{N}` is minted from an issue number by tools/valor_session.py::_auto_slug.
_SDLC_SLUG_RE = re.compile(r"^sdlc-(\d+)$")
# `sdlc-local-{N}` is the local /do-sdlc anchor session id
# (tools/sdlc_session_ensure.py).
_LOCAL_ANCHOR_RE = re.compile(r"^sdlc-local-(\d+)$")
# Owner/repo out of a GitHub issue or PR URL.
_GITHUB_REPO_RE = re.compile(r"https://github\.com/([^\s/]+/[^\s/]+)/(?:issues|pull)/\d+")

# Guard against a malformed parent chain looping forever.
_MAX_ANCESTOR_WALK = 32


class JobGroup(BaseModel):
    """One unit of work plus the AgentSession runs that served it.

    Fields:
        key: Stable identity for this Job within a render. Also the DOM handle
            the expand/collapse control keys off.
        kind: How the key was derived: "issue", "pr", "slug", or "thread".
        display_name: Human-facing label for the Job row.
        issue_number/pr_number/slug: The work item, when known.
        repo: owner/repo scope the issue and PR numbers belong to.
        project_key/project_name/project_metadata: Project of the newest run.
        status: The live run's status, or the newest run's outcome once every
            run is terminal.
        is_active: True while any run is still in flight.
        run_count/active_run_count: How many AgentSessions served this Job.
        is_stale/process_alive/unhealthy_reason/stall_advisory/
            stall_advisory_reason/last_evidence_at: Liveness of the
            representative run. See the ``_build_job`` note on why the row
            speaks for one run.
        stages: SDLC stages from the run that recorded them.
        started_at: Earliest run's start. last_activity_at: newest run's.
        total_cost_usd/turn_count/tool_call_count: Summed across runs.
        sessions: Every run, newest first. Feeds the drill-down and the modal.
    """

    key: str
    kind: str
    display_name: str
    full_display_name: str

    issue_number: int | None = None
    pr_number: int | None = None
    slug: str | None = None
    repo: str | None = None

    project_key: str | None = None
    project_name: str | None = None
    project_metadata: dict | None = None

    status: str | None = None
    is_active: bool = False
    run_count: int = 0
    active_run_count: int = 0

    # The run that speaks for the Job: the live one, or the newest outcome once
    # every run is terminal. Clicking the Job row opens this run's detail modal.
    primary_agent_session_id: str | None = None

    # Liveness of the representative run, so the Job row answers "is this
    # healthy" at a glance instead of one click down. Mirrors the run-row
    # signals in `_partials/session_row.html` field for field.
    is_stale: bool = False
    process_alive: bool | None = None
    unhealthy_reason: str | None = None
    stall_advisory: str | None = None
    stall_advisory_reason: str | None = None
    last_evidence_at: float | None = None

    stages: list[StageState] = []
    current_stage: str | None = None

    started_at: float | None = None
    last_activity_at: float | None = None
    completed_at: float | None = None

    total_cost_usd: float = 0.0
    turn_count: int = 0
    tool_call_count: int = 0

    issue_url: str | None = None
    plan_url: str | None = None
    pr_url: str | None = None

    sessions: list[PipelineProgress] = []

    @property
    def duration(self) -> float | None:
        """Wall time from the first run's start to the last sign of life."""
        if not self.started_at:
            return None
        end = self.last_activity_at if not self.is_active else time.time()
        return (end or time.time()) - self.started_at


def _project_repo(project_key: str | None) -> str | None:
    """Return ``owner/repo`` configured for ``project_key`` in projects.json."""
    if not project_key:
        return None
    project = _load_project_configs().get(project_key) or {}
    github = project.get("github") or {}
    org = github.get("org")
    repo = github.get("repo")
    if org and repo:
        return f"{org}/{repo}"
    return None


def _url_repo(*urls: str | None) -> str | None:
    """First ``owner/repo`` parsed out of the given GitHub URLs."""
    for url in urls:
        if url:
            match = _GITHUB_REPO_RE.search(url)
            if match:
                return match.group(1)
    return None


def _repo_scope(p: PipelineProgress, adopted: str | None = None) -> str:
    """Resolve the repo an issue/PR number on ``p`` belongs to.

    ``adopted`` is the scope a sibling run of the same work item recorded, used
    only when projects.json answers nothing. Two runs serving one issue must
    land on the same scope even when only one of them carries a URL and the
    project config is unreadable.
    """
    return (
        _url_repo(p.issue_url, p.pr_url)
        or _project_repo(p.project_key)
        or adopted
        or p.project_key
        or "unscoped"
    )


def _work_item(p: PipelineProgress) -> tuple[str, int] | None:
    """Return ``(kind, number)`` for the issue or PR ``p`` serves, or None.

    Scope-free on purpose: this is the identity two runs of one work item agree
    on before either of them has resolved a repo.
    """
    issue_number = _issue_number_for(p)
    if issue_number:
        return "issue", issue_number

    pr_number = p.pr_number or _number_from_github_url(p.pr_url)
    if pr_number:
        return "pr", pr_number

    return None


def _adoptable_scopes(
    pipelines: list[PipelineProgress],
) -> dict[tuple[str | None, str, int], str]:
    """Map each work item to the one repo scope its runs recorded.

    Keyed by ``(project_key, kind, number)`` so an issue number shared across
    projects stays apart. A work item whose runs name two different repos is
    ambiguous and is left out, which keeps two genuinely different repos'
    issue #665 as two Jobs.
    """
    candidates: dict[tuple[str | None, str, int], set[str]] = defaultdict(set)
    for p in pipelines:
        item = _work_item(p)
        scope = _url_repo(p.issue_url, p.pr_url)
        if item and scope:
            candidates[(p.project_key, *item)].add(scope)
    return {item: scopes.pop() for item, scopes in candidates.items() if len(scopes) == 1}


def _issue_number_for(p: PipelineProgress) -> int | None:
    """Issue number for ``p``, including the two SDLC naming conventions.

    The ``sdlc-{N}`` slug and the ``sdlc-local-{N}`` session id are both minted
    from an issue number, so a session carrying only one of those still belongs
    to that issue's Job.
    """
    if p.issue_number:
        return p.issue_number
    from_url = _number_from_github_url(p.issue_url)
    if from_url:
        return from_url
    for value, pattern in ((p.slug, _SDLC_SLUG_RE), (p.session_id, _LOCAL_ANCHOR_RE)):
        if value:
            match = pattern.match(value)
            if match:
                return int(match.group(1))
    return None


def self_job_key(
    p: PipelineProgress,
    adoptable_scopes: dict[tuple[str | None, str, int], str] | None = None,
) -> tuple[str, str] | None:
    """Return ``(key, kind)`` for the work item ``p`` serves, or None.

    None means this session carries no work-item identity of its own. The
    caller resolves it through the parent chain instead.

    ``adoptable_scopes`` comes from :func:`_adoptable_scopes` over the whole
    render set. Passing it lets a run with no URL of its own take the repo its
    siblings named when projects.json is unreadable.
    """
    item = _work_item(p)
    if item:
        kind, number = item
        adopted = (adoptable_scopes or {}).get((p.project_key, kind, number))
        return f"{kind}:{_repo_scope(p, adopted)}#{number}", kind

    if p.slug:
        return f"slug:{p.project_key or 'unscoped'}:{p.slug}", "slug"

    return None


def _thread_root(p: PipelineProgress, by_id: dict[str, PipelineProgress]) -> str:
    """Walk to the furthest ancestor of ``p`` still present in the render set.

    An orphaned child (parent aged out of the retention window) resolves to its
    missing parent's id, so siblings orphaned together stay together.

    A cycle has no furthest ancestor, and the last node walked differs by where
    the walk started: ``a.parent=b, b.parent=a`` would answer ``b`` for ``a`` and
    ``a`` for ``b``, splitting one loop into two Jobs. Every member of a cycle
    sees the same set of ids, so the smallest one is the canonical root.
    """
    current = p
    seen = {current.agent_session_id}
    for _ in range(_MAX_ANCESTOR_WALK):
        parent_id = current.parent_agent_session_id
        if not parent_id:
            break
        if parent_id in seen:
            return min(seen)
        parent = by_id.get(parent_id)
        if parent is None:
            return parent_id
        seen.add(parent_id)
        current = parent
    return current.agent_session_id


def _resolve_job_key(
    p: PipelineProgress,
    by_id: dict[str, PipelineProgress],
    self_keys: dict[str, tuple[str, str] | None],
) -> tuple[str, str]:
    """Resolve the Job key for ``p``, inheriting from its ancestors.

    A dev sub-session spawned for planned work rarely carries the slug itself;
    it inherits the nearest ancestor that does. When no ancestor carries a work
    item either, the thread root becomes the Job so a parent and its children
    still read as one piece of work.
    """
    own = self_keys.get(p.agent_session_id)
    if own:
        return own

    current = p
    seen = {current.agent_session_id}
    for _ in range(_MAX_ANCESTOR_WALK):
        parent_id = current.parent_agent_session_id
        if not parent_id or parent_id in seen:
            break
        parent = by_id.get(parent_id)
        if parent is None:
            break
        inherited = self_keys.get(parent.agent_session_id)
        if inherited:
            return inherited
        seen.add(parent_id)
        current = parent

    return f"thread:{_thread_root(p, by_id)}", "thread"


# Job rows stay one line tall in a dense table. Issue titles run long, so they
# are clipped here the way PipelineProgress.display_name already clips message
# text; the full label rides along in `full_display_name` for the tooltip.
_DISPLAY_NAME_MAX = 72


def _job_display_name(runs: list[PipelineProgress], slug: str | None) -> str:
    """Label the Job row: slug, then the issue/PR title, then the newest run."""
    if slug:
        return slug
    for run in runs:
        for url in (run.issue_url, run.pr_url):
            if url:
                title = _fetch_github_title(url)
                if title:
                    return title
    return runs[0].display_name if runs else "unknown"


def _clip(name: str) -> str:
    return name if len(name) <= _DISPLAY_NAME_MAX else name[:_DISPLAY_NAME_MAX].rstrip() + "…"


def _scope_of(key: str, kind: str) -> str | None:
    """Read the repo scope back out of an issue/PR Job key.

    Taking it from the key rather than re-resolving it keeps the repo the row
    displays identical to the repo the runs were grouped by.
    """
    if kind in ("issue", "pr"):
        return key[len(kind) + 1 : key.rindex("#")]
    return None


def _build_job(key: str, kind: str, runs: list[PipelineProgress]) -> JobGroup:
    """Roll a Job's runs up into the row the dashboard renders.

    Totals (cost, turns, tool calls) sum across every run. Liveness is not
    summable: it is read off the **representative** run, the same run the Job's
    status, duration and click-through modal already speak for, so a row reads
    as one coherent statement rather than a mix of two runs' health. A Job with
    more than one live run carries an "N live" badge pointing the operator at
    the per-run rows.
    """
    runs = sorted(runs, key=best_timestamp, reverse=True)
    newest = runs[0]

    # Ledger anchors (#2042) are never in flight: `sdlc-tool session-ensure`
    # creates them to hold pipeline state, and they carry a non-terminal status
    # for their whole life with no subprocess behind it. Counting them as active
    # rendered dozens of phantom "running" Jobs that no reaper could ever clear,
    # since every worker loop already skips ledgers via `_is_ledger`.
    active_runs = [r for r in runs if r.status in ACTIVE_STATUSES and not r.is_ledger]
    # The live run speaks for the Job; once every run is terminal the newest
    # outcome does.
    representative = active_runs[0] if active_runs else newest

    staged = next((r for r in runs if r.stages), None)

    def _first(attr: str):
        return next((getattr(r, attr) for r in runs if getattr(r, attr)), None)

    starts = [r.started_at or r.created_at for r in runs if (r.started_at or r.created_at)]
    completions = [r.completed_at for r in runs if r.completed_at]
    name = _job_display_name(runs, _first("slug"))

    return JobGroup(
        key=key,
        kind=kind,
        display_name=_clip(name),
        full_display_name=name,
        issue_number=_first("issue_number") or _issue_number_for(newest),
        pr_number=_first("pr_number"),
        slug=_first("slug"),
        repo=_scope_of(key, kind) or _repo_scope(newest),
        project_key=newest.project_key,
        project_name=newest.project_name,
        project_metadata=newest.project_metadata,
        status=representative.status,
        primary_agent_session_id=representative.agent_session_id,
        is_stale=representative.is_stale,
        process_alive=representative.process_alive,
        unhealthy_reason=representative.unhealthy_reason,
        stall_advisory=representative.stall_advisory,
        stall_advisory_reason=representative.stall_advisory_reason,
        last_evidence_at=representative.last_evidence_at,
        is_active=bool(active_runs),
        run_count=len(runs),
        active_run_count=len(active_runs),
        stages=staged.stages if staged else [],
        current_stage=staged.current_stage if staged else None,
        started_at=min(starts) if starts else None,
        last_activity_at=best_timestamp(newest) or None,
        completed_at=max(completions) if completions and not active_runs else None,
        total_cost_usd=sum(r.total_cost_usd or 0.0 for r in runs),
        turn_count=sum(r.turn_count or 0 for r in runs),
        tool_call_count=sum(r.tool_call_count or 0 for r in runs),
        issue_url=_first("issue_url"),
        plan_url=_first("plan_url"),
        pr_url=_first("pr_url"),
        sessions=runs,
    )


def group_into_jobs(pipelines: list[PipelineProgress]) -> list[JobGroup]:
    """Collapse a flat list of sessions into Jobs.

    Every session in ``pipelines`` appears in exactly one returned Job. That is
    the acceptance criterion from #2519 and the regression guard against #1379:
    an ad-hoc session with no slug, no issue and no parent becomes a Job of one
    rather than disappearing.

    Jobs come back active first, each ordered by most recent activity.
    """
    if not pipelines:
        return []

    by_id = {p.agent_session_id: p for p in pipelines}
    adoptable = _adoptable_scopes(pipelines)
    self_keys = {p.agent_session_id: self_job_key(p, adoptable) for p in pipelines}

    grouped: dict[str, tuple[str, list[PipelineProgress]]] = {}
    for p in pipelines:
        key, kind = _resolve_job_key(p, by_id, self_keys)
        grouped.setdefault(key, (kind, []))[1].append(p)

    jobs = [_build_job(key, kind, runs) for key, (kind, runs) in grouped.items()]

    active = [j for j in jobs if j.is_active]
    inactive = [j for j in jobs if not j.is_active]
    # `started_at` breaks ties on `last_activity_at`. Any batch write over many
    # rows lands them all in the same second — and because `AgentSession.save()`
    # unconditionally restamps `updated_at`, which `best_timestamp` prefers, a
    # single sweep can flatten the primary key across the whole list. Without a
    # tiebreak the stable sort then falls back to Redis scan order, which reads
    # as no ordering at all.
    active.sort(key=lambda j: (j.last_activity_at or 0, j.started_at or 0), reverse=True)
    inactive.sort(key=lambda j: (j.last_activity_at or 0, j.started_at or 0), reverse=True)
    return active + inactive


def limit_jobs(jobs: list[JobGroup], limit: int = 15) -> list[JobGroup]:
    """Show every active Job, cap the settled ones at ``limit``.

    Mirrors ``get_all_sessions``: work in flight is never hidden behind a cap.
    """
    active = [j for j in jobs if j.is_active]
    inactive = [j for j in jobs if not j.is_active]
    return active + inactive[:limit]


def get_all_jobs(limit: int = 15) -> list[JobGroup]:
    """Top-level dashboard list: Jobs, newest activity first.

    Args:
        limit: Maximum number of settled Jobs to show. Active Jobs are uncapped.
    """
    return limit_jobs(group_into_jobs(load_pipelines()), limit)


__all__ = [
    "JobGroup",
    "get_all_jobs",
    "group_into_jobs",
    "limit_jobs",
    "self_job_key",
]
