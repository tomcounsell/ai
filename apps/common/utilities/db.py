import typing
from typing import List, Tuple
from enum import Enum


def enum_to_choices(enum: Enum) -> List[Tuple[int, str]]:
    return [(i.value, i.name) for i in enum]
