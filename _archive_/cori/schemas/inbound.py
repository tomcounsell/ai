"""An item a connector read, before anything reads its body. Seams §1.11;
architecture §9.

The item is headers and nothing else. A body is a separate `read` the broker
performs only for an item in an assigned space, so an unassigned item stays
header-only forever. `headers` keys are lowercase: the reader lowercases them
before constructing the item, so `matches` never has to guess a header's case
and two connectors never disagree about `From` against `from`.
"""

from datetime import datetime
from typing import Literal

from pydantic import field_validator

from schemas.ids import SpaceId
from schemas.space import Strict

ConnectorKind = Literal["gmail", "calendar", "messaging"]


class InboundItem(Strict, frozen=True):
    id: str
    connector: ConnectorKind
    account: str
    external_id: str  # the provider's message id
    headers: dict[str, str]  # lowercase keys: from, to, subject, date, message-id
    received_at: datetime
    space: SpaceId  # the routed space, or "unassigned"
    routed_by: str | None  # the rule that matched, rendered as text

    @field_validator("headers")
    @classmethod
    def headers_are_lowercase(cls, value: dict[str, str]) -> dict[str, str]:
        wrong = sorted(k for k in value if k != k.lower())
        if wrong:
            raise ValueError(
                f"header keys must be lowercase; the reader lowercases them: {wrong}"
            )
        return value
