from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import settings
from config import LOCAL


async def startup_database(app):
    from models import active_db_models

    app.mongodb_client = (
        AsyncIOMotorClient() if LOCAL else AsyncIOMotorClient(settings.mongodb_url)
    )
    app.database = app.mongodb_client[settings.mongodb_db_name]
    await init_beanie(
        database=app.database,
        document_models=active_db_models,
    )


def shutdown_database(app):
    app.mongodb_client.close()
