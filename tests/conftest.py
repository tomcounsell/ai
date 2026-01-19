"""
Shared test fixtures for Valor AI tests.
"""

import pytest


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
                    "tech_stack": ["Python", "Clawdbot", "Telethon"],
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
