from enum import Enum


def enum_to_choices(enum: Enum) -> list[tuple[int, str]]:
    return [(i.value, i.name) for i in enum]
