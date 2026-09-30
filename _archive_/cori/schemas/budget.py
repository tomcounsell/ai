"""Budget and Ceilings. Seams §1.3; architecture §4.

A budget is money and nothing else: `usd_micros`, an integer, because spike 01
measured conservation as integer SQL arithmetic and a float in a ledger is a
rounding dispute. Tokens are what the gateway meters; the seat's price
converts. Ceilings are never summed and never exceeded by a child: effect
class, deadline, data class.

`DATA_CLASS_RANK` is the one place the data class order is written
(PROJECT below OPERATOR): everyone may see PROJECT slices and only the
supervisor and the Scribe see OPERATOR ones (architecture §3.1).
`Ceilings.fits_within` and `kernel.tree.delegate` both read it.
"""

from datetime import datetime

from pydantic import Field

from schemas.space import EFFECT_RANK, DataClass, EffectClass, Strict

DATA_CLASS_RANK: dict[DataClass, int] = {"PROJECT": 0, "OPERATOR": 1}


class Budget(Strict, frozen=True):
    """Money, and nothing else (architecture §4). Tokens are what the gateway
    meters; the seat's price converts."""

    usd_micros: int = Field(ge=0)

    def fits_within(self, other: "Budget") -> bool:
        return self.usd_micros <= other.usd_micros

    def __add__(self, other: "Budget") -> "Budget":
        return Budget(usd_micros=self.usd_micros + other.usd_micros)

    def __sub__(self, other: "Budget") -> "Budget":
        if other.usd_micros > self.usd_micros:
            raise ValueError(
                f"budget {self.usd_micros} cannot cover {other.usd_micros} usd_micros"
            )
        return Budget(usd_micros=self.usd_micros - other.usd_micros)


ZERO = Budget(usd_micros=0)


class Ceilings(Strict, frozen=True):
    """Never summed, never exceeded by a child."""

    max_effect_class: EffectClass
    deadline: datetime
    max_data_class: DataClass

    def fits_within(self, other: "Ceilings") -> bool:
        """Class rank not above, deadline not later, data class not above."""
        return (
            EFFECT_RANK[self.max_effect_class] <= EFFECT_RANK[other.max_effect_class]
            and self.deadline <= other.deadline
            and DATA_CLASS_RANK[self.max_data_class]
            <= DATA_CLASS_RANK[other.max_data_class]
        )
