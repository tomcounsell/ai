"""The emulator sweep's findings (`routines/emulator/runner.py`): the hidden
test command's output read, the D5 record's bar applied against this run's
`bare`, and each worse item started as one fix task.

The real outputs below are written from the D5 record's counts and ids
(docs/plans/d5-cutover-sweep-record.md), in the shape each item's first
verify command prints through its grep: pytest's `FAILED` lines and summary
for the popoto items, Django's `FAIL:` lines, `Ran`, and `OK` or
`FAILED (...)` for the psyoptimal items."""

import asyncio
import dataclasses
import json

import pytest

from core import db, ledger, routines, tasks
from core.settings import settings
from routines.emulator import runner
from tests.test_findings import fake_start, with_project
from tests.test_objective_tree import run
from tests.test_routines import unique, where, write_toml  # noqa: F401  (where is a fixture)

HIDDEN = "tests/test_zz_hidden_reference.py"
DJANGO = "apps.public.tests.test_zz_hidden_reference"


def pytest_out(passed: int, failing: list[str], secs: str = "3.21") -> str:
    lines = [f"FAILED {HIDDEN}::{t}" for t in failing]
    counts = ([f"{len(failing)} failed"] if failing else []) + [f"{passed} passed"]
    return "\n".join([*lines, f"{', '.join(counts)} in {secs}s"]) + "\n"


def django_out(ran: int, failing: list[str]) -> str:
    lines = [f"FAIL: {t} ({DJANGO}.HiddenReferenceTest.{t})" for t in failing]
    lines.append(f"Ran {ran} tests in 4.512s")
    lines.append(f"FAILED (failures={len(failing)})" if failing else "OK")
    return "\n".join(lines) + "\n"


PSO_A_BOTH = [
    "test_archived_team_assign_and_revoke_return_json_403",
    "test_archived_team_member_detail_modal_hides_assign_and_revoke",
]
# Per item and arm: (outcome, fidelity/correctness/simplicity, the first
# verify command's output tail, and a broad command's after it).
D5 = {
    "pop-b": {
        "bare": ("merged", (3, 4, 1), pytest_out(10, ["test_push_on_unsaved_model_raises"])),
        "clarify": ("merged", (3, 5, 1), pytest_out(11, [])),
        "routed": ("merged", (3, 3, 2), pytest_out(10, ["test_push_complex_types"])),
    },
    "pso-a": {
        "bare": ("merged", (4, 3, 3), django_out(22, PSO_A_BOTH)),
        "clarify": ("merged", (4, 3, 4), django_out(22, [*PSO_A_BOTH, "test_api_available_evaluations_200_for_coach"])),
        "routed": ("merged", (4, 3, 4), django_out(22, PSO_A_BOTH)),
    },
    "pso-b": {
        "bare": ("merged", (4, 4, 3), django_out(66, [])),
        "clarify": ("merged", (4, 4, 3), django_out(62, [])),
        "routed": ("merged", (4, 4, 3), django_out(63, [])),
    },
    "pop-a": {
        "bare": ("merged", (4, 3, 2), pytest_out(59, [])),
        "clarify": ("merged", (4, 4, 3), pytest_out(59, [])),
        "routed": ("merged", (4, 4, 4), pytest_out(59, [])),
    },
}  # fmt: skip
# Pop-a's broad command, after the hidden one: Redis down on the judge host.
POP_A_BROAD = (
    "".join(f"FAILED tests/test_integrations_hooks.py::test_hook_{i}\n" for i in range(3))
    + "4 failed, 210 passed in 40.10s\n"
)


def result(outcome, scores, tail, *, task="t", questions=(), broad=None) -> dict:
    verification = [{"command": "hidden", "exit": 0, "output_tail": tail}]
    if broad is not None:
        verification.append({"command": "broad", "exit": 1, "output_tail": broad})
    f, c, s = scores
    return {
        "outcome": outcome,
        "task_id": task,
        "questions": list(questions),
        "judge": {"scores": {"fidelity": f, "correctness": c, "simplicity": s}, "verification": verification},
    }


def rows_for(item: str) -> dict:
    out = {}
    for arm, (outcome, scores, tail) in D5[item].items():
        broad = POP_A_BROAD if item == "pop-a" else None
        out[arm] = runner._row(result(outcome, scores, tail, broad=broad), 0)
    return out


# -- the parser --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "arm", "failed", "failing"),
    [
        ("pop-b", "bare", 1, [f"{HIDDEN}::test_push_on_unsaved_model_raises"]),
        ("pop-b", "clarify", 0, []),
        ("pop-b", "routed", 1, [f"{HIDDEN}::test_push_complex_types"]),
        ("pso-a", "bare", 2, [f"{t} ({DJANGO}.HiddenReferenceTest.{t})" for t in PSO_A_BOTH]),
        ("pso-a", "clarify", 3, None),
        ("pso-b", "clarify", 0, []),
        ("pop-a", "bare", 0, []),
    ],
)
def test_the_hidden_output_gives_the_d5_records_counts(item, arm, failed, failing):
    got = runner.hidden(D5[item][arm][2])
    assert got["failed"] == failed
    if failing is not None:
        assert got["failing"] == sorted(failing)


def test_a_tail_cut_to_its_last_4000_characters_keeps_its_summary_count():
    noise = "".join(f"FAILED {HIDDEN}::test_case_{i:04d}\n" for i in range(300))
    whole = noise + "300 failed, 12 passed in 9.99s\n"
    cut = whole[-4000:]
    got = runner.hidden(cut)
    assert len(cut) == 4000 and got["failed"] == 300 and len(got["failing"]) < 300
    assert runner.hidden("ls docs\ngrep: nothing\n") is None


# -- the bar -----------------------------------------------------------------------------------


BARE = {"outcome": "merged", "scores": {"fidelity": 4, "correctness": 4, "simplicity": 4},
        "hidden": {"failed": 1, "failing": ["a"]}}  # fmt: skip


@pytest.mark.parametrize(
    ("change", "flags"),
    [
        ({"outcome": "accepted"}, 1),  # short of a merge where bare merged
        ({"scores": {"fidelity": 4, "correctness": 3, "simplicity": 3}}, 1),  # two points below
        ({"scores": {"fidelity": 4, "correctness": 4, "simplicity": 3}}, 0),  # exactly one below
        ({"hidden": {"failed": 2, "failing": ["a", "b"]}}, 1),  # more hidden failures
        ({"hidden": {"failed": 1, "failing": ["b"]}}, 0),  # as many, other ids: evidence only
        ({"hidden": None}, 0),  # not read: the rule is not applied
    ],
)
def test_each_rule_of_the_bar_flags_alone(change, flags):
    assert len(runner.worse({**BARE, **change}, BARE)) == flags


@pytest.mark.parametrize(
    ("item", "worse"),
    [("pso-b", set()), ("pso-a", {"clarify"}), ("pop-b", set()), ("pop-a", set())],
)
def test_the_bar_on_the_d5_records_outputs(item, worse):
    assert set(runner.regressions({"items": {item: rows_for(item)}}).get(item, {})) == worse


@pytest.mark.parametrize(
    "rows",
    [
        {"bare": {**BARE, "outcome": None}, "clarify": {**BARE, "outcome": "accepted"}},
        {"bare": BARE, "clarify": {**BARE, "outcome": "stopped"}},
        {"bare": {"failed": "driver output"}, "clarify": {**BARE, "outcome": "accepted"}},
    ],
)
def test_an_arm_not_compared_flags_nothing(rows):
    assert runner.regressions({"items": {"it": rows}}) == {}


# -- firings -----------------------------------------------------------------------------------


@pytest.fixture
def sweep(where, tmp_path, monkeypatch):  # noqa: F811
    """A routine on the emulator runner naming a project, and a replay
    directory whose driver writes the result files `plan` holds."""
    name = unique()
    write_toml(where, name, runner="emulator", ceiling="act")
    with_project(where / name / "routine.toml")
    demo = tmp_path / "demo"
    (demo / "items").mkdir(parents=True)
    (demo / "results").mkdir()
    monkeypatch.setattr(runner, "settings", dataclasses.replace(settings, demo_dir=str(demo)))
    plan: dict[str, dict[str, dict]] = {}
    calls: list[tuple[str, str]] = []

    async def drive(item, arm, run_id, nm):
        calls.append((nm, run_id))
        await asyncio.sleep(0.2)
        got = plan[json.loads(item.read_text())["name"]][arm]
        (demo / "results" / f"{nm}.json").write_text(json.dumps(got))
        return 0, ""

    monkeypatch.setattr(runner, "_drive", drive)

    def add(item: str, arms: dict[str, dict]) -> None:
        (demo / "items" / f"{item}.json").write_text(json.dumps({"name": item}))
        plan[item] = arms

    return name, add, calls


async def fire(dsn, name) -> str:
    async with await db.connect(dsn) as conn:
        return await routines.run(conn, name, {"emulator": runner.run}, start_project=fake_start, dsn=dsn)


async def tree(dsn, name) -> tuple[str, list[str], dict[str, list[dict]]]:
    """The objective, its run nodes, and each run's fix tasks' started rows."""
    async with await db.connect(dsn) as conn:
        (reg,) = await routines.registrations(conn, name)
        nodes, found = [], {}
        for child in await tasks.children(conn, reg["objective"]):
            if (await ledger.read(conn, child))[0]["payload"].get("objective") == name:
                nodes.append(child)
                found[child] = [
                    {**(await ledger.read(conn, f))[0]["payload"], "brief": await tasks.brief(conn, f)}
                    for f in await tasks.children(conn, child)
                ]
        return reg["objective"], nodes, found


def done_case() -> dict[str, dict]:
    bare = pytest_out(9, ["test_a", "test_b"])
    clarify = pytest_out(8, ["test_a", "test_b", "test_c"])
    asked = [{"question": "Which list?", "answer": "The capped one."}]
    return {
        "bare": result("merged", (4, 4, 4), bare, task="replay-bare"),
        "clarify": result("merged", (4, 4, 4), clarify, task="replay-clarify", questions=asked),
        "routed": result("merged", (4, 4, 4), bare, task="replay-routed"),
    }


def test_an_item_worse_than_bare_starts_one_task_carrying_the_evidence(dsn, sweep):
    name, add, _ = sweep
    item = unique()  # a key is global to the ledger
    add(item, done_case())
    said = run(fire(dsn, name))
    assert "finished" in said
    _, (node,), found = run(tree(dsn, name))
    (fix,) = found[node]
    assert fix["finding"] == {"keys": [f"emulator:{item}"], "source": name}
    text = fix["brief"].instruction
    assert "clarify 3 hidden failures against bare's 2" in text
    assert f"failing in clarify and not in bare: {HIDDEN}::test_c" in text
    assert "Which list?" in text and "The capped one." in text
    for arm in ("bare", "clarify", "routed"):
        assert f"replay task replay-{arm}" in text
    assert "'fidelity': 4" in text and fix["brief"].max_effect_class == "act"

    assert "finished" in run(fire(dsn, name))  # the next sweep holds the key
    _, nodes, found = run(tree(dsn, name))
    assert len(nodes) == 2 and sum(len(f) for f in found.values()) == 1


def test_an_item_worse_in_both_arms_starts_one_task(dsn, sweep):
    name, add, _ = sweep
    arms = done_case()
    arms["routed"] = arms["clarify"]
    item = unique()
    add(item, arms)
    run(fire(dsn, name))
    _, (node,), found = run(tree(dsn, name))
    (fix,) = found[node]
    assert "clarify" in fix["brief"].instruction and "routed 3 hidden failures" in fix["brief"].instruction


def test_a_crash_between_the_report_and_its_findings_is_continued(dsn, sweep, monkeypatch):
    from core import findings

    name, add, calls = sweep
    item = unique()  # a key is global to the ledger
    add(item, done_case())
    real = findings.start_fix

    async def crash(*a, **kw):
        raise RuntimeError("the kernel died")

    monkeypatch.setattr(findings, "start_fix", crash)
    with pytest.raises(RuntimeError, match="the kernel died"):
        run(fire(dsn, name))
    monkeypatch.setattr(findings, "start_fix", real)
    assert "finished" in run(fire(dsn, name))
    _, (node,), found = run(tree(dsn, name))
    assert len(found[node]) == 1 and len(calls) == 3  # the replays ran once


def test_a_hand_child_is_never_a_run_and_two_firings_at_once_make_one(dsn, sweep):
    name, add, _ = sweep
    item = unique()  # a key is global to the ledger
    add(item, done_case())

    async def go():
        async with await db.connect(dsn) as conn:
            obj, _ = await routines.ensure(conn, routines.load(name))
            hand = await tasks.start_child(conn, obj, instruction="by hand", marker={}, routine=name)
        return hand, await asyncio.gather(fire(dsn, name), fire(dsn, name))

    hand, said = run(go())
    _, nodes, _ = run(tree(dsn, name))
    assert len(nodes) == 1 and hand not in nodes
    assert sorted(" running;" in s for s in said) == [False, True], said
