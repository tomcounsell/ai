"""The product Sheet helper (skills/valor/skills/product-sheet/sheet.py):
the ranking rule, the chase and review list, and `order` writing the
Queue back through a fake `gws`."""

import importlib.util
import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sheet", ROOT / "skills/valor/skills/product-sheet/sheet.py")
sheet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sheet)


def row(id, status, lane="bet", value="medium", appetite="medium", due=""):
    return {"id": id, "lane": lane, "rank": "9", "item": id, "goal": "", "value": value, "appetite": appetite,
            "due": due, "status": status, "blocked by": "", "issue": "", "dev thread": "", "review on": ""}  # fmt: skip


def test_in_flight_first_then_ready_by_lane_value_appetite_and_only_ready_rows_are_ranked():
    queue = [
        row("P-1", "raw", value="high"),
        row("P-2", "ready", value="low", appetite="small"),
        row("P-3", "ready", value="high", appetite="large"),
        row("P-4", "ready", value="high", appetite="small"),
        row("P-5", "ready", lane="mandatory", value="low", due="2026-12-01"),
        row("P-6", "ready", lane="mandatory", value="high"),
        row("P-7", "ready", lane="mandatory", due="2026-11-10"),
        row("P-8", "building"),
        row("P-9", "shipped", value="high"),
        row("P-10", "shaped", value="high"),
        row("P-11", "approved"),
    ]
    got = sheet.ordered(queue)
    assert [r["id"] for r in got] == [
        "P-11", "P-8",  # in flight, in their place
        "P-7", "P-5", "P-6",  # mandatory by due date, none last
        "P-4", "P-3", "P-2",  # bets by value, then the smaller appetite
        "P-10", "P-1", "P-9",
    ]  # fmt: skip
    assert [r["rank"] for r in got] == ["", "", "1", "2", "3", "4", "5", "6", "", "", ""]


def test_ties_keep_their_place_so_a_hand_reorder_stands():
    queue = [row("P-2", "ready"), row("P-1", "ready")]
    assert [r["id"] for r in sheet.ordered(queue)] == ["P-2", "P-1"]


def test_due_lists_open_blockers_to_chase_and_shipped_items_to_review():
    blocked = [
        {"item": "P-1", "blocker": "a", "chase on": "2026-11-02", "status": "open"},
        {"item": "P-2", "blocker": "b", "chase on": "2026-11-03", "status": "open"},
        {"item": "P-3", "blocker": "c", "chase on": "2026-11-01", "status": "cleared"},
        {"item": "P-4", "blocker": "d", "chase on": "", "status": "open"},
    ]
    queue = [{**row("P-5", "shipped"), "review on": "2026-11-02"}, {**row("P-6", "shipped"), "review on": "2026-11-20"},
             {**row("P-7", "reviewed"), "review on": "2026-10-01"}]  # fmt: skip
    got = sheet.due(blocked, queue, date(2026, 11, 2))
    assert [b["item"] for b in got["chase"]] == ["P-1"]
    assert [r["id"] for r in got["review"]] == ["P-5"]


def test_due_gives_every_open_blocker_with_the_days_it_has_waited():
    blocked = [
        {"item": "P-1", "owner": "Kim", "asked on": "2026-10-30", "chase on": "2026-11-02", "status": "open"},
        {"item": "P-2", "owner": "P-9", "asked on": "", "chase on": "2026-11-05", "status": "open"},
        {"item": "P-3", "owner": "Lee", "asked on": "2026-10-01", "chase on": "", "status": "Cleared"},
    ]
    got = sheet.due(blocked, [], date(2026, 11, 2))["open"]
    assert [(b["item"], b["owner"], b["waited"], b["chase due"]) for b in got] == [
        ("P-1", "Kim", 3, True), ("P-2", "P-9", None, False),
    ]  # fmt: skip


def test_order_writes_the_whole_queue_back_and_prints_what_moved(monkeypatch, capsys):
    stored = [[*row("P-1", "raw").values()], [*row("P-2", "ready").values()]]
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        out = {"values": stored} if "get" in cmd else {}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(sheet.subprocess, "run", fake)
    assert sheet.main(["order", "SHEET"]) == 0
    (update,) = [c for c in calls if "update" in c]
    params, body = (
        json.loads(update[update.index("--params") + 1]),
        json.loads(update[update.index("--json") + 1]),
    )
    assert params["range"] == "Queue!A2:Z3"
    assert [r[0] for r in body["values"]] == ["P-2", "P-1"] and [r[2] for r in body["values"]] == ["1", ""]
    moved = json.loads(capsys.readouterr().out)
    assert [(m["id"], m["from"], m["to"]) for m in moved] == [("P-2", 2, 1), ("P-1", 1, 2)]


def test_order_writes_over_a_blank_row_so_no_stale_row_stays_below(monkeypatch, capsys):
    stored = [[*row("P-1", "raw").values()], [""] * 13, [*row("P-2", "ready").values()]]
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"values": stored} if "get" in cmd else {}), "")

    monkeypatch.setattr(sheet.subprocess, "run", fake)
    assert sheet.main(["order", "SHEET"]) == 0
    (update,) = [c for c in calls if "update" in c]
    params, body = (
        json.loads(update[update.index("--params") + 1]),
        json.loads(update[update.index("--json") + 1]),
    )
    assert params["range"] == "Queue!A2:Z4"
    assert [r[0] for r in body["values"]] == ["P-2", "P-1", ""] and not any(body["values"][2])


def test_order_moves_a_column_people_added_with_its_row(monkeypatch, capsys):
    stored = [[*row("P-1", "raw").values(), "note one"], [*row("P-2", "ready").values()]]
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"values": stored} if "get" in cmd else {}), "")

    monkeypatch.setattr(sheet.subprocess, "run", fake)
    assert sheet.main(["order", "SHEET"]) == 0
    (update,) = [c for c in calls if "update" in c]
    body = json.loads(update[update.index("--json") + 1])
    assert [(r[0], r[13]) for r in body["values"]] == [("P-2", ""), ("P-1", "note one")]
    assert all(len(r) == 26 for r in body["values"])
