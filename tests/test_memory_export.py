"""The old memory's export dates each record (scripts/cutover_export_memory.py,
docs/plans/memory-measure.md): from a Telegram message, an extraction's
message, a session, a document's file date, a date in its metadata, and
else an upper bound, the record that superseded it or its first outcome."""

import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.spend(usd=0)

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def export():
    spec = importlib.util.spec_from_file_location("export", ROOT / "scripts/cutover_export_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rec(i, source="agent", agent="", content="", **more):
    return {"memory_id": i, "source": source, "agent_id": agent, "content": content, "metadata": {}, **more}


def test_each_record_takes_the_first_date_its_dump_holds(export, monkeypatch):
    june = 1780000000.0
    t = {
        "message": {("-100123", "4"): june + 1},
        "text": {"ship it on friday": june + 2},
        "text_chat": {"ship it on friday": "-100123"},
        "session": {"tg_x_-100999_8": june + 3, "0123abcd-0000-4000-8000-000000000000": june + 4},
        "doc": {"/vault/plan.md": june + 5},
        "chats": {"x": {"-100123"}},
    }
    recs = [
        rec("said", "human", "Tom", "ship  it on friday"),
        rec("batch", agent="extraction-tg_x_-100123_4_cont1"),
        rec("session", agent="extraction-tg_x_-100999_8"),
        rec("ms", agent="extraction-0_1780000006000"),
        rec("uuid", agent="extraction-0123abcd-0000-4000-8000-000000000000"),
        rec("doc", "knowledge", metadata={"file_path": "/vault/plan.md"}),
        rec("meta", reference='{"url": "https://x.test/2026-06-09/notes"}'),
        rec("bounded", superseded_by="said"),
        rec("outcome", metadata={"outcome_history": [{"ts": june + 9}, {"ts": june + 8}]}),
        rec("none", "human", "Tom", "never said in any chat"),
        rec("asked", "human", "Tom", "a message only the chat holds", project_key="x"),
    ]
    monkeypatch.setattr(
        export,
        "read",
        lambda chat, *a: (
            [{"id": 9, "sent_at": "2026-06-10T00:00:00+00:00", "text": "a message only the  chat holds"}]
            if "--search" in a
            else []
        ),
    )
    export.history(recs, t)
    export.date_all(recs, t)
    got = {r["memory_id"]: (r.get("occurred_at", "")[:19], r.get("dated_by")) for r in recs}
    assert got == {
        "said": ("2026-05-28T20:26:42", "telegram message"),
        "batch": ("2026-05-28T20:26:41", "telegram message"),
        "session": ("2026-05-28T20:26:43", "session start"),
        "ms": ("2026-05-28T20:26:46", "session id"),
        "uuid": ("2026-05-28T20:26:44", "session start"),
        "doc": ("2026-05-28T20:26:45", "document file date"),
        "meta": ("2026-06-09T00:00:00", "date in metadata"),
        "bounded": ("2026-05-28T20:26:42", "superseding record (no later than)"),
        "outcome": ("2026-05-28T20:26:48", "first outcome (no later than)"),
        "none": ("", None),
        "asked": ("2026-06-10T00:00:00", "telegram message"),
    }
    assert recs[0]["chat_id"] == recs[1]["chat_id"] == recs[10]["chat_id"] == "-100123"
