"""Rollback drill for a proposed improvement release (#3218, lane 6).

A rollback plan written at proposal time is a claim. The drill executes it
against the proposed release in a throwaway git worktree, checks that the
tree comes back to ``base_revision`` on every declared surface and as a
whole, runs the plan's ``verify`` commands with a timeout, and writes a
record naming what it exercised and what it did not.

What a ``pass`` establishes: a full-range ``git revert --no-commit
<base_revision>..<candidate_ref>`` on the candidate branch, run inside a fresh
worktree, restores the tree to ``base_revision`` and the verify commands
succeed on that restored tree. What it does not establish is on the record
too, under ``not_exercised``: the fleet picking the revert up, production
traffic, and the ``-m 1`` merge-commit revert that ``lifecycle.rollback``
runs against ``origin/main`` after a merge. The two operations share this
module's step executor, restoration check, and declared surfaces, which is
why the drill stands as evidence for the real rollback.

Order of operations in :func:`run`:

1. Worktree: ``git worktree prune`` then ``git worktree add --detach <path>
   <candidate_ref>`` from ``repo``, with ``<path>`` under
   ``<retention root>/drills/<release id>/<timestamp>/``. Any other path is
   refused before anything is created (``DrillRefused("CHECKOUT_PATH")``).
2. Identity: the two SHAs and ``git diff --stat base..candidate``.
3. Pre-revert range checks, in this order, each a recorded ``fail`` that
   stops the drill before any revert: ``BASE_NOT_ANCESTOR``,
   ``MERGE_COMMITS_IN_RANGE``, ``UNDECLARED_SURFACE_CHANGED``.
4. ``git revert --no-commit <base>..<candidate>``; a conflict is
   ``revert_conflict`` with the unmerged paths.
5. Restoration: ``git diff --quiet <base> -- <surface>`` per surface, then
   ``git diff --quiet <base>`` over the whole tree.
6. Each ``verify`` command, ``shlex.split`` and run without a shell in the
   worktree, capped by ``TIMEOUTS.improvement_drill_verify_seconds``.
7. Restoration again. Verify commands are read-only over tracked files by
   contract; this final pass is the sequence-completed invariant that
   catches one that is not.
8. ``finally``: ``git worktree remove --force`` and ``git worktree prune``.

A drill never changes ``state``; it writes ``rollback_drill`` (the record,
as a JSON string, since a plain popoto ``Field`` stores ``str()`` of a dict)
and ``drill_log`` and saves the release. :func:`drill_record` and
:func:`read_drill_log` read them back from a queried row. Every argv is a list. Refs from the
release row are checked for an option shape before use and replaced by their
resolved SHAs for every later command; surfaces go through
:func:`denylist.normalize_surface` and are passed after ``--``.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from config.settings import settings
from models.verifying_artifact_store import _default_base_path
from tools.improvement_release.denylist import InvalidSurface, normalize_surface
from tools.improvement_release.runner import Runner, SubprocessRunner, run_step

logger = logging.getLogger(__name__)

#: Directory under the retention root that holds every drill worktree.
DRILLS_DIRNAME = "drills"

#: Timestamp format of a drill directory name; sortable and collision-free
#: at second resolution, with microseconds appended when two drills of one
#: release land in the same second.
_STAMP = "%Y%m%dT%H%M%SZ"
_STAMP_MICRO = "%Y%m%dT%H%M%S%fZ"

#: What a drill can and cannot prove. ``verify`` moves between the two lists
#: depending on whether the plan declared any verify command.
EXERCISED = ("range_checks", "revert", "tree_restoration", "verify")
NOT_EXERCISED = ("fleet_update", "production_traffic", "merge_commit_revert")

#: Default age past which ``sweep`` removes a drill worktree (Race 3).
SWEEP_AGE_SECONDS = 86400


class DrillRefused(Exception):  # noqa: N818 -- plan-mandated name (#3218)
    """The drill did not start. ``code`` names why.

    Codes: ``NOT_PROPOSED`` (the release is past proposal), ``CHECKOUT_PATH``
    (the worktree path is inside a git checkout or outside the retention
    root), ``BAD_REF`` (a ref shaped like a command-line option), ``BAD_SURFACE``
    (a surface the denylist cannot normalize), ``BAD_PLAN`` (a rollback plan
    that is not a JSON object), ``NO_REPO`` (no repository to drill from).
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _git_timeout() -> float:
    return settings.timeouts.git_subprocess_s


def _verify_timeout() -> float:
    return settings.timeouts.improvement_drill_verify_seconds


def _retention_root(root: str | Path | None) -> Path:
    return Path(root) if root is not None else Path(_default_base_path())


def drills_dir(*, root: str | Path | None = None) -> Path:
    """``<retention root>/drills``."""
    return _retention_root(root) / DRILLS_DIRNAME


def drill_root(
    release_id: str, *, root: str | Path | None = None, now: datetime | None = None
) -> Path:
    """``<retention root>/drills/<release id>/<timestamp>``; never created here."""
    stamp_at = now or datetime.now(UTC)
    stamp = stamp_at.strftime(_STAMP_MICRO if stamp_at.microsecond else _STAMP)
    return drills_dir(root=root) / str(release_id) / stamp


def _inside_git_checkout(path: Path) -> bool:
    """True when any ancestor of ``path`` (excluding ``path``) carries ``.git``."""
    for ancestor in path.parents:
        if (ancestor / ".git").exists():
            return True
    return False


def refuse_checkout_path(path: str | Path, *, root: str | Path | None = None) -> Path:
    """Refuse any path that is not a drill worktree slot under the retention root.

    Accepts ``<retention root>/drills/<release id>/<timestamp>`` exactly and
    refuses everything else: a path elsewhere on disk, the retention root
    itself, and any slot whose ancestors sit inside a git checkout. The
    accepted path is returned resolved.

    Raises:
        DrillRefused: ``CHECKOUT_PATH``.
    """
    candidate = Path(path).resolve()
    base = drills_dir(root=root).resolve()
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        raise DrillRefused("CHECKOUT_PATH", f"{candidate} is outside {base}") from None
    if len(relative.parts) != 2:
        raise DrillRefused("CHECKOUT_PATH", f"{candidate} is not a drill slot under {base}")
    if _inside_git_checkout(candidate):
        raise DrillRefused("CHECKOUT_PATH", f"{candidate} sits inside a git checkout")
    return candidate


def _check_ref(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DrillRefused("BAD_REF", f"{field} is empty")
    if value.startswith("-"):
        raise DrillRefused("BAD_REF", f"{field} {value!r} is shaped like an option")
    return value


def _json_field(value: object, code: str, field: str) -> object:
    """A row field as its parsed value.

    Popoto's plain ``Field`` stores whatever ``str()`` makes of a non-string,
    so list and dict fields on this model are written as JSON strings
    (``json.dumps``) and read back here; an already-parsed value passes
    through for callers holding the row before ``save()``.
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError as exc:
        raise DrillRefused(code, f"{field} is not JSON: {exc}") from exc


def _check_surfaces(surfaces: object) -> list[str]:
    surfaces = _json_field(surfaces, "BAD_SURFACE", "surfaces")
    if not isinstance(surfaces, list | tuple) or not surfaces:
        raise DrillRefused("BAD_SURFACE", "a release declares at least one surface")
    normalized = []
    for surface in surfaces:
        try:
            normalized.append(normalize_surface(surface))
        except InvalidSurface as exc:
            raise DrillRefused("BAD_SURFACE", str(exc)) from exc
    return normalized


def _verify_commands(rollback_plan: object) -> list[list[str]]:
    plan = _json_field(rollback_plan, "BAD_PLAN", "rollback_plan")
    if plan is None:
        plan = {}
    if not isinstance(plan, dict):
        raise DrillRefused("BAD_PLAN", "rollback_plan is not an object")
    commands = plan.get("verify") or []
    if isinstance(commands, str):
        commands = [commands]
    parsed: list[list[str]] = []
    for command in commands:
        argv = shlex.split(command) if isinstance(command, str) else [str(p) for p in command]
        if argv:
            parsed.append(argv)
    return parsed


def _resolve_repo(runner: Runner, repo: str | Path | None) -> str:
    if repo is not None:
        return str(Path(repo).resolve())
    result = runner(["git", "rev-parse", "--show-toplevel"], cwd=None, timeout=_git_timeout())
    top = result.stdout.strip()
    if result.returncode != 0 or not top:
        raise DrillRefused("NO_REPO", "cwd is not inside a git repository and no repo was given")
    return top


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def add_detached_worktree(
    runner: Runner, *, repo: str | Path, ref: str, path: str | Path, transcript: list[str]
) -> dict:
    """``git worktree prune`` then ``git worktree add --detach <path> <ref>`` from ``repo``.

    Returns the ``add`` step record. The caller checks its ``returncode``.
    """
    repo = str(repo)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    run_step(
        runner,
        ["git", "worktree", "prune"],
        cwd=repo,
        timeout=_git_timeout(),
        transcript=transcript,
        name="worktree_prune",
    )
    return run_step(
        runner,
        ["git", "worktree", "add", "--detach", str(path), ref],
        cwd=repo,
        timeout=_git_timeout(),
        transcript=transcript,
        name="worktree_add",
    )


def remove_worktree(
    runner: Runner,
    *,
    repo: str | Path,
    path: str | Path,
    transcript: list[str],
    root: str | Path | None = None,
) -> None:
    """``git worktree remove --force`` and ``git worktree prune``; never raises.

    A directory that survives the removal (a worktree of another repository,
    a slot git never registered) is deleted directly when it is a drill slot
    under the retention root, so the sweep converges on every kind of residue.
    """
    repo = str(repo)
    target = Path(path)
    try:
        run_step(
            runner,
            ["git", "worktree", "remove", "--force", str(target)],
            cwd=repo,
            timeout=_git_timeout(),
            transcript=transcript,
            name="worktree_remove",
        )
        run_step(
            runner,
            ["git", "worktree", "prune"],
            cwd=repo,
            timeout=_git_timeout(),
            transcript=transcript,
            name="worktree_prune",
        )
    except Exception as exc:  # the caller is already unwinding; log, never mask
        logger.warning("[drill] worktree removal raised for %s: %s", target, exc)
        transcript.append(f"[worktree removal raised: {exc}]")
    if target.exists():
        try:
            refuse_checkout_path(target, root=root)
        except DrillRefused as exc:
            transcript.append(f"[left {target} in place: {exc}]")
            return
        shutil.rmtree(target, ignore_errors=True)
        transcript.append(f"[removed {target} directly]")
    parent = target.parent
    if (
        parent.exists()
        and parent.resolve().parent == drills_dir(root=root).resolve()
        and not any(parent.iterdir())
    ):
        parent.rmdir()


def assert_restored(
    runner: Runner,
    *,
    worktree: str | Path,
    base_revision: str,
    surfaces: list[str],
    transcript: list[str],
) -> dict:
    """Per-surface then whole-tree ``git diff --quiet <base>``.

    Returns ``{"restored": bool, "differing": [paths]}``; the paths come from
    ``git diff --name-only <base>`` when any check is nonzero.
    """
    worktree = str(worktree)
    restored = True
    for surface in surfaces:
        step = run_step(
            runner,
            ["git", "diff", "--quiet", base_revision, "--", surface],
            cwd=worktree,
            timeout=_git_timeout(),
            transcript=transcript,
            name=f"restored[{surface}]",
        )
        if step["returncode"] != 0:
            restored = False
    whole = run_step(
        runner,
        ["git", "diff", "--quiet", base_revision],
        cwd=worktree,
        timeout=_git_timeout(),
        transcript=transcript,
        name="restored[tree]",
    )
    if whole["returncode"] != 0:
        restored = False
    differing: list[str] = []
    if not restored:
        listing = run_step(
            runner,
            ["git", "diff", "--name-only", base_revision],
            cwd=worktree,
            timeout=_git_timeout(),
            transcript=transcript,
            name="differing_paths",
        )
        differing = _lines(listing["stdout_tail"])
    return {"restored": restored, "differing": differing}


def _undeclared(changed: list[str], surfaces: list[str]) -> list[str]:
    return [
        p for p in changed if not any(p == s or p.startswith(s.rstrip("/") + "/") for s in surfaces)
    ]


class _Drill:
    """One drill's mutable state; ``run`` builds it and returns its record."""

    def __init__(
        self,
        release,
        *,
        runner: Runner,
        root: Path | None,
        repo: str,
        now: datetime,
        verify_commands: list[list[str]],
    ):
        self.release = release
        self.verify_commands = verify_commands
        self.runner = runner
        self.root = root
        self.repo = repo
        self.now = now
        self.transcript: list[str] = []
        self.steps: list[dict] = []
        self.record: dict = {
            "drilled_at": now.isoformat(),
            "base_revision": None,
            "candidate_ref": None,
            "worktree": None,
            "diff_stat": None,
            "steps": self.steps,
            "restored": None,
            "result": None,
            "reason": None,
            "paths": None,
            "exercised": [],
            "not_exercised": list(NOT_EXERCISED),
            "seconds": None,
        }

    def step(self, argv: list[str], *, cwd: str, name: str, timeout: float | None = None) -> dict:
        record = run_step(
            self.runner,
            argv,
            cwd=cwd,
            timeout=_git_timeout() if timeout is None else timeout,
            transcript=self.transcript,
            name=name,
        )
        self.steps.append(record)
        return record

    def fail(self, reason: str, **extra) -> dict:
        self.record["result"] = "fail"
        self.record["reason"] = reason
        self.record.update(extra)
        self.transcript.append(f"[drill fail: {reason}]")
        return self.record

    def exercised(self, name: str) -> None:
        if name not in self.record["exercised"]:
            self.record["exercised"].append(name)

    def execute(self, worktree: str, base_ref: str, candidate_ref: str, surfaces: list[str]):
        wt = worktree
        base = self.step(["git", "rev-parse", "--verify", base_ref], cwd=wt, name="rev_parse_base")
        cand = self.step(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=wt, name="rev_parse_candidate"
        )
        base_sha = base["stdout_tail"].strip()
        cand_sha = cand["stdout_tail"].strip()
        self.record["base_revision"] = base_sha
        self.record["candidate_ref"] = cand_sha
        if base["returncode"] != 0 or not base_sha:
            return self.fail("UNRESOLVED_REF", paths=[base_ref])
        if cand["returncode"] != 0 or not cand_sha:
            return self.fail("UNRESOLVED_REF", paths=[candidate_ref])
        stat = self.step(
            ["git", "diff", "--stat", f"{base_sha}..{cand_sha}"], cwd=wt, name="diff_stat"
        )
        self.record["diff_stat"] = stat["stdout_tail"]

        # 3. Pre-revert range checks, plan order. Each stops before any revert.
        self.exercised("range_checks")
        ancestry = self.step(
            ["git", "merge-base", "--is-ancestor", base_sha, cand_sha], cwd=wt, name="ancestry"
        )
        if ancestry["returncode"] != 0:
            return self.fail("BASE_NOT_ANCESTOR")
        merges = self.step(
            ["git", "rev-list", "--merges", f"{base_sha}..{cand_sha}"],
            cwd=wt,
            name="merges_in_range",
        )
        merge_shas = _lines(merges["stdout_tail"])
        if merge_shas:
            return self.fail("MERGE_COMMITS_IN_RANGE", merges=merge_shas)
        changed_step = self.step(
            ["git", "diff", "--name-only", base_sha, cand_sha], cwd=wt, name="changed_paths"
        )
        changed = _lines(changed_step["stdout_tail"])
        undeclared = _undeclared(changed, surfaces)
        if undeclared:
            return self.fail("UNDECLARED_SURFACE_CHANGED", paths=undeclared)

        # 4. The revert the plan declares.
        self.exercised("revert")
        revert = self.step(
            ["git", "revert", "--no-commit", f"{base_sha}..{cand_sha}"], cwd=wt, name="revert"
        )
        if revert["returncode"] != 0:
            unmerged = self.step(
                ["git", "diff", "--name-only", "--diff-filter=U"], cwd=wt, name="unmerged_paths"
            )
            return self.fail("revert_conflict", paths=_lines(unmerged["stdout_tail"]))

        # 5. Restoration on every declared surface and the whole tree.
        self.exercised("tree_restoration")
        restoration = self.restoration(wt, base_sha, surfaces)
        if not restoration["restored"]:
            return self.fail("residue", paths=restoration["differing"])

        # 6. The plan's verify commands, in the restored worktree.
        commands = self.verify_commands
        if commands:
            self.exercised("verify")
            timeout = _verify_timeout()
            for index, argv in enumerate(commands):
                step = self.step(argv, cwd=wt, name=f"verify[{index}]", timeout=timeout)
                if step["timed_out"]:
                    return self.fail("verify_timeout", timeout=timeout, command=argv)
                if step["returncode"] != 0:
                    return self.fail("verify_failed", command=argv)
            # 7. The final invariant: verify left the restored tree alone.
            restoration = self.restoration(wt, base_sha, surfaces)
            if not restoration["restored"]:
                return self.fail("residue", paths=restoration["differing"])
        else:
            self.record["not_exercised"].append("verify")

        self.record["result"] = "pass"
        return self.record

    def restoration(self, worktree: str, base_sha: str, surfaces: list[str]) -> dict:
        before = len(self.transcript)
        result = assert_restored(
            self.runner,
            worktree=worktree,
            base_revision=base_sha,
            surfaces=surfaces,
            transcript=self.transcript,
        )
        self.steps.append(
            {
                "name": "restoration",
                "argv": ["git", "diff", "--quiet", base_sha],
                "returncode": 0 if result["restored"] else 1,
                "seconds": 0.0,
                "timed_out": False,
                "stdout_tail": "\n".join(result["differing"]),
                "stderr_tail": "",
                "checks": len(self.transcript) - before,
            }
        )
        self.record["restored"] = result["restored"]
        return result


def run(
    release,
    *,
    runner: Runner | None = None,
    root: str | Path | None = None,
    repo: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Drill the rollback plan of a proposed release; return the record written.

    Args:
        release: An ``ImprovementRelease`` in state ``proposed`` (or any object
            with the same fields and a ``save()``).
        runner: Command runner; ``SubprocessRunner()`` by default.
        root: Retention root; ``POPOTO_IMPROVEMENT_CONTENT_PATH`` by default.
        repo: Repository to drill from; the toplevel of the cwd by default.
        now: Clock, for the timestamped slot and ``drilled_at``.

    Raises:
        DrillRefused: before anything is created, for a release that is not
            ``proposed``, an option-shaped ref, an invalid surface, a rollback
            plan that is not a JSON object, a missing repo, or a worktree slot
            that is not under the retention root.
    """
    if getattr(release, "state", None) != "proposed":
        raise DrillRefused("NOT_PROPOSED", f"release {release.id} is {release.state!r}")
    candidate_ref = _check_ref(release.candidate_ref, "candidate_ref")
    base_ref = _check_ref(release.base_revision, "base_revision")
    surfaces = _check_surfaces(release.surfaces)
    verify_commands = _verify_commands(release.rollback_plan)
    runner = runner or SubprocessRunner()
    repo_path = _resolve_repo(runner, repo)
    stamp_at = now or datetime.now(UTC)
    slot = drill_root(release.id, root=root, now=stamp_at)
    worktree = refuse_checkout_path(slot, root=root)

    started = time.monotonic()
    drill = _Drill(
        release,
        runner=runner,
        root=root,
        repo=repo_path,
        now=stamp_at,
        verify_commands=verify_commands,
    )
    drill.record["worktree"] = str(worktree)
    try:
        add = add_detached_worktree(
            runner, repo=repo_path, ref=candidate_ref, path=worktree, transcript=drill.transcript
        )
        drill.steps.append(add)
        if add["returncode"] != 0:
            drill.fail("worktree_add_failed")
        else:
            drill.execute(str(worktree), base_ref, candidate_ref, surfaces)
    finally:
        remove_worktree(
            runner, repo=repo_path, path=worktree, transcript=drill.transcript, root=root
        )
        drill.record["seconds"] = round(time.monotonic() - started, 3)
        # A plain Field stores str() of a dict; the record is written as JSON
        # so a reader gets it back with json.loads (the row convention).
        release.rollback_drill = json.dumps(drill.record, sort_keys=True)
        release.drill_log = "\n".join(drill.transcript)
        release.save()
    return drill.record


def drill_record(release) -> dict | None:
    """The ``rollback_drill`` record of a row, or ``None`` when no drill ran."""
    value = getattr(release, "rollback_drill", None)
    if value is None or value == "":
        return None
    parsed = _json_field(value, "BAD_DRILL", "rollback_drill")
    return parsed if isinstance(parsed, dict) else None


def read_drill_log(release) -> str | None:
    """The ``drill_log`` transcript of a row, verified on load.

    popoto hydrates a queried row lazily, so the attribute reads back as its
    ``$CF:`` store reference; resolving it through the field's own store is
    what re-hashes the bytes, and a corrupted transcript raises
    ``ArtifactIntegrityError`` here instead of loading as something else.
    """
    value = getattr(release, "drill_log", None)
    if isinstance(value, str) and value.startswith("$CF:"):
        store = release._meta.fields["drill_log"].store
        return store.load(value).decode("utf-8")
    return value


def _slot_age(slot: Path, now: datetime) -> float:
    """Seconds since the slot was created, from its name; mtime as fallback."""
    for fmt in (_STAMP_MICRO, _STAMP):
        try:
            stamped = datetime.strptime(slot.name, fmt).replace(tzinfo=UTC)
            return (now - stamped).total_seconds()
        except ValueError:
            continue
    return now.timestamp() - slot.stat().st_mtime


def sweep(
    *,
    root: str | Path | None = None,
    older_than_seconds: float = SWEEP_AGE_SECONDS,
    runner: Runner | None = None,
    repo: str | Path | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Remove drill worktrees older than ``older_than_seconds``; return their paths.

    The drill's ``finally`` removes its own worktree on every exception path;
    only a hard kill leaves one behind (Race 3). Slots are aged by the
    timestamp in their name, so a stale slot from a crashed drill is swept
    even when the filesystem later touched it. With ``repo`` unset and no
    repository at the cwd, slots are deleted directly.
    """
    base = drills_dir(root=root)
    if not base.exists():
        return []
    now = now or datetime.now(UTC)
    runner = runner or SubprocessRunner()
    try:
        repo_path: str | None = _resolve_repo(runner, repo)
    except DrillRefused:
        repo_path = None
    removed: list[str] = []
    for release_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for slot in sorted(p for p in release_dir.iterdir() if p.is_dir()):
            if _slot_age(slot, now) < older_than_seconds:
                continue
            transcript: list[str] = []
            if repo_path is not None:
                remove_worktree(runner, repo=repo_path, path=slot, transcript=transcript, root=root)
            if slot.exists():
                refuse_checkout_path(slot, root=root)
                shutil.rmtree(slot, ignore_errors=True)
            logger.info("[drill] swept stale worktree %s", slot)
            removed.append(str(slot))
        if release_dir.exists() and not any(release_dir.iterdir()):
            release_dir.rmdir()
    return removed


__all__ = [
    "EXERCISED",
    "NOT_EXERCISED",
    "SWEEP_AGE_SECONDS",
    "DrillRefused",
    "add_detached_worktree",
    "assert_restored",
    "drill_record",
    "drill_root",
    "drills_dir",
    "read_drill_log",
    "refuse_checkout_path",
    "remove_worktree",
    "run",
    "sweep",
]
