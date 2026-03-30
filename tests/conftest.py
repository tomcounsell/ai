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
    """Switch popoto to a dedicated test Redis client for ALL tests.

    autouse=True ensures this runs for every test, even those that don't
    explicitly request the fixture. This prevents accidental writes to db=0
    if a test imports a popoto model without requesting isolation.

    Under pytest-xdist, each worker (gw0, gw1, ...) gets its own Redis database
    (db=1, db=2, ...) to prevent cross-worker contamination from flushdb().
    Without xdist, uses db=1 as before.

    CRITICAL: We replace the POPOTO_REDIS_DB object with a new Redis client
    pointed at the test db, rather than using SELECT on the production connection.
    SELECT is unsafe with connection pools — if the pool recycles a connection,
    the new connection defaults back to db=0 and flushdb() wipes production data.

    Also resets the async Redis connection to use the same db, since popoto v1.0.0b2
    maintains a separate _POPOTO_ASYNC_REDIS_DB connection.
    """
    import redis
    import redis.asyncio as aioredis

    import popoto.redis_db as rdb

    # Determine per-worker db number for xdist isolation
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "")
    if worker_id.startswith("gw"):
        test_db = int(worker_id[2:]) + 1  # gw0->db1, gw1->db2, etc.
    else:
        test_db = 1  # No xdist or master process

    # Save original connections
    original_sync = rdb.POPOTO_REDIS_DB
    original_async = getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)

    # Create a NEW Redis client pointed at the test db (not SELECT on the pool)
    test_client = redis.Redis(db=test_db)
    rdb.POPOTO_REDIS_DB = test_client
    test_client.flushdb()

    # Reset async Redis connection to point at the same test db.
    rdb._POPOTO_ASYNC_REDIS_DB = aioredis.Redis(db=test_db)

    yield

    # Flush test db and restore original production connections
    test_client.flushdb()
    test_client.close()
    rdb.POPOTO_REDIS_DB = original_sync
    rdb._POPOTO_ASYNC_REDIS_DB = original_async


# ---------------------------------------------------------------------------
# Auto-apply feature markers based on test filename
# ---------------------------------------------------------------------------
# Centralised here so it applies to ALL test directories (unit, integration,
# e2e, tools, performance, ai_judge).  Run a specific feature's tests with:
#     pytest -m sdlc
#     pytest -m "messaging or sessions"
# ---------------------------------------------------------------------------
FEATURE_MAP = {
    "bridge": "messaging",
    "messenger": "messaging",
    "telegram": "messaging",
    "duplicate_delivery": "messaging",
    "transcript": "messaging",
    "dedup": "messaging",
    "markdown": "messaging",
    "media_handling": "messaging",
    "routing": "messaging",
    "pm_channels": "messaging",
    "unthreaded": "messaging",
    "file_extraction": "messaging",
    "message_pipeline": "messaging",
    "reply_delivery": "messaging",
    "pipeline": "sdlc",
    "sdlc": "sdlc",
    "observer": "sdlc",
    "stop_hook": "sdlc",
    "stop_reason": "sdlc",
    "post_tool_use": "sdlc",
    "pre_tool_use": "sdlc",
    "skill_outcome": "sdlc",
    "skills_audit": "sdlc",
    "steering": "sdlc",
    "cross_repo_build": "sdlc",
    "session_status": "sessions",
    "session_stuck": "sessions",
    "session_watchdog": "sessions",
    "stall_detection": "sessions",
    "pending_stall": "sessions",
    "pending_recovery": "sessions",
    "escape_hatch": "sessions",
    "lifecycle": "sessions",
    "session_continuity": "sessions",
    "goal_gates": "sessions",
    "open_question": "sessions",
    "agent_session": "sessions",
    "job_hierarchy": "jobs",
    "job_scheduler": "jobs",
    "job_queue": "jobs",
    "job_health": "jobs",
    "enqueue": "jobs",
    "reflection": "reflections",
    "config": "config",
    "context_modes": "context",
    "session_tags": "context",
    "auto_continue": "classifiers",
    "intake_classifier": "classifiers",
    "work_request_classifier": "classifiers",
    "message_quality": "classifiers",
    "stage_aware_auto_continue": "classifiers",
    "validate_commit": "validation",
    "validate_verification": "validation",
    "validate_test_impact": "validation",
    "validate_sdlc": "validation",
    "verification_parser": "validation",
    "features_readme": "validation",
    "build_validation": "validation",
    "checkpoint": "validation",
    "docs_auditor": "validation",
    "branch_manager": "git",
    "worktree_manager": "git",
    "git_state": "git",
    "workspace_safety": "git",
    "symlinks": "git",
    "sdk_client": "sdk",
    "sdk_permissions": "sdk",
    "workflow_sdk": "sdk",
    "code_impact": "impact",
    "doc_impact": "impact",
    "cross_repo_gh": "impact",
    "cross_wire": "impact",
    "model_relationships": "models",
    "redis_models": "models",
    "summarizer": "summarizer",
    "coach": "summarizer",
    "telemetry": "monitoring",
    "health_check": "monitoring",
    "bridge_watchdog": "monitoring",
    "connectivity": "monitoring",
    "silent_failures": "monitoring",
    "remote_update": "config",
    "benchmarks": "monitoring",
    "classifier": "classifiers",
    "code_execution": "tools",
    "link_analysis": "tools",
    "doc_summary": "tools",
    "image_analysis": "tools",
    "knowledge_search": "tools",
    "search": "tools",
    "test_judge": "tools",
    "ai_judge": "tools",
    "telegram_history": "tools",
}


def pytest_collection_modifyitems(items):
    """Auto-apply feature markers based on test file name."""
    for item in items:
        filename = item.nodeid.split("::")[0].split("/")[-1].replace("test_", "").replace(".py", "")
        for pattern, marker_name in FEATURE_MAP.items():
            if pattern in filename:
                item.add_marker(getattr(pytest.mark, marker_name))
                break


@pytest.fixture
def sample_config():
    """Sample project configuration matching ~/Desktop/Valor/projects.json structure."""
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
