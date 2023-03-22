from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import settings
from config import LOCAL
from systems.agent.steve.documents.users import User


async def startup_database(app):
    app.client = (
        AsyncIOMotorClient() if LOCAL else AsyncIOMotorClient(settings.mongodb_url)
    )
    app.database = app.client[settings.mongodb_db_name]
    document_models = [
        User,
    ]

    await init_beanie(database=app.database, document_models=document_models)


def shutdown_database(app):
    app.mongodb_client.close()
