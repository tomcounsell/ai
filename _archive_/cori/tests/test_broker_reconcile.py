"""The restart door: every intent a kill left open is closed by asking the
target, and a dangling read is re-run rather than called recovered without
its data. Plan 07 task 8; seams §3.10; spike 02.

The three-way branch is spike 02's `resolve_dangling`: the target held the
key, the target did not, or the target could not say. Only the third earns a
card, and the card is `kernel/__main__.py`'s.
"""

import uuid
from typing import ClassVar

import psycopg
import pytest

import broker
from broker import actions, ledger
from broker.credentials import Credential
from schemas.effect import Action, ConnectorRead
from schemas.ids import new_id
from schemas.space import EffectClass
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres

TARGET = "fake://target"


class FakeAction(Action):
    effect_class: ClassVar[EffectClass] = "propose"
    action_type: ClassVar[str] = "fake_effect"

    name: str

    def derive_key(self) -> str:
        return f"fake:{self.name}"

    def target(self) -> str:
        return TARGET


class FakeTarget:
    """A target whose answer is set per action name, so one pass can walk
    three dangling intents in three different states."""

    model = FakeAction

    def __init__(self):
        self.states: dict[str, str] = {}
        self.written: list[str] = []
        self.raises: set[str] = set()

    def check(self, action) -> None:
        return None

    async def run(self, conn, action, credential):
        if action.name in self.raises:
            raise RuntimeError(f"the re-run of {action.name} exploded")
        self.written.append(action.name)
        return {"name": action.name}

    async def query(self, conn, action, credential):
        return self.states[action.name]


@pytest.fixture
async def kernel_conn():
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        await conn.set_autocommit(True)
        yield conn


@pytest.fixture
def target(monkeypatch):
    fake = FakeTarget()
    monkeypatch.setitem(actions.MODULES, FakeAction.action_type, fake)
    monkeypatch.setattr(
        broker,
        "credential_for",
        lambda space_id, t: Credential(space=space_id, target=t, names=()),
    )
    return fake


def act(space_id: str, name: str) -> FakeAction:
    return FakeAction(
        space=space_id, objective_id=new_id(), brief_id=new_id(), name=name
    )


async def leave_dangling(conn, action: FakeAction, generation: int = 1) -> str:
    """An intent with no closing row: what a kill between the intent and the
    action leaves behind."""
    return await ledger.intent(
        conn,
        action_type=action.action_type,
        effect_class=action.effect_class,
        space=action.space,
        target=action.target(),
        idempotency_key=action.idempotency_key,
        payload=action.model_dump(mode="json"),
        objective_id=action.objective_id,
        brief_id=action.brief_id,
        generation=generation,
    )


async def rows_for(conn, effect_id: str) -> list[tuple]:
    return await (
        await conn.execute(
            "SELECT event, outcome_kind FROM effect_ledger WHERE effect_id = %s "
            "ORDER BY id",
            (effect_id,),
        )
    ).fetchall()


async def test_three_dangling_intents_close_by_what_the_target_says(
    kernel_conn, target
):
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    names = {"present": "recovered", "absent": "done", "differs": "unknown"}
    ids = {}
    for state in names:
        action = act(space_id, f"{state}-{uuid.uuid4().hex[:6]}")
        target.states[action.name] = state
        ids[action.name] = (state, await leave_dangling(kernel_conn, action))

    closed = await broker.reconcile_dangling(kernel_conn)
    by_id = {outcome.effect_id: outcome for outcome in closed}

    for name, (state, effect_id) in ids.items():
        assert by_id[effect_id].kind == names[state], name
        assert await rows_for(kernel_conn, effect_id) == [
            ("intent", None),
            ("reconciled", names[state]),
        ]
    # Only the `absent` one ran again: `present` already landed and `differs`
    # is the person's to read (blind-spot finding 15).
    assert [n for n in target.written if n in ids] == [
        n for n in ids if ids[n][0] == "absent"
    ]


async def test_an_unreachable_target_closes_unknown_with_its_answer(
    kernel_conn, target
):
    action = act(f"space-{uuid.uuid4().hex[:8]}", f"gone-{uuid.uuid4().hex[:6]}")
    target.states[action.name] = "unreachable"
    effect_id = await leave_dangling(kernel_conn, action)

    closed = await broker.reconcile_dangling(kernel_conn)
    outcome = next(o for o in closed if o.effect_id == effect_id)
    assert outcome.kind == "unknown"
    assert outcome.result == {"target_state": "unreachable"}
    assert await rows_for(kernel_conn, effect_id) == [
        ("intent", None),
        ("reconciled", "unknown"),
    ]
    assert action.name not in target.written


async def test_a_closed_effect_is_not_reconciled_twice(kernel_conn, target):
    action = act(f"space-{uuid.uuid4().hex[:8]}", f"done-{uuid.uuid4().hex[:6]}")
    target.states[action.name] = "absent"
    effect_id = await leave_dangling(kernel_conn, action)
    await broker.reconcile_dangling(kernel_conn)
    await broker.reconcile_dangling(kernel_conn)
    assert await rows_for(kernel_conn, effect_id) == [
        ("intent", None),
        ("reconciled", "done"),
    ]
    assert target.written.count(action.name) == 1


# ---------------------------------------------------------------------------
# A kernel read: null brief fields, re-run by construction


def read_payload(space_id: str, account: str, external_id: str) -> dict:
    return ConnectorRead(
        space=space_id,
        objective_id=None,
        brief_id=None,
        connector="gmail",
        account=account,
        query=f"id:{external_id}",
        idempotency_key=f"gmail:{account}:id:{external_id}:1758412800",
    ).model_dump(mode="json")


class StubGmail:
    """`gmail`'s two names, with `query` answering `absent` by rule."""

    model = ConnectorRead

    def __init__(self, *, raises=False):
        self.read: list[str] = []
        self.raises = raises

    def check(self, action) -> None:
        return None

    async def run(self, conn, action, credential):
        if self.raises:
            raise RuntimeError("gmail said no")
        self.read.append(action.query)
        return {"headers": {"from": "ana@psyoptimal.com"}, "body": "hello"}

    async def query(self, conn, action, credential):
        return "absent"


async def dangling_read(kernel_conn, space_id, account, external_id) -> str:
    payload = read_payload(space_id, account, external_id)
    return await ledger.intent(
        kernel_conn,
        action_type="connector_read",
        effect_class="read",
        space=space_id,
        target="mailto:" + account,
        idempotency_key=payload["idempotency_key"],
        payload=payload,
    )


async def test_a_dangling_read_re_runs_and_closes_with_its_data(
    kernel_conn, monkeypatch
):
    """A read leaves nothing on the target to find, so it is re-run rather
    than recovered: no read is ever called recovered without its data."""
    stub = StubGmail()
    monkeypatch.setitem(actions.MODULES, "connector_read", stub)
    monkeypatch.setattr(
        broker,
        "credential_for",
        lambda s, t: Credential(space=s, target=t, names=()),
    )
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    external_id = uuid.uuid4().hex[:16]
    effect_id = await dangling_read(kernel_conn, space_id, "tom@yuda.me", external_id)

    closed = await broker.reconcile_dangling(kernel_conn)
    outcome = next(o for o in closed if o.effect_id == effect_id)
    assert outcome.kind == "done"
    assert outcome.result["body"] == "hello"
    assert stub.read == [f"id:{external_id}"]

    rows = await (
        await kernel_conn.execute(
            "SELECT event, outcome_kind, objective_id, brief_id, generation "
            "FROM effect_ledger WHERE effect_id = %s ORDER BY id",
            (effect_id,),
        )
    ).fetchall()
    assert [r[:2] for r in rows] == [("intent", None), ("reconciled", "done")]
    assert all(r[2:] == (None, None, None) for r in rows)


async def test_a_read_whose_re_run_raises_closes_failed_with_no_card(
    kernel_conn, monkeypatch
):
    """`failed` is the action raising while the target confirms the key is
    absent, which a read's query does by construction (seams §1.10). It is a
    report, never a card."""
    stub = StubGmail(raises=True)
    monkeypatch.setitem(actions.MODULES, "connector_read", stub)
    monkeypatch.setattr(
        broker,
        "credential_for",
        lambda s, t: Credential(space=s, target=t, names=()),
    )
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    effect_id = await dangling_read(
        kernel_conn, space_id, "tom@yuda.me", uuid.uuid4().hex[:16]
    )

    closed = await broker.reconcile_dangling(kernel_conn)
    outcome = next(o for o in closed if o.effect_id == effect_id)
    assert outcome.kind == "failed"
    assert "gmail said no" in outcome.error
    assert await rows_for(kernel_conn, effect_id) == [
        ("intent", None),
        ("outcome", "failed"),
    ]
    assert not [o for o in closed if o.effect_id == effect_id and o.kind == "unknown"]


async def test_an_action_type_no_module_performs_closes_unknown(
    kernel_conn, monkeypatch
):
    """A payload whose module is gone cannot be asked about, so its state is
    in doubt and the person is the reader."""
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    effect_id = await ledger.intent(
        kernel_conn,
        action_type="open_pr",
        effect_class="propose",
        space=space_id,
        target="github.com/yudame",
        idempotency_key=f"open_pr:{uuid.uuid4().hex}",
        payload={"unreadable": True},
    )
    closed = await broker.reconcile_dangling(kernel_conn)
    outcome = next(o for o in closed if o.effect_id == effect_id)
    assert outcome.kind == "unknown"
    assert "open_pr" in outcome.error
    assert await rows_for(kernel_conn, effect_id) == [
        ("intent", None),
        ("reconciled", "unknown"),
    ]
