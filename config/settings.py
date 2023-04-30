from pydantic import BaseSettings


class Settings(BaseSettings):
    env_type: str = "LOCAL"
    secret_key: str = None
    mongodb_url: str = None
    mongodb_db_name: str = "ai"

    # Chat Interfaces
    telegram_bot_token: str = None
    slack_token: str = None

    # GITHUB
    github_username: str = None
    github_access_token: str = None

    # 3rd party API's
    openai_api_key: str = None
    serpapi_api_key: str = None
    wolfram_alpha_appid: str = None
    extractor_api_key: str = None
    superface_sdk_token: str = None

    class Config:
        env_file = "config/.env"


settings = Settings()

PRODUCTION = settings.env_type.startswith("PROD")
STAGE = settings.env_type.startswith("STAGE")
LOCAL = settings.env_type.startswith("LOCAL") or not any([PRODUCTION, STAGE])
DEBUG = not PRODUCTION
