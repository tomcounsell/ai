"""The door: every refusal leaves one row and touches nothing, a good action
leaves an intent committed before the target is called, and a repeat of an
effect that landed is the same effect. Plan 07 task 7; seams §3.10, §5.3.

The target here is a fake module registered in `broker.actions.MODULES`, so
the protocol is tested without a network. `broker.space_for`,
`broker.credential_for`, and `broker.check_generation` are the module-level
names `perform` calls, replaced per test.
"""

import subprocess
import uuid
from pathlib import Path
from typing import ClassVar

import psycopg
import pytest

import broker
from broker import actions, push_branch
from broker.credentials import Credential, credential_for
from broker.errors import EffectRefused
from infra.secrets import MissingSecret, read_secret
from kernel.tree import StaleGeneration
from schemas.brief import BriefToken
from schemas.effect import Action, EffectOutcome
from schemas.ids import new_id
from schemas.space import EffectClass, Space
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres

TARGET = "fake://target"
SANDBOX = "yudame/cori-sandbox"


class FakeAction(Action):
    """A `propose` action whose target is a dict in this process."""

    effect_class: ClassVar[EffectClass] = "propose"
    action_type: ClassVar[str] = "fake_effect"

    name: str

    def derive_key(self) -> str:
        return f"fake:{self.name}"

    def target(self) -> str:
        return TARGET


class FakeTarget:
    """The four names of `broker/module.py`, with a record of every call.

    `raises` makes `run` fail after deciding what the target holds, which is
    the shape `perform` step 8 reads: the action raised and only the target
    knows whether it landed.
    """

    model = FakeAction

    def __init__(self, *, state="absent", raises=None, on_run=None):
        self.written: list[str] = []
        self.queried: list[str] = []
        self.checked: list[str] = []
        self.state = state
        self.raises = raises
        self.on_run = on_run
        self.refuse = None

    def check(self, action) -> None:
        self.checked.append(action.name)
        if self.refuse:
            raise EffectRefused(self.refuse)

    async def run(self, conn, action, credential):
        if self.on_run is not None:
            await self.on_run(action)
        if self.raises is not None:
            raise self.raises
        self.written.append(action.name)
        return {"name": action.name}

    async def query(self, conn, action, credential):
        self.queried.append(action.name)
        return self.state


@pytest.fixture
def space_id():
    return f"space-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def manifest(space_id):
    return Space(
        id=space_id,
        kind="client",
        roots=[f"/{space_id}"],
        max_effect_class="propose",
        allowed_targets=[TARGET],
    )


@pytest.fixture
def door(monkeypatch, manifest):
    """The fake target registered, with the manifest, credential, and
    generation check `perform` reads replaced for one test."""
    target = FakeTarget()
    monkeypatch.setitem(actions.MODULES, FakeAction.action_type, target)
    monkeypatch.setattr(broker, "space_for", lambda space_id: manifest)
    monkeypatch.setattr(
        broker,
        "credential_for",
        lambda space_id, t: Credential(space=space_id, target=t, names=()),
    )

    async def fresh(conn, token):
        return None

    monkeypatch.setattr(broker, "check_generation", fresh)
    return target


@pytest.fixture
async def kernel_conn():
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        await conn.set_autocommit(True)
        yield conn


def act(space_id, **over) -> FakeAction:
    fields = {
        "space": space_id,
        "objective_id": new_id(),
        "brief_id": new_id(),
        "name": uuid.uuid4().hex[:8],
    }
    fields.update(over)
    return FakeAction(**fields)


def token_for(action: FakeAction, generation: int = 1) -> BriefToken:
    return BriefToken(brief_id=action.brief_id or new_id(), generation=generation)


async def rows_for(conn, action) -> list[tuple]:
    return await (
        await conn.execute(
            "SELECT event, outcome_kind, error FROM effect_ledger "
            "WHERE idempotency_key = %s ORDER BY id",
            (action.idempotency_key,),
        )
    ).fetchall()


# ---------------------------------------------------------------------------
# Refusals: one row, no target call


async def test_an_action_without_a_brief_is_refused(kernel_conn, door, space_id):
    action = act(space_id, brief_id=None)
    outcome = await broker.perform(
        kernel_conn, action, None, token=BriefToken(brief_id=new_id(), generation=1)
    )
    assert outcome.kind == "refused"
    assert outcome.error == "worker_action_without_brief"
    assert await rows_for(kernel_conn, action) == [
        ("refused", "refused", "worker_action_without_brief")
    ]
    assert door.written == [] and door.queried == []


async def test_an_action_without_an_objective_is_refused(kernel_conn, door, space_id):
    action = act(space_id, objective_id=None)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.error == "worker_action_without_brief"
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.written == []


async def test_a_stale_generation_is_refused_before_the_target(
    kernel_conn, door, space_id, monkeypatch
):
    action = act(space_id)

    async def stale(conn, token):
        raise StaleGeneration(f"{token.brief_id} carries generation 1, node is at 2")

    monkeypatch.setattr(broker, "check_generation", stale)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "refused"
    rows = await rows_for(kernel_conn, action)
    assert [r[0] for r in rows] == ["refused"]
    assert rows[0][2].startswith("stale_generation")
    assert door.written == [] and door.checked == []


async def test_a_token_for_another_brief_is_refused(kernel_conn, door, space_id):
    action = act(space_id)
    other = BriefToken(brief_id=new_id(), generation=1)
    outcome = await broker.perform(kernel_conn, action, None, token=other)
    assert outcome.error == "brief_mismatch"
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.checked == []


async def test_an_act_is_refused_as_unshipped(kernel_conn, door, space_id, monkeypatch):
    """No `act` model exists at M0, so the class is forced onto the fake to
    prove the door's own line rather than the schema's (tech stack §10)."""
    action = act(space_id)
    monkeypatch.setattr(FakeAction, "effect_class", "act")
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "refused"
    assert "class_not_shipped" in outcome.error
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.checked == []


async def test_an_unregistered_action_type_is_refused(
    kernel_conn, door, space_id, monkeypatch
):
    monkeypatch.delitem(actions.MODULES, FakeAction.action_type)
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert "class_not_shipped" in outcome.error
    assert len(await rows_for(kernel_conn, action)) == 1


async def test_a_space_above_its_ceiling_is_refused(
    kernel_conn, door, space_id, manifest, monkeypatch
):
    lowered = manifest.model_copy(update={"max_effect_class": "read"})
    monkeypatch.setattr(broker, "space_for", lambda s: lowered)
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.error == "above_space_ceiling"
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.checked == []


async def test_a_target_outside_the_space_is_refused(
    kernel_conn, door, space_id, manifest, monkeypatch
):
    narrowed = manifest.model_copy(update={"allowed_targets": ["github.com/yudame"]})
    monkeypatch.setattr(broker, "space_for", lambda s: narrowed)
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.error == "target_not_allowed"
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.checked == []


async def test_a_space_with_no_manifest_is_refused(
    kernel_conn, door, space_id, monkeypatch
):
    def missing(s):
        raise EffectRefused(f"no manifest for space {s!r}")

    monkeypatch.setattr(broker, "space_for", missing)
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert "no manifest" in outcome.error
    assert len(await rows_for(kernel_conn, action)) == 1


async def test_a_missing_credential_is_refused(
    kernel_conn, door, space_id, monkeypatch
):
    def missing(s, t):
        raise EffectRefused(f"no credential for {s!r} on {t!r}")

    monkeypatch.setattr(broker, "credential_for", missing)
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.error == "no_credential"
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.checked == []


async def test_the_modules_own_refusal_is_the_reason(kernel_conn, door, space_id):
    door.refuse = "the module says no"
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.error == "the module says no"
    assert len(await rows_for(kernel_conn, action)) == 1
    assert door.written == []


# ---------------------------------------------------------------------------
# The protocol


async def test_a_good_action_commits_its_intent_before_the_target(
    kernel_conn, door, space_id
):
    """Two transactions, not one: a second connection sees the intent while
    the target is still running. This is what makes a kill recoverable."""
    seen: list[list[tuple]] = []

    async def look(action):
        async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as other:
            seen.append(await rows_for(other, action))

    door.on_run = look
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "done" and outcome.result == {"name": action.name}
    assert seen == [[("intent", None, None)]]
    assert await rows_for(kernel_conn, action) == [
        ("intent", None, None),
        ("outcome", "done", None),
    ]
    assert door.written == [action.name]


async def test_a_raise_with_the_target_absent_closes_failed(
    kernel_conn, door, space_id
):
    door.raises = RuntimeError("the push exploded")
    door.state = "absent"
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "failed" and "exploded" in outcome.error
    assert [r[:2] for r in await rows_for(kernel_conn, action)] == [
        ("intent", None),
        ("outcome", "failed"),
    ]
    assert door.queried == [action.name]


async def test_a_raise_with_the_target_unreachable_closes_unknown(
    kernel_conn, door, space_id
):
    door.raises = RuntimeError("the push exploded")
    door.state = "unreachable"
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "unknown"
    assert [r[:2] for r in await rows_for(kernel_conn, action)] == [
        ("intent", None),
        ("outcome", "unknown"),
    ]


async def test_a_raise_with_the_target_holding_the_key_closes_recovered(
    kernel_conn, door, space_id
):
    door.raises = RuntimeError("lost the answer on the way back")
    door.state = "present"
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "recovered"
    assert [r[:2] for r in await rows_for(kernel_conn, action)] == [
        ("intent", None),
        ("reconciled", "recovered"),
    ]


async def test_a_repeat_of_a_landed_effect_is_the_same_effect(
    kernel_conn, door, space_id
):
    action = act(space_id)
    first = await broker.perform(kernel_conn, action, None, token=token_for(action))
    again = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert again == EffectOutcome(
        effect_id=first.effect_id, kind="done", result={"name": action.name}
    )
    assert len(await rows_for(kernel_conn, action)) == 2
    assert door.written == [action.name]


async def test_a_second_request_while_one_is_open_is_refused(
    kernel_conn, door, space_id
):
    """The first intent is still open, so the second request is `in_flight`
    rather than a second call on the target (seams §5.3)."""
    action = act(space_id)
    inner: list[EffectOutcome] = []

    async def race(running):
        async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as other:
            await other.set_autocommit(True)
            inner.append(
                await broker.perform(other, action, None, token=token_for(action))
            )

    door.on_run = race
    await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert inner[0].kind == "refused" and inner[0].error == "in_flight"
    assert [r[0] for r in await rows_for(kernel_conn, action)] == [
        "intent",
        "refused",
        "outcome",
    ]
    assert door.written == [action.name]


async def test_a_connection_inside_a_transaction_raises_before_any_row(door, space_id):
    action = act(space_id)
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        await conn.execute("SELECT 1")
        with pytest.raises(RuntimeError) as info:
            await broker.perform(conn, action, None, token=token_for(action))
        assert "no open transaction" in str(info.value)
        await conn.rollback()
        assert await rows_for(conn, action) == []
    assert door.checked == []


async def test_a_generation_bump_between_the_two_checks_writes_no_intent(
    kernel_conn, door, space_id, monkeypatch
):
    """Step 1 is the cheap refusal and step 6 is the one that binds. A
    `tree.stop` that commits between them still refuses, because the fence
    and the intent read one committed history (tech stack §4)."""
    calls: list[int] = []

    async def bumps(conn, token):
        calls.append(1)
        if len(calls) > 1:
            raise StaleGeneration("the node moved to generation 2")

    monkeypatch.setattr(broker, "check_generation", bumps)
    action = act(space_id)
    outcome = await broker.perform(kernel_conn, action, None, token=token_for(action))
    assert outcome.kind == "refused"
    rows = await rows_for(kernel_conn, action)
    assert [r[0] for r in rows] == ["refused"]
    assert rows[0][2].startswith("stale_generation")
    assert door.written == [] and len(calls) == 2


# ---------------------------------------------------------------------------
# Live: the whole door against the throwaway repository


def git(cwd, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


def repo_with_one_commit(tmp_path: Path) -> str:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "note.md").write_text("one commit for the broker's live test\n")
    git(tmp_path, "add", "note.md")
    git(
        tmp_path,
        "-c",
        "user.name=cori",
        "-c",
        "user.email=cori@yuda.me",
        "commit",
        "-q",
        "-m",
        "broker perform test",
    )
    return git(tmp_path, "rev-parse", "HEAD")


async def test_perform_pushes_a_branch_to_cori_sandbox(
    kernel_conn, tmp_path, monkeypatch
):
    """The one live pass through the whole door: the fence, the space, the
    credential pair, the branch rule, the intent, the push, the outcome."""
    try:
        read_secret("github_token")
    except MissingSecret:
        pytest.skip("github_token is not in the Keychain")

    async def fresh(conn, token):
        return None

    monkeypatch.setattr(broker, "check_generation", fresh)
    head = repo_with_one_commit(tmp_path)
    objective_id = new_id()
    action = push_branch.PushBranch(
        space="psyoptimal",
        objective_id=objective_id,
        brief_id=new_id(),
        repo=SANDBOX,
        branch=f"cori/{objective_id}",
        source_dir=str(tmp_path),
        head_sha=head,
    )
    token = BriefToken(brief_id=action.brief_id, generation=1)
    credential = credential_for("psyoptimal", "github.com/yudame")
    try:
        outcome = await broker.perform(kernel_conn, action, None, token=token)
        assert outcome.kind == "done" and outcome.result == {"sha": head}
        assert await push_branch.query(None, action, credential) == "present"
        assert [r[:2] for r in await rows_for(kernel_conn, action)] == [
            ("intent", None),
            ("outcome", "done"),
        ]
    finally:
        push_branch.delete_branch(action, credential.value("github_token"))
