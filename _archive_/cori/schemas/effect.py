"""Typed actions and their outcomes. Seams §1.10; tech stack §7; spike 02.

Every effect that leaves the space is one of these models. A subclass fixes
its `effect_class` and `action_type` as class variables, so the class of an
action is a fact about its type and never a field a requester fills.

The idempotency key is derived from the fields by a validator. A requester
never chooses its own key: a supplied key that differs from the derived one
is refused before the model exists. The one widening is the kernel's own
reads, which append the poll time to the derived key so two polls with one
unchanged `since` are two effects rather than one (plan 07, task 6); the
suffix is the kernel's, never a worker's, because a worker-requested read
carries no key at all.

`objective_id` and `brief_id` are `| None`, None only on a `read` the kernel
itself performs (seams Round two, amendment G). `broker.perform` refuses a
worker action with either missing, so the None shape reaches the ledger only
through `read_recent` and `fetch_body`.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from schemas.ids import BriefId, EffectId, ObjectiveId, SpaceId
from schemas.space import EffectClass, Strict

__all__ = [
    "Action",
    "ConnectorRead",
    "EffectOutcome",
    "EffectOutcomeKind",
    "PushBranch",
    "canonical_sha256",
]


def canonical_sha256(payload: dict[str, Any]) -> str:
    """The one spelling of a payload hash. `broker/ledger.py` hashes the dict
    it was handed with this, so a row written from a model and a row written
    from the kernel's own read agree on what the hash is over."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


# Reserved names, models arrive with their milestones (tech stack §7, §10):
# `propose` at M2: OpenPr, PostMessageDraft, CalendarHold.
# `act` at M3: Merge, Send, Deploy, Pay.

_POLL_SUFFIX = re.compile(r":\d+$")


class Action(Strict, frozen=True):
    """Base of every typed action."""

    space: SpaceId
    objective_id: ObjectiveId | None
    brief_id: BriefId | None
    idempotency_key: str = ""

    effect_class: ClassVar[EffectClass]
    action_type: ClassVar[str]

    @model_validator(mode="after")
    def key_is_derived(self):
        derived = self.derive_key()
        if not self.idempotency_key:
            object.__setattr__(self, "idempotency_key", derived)
            return self
        if not self.key_is_acceptable(self.idempotency_key, derived):
            raise ValueError(
                f"idempotency_key {self.idempotency_key!r} is not the key derived "
                f"from the fields, {derived!r}: a requester never chooses its own key"
            )
        return self

    def derive_key(self) -> str:
        """The key this action's fields determine. Overridden per subclass."""
        raise NotImplementedError

    @classmethod
    def key_is_acceptable(cls, supplied: str, derived: str) -> bool:
        return supplied == derived

    def target(self) -> str:
        """Matched against `Space.allowed_targets`."""
        raise NotImplementedError

    def payload_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class PushBranch(Action):
    """The one `propose` action built at M0. `broker/push_branch.py` performs
    it outside the sandbox, so no credential ever enters one."""

    effect_class: ClassVar[EffectClass] = "propose"
    action_type: ClassVar[str] = "push_branch"

    repo: str  # owner/name
    branch: str  # equals f"cori/{objective_id}"; checked by the module
    source_dir: str  # the worktree's host mount; the tool bridge fills it
    head_sha: str

    def derive_key(self) -> str:
        return f"{self.repo}#{self.branch}@{self.head_sha}"

    def target(self) -> str:
        return "github.com/" + self.repo.split("/", 1)[0]


class ConnectorRead(Action):
    """A `read` through a connector, performed outside the sandbox with the
    person's own token (architecture §9)."""

    effect_class: ClassVar[EffectClass] = "read"
    action_type: ClassVar[str] = "connector_read"

    connector: Literal["gmail"]
    account: str
    query: str

    def derive_key(self) -> str:
        return f"{self.connector}:{self.account}:{self.query}"

    @classmethod
    def key_is_acceptable(cls, supplied: str, derived: str) -> bool:
        """The derived key, or the derived key with the kernel's poll time
        appended. A read is repeatable by nature and the poll must never
        return an earlier poll's outcome, so the kernel's own reads carry the
        second (plan 07, the Gmail connector)."""
        if supplied == derived:
            return True
        return supplied.startswith(derived) and bool(
            _POLL_SUFFIX.fullmatch(supplied[len(derived) :])
        )

    def target(self) -> str:
        return "mailto:" + self.account


EffectOutcomeKind = Literal["done", "refused", "unknown", "recovered", "failed"]


class EffectOutcome(Strict, frozen=True):
    """What `perform` and `reconcile_dangling` return.

    `failed`: the action raised and the target confirms the key is absent; no
    card, only a report. `unknown` is reserved for an effect whose state on
    the target is in doubt, and is the one kind that earns a card.
    """

    effect_id: EffectId
    kind: EffectOutcomeKind
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
