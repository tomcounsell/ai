import csv
import os
from enum import Enum
from pathlib import Path
from typing import Optional

from beanie import Document

from models.behaviors.mixins import TimestampableMixin

FRIENDLY_NAMES_LIST = []


def load_friendly_names():
    global FRIENDLY_NAMES_LIST
    if not FRIENDLY_NAMES_LIST:
        site_root = Path(__file__).parent / ".."
        with open(site_root / "apps/agent/names.csv", newline="") as f:
            reader = csv.reader(f)
            FRIENDLY_NAMES_LIST = [row[0] for row in reader]


class Title(Enum):
    Visionary = "Visionary"
    Manager = "Manager"
    Engineer = "Engineer"
    Intern = "Intern"


class Agent(TimestampableMixin, Document):
    name: Optional[str]
    title: Title = Title.Intern
    is_human: bool = False

    async def save(self):
        # Get a name if you don't have one
        if not self.id:
            await super().save()  # need an id generated before choosing a name
        if not self.name:
            load_friendly_names()
            self.name = FRIENDLY_NAMES_LIST[
                hash(self.id) % len(FRIENDLY_NAMES_LIST)
            ] + str(hash(self.id) % 100)
        await super().save()
