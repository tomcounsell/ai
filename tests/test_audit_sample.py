"""The audit sample on real Postgres: Tom's labels as rows on their own
stream, what may be labelled, the blind weighted list, the stratified
scores with their coverage, the labels a reverted merge gives (on real git
repositories laid out as `tests/test_outcomes.py` lays them), and that
nothing holds, sends, or briefs a turn with any of it.

The scoring fixtures write session `review.decided` rows (and a merge's
held and done rows) straight into the ledger, since the scores read rows
and nothing else; each test names its own verifier model, so other tests'
reviews in the shared database never enter its figures. The end-to-end
test runs a scripted task through checks and merge (`tests/scripted.py`)
and labels its candidate from the command line.

No model call. Live spend: none.
"""

import asyncio
import itertools
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from core import audit_sample, broker, db, ledger, machine, outcomes, tasks
from core.machine import State
from tests import scripted
from tests import test_outcomes as to
from tests.conftest import TEST_DB

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
MARK = "FINDING-MARKER-7f3a"
ABSENT = object()


def run(coro):
    return asyncio.run(coro)


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB},
        capture_output=True,
        text=True,
        check=False,
    )


def sha(n) -> str:
    return f"{n:040x}" if isinstance(n, int) else n


def model_name() -> str:
    return f"fixture-{uuid.uuid4().hex[:8]}"


async def task(conn, instruction="Write the greeting.", base="b" * 40) -> str:
    return await tasks.start(conn, tasks.Brief(instruction=instruction, base_sha=base))


async def review(conn, task_id, candidate, verdict, model, pf=ABSENT, **extra) -> int:
    """A session review row, as the review runner records one."""
    payload = {
        "candidate": {"sha": sha(candidate), "turn_id": "fixture-turn"},
        "verdict": extra.pop("computed", verdict),
        "reviewer_verdict": verdict,
        "findings": [{"kind": "review", "text": MARK}],
        "leg": "session",
        "model": model,
        "requirements": [],
        "governance": {"adds": False, "instances": []},
        **extra,
    }
    if pf is not ABSENT:
        payload["predicted_failure"] = pf
    return await ledger.append(conn, task_id, "review.decided", payload)


async def merge(conn, task_id, candidate, kind: str | None = "done") -> str:
    """A merge effect held for the candidate, and its outcome when `kind`."""
    effect_id = uuid.uuid4().hex[:12]
    held = {
        "effect_id": effect_id,
        "action_type": "merge",
        "effect_class": "act",
        "target": "main",
        "payload": {
            "head_sha": sha(candidate),
            "candidate": {"sha": sha(candidate), "turn_id": "fixture-turn"},
        },
    }
    await ledger.append(conn, task_id, "effect.held", held)
    if kind is not None:
        await ledger.append(conn, task_id, "effect.outcome", {"effect_id": effect_id, "kind": kind})
    return effect_id


async def label(conn, task_id, candidate, value, **kw):
    return await audit_sample.record(conn, task_id, sha(candidate), value, **kw)


async def scored(conn, model) -> dict:
    return (await audit_sample.scores(conn))["sources"]["tom"][model]


def stratum(f, verdict, w) -> dict:
    return next(s for s in f["strata"] if s["verdict"] == verdict and s["w"] == w)


# -- the label row ---------------------------------------------------------------------------


def test_a_label_is_one_row_on_the_audit_stream_and_role_played_sets_it(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", model_name())
            before = await ledger.read(conn, t)
            real = await label(conn, t, 1, "changes", note="the greeting is wrong")
            stand = await label(conn, t, 1, "pass", by="valor", via="a test", role_played=True)
            rows = [
                r
                for r in await ledger.read(conn, "audit")
                if r["id"] in (real["event_id"], stand["event_id"])
            ]
            return before, await ledger.read(conn, t), rows, t

    before, after, rows, t = run(go())
    assert before == after
    assert [r["type"] for r in rows] == ["audit.labelled", "audit.labelled"]
    p = rows[0]["payload"]
    assert {k: p[k] for k in ("task_id", "candidate_sha", "label", "note")} == {
        "task_id": t,
        "candidate_sha": sha(1),
        "label": "changes",
        "note": "the greeting is wrong",
    }
    assert p["provenance"]["by"] == "tom" and p["provenance"]["role_played"] is False
    assert rows[1]["payload"]["provenance"] == {
        **rows[1]["payload"]["provenance"],
        "by": "valor",
        "role_played": True,
    }


def test_the_command_line_labels_a_reviewed_candidate_and_refuses_everything_else(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", model_name())
            # A kernel review and a review recorded by hand carry no reviewer_verdict.
            await ledger.append(
                conn,
                t,
                "review.decided",
                {"candidate": {"sha": sha(2), "turn_id": "k"}, "verdict": "changes", "leg": "kernel"},
            )
            await ledger.append(
                conn,
                t,
                "review.decided",
                {"candidate": {"sha": sha(3), "turn_id": "m"}, "verdict": "pass", "leg": "manual"},
            )
            return t

    async def labelled():
        async with await db.connect(dsn) as conn:
            return len(await ledger.read(conn, "audit"))

    t = run(go())
    count = run(labelled())
    refused = [
        cli("audit", "label", "no-such-task", sha(1), "pass"),
        cli("audit", "label", t, sha(9), "pass"),
        cli("audit", "label", t, sha(2), "pass"),
        cli("audit", "label", t, sha(3), "pass"),
        cli("audit", "label", t, sha(1), "maybe"),
    ]
    for out in refused:
        assert out.returncode != 0, out.stdout
    assert "no task no-such-task" in refused[0].stderr
    assert "no verifier's review" in refused[1].stderr and "no verifier's review" in refused[2].stderr
    assert "no verifier's review" in refused[3].stderr
    assert "invalid choice" in refused[4].stderr
    assert run(labelled()) == count
    ok = cli("audit", "label", t, sha(1), "pass", "--note", "fine")
    assert ok.returncode == 0, ok.stderr
    assert f"labelled {t} {sha(1)} pass" in ok.stdout
    assert run(labelled()) == count + 1


def test_a_relabel_is_a_new_row_and_the_latest_real_label_is_scored(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m)
            await label(conn, t, 1, "pass")
            await label(conn, t, 1, "changes")
            rows = [r for r in await ledger.read(conn, "audit") if r["payload"]["task_id"] == t]
            return rows, await scored(conn, m)

    rows, f = run(go())
    assert [r["payload"]["label"] for r in rows] == ["pass", "changes"]
    assert f["confusion"] == {"pass/pass": 0, "pass/changes": 1, "changes/pass": 0, "changes/changes": 0}


def test_a_later_stand_in_label_never_replaces_a_real_one(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m)
            before = (await audit_sample.scores(conn))["stand_in_labels"]
            await label(conn, t, 1, "pass")
            await label(conn, t, 1, "changes", by="valor", role_played=True)
            s = await audit_sample.scores(conn)
            return before, s, s["sources"]["tom"][m]

    before, s, f = run(go())
    assert f["confusion"]["pass/pass"] == 1 and f["confusion"]["pass/changes"] == 0
    assert s["stand_in_labels"] == before + 1


def test_a_stand_in_label_leaves_the_candidate_listed_and_out_of_every_figure(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m)
            await label(conn, t, 1, "changes", by="valor", role_played=True)
            listed = [c for c in await audit_sample.sample(conn) if c["task_id"] == t]
            return listed, await scored(conn, m)

    listed, f = run(go())
    assert [c["sha"] for c in listed] == [sha(1)]
    assert f["verdicts"] == 0 and sum(f["confusion"].values()) == 0


def test_a_label_by_someone_else_without_role_played_is_toms_own_printed_under_that_by(dsn):
    m = model_name()
    who = f"alice-{uuid.uuid4().hex[:6]}"

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m)
            await label(conn, t, 1, "pass", by=who)
            s = await audit_sample.scores(conn)
            return s, [c for c in await audit_sample.sample(conn) if c["task_id"] == t]

    s, listed = run(go())
    assert s["sources"]["tom"][m]["verdicts"] == 1
    assert s["labels_by"][who] == 1 and f"{who} 1" in audit_sample.render_scores(s)
    assert listed == []


# -- the list --------------------------------------------------------------------------------


def test_the_key_is_stable_ignores_the_sha_and_a_new_candidate_keeps_the_others_order(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            ts = [await task(conn) for _ in range(6)]
            for i, t in enumerate(ts):
                await review(conn, t, i + 1, "pass" if i % 2 else "changes", m)
            mine = set(ts)
            one = [c["task_id"] for c in await audit_sample.sample(conn) if c["task_id"] in mine]
            two = [c["task_id"] for c in await audit_sample.sample(conn) if c["task_id"] in mine]
            extra = await task(conn)
            await review(conn, extra, 99, "pass", m)
            three = [c["task_id"] for c in await audit_sample.sample(conn) if c["task_id"] in mine]
            return one, two, three

    one, two, three = run(go())
    assert one == two == three and len(one) == 6
    assert audit_sample.key("t", 7, 2) == audit_sample.key("t", 7, 2)
    assert audit_sample.key("t", 7, 2) != audit_sample.key("t", 8, 2)


def test_the_weights_merged_four_latest_pass_two_otherwise_one(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m)
            await merge(conn, t, 1)  # merged: 4
            await review(conn, t, 2, "pass", m)
            await merge(conn, t, 2, kind=None)  # held, never done: unmerged
            await review(conn, t, 3, "pass", m)
            await merge(conn, t, 3, kind="failed")
            await review(conn, t, 7, "pass", m)
            await ledger.append(  # a refused merge: no outcome follows it
                conn,
                t,
                "effect.refused",
                {
                    "effect_id": uuid.uuid4().hex[:12],
                    "action_type": "merge",
                    "payload": {"candidate": {"sha": sha(7), "turn_id": "fixture-turn"}},
                    "reason": "a test",
                },
            )
            await review(conn, t, 4, "changes", m)
            await review(conn, t, 4, "pass", m)  # an earlier changes, latest pass: 2
            await review(conn, t, 5, "pass", m)
            await review(conn, t, 5, "changes", m)  # an earlier pass, latest changes: 1
            await review(conn, t, 6, "changes", m)
            return await scored(conn, m)

    f = run(go())
    n = {(s["verdict"], s["w"]): s["N"] for s in f["strata"]}
    assert n == {
        ("pass", 4): 1,
        ("pass", 2): 4,  # candidates 2, 3, 7, and 4's pass
        ("pass", 1): 1,  # candidate 5's earlier pass
        ("changes", 4): 0,
        ("changes", 2): 1,  # candidate 4's earlier changes
        ("changes", 1): 2,  # candidates 5 and 6
    }


def test_every_candidate_of_a_patched_task_is_its_own_line(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "changes", model_name())
            await review(conn, t, 2, "pass", model_name())
            return t, [c["sha"] for c in await audit_sample.sample(conn) if c["task_id"] == t]

    _t, listed = run(go())
    assert sorted(listed) == [sha(1), sha(2)]


def test_the_list_is_blind_and_pass_and_changes_interleave_with_the_same_columns(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            ts = {}
            for i in range(24):
                t = await task(conn, instruction=f"Greeting number {i}.")
                verdict = "pass" if i % 2 else "changes"
                await review(conn, t, 1000 + i, verdict, m, pf=0.42)
                ts[t] = verdict
            merged = await task(conn, instruction="A merged greeting.")
            await review(conn, merged, 5000, "pass", m, pf=0.42)
            await merge(conn, merged, 5000)
            listed = [
                c for c in await audit_sample.sample(conn) if c["task_id"] in ts or c["task_id"] == merged
            ]
            return ts, listed

    ts, listed = run(go())
    text = audit_sample.render_list(listed)
    for word in ("pass", "changes", "merged", "0.42", MARK, "/task/", "verdict"):
        assert word not in text.replace("A merged greeting.", ""), word
    assert all(set(c) == {"task_id", "instruction", "base_sha", "sha", "read"} for c in listed)
    kinds = [ts[c["task_id"]] for c in listed if c["task_id"] in ts]
    runs = sum(1 for a, b in itertools.pairwise(kinds) if a != b)
    assert runs >= 4, kinds
    assert listed[0]["read"] == f"git -C . diff {'b' * 40} {listed[0]['sha']}"


# -- the scores ------------------------------------------------------------------------------


async def fixture(conn, m, *, label_v3=True) -> list[str]:
    """V1 pass merged 0.2 label pass; V2 pass unmerged 0.6 label changes;
    V3 changes unmerged absent label pass; V4 pass merged unlabelled; V5
    changes unmerged unlabelled."""
    ts = [await task(conn) for _ in range(5)]
    await review(conn, ts[0], 1, "pass", m, pf=0.2)
    await merge(conn, ts[0], 1)
    await review(conn, ts[1], 2, "pass", m, pf=0.6)
    await review(conn, ts[2], 3, "changes", m)
    await review(conn, ts[3], 4, "pass", m, pf=0.9)
    await merge(conn, ts[3], 4)
    await review(conn, ts[4], 5, "changes", m, pf=0.1)
    await label(conn, ts[0], 1, "pass")
    await label(conn, ts[1], 2, "changes")
    if label_v3:
        await label(conn, ts[2], 3, "pass")
    return ts


def test_the_scores_are_exact_on_the_fixture(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            await fixture(conn, m)
            s = await audit_sample.scores(conn)
            return s, s["sources"]["tom"][m]

    s, f = run(go())
    assert {(x["verdict"], x["w"]): (x["N"], x["n"]) for x in f["strata"]} == {
        ("pass", 4): (2, 1),
        ("pass", 2): (1, 1),
        ("pass", 1): (0, 0),
        ("changes", 4): (0, 0),
        ("changes", 2): (0, 0),
        ("changes", 1): (2, 1),
    }
    assert f["confusion"] == {"pass/pass": 1, "pass/changes": 1, "changes/pass": 1, "changes/changes": 0}
    assert f["verdicts"] == 3 and f["candidates"] == 3
    assert (
        f["pass_labelled_changes"]["value"] == pytest.approx(1 / 3) and f["pass_labelled_changes"]["n"] == 2
    )
    assert f["changes_labelled_pass"]["value"] == pytest.approx(1) and f["changes_labelled_pass"]["n"] == 1
    assert f["false_accept"]["value"] == pytest.approx(1) and f["false_accept"]["n"] == 1
    assert f["false_reject"]["value"] == pytest.approx(0.5) and f["false_reject"]["n"] == 2
    assert f["brier"]["value"] == pytest.approx(0.08) and f["brier"]["n"] == 2
    assert f["brier"]["absent"] == 1 and f["brier"]["skipped"] == 0
    text = audit_sample.render_scores(s)
    assert "(changes, 4) N 0 n 0" in text and "Brier score: 0.080 (n 2)" in text
    assert "merged 4, latest review pass 2, otherwise 1" in text and "list order" in text


def test_a_stratum_with_no_label_makes_its_figures_not_estimable(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            await fixture(conn, m, label_v3=False)  # (changes, 1): N 2, n 0
            s = await audit_sample.scores(conn)
            return s, s["sources"]["tom"][m]

    s, f = run(go())
    assert stratum(f, "changes", 1) == {"verdict": "changes", "w": 1, "N": 2, "n": 0}
    assert f["pass_labelled_changes"]["value"] == pytest.approx(1 / 3)
    for name in ("changes_labelled_pass", "false_accept", "false_reject", "brier"):
        assert f[name]["value"] is None and f[name]["not_estimable"] == ["changes, 1"], name
    text = audit_sample.render_scores(s)
    assert "False accepts: not estimable" in text and "changes, 1" in text


def test_two_reviews_of_one_candidate_are_two_verdicts_against_one_label(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m, pf=0.3)
            await review(conn, t, 1, "pass", m, pf=0.5)
            await label(conn, t, 1, "pass")
            return await scored(conn, m)

    f = run(go())
    assert f["verdicts"] == 2 and f["candidates"] == 1 and f["brier"]["n"] == 2


@pytest.mark.parametrize("bad", ["0.3", 1.5, -0.1, True])
def test_a_forecast_that_is_not_a_number_in_range_is_skipped_and_counted(dsn, bad):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m, pf=bad)
            await review(conn, t, 2, "pass", m, pf=0.25)
            await label(conn, t, 1, "pass")
            await label(conn, t, 2, "pass")
            return await scored(conn, m)

    f = run(go())
    assert f["brier"]["n"] == 1 and f["brier"]["skipped"] == 1
    assert f["brier"]["value"] == pytest.approx(0.0625)


def test_a_review_with_no_model_is_scored_under_unknown(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", None)
            return await audit_sample.scores(conn)

    assert "unknown" in run(go())["sources"]["tom"]


def test_two_models_on_one_candidate_are_scored_apart(dsn):
    a, b = model_name(), model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", a)
            await review(conn, t, 1, "changes", b)
            await label(conn, t, 1, "changes")
            return await scored(conn, a), await scored(conn, b)

    fa, fb = run(go())
    assert fa["confusion"]["pass/changes"] == 1 and fa["confusion"]["changes/changes"] == 0
    assert fb["confusion"]["changes/changes"] == 1 and fb["confusion"]["pass/changes"] == 0


def test_a_reviewer_pass_recorded_as_governance_refused_scores_as_pass(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m, computed="governance_refused")
            await label(conn, t, 1, "pass")
            return await scored(conn, m)

    assert run(go())["confusion"]["pass/pass"] == 1


def test_review_compared_rows_are_not_scored(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            t = await task(conn)
            await review(conn, t, 1, "pass", m)
            await ledger.append(
                conn,
                t,
                "review.compared",
                {"candidate": {"sha": sha(1), "turn_id": "x"}, "reviewer_verdict": "changes", "model": m},
            )
            await label(conn, t, 1, "pass")
            return await scored(conn, m)

    f = run(go())
    assert f["verdicts"] == 1 and f["confusion"]["changes/pass"] == 0


def test_the_scores_carry_no_governance_figure(dsn):
    m = model_name()

    async def go():
        async with await db.connect(dsn) as conn:
            await fixture(conn, m)
            return await audit_sample.scores(conn)

    s = run(go())

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    found = {k for k in keys({k: v for k, v in s.items() if k != "sources"})} | {
        k for models in s["sources"].values() for f in models.values() for k in keys(f)
    }
    text = audit_sample.render_scores(s).lower()
    for word in ("governance", "instance", "grant", "guard"):
        assert not any(word in k for k in found), word
        assert word not in text, word


# -- merge outcomes as labels ---------------------------------------------------------------


async def released(conn, w, b, candidate: str, head: str, *, landed: dict | None = None) -> str:
    """A done merge of `candidate` landing `head` (a docs commit on top of
    it), as the broker records one, with `landed` read from the mirror
    unless given."""
    w.land(b, head)
    payload = {
        "url": b.origin_url,
        "target_branch": b.target_branch,
        "head_sha": head,
        "candidate": {"sha": candidate, "turn_id": "fixture-turn"},
    }
    if landed is None:
        landed = await outcomes.landed(conn, b, await ledger.read(conn, b.id), payload)
    effect_id = ledger.new_id()
    await ledger.append(
        conn, b.id, "effect.held", {"effect_id": effect_id, "action_type": "merge", "payload": payload}
    )
    await ledger.append(
        conn, b.id, "effect.intent", {"effect_id": effect_id, "action_type": "merge", "landed": landed}
    )
    await ledger.append(conn, b.id, "effect.outcome", {"effect_id": effect_id, "kind": "done"})
    return effect_id


def reviewed_and_merged(dsn, w, b, m, **kw) -> tuple[str, str]:
    """B's candidate reviewed `pass` by model `m` and merged with a docs
    commit on top; returns the candidate and the head."""

    async def go():
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, b)
            w.branch("main")
            candidate = to.commit(w.ws, "a.py", "a = 1\n")
            head = to.commit(w.ws, "docs/a.md", "a\n")
            await review(conn, b.id, candidate, "pass", m, pf=0.3)
            await released(conn, w, b, candidate, head, **kw)
            return candidate, head

    return run(go())


def scores_and_labels(dsn) -> tuple[dict, list]:
    async def go():
        async with await db.connect(dsn) as conn:
            return await audit_sample.scores(conn), await audit_sample.labels(conn)

    return run(go())


def test_a_reverted_merge_labels_its_candidate_changes_under_source_revert(dsn, tmp_path):
    w, m = to.World(tmp_path), model_name()
    b = w.brief()
    candidate, head = reviewed_and_merged(dsn, w, b, m)
    r = to.on_target(w, f"Revert a\n\nThis reverts commit {candidate}.\n")
    s, found = scores_and_labels(dsn)
    (lab,) = [x for x in found if x["task_id"] == b.id]
    assert lab["candidate_sha"] == candidate != head
    assert lab["label"] == "changes" and lab["source"] == audit_sample.REVERT
    assert lab["provenance"]["by"] == "git" and lab["provenance"]["role_played"] is False
    assert lab["provenance"]["via"] == f"revert of {candidate}" and r != candidate
    f = s["sources"]["revert"][m]
    assert f["verdicts"] == 1 and f["confusion"]["pass/changes"] == 1
    assert f["false_accept"]["value"] == 1.0 and f["false_accept"]["n"] == 1
    assert s["sources"]["tom"][m]["verdicts"] == 0 and m not in str(s["labels_by"])
    assert f"## {m}, labels from revert" in audit_sample.render_scores(s)
    # the list is Tom's: a revert label leaves the candidate on it
    listed = run(_listed(dsn))
    assert (b.id, candidate) in listed


async def _listed(dsn) -> set:
    async with await db.connect(dsn) as conn:
        return {(c["task_id"], c["sha"]) for c in await audit_sample.sample(conn)}


def test_no_revert_seen_gives_no_label(dsn, tmp_path):
    """No reading (a task on its own origin), a `reverted_by` that is empty
    (no revert on the target), one that is None (no recorded commits), and
    a branch moved off the head with no revert each yield no label."""
    w = to.World(tmp_path)
    private, plain, unrecorded, moved = (
        w.brief(private=True), w.brief(), w.brief(), w.brief()
    )  # fmt: skip
    m = model_name()
    for b in (private, plain):
        reviewed_and_merged(dsn, w, b, m)
    reviewed_and_merged(
        dsn, w, unrecorded, m, landed={"before": w.base, "commits": None, "paths": None, "why": "GitError"}
    )
    reviewed_and_merged(dsn, w, moved, m)
    w.fetch()
    to.sh(w.ws, "push", "-q", "-f", str(w.up), f"{w.base}:refs/heads/main")
    w.fetch()

    async def readings():
        async with await db.connect(dsn) as conn:
            out = {}
            for b in (private, plain, unrecorded, moved):
                (mm,) = outcomes.merges(await ledger.read(conn, b.id))
                out[b.id] = outcomes.revert(b, mm)
            return out

    got = run(readings())
    assert got[private.id] == (None, outcomes.PRIVATE_TARGET)
    assert got[unrecorded.id][0]["reverted_by"] is None
    assert got[moved.id][0]["on_branch"] is False and got[moved.id][0]["reverted_by"] == []
    s, found = scores_and_labels(dsn)
    assert not [x for x in found if x["task_id"] in {private.id, plain.id, unrecorded.id, moved.id}]
    assert s["sources"]["revert"][m]["verdicts"] == 0


# -- nothing holds, sends, or briefs ---------------------------------------------------------


@pytest.mark.macos
def test_labels_change_no_task_row_merge_effect_notice_or_review_brief(dsn, tmp_path):
    """Two tasks through checks and merge; one has its candidate labelled
    from the command line (a `changes` against the reviewer's `pass`, and a
    stand-in's label), the other none. Their rows, merge effects, and
    notices match; the review Brief is the same before and after."""
    from tests.test_pipeline import drive, to_checks

    ws_a, origin_a = scripted.workspace(tmp_path / "a")
    ws_b, _ = scripted.workspace(tmp_path / "b")
    m = model_name()

    async def to_merge(ws):
        t = await to_checks(dsn, ws)
        await scripted.check(dsn, t, "test", "pass")
        await scripted.check(dsn, t, "review", "pass", model=m, predicted_failure=0.1)
        await scripted.check(dsn, t, "docs", "no_change")
        return t

    async def finish(t):
        async with await db.connect(dsn) as conn:
            f = machine.fold(await ledger.read(conn, t))
            effect = f.merge_effect["effect_id"]
            await broker.approve(conn, effect, note="merge it")
            done = await scripted.release(conn, effect)
        await drive(dsn, t)
        return done

    async def brief(t):
        async with await db.connect(dsn) as conn:
            return await tasks.dispatch(conn, t, fresh="review")

    async def read(t):
        async with await db.connect(dsn) as conn:
            return await ledger.read(conn, t)

    a = run(to_merge(ws_a))
    b = run(to_merge(ws_b))
    candidate = run(read(a))
    candidate = machine.fold(candidate).candidate.sha
    brief_before, rows_before = run(brief(a)), run(read(a))
    assert cli("audit", "label", a, candidate, "changes", "--note", "wrong greeting").returncode == 0
    assert cli("audit", "label", a, candidate, "pass", "--by", "valor", "--role-played").returncode == 0
    assert run(read(a)) == rows_before
    assert run(brief(a)) == brief_before
    assert MARK not in brief_before["text"] and "audit" not in brief_before["text"].lower()
    done_a, done_b = run(finish(a)), run(finish(b))
    rows_a, rows_b = run(read(a)), run(read(b))
    assert done_a.kind == done_b.kind == "done"
    assert [r["type"] for r in rows_a] == [r["type"] for r in rows_b]
    fa, fb = machine.fold(rows_a), machine.fold(rows_b)
    assert fa.state is fb.state is State.MERGED
    assert fa.merge_effect["state"] == fb.merge_effect["state"] == "done"
    assert scripted.git(origin_a, "rev-parse", "main") == candidate

    async def audit_rows():
        async with await db.connect(dsn) as conn:
            return {r["type"] for r in await ledger.read(conn, "audit")}

    assert run(audit_rows()) == {"audit.labelled"}
    scores = cli("audit", "scores")
    assert scores.returncode == 0 and f"## {m}, labels from tom" in scores.stdout
    listing = cli("audit")
    assert listing.returncode == 0 and candidate not in listing.stdout
