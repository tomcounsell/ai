"""`core/signals.py` against a workspace a turn could have shaped: links,
hard links, FIFOs, and directories where files belong, all pointing at an
"outside" directory beside the workspace that holds a marker file and a
FIFO. Each collect runs in a thread joined with a 5 second limit, so a
read that would block on the FIFO fails the test instead of the suite.

No database, no model, no real secret: the outside files are `tmp_path`
files.

Live spend: none.
"""

import json
import os
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

from core import signals, workspace

pytestmark = pytest.mark.spend(usd=0)

MARKER = "outside-marker-not-a-signal"


def collect(ws: Path, turn_id: str = "turn-1") -> signals.Signals:
    got: dict = {}

    def go():
        try:
            got["value"] = signals.collect(ws, turn_id)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            got["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "collect blocked"
    if "error" in got:
        raise got["error"]
    return got["value"]


@pytest.fixture
def outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "marker.txt").write_text(MARKER)
    os.mkfifo(out / "fifo")
    return out


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / ".valor").mkdir(parents=True)
    return w


def listing(d: Path) -> dict[str, str]:
    return {
        str(p.relative_to(d)): (p.read_text() if p.is_file() and not p.is_symlink() else "")
        for p in sorted(d.rglob("*"))
        if not p.is_fifo()
    }


def leaked(found: signals.Signals) -> bool:
    return MARKER in json.dumps(asdict(found))


@pytest.mark.parametrize("target", ["marker.txt", "fifo"])
def test_a_linked_question_is_not_followed(ws, outside, target):
    before = listing(outside)
    (ws / ".valor" / "question.md").symlink_to(outside / target)
    found = collect(ws)
    assert found.question is None and not leaked(found)
    assert found.unreadable == ["question.md is a link, not a plain file"]
    assert (ws / ".valor" / "handled" / "turn-1" / "question.md").is_symlink()
    assert not (ws / ".valor" / "question.md").exists(follow_symlinks=False)
    assert listing(outside) == before


def test_a_hard_linked_done_is_refused(ws, outside):
    os.link(outside / "marker.txt", ws / ".valor" / "done.md")
    found = collect(ws)
    assert found.done is None and not leaked(found)
    assert found.unreadable == ["done.md has 2 links"]


def test_fifos_where_signals_belong_return_with_reasons(ws):
    os.mkfifo(ws / ".valor" / "question.md")
    os.mkfifo(ws / ".valor" / "plan.json")
    (ws / ".valor" / "effects").mkdir()
    os.mkfifo(ws / ".valor" / "effects" / "a.json")
    found = collect(ws)
    assert found.question is None and found.plan is None
    assert found.unreadable == ["question.md is not a regular file"]
    assert found.plan_error == "plan.json is unreadable: plan.json is not a regular file"
    assert found.effects == [{"file": "a.json", "error": "unreadable request: a.json is not a regular file"}]
    handled = ws / ".valor" / "handled" / "turn-1"
    assert (handled / "question.md").is_fifo() and (handled / "plan.json").is_fifo()
    assert (handled / "effects" / "a.json").is_fifo()


def test_a_linked_valor_is_not_read_and_nothing_is_made_there(ws, outside):
    (outside / "question.md").write_text(MARKER)
    (outside / "effects").mkdir()
    (outside / "effects" / "a.json").write_text(json.dumps({"action_type": MARKER, "target": "t"}))
    before = listing(outside)
    (ws / ".valor").rmdir()
    (ws / ".valor").symlink_to(outside)
    found = collect(ws)
    assert not leaked(found) and found.question is None and found.effects == []
    assert found.unreadable == [".valor is not a plain directory"]
    assert listing(outside) == before and not (outside / "handled").exists()


def test_a_linked_effects_directory_is_not_listed(ws, outside):
    (outside / "a.json").write_text(json.dumps({"action_type": MARKER, "target": "t"}))
    before = listing(outside)
    (ws / ".valor" / "effects").symlink_to(outside)
    found = collect(ws)
    assert found.effects == [] and not leaked(found)
    assert found.unreadable == ["effects is not a plain directory"]
    assert listing(outside) == before


@pytest.mark.parametrize("plant", ["handled", "turn", "occupied"])
def test_a_refused_move_reads_nothing_and_leaves_nothing_for_the_next_turn(ws, outside, plant):
    (ws / ".valor" / "question.md").write_text(MARKER)
    if plant == "handled":
        (ws / ".valor" / "handled").symlink_to(outside)
    elif plant == "turn":
        (ws / ".valor" / "handled").mkdir()
        (ws / ".valor" / "handled" / "turn-1").symlink_to(outside)
    else:
        (ws / ".valor" / "handled" / "turn-1" / "question.md" / "x").mkdir(parents=True)
    before = listing(outside)
    found = collect(ws)
    assert found.question is None and not leaked(found)
    assert len(found.unreadable) == 1 and found.unreadable[0].endswith("; removed unread")
    assert listing(outside) == before
    assert not (ws / ".valor" / "question.md").exists()
    if plant == "occupied":
        (ws / ".valor" / "handled" / "turn-1" / "question.md" / "x").rmdir()
    later = collect(ws, "turn-2")
    assert later.question is None


def test_a_linked_workspace_is_not_read(tmp_path, ws):
    (ws / ".valor" / "question.md").write_text(MARKER)
    link = tmp_path / "ws-link"
    link.symlink_to(ws)
    found = collect(link)
    assert found.question is None and not leaked(found) and len(found.unreadable) == 1
    assert (ws / ".valor" / "question.md").read_text() == MARKER


def test_directories_named_as_signals_are_refused_not_read(ws):
    (ws / ".valor" / "question.md").mkdir()
    (ws / ".valor" / "effects" / "b.json").mkdir(parents=True)
    found = collect(ws)
    assert found.question is None
    assert found.unreadable == ["question.md is not a regular file"]
    assert found.effects == [{"file": "b.json", "error": "unreadable request: b.json is not a regular file"}]


def test_no_valor_is_no_signals(tmp_path):
    (tmp_path / "ws").mkdir()
    assert collect(tmp_path / "ws") == signals.Signals()
    assert collect(tmp_path / "missing") == signals.Signals()


def test_plain_files_are_collected_and_moved(ws):
    v = ws / ".valor"
    (v / "question.md").write_text("Which one?\n")
    (v / "done.md").write_text("built\n")
    (v / "plan.json").write_text(json.dumps({"path": "docs/plans/x.md"}))
    (v / "effects").mkdir()
    (v / "effects" / "b.json").write_text(json.dumps({"action_type": "send", "target": "tom"}))
    (v / "effects" / "a.json").write_text("not json")
    found = collect(ws)
    assert found.question == "Which one?" and found.done == "built"
    assert found.plan == {"path": "docs/plans/x.md"} and found.unreadable == []
    assert [e["file"] for e in found.effects] == ["a.json", "b.json"]
    assert found.effects[1]["request"] == {"action_type": "send", "target": "tom", "payload": {}}
    assert "error" in found.effects[0]
    handled = v / "handled" / "turn-1"
    assert sorted(p.name for p in handled.iterdir()) == ["done.md", "effects", "plan.json", "question.md"]
    assert sorted(p.name for p in (handled / "effects").iterdir()) == ["a.json", "b.json"]
    assert collect(ws, "turn-2") == signals.Signals()


def test_a_sparse_file_is_refused_and_written_or_cloned_files_are_read(ws):
    v = ws / ".valor"
    with open(v / "question.md", "wb") as f:
        f.truncate(1 << 50)  # claims 1 PiB, uses no disk
    body = "x" * 3_000_000
    (v / "done.md").write_text(body)
    (v / "effects").mkdir()
    (v / "effects" / "a.json").write_text(
        json.dumps({"action_type": "send", "target": "tom", "payload": {"t": body}})
    )
    subprocess.run(["cp", "-c", str(v / "effects" / "a.json"), str(v / "effects" / "b.json")], check=True)
    found = collect(ws)
    assert found.question is None
    assert found.unreadable == [f"question.md is sparse ({1 << 50} bytes claimed, 0 on disk)"]
    assert found.done == body
    assert [e["request"]["payload"]["t"] == body for e in found.effects] == [True, True]


def test_a_file_grown_after_its_check_is_read_only_to_the_size_checked(ws, monkeypatch):
    (ws / ".valor" / "done.md").write_text("checked")
    real = workspace._open_checked

    def grow(dir_fd, relpath):
        got = real(dir_fd, relpath)
        with open(ws / ".valor" / "handled" / "turn-1" / relpath, "a") as f:
            f.write(" and grown after the check" * 1000)
        return got

    monkeypatch.setattr(workspace, "_open_checked", grow)
    assert collect(ws).done == "checked"


def test_an_entry_gone_after_its_move_is_recorded_unreadable(ws, monkeypatch):
    (ws / ".valor" / "question.md").write_text("q")
    (ws / ".valor" / "effects").mkdir()
    (ws / ".valor" / "effects" / "a.json").write_text("{}")
    real = workspace._file_away

    def then_gone(src, name, valor, turn_id, sub=()):
        dest, why = real(src, name, valor, turn_id, sub)
        if dest is not None:
            os.unlink(name, dir_fd=dest)
        return dest, why

    monkeypatch.setattr(workspace, "_file_away", then_gone)
    found = collect(ws)
    assert found.question is None
    assert found.unreadable == ["question.md was gone from handled/turn-1 when it was read"]
    assert found.effects == [
        {
            "file": "a.json",
            "error": "unreadable request: a.json was gone from handled/turn-1 when it was read",
        }
    ]


def test_an_entry_swapped_for_a_link_after_its_move_is_not_followed(ws, outside, monkeypatch):
    (ws / ".valor" / "question.md").write_text("q")
    real = workspace._file_away

    def then_swapped(src, name, valor, turn_id, sub=()):
        dest, why = real(src, name, valor, turn_id, sub)
        if dest is not None:
            os.unlink(name, dir_fd=dest)
            os.symlink(outside / "fifo", name, dir_fd=dest)
        return dest, why

    monkeypatch.setattr(workspace, "_file_away", then_swapped)
    found = collect(ws)
    assert found.question is None and not leaked(found)
    assert found.unreadable == ["question.md is a link, not a plain file"]


def test_text_that_is_not_utf8_is_read_with_replacement(ws):
    (ws / ".valor" / "question.md").write_bytes(b"caf\xe9 or tea?\n")
    (ws / ".valor" / "plan.json").write_bytes(b'{"path": "\xff"}')
    found = collect(ws)
    assert found.question == "caf� or tea?"
    assert found.plan is None and found.plan_error.startswith("plan.json is unreadable")
