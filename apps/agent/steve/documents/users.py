from beanie import Document, Indexed
from pydantic import BaseModel, Field
import pymongo
from apps.agent.steve.documents.mixins import TimestampableMixin
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, Set

USERS_COLLECTION = "users"


class Agent(TimestampableMixin, Document):
    """
    User object for storage in app.database[USERS_COLLECTION]
    """

    name: Indexed(str, pymongo.DESCENDING)
