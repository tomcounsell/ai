"""Fakes for the tree's late-bound collaborators (plan 02, "Late-bound
collaborators"). Each implements the seams' stated behaviour of the module
that will replace it. `bind` sets every name on `kernel.tree`."""

import secrets
import tempfile
from datetime import UTC, datetime
from typing import get_args

from pydantic import SecretStr

import kernel.tree as tree
from schemas.capability import Capabilities, Capability, CapabilityName
from schemas.ids import ApprovalId, CardId, ObjectiveId, SpaceId
from schemas.sandbox import SandboxProfile
from schemas.space import Space, Strict


class SpaceRefused(Exception):
    """Stands in for kernel.spaces.SpaceRefused (seams §3.4)."""


class NotAnApproval(Exception):
    """Stands in for kernel.approvals.NotAnApproval (seams §3.7)."""


class ApprovalRecord(Strict, frozen=True):
    """Stands in for schemas.approval.ApprovalRecord (seams §1.12, surface's
    to write); the fields the tree reads are `id`, `kind`, `objective_id`,
    and `contract_revision`."""

    id: ApprovalId
    card_id: CardId
    kind: str
    space: SpaceId
    objective_id: ObjectiveId | None
    contract_revision: int | None
    argument_sha256: str | None = None
    raw_message: str = ""
    session_id: str = "s1"
    decided_at: datetime = datetime(2026, 9, 21, tzinfo=UTC)
    text: str | None = None


def approval(kind: str, objective_id, revision: int, space="psyoptimal"):
    return ApprovalRecord(
        id=f"approval-{secrets.token_hex(4)}",
        card_id=f"card-{secrets.token_hex(4)}",
        kind=kind,
        space=space,
        objective_id=objective_id,
        contract_revision=revision,
    )


def make_space(
    space_id: SpaceId, *, max_effect_class="act", roots=("/repo", "/vault")
) -> Space:
    return Space(
        id=space_id, kind="client", roots=list(roots), max_effect_class=max_effect_class
    )


def fake_check_may_open(space_id: SpaceId, spaces) -> None:
    if space_id == "unassigned" or space_id not in spaces:
        raise SpaceRefused(f"space {space_id!r} may not open an objective")


def fake_root_capabilities(space: Space) -> Capabilities:
    return frozenset(
        Capability(name=n, effect_class=space.max_effect_class, scope=space.id)
        for n in get_args(CapabilityName)
    )


APPROVING = {"approved", "approved_with_edit", "self_approved"}


def fake_approval_check(
    approval, *, contract_revision=None, argument_sha256=None
) -> None:
    if approval.kind not in APPROVING:
        raise NotAnApproval(f"{approval.kind!r} is not an approval")
    if (
        contract_revision is not None
        and approval.contract_revision != contract_revision
    ):
        raise tree.StaleRevision(
            f"approval is for revision {approval.contract_revision}, "
            f"current is {contract_revision}"
        )


class FakeProfileFor:
    """Records every call; returns a profile with a temporary mount."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(
        self,
        name,
        space: Space,
        key: str,
        *,
        artifact_kind,
        max_data_class="PROJECT",
        snapshot=None,
        root=None,
    ) -> SandboxProfile:
        self.calls.append(
            dict(
                name=name,
                space=space.id,
                key=key,
                artifact_kind=artifact_kind,
                max_data_class=max_data_class,
                snapshot=snapshot,
                root=root,
            )
        )
        return SandboxProfile(
            name=name,
            space=space.id,
            mount_source=tempfile.mkdtemp(prefix="cori-tree-test-"),
            readonly=name != "worktree",
            network="hostonly",
            key=key,
            env={},
        )


class RecordingIssuer:
    def __init__(self):
        self.issued: list[dict] = []
        self.revoked: list[tuple] = []

    async def issue_token(
        self, conn, *, brief_id: str, generation: int, model_ref: str, space: SpaceId
    ) -> SecretStr:
        self.issued.append(
            dict(
                brief_id=brief_id,
                generation=generation,
                model_ref=model_ref,
                space=space,
            )
        )
        return SecretStr(secrets.token_urlsafe(24))

    async def revoke(self, conn, brief_id: str) -> None:
        self.revoked.append((conn, brief_id))


def bind(monkeypatch, spaces: dict[SpaceId, Space], *, issuer=None, profile_for=None):
    """Bind every late-bound name of kernel.tree to a fake. Returns the
    issuer and the profile recorder so a test can inspect them."""
    issuer = issuer if issuer is not None else RecordingIssuer()
    profile_for = profile_for if profile_for is not None else FakeProfileFor()
    monkeypatch.setattr(tree, "check_may_open", fake_check_may_open)
    monkeypatch.setattr(tree, "root_capabilities", fake_root_capabilities)
    monkeypatch.setattr(tree, "load_all", lambda: spaces)
    monkeypatch.setattr(tree, "approval_check", fake_approval_check)
    monkeypatch.setattr(tree, "profile_for", profile_for)
    monkeypatch.setattr(tree, "default_token_issuer", issuer)
    return issuer, profile_for


# ---------------------------------------------------------------------------
# Builders and drivers shared by the tree tests


def contract(
    *,
    budget=1_000_000,
    max_effect_class="propose",
    deadline=None,
    root="/repo",
    artifact_kind="code",
    assumptions=None,
    max_data_class="PROJECT",
):
    from datetime import timedelta

    from schemas.budget import Budget, Ceilings
    from schemas.objective import Assumption, Contract

    return Contract(
        premise="make the failing test pass",
        non_goals=["refactor"],
        success_criteria=["pytest green"],
        assumptions=(
            assumptions
            if assumptions is not None
            else [Assumption(statement="tests exist", falsification_test="ls tests")]
        ),
        budget=Budget(usd_micros=budget),
        basis="one Executor turn and one Verifier turn",
        ceilings=Ceilings(
            max_effect_class=max_effect_class,
            deadline=deadline or datetime.now(UTC) + timedelta(hours=1),
            max_data_class=max_data_class,
        ),
        task_class="code.change",
        artifact_kind=artifact_kind,
        root=root,
    )


async def force_state(conn, objective_id, state, reason, by):
    """Write an edge as its owning function would (`delegate` for RUNNING,
    `land_report` for VERIFYING) so a test can reach states whose writer
    lands in a later task."""
    loaded = await tree._load(conn, objective_id)
    return await tree._transition(conn, loaded, state, reason, by=by)


async def mint(conn, record: ApprovalRecord) -> str:
    """Append the `approval.minted` event the surface would have, so the
    tree can look the record up by id. Returns the approval id."""
    from kernel.events import append

    await append(
        conn,
        space_id=record.space,
        type="approval.minted",
        payload={"approval": record.model_dump(mode="json")},
    )
    return record.id


def caps(space_id: SpaceId, names=("read",), effect_class="propose") -> Capabilities:
    return frozenset(
        Capability(name=n, effect_class=effect_class, scope=space_id) for n in names
    )


def block(kind="instruction", data_class="PROJECT", text="write it up"):
    from schemas.brief import ContextBlock

    return ContextBlock(kind=kind, data_class=data_class, text=text, sources=[])


def context_slice(space_id: SpaceId, blocks=None, *, sha256=None):
    from schemas.brief import ContextSlice

    blocks = list(blocks) if blocks is not None else [block()]
    return ContextSlice(
        space=space_id,
        blocks=blocks,
        sha256=sha256 if sha256 is not None else ContextSlice.digest(blocks),
    )


def snapshot():
    from datetime import UTC, datetime

    from schemas.ids import new_id
    from schemas.sandbox import SnapshotRef

    return SnapshotRef(
        id=new_id(),
        handle_id="handle-1",
        path="/tmp/snapshot.tar",
        sha256="0" * 64,
        files={},
        taken_at=datetime.now(UTC),
    )


DEFAULTS = {
    "Executor": dict(
        names=("read", "write", "bash"), profile="worktree", schema="Report"
    ),
    "Verifier": dict(names=("read", "bash"), profile="verify", schema="Verdict"),
    "Scribe": dict(names=("read",), profile="scratch", schema="ScribeReport"),
    "Planner": dict(names=("read",), profile="scratch", schema="Report"),
}


def request(
    objective_id,
    agent_class,
    *,
    space: SpaceId,
    budget=1000,
    names=None,
    effect_class="propose",
    sandbox_profile=None,
    max_data_class="PROJECT",
    blocks=None,
    sha256=None,
    slice_space=None,
    snapshot_ref=None,
    instruction=None,
    report_schema=None,
):
    from schemas.brief import DelegateRequest
    from schemas.budget import Budget

    d = DEFAULTS[agent_class]
    profile = sandbox_profile or d["profile"]
    if profile == "verify" and snapshot_ref is None:
        snapshot_ref = snapshot()
    return DelegateRequest(
        objective_id=objective_id,
        agent_class=agent_class,
        budget=Budget(usd_micros=budget),
        capabilities=caps(space, names or d["names"], effect_class),
        sandbox_profile=profile,
        report_schema=report_schema or d["schema"],
        max_data_class=max_data_class,
        context_slice=context_slice(slice_space or space, blocks, sha256=sha256),
        instruction=instruction,
        snapshot=snapshot_ref,
    )


def receipt(handle_id="handle-1"):
    from schemas.sandbox import StopReceipt

    now = datetime.now(UTC)
    return StopReceipt(
        handle_id=handle_id, killed_at=now, confirmed_dead_at=now, probe="ps"
    )


def token(brief):
    from schemas.brief import BriefToken

    return BriefToken(brief_id=brief.id, generation=brief.generation)


def evidence(ref="tests/output.txt"):
    from schemas.report import EvidenceRef

    return EvidenceRef(kind="test_output", ref=ref, sha256=None)


def executor_report(deltas=None, summary="done"):
    from schemas.report import Report

    return Report(
        artifact_refs=[],
        evidence=[evidence()],
        assumption_deltas=deltas or [],
        summary=summary,
    )


def delta(statement, status="challenged"):
    from schemas.report import AssumptionDelta

    return AssumptionDelta(statement=statement, status=status, evidence=[evidence()])


def verdict(outcome="pass", criterion="pytest green"):
    from schemas.report import CriterionResult, Verdict

    met = {"pass": True, "fail": False, "abstain": None}[outcome]
    return Verdict(
        outcome=outcome,
        predicted_failure=0.1,
        criteria=[CriterionResult(criterion=criterion, met=met, reason="ran it")],
        scope_findings=[],
        summary="looked",
    )


def scribe_report():
    from schemas.report import ScribeReport

    return ScribeReport(written=[], proposed=[], could_not=[])


def terminal(outcome="report", report=None, error=None):
    from schemas.trace import Terminal

    return Terminal(outcome=outcome, report=report, error=error)
