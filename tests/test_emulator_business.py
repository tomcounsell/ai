"""The emulator's business case set (docs/emulator.md, Business items): a
business item names the decision that was merged and is judged on
agreement with it alone; a run records its decision record; the
perspective measure is read from that record and nothing else; and a
base's plan documents are dropped from the tree a replay starts from.

The judge's `claude` is a small Python program that writes what it was
given to a file and prints a verdict as `claude -p` would. No model call.

Live spend: none.
"""

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests import scripted
from tests.emulator import business, common, judge, replay, workspace

pytestmark = pytest.mark.spend(usd=0)

DECISION = "One annual tier at $749/year; the intro episode publishes without payment."


def _report(record: dict | str) -> str:
    body = record if isinstance(record, str) else json.dumps(record)
    return f"Pricing is set.\n---\nWhat I delivered.\n\n```decision-record\n{body}\n```\n"


RECORD = {
    "recommended": "$99/month",
    "chosen": "$749/year",
    "objections": [
        {"perspective": "pricing strategist", "objection": "monthly invites churn", "changed_choice": True},
        {"perspective": "advisor", "objection": "anchor against a higher rate", "changed_choice": False},
    ],
}


# -- the decision record ------------------------------------------------------------


def test_a_decision_record_is_read_from_the_last_block_of_a_delivery():
    text = _report({"recommended": "a", "chosen": "a", "objections": []}) + _report(RECORD)
    record = business.decision_record(text)
    assert record["recommended"] == "$99/month" and record["chosen"] == "$749/year"
    assert business.changed_by(record) == ["pricing strategist"]
    assert business.decision_record("No record here.\n---\nA report.") is None


@pytest.mark.parametrize(
    "body",
    [
        "{not json",
        "[1, 2]",
        json.dumps({"chosen": "x", "objections": [{"objection": "no one raised this"}]}),
    ],
)
def test_a_record_not_of_its_shape_is_unreadable_and_names_no_perspective(body):
    record = business.decision_record(_report(body))
    assert "unreadable" in record and record["text"].strip() == body
    assert business.changed_by(record) == []


def test_changed_choice_counts_only_when_it_is_true():
    record = business.decision_record(
        _report({"objections": [{"perspective": "p", "objection": "o", "changed_choice": "yes"}]})
    )
    assert record["objections"][0]["changed_choice"] is False and business.changed_by(record) == []


def test_the_run_records_the_last_delivery_that_carries_a_record():
    deliveries = [_report(RECORD), "A patch with no record.\n---\nFixed the copy."]
    assert business.latest_record(deliveries)["chosen"] == "$749/year"
    assert business.latest_record(["no record"]) is None and business.latest_record([]) is None


# -- the perspective measure ---------------------------------------------------------


def _result(run: str, record, *, agreement=None, **extra) -> dict:
    item = {"name": run.rsplit("-", 1)[0], "kind": "business", "decision": DECISION}
    return {
        "run": run,
        "item": item,
        "decision_record": record,
        **({"judge": {"scores": {"agreement": agreement}}} if agreement is not None else {}),
        **extra,
    }


def test_the_perspective_measure_is_read_from_the_decision_record_alone():
    """A perspective counts as having changed a decision only where the
    record names its objection as having changed the option chosen;
    whether its text was used, edited, or discarded counts for nothing."""
    second = {
        "recommended": "free trial",
        "chosen": "free trial",
        "objections": [{"perspective": "advisor", "objection": "o", "changed_choice": False}],
    }
    results = [
        _result("biz-a-routed", business.decision_record(_report(RECORD)), agreement=5),
        _result(
            "biz-b-routed",
            business.decision_record(_report(second)),
            agreement=1,
            # Marks of what was done with the text are not read.
            deliveries_used=[{"perspective": "advisor", "used": True, "edited": False}],
        ),
        _result("biz-c-routed", None),
        _result("biz-d-routed", {"unreadable": "not JSON", "text": "{"}),
    ]
    measure = business.perspective_measure(results)
    assert measure["perspectives"] == {
        "advisor": {"raised": ["biz-a-routed", "biz-b-routed"], "changed": []},
        "pricing strategist": {"raised": ["biz-a-routed"], "changed": ["biz-a-routed"]},
    }
    assert measure["unrecorded"] == ["biz-c-routed", "biz-d-routed"]

    shown = business.report([*results, {"run": "cut-a-bare", "item": {"name": "cut-a"}}])
    assert [(r["run"], r["agreement"], r["changed_by"]) for r in shown["runs"]] == [
        ("biz-a-routed", 5, ["pricing strategist"]),
        ("biz-b-routed", 1, []),
        ("biz-c-routed", None, []),
        ("biz-d-routed", None, []),
    ]
    assert all(r["decision"] == DECISION for r in shown["runs"])


def test_the_report_reads_result_files_as_a_module(tmp_path):
    path = tmp_path / "biz-a-routed.json"
    path.write_text(json.dumps(_result("biz-a-routed", business.decision_record(_report(RECORD)), agreement=4)))
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-m", "tests.emulator.business", str(path)], cwd=root, capture_output=True, text=True,
        check=True,
    )  # fmt: skip
    shown = json.loads(out.stdout)
    assert shown["runs"][0]["agreement"] == 4
    assert shown["perspectives"]["pricing strategist"]["changed"] == ["biz-a-routed"]


# -- the item ----------------------------------------------------------------------


def _item(tmp_path: Path, **over) -> Path:
    (tmp_path / "biz-a.key.md").write_text("Our customers are founders.\n")
    item = {
        "name": "biz-a",
        "kind": "business",
        "repo": "yudame/cuttlefish",
        "base": "0" * 40,
        "request": "What should we charge?",
        "answer_key": "biz-a.key.md",
        "decision": DECISION,
        "drop": ["docs/plans"],
        **over,
    }
    path = tmp_path / "biz-a.json"
    path.write_text(json.dumps({k: v for k, v in item.items() if v is not None}))
    return path


def test_a_business_item_names_the_decision_that_was_merged(tmp_path):
    item = replay.load_item(str(_item(tmp_path)))
    assert item["decision"] == DECISION and item["drop"] == ["docs/plans"]
    with pytest.raises(SystemExit, match="'decision'"):
        replay.load_item(str(_item(tmp_path, decision="")))
    code = replay.load_item(str(_item(tmp_path, kind=None, decision=None, drop=None)))
    assert "decision" not in code and code["drop"] == []


# -- the clean base ----------------------------------------------------------------


def test_the_clean_base_drops_the_plans_and_the_history_and_is_the_same_each_time(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DEMO", tmp_path / "demo")
    (tmp_path / "demo" / "cache").mkdir(parents=True)
    src = tmp_path / "src"
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    scripted.commit(src, "app/pricing.py", "PRICE = 99\n", "first")
    scripted.commit(src, "docs/plans/pricing.md", "Charge $749/year.\n", "plan")
    scripted.commit(src, "docs/plans-archive/old.md", "kept\n", "a sibling path")
    base = scripted.commit(src, "docs/features/pricing.md", "The price is monthly.\n", "doc")
    cache = tmp_path / "cache.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(cache)], check=True)

    clean = workspace.clean_base(cache, base, ["docs/plans/"])
    files = scripted.git(cache, "ls-tree", "-r", "--name-only", clean).splitlines()
    assert files == ["app/pricing.py", "docs/features/pricing.md", "docs/plans-archive/old.md"]
    assert scripted.git(cache, "rev-list", "--count", clean) == "1"
    assert scripted.git(cache, "show", f"{clean}:app/pricing.py") == "PRICE = 99"
    assert workspace.clean_base(cache, base, ["docs/plans"]) == clean
    assert scripted.git(cache, "rev-parse", f"refs/heads/clean/{clean}") == clean
    assert not list((tmp_path / "demo" / "cache").glob("*.index"))


# -- the business judge --------------------------------------------------------------

FAKE_CLAUDE = r"""#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_OUT"], "a") as f:
    f.write(json.dumps({{"prompt": sys.stdin.read(), "system": args[args.index("--system-prompt") + 1]}}) + "\n")
print(json.dumps({{"result": os.environ["FAKE_CLAUDE_REPLY"], "is_error": False}}))
"""


def test_a_business_run_is_judged_on_agreement_with_the_merged_decision(dsn, tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    seen = tmp_path / "seen.jsonl"
    verdict = {
        "scores": {"agreement": 3},
        "candidate_decision": "One annual tier at $699/year.",
        "divergences": ["the price differs by $50"],
        "rationale": "same structure, different price",
    }
    monkeypatch.setattr(common, "CLAUDE", str(exe))
    monkeypatch.setattr(common, "DEMO", tmp_path / "demo")
    monkeypatch.setattr(common, "LOCK", tmp_path / "demo" / "claude-turn.lock")
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(seen))
    monkeypatch.setenv("FAKE_CLAUDE_REPLY", "Verdict:\n" + json.dumps(verdict))

    ws, _origin = scripted.workspace(tmp_path)
    base = scripted.git(ws, "rev-parse", "HEAD")
    rev = scripted.commit(ws, "pricing.py", "PRICE = '$699/year'\n", "candidate")
    mirror = tmp_path / "kernel.git"
    subprocess.run(["git", "init", "-q", "--bare", str(mirror)], check=True)
    scripted.git(ws, "push", "-q", str(mirror), "+HEAD:refs/heads/candidate")
    (tmp_path / "ref.diff").write_text("+PRICE = '$749/year'\n")
    item = replay.load_item(str(_item(tmp_path, reference_diff="ref.diff")))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    task = common.start_emulator_task("biz-a-routed", None, dsn=dsn)
    path = tmp_path / "biz-a-routed.json"
    path.write_text(
        json.dumps(
            {
                "run": "biz-a-routed",
                "arm": "routed",
                "item": item,
                "emulator_task": task,
                "final_rev": rev,
                "workspace": {"mirror": str(mirror), "base": base, "run_dir": str(run_dir)},
                "questions": [{"question": "Which price?", "answer": "Your call."}],
                "deliveries": [_report(RECORD)],
                "decision_record": business.decision_record(_report(RECORD)),
            }
        )
    )

    with common.Meter(task, dsn=dsn, upstream="http://127.0.0.1:1", login=False) as meter:
        result = judge.judge(str(path), meter=meter)

    assert result["judge"]["scores"] == {"agreement": 3}
    assert result["judge"]["candidate_decision"] == "One annual tier at $699/year."
    assert result["judge"]["divergences"] == ["the price differs by $50"]
    assert result["judge"]["verification"] == []
    assert json.loads(path.read_text())["judge"]["scores"] == {"agreement": 3}
    (call,) = [json.loads(line) for line in seen.read_text().splitlines()]
    assert call["system"] == business.SYSTEM
    prompt = call["prompt"]
    for shown in ("What should we charge?", "Our customers are founders.", DECISION, "$749/year'", "$699/year'"):
        assert shown in prompt
    # Blind to the arm, the questions, and Valor's own account of the work.
    for hidden in ("routed", "Which price?", "pricing strategist", "decision-record"):
        assert hidden not in prompt
