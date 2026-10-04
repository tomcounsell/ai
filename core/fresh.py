"""Fresh sessions: one turn that never resumes and never reads the working
session. Critique, review, and docs run here.

A fresh session gets:

- a checkout of its own under `<task>/checks/<stage>-<key>/repo`, made from
  the kernel mirror: for critique (and review) a *blind* checkout holding
  only two commits the kernel made, the base's tree and the plan's (or the
  candidate's) tree, so no builder commit message or intermediate commit
  exists in it; for docs a real clone of the candidate through the pack
  protocol, sharing no object file with the mirror, whose commits the
  kernel fetches into the mirror and cuts to the prefix it keeps;
- for review, a checkout the kernel set up: the seed cache cloned in, the
  spec's setup run under the check profile, and the turn inside fresh
  Postgres and Redis on the task's ports (`workspace.check_services`), the
  task's own stopped meanwhile;
- inputs as files under `.valor/inputs/`, written by the kernel from ledger
  rows: the request verbatim, Tom's answers and feedback with provenance,
  the diff against the base, and the stage's own. A plan or candidate whose
  tree holds `.valor` is refused, `.valor` must not exist before the kernel
  makes it, and every input is written relative to a descriptor with no
  link followed and no file overwritten, so nothing committed can redirect
  a write or plant a verdict;
- its own sandbox profile, `TMPDIR`, and Claude Code config directory, with
  the whole work directory, `/private/tmp`, `/private/var/tmp`,
  `/private/var/folders`, and the user's Claude Code state denied, so it
  reads nothing the builder wrote in the paths the kernel names for the
  builder (its clone, caches, `TMPDIR`, and Claude Code state); what the
  builder writes elsewhere in the user's home is outside this (harnesses.md,
  Known openings). Critique and docs get no database credential and no
  service port;
- a Brief carrying the stage file and the verdict channel
  (`skills/sdlc/verdict.md`) instead of the working session's: no question,
  no effect;
- one turn, recorded with `fresh: true`, whose session is never resumed.

Critique's and docs' verdict is `.valor/verdict.json`, read without
following links or blocking (`workspace.read_verdict`). Review's is the
session's final message, read from the turn's result on the harness's
stdout (`final_verdict`): the reviewer runs the candidate's code, and a
process that code leaves running can rewrite any file in the checkout to
the end of the turn, but cannot write that pipe. The kernel validates the
verdict and writes the verdict row. A turn that fails, is stopped, or leaves no valid verdict
writes no verdict: the runner returns `failed` (or `stopped`), and the next
run starts the stage again.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core import checks, db, git, judgement_sites, ledger, machine, runs, slot, tasks, verdicts, workspace
from core.machine import State
from core.settings import resolve_seat

# Builds one fresh turn: (prompt, checkout, model, harness settings, harness
# name) -> builder.
FreshFor = Callable[[str, str, str, dict[str, Any], str], Callable[[str, str, str], runs.TurnCommand]]

SEATS = {"critique": "frontier", "docs": "frontier", "review": "reviewer"}


def _answers(rows: list[dict]) -> str:
    """Every question, Tom's answer, and his feedback, in ledger order, with
    who wrote each."""
    out: list[str] = []
    questions: dict[str, str] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] == "question.asked":
            questions[p["question_id"]] = p.get("text") or ""
        elif r["type"] == "question.answered":
            who = p.get("provenance") or {}
            out.append(
                f"## Question\n\n{questions.get(p.get('question_id'), '')}\n\n"
                f"## Answer (by {who.get('by')}, role played: {who.get('role_played')})\n\n{p.get('text')}"
            )
        elif r["type"] == "feedback.given":
            who = p.get("provenance") or {}
            out.append(
                f"## Feedback (by {who.get('by')}, role played: {who.get('role_played')})\n\n{p.get('text')}"
            )
    return "\n\n".join(out) or "No questions were asked and no feedback was given."


def _quoted(value: Any) -> str:
    """A value a turn chose, as JSON: quoted, newlines escaped."""
    return json.dumps(value, ensure_ascii=False)


def critique_inputs(
    checkout: Path, rows: list[dict], f: machine.Fold, b: tasks.Brief, diff: str
) -> list[str]:
    plan = f.plan or {}
    critiques = [
        r["payload"] for r in rows if r["type"] == "critique.decided"
    ]  # earlier rounds, so a second round can see whether the first round's findings were met
    files = {
        "request.md": b.instruction,
        "answers.md": _answers(rows),
        "diff.patch": diff,
        "plan.md": (
            f"The plan file: {_quoted(plan.get('path'))}\n"
            f"Its stakes: {_quoted(plan.get('stakes'))}\n"
            f"Critique rounds: {plan.get('critique_rounds')}; review rounds: {plan.get('review_rounds')}\n"
            f"Scope additions: {_quoted(plan.get('scope') or [])}\n"
        ),
        "critiques.md": "\n\n".join(
            f"## Critique {i + 1}: {c.get('verdict')}\n\n"
            + "\n".join(f"- [{x.get('kind')}] {x.get('text')}" for x in c.get("findings") or [])
            for i, c in enumerate(critiques)
        )
        or "No earlier critique.",
    }
    workspace.write_inputs(checkout, files)
    return list(files)


def prompt(files: list[str]) -> str:
    return "# Inputs\n\n" + "\n".join(f"- `.valor/inputs/{n}`" for n in files)


class Malformed(ValueError):
    """A verdict file the kernel will not record."""


def _verdict_fields(data: dict[str, Any]) -> tuple[str, list, dict[str, int]]:
    verdict = data.get("verdict")
    findings = data.get("findings") or []
    raised = data.get("raise") or {}
    if not isinstance(verdict, str):
        raise Malformed("verdict.json names no verdict")
    if not isinstance(findings, list) or not all(isinstance(x, (dict, str)) for x in findings):
        raise Malformed("findings is not a list of findings")
    for x in findings:
        if isinstance(x, dict) and not isinstance(x.get("text"), str):
            raise Malformed("a finding has no text")
    if verdict not in machine.VERDICTS[State.CRITIQUE]:
        raise Malformed(f"the verdict is not one of {sorted(machine.VERDICTS[State.CRITIQUE])}")
    if not isinstance(raised, dict):
        raise Malformed("raise is not an object")
    for k, v in raised.items():
        if (
            k not in ("critique_rounds", "review_rounds")
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v not in machine.ROUNDS
        ):
            raise Malformed("each count raised is critique_rounds or review_rounds, 0 to 2")
    return verdict, findings, raised


def critique_runner(fresh_for: FreshFor, model: str | None = None, seat: str | None = None):
    """The runner for `State.CRITIQUE`, at `seat` (default: critique's own,
    which names a harness and a model). `model` overrides the seat's pinned
    model (the live test runs a light model to keep its spend small)."""

    model_ = model

    async def run(ctx) -> dict[str, Any]:
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
            f = machine.fold(rows)
            if f.state is State.STOPPED:
                return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
            if f.state is not State.CRITIQUE:
                return {"status": "moved"}
            b = await tasks.brief(conn, ctx.task_id)
            state = await tasks.status(conn, ctx.task_id)
        if not b.mirror:
            return {
                "status": "failed",
                "state": state,
                "turn": {"result": "a fresh session runs only in a workspace the kernel provisioned"},
            }
        lay = workspace.Layout(Path(b.mirror).parent)
        plan_sha = f.plan["sha256"]
        check_dir = workspace.fresh_dir(lay.checks / f"critique-{plan_sha[:12]}")
        checkout = check_dir / "repo"
        try:
            made = workspace.blind_checkout(b.mirror, b.base_sha, f.plan["commit"], checkout)
            diff = git.trusted(checkout, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                               made["base"], made["candidate"])  # fmt: skip
        except workspace.ValorInTree as exc:
            # The plan commit's own tree: no rerun would ever check it out,
            # so the plan goes back with the reason, as any revise does.
            if not await ctx.alive():
                return {"status": "lock lost"}
            try:
                async with await db.connect(ctx.dsn) as conn:
                    await verdicts.record_critique(
                        conn,
                        ctx.task_id,
                        "revise",
                        findings=[{"kind": "commit", "text": f"no critique checkout: {exc}"}],
                        leg="kernel",
                        plan_sha256=plan_sha,
                    )
            except verdicts.VerdictRefused as refused:
                return {"status": "failed", "state": state, "turn": {"result": f"verdict refused: {refused}"}}
            return {"status": "moved"}
        except git.GitError as exc:
            return {"status": "failed", "state": state, "turn": {"result": f"critique checkout: {exc}"}}
        try:
            files = critique_inputs(checkout, rows, f, b, diff)
        except (OSError, ValueError) as exc:
            return {"status": "failed", "state": state, "turn": {"result": f"critique inputs: {exc}"}}
        harness = workspace.check_harness(lay, check_dir, [], b.harness.get("env", {}), services=False)
        harness_name, seat_model = resolve_seat(seat or SEATS["critique"])
        model = model_ or seat_model
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            ended = await runs.run_turn(
                ctx.gateway,
                ctx.task_id,
                fresh_for(prompt(files), str(checkout), model, harness, harness_name),
                dsn=ctx.dsn,
                state=State.CRITIQUE.value,
                fresh="critique",
            )
        except tasks.TaskStopped:
            return {"status": "stopped"}
        async with await db.connect(ctx.dsn) as conn:
            now = await tasks.status(conn, ctx.task_id)
        if ended["outcome"] == "stopped":
            return {"status": "stopped", "state": now, "turn": ended}
        if ended["outcome"] != "done" or ended["result"].get("is_error"):
            return {"status": "failed", "state": now, "turn": ended}
        data, why = await asyncio.to_thread(
            workspace.read_verdict, lay.checks, check_dir.name, ended["turn_id"]
        )
        if data is None:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"no verdict: {why}"}}
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            verdict, findings, raised = _verdict_fields(data)
            async with await db.connect(ctx.dsn) as conn:
                await verdicts.record_critique(
                    conn,
                    ctx.task_id,
                    verdict,
                    findings=findings,
                    raised=raised,
                    leg="session",
                    model=model,
                    usd_micros=int(ended.get("metered_usd_micros") or 0),
                    turn_id=ended["turn_id"],
                    plan_sha256=plan_sha,
                )
        except (ValueError, verdicts.VerdictRefused) as exc:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"verdict refused: {exc}"}}
        return {"status": "moved"}

    return run


# -- docs ------------------------------------------------------------------------------------

DOCS_KEPT = "docs.kept"
DOCS_IDENTITY = {
    "GIT_AUTHOR_NAME": "Valor docs",
    "GIT_AUTHOR_EMAIL": "docs@valor.invalid",
    "GIT_COMMITTER_NAME": "Valor docs",
    "GIT_COMMITTER_EMAIL": "docs@valor.invalid",
}
REGULAR = ("100644", "100755")
NO_FILE = "000000"


def docs_clone(mirror: str | Path, candidate: str, dest: Path, key: str) -> None:
    """A real clone of the candidate from the mirror at `dest`, made through
    the pack protocol (`file://`), so it shares no object file with the
    mirror. A temporary branch names the candidate in the mirror for the
    clone and is deleted after it. A candidate whose tree holds `.valor` is
    refused (`workspace.ValorInTree`)."""
    mirror = Path(mirror)
    if workspace.tree_has_valor(mirror, candidate, trusted=True):
        raise workspace.ValorInTree(candidate)
    ref = f"refs/heads/valor-docs/{key}"
    git.trusted(mirror, "update-ref", ref, candidate)
    try:
        git.trusted(dest.parent, "clone", "-q", "--no-tags", "--single-branch", "--branch",
                    f"valor-docs/{key}", f"file://{mirror}", str(dest))  # fmt: skip
    finally:
        git.trusted(mirror, "update-ref", "-d", ref)
    git.trusted(dest, "remote", "remove", "origin")
    with (dest / ".git" / "info" / "exclude").open("a") as f:
        f.write(".valor/\n")


def _entries(mirror: str | Path, sha: str) -> list[tuple[str, str, str]]:
    """A commit's entries against its parent, from raw `diff-tree -r`
    output: (old mode, new mode, path)."""
    raw = git.trusted(mirror, "diff-tree", "--no-renames", "--no-commit-id", "-r", "-z", sha)
    fields = raw.split("\0")
    out = []
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta.startswith(":"):
            continue
        old_mode, new_mode = meta[1:].split()[:2]
        out.append((old_mode, new_mode, path))
    return out


def _why_dropped(mirror: str | Path, sha: str) -> list[str]:
    """What makes a docs commit one the kernel will not keep: a path no docs
    commit may touch, a `.valor` entry, or an entry that is not a regular
    file (a symlink or a gitlink). Empty when it is kept."""
    bad = []
    for old_mode, new_mode, path in _entries(mirror, sha):
        regular = new_mode in REGULAR or (new_mode == NO_FILE and old_mode in REGULAR)
        if not machine.is_doc_path(path):
            bad.append(f"{path} (not a docs path)")
        elif not regular:
            bad.append(f"{path} (mode {new_mode if new_mode != NO_FILE else old_mode}, not a regular file)")
    if workspace.tree_has_valor(mirror, sha, trusted=True):
        bad.append(".valor")
    return bad


def kept_prefix(mirror: str | Path, candidate: str, head: str) -> tuple[str, list[dict], str | None]:
    """The docs commits the kernel keeps: the longest run from the candidate
    toward `head` in which every commit passes `_why_dropped`. Returns (the
    kept head, the dropped commits with their paths, why nothing was
    considered). A head that does not descend from the candidate, or a run
    holding a merge commit, keeps nothing."""
    if not git.is_ancestor(mirror, candidate, head):
        return (
            candidate,
            [],
            f"the docs head {head[:12]} does not descend from the candidate {candidate[:12]}",
        )
    if git.merges_between(mirror, candidate, head):
        return candidate, [], "the docs commits hold a merge commit"
    commits = git.trusted(mirror, "rev-list", "--reverse", f"{candidate}..{head}").split()
    kept = candidate
    for i, sha in enumerate(commits):
        bad = _why_dropped(mirror, sha)
        if bad:
            dropped = [{"commit": sha, "paths": bad}]
            dropped += [
                {"commit": s, "paths": [p for _o, _n, p in _entries(mirror, s)]} for s in commits[i + 1 :]
            ]
            return kept, dropped, None
        kept = sha
    return kept, [], None


def _docs_fields(data: dict[str, Any]) -> tuple[str, list, str | None]:
    verdict = data.get("verdict")
    findings = data.get("findings") or []
    head = data.get("head")
    if verdict not in machine.VERDICTS[machine.Check.DOCS]:
        raise Malformed(f"the verdict is not one of {sorted(machine.VERDICTS[machine.Check.DOCS])}")
    if not isinstance(findings, list) or not all(isinstance(x, (dict, str)) for x in findings):
        raise Malformed("findings is not a list of findings")
    for x in findings:
        if isinstance(x, dict) and not isinstance(x.get("text"), str):
            raise Malformed("a finding has no text")
    if head is not None and not (isinstance(head, str) and verdicts.re_sha(head)):
        raise Malformed("the head is not a full commit id")
    return verdict, findings, head


def docs_inputs(checkout: Path, rows: list[dict], f: machine.Fold, b: tasks.Brief, mirror: str) -> list[str]:
    plan = f.plan or {}
    c = f.candidate
    plan_text = ""
    if plan.get("commit") and plan.get("path"):
        try:
            plan_text = git.trusted(mirror, "cat-file", "blob", f"{plan['commit']}:{plan['path']}")
        except git.GitError:
            plan_text = ""
    files = {
        "request.md": b.instruction,
        "plan.md": plan_text or "No plan file.",
        "diff.patch": git.trusted(
            mirror, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", b.base_sha, c.sha
        ),
    }
    earlier = [r["payload"] for r in rows if r["type"] == "docs.decided"]
    if earlier:
        last = earlier[-1]
        older, newer = last["candidate"]["sha"], last.get("head")
        if newer and newer != older:
            try:
                files["previous-docs.patch"] = git.trusted(
                    mirror, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", older, newer
                )
            except git.GitError:
                pass
    workspace.write_inputs(checkout, files)
    return list(files)


def _kept_row(rows: list[dict], candidate: str, mirror: str) -> dict | None:
    """The last `docs.kept` row for this candidate whose ref still resolves
    in the mirror: the turn already ran and its commits were kept."""
    for r in reversed(rows):
        p = r["payload"]
        if r["type"] != DOCS_KEPT or p.get("candidate") != candidate:
            continue
        try:
            at = git.trusted(mirror, "rev-parse", "--verify", "--quiet", f"refs/valor/docs/{p['turn_id']}")
        except git.GitError:
            return None
        return p if at == p["kept"] else None
    return None


def docs_runner(fresh_for: FreshFor, port, model: str | None = None):
    """The runner for `Check.DOCS`: one fresh docs turn in a real clone of
    the candidate, the commits it made fetched into the mirror and cut to
    the prefix the kernel keeps, governance over the kept diff, and the
    verdict the kernel computes from what was kept (the session's own
    `changes` stands). A `docs.kept` row for the candidate is reused, so a
    run that died after the turn asks only governance again. The check
    holds the turn slot throughout; its docs turn takes it again as a
    no-op."""

    model_ = model

    async def run(ctx) -> dict[str, Any]:
        async with slot.held(ctx.task_id, ctx.dsn):
            return await _run(ctx)

    async def _run(ctx) -> dict[str, Any]:
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
            f = machine.fold(rows)
            if f.state is State.STOPPED:
                return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
            if f.state is not State.CHECKS or machine.Check.DOCS in f.checks:
                return {"status": "moved"}
            b = await tasks.brief(conn, ctx.task_id)
            state = await tasks.status(conn, ctx.task_id)
        if not b.mirror:
            return {
                "status": "failed",
                "state": state,
                "turn": {"result": "a fresh session runs only in a workspace the kernel provisioned"},
            }
        c = f.candidate
        kept_row = _kept_row(rows, c.sha, b.mirror)
        if kept_row is None:
            harness_name, seat_model = resolve_seat(SEATS["docs"])
            out = await _docs_turn(ctx, fresh_for, rows, f, b, state, model_ or seat_model, harness_name)
            if "status" in out:
                return out
            kept_row = out["kept"]
        kept = kept_row["kept"]
        try:
            ids = (
                await judgement_sites.governance(port, ctx.dsn, ctx.task_id, c.sha, kept)
                if kept != c.sha
                else []
            )
        except tasks.TaskStopped:
            return {"status": "stopped"}
        dropped = kept_row["dropped"]
        changes = bool(dropped or kept_row["refused"] or kept_row["verdict"] == "changes")
        verdict = "changes" if changes else ("updated" if kept != c.sha else "no_change")
        findings = list(kept_row["findings"])
        if kept_row["refused"]:
            findings.append({"kind": "changes", "text": f"no docs commit kept: {kept_row['refused']}"})
        findings += [
            {"kind": "changes", "text": f"docs commit {d['commit'][:12]} dropped: {', '.join(d['paths'])}"}
            for d in dropped
        ]
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            async with await db.connect(ctx.dsn) as conn:
                await verdicts.record_check(
                    conn, ctx.task_id, machine.Check.DOCS, verdict, head=kept, governance_from=ids,
                    findings=findings, dropped=dropped, leg="session", model=kept_row["model"],
                    usd_micros=kept_row["usd_micros"], turn_id=kept_row["turn_id"],
                )  # fmt: skip
        except verdicts.VerdictRefused as exc:
            async with await db.connect(ctx.dsn) as conn:
                if await tasks.is_stopped(conn, ctx.task_id):
                    return {"status": "stopped"}
            return {"status": "failed", "state": state, "turn": {"result": f"verdict refused: {exc}"}}
        return {"status": "moved"}

    return run


async def _docs_turn(
    ctx, fresh_for: FreshFor, rows, f: machine.Fold, b: tasks.Brief, state, model: str, harness_name: str
) -> dict[str, Any]:
    """The docs turn and what the kernel keeps of its commits, written as a
    `docs.kept` row. Returns {"kept": payload}, or the runner's result when
    there is nothing to keep (a failed or stopped turn, no valid verdict)."""
    c = f.candidate
    lay = workspace.Layout(Path(b.mirror).parent)
    check_dir = workspace.fresh_dir(lay.checks / f"docs-{c.sha[:12]}")
    checkout = check_dir / "repo"
    try:
        docs_clone(b.mirror, c.sha, checkout, f"{ctx.task_id}-{c.sha[:12]}")
        files = docs_inputs(checkout, rows, f, b, b.mirror)
    except workspace.ValorInTree as exc:
        # The candidate's own tree: no rerun would ever clone it, so docs
        # says `changes` with the reason and the join moves on.
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            async with await db.connect(ctx.dsn) as conn:
                await verdicts.record_check(
                    conn, ctx.task_id, machine.Check.DOCS, "changes", head=c.sha, leg="kernel",
                    findings=[{"kind": "commit", "text": f"no docs clone: {exc}"}],
                )  # fmt: skip
        except verdicts.VerdictRefused as refused:
            return {"status": "failed", "state": state, "turn": {"result": f"verdict refused: {refused}"}}
        return {"status": "moved"}
    except (git.GitError, OSError, ValueError) as exc:
        return {"status": "failed", "state": state, "turn": {"result": f"docs checkout: {exc}"}}
    env = {**b.harness.get("env", {}), **DOCS_IDENTITY}
    harness = workspace.check_harness(lay, check_dir, [], env, services=False)
    harness["env"].update(DOCS_IDENTITY)
    if not await ctx.alive():
        return {"status": "lock lost"}
    try:
        ended = await runs.run_turn(
            ctx.gateway,
            ctx.task_id,
            fresh_for(prompt(files), str(checkout), model, harness, harness_name),
            dsn=ctx.dsn,
            state=State.CHECKS.value,
            fresh="docs",
        )
    except tasks.TaskStopped:
        return {"status": "stopped"}
    async with await db.connect(ctx.dsn) as conn:
        now = await tasks.status(conn, ctx.task_id)
    if ended["outcome"] == "stopped":
        return {"status": "stopped", "state": now, "turn": ended}
    if ended["outcome"] != "done" or ended["result"].get("is_error"):
        return {"status": "failed", "state": now, "turn": ended}
    data, why = await asyncio.to_thread(workspace.read_verdict, lay.checks, check_dir.name, ended["turn_id"])
    if data is None:
        return {"status": "failed", "state": now, "turn": {**ended, "result": f"no verdict: {why}"}}
    try:
        said, findings, head = _docs_fields(data)
    except Malformed as exc:
        return {"status": "failed", "state": now, "turn": {**ended, "result": f"verdict refused: {exc}"}}
    turn_id = ended["turn_id"]
    kept, dropped, refused = c.sha, [], None
    if head is not None and head != c.sha:
        raw = f"refs/valor/docs-raw/{turn_id}"
        try:
            # In `git.threaded`, so a stop kills the fetch.
            await git.threaded(
                workspace.fetch_into_mirror, b.mirror, checkout, head, raw, harness["sandbox_profile"],
                f"docs-fetch-{ctx.task_id}",
            )  # fmt: skip
        except workspace.FetchRefused as exc:
            refused = str(exc)
        else:
            try:
                kept, dropped, refused = kept_prefix(b.mirror, c.sha, head)
            finally:
                git.trusted(b.mirror, "update-ref", "-d", raw)
    git.trusted(b.mirror, "update-ref", f"refs/valor/docs/{turn_id}", kept)
    payload = {
        "candidate": c.sha, "turn_id": turn_id, "kept": kept, "dropped": dropped, "refused": refused,
        "verdict": said, "findings": verdicts._findings(findings), "model": model,
        "usd_micros": int(ended.get("metered_usd_micros") or 0),
    }  # fmt: skip
    if not await ctx.alive():
        return {"status": "lock lost"}
    async with await db.connect(ctx.dsn) as conn, conn.transaction():
        await ledger.lock(conn, f"task:{ctx.task_id}")
        if await tasks.is_stopped(conn, ctx.task_id):
            return {"status": "stopped"}
        try:
            await verdicts._append(conn, ctx.task_id, DOCS_KEPT, payload)
        except verdicts.VerdictRefused as exc:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"verdict refused: {exc}"}}
    return {"kept": payload}


# -- review ----------------------------------------------------------------------------------

VERIFY = "verify.ran"
REVIEW_COMPARED = "review.compared"
WHERE = "host"


def reusable_verify(rows: list[dict], candidate: str, digest: str, where: str) -> dict | None:
    """The latest `verify.ran` with this candidate, environment digest, and
    `where`, and a cause other than `kernel`: a review rerun after Tom's
    grant reruns nothing, and a host result never stands in for a VM run."""
    for r in reversed(rows):
        p = r["payload"]
        if (
            r["type"] == VERIFY
            and p.get("candidate") == candidate
            and p.get("digest") == digest
            and p.get("where") == where
            and p.get("cause") != "kernel"
        ):
            return r
    return None


def verify_payload(
    candidate: str, base_sha: str, digest: str, base: dict, head: dict, lists: dict[str, list[str]]
) -> dict[str, Any]:
    """The `verify.ran` fields from the base and head `suite.ran` rows:
    ids, counts, codes, and lint locations, never a message the candidate's
    tests or lint printed."""
    hp = head["payload"]
    tests = hp.get("tests")
    return {
        "where": WHERE,
        "candidate": candidate,
        "base": base_sha,
        "digest": digest,
        "suites": [base["id"], head["id"]],
        "exit": hp.get("exit"),
        "counts": {k: len(v) for k, v in tests.items()} if tests is not None else None,
        "lint": hp.get("lint"),
        "failures": lists["failures"],
        "failing_at_base": lists["failing_at_base"],
        "deleted_at_head": lists["deleted_at_head"],
        "duration_s": hp.get("duration_s"),
        "cause": hp.get("cause"),
    }


def _effects(rows: list[dict]) -> str:
    """The task's held, released, and refused effects, every value quoted."""
    effects: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = r["payload"]
        kind = r["type"]
        if kind in ("effect.held", "effect.refused"):
            effects[p["effect_id"]] = {**p, "state": "held" if kind == "effect.held" else "refused"}
        elif kind == "effect.intent" and p.get("effect_id") in effects:
            effects[p["effect_id"]]["state"] = "released"
        elif kind == "effect.outcome" and p.get("effect_id") in effects:
            effects[p["effect_id"]]["state"] = f"released, {p.get('kind')}"
    out = []
    for effect_id, e in effects.items():
        lines = [
            f"## Effect {_quoted(effect_id)}",
            "",
            f"- action: {_quoted(e.get('action_type'))}",
            f"- effect class: {_quoted(e.get('effect_class'))}",
            f"- target: {_quoted(e.get('target'))}",
            f"- state: {_quoted(e['state'])}",
        ]
        if e.get("reason") is not None:
            lines.append(f"- refused because: {_quoted(e['reason'])}")
        payload = e.get("payload") or {}
        lines += [f"- payload {_quoted(k)}: {_quoted(v)}" for k, v in payload.items()]
        out.append("\n".join(lines))
    return "\n\n".join(out) or "No effect was held or refused."


def governance_input(
    mirror: str, base: str, candidate: str, instances: list[dict[str, Any]], judged: dict[str, Any],
    granted: set[str],
) -> dict[str, Any]:  # fmt: skip
    """`governance.json`: each kernel instance (id, path, start and end line,
    the hunk's added lines, granted or not), the abstentions, and the
    unjudged hunks."""
    hunks: dict[str, git.Hunk] = {}
    for path in {i["path"] for i in instances if not i["id"].startswith("unjudged-")}:
        for h in git.hunks(mirror, base, candidate, path):
            hunks.setdefault(h.id(), h)
    out = []
    for i in instances:
        h = hunks.get(i["id"])
        out.append(
            {
                "id": i["id"],
                "path": i["path"],
                "start": h.start if h else None,
                "end": h.start + max(h.length, 1) - 1 if h else None,
                "added": list(h.added) if h else [],
                "hunks": i.get("hunks"),
                "granted": i["id"] in granted,
            }
        )
    return {
        "instances": out,
        "abstained": list(judged.get("abstained") or []),
        "unjudged": [{"id": h.id, "path": h.path} for h, _ in judged["unjudged"]],
    }


def review_inputs(
    rows: list[dict], f: machine.Fold, b: tasks.Brief, verify: dict[str, Any], governance: dict[str, Any],
) -> dict[str, str]:  # fmt: skip
    """The reviewer's inputs, by file name. `plan.md` is the stakes header
    and the plan file's bytes at the commit critique read, from the mirror,
    so an edit the builder made after critique shows only in `diff.patch`."""
    plan = f.plan or {}
    body = ""
    if plan.get("commit") and plan.get("path"):
        try:
            body = git.trusted(b.mirror, "cat-file", "blob", f"{plan['commit']}:{plan['path']}")
        except git.GitError:
            body = ""
    header = (
        f"The plan file: {_quoted(plan.get('path'))}\n"
        f"Its stakes: {_quoted(plan.get('stakes'))}\n"
        f"Critique rounds: {plan.get('critique_rounds')}; review rounds: {plan.get('review_rounds')}\n"
        f"Scope additions: {_quoted(plan.get('scope') or [])}\n"
    )
    return {
        "request.md": b.instruction,
        "answers.md": _answers(rows),
        "plan.md": header + "\n" + (body or "No plan file."),
        "diff.patch": git.trusted(
            b.mirror, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", b.base_sha, f.candidate.sha
        ),
        "verify.json": json.dumps(verify, indent=2),
        "governance.json": json.dumps(governance, indent=2),
        "effects.md": _effects(rows),
    }


def final_verdict(text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """The reviewer's verdict: its final message, the one JSON object, bare
    or in one fenced block. The kernel reads it from the turn's result on
    the harness's stdout, which no process the session starts can write; a
    file in the checkout can be rewritten by the candidate's code the
    reviewer runs, to the end of the turn. Returns (verdict, why not)."""
    body = (text or "").strip()
    if body.startswith("```") and body.endswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body[: body.rfind("```")].strip()
    try:
        data = json.loads(body)
    except ValueError:
        return None, "the final message is not a JSON object"
    if not isinstance(data, dict):
        return None, "the final message is not a JSON object"
    return data, None


def _text(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _review_fields(data: dict[str, Any]) -> dict[str, Any]:
    """The reviewer's verdict object, checked for shape: `verdict` is `pass`
    or `changes`; anything else, or a malformed field, is `Malformed`."""
    verdict = data.get("verdict")
    findings = data.get("findings") or []
    instances = data.get("governance") or []
    notes = data.get("notes") or {}
    predicted = data.get("predicted_failure")
    requirements = data.get("requirements") or []
    if verdict not in verdicts.REVIEWER_VERDICTS:
        raise Malformed(f"the verdict is not one of {list(verdicts.REVIEWER_VERDICTS)}")
    if not isinstance(findings, list) or not all(isinstance(x, (dict, str)) for x in findings):
        raise Malformed("findings is not a list of findings")
    for x in findings:
        if isinstance(x, dict) and not isinstance(x.get("text"), str):
            raise Malformed("a finding has no text")
    if not isinstance(instances, list):
        raise Malformed("governance is not a list of instances")
    for i in instances:
        if (
            not isinstance(i, dict)
            or not isinstance(i.get("path"), str)
            or not isinstance(i.get("line"), int)
            or isinstance(i.get("line"), bool)
            or not all(_text(i.get(k)) for k in ("summary", "incident", "mission_item"))
        ):
            raise Malformed("a governance instance is not {path, line, summary, incident, mission_item}")
    if not isinstance(notes, dict) or not all(
        isinstance(v, dict) and all(_text(v.get(k)) for k in ("summary", "incident", "mission_item"))
        for v in notes.values()
    ):
        raise Malformed("notes is not an object of {summary, incident, mission_item} by instance id")
    if predicted is not None and (
        not isinstance(predicted, (int, float)) or isinstance(predicted, bool) or not 0 <= predicted <= 1
    ):
        raise Malformed("predicted_failure is a number from 0 to 1")
    if not isinstance(requirements, list):
        raise Malformed("requirements is not a list")
    return {
        "verdict": verdict,
        "findings": findings,
        "instances": instances,
        "notes": notes,
        "predicted_failure": predicted,
        "requirements": requirements,
    }


def normalize(
    mirror: str, base: str, candidate: str, fields: dict[str, Any], known: set[str]
) -> tuple[list[verdicts.InstanceSpec], dict[str, dict[str, Any]], list]:
    """The reviewer's instances and notes as the record takes them, so
    nothing the reviewer writes can refuse it: an instance whose path is not
    exactly one of the diff's paths, or with no added line at its line, and
    a note for an id not in `governance.json`, each become a finding of
    kind `governance`. Returns (instance specs, notes, findings)."""
    paths = set(git.diff_paths(mirror, base, candidate))
    specs: list[verdicts.InstanceSpec] = []
    findings = list(fields["findings"])
    for i in fields["instances"]:
        spec = verdicts.InstanceSpec(
            i["path"], i["line"], i.get("summary") or "", i.get("incident"), i.get("mission_item")
        )
        if spec.path in paths and git.hunk_at(mirror, base, candidate, spec.path, spec.line) is not None:
            specs.append(spec)
        else:
            findings.append(
                {
                    "kind": "governance",
                    "text": f"the reviewer named governance at {_quoted(spec.path)} line {spec.line}, "
                    f"where the diff adds no line: {spec.summary or 'no summary'}",
                }
            )
    notes: dict[str, dict[str, Any]] = {}
    for key, note in fields["notes"].items():
        if key in known:
            notes[key] = note
        else:
            findings.append(
                {
                    "kind": "governance",
                    "text": f"the reviewer noted {_quoted(key)}, which is not a governance instance here: "
                    f"{note.get('summary') or 'no summary'}",
                }
            )
    return specs, notes, findings


def review_runner(fresh_for: FreshFor, port, model: str | None = None, seat: str = SEATS["review"]):
    """The runner for `Check.REVIEW`: governance per hunk first, then the
    kernel's own base and head runs with the lint (`verify.ran`), then one
    blind session in a checkout the kernel set up, inside fresh services,
    whose final message is its verdict (`final_verdict`).
    The recorded verdict is computed from the reviewer's `pass` or
    `changes`, the instances, and the grants (`verdicts.review_verdict`).
    At the registered seat it records `review.decided`; at any other seat
    it appends `review.compared` as information and never moves the task.
    `model` overrides the seat's pinned model. The check holds the turn
    slot throughout, so no turn runs beside its suites; its review turn
    takes it again as a no-op."""

    model_ = model
    registered = seat == SEATS["review"]

    async def run(ctx) -> dict[str, Any]:
        async with slot.held(ctx.task_id, ctx.dsn):
            return await _run(ctx)

    async def _run(ctx) -> dict[str, Any]:
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
            f = machine.fold(rows)
            if f.state is State.STOPPED:
                return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
            if registered and (f.state is not State.CHECKS or machine.Check.REVIEW in f.checks):
                return {"status": "moved"}
            b = await tasks.brief(conn, ctx.task_id)
            state = await tasks.status(conn, ctx.task_id)

        def failed(why: str, turn: dict | None = None) -> dict[str, Any]:
            return {"status": "failed", "state": state, "turn": {**(turn or {}), "result": why}}

        if not b.mirror:
            return failed("a fresh session runs only in a workspace the kernel provisioned")
        if f.candidate is None:
            return failed("no candidate to review")
        candidate = f.candidate.sha
        lay = workspace.Layout(Path(b.mirror).parent)
        project = b.project or {}

        # 1. Governance first, so a judge outage costs no run and no turn.
        try:
            ids = await judgement_sites.governance(port, ctx.dsn, ctx.task_id, b.base_sha, candidate)
        except tasks.TaskStopped:
            return {"status": "stopped"}
        except judgement_sites.Unusable as exc:
            return failed(f"governance: {exc}")
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
        try:
            kernel_instances, judged = verdicts.governance_instances(
                rows, b.mirror, b.base_sha, candidate, ids, [], {}
            )
            governance = governance_input(
                b.mirror, b.base_sha, candidate, kernel_instances, judged, f.granted
            )
        except verdicts.VerdictRefused as exc:
            return failed(f"governance: {exc}")
        except git.GitError as exc:
            return failed(f"the diff: {exc}")

        # 2. The kernel's own head run and lint, against the shared base run.
        bin_dir = lay.root.parent / "bin"
        digest = await asyncio.to_thread(checks.environment_digest, b.mirror, candidate, project, bin_dir)
        kept = reusable_verify(rows, candidate, digest, WHERE)
        if kept is None:
            if not await ctx.alive():
                return {"status": "lock lost"}
            try:
                async with checks.stop_heard(ctx) as stop:
                    got = await checks.base_and_head(ctx, lay, b, stop, candidate, "review", lint=True)
            except checks._Stopped:
                return {"status": "stopped"}
            if "ran" not in got:
                return failed(got["turn"]["result"]) if got["status"] == "failed" else got
            base, head = got["ran"]["base"], got["ran"]["head"]
            try:
                deleted = await asyncio.to_thread(checks.removed_definitions, b.mirror, b.base_sha, candidate)
            except git.GitError as exc:
                return failed(f"the diff: {exc}")
            lists = checks.compare(base["payload"], head["payload"], deleted)
            verify = verify_payload(candidate, b.base_sha, digest, base, head, lists)
            if not await ctx.alive():
                return {"status": "lock lost"}
            async with await db.connect(ctx.dsn) as conn, conn.transaction():
                await ledger.lock(conn, f"task:{ctx.task_id}")
                if await tasks.is_stopped(conn, ctx.task_id):
                    return {"status": "stopped"}
                verify_id = await ledger.append(conn, ctx.task_id, VERIFY, verify)
        else:
            verify, verify_id = kept["payload"], kept["id"]

        # 3. The reviewer's checkout, set up by the kernel, and the turn,
        # both inside fresh services.
        harness_name, seat_model = resolve_seat(seat)
        model = model_ or seat_model
        check_dir = workspace.fresh_dir(lay.checks / f"review-{candidate[:12]}")
        checkout = check_dir / "repo"

        async def commits_own(why: str) -> dict[str, Any]:
            # The commit's own doing: every rerun would meet it again, so
            # review says `changes` with the reason and the join moves on.
            if not registered:
                return failed(why)
            if not await ctx.alive():
                return {"status": "lock lost"}
            try:
                async with await db.connect(ctx.dsn) as conn:
                    await verdicts.record_check(
                        conn, ctx.task_id, machine.Check.REVIEW, "changes", governance_from=ids,
                        findings=[{"kind": "commit", "text": why}], verify=verify_id, leg="kernel",
                    )  # fmt: skip
            except verdicts.VerdictRefused as refused:
                return failed(f"verdict refused: {refused}")
            return {"status": "moved"}

        try:
            await asyncio.to_thread(workspace.blind_checkout, b.mirror, b.base_sha, candidate, checkout)
        except workspace.ValorInTree as exc:
            return await commits_own(f"no review checkout: {exc}")
        except git.GitError as exc:
            return failed(f"review checkout: {exc}")
        seed = lay.checks / checks.SEED
        if seed.is_dir():
            await asyncio.to_thread(workspace.clone_tree, seed, check_dir / "cache")
        else:
            (check_dir / "cache").mkdir()
        services = workspace.check_services(lay, check_dir, project, ctx.task_id)
        try:
            env = await asyncio.to_thread(services.__enter__)
        except Exception as exc:  # noqa: BLE001  a service step failing is the kernel's
            return failed(f"the review's services did not start: {exc}")
        try:
            names = list(project.get("services") or ())
            ports = [int((project.get("ports") or {})[n]) for n in names]
            harness = workspace.check_harness(lay, check_dir, ports, env, services=True)
            setup_harness = workspace.setup_harness(lay, check_dir, ports, harness)
            commands = list(project.get("setup") or ())
            if not await ctx.alive():
                return {"status": "lock lost"}
            try:
                async with checks.stop_heard(ctx) as stop:
                    # A stop written before the LISTEN is read here.
                    async with await db.connect(ctx.dsn) as conn:
                        if await tasks.is_stopped(conn, ctx.task_id):
                            return {"status": "stopped"}
                    setup = await checks._setup(
                        checkout, setup_harness, commands, f"review-{ctx.task_id}-setup", stop,
                        lambda step: lay.checks / f"{check_dir.name}.{step}.out",
                    )  # fmt: skip
            except checks._Stopped:
                return {"status": "stopped"}
            # Setup ran before the inputs exist, so it can plant none, and
            # its profile kept it out of the session's own directories and
            # the checkout's repository. What it left in the checkout that
            # the session must not start with, and any error the kernel
            # meets there now, is the commit's own: every rerun meets it.
            try:
                left = await asyncio.to_thread(workspace.setup_left, checkout)
            except OSError as exc:
                return await commits_own(f"the checkout after setup: {exc}")
            if left:
                return await commits_own(left)
            seen = {**verify, "reviewer_setup_exit": setup["commands"][-1]["exit"] if commands else None}
            try:
                inputs = review_inputs(rows, f, b, seen, governance)
            except (OSError, ValueError, git.GitError) as exc:
                return failed(f"review inputs: {exc}")
            try:
                await asyncio.to_thread(workspace.write_inputs, checkout, inputs)
            except ValueError as exc:
                return failed(f"review inputs: {exc}")
            except OSError as exc:
                return await commits_own(f"the checkout after setup: {exc}")
            files = list(inputs)
            if not await ctx.alive():
                return {"status": "lock lost"}
            try:
                ended = await runs.run_turn(
                    ctx.gateway,
                    ctx.task_id,
                    fresh_for(prompt(files), str(checkout), model, harness, harness_name),
                    dsn=ctx.dsn,
                    state=f.state.value,
                    fresh="review",
                )
            except tasks.TaskStopped:
                return {"status": "stopped"}
        finally:
            await asyncio.to_thread(services.__exit__, None, None, None)
        async with await db.connect(ctx.dsn) as conn:
            now = await tasks.status(conn, ctx.task_id)
        if ended["outcome"] == "stopped":
            return {"status": "stopped", "state": now, "turn": ended}
        if ended["outcome"] != "done" or ended["result"].get("is_error"):
            return {"status": "failed", "state": now, "turn": ended}

        # 4 and 5. The verdict, its shape, and what the record takes.
        data, why = final_verdict(ended["result"].get("text"))
        if data is None:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"no verdict: {why}"}}
        try:
            fields = _review_fields(data)
            known = {i["id"] for i in governance["instances"]}
            specs, notes, findings = normalize(b.mirror, b.base_sha, candidate, fields, known)
        except Malformed as exc:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"verdict refused: {exc}"}}
        except git.GitError as exc:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"the diff: {exc}"}}
        usd = int(ended.get("metered_usd_micros") or 0)
        if not await ctx.alive():
            return {"status": "lock lost"}

        # 6. The record.
        if registered:
            try:
                async with await db.connect(ctx.dsn) as conn:
                    await verdicts.record_check(
                        conn, ctx.task_id, machine.Check.REVIEW, fields["verdict"], governance_from=ids,
                        governance=specs, notes=notes, findings=findings,
                        predicted_failure=fields["predicted_failure"], requirements=fields["requirements"],
                        verify=verify_id, leg="session", turn_id=ended["turn_id"], model=model, usd_micros=usd,
                    )  # fmt: skip
            except verdicts.VerdictRefused as exc:
                async with await db.connect(ctx.dsn) as conn:
                    if await tasks.is_stopped(conn, ctx.task_id):
                        return {"status": "stopped"}
                return {
                    "status": "failed",
                    "state": now,
                    "turn": {**ended, "result": f"verdict refused: {exc}"},
                }
            return {"status": "moved"}
        async with await db.connect(ctx.dsn) as conn, conn.transaction():
            await ledger.lock(conn, f"task:{ctx.task_id}")
            if await tasks.is_stopped(conn, ctx.task_id):
                return {"status": "stopped"}
            rows = await ledger.read(conn, ctx.task_id)
            try:
                instances, _ = verdicts.governance_instances(
                    rows, b.mirror, b.base_sha, candidate, ids, specs, notes
                )
            except (verdicts.VerdictRefused, git.GitError) as exc:
                return {"status": "failed", "state": now, "turn": {**ended, "result": f"governance: {exc}"}}
            computed, added = verdicts.review_verdict(
                fields["verdict"], instances, machine.fold(rows).granted
            )
            compared = {
                "seat": seat,
                "model": model,
                "candidate": candidate,
                "verdict": computed,
                "reviewer_verdict": fields["verdict"],
                "findings": verdicts._findings([*findings, *added]),
                "instances": instances,
                "predicted_failure": fields["predicted_failure"],
                "verify": verify_id,
                "turn_id": ended["turn_id"],
                "usd_micros": usd,
            }
            try:
                await verdicts._append(conn, ctx.task_id, REVIEW_COMPARED, compared)
            except verdicts.VerdictRefused as exc:
                return {
                    "status": "failed",
                    "state": now,
                    "turn": {**ended, "result": f"verdict refused: {exc}"},
                }
        return {"status": "compared", "state": now}

    return run
