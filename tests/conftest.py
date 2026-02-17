"""
Shared test fixtures for Valor AI tests.
"""

import pytest


@pytest.fixture(autouse=True)
def redis_test_db():
    """Switch popoto to Redis db=1 for ALL tests, preventing production pollution.

    autouse=True ensures this runs for every test, even those that don't
    explicitly request the fixture. This prevents accidental writes to db=0
    if a test imports a popoto model without requesting isolation.

    Uses SELECT on the existing connection so that popoto's module-level
    POPOTO_REDIS_DB reference (used by Query, fields, etc.) points at the
    test database without needing to replace the object.

    Also resets the async Redis connection to use db=1, since popoto v1.0.0b2
    maintains a separate _POPOTO_ASYNC_REDIS_DB connection.
    """
    import popoto.redis_db as rdb
    import redis.asyncio as aioredis
    from popoto.redis_db import POPOTO_REDIS_DB

    # Switch sync connection to db=1 and flush before each test
    POPOTO_REDIS_DB.select(1)
    POPOTO_REDIS_DB.flushdb()

    # Reset async Redis connection to point at db=1.
    # Directly create the aioredis.Redis object (constructor is sync)
    # rather than calling the async set_async_redis_db_settings().
    rdb._POPOTO_ASYNC_REDIS_DB = None
    rdb._POPOTO_ASYNC_REDIS_DB = aioredis.Redis(db=1)

    yield

    # Flush test db and switch back to production db=0
    POPOTO_REDIS_DB.flushdb()
    POPOTO_REDIS_DB.select(0)

    # Reset async connection back to default (lazy-init on next use)
    rdb._POPOTO_ASYNC_REDIS_DB = None


@pytest.fixture
def sample_config():
    """Sample project configuration matching config/projects.json structure."""
    return {
        "projects": {
            "valor": {
                "name": "Valor AI",
                "description": "AI coworker system",
                "telegram": {
                    "groups": ["Dev: Valor"],
                    "respond_to_all": False,
                    "respond_to_mentions": True,
                    "respond_to_dms": True,
                    "mention_triggers": ["@valor", "valor", "hey valor"],
                },
                "github": {"org": "tomcounsell", "repo": "ai"},
                "context": {
                    "tech_stack": ["Python", "Claude Agent SDK", "Telethon"],
                    "description": "Focus on agentic systems",
                },
            },
            "popoto": {
                "name": "Popoto",
                "description": "Redis ORM for Python",
                "telegram": {
                    "groups": ["Dev: Popoto"],
                    "respond_to_all": False,
                    "respond_to_mentions": True,
                    "respond_to_dms": False,
                },
                "github": {"org": "tomcounsell", "repo": "popoto"},
                "context": {
                    "tech_stack": ["Python", "Redis"],
                    "description": "Focus on Redis data modeling",
                },
            },
            "django-project-template": {
                "name": "Django Project Template",
                "description": "Modern Django template",
                "telegram": {
                    "groups": ["Dev: Django Template"],
                    "respond_to_all": True,  # Responds to all messages
                    "respond_to_mentions": True,
                    "respond_to_dms": False,
                },
                "github": {"org": "tomcounsell", "repo": "django-project-template"},
                "context": {
                    "tech_stack": ["Django", "PostgreSQL", "Redis"],
                    "description": "Focus on Django best practices",
                },
            },
        },
        "defaults": {
            "telegram": {
                "respond_to_all": False,
                "respond_to_mentions": True,
                "respond_to_dms": True,
                "mention_triggers": ["@valor", "valor", "hey valor"],
            },
            "response": {
                "typing_indicator": True,
                "max_response_length": 4000,
                "timeout_seconds": 300,
            },
        },
    }


@pytest.fixture
def valor_project(sample_config):
    """Extract Valor project config with _key added."""
    project = sample_config["projects"]["valor"].copy()
    project["_key"] = "valor"
    return project


@pytest.fixture
def popoto_project(sample_config):
    """Extract Popoto project config with _key added."""
    project = sample_config["projects"]["popoto"].copy()
    project["_key"] = "popoto"
    return project


@pytest.fixture
def django_project(sample_config):
    """Extract Django project config with _key added."""
    project = sample_config["projects"]["django-project-template"].copy()
    project["_key"] = "django-project-template"
    return project
