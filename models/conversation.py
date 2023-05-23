from typing import Set, List

from beanie import Document, Link
from pydantic import BaseModel

from models import Agent
from models.agent import Title
from models.behaviors.mixins import TimestampableMixin


class Role(BaseModel):
    title: Title
    level: int
    personality: str


class Conversation(TimestampableMixin, Document):
    beholder_agent: Link[Agent]
    role: Role
    member_agents: Set[Link[Agent]]
    topic: str = ""
    transcript: List[str] = []
