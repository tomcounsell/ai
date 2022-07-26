import math
from dataclasses import dataclass, asdict


@dataclass
class Flection:
    from_e: bytes  # coords as bytes
    to_e: bytes  # coords as bytes
    _exp: int  # exponent for the strength value

    @property
    def strength(self):
        return math.e**self._exp

    def strengthen(self):
        self._exp += 1

    def weaken(self):
        self._exp -= 1
