from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
from typing import List
from uuid import UUID


class AgentType(Enum):
    EUROPEAN = "european"
    PERSIAN = "persian"


class Foo(IntEnum):
    FOO = 1
    UFO = 2


@dataclass
class CreateAgentInput:
    name: str
    active: bool
    type: AgentType
    foo: Foo


@dataclass
class CreateAgentOutput:
    id: UUID


@dataclass
class FriendInput:
    id: str
    best: bool


@dataclass
class Agent:
    id: UUID
    name: str
    active: bool
    type: AgentType
    creation_time: datetime


@dataclass
class UpdateAgentInput:
    name: str
    active: bool


@dataclass
class AgentsList:
    items: List[Agent]
    total: int
