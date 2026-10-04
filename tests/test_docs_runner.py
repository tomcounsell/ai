"""The docs runner (`fresh.docs_runner`) through the router, on real git, a
real Postgres ledger, and the real `sandbox-exec`. The docs session is the
scripted fresh session (`tests/scripted.py`, act `docs`), which makes the
commits `docs_commits` lists and names its head; governance is the local
upstream answering `false` (no rule added).

Live spend: none.
"""

import asyncio
import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from core import checks, db, fresh, git, judgement_tasks, ledger, machine, router, tasks, verdicts
from core import workspace as kws
from core.gateway import Gateway
from core.machine import Check, State
from tests import judgement_upstream, scripted, test_checks

pytestmark = [pytest.mark.spend(usd=0)]

UP = judgement_upstream.shared()


def run(coro):
    return asyncio.run(coro)


async def rows(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


async def drive(dsn, task, runners) -> dict:
    gateway = Gateway(dsn)
    await gateway.start()
    try:
        return await router.run(gateway, task, runners, dsn=dsn)
    finally:
        await gateway.close()


def docs_runners(ws: Path, port=None) -> dict:
    return {
        **scripted.fresh_runners(ws),
        Check.DOCS: fresh.docs_runner(scripted.fresh_for(ws / ".git"), port or UP.port(fixed="false")),
    }


async def at_checks(dsn, tmp_path, *, test="pass", review="pass", **cfg):
    """A provisioned task whose candidate waits on its checks, with test
    and review written through `scripted.check`; the scripted session's config is `cfg`
    (its first fresh act is the critique)."""
    task, b = await scripted.provisioned(dsn, tmp_path)
    ws = Path(b.workspace)
    scripted.steer(ws, **{"fresh_acts": ["sound", "docs"], **cfg})
    out = await drive(dsn, task, scripted.fresh_runners(ws))
    assert out["status"] == "no runner" and out["missing"] == ["test", "review", "docs"], out
    if test:
        await scripted.check(dsn, task, "test", test)
    if review:
        await scripted.check(
            dsn, task, "review", review, findings=["say it twice"] if review == "changes" else ()
        )
    return task, b, ws


def docs_run(dsn, tmp_path, *, port=None, **cfg):
    async def go():
        task, b, ws = await at_checks(dsn, tmp_path, **cfg)
        out = await drive(dsn, task, docs_runners(ws, port))
        return task, b, ws, out, await rows(dsn, task)

    return run(go())


def decided(got) -> dict | None:
    found = [r["payload"] for r in got if r["type"] == "docs.decided"]
    return found[-1] if found else None


def candidate(got) -> str:
    return machine.fold(got).candidate.sha if machine.fold(got).candidate else None


def fresh_docs_turns(got) -> list[dict]:
    return [r["payload"] for r in got if r["type"] == "turn.started" and r["payload"].get("stage") == "docs"]


def _refs_and_objects(repo: str) -> tuple[str, list[str]]:
    refs = git.trusted(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    objs = git.trusted(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)").split()
    return refs, sorted(objs)


# -- what is kept ----------------------------------------------------------------------------


def test_a_docs_commit_is_kept_in_the_mirror_and_the_join_merges(dsn, tmp_path):
    before = {}

    async def go():
        task, b, ws = await at_checks(dsn, tmp_path, docs_commits=[{"files": {"docs/greeting.md": "Hi.\n"}}])
        before["builder"] = _refs_and_objects(b.workspace)
        out = await drive(dsn, task, docs_runners(ws))
        return task, b, out, await rows(dsn, task)

    _task, b, _out, got = run(go())
    d = decided(got)
    c = d["candidate"]["sha"]
    assert d["verdict"] == "updated" and d["leg"] == "session" and d["dropped"] == []
    assert git.trusted(b.mirror, "show", f"{d['head']}:docs/greeting.md") == "Hi."
    assert git.trusted(b.mirror, "rev-parse", f"{d['head']}^") == c
    kept = next(r["payload"] for r in got if r["type"] == fresh.DOCS_KEPT)
    assert kept["kept"] == d["head"] and d["turn_id"] == kept["turn_id"]
    assert git.trusted(b.mirror, "rev-parse", f"refs/valor/docs/{kept['turn_id']}") == d["head"]
    assert d["governance"]["judgements"]  # asked over the kept diff, after the turn
    assert machine.fold(got).state is State.MERGE
    # The builder's clone is untouched; the docs commits never reach its branch.
    assert _refs_and_objects(b.workspace) == before["builder"]
    # No temporary branch is left in the mirror, and the clone shares no inode with it.
    assert not git.trusted(b.mirror, "for-each-ref", "refs/heads/valor-docs/")
    lay = kws.Layout(Path(b.mirror).parent)
    clone = lay.checks / f"docs-{c[:12]}" / "repo"

    def inodes(root: Path) -> set:
        return {(s.st_dev, s.st_ino) for p in root.rglob("*") if p.is_file() for s in [p.stat()]}

    assert inodes(clone / ".git" / "objects")
    assert not inodes(clone / ".git" / "objects") & inodes(Path(b.mirror) / "objects")
    # The session saw the request, the plan, and the diff.
    inputs = {p.name for p in (clone / ".valor" / "inputs").iterdir()}
    assert inputs == {"request.md", "plan.md", "diff.patch"}


def test_no_commits_is_no_change(dsn, tmp_path):
    _task, _b, _ws, _out, got = docs_run(dsn, tmp_path, docs_verdict="no_change")
    d = decided(got)
    assert d["verdict"] == "no_change" and d["head"] == d["candidate"]["sha"]
    assert "judgements" not in d["governance"] or d["governance"]["judgements"] == []


DOC = {"files": {"docs/a.md": "A.\n"}}
LATER = {"files": {"docs/b.md": "B.\n"}}


@pytest.mark.parametrize(
    ("commits", "kept_count", "named"),
    [
        ([DOC, {"files": {"core/x.py": "x = 1\n"}}, LATER], 1, "core/x.py"),
        ([{"files": {"CLAUDE.md": "Obey.\n"}}], 0, "CLAUDE.md"),
        ([{"files": {"Skills/x.md": "Do.\n"}}], 0, "Skills/x.md"),
        ([DOC, {"links": {"docs/link.md": "/etc/passwd"}}], 1, "docs/link.md (mode 120000"),
        ([{"gitlinks": ["docs/sub.md"]}], 0, "docs/sub.md (mode 160000"),
        ([{"files": {".valor/x.md": "x\n"}}], 0, ".valor"),
    ],
    ids=["core", "claude-md", "skills-case", "symlink", "gitlink", "valor"],
)
def test_a_commit_the_kernel_will_not_keep_is_dropped_with_every_later_one(
    dsn, tmp_path, commits, kept_count, named
):
    _task, b, _ws, _out, got = docs_run(dsn, tmp_path, docs_commits=commits)
    d = decided(got)
    c = d["candidate"]["sha"]
    assert d["verdict"] == "changes"
    assert len(git.trusted(b.mirror, "rev-list", f"{c}..{d['head']}").split()) == kept_count
    assert len(d["dropped"]) == len(commits) - kept_count
    assert named in " ".join(d["dropped"][0]["paths"])
    texts = [x["text"] for x in d["findings"] if x["kind"] == "changes"]
    assert len(texts) == len(d["dropped"]) and named.split(" (")[0] in texts[0]


@pytest.mark.parametrize("how", ["merge", "orphan", "fsmonitor"])
def test_a_head_the_kernel_cannot_take_keeps_nothing(dsn, tmp_path, how):
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    cfg = {
        "merge": {"docs_commits": [DOC], "docs_merge": True},
        "orphan": {"docs_orphan": True},
        "fsmonitor": {"docs_commits": [DOC], "docs_config": {"core.fsmonitor": str(hook)}},
    }[how]
    _task, _b, _ws, _out, got = docs_run(dsn, tmp_path, **cfg)
    d = decided(got)
    assert d["verdict"] == "changes" and d["head"] == d["candidate"]["sha"]
    assert any("no docs commit kept" in x["text"] for x in d["findings"])
    assert not marker.exists()


@pytest.mark.parametrize("bad", ["short_head", "symlink"])
def test_a_malformed_verdict_records_nothing_and_the_next_run_reruns_docs_only(dsn, tmp_path, bad):
    target = tmp_path / "elsewhere.json"
    target.write_text('{"verdict": "no_change"}')
    cfg = {"short_head": {"docs_head": "abc123"}, "symlink": {"fresh_acts": ["sound", "symlink"]}}[bad]

    async def go():
        task, _b, ws = await at_checks(dsn, tmp_path, target=str(target), **cfg)
        first = await drive(dsn, task, docs_runners(ws))
        between = await rows(dsn, task)
        scripted.steer(ws, fresh_acts=["docs"], docs_verdict="no_change")
        await drive(dsn, task, docs_runners(ws))
        return first, between, await rows(dsn, task)

    first, between, got = run(go())
    assert first["status"] == "failed" and decided(between) is None
    assert not [r for r in between if r["type"] == fresh.DOCS_KEPT]
    assert decided(got)["verdict"] == "no_change"
    assert len([r for r in got if r["type"] == "test.decided"]) == 1
    assert len([r for r in got if r["type"] == "review.decided"]) == 1


def test_a_run_that_dies_after_the_turn_asks_only_governance_again(dsn, tmp_path):
    down = UP.port(script=UP.script(default={"status": 503}))

    async def go():
        task, _b, ws = await at_checks(dsn, tmp_path, docs_commits=[DOC])
        first = await drive(dsn, task, docs_runners(ws, down))
        for _ in range(3):  # until governance's reruns are spent or it answers
            between = await rows(dsn, task)
            if decided(between):
                break
            await drive(dsn, task, docs_runners(ws))
        return first, await rows(dsn, task)

    first, got = run(go())
    assert first["status"] == "failed"
    kept = [r["payload"] for r in got if r["type"] == fresh.DOCS_KEPT]
    assert len(kept) == 1 and len(fresh_docs_turns(got)) == 1
    d = decided(got)
    assert d["verdict"] == "updated" and d["head"] == kept[0]["kept"] and d["turn_id"] == kept[0]["turn_id"]


def test_after_a_send_back_the_candidate_holds_no_docs_commit_and_the_next_session_sees_them(dsn, tmp_path):
    async def go():
        task, b, ws = await at_checks(
            dsn, tmp_path, review="changes", build="default",
            fresh_acts=["sound", "docs", "docs"], docs_commits=[{"files": {"docs/greeting.md": "Hi.\n"}}],
        )  # fmt: skip
        # Docs, the join sends it back, patch, and docs again on the new candidate.
        await drive(dsn, task, docs_runners(ws))
        return b, await rows(dsn, task)

    b, got = run(go())
    first, second = [r["payload"] for r in got if r["type"] == "docs.decided"]
    new = second["candidate"]["sha"]
    assert new != first["candidate"]["sha"]
    assert not git.is_ancestor(b.mirror, first["head"], new)
    assert "docs/greeting.md" not in git.trusted(b.mirror, "ls-tree", "-r", "--name-only", new).split()
    lay = kws.Layout(Path(b.mirror).parent)
    inputs = lay.checks / f"docs-{new[:12]}" / "repo" / ".valor" / "inputs"
    assert "docs/greeting.md" in (inputs / "previous-docs.patch").read_text()


def test_a_docs_turn_stopped_mid_turn_leaves_no_verdict(dsn, tmp_path):
    async def go():
        task, _b, ws = await at_checks(dsn, tmp_path, fresh_acts=["sound", "hang"])
        running = asyncio.create_task(drive(dsn, task, docs_runners(ws)))
        for _ in range(200):
            if fresh_docs_turns(await rows(dsn, task)):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test", by="test")
        return await running, await rows(dsn, task)

    out, got = run(go())
    assert out["status"] == "stopped"
    assert decided(got) is None and not [r for r in got if r["type"] == fresh.DOCS_KEPT]


# -- the head a session verdict names --------------------------------------------------------


def test_a_session_docs_head_must_be_one_the_kernel_kept(dsn, tmp_path):
    async def go():
        task, _b, ws = await at_checks(dsn, tmp_path)
        c = machine.fold(await rows(dsn, task)).candidate.sha
        head = scripted.commit(ws, "docs/x.md", "x\n", "docs")  # in the builder's clone only
        async with await db.connect(dsn) as conn:
            for h in (head, "abc"):
                with pytest.raises(verdicts.VerdictRefused, match="not a docs head the kernel kept"):
                    await verdicts.record_check(
                        conn, task, Check.DOCS, "updated", head=h, governance_from=[], leg="session",
                        model="m", turn_id="t",
                    )  # fmt: skip
            with pytest.raises(verdicts.VerdictRefused, match="names its governance judgements"):
                await verdicts.record_check(conn, task, Check.DOCS, "no_change", leg="session", model="m",
                                            turn_id="t")  # fmt: skip
        return c

    run(go())


def test_a_kernel_review_verdict_names_no_turn(dsn, tmp_path):
    async def go():
        task, _b, _ws = await at_checks(dsn, tmp_path)
        async with await db.connect(dsn) as conn:
            await verdicts.record_check(
                conn, task, Check.REVIEW, "changes", leg="kernel",
                findings=[{"kind": "commit", "text": "no review checkout"}],
            )  # fmt: skip
        return await rows(dsn, task)

    kernel = [
        r["payload"] for r in run(go()) if r["type"] == "review.decided" and r["payload"]["leg"] == "kernel"
    ]
    assert kernel and kernel[-1]["verdict"] == "changes" and "turn_id" not in kernel[-1]


# -- the config read runs under the profile --------------------------------------------------


def test_the_config_read_runs_under_the_profile_passed(dsn, tmp_path):
    task, b = run(scripted.provisioned(dsn, tmp_path))
    lay = kws.Layout(Path(b.mirror).parent)
    check_dir = kws.fresh_dir(lay.checks / "docs-include")
    checkout = check_dir / "repo"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    denied = Path(b.workspace) / "included.cfg"  # the builder's clone: denied to a check
    denied.write_text("[user]\n\tname = x\n")
    scripted.git(checkout, "config", "include.path", str(denied))
    harness = kws.check_harness(lay, check_dir, [], {}, services=False)
    open_read = git.hostile(checkout)
    assert any(f.startswith("local: include.path") for f in open_read)
    under = git.hostile(checkout, harness["sandbox_profile"], f"docs-config-{task}")
    assert len(under) == 1 and under[0].startswith("the config could not be read"), under
    head = scripted.commit(checkout, "docs/x.md", "x\n", "docs")
    with pytest.raises(kws.FetchRefused, match="could not be read"):
        kws.fetch_into_mirror(b.mirror, checkout, head, "refs/valor/docs-raw/t", harness["sandbox_profile"],
                              f"docs-fetch-{task}")  # fmt: skip
    assert not os.path.exists(Path(b.mirror) / "refs" / "valor" / "docs-raw" / "t")


# -- every join row, with the real test and docs runners -------------------------------------

GREEN = {"greeting.txt": "hi\n"}
RED = {"tests/test_b.py": "def test_kept():\n    assert False\n"}


@pytest.mark.parametrize(
    ("row", "writes", "reviews", "rounds", "calibrated"),
    [
        (1, GREEN, ["pass"], 1, False),
        (2, GREEN, ["governance_refused"], 1, False),
        (3, GREEN, ["changes"], 1, False),
        (4, GREEN, ["changes"], 0, False),
        (5, RED, ["pass"], 1, False),
        (6, RED, ["pass", "pass"], 1, False),
        (7, GREEN, ["pass", "pass"], 1, True),
    ],
)
def test_every_join_row_through_the_router(
    dsn, tmp_path, monkeypatch, row, writes, reviews, rounds, calibrated
):
    if calibrated:
        monkeypatch.setattr(
            judgement_tasks, "BREADTH", dataclasses.replace(judgement_tasks.BREADTH, calibrated="0" * 64)
        )
    breadth = (
        UP.port(script=UP.script(default={"probs": test_checks._gaps("gap_enum")})) if calibrated else None
    )

    async def go():
        task, b = await scripted.provisioned(
            dsn, tmp_path, files=test_checks.BASE_TESTS, suite=test_checks.SUITE
        )
        ws = Path(b.workspace)
        test_checks.change(ws, writes)
        scripted.steer(ws, build="reasons", fresh_acts=["sound", "docs", "docs"], docs_commits=[DOC],
                       counts={"critique_rounds": 0, "review_rounds": rounds})  # fmt: skip
        everything = {**docs_runners(ws), Check.TEST: checks.test_runner(breadth or UP.port(fixed="false"))}
        for verdict in reviews:
            out = await drive(dsn, task, everything)
            assert out["status"] == "no runner" and out["missing"] == ["review"], out
            governance = [verdicts.InstanceSpec("greeting.txt", 1)] if verdict == "governance_refused" else []
            findings = ["say it twice"] if verdict == "changes" else []
            # An ungranted instance makes the reviewer's pass governance_refused.
            said = "pass" if verdict == "governance_refused" else verdict
            await scripted.check(dsn, task, "review", said, governance=governance, findings=findings)
        return await rows(dsn, task)

    got = run(go())
    f = machine.fold(got)
    joined = [r["payload"] for r in got if r["type"] == "task.delivered"]
    if row in (3, 5):
        assert f.state is State.PATCH and f.join.row == row, f.join
    else:
        assert f.state is State.MERGE and joined and joined[-1]["join_row"] == row, joined


def test_a_candidate_whose_tree_holds_valor_is_changes_with_the_reason_and_docs_does_not_rerun(
    dsn, tmp_path, monkeypatch
):
    # The builder's clone hides the entry from the kernel's look there (a
    # replace ref would), so the mirror holds a candidate with `.valor`.
    seen = kws.tree_has_valor
    monkeypatch.setattr(
        kws, "tree_has_valor", lambda *a, trusted, **k: trusted and seen(*a, trusted=trusted, **k)
    )

    async def go():
        task, _b, ws = await test_checks.to_candidate(dsn, tmp_path, writes={".VALOR/x": "x"})
        out = await drive(dsn, task, scripted.fresh_runners(ws))
        assert out["missing"] == ["test", "review", "docs"], out
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        await drive(dsn, task, docs_runners(ws))
        return await rows(dsn, task)

    got = run(go())
    first = next(r["payload"] for r in got if r["type"] == "docs.decided")
    assert first["verdict"] == "changes" and first["leg"] == "kernel" and "turn_id" not in first
    assert "holds a .valor entry" in first["findings"][0]["text"]
    assert not fresh_docs_turns(got)  # no docs session ran
    at = next(i for i, r in enumerate(got) if r["type"] == "docs.decided")
    assert machine.fold(got[: at + 1]).state is State.PATCH  # the join moved on: the repair round
