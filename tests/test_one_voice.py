"""One voice (docs/plans/one-voice.md): only a chat session speaks as Valor.

The incident, 2026-10-31: the nightly Sentry routine's report reached Tom's
Slack DM as kernel text; his two questions in its thread were bound as
feedback, and the routine re-sent its report twice, shorter, unanswered.
Now a routine's result is a quiet report held in the record, and every
message of Tom's in his DM is his session's, with the post it is under.
"""

import asyncio
import hashlib
import pathlib
import uuid

import pytest

from core import broker, db, intake, ledger, machine, notices, record, router, routines, session, tasks
from core.bridge import DECLARED, declared_performers
from core.machine import State
from core.settings import settings
from tests import bridges
from tests.test_kernel import at, legacy_notice, tom_says

run = asyncio.run

REPORT = (
    'On "Review the unresolved issues of the `valor` Sentry project...": This is done; nothing needed '
    'merging.\n\nDone: filed tomcounsell/ai#3678, "[VALOR-JV] CancelledError".'
)


@pytest.fixture
def dm(tmp_path):
    """Tom's Slack DM as the operator chat, listed by the `valor` spec, as
    on the Mac that serves it."""
    tom = f"T0ABC/U{uuid.uuid4().hex[:8].upper()}"
    with (
        bridges.operator(tmp_path, operator_slack_ids=(tom,)),
        bridges.configure(operator_channel="slack", operator_chat=tom),
    ):
        (pathlib.Path(settings.projects_dir) / "valor.toml").write_text(
            bridges.VALOR_SPEC + f'chats = ["slack:{tom}"]\n'
        )
        yield tom


async def new_task(dsn, instruction="test", **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction=instruction, **kw))


async def routine_run(dsn, name="sentry", **kw) -> str:
    """A routine's run: a child of its objective, as `task_runner` starts it."""
    objective = await new_task(dsn, routine=name, instruction=f"routine {name}")
    return await new_task(
        dsn, parent_id=objective, routine=name, project={"name": "valor"}, max_effect_class="act", **kw
    )


async def end(dsn, task, text) -> None:
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "task.ended", {"by": "valor", "kind": "decided", "text": text})
        await notices.owe(conn, task)


async def operator(dsn) -> str | None:
    async with await db.connect(dsn) as conn:
        return await intake.chat_session(conn, settings.operator_channel, settings.operator_chat)


def test_the_incident_a_routine_report_is_quiet_and_toms_thread_questions_reach_his_session(dsn, dm):
    async def go():
        sentry = await routine_run(
            dsn, instruction="Review the unresolved issues of the `valor` Sentry project"
        )
        await end(dsn, sentry, "Done: filed tomcounsell/ai#3678.")
        hearer = await operator(dsn)
        heard = await bridges.rows(dsn, hearer)

        # Before the change the kernel posted the report itself (23:07); Tom asked twice in its thread.
        old = await routine_run(dsn, instruction="Review the unresolved issues of the `valor` Sentry project")
        await end(dsn, old, "Done: filed tomcounsell/ai#3678.")  # reported now: quiet, on the session
        root = at(1)
        entries = [{"channel": "slack", "chat_id": dm, "message_id": root, "thread_ts": None}]
        async with await db.connect(dsn) as conn:
            await legacy_notice(conn, old, "ended", "ended:legacy", REPORT, entries)
        first = await tom_says(dsn, dm, "Why did you send me this big mess? Your persona doesn't allow it",
                               at(2), reply_to=root, topic_id=root)  # fmt: skip
        second = await tom_says(dsn, dm, "Why are you telling me this? I didn't ask for this.",
                                at(3), reply_to=root, topic_id=root)  # fmt: skip
        async with await db.connect(dsn) as conn:
            b = await tasks.brief(conn, hearer)
            rows_ = await ledger.read(conn, hearer)
        return sentry, hearer, heard, old, first, second, b, rows_, await bridges.rows(dsn, old)

    sentry, hearer, heard, old, first, second, b, rows_, old_rows = run(go())
    # The run's result: a quiet report on the operator session, nothing for a bridge.
    (result,) = [p for p in bridges.typed(heard, "child.reported") if p["from"] == sentry]
    assert (result["kind"], result["quiet"]) == ("ended", True)
    assert not bridges.typed(heard, "notice.requested")
    assert [n["about_key"] for n in bridges.typed(old_rows, "notice.requested")] == ["ended:legacy"]
    assert not session.chat_waiting(heard)
    assert not router.has_step(heard, machine.fold(heard), b.chat)
    assert "A routine result, held in the record (task " + sentry in session.chat_prompt(heard)
    # Tom's two questions: his session's, each with the report quoted; the run is untouched.
    assert [(x["task_id"], x["as"]) for x in (first, second)] == [(hearer, "steer")] * 2
    steered = bridges.typed(rows_, "message.steered")
    assert [p["replying_to"]["task_id"] for p in steered] == [old, old]
    assert steered[0]["replying_to"]["text"].startswith('On "Review the unresolved issues')
    prompt = session.chat_prompt(rows_)
    assert "Why are you telling me this?" in prompt and "  > Done: filed tomcounsell/ai#3678" in prompt
    assert session.chat_waiting(rows_)
    assert machine.fold(old_rows).state is State.ENDED
    assert not bridges.typed(old_rows, "feedback.given") and not bridges.typed(old_rows, "message.steered")


def test_a_quiet_routine_report_opens_a_session_and_runs_no_turn(dsn, dm):
    async def go():
        assert await operator(dsn) is None
        run_ = await routine_run(dsn, instruction="nightly review")
        await end(dsn, run_, "Done: nothing new.")
        hearer = await operator(dsn)
        async with await db.connect(dsn) as conn:
            rows_ = await ledger.read(conn, hearer)
            b = await tasks.brief(conn, hearer)
        return hearer, rows_, b

    hearer, rows_, b = run(go())
    assert hearer is not None and b.chat["chat_id"] == settings.operator_chat
    f = machine.fold(rows_)
    assert not router.has_step(rows_, f, b.chat)
    # The report is quiet, so the scheduler's latest row (`serve._ready`) does not move for it.
    assert ledger.quiet(rows_[-1]) and rows_[-1]["type"] == "child.reported"


def test_a_later_session_finds_a_routine_result_in_the_record(dsn, dm):
    async def go():
        run_ = await routine_run(dsn, instruction="Review the unresolved issues of the valor Sentry project")
        await end(dsn, run_, "Done: filed tomcounsell/ai#3678.")
        first = await operator(dsn)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, first, reason="the session ended")
        later = (await tom_says(dsn, dm, "what did the sentry review find?", at(5)))["task_id"]
        found = await record.answer(dsn, later, "tasks", {"q": "sentry project"})
        return first, later, run_, found

    first, later, run_, found = run(go())
    assert later != first
    (hit,) = [t for t in found if t["task_id"] == run_]
    assert hit["delivered"] == "Done: filed tomcounsell/ai#3678."


def test_what_wakes_the_session_and_what_does_not(dsn, dm):
    """A routine's question wakes; so does every report of Tom's own work
    and every kernel fault. A routine whose toml `wakes` (invoices) wakes
    it with its result; any other routine's result is quiet."""
    assert routines.wakes("invoices") and not routines.wakes("sentry") and not routines.wakes("no-such")

    async def go():
        made = {}
        asking = await routine_run(dsn)
        foreground = await new_task(dsn, max_effect_class="act")
        invoices = await routine_run(dsn, name="invoices")
        quiet = await routine_run(dsn, name="verify")
        async with await db.connect(dsn) as conn:
            for name, reporter, kind in (
                ("routine question", asking, "question"),
                ("foreground ended", foreground, "ended"),
                ("merge report", foreground, "report"),
                ("credential", quiet, "credential"),
                ("rollout", intake.UPDATES, "rollout"),
                ("send in doubt", foreground, "send_in_doubt"),
                ("invoices result", invoices, "ended"),
                ("routine result", quiet, "ended"),
            ):
                await notices.request(conn, reporter, kind=kind, about_key=f"{name}:x", text=name)
            hearer = await operator(dsn)
            for p in bridges.typed(await ledger.read(conn, hearer), "child.reported"):
                made[p["text"]] = p
        return made

    made = run(go())
    assert {t for t, p in made.items() if p.get("quiet")} == {"routine result"}
    rendered = session.chat_prompt([{"id": 1, "type": "child.reported", "payload": made["rollout"]}])
    assert "The kernel reports (updates, rollout)" in rendered and "None" not in rendered


def test_a_report_is_made_once_and_never_again_after_a_legacy_notice(dsn, dm):
    """Cutover: a task whose notice the kernel sent itself before
    2026-11-01 reports nothing again; two kernels asking at once write one row."""

    async def go():
        legacy, fresh_ = await new_task(dsn), await new_task(dsn)
        entries = [{"channel": "slack", "chat_id": dm, "message_id": at(1), "thread_ts": None}]
        async with await db.connect(dsn) as conn:
            await legacy_notice(conn, legacy, "ended", "ended:1", "Stopped.", entries)
            again = await notices.request(conn, legacy, kind="ended", about_key="ended:1", text="Stopped.")
        await operator_ready(dsn)

        async def one():
            async with await db.connect(dsn) as conn:
                return await notices.request(conn, fresh_, kind="ended", about_key="ended:2", text="Stopped.")

        made = await asyncio.gather(*(one() for _ in range(4)))
        async with await db.connect(dsn) as conn:
            (n,) = await (
                await conn.execute(
                    "SELECT count(*) FROM events WHERE type = 'child.reported' AND payload->>'from' = ANY(%s)",
                    ([legacy, fresh_],),
                )
            ).fetchone()
        return again, made, n

    again, made, n = run(go())
    assert again is None and n == 1 and made.count("ended:2") == 1


async def operator_ready(dsn) -> None:
    async with await db.connect(dsn) as conn:
        await intake.operator_session(conn)


def test_a_session_sends_a_file_a_task_reporting_to_it_made_and_no_other(dsn, dm, tmp_path):
    """The invoice reminders: the run drafts them with their PDFs in its own
    workspace; the session it reports to sends them with those files. A
    file in a task that does not report to the session is refused."""

    def made(root):
        root.mkdir(parents=True)
        pdf = root / "invoice_1.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        return {"path": str(pdf), "sha256": hashlib.sha256(b"%PDF-1.4").hexdigest()}

    async def go():
        drafts = made(tmp_path / "run" / ".valor" / "invoices")
        stray = made(tmp_path / "stray")
        run_ = await routine_run(dsn, name="invoices", workspace=str(tmp_path / "run"))
        await new_task(dsn, workspace=str(tmp_path / "stray"))
        await end(dsn, run_, "Done: one reminder drafted.")
        hearer = await operator(dsn)
        email = DECLARED["email.send"]
        send = next(
            d for d in declared_performers(str(tmp_path / "session"), hearer) if d.action_type == "email.send"
        )
        async with await db.connect(dsn) as conn:
            ok = await send.refuse(
                conn, broker.Action("email.send", "a@b.test", {"to": ["a@b.test"], "files": [drafts]})
            )
            no = await send.refuse(
                conn, broker.Action("email.send", "a@b.test", {"to": ["a@b.test"], "files": [stray]})
            )
            rows_ = await ledger.read(conn, hearer)
        return email, ok, no, rows_

    email, ok, no, rows_ = run(go())
    assert email.owner == "email" and ok is None
    assert no == "files[0] is not a regular file in your workspace or a reporting task's"
    assert session.chat_waiting(rows_)
