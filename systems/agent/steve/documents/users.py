from beanie import Document, Indexed
import pymongo

from systems.agent.steve.documents.mixins import TimestampableMixin


class User(TimestampableMixin, Document):
    name: Indexed(str, pymongo.DESCENDING)
