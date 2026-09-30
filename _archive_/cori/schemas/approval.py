"""The conversation, the typed card, and the approval record. Seams §1.12;
architecture §6, §7; tech stack §13.

Architecture §7: a card is rendered by the kernel from structured fields and
agent prose appears only in the marked, length-capped `note` region. That is
why `Card.fields` is `dict[str, str]`: every value is already a string by the
time an adapter sees it, so an adapter formats nothing and invents nothing.
`CARD_FIELDS` fixes the keys per kind and their render order, and
`OPTIONAL_CARD_FIELDS` names the keys a kind may carry and need not.

An adapter relays a `Reply`; it never constructs an `ApprovalRecord`.
`kernel/approvals.py` mints one and is the only module that does (blind-spot
review finding 12), so no adapter code can create authority.

`Reply.text` is the free text after the option on the surface (seams Round
two). `ApprovalRecord.text` carries the same text on the record, so the turn
that answers a worker has it typed rather than parsed out of `raw_message`.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from schemas.budget import Budget
from schemas.ids import ApprovalId, CardId, ConversationId, ObjectiveId, SpaceId
from schemas.space import Strict


class Conversation(Strict, frozen=True):
    id: ConversationId
    space: SpaceId
    opened_at: datetime


CardKind = Literal[
    "commit",
    "question",
    "budget_increase",
    "scope_change",
    "effect_class_elevation",
    "verification_failure",
    "unknown_outcome",
    "ethics_flag",
    "route_inbound",
]

# The keys each kind carries, in render order (seams §1.12; version 3 item 9).
# A kind whose tuple is empty has no constructor at M0 and no card of that
# kind validates; the three are raised by plans that arrive after M0.
CARD_FIELDS: dict[CardKind, tuple[str, ...]] = {
    "commit": (
        "premise",
        "non_goals",
        "success_criteria",
        "budget",
        "basis",
        "effect_ceiling",
        "deadline",
        "stop_conditions",
        "audience",
    ),
    "question": ("brief_id", "question_id", "question"),
    "verification_failure": ("brief_id", "verdict_summary", "failed_criteria"),
    "budget_increase": ("brief_id", "spent", "produced", "requested"),
    "unknown_outcome": ("effect_id", "action_type", "idempotency_key"),
    "route_inbound": ("headers", "candidates"),
    "scope_change": (),
    "effect_class_elevation": (),
    "ethics_flag": (),
}

# Keys a kind may carry and need not. `argument_sha256` on a `commit` card is
# the M2 effect approval's binding; no M0 card carries one.
OPTIONAL_CARD_FIELDS: dict[CardKind, tuple[str, ...]] = {
    kind: ("argument_sha256",) if kind == "commit" else () for kind in CARD_FIELDS
}

# Kinds with no constructor at M0. Their key tuples are empty, so an empty
# `fields` dict would otherwise pass; the refusal is explicit instead.
KINDS_WITHOUT_CONSTRUCTORS: frozenset[str] = frozenset(
    kind for kind, keys in CARD_FIELDS.items() if not keys
)

# Kinds whose `regards` is an objective id, so the row carries a non-null
# `objective_id` and the card binds a contract revision (plan 10, critique
# 10). `route_inbound` regards an inbound item and `unknown_outcome` an
# effect, and neither binds a revision.
OBJECTIVE_CARD_KINDS: frozenset[str] = frozenset(
    {"commit", "question", "verification_failure", "budget_increase"}
)


def render_value(value: Any) -> str:
    """One fixed string form per value, so a golden render is a fact.

    A string is copied as is. A `Budget` renders as dollars with two
    decimals, because the person reads money and never micros (architecture
    §4). Everything else is canonical JSON with sorted keys and no spaces,
    `datetime` as ISO 8601 in UTC.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, Budget):
        dollars = (Decimal(value.usd_micros) / Decimal(1_000_000)).quantize(
            Decimal("0.01")
        )
        return f"${dollars}"
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default
    )


def render_instant(value: datetime) -> str:
    """ISO 8601 in UTC to the second, unquoted. The form a person reads on a
    `deadline` field and in a card's header line; `render_value` quotes it,
    because there it is one value inside canonical JSON."""
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return render_instant(value)
    if isinstance(value, Budget):
        return render_value(value)
    return str(value)


class Option(Strict, frozen=True):
    key: str
    label: str
    recommended: bool = False


class Card(Strict, frozen=True):
    """Rendered by the kernel from structured fields. Agent prose only in
    `note`."""

    id: CardId
    kind: CardKind
    space: SpaceId
    conversation_id: ConversationId
    # the objective id for objective cards; the effect id for unknown_outcome;
    # the inbound item id for route_inbound
    regards: str
    # the objective's current revision on objective cards; None otherwise
    contract_revision: int | None
    fields: dict[str, str]
    options: list[Option] = Field(min_length=2, max_length=3)
    note: str = Field(max_length=500)
    expires_at: datetime
    issued_at: datetime

    @model_validator(mode="after")
    def exactly_one_recommendation(self) -> "Card":
        recommended = [option.key for option in self.options if option.recommended]
        if len(recommended) != 1:
            raise ValueError(
                "a card offers exactly one recommended option (seams §1.12); "
                f"{self.kind} offered {recommended}"
            )
        return self

    @model_validator(mode="after")
    def fields_match_the_kind(self) -> "Card":
        if self.kind in KINDS_WITHOUT_CONSTRUCTORS:
            raise ValueError(
                f"{self.kind} has no constructor at M0 and no fixed field table; "
                "the plan that raises it adds both"
            )
        required = CARD_FIELDS[self.kind]
        allowed = set(required) | set(OPTIONAL_CARD_FIELDS[self.kind])
        missing = [key for key in required if key not in self.fields]
        extra = sorted(set(self.fields) - allowed)
        if missing or extra:
            raise ValueError(
                f"{self.kind} card fields are {list(required)}"
                f"{f' plus optional {list(OPTIONAL_CARD_FIELDS[self.kind])}' if OPTIONAL_CARD_FIELDS[self.kind] else ''}; "
                f"missing {missing}, unexpected {extra}"
            )
        return self


class Reply(Strict, frozen=True):
    """What an adapter relays. Never an approval; the kernel mints that."""

    card_id: CardId
    option: str
    edit: dict[str, str] | None  # edited contract fields, commit cards only
    raw_message: str
    session_id: str
    at: datetime
    text: str | None = None  # the free text after the option (seams Round two)

    @model_validator(mode="after")
    def edit_only_on_the_edit_option(self) -> "Reply":
        if self.edit is not None and self.option != "edit":
            raise ValueError(
                f"an edit accompanies the 'edit' option only, not {self.option!r}"
            )
        return self


class Utterance(Strict, frozen=True):
    conversation_id: ConversationId
    text: str
    session_id: str
    at: datetime


class Correction(Strict, frozen=True):
    """A kernel-typed utterance, architecture §6."""

    conversation_id: ConversationId
    space: SpaceId
    what_was_wrong: str
    what_is_right: str
    regards: str | None
    raw_message: str
    session_id: str
    at: datetime


class SpaceSwitch(Strict, frozen=True):
    conversation_id: ConversationId
    to_space: SpaceId
    session_id: str
    at: datetime


Inbound = Utterance | Reply | Correction | SpaceSwitch

ApprovalKind = Literal[
    "approved", "approved_with_edit", "rejected", "answered", "self_approved", "expired"
]


class ApprovalRecord(Strict, frozen=True):
    """Minted by kernel/approvals.py and nowhere else."""

    id: ApprovalId
    card_id: CardId
    kind: ApprovalKind
    space: SpaceId
    objective_id: ObjectiveId | None
    contract_revision: int | None  # the revision the person said yes to
    argument_sha256: str | None  # digest of the exact invocation batch
    raw_message: str
    session_id: str
    decided_at: datetime
    text: str | None = None  # the reply's free text (seams Round two)
