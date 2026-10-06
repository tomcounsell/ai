"""Transcript copies (`core/transcripts.py`): each turn's session file and
its subagents' files, stored as base64 of the raw bytes with a digest,
deltas against the task's last copy, read without following a link.

Live spend: none.
"""

import asyncio
import base64
import hashlib
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

from core import db, ledger, runs, tasks, transcripts
from core.gateway import Gateway
from harnesses import claude_code
from tests.ports import listen

pytestmark = [pytest.mark.spend(usd=0)]

SECRET = b"scratch-secret-" + uuid.uuid4().hex.encode()


def run(coro):
    return asyncio.run(coro)


class Layout:
    """`<anchor>/claude/projects/<dir>/<sid>.jsonl`, as a turn's config
    directory holds it."""

    def __init__(self, tmp_path: Path, sid: str | None = None):
        self.anchor = tmp_path / "state"
        self.sid = sid or str(uuid.uuid4())
        self.dir = self.anchor / "claude" / "projects" / "-private-tmp-some-workspace"
        self.dir.mkdir(parents=True)
        self.t = transcripts.Transcript(str(self.anchor), "claude", self.sid)

    @property
    def session(self) -> Path:
        return self.dir / f"{self.sid}.jsonl"

    def agent(self, name: str) -> Path:
        path = self.dir / self.sid / "subagents" / f"agent-{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def session_name(self) -> str:
        return f"{self.sid}.jsonl"


async def new_task(dsn) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="x"))


async def copy(dsn, task, t, **kw) -> tuple[str, dict]:
    turn_id = ledger.new_id()
    async with await db.connect(dsn) as conn:
        out = await transcripts.copy(conn, task, turn_id, t, **kw)
        await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", **out})
    return turn_id, out


async def joined(dsn, turn_id, name) -> bytes:
    async with await db.connect(dsn) as conn:
        return await transcripts.joined(conn, turn_id, name)


async def stored(dsn, turn_id) -> list[dict]:
    async with await db.connect(dsn) as conn:
        rows = await (
            await conn.execute(
                "SELECT body FROM documents WHERE kind = 'transcript' AND body->>'turn_id' = %s", (turn_id,)
            )
        ).fetchall()
    return [r[0] for r in rows]


def by_name(out: dict) -> dict[str, dict]:
    return {f["name"]: f for f in out["transcript"]["files"]}


def no_secret(docs: list[dict], out: dict) -> bool:
    return not any(SECRET in base64.b64decode(d["base64"]) for d in docs) and SECRET.decode() not in str(out)


# -- what is stored --------------------------------------------------------------------------


def test_the_session_and_its_subagents_are_stored_with_their_digests(dsn, tmp_path):
    lay = Layout(tmp_path)
    files = {lay.session: b'{"a":1}\n', lay.agent("one"): b'{"b":2}\n', lay.agent("two"): b'{"c":3}\n'}
    for path, data in files.items():
        path.write_bytes(data)
    (lay.dir / lay.sid / "subagents" / "notes.txt").write_text("not a transcript")
    task = run(new_task(dsn))
    turn_id, out = run(copy(dsn, task, lay.t))
    got = by_name(out)
    names = {lay.session_name, f"{lay.sid}/subagents/agent-one.jsonl", f"{lay.sid}/subagents/agent-two.jsonl"}
    assert set(got) == names and out["transcript"]["skipped"] == []
    assert len(run(stored(dsn, turn_id))) == 3
    for path, data in files.items():
        name = str(path.relative_to(lay.dir))
        assert run(joined(dsn, turn_id, name)) == data
        assert got[name]["sha256"] == hashlib.sha256(data).hexdigest() and got[name]["bytes"] == len(data)
        assert got[name]["offset"] == 0 and got[name]["prefix_changed"] is False


def test_a_resumed_turn_stores_only_what_was_appended(dsn, tmp_path):
    lay = Layout(tmp_path)
    first, more = b'{"turn":1}\n', b'{"turn":2}\n'
    lay.session.write_bytes(first)
    task = run(new_task(dsn))
    _t1, _ = run(copy(dsn, task, lay.t))
    lay.session.write_bytes(first + more)
    t2, out = run(copy(dsn, task, lay.t))
    rec = by_name(out)[lay.session_name]
    assert rec["offset"] == len(first) and rec["prefix_changed"] is False
    assert run(joined(dsn, t2, lay.session_name)) == more
    assert hashlib.sha256(first + run(joined(dsn, t2, lay.session_name))).hexdigest() == rec["sha256"]


def test_an_edited_earlier_line_is_stored_whole_and_marked(dsn, tmp_path):
    lay = Layout(tmp_path)
    lay.session.write_bytes(b'{"turn":1}\n')
    task = run(new_task(dsn))
    run(copy(dsn, task, lay.t))
    lay.session.write_bytes(b'{"turn":X}\n{"turn":2}\n')
    t2, out = run(copy(dsn, task, lay.t))
    rec = by_name(out)[lay.session_name]
    assert rec["offset"] == 0 and rec["prefix_changed"] is True
    assert run(joined(dsn, t2, lay.session_name)) == b'{"turn":X}\n{"turn":2}\n'


def test_a_nul_byte_and_a_split_multibyte_character_round_trip(dsn, tmp_path):
    lay = Layout(tmp_path)
    first = b"nul\x00here " + "é".encode()[:1]  # the delta starts mid-character
    rest = "é".encode()[1:] + b" and \xff\xfe raw\n"
    lay.session.write_bytes(first)
    task = run(new_task(dsn))
    t1, _ = run(copy(dsn, task, lay.t))
    lay.session.write_bytes(first + rest)
    t2, out = run(copy(dsn, task, lay.t))
    assert run(joined(dsn, t1, lay.session_name)) == first
    assert run(joined(dsn, t2, lay.session_name)) == rest
    assert by_name(out)[lay.session_name]["sha256"] == hashlib.sha256(first + rest).hexdigest()


def test_a_file_larger_than_a_chunk_is_split_and_joins_back(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "CHUNK", 5)
    lay = Layout(tmp_path)
    data = bytes(range(23))
    lay.session.write_bytes(data)
    task = run(new_task(dsn))
    turn_id, out = run(copy(dsn, task, lay.t))
    assert by_name(out)[lay.session_name]["documents"] == 5
    docs = sorted(run(stored(dsn, turn_id)), key=lambda d: d["chunk"])
    assert [d["offset"] for d in docs] == [0, 5, 10, 15, 20]
    assert run(joined(dsn, turn_id, lay.session_name)) == data


def test_a_failing_insert_stores_nothing_and_still_ends_the_turn(dsn, tmp_path):
    lay = Layout(tmp_path)
    task = run(new_task(dsn))

    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    fds = []

    def read(t):  # a name Postgres text cannot hold, after one that it can
        fds.extend([os.open(one, os.O_RDONLY), os.open(two, os.O_RDONLY)])
        return [("a.jsonl", fds[0]), ("b\x00.jsonl", fds[1])], []

    turn_id, out = run(copy(dsn, task, lay.t, read=read))
    assert "no_transcript" in out and "transcript" not in out
    assert run(stored(dsn, turn_id)) == []
    for fd in fds:  # the copy closed every descriptor it was handed
        with pytest.raises(OSError):
            os.fstat(fd)


def open_fds() -> set[int]:
    return {int(n) for n in os.listdir("/dev/fd")}


def test_a_copy_cancelled_while_its_thread_reads_leaves_no_descriptor_open(dsn, tmp_path, monkeypatch):
    lay = Layout(tmp_path)
    lay.session.write_bytes(b'{"a":1}\n')
    lay.agent("one").write_bytes(b'{"b":2}\n')
    task = run(new_task(dsn))
    reading, cancelled, closed = threading.Event(), threading.Event(), threading.Event()
    real_close = transcripts._close

    def close(files):
        real_close(files)
        if files:
            closed.set()

    def read(t):  # opens the files, then is still reading when the copy is cancelled
        files = transcripts.collect(t)
        reading.set()
        cancelled.wait(10)
        return files

    monkeypatch.setattr(transcripts, "_close", close)

    async def go():
        async with await db.connect(dsn) as conn:
            before = open_fds()
            copying = asyncio.create_task(transcripts.copy(conn, task, ledger.new_id(), lay.t, read=read))
            await asyncio.to_thread(reading.wait, 10)
            copying.cancel()
            with pytest.raises(asyncio.CancelledError):
                await copying
            cancelled.set()
            await asyncio.to_thread(closed.wait, 10)
            return before, open_fds()

    before, after = run(go())
    assert reading.is_set() and closed.is_set() and after <= before


def test_a_sparse_file_is_skipped_without_reading_its_holes(dsn, tmp_path):
    lay = Layout(tmp_path)
    lay.session.write_bytes(b"ok\n")
    with lay.agent("holes").open("wb") as f:
        f.truncate(1 << 50)  # a petabyte of apparent size, no disk used
    task = run(new_task(dsn))
    turn_id, out = run(asyncio.wait_for(copy(dsn, task, lay.t), 10))
    name = f"{lay.sid}/subagents/agent-holes.jsonl"
    assert out["transcript"]["skipped"] == [
        {"name": name, "why": f"agent-holes.jsonl is sparse ({1 << 50} bytes claimed, 0 on disk)"}
    ]
    assert set(by_name(out)) == {lay.session_name}
    assert len(run(stored(dsn, turn_id))) == 1


def test_every_read_digest_and_encoding_runs_off_the_event_loop(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "CHUNK", 4)
    lay = Layout(tmp_path)
    lay.session.write_bytes(b"0123456789")
    task = run(new_task(dsn))
    run(copy(dsn, task, lay.t))
    lay.session.write_bytes(b"0123456789abcdef")
    threads = []
    real = os.pread

    def pread(fd, n, offset):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(fd, n, offset)

    real_b64, real_dumps = transcripts.base64.b64encode, transcripts.json.dumps
    encoded = []

    def b64encode(data):
        encoded.append(threading.current_thread() is threading.main_thread())
        return real_b64(data)

    def dumps(obj, **kw):
        encoded.append(threading.current_thread() is threading.main_thread())
        return real_dumps(obj, **kw)

    monkeypatch.setattr(transcripts.os, "pread", pread)
    monkeypatch.setattr(transcripts.base64, "b64encode", b64encode)
    monkeypatch.setattr(transcripts.json, "dumps", dumps)
    turn_id, out = run(copy(dsn, task, lay.t))
    assert threads and not any(threads)
    assert len(encoded) >= 4 and not any(encoded)
    rec = by_name(out)[lay.session_name]
    assert rec["offset"] == 10 and rec["documents"] == 2
    assert run(joined(dsn, turn_id, lay.session_name)) == b"abcdef"


def test_no_session_file_ends_the_turn_with_no_transcript(dsn, tmp_path):
    lay = Layout(tmp_path)
    task = run(new_task(dsn))
    _turn, out = run(copy(dsn, task, lay.t))
    assert "no project directory holds" in out["no_transcript"]


# -- what a turn cannot make the kernel read -----------------------------------------------


def test_a_hard_link_to_a_secret_is_skipped(dsn, tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(SECRET)
    lay = Layout(tmp_path)
    os.link(secret, lay.session)
    os.link(secret, lay.agent("x"))
    task = run(new_task(dsn))
    turn_id, out = run(copy(dsn, task, lay.t))
    skipped = {s["name"]: s["why"] for s in out["transcript"]["skipped"]}
    assert out["transcript"]["files"] == []
    assert skipped[lay.session_name].endswith("has 3 links")
    assert skipped[f"{lay.sid}/subagents/agent-x.jsonl"].endswith("has 3 links")
    assert no_secret(run(stored(dsn, turn_id)), out)


def test_a_hard_link_with_two_links_says_so(dsn, tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(SECRET)
    lay = Layout(tmp_path)
    os.link(secret, lay.session)
    task = run(new_task(dsn))
    turn_id, out = run(copy(dsn, task, lay.t))
    assert out["transcript"]["skipped"] == [
        {"name": lay.session_name, "why": f"{lay.session_name} has 2 links"}
    ]
    assert no_secret(run(stored(dsn, turn_id)), out)


@pytest.mark.parametrize("where", ["session", "project_dir", "root"])
def test_a_symlink_stores_nothing_with_a_reason(dsn, tmp_path, where):
    secret_dir = tmp_path / "elsewhere"
    lay = Layout(tmp_path)
    if where == "session":
        (tmp_path / "secret").write_bytes(SECRET)
        lay.session.symlink_to(tmp_path / "secret")
    elif where == "project_dir":
        target = secret_dir / "proj"
        target.mkdir(parents=True)
        (target / lay.session_name).write_bytes(SECRET)
        lay.dir.rmdir()
        lay.dir.symlink_to(target)
    else:
        target = secret_dir / "claude" / "projects" / "d"
        target.mkdir(parents=True)
        (target / lay.session_name).write_bytes(SECRET)
        claude = lay.anchor / "claude"
        lay.dir.rmdir()
        (claude / "projects").rmdir()
        claude.rmdir()
        claude.symlink_to(secret_dir / "claude")
    task = run(new_task(dsn))
    turn_id, out = run(copy(dsn, task, lay.t))
    if where == "session":
        assert out["transcript"]["skipped"] == [
            {"name": lay.session_name, "why": f"{lay.session_name} is a link, not a plain file"}
        ]
    elif where == "project_dir":
        assert (
            "no project directory holds" in out["no_transcript"]
            and "is not a plain directory" in out["no_transcript"]
        )
    else:
        assert "no projects directory: claude is not a plain directory" in out["no_transcript"]
    assert no_secret(run(stored(dsn, turn_id)), out)


def test_a_fifo_or_a_directory_named_like_a_subagent_file_is_skipped_without_blocking(dsn, tmp_path):
    lay = Layout(tmp_path)
    lay.session.write_bytes(b"ok\n")
    os.mkfifo(lay.agent("fifo"))
    lay.agent("dir").mkdir()
    lay.agent("real").write_bytes(b"real\n")
    task = run(new_task(dsn))
    _turn, out = run(asyncio.wait_for(copy(dsn, task, lay.t), 10))
    skipped = {s["name"]: s["why"] for s in out["transcript"]["skipped"]}
    assert skipped[f"{lay.sid}/subagents/agent-fifo.jsonl"].endswith("is not a regular file")
    assert f"{lay.sid}/subagents/agent-dir.jsonl" in skipped
    assert set(by_name(out)) == {lay.session_name, f"{lay.sid}/subagents/agent-real.jsonl"}


# -- the turn ------------------------------------------------------------------------------


def test_a_stopped_turn_s_transcript_is_copied(dsn, tmp_path):
    lay = Layout(tmp_path)
    ready = tmp_path / "ready"
    script = (
        f"import pathlib,time\npathlib.Path({str(lay.session)!r}).write_bytes(b'line\\n')\n"
        f"pathlib.Path({str(ready)!r}).touch()\ntime.sleep(60)\n"
    )

    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[sys.executable, "-c", script],
            env={},
            cwd=str(tmp_path),
            harness="sleeper",
            transcript=lay.t,
        )
        turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=dsn))
        while not ready.exists():
            await asyncio.sleep(0.05)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        ended = await turn
        await gateway.close()
        return ended

    ended = run(go())
    assert ended["outcome"] == "stopped"
    (rec,) = ended["transcript"]["files"]
    assert rec["name"] == lay.session_name and rec["sha256"] == hashlib.sha256(b"line\n").hexdigest()


def test_the_kernel_chooses_the_session_id_and_resumes_the_fold_s(tmp_path):
    harness = {"sandbox_profile": "/p.sb", "claude_config_dir": str(tmp_path / "state" / "claude")}
    try:
        new = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness=harness)(
            "http://127.0.0.1:9/t/x", "# B", "t1"
        )
    except Exception as exc:  # noqa: BLE001  sandbox-exec missing or not root-owned on this machine
        pytest.skip(str(exc))
    sid = new.argv[new.argv.index("--session-id") + 1]
    assert str(uuid.UUID(sid)) == sid and "--resume" not in new.argv
    assert new.transcript == transcripts.Transcript(str(tmp_path / "state"), "claude", sid)
    old = "00000000-0000-4000-8000-000000000001"
    resumed = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness=harness, resume=old)(
        "http://127.0.0.1:9/t/x", "# B", "t2"
    )
    assert resumed.argv[resumed.argv.index("--resume") + 1] == old and "--session-id" not in resumed.argv
    assert resumed.transcript.session_id == old
    bare = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness={"sandbox_profile": "/p.sb"})(
        "http://127.0.0.1:9/t/x", "# B", "t3"
    )
    assert bare.transcript is None
