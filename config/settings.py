from pydantic import BaseSettings


class Settings(BaseSettings):
    env_type: str = "LOCAL"
    secret_key: str = None
    mongodb_url: str = None
    mongodb_db_name: str = "ai"

    # GITHUB
    github_username: str = None
    github_access_token: str = None

    class Config:
        env_file = "config/.env"


settings = Settings()

PRODUCTION = settings.env_type.startswith("PROD")
STAGE = settings.env_type.startswith("STAGE")
LOCAL = settings.env_type.startswith("LOCAL") or not any([PRODUCTION, STAGE])
DEBUG = not PRODUCTION
