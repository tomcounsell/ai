import math
from dataclasses import dataclass, asdict


@dataclass
class Flection:
    from_e: bytes
    to_e: bytes
    _exp: int

    @property
    def strength(self):
        return math.e**self._exp

    def strengthen(self):
        self._exp += 1

    def weaken(self):
        self._exp -= 1
