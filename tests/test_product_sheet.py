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
    stored = [[*row("P-1", "raw").values()], [""] * 14, [*row("P-2", "ready").values()]]
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
    stored = [[*row("P-1", "raw").values(), "a call", "note one"], [*row("P-2", "ready").values()]]
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"values": stored} if "get" in cmd else {}), "")

    monkeypatch.setattr(sheet.subprocess, "run", fake)
    assert sheet.main(["order", "SHEET"]) == 0
    (update,) = [c for c in calls if "update" in c]
    body = json.loads(update[update.index("--json") + 1])
    assert [(r[0], r[13], r[14]) for r in body["values"]] == [("P-2", "", ""), ("P-1", "a call", "note one")]
    assert all(len(r) == 26 for r in body["values"])


def test_the_doc_renders_every_placeholder():
    text = sheet.render("Acme", "https://sheet.example", "https://folder.example")
    assert "{{" not in text and "Acme product backlog" in text
    assert "https://sheet.example" in text and "https://folder.example" in text


def test_the_columns_table_is_one_header_row_of_four_cells_and_documents_exactly_the_sheets_columns_and_statuses():
    table = sheet.columns()
    assert table[0] == ["tab", "column", "values", "meaning"]
    assert all(len(r) == 4 and all(r[:2]) for r in table)
    assert [r[0] for r in table].count("tab") == 1
    for tab, cols in (("Queue", sheet.QUEUE), ("Blocked", sheet.BLOCKED)):
        assert [r[1] for r in table if r[0] == tab and not r[1].startswith("status =")] == cols
    statuses = [r[1].removeprefix("status = ") for r in table if r[1].startswith("status =")]
    assert sorted(statuses) == sorted(sheet.STAGES) and len(statuses) == len(sheet.STAGES)
    assert sorted(next(r for r in table if r[1] == "status")[2].split(", ")) == sorted(sheet.STAGES)


class Drive:
    """A fake `gws` and `gh`: records each call and answers from `answers`,
    keyed by the call's verbs."""

    def __init__(self, **answers):
        self.calls, self.uploads, self.answers = [], [], answers

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "--upload" in cmd:
            self.uploads.append((Path(kw["cwd"]) / cmd[cmd.index("--upload") + 1]).read_text())
        verbs = "_".join(a for a in cmd[1:] if not a.startswith("-") and a.isalpha() or a in ("batchUpdate",))
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.answers.get("gh", [])), "")
        if verbs == "drive_files_get":
            params = json.loads(cmd[cmd.index("--params") + 1])
            out = {"id": "ROOT"} if params["fileId"] == "root" else self.answers["meta"]
        else:
            out = self.answers.get(verbs, {"id": verbs.upper()})
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    def made(self, *verbs):
        return [
            (json.loads(c[c.index("--params") + 1]), json.loads(c[c.index("--json") + 1]) if "--json" in c else None, c)
            for c in self.calls if c[0] == "gws" and [a for a in c[1:1 + len(verbs)]] == list(verbs)
        ]  # fmt: skip


def run(monkeypatch, drive, argv):
    monkeypatch.setattr(sheet.subprocess, "run", drive)
    assert sheet.main(argv) == 0


def test_new_makes_a_folder_copies_the_template_into_it_writes_the_doc_there_and_shares_the_folder(
    monkeypatch, capsys
):
    drive = Drive(
        meta={"name": "Acme product backlog", "parents": ["FOLDER"]},
        drive_files_create={"id": "FOLDER"}, drive_files_copy={"id": "SHEET"},
        drive_files_list={"files": []},
        sheets_spreadsheets_get={"sheets": [{"properties": {"title": "Guide", "sheetId": 7}},
                                            {"properties": {"title": "Goal", "sheetId": 8}}]},
        sheets_spreadsheets_values_get={"values": [["field"], ["Product goal"], ["WIP limit"]]},
    )  # fmt: skip
    run(monkeypatch, drive, ["new", "Acme", "--share", "tom@example.com"])
    folder, doc = [b for _, b, _ in drive.made("drive", "files", "create")]
    assert folder == {"name": "Acme product", "mimeType": "application/vnd.google-apps.folder"}
    assert doc["parents"] == ["FOLDER"] and doc["mimeType"] == "application/vnd.google-apps.document"
    ((_, copy, _),) = drive.made("drive", "files", "copy")
    assert copy == {"name": "Acme product backlog", "parents": ["FOLDER"]}
    ((share, who, _),) = drive.made("drive", "permissions", "create")
    assert share["fileId"] == "FOLDER" and who["emailAddress"] == "tom@example.com"
    ((_, tabs, _),) = drive.made("sheets", "spreadsheets", "batchUpdate")
    assert [next(iter(r)) for r in tabs["requests"]] == ["addSheet", "deleteSheet"]
    assert tabs["requests"][1]["deleteSheet"]["sheetId"] == 7
    written = {
        p["range"]: b["values"] for p, b, _ in drive.made("sheets", "spreadsheets", "values", "update")
    }
    assert written["Columns!A1"] == sheet.columns()
    # The fake gives every create the id FOLDER, the Doc's too; a Goal tab without the row gets it appended.
    assert written["Goal!A4:B4"] == [["Companion Doc", "https://docs.google.com/document/d/FOLDER"]]
    out = json.loads(capsys.readouterr().out)
    assert out["sheet"].endswith("/SHEET") and out["folder"].endswith("/FOLDER") and out["id"] == "SHEET"


def test_doc_replaces_the_doc_in_place_rewrites_columns_and_the_goal_link_and_adds_no_tab(
    monkeypatch, capsys
):
    drive = Drive(
        meta={"name": "Acme product backlog", "parents": ["FOLDER"]},
        drive_files_list={"files": [{"id": "DOC"}]},
        sheets_spreadsheets_get={"sheets": [{"properties": {"title": "Columns", "sheetId": 1}}]},
        sheets_spreadsheets_values_get={"values": [["field"], ["Companion Doc", "old"], ["WIP limit"]]},
    )  # fmt: skip
    run(monkeypatch, drive, ["doc", "SHEET"])
    assert not drive.made("drive", "files", "create") and not drive.made(
        "sheets", "spreadsheets", "batchUpdate"
    )
    media = [c for p, _, c in drive.made("drive", "files", "update") if p["fileId"] == "DOC"]
    # gws uploads only a file under its working directory, so the path is relative.
    assert len(media) == 1 and media[0][media[0].index("--upload") + 1] == "doc.md"
    assert drive.uploads == [
        sheet.render(
            "Acme",
            "https://docs.google.com/spreadsheets/d/SHEET",
            "https://drive.google.com/drive/folders/FOLDER",
        )
    ]
    written = {
        p["range"]: b["values"] for p, b, _ in drive.made("sheets", "spreadsheets", "values", "update")
    }
    assert written["Goal!A2:B2"] == [["Companion Doc", "https://docs.google.com/document/d/DOC"]]
    assert json.loads(capsys.readouterr().out)["doc"].endswith("/DOC")


def test_doc_on_a_sheet_loose_in_my_drive_makes_its_folder_and_moves_it_in(monkeypatch, capsys):
    drive = Drive(
        meta={"name": "Acme product", "parents": ["ROOT"]},
        drive_files_create={"id": "NEW"}, drive_files_list={"files": []},
        sheets_spreadsheets_get={"sheets": [{"properties": {"title": "Guide", "sheetId": 3}}]},
    )  # fmt: skip
    run(monkeypatch, drive, ["doc", "SHEET", "--share", "tom@example.com"])
    folder, doc = [b for _, b, _ in drive.made("drive", "files", "create")]
    assert folder["name"] == "Acme product" and doc["parents"] == ["NEW"]
    assert any(
        p.get("addParents") == "NEW" and p.get("removeParents") == "ROOT"
        for p, _, _ in drive.made("drive", "files", "update")
    )
    ((share, _, _),) = drive.made("drive", "permissions", "create")
    assert share["fileId"] == "NEW"


def test_seed_appends_unlinked_open_issues_as_raw_rows_numbered_after_the_highest_id(monkeypatch, capsys):
    url = "https://github.com/acme/app/issues/{}".format
    stored = [["P-001", "bet", "", "a", "", "", "", "", "raw", "", url(1)], ["P-007", "bet", "", "b", "", "", "", "", "raw", "", ""],
              ["P-003", "bet", "", "c", "", "", "", "", "raw", "", f"see {url(2)}, {url(5)}"]]  # fmt: skip
    issues = [{"number": n, "title": f"issue {n}", "url": url(n), "labels": labels}
              for n, labels in [(9, []), (1, []), (2, []), (4, [{"name": "Bug"}]), (12, [{"name": "bugfix"}])]]  # fmt: skip
    drive = Drive(gh=issues, sheets_spreadsheets_values_get={"values": stored})
    run(monkeypatch, drive, ["seed", "SHEET", "acme/app"])
    ((params, body, _),) = drive.made("sheets", "spreadsheets", "values", "append")
    assert params["insertDataOption"] == "INSERT_ROWS"
    got = [dict(zip(sheet.QUEUE, r)) for r in body["values"]]
    assert [(r["id"], r["lane"], r["issue"], r["status"], r["source"]) for r in got] == [
        ("P-008", "mandatory", url(4), "raw", "GitHub"), ("P-009", "bet", url(9), "raw", "GitHub"),
        ("P-010", "bet", url(12), "raw", "GitHub"),
    ]  # fmt: skip
    assert all(len(r) == len(sheet.QUEUE) and not r[2] for r in body["values"])
    gh = next(c for c in drive.calls if c[0] == "gh")
    assert (
        gh[:3] == ["gh", "issue", "list"] and "--state" in gh
    )  # `gh issue list` returns issues only, never pull requests


def test_seed_with_nothing_new_appends_nothing(monkeypatch, capsys):
    drive = Drive(gh=[], sheets_spreadsheets_values_get={"values": []})
    run(monkeypatch, drive, ["seed", "SHEET", "acme/app"])
    assert not drive.made("sheets", "spreadsheets", "values", "append")
    assert json.loads(capsys.readouterr().out) == []


# -- lookup: the questions asked of Valor about the backlog (#valor-product and its call, 2026-11-13) --


def q(id, item, **kw):
    return [kw.get(c, {"id": id, "item": item}.get(c, "")) for c in sheet.QUEUE]


QUEUE = [
    q("P-004", "Overloaded turns are never retried", lane="mandatory", value="high", appetite="small",
      due="2026-11-20", status="ready", **{"blocked by": "human: Tom"}),
    q("P-005", "Entity memory: per-person records with live and retired facts", value="medium", appetite="large",
      status="shaped"),
    q("P-006", "Rerank injected memories for diversity", status="raw"),
    q("P-022", "Auto-responder marks customer email handled", lane="mandatory", status="raw"),
]  # fmt: skip
PITCHES = [
    ["P-004", "Overloaded turns", "Valor finishes a turn the API refused", "A refused turn is dropped",
     "observed: 3 dropped turns in the log, 2026-11-02", "Retry with backoff", "", "No new queue",
     "A refused turn is retried", "", "", "2026-11-10: passed"],
    ["P-022", "Auto-responder", "Staff answer every customer", ("A customer email drops off the staff queue and "
                                                      "nobody ever replies"), "reported: a customer wrote twice, 2026-11-01", "Keep it on the queue"],
]  # fmt: skip
BLOCKED = [
    ["P-004", "Tom to pick the backoff ceiling", "human", "Tom", "2026-11-10", "2026-11-13", "", "open"],
    ["P-005", "the memory store", "eng", "#123", "2026-11-01", "2026-11-04", "", "open"],
]  # fmt: skip


def lookup(monkeypatch, capsys, query):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        got = {"valueRanges": [{"values": QUEUE}, {"values": PITCHES}, {"values": BLOCKED}]}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(got), "")

    monkeypatch.setattr(sheet.subprocess, "run", fake)
    assert sheet.main(["lookup", "SHEET", query]) == 0
    return json.loads(capsys.readouterr().out)["matches"], calls


def test_an_id_in_any_spelling_finds_its_row_in_one_read(monkeypatch, capsys):
    for query in ("P-004", "p-4", "P4"):
        (found,), calls = lookup(monkeypatch, capsys, query)
        assert found["id"] == "P-004" and "score" not in found
    (cmd,) = calls  # one batched read of the three tabs, nothing else
    assert cmd[1:5] == ["sheets", "spreadsheets", "values", "batchGet"]
    assert json.loads(cmd[cmd.index("--params") + 1])["ranges"] == [
        "Queue!A2:Z",
        "Pitches!A2:Z",
        "Blocked!A2:Z",
    ]


def test_an_item_comes_with_its_values_its_pitch_and_its_blockers_and_no_other_items(monkeypatch, capsys):
    (found,), _ = lookup(monkeypatch, capsys, "P-004")
    assert {k: found[k] for k in ("lane", "value", "appetite", "due", "status", "blocked by")} == {
        "lane": "mandatory", "value": "high", "appetite": "small", "due": "2026-11-20", "status": "ready",
        "blocked by": "human: Tom"}  # fmt: skip
    assert found["pitch"]["evidence"] == "observed: 3 dropped turns in the log, 2026-11-02"
    assert found["pitch"]["readiness"] == "2026-11-10: passed"
    assert [b["blocker"] for b in found["blocked"]] == ["Tom to pick the backoff ceiling"]
    (bare,), _ = lookup(monkeypatch, capsys, "P-006")
    assert bare["pitch"] is None and bare["blocked"] == []


def test_a_loose_description_finds_the_item_through_its_pitch(monkeypatch, capsys):
    """Tom, 2026-11-13: people never know an item's id or title."""
    found, _ = lookup(monkeypatch, capsys, "the bug where an email never got a reply")
    assert found[0]["id"] == "P-022" and found[0]["pitch"]["evidence"].startswith("reported:")


def test_a_description_several_items_fit_returns_them_best_first(monkeypatch, capsys):
    found, _ = lookup(monkeypatch, capsys, "the memory thing with retired facts")
    assert [(f["id"], f["score"]) for f in found] == [("P-005", 6), ("P-006", 2)]
    assert found[0]["blocked"][0]["owner"] == "#123"
    found, _ = lookup(monkeypatch, capsys, "the memories one")
    assert [f["id"] for f in found] == ["P-005", "P-006"]  # a tie keeps the Sheet's order, the ranking


def test_the_values_asked_about_and_stray_letters_say_nothing_about_which_item(monkeypatch, capsys):
    """From the live check (2026-11-13): "what's" left an "s" that matched "Valor's"."""
    found, _ = lookup(monkeypatch, capsys, "what's the appetite and due date on the memory item?")
    assert [f["id"] for f in found] == ["P-005", "P-006"]
    assert lookup(monkeypatch, capsys, "what's the value, size and status?")[0] == []


def test_a_description_nothing_fits_finds_nothing(monkeypatch, capsys):
    assert lookup(monkeypatch, capsys, "the restart that happened")[0] == []
    assert lookup(monkeypatch, capsys, "P-099")[0] == []
