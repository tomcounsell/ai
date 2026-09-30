"""Read the seat file. Tech stack §4.1: the kernel picks, the gateway enforces.

The verifier seat carries a `screened` flag. Age is a default, not evidence:
an unscreened seat may only be older than the frontier, and a newer model
takes the seat once it has passed the fixture screen (tech stack §4.1, M1).
"""

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

SEAT_FILE = Path(__file__).with_name("models.yaml")

# The gateway's usage fields, in the order the provider bills them, against
# the price each is charged at (plan 04, the budget pre-check).
PRICE_OF_FIELD = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_creation_input_tokens": "cache_write",
    "cache_read_input_tokens": "cache_read",
}


class Prices(BaseModel):
    """US dollars per million tokens, as the provider's pricing page writes
    them. Kept as written so the file stays readable against the page, and
    converted once through `Decimal` so no binary float rounds a charge."""

    model_config = ConfigDict(extra="forbid")

    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


class ModelEntry(BaseModel):
    id: str
    family: str
    generation: str
    created_at: object
    max_input_tokens: int
    max_output_tokens: int
    min_cache_prefix: int
    usd_per_mtok: Prices | None = None

    @property
    def usd_micros_per_mtok(self) -> dict[str, int]:
        """The four prices as integer micro-dollars per million tokens,
        which is the unit every budget in the system is written in (seams
        §1.5). A model with no prices cannot be seated, so the gateway never
        reaches this on one."""
        if self.usd_per_mtok is None:
            raise ValueError(f"model {self.id!r} carries no usd_per_mtok")
        return {
            name: int(getattr(self.usd_per_mtok, name) * 1_000_000)
            for name in ("input", "output", "cache_write", "cache_read")
        }


class VerifierSeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    screened: bool


class Seats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier: str
    verifier: VerifierSeat
    summarizer: str


class SeatFile(BaseModel):
    models: list[ModelEntry]
    seats: Seats

    @model_validator(mode="after")
    def seats_name_pinned_models(self):
        by_id = {m.id: m for m in self.models}
        for seat, model_id in self.seat_ids().items():
            if model_id not in by_id:
                raise ValueError(
                    f"seat {seat!r} names {model_id!r}, which is not pinned"
                )
            # A seated model is one the gateway will price. Without the four
            # numbers the budget pre-check has nothing to reserve against.
            if by_id[model_id].usd_per_mtok is None:
                raise ValueError(
                    f"seat {seat!r} names {model_id!r}, which carries no "
                    f"usd_per_mtok; every seated model needs all four prices"
                )
        frontier = by_id[self.seats.frontier]
        verifier = by_id[self.seats.verifier.model]
        # Independence: the verifier is never the executor's snapshot.
        if verifier.id == frontier.id:
            raise ValueError("verifier seat is the frontier snapshot")
        # Age stands in for evidence only until the screen has run.
        if not self.seats.verifier.screened and str(verifier.created_at) >= str(
            frontier.created_at
        ):
            raise ValueError(
                "verifier seat is newer than the frontier and unscreened; "
                "a newer model takes the seat after the fixture screen"
            )
        return self

    def seat_ids(self) -> dict[str, str]:
        return {
            "frontier": self.seats.frontier,
            "verifier": self.seats.verifier.model,
            "summarizer": self.seats.summarizer,
        }

    def model(self, seat: str) -> ModelEntry:
        model_id = self.seat_ids()[seat]
        return next(m for m in self.models if m.id == model_id)


def load(path: Path = SEAT_FILE) -> SeatFile:
    return SeatFile.model_validate(yaml.safe_load(path.read_text()))
