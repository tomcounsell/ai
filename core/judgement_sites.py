"""The kernel's three judgement sites, and calibration.

- The judge runner: the `judge` state's runner (`intake.underspecified`).
  The composition root registers `judge_runner(port)` for `State.JUDGE`.
- `breadth`: the call `checks.test` makes (`checks.test.breadth`). 1.4's
  test runner calls it and passes the id to
  `verdicts.record_check(..., breadth=id)`, which reads the row itself.
- `governance`: one call per hunk of a diff (`governance.adds`). 1.4's
  review and docs runners call it and pass the ids to
  `verdicts.record_check(..., governance_from=ids)`, which recomputes the
  hunks from git and reads the rows itself.

Breadth runs before the suite; governance before the reviewer's turn, and
after the docs turn (the docs diff exists only then). So a provider outage
costs no suite run and no Opus turn, and each reuses an answered row on a
rerun (breadth by candidate; governance by hunk id and input digest). Both
read git in the kernel mirror when the task has one, so a builder's clone
that lost a commit, and a docs head that lives only in the mirror, are
read the same way. A
judgement both legs failed leaves its branch without a verdict, to be asked
again on the next run; after `judgement.UNANSWERED_RUNS` such failures, or
when the inputs were too large for both legs, the consumer applies caution
instead, so no branch waits on a provider for good.

`calibrate` runs both legs alone on a labelled case set and writes the
calibration record (`judgement.calibrated` on the `judgement` stream).
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from core import db, git, judgement, ledger, machine, tasks
from core.judgement import UNANSWERED_RUNS, JudgementPort
from core.judgement_tasks import BREADTH, BY_SITE, GOVERNANCE, JUDGE
from core.machine import State

DIFF = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--no-color")
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# A `git diff -W` hunk longer than this goes to the judgement as its
# three-line hunk instead, so a vast function does not make a hunk
# unjudgeable.
FUNCTION_HUNK_MAX_BYTES = 60_000


class Unusable(LookupError):
    """A judgement row cannot stand for what it is offered for."""


class Unanswered(LookupError):
    """Both legs failed and the site's reruns are not spent: the branch
    records no verdict and the next run asks again."""


# -- the judge -------------------------------------------------------------------


def project_name(b: tasks.Brief) -> str:
    source = b.origin_url or b.workspace or ""
    return PurePosixPath(source.rstrip("/")).name.removesuffix(".git") or "unknown"


def judge_runner(port: JudgementPort):
    """The runner for `State.JUDGE`: ask once (or reuse an answer no verdict
    has consumed yet), then let the kernel record the verdict from the row."""

    async def run(ctx) -> dict[str, Any]:
        from core import verdicts

        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
            f = machine.fold(rows)
            if f.state is State.STOPPED:
                return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
            if f.state is not State.JUDGE:
                return {"status": "moved"}
            b = await tasks.brief(conn, ctx.task_id)
        consumed = {r["payload"].get("judgement_id") for r in rows if r["type"] == "judge.decided"}
        pending = [
            r
            for r in rows
            if r["type"] in judgement.OUTCOMES
            and r["payload"].get("site") == JUDGE.site
            and r["payload"].get("judgement_id") not in consumed
        ]
        if pending:
            judgement_id = pending[-1]["payload"]["judgement_id"]
        else:
            start = next(r for r in rows if r["type"] == "task.started")
            request = str(start["payload"].get("instruction") or "")
            inputs = {"request": request, "thread": "", "project": project_name(b)}
            try:
                j = await port.judge(
                    JUDGE,
                    inputs,
                    task_id=ctx.task_id,
                    ref={"request_sha256": ledger.digest(request)},
                    dsn=ctx.dsn,
                )
            except tasks.TaskStopped:
                async with await db.connect(ctx.dsn) as conn:
                    state = await tasks.status(conn, ctx.task_id)
                return {"status": "stopped", "state": state}
            judgement_id = j.judgement_id
        if not await ctx.alive():
            return {"status": "lock lost"}
        async with await db.connect(ctx.dsn) as conn:
            try:
                await verdicts.record_judge(conn, ctx.task_id, judgement_id)
            except verdicts.VerdictRefused:
                if machine.fold(await ledger.read(conn, ctx.task_id)).state is State.STOPPED:
                    return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
                raise
        return {"status": "moved"}

    return run


def judge_verdict(row: dict) -> str:
    """The kernel's table for `intake.underspecified`: proceed is `precise`;
    caution, an abstain, and a failure are `thin`."""
    if row["type"] == "judgement.answered" and row["payload"].get("action", {}).get("request") == "proceed":
        return "precise"
    return "thin"


# -- breadth ---------------------------------------------------------------------

TEST_DIRS = ("tests", "test", "__tests__", "spec")
TEST_NAME = re.compile(r"(^test_.*|.*_test\.[^.]+$|.*\.(test|spec)\.[^.]+$|^conftest\.py$)")


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(p in TEST_DIRS for p in parts[:-1]) or bool(TEST_NAME.match(parts[-1] if parts else ""))


def _candidate_ref(c: machine.Candidate) -> dict[str, str]:
    return {"sha": c.sha, "turn_id": c.turn_id}


async def breadth(port: JudgementPort, dsn: str, task_id: str) -> str:
    """The breadth judgement for the task's current candidate: an answered
    row already on the ledger for it, else a failed one once its reruns are
    spent, else a new call. Returns the judgement id. Raises
    `tasks.TaskStopped` when the task is stopped."""
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task_id)
        b = await tasks.brief(conn, task_id)
    f = machine.fold(rows)
    if f.candidate is None or f.state is not State.CHECKS:
        raise Unusable(f"task {task_id} has no candidate waiting on its checks")
    ref = {"candidate": _candidate_ref(f.candidate)}
    mine = [
        r
        for r in rows
        if r["type"] in judgement.OUTCOMES
        and r["payload"].get("site") == BREADTH.site
        and r["payload"].get("ref") == ref
    ]
    answered = [r for r in mine if r["type"] == "judgement.answered" or r["payload"].get("too_large")]
    if answered:
        return answered[-1]["payload"]["judgement_id"]
    if len(mine) >= UNANSWERED_RUNS:
        return mine[-1]["payload"]["judgement_id"]
    repo = b.mirror or b.workspace
    paths = git.diff_paths(repo, b.base_sha, f.candidate.sha)
    tests_ = [p for p in paths if is_test_path(p)]
    code = [p for p in paths if not is_test_path(p)]
    inputs = {
        "diff": _diff(repo, b.base_sha, f.candidate.sha, code),
        "tests": _diff(repo, b.base_sha, f.candidate.sha, tests_),
    }
    j = await port.judge(BREADTH, inputs, task_id=task_id, ref=ref, dsn=dsn)
    return j.judgement_id


def _diff(workspace: str, older: str, newer: str, paths: list[str]) -> str:
    return git.out(workspace, "--literal-pathspecs", *DIFF, older, newer, "--", *paths) if paths else ""


def breadth_outcome(rows: list[dict], judgement_id: str, candidate: machine.Candidate) -> dict[str, Any]:
    """What a breadth row means for `test.decided`: the behaviors it lists,
    by the kernel's table. Raises `Unusable` for a row that is not this
    candidate's breadth judgement, and `Unanswered` for a failure whose
    reruns are not spent."""
    row = judgement.find(rows, judgement_id)
    if row is None or row["payload"].get("site") != BREADTH.site:
        raise Unusable(f"{judgement_id} is not a breadth judgement on this task")
    p = row["payload"]
    if p.get("ref") != {"candidate": _candidate_ref(candidate)}:
        raise Unusable(f"breadth judgement {judgement_id} is for another candidate")
    out = {
        "judgement_id": judgement_id,
        "model": p.get("model"),
        "usd_micros": p.get("usd_micros"),
        "guard_id": None,
        "actions": p.get("action") or {},
        "abstained": p.get("abstained") or [],
    }
    if row["type"] == "judgement.answered":
        labels = {q.id: q.labels["true"] for q in BREADTH.questions}
        out["behaviors"] = [labels[q] for q, a in p["action"].items() if a == "caution"]
    elif p.get("too_large"):
        out["behaviors"] = ["breadth not judged: the change is larger than either provider accepts"]
    else:
        spent = judgement.unanswered_count(rows, BREADTH.site, {"candidate": p["ref"]["candidate"]})
        if spent < UNANSWERED_RUNS:
            raise Unanswered(f"breadth unanswered ({_reasons(p)}); the next run asks again")
        out["behaviors"] = [f"breadth not judged: both judgement legs failed on {spent} runs ({_reasons(p)})"]
    if out["behaviors"]:
        out["guard_id"] = machine.GUARD_BREADTH
    return out


def _reasons(p: dict) -> str:
    return "; ".join(f"{a['leg']}: {a.get('reason')}" for a in p.get("attempts") or [] if a.get("reason"))


# -- governance ------------------------------------------------------------------


@dataclass(frozen=True)
class DiffHunk:
    id: str
    path: str
    start: int
    text: str  # what the judgement reads: the `-W` hunk, or the three-line hunk when that is too long


def _hunk_texts(diff: str) -> list[tuple[int, int, str]]:
    """Each hunk of one path's diff: its new start, new length, and lines."""
    out: list[list] = []
    for line in diff.splitlines():
        m = HUNK_HEADER.match(line)
        if m:
            out.append([int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1), [line]])
        elif out:
            out[-1][2].append(line)
    return [(s, n, "\n".join(lines)) for s, n, lines in out]


def diff_hunks(workspace: str, older: str, newer: str) -> list[DiffHunk]:
    """Every hunk with added lines between two commits, once per hunk id.
    The hunks and their ids are `git.hunks`'s (three lines of context), the
    ones `git.hunk_at` and the instance ids read. The text the judgement
    reads is the `git diff -W` hunk holding it, so the enclosing function
    comes with it: an unchanged hunk in a changed function keeps its id
    while its input changes."""
    seen: dict[str, DiffHunk] = {}
    for path in git.diff_paths(workspace, older, newer):
        hunks = git.hunks(workspace, older, newer, path)
        if not any(h.added for h in hunks):
            continue
        plain = _hunk_texts(git.out(workspace, "--literal-pathspecs", *DIFF, older, newer, "--", path))
        wide = _hunk_texts(git.out(workspace, "--literal-pathspecs", *DIFF, "-W", older, newer, "--", path))
        for h, (_, _, own) in zip(hunks, plain, strict=False):
            if not h.added or h.id() in seen:
                continue
            text = next((t for s, n, t in wide if s <= h.start and h.start + h.length <= s + max(n, 1)), own)
            if len(text.encode()) > FUNCTION_HUNK_MAX_BYTES:
                text = own
            seen[h.id()] = DiffHunk(h.id(), path, h.start, text)
    return list(seen.values())


def _governance_inputs(h: DiffHunk, paths: list[str]) -> dict[str, str]:
    return {"path": h.path, "hunk": h.text, "paths": "\n".join(paths)}


async def governance(port: JudgementPort, dsn: str, task_id: str, older: str, newer: str) -> list[str]:
    """One governance judgement per hunk with added lines between `older`
    and `newer` (review: the base to the candidate; docs: the candidate to
    the docs head), all at once, writing through one shared connection. A
    hunk with an answered row for the same id and the same input under the
    same question (its `task_sha256`) reuses it; a hunk whose reruns under
    that question are spent reuses its last failure. A reworded question is
    asked fresh. Every hunk
    finishes, and its calls are charged, before a stop or other failure is
    raised. Returns the ids, one per hunk."""

    async def one(h: DiffHunk) -> str:
        inputs = _governance_inputs(h, paths)
        digest = ledger.digest(inputs)
        mine = [
            r
            for r in rows
            if r["type"] in judgement.OUTCOMES
            and r["payload"].get("site") == GOVERNANCE.site
            and (r["payload"].get("ref") or {}).get("hunk", {}).get("id") == h.id
            and r["payload"].get("inputs_sha256") == digest
            and r["payload"].get("task_sha256") == question
        ]
        settled = [r for r in mine if r["type"] == "judgement.answered" or r["payload"].get("too_large")]
        if settled:
            return settled[-1]["payload"]["judgement_id"]
        if len(mine) >= UNANSWERED_RUNS:
            return mine[-1]["payload"]["judgement_id"]
        ref = {"hunk": {"id": h.id, "path": h.path, "start": h.start}}
        j = await port.judge(GOVERNANCE, inputs, task_id=task_id, ref=ref, shared=shared)
        return j.judgement_id

    question = judgement.task_sha256(GOVERNANCE, port.signature())
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task_id)
        b = await tasks.brief(conn, task_id)
        repo = b.mirror or b.workspace
        hunks = diff_hunks(repo, older, newer)
        paths = git.diff_paths(repo, older, newer)
        shared = judgement.Shared(conn)
        # Every hunk runs to its end while the connection is open, so a stop
        # that one hunk meets never leaves another's opened call uncharged.
        done = await asyncio.gather(*(one(h) for h in hunks), return_exceptions=True)
    for got in done:
        if isinstance(got, BaseException):
            raise got
    return list(done)


def governance_outcome(rows: list[dict], ids: list[str], hunks: list[DiffHunk]) -> dict[str, Any]:
    """What a set of governance rows means for a review or docs verdict:
    the hunks that are instances (caution, abstain, or too large for both
    legs), how many came from an abstain and which, and the hunks left unjudged after
    their reruns. Refuses a set that skips a hunk, names one twice, or
    names a hunk the diff does not hold; raises `Unanswered` while any
    failed hunk has reruns left."""
    by_hunk: dict[str, dict] = {}
    for jid in ids:
        row = judgement.find(rows, jid)
        if row is None or row["payload"].get("site") != GOVERNANCE.site:
            raise Unusable(f"{jid} is not a governance judgement on this task")
        hid = ((row["payload"].get("ref") or {}).get("hunk") or {}).get("id")
        if hid in by_hunk:
            raise Unusable(f"hunk {hid} is answered twice")
        by_hunk[hid] = row
    wanted = {h.id: h for h in hunks}
    stray = set(by_hunk) - set(wanted)
    if stray:
        raise Unusable(f"governance judgements for hunks the diff does not hold: {sorted(stray)}")
    missing = set(wanted) - set(by_hunk)
    if missing:
        raise Unusable(f"no governance judgement for hunks {sorted(missing)}")
    instances, unjudged, abstained, abstained_ids = [], [], 0, []
    for h in hunks:
        row = by_hunk[h.id]
        p = row["payload"]
        if row["type"] == "judgement.answered":
            if p["action"].get("adds") == "caution":
                instances.append(h)
                if "adds" in (p.get("abstained") or []):
                    abstained += 1
                    abstained_ids.append(h.id)
        elif p.get("too_large"):
            instances.append(h)
        else:
            spent = judgement.unanswered_count(
                rows,
                GOVERNANCE.site,
                {
                    "hunk": p["ref"]["hunk"],
                    "inputs_sha256": p["inputs_sha256"],
                    "task_sha256": p["task_sha256"],
                },
            )
            if spent < UNANSWERED_RUNS:
                raise Unanswered(
                    f"governance unanswered for {h.path} ({_reasons(p)}); the next run asks again"
                )
            unjudged.append((h, _reasons(p)))
    return {
        "instances": instances,
        "abstain_instances": abstained,
        "abstained": abstained_ids,
        "unjudged": unjudged,
    }


def unjudged_instance(unjudged: list[tuple[DiffHunk, str]]) -> dict[str, Any]:
    """One instance for the whole diff, standing for every hunk the
    judgement could not answer after its reruns: Tom taps once, not once
    per hunk."""
    ids = sorted(h.id for h, _ in unjudged)
    paths = sorted({h.path for h, _ in unjudged})
    return {
        "id": "unjudged-" + ledger.digest(ids)[:16],
        "path": "(diff)",
        "line": 0,
        "context": "",
        "summary": (
            f"governance not judged for {len(ids)} hunk(s) in {', '.join(paths)}: both judgement legs failed "
            f"({unjudged[0][1]})"
        ),
        "incident": None,
        "mission_item": None,
        "hunks": ids,
    }


# -- calibration -----------------------------------------------------------------

STREAM = "judgement"
# A judge case's label and the action it expects.
JUDGE_LABELS = {"precise": "proceed", "thin": "caution"}
DRAFTED = "drafted"


def load_cases(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    """A cases file: `{"site": ..., "cases": [...]}`, each case in its
    site's shape (`check_calibration`). A judge case's `item` is a replay
    item file (relative to the cases file) whose `request` and `repo` are
    read."""
    path = Path(path)
    doc = json.loads(path.read_text())
    site, cases = doc["site"], doc["cases"]
    out = []
    for c in cases:
        c = dict(c)
        if "item" in c:
            item = json.loads((path.parent / c["item"]).read_text())
            c.setdefault("request", item["request"])
            c.setdefault("project", PurePosixPath(str(item.get("repo", ""))).name)
        out.append(c)
    return site, out


@dataclass(frozen=True)
class Expected:
    """One human or drafted label of one case, on one question: the label
    as the case gives it, the action it expects, and where it came from."""

    label: str
    action: str
    source: str


def case_shape(site: str, c: dict[str, Any]) -> tuple[dict[str, str], dict[str, Expected]]:
    """A case's inputs and its expected action per question, by the site's
    case shape:

    - judge: `{"id", "request" or "item", "label": precise|thin, "sub_label", "source"}`;
    - breadth: `{"id", "inputs": {"diff", "tests"}, "labels": {question: {"value", "source", "evidence"}}}`;
    - governance: `{"id", "inputs": {"path", "hunk", "paths"}, "label": true|false, "source", "evidence"}`.

    A label `false` expects proceed and `true` caution (each question's own
    `proceed` set). Raises `ValueError` naming what is wrong."""
    task = BY_SITE[site]
    if site == JUDGE.site:
        if c.get("label") not in JUDGE_LABELS:
            raise ValueError(f"case {c.get('id')}: a judge label is precise or thin")
        q = task.questions[0]
        action = JUDGE_LABELS[c["label"]]
        inputs = {"request": str(c["request"]), "thread": "", "project": str(c.get("project", ""))}
        return inputs, {q.id: Expected(c["label"], action, str(c.get("source", "judge")))}
    inputs = c.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(task.inputs):
        raise ValueError(f"case {c.get('id')}: inputs are {sorted(task.inputs)}")
    if site == GOVERNANCE.site:
        labels = {"adds": {"value": c.get("label"), "source": c.get("source")}}
    else:
        labels = c.get("labels") or {}
    expected = {}
    for q in task.questions:
        lab = labels.get(q.id)
        if not isinstance(lab, dict) or not isinstance(lab.get("value"), bool) or not lab.get("source"):
            raise ValueError(f"case {c.get('id')}: no true or false label with a source for {q.id}")
        label = "true" if lab["value"] else "false"
        action = "proceed" if label in q.proceed else "caution"
        expected[q.id] = Expected(label, action, str(lab["source"]))
    return {k: str(v) for k, v in inputs.items()}, expected


def check_calibration(cases_path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    """The cases file and its site, before anything is asked: `ValueError`
    naming what is wrong."""
    try:
        site, cases = load_cases(cases_path)
    except FileNotFoundError as exc:
        raise ValueError(f"no cases file {exc.filename}") from None
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{cases_path} is not a cases file ({exc!r})") from None
    if site not in BY_SITE:
        raise ValueError(f"no calibration case shape for {site}")
    for c in cases:
        try:
            case_shape(site, c)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"case {c.get('id')} is not a {site} case ({exc!r})") from None
    return site, cases


def endpoint_hosts(port: JudgementPort) -> dict[str, str | None]:
    from urllib.parse import urlparse

    return {name: urlparse(leg.endpoint).hostname for name, leg in sorted(port.legs.items())}


async def calibrate(port: JudgementPort, dsn: str, cases_path: str | Path) -> dict[str, Any]:
    """Both legs alone on every case, every question scored, one calibration
    task, one record. The record names each leg's endpoint host, so a run
    against anything but the providers says so; the command line refuses
    one."""
    site, cases = check_calibration(cases_path)
    task = BY_SITE[site]
    async with await db.connect(dsn) as conn:
        task_id = await tasks.start_calibration(conn, site)
    results: list[dict[str, Any]] = []
    for c in cases:
        inputs, expected = case_shape(site, c)
        per_leg = {}
        for leg in judgement.route(task):
            j = await port.ask_leg(leg, task, inputs, task_id=task_id, ref={"case": c["id"]}, dsn=dsn)
            per_leg[leg] = _scored(task, j, expected, asked_again=0)
        label = c["label"] if site != BREADTH.site else {q: e.label for q, e in expected.items()}
        results.append(
            {"case": c["id"], "label": label, "sub_label": c.get("sub_label"), "source": c.get("source"),
             **per_leg}
        )  # fmt: skip
    # A 429 is no answer: each (case, leg) refused that way is asked again,
    # after the provider's own Retry-After when it named one, for as long as
    # a pass answers at least one of them.
    while True:
        refused = [(c, r, leg) for c, r in zip(cases, results, strict=True)
                   for leg in judgement.route(task) if r[leg]["rate_limited"]]  # fmt: skip
        answered = 0
        for c, r, leg in refused:
            wait = judgement.held_until(port.legs[leg].endpoint) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            inputs, expected = case_shape(site, c)
            j = await port.ask_leg(leg, task, inputs, task_id=task_id, ref={"case": c["id"]}, dsn=dsn)
            r[leg] = _scored(task, j, expected, asked_again=r[leg]["asked_again"] + 1)
            answered += j.answered
        if not answered:
            break
    async with await db.connect(dsn) as conn:
        calls = await ledger.read(conn, task_id)
    record = _record(task, port, results, task_id, calls)
    async with await db.connect(dsn) as conn, conn.transaction():
        await ledger.lock(conn, STREAM)
        prior = await (
            await conn.execute(
                "SELECT count(*) FROM events WHERE task_id = %s AND type = 'judgement.calibrated' "
                "AND payload->>'site' = %s",
                (STREAM, site),
            )
        ).fetchone()
        record["run"] = int(prior[0]) + 1
        record["event_id"] = await ledger.append(conn, STREAM, "judgement.calibrated", record)
    return record


def _scored(task, j, expected, *, asked_again: int) -> dict[str, Any]:
    """One leg's answer to one case, every question scored against its label;
    a failed ask is `caution`."""
    questions = {}
    for q in task.questions:
        a = j.answers.get(q.id) if j.answered else None
        action = j.action.get(q.id) if j.answered else "caution"
        e = expected[q.id]
        questions[q.id] = {
            "expected": e.label,
            "wants": e.action,
            "source": e.source,
            "label": a["label"] if a else None,
            "p_proceed": a["p_proceed"] if a else None,
            "decision": a["decision"] if a else "failed",
            "action": action,
            "correct": action == e.action,
        }
    first = questions[task.questions[0].id]
    return {
        "answered": j.answered,
        "label": first["label"],
        "p_proceed": first["p_proceed"],
        "decision": first["decision"],
        "verdict": _verdict_word(task, first["action"]),
        "correct": all(q["correct"] for q in questions.values()),
        "questions": questions,
        "usd_micros": j.usd_micros,
        "reason": None if j.answered else "; ".join(str(x.get("reason")) for x in j.attempts),
        "rate_limited": not j.answered
        and all(x.get("reason") == "rate_limited" for x in j.attempts if x.get("outcome") != "unused"),
        "asked_again": asked_again,
        "judgement_id": j.judgement_id,
    }


def _verdict_word(task, action: str) -> str:
    if task.site == JUDGE.site:
        return "precise" if action == "proceed" else "thin"
    return action


def _counts(answers: list[dict[str, Any]]) -> dict[str, Any]:
    confusion: dict[str, dict[str, int]] = {}
    for a in answers:
        row = confusion.setdefault(a["expected"], {})
        row[a["action"]] = row.get(a["action"], 0) + 1
    return {"n": len(answers), "wrong": sum(not a["correct"] for a in answers), "confusion": confusion}


def estimate_check(rows: list[dict], leg: str) -> dict[str, Any]:
    """Billed input tokens against `estimated_input`, from the calibration
    task's own `gateway.opened` and `gateway.charged` rows for one leg: the
    largest and the median ratio, and how many calls billed more than
    estimated. The estimate sets the input-size limit and the charge when
    a provider reports no usage, so any call over it means it is too low."""
    import statistics

    opened = {
        r["payload"]["call_id"]: r["payload"]
        for r in rows
        if r["type"] == "gateway.opened" and r["payload"].get("leg") == leg
    }
    ratios = []
    for r in rows:
        call = opened.get(r["payload"].get("call_id")) if r["type"] == "gateway.charged" else None
        billed = ((r["payload"].get("usage") or {}).get("input_tokens")) if call else None
        if call and isinstance(billed, int) and call.get("estimated_input"):
            ratios.append(billed / call["estimated_input"])
    return {
        "n": len(ratios),
        "max_ratio": round(max(ratios), 4) if ratios else None,
        "median_ratio": round(statistics.median(ratios), 4) if ratios else None,
        "over_estimate": sum(x > 1 for x in ratios),
    }


def _record(task, port, results, task_id, calls) -> dict[str, Any]:
    """Per leg: per question, the confusion counts over human labels
    (`per_question`) and over drafted ones (`drafted`, information only);
    the entry check, true when every question has a human `proceed` label
    and a human `caution` label and the leg is right on every human label;
    and the estimate re-check. The record's entry check is every leg's."""
    from datetime import UTC, datetime

    legs = {}
    for leg in judgement.route(task):
        rs = [r[leg] for r in results]
        per_question, drafted = {}, {}
        entry = True
        for q in task.questions:
            answers = [r["questions"][q.id] for r in rs]
            human = [a for a in answers if a["source"] != DRAFTED]
            counts = _counts(human)
            directions = {d: sum(a["wants"] == d for a in human) for d in ("proceed", "caution")}
            counts["labels"] = directions
            per_question[q.id] = counts
            drafted[q.id] = _counts([a for a in answers if a["source"] == DRAFTED])
            entry = entry and counts["wrong"] == 0 and all(directions.values())
        scored = [
            (a["p_proceed"], a["wants"]) for r in rs if r["answered"]
            for q in task.questions for a in [r["questions"][q.id]]
        ]  # fmt: skip
        brier = (
            sum(((1 - p) - (1.0 if want == "caution" else 0.0)) ** 2 for p, want in scored) / len(scored)
            if scored
            else None
        )
        confusion: dict[str, dict[str, int]] = {}
        sub: dict[str, dict[str, int]] = {}
        for r, c in zip(rs, results, strict=True):
            first = r["questions"][task.questions[0].id]
            confusion.setdefault(first["expected"], {}).setdefault(r["verdict"], 0)
            confusion[first["expected"]][r["verdict"]] += 1
            key = c.get("sub_label") or first["expected"]
            sub.setdefault(key, {}).setdefault(str(r["label"]), 0)
            sub[key][str(r["label"])] += 1
        abstains = [r for r in rs if r["decision"] == "abstain"]
        decided = [r for r in rs if r["decision"] in ("proceed", "caution")]
        errors: dict[str, int] = {}
        for r in rs:
            if not r["answered"]:
                errors[r["reason"]] = errors.get(r["reason"], 0) + 1
        legs[leg] = {
            "model": port.legs[leg].model,
            "n": len(rs),
            "brier": None if brier is None else round(brier, 4),
            "brier_n": len(scored),
            "confusion": confusion,
            "sub_label_confusion": sub,
            "per_question": per_question,
            "drafted": drafted,
            "abstain_rate": round(len(abstains) / len(rs), 4) if rs else None,
            "accuracy_not_abstained": round(sum(r["correct"] for r in decided) / len(decided), 4)
            if decided
            else None,
            "error_rate": round(sum(errors.values()) / len(rs), 4) if rs else None,
            "asked_again": sum(r["asked_again"] for r in rs),
            "errors": errors,
            "usd_micros_per_call": round(sum(r["usd_micros"] for r in rs) / len(rs), 2) if rs else None,
            "all_correct": all(c["wrong"] == 0 for c in per_question.values()),
            "entry_check": entry,
            "estimate": estimate_check(calls, leg),
        }
    sources: dict[str, int] = {"tom": 0, "role_played": 0, "judge": 0}
    for r in results:
        any_leg = r[judgement.route(task)[0]]
        for a in any_leg["questions"].values():
            sources[a["source"]] = sources.get(a["source"], 0) + 1
    return {
        "site": task.site,
        "task_sha256": judgement.task_sha256(task, port.signature()),
        "models": port.models(),
        "endpoints": endpoint_hosts(port),
        "floor": task.floor,
        "at": datetime.now(UTC).isoformat(),
        "calibration_task": task_id,
        "n": len(results),
        "label_sources": sources,
        "legs": legs,
        "entry_check": all(leg["entry_check"] for leg in legs.values()),
        "cases": results,
        "note": (
            "The Brier score is information beside its n, not evidence of calibration. Drafted labels "
            "are counted apart and never in the entry check."
        ),
    }
