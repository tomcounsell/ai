"""
Shared test fixtures for Valor AI tests.
"""

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Centralized claude_agent_sdk mock
# ---------------------------------------------------------------------------
# Several test files need ``import agent.*`` which transitively imports
# ``claude_agent_sdk``.  When the real SDK is not installed the import
# would fail during pytest collection.  Previously each test file had its
# own module-level ``sys.modules["claude_agent_sdk"] = MagicMock()`` which
# persisted across the pytest session and contaminated later tests.
#
# Centralizing the mock here (conftest.py is always imported before test
# modules are collected) means:
# 1. Only one place manages the mock -- no 7 scattered copies
# 2. The autouse fixture below restores sys.modules after each test
# 3. Tests that need the real SDK (e.g. test_cross_wire_fixes.py) get
#    a clean sys.modules state
# ---------------------------------------------------------------------------
# Check if the real SDK is importable (installed), not just loaded.
# If it's installed, don't inject a mock -- let tests use the real SDK.
# If it's NOT installed, inject a MagicMock so that ``import agent.*``
# succeeds during test collection.
try:
    import claude_agent_sdk  # noqa: F401

    _SDK_IMPORTABLE = True
except ImportError:
    _SDK_IMPORTABLE = False

_SDK_PRESENT_AT_STARTUP = "claude_agent_sdk" in sys.modules
_SDK_ORIGINAL_VALUE = sys.modules.get("claude_agent_sdk")

if not _SDK_IMPORTABLE:
    sys.modules["claude_agent_sdk"] = MagicMock()


@pytest.fixture(autouse=True)
def mock_claude_sdk_cleanup():
    """Restore sys.modules["claude_agent_sdk"] to pre-collection state after each test.

    Problem: Seven test files previously injected a MagicMock into
    sys.modules at module level (during collection, before any fixture
    runs).  The mock persisted for the entire pytest session, contaminating
    later tests (e.g. test_cross_wire_fixes.py) that expect the real SDK.

    Solution: At conftest import time (before test files are collected) we
    snapshot whether the real SDK exists.  After each test function we
    restore that original state.  If the SDK entry was swapped during the
    test (i.e. a mock was injected where the real SDK was, or vice versa),
    we also evict cached ``agent.*`` modules so they get re-imported
    cleanly against the restored SDK.
    """
    sdk_before_test = sys.modules.get("claude_agent_sdk")

    yield

    sdk_after_test = sys.modules.get("claude_agent_sdk")

    # Restore the SDK entry to its pre-collection state
    if _SDK_PRESENT_AT_STARTUP:
        sys.modules["claude_agent_sdk"] = _SDK_ORIGINAL_VALUE
    else:
        sys.modules.pop("claude_agent_sdk", None)

    # Only evict agent.* modules if the SDK entry was swapped during the
    # test.  Blanket eviction after every test is too aggressive and
    # breaks module-level state for unrelated tests.
    if sdk_after_test is not sdk_before_test:
        agent_modules = [key for key in sys.modules if key == "agent" or key.startswith("agent.")]
        for mod_key in agent_modules:
            del sys.modules[mod_key]


@pytest.fixture(autouse=True)
def redis_test_db(request):
    """Switch popoto to a per-worker Redis db for ALL tests, preventing production pollution.

    autouse=True ensures this runs for every test, even those that don't
    explicitly request the fixture. This prevents accidental writes to db=0
    if a test imports a popoto model without requesting isolation.

    Under pytest-xdist, each worker (gw0, gw1, ...) gets its own Redis database
    (db=1, db=2, ...) to prevent cross-worker contamination from flushdb().
    Without xdist, uses db=1 as before.

    Uses SELECT on the existing connection so that popoto's module-level
    POPOTO_REDIS_DB reference (used by Query, fields, etc.) points at the
    test database without needing to replace the object.

    Also resets the async Redis connection to use the same db, since popoto v1.0.0b2
    maintains a separate _POPOTO_ASYNC_REDIS_DB connection.
    """
    import popoto.redis_db as rdb
    import redis.asyncio as aioredis
    from popoto.redis_db import POPOTO_REDIS_DB

    # Determine per-worker db number for xdist isolation
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "")
    if worker_id.startswith("gw"):
        test_db = int(worker_id[2:]) + 1  # gw0->db1, gw1->db2, etc.
    else:
        test_db = 1  # No xdist or master process

    # Switch sync connection to test db and flush before each test
    POPOTO_REDIS_DB.select(test_db)
    POPOTO_REDIS_DB.flushdb()

    # Reset async Redis connection to point at the same test db.
    # Directly create the aioredis.Redis object (constructor is sync)
    # rather than calling the async set_async_redis_db_settings().
    rdb._POPOTO_ASYNC_REDIS_DB = None
    rdb._POPOTO_ASYNC_REDIS_DB = aioredis.Redis(db=test_db)

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
