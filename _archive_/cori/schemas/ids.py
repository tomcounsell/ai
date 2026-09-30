"""Identifiers. Seams §0: every id is a UUIDv7 string minted by the kernel,
never by a worker or adapter; rows in the append-only tables use the table's
bigint identity, so an event id is an int.

Aliases rather than NewTypes: no type gate runs in CI (tech stack §1), and an
alias is what a reader sees at the call site.
"""

import uuid

SpaceId = str
ObjectiveId = str
BriefId = str
ConversationId = str
TurnId = str
CardId = str
ApprovalId = str
EffectId = str
QuestionId = str
SnapshotId = str
EpisodeId = str
EventId = int


def new_id() -> str:
    """A fresh UUIDv7 as a string. Sorts by mint time, so no table needs a
    second ordering column (seams §0)."""
    return str(uuid.uuid7())
