"""
Test configuration module.

This module defines test categories, scopes, and configuration settings
for the Django Project Template testing framework.
"""

from enum import Enum
from typing import Dict, Set


class TestType(Enum):
    """Test types supported by the testing framework."""

    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    VISUAL = "visual"
    API = "api"
    PERFORMANCE = "performance"


class TestScope(Enum):
    """Scopes or domains that tests can focus on."""

    MODEL = "model"
    VIEW = "view"
    FORM = "form"
    BEHAVIOR = "behavior"
    COMPONENT = "component"
    WORKFLOW = "workflow"


# Mapping of file patterns to test types
FILE_PATTERN_TYPE_MAP: dict[str, TestType] = {
    "test_e2e_": TestType.E2E,
    "test_browser": TestType.E2E,
    "test_visual_": TestType.VISUAL,
    "test_screenshot": TestType.VISUAL,
    "test_api": TestType.API,
    "test_integration": TestType.INTEGRATION,
    "test_performance": TestType.PERFORMANCE,
}

# Mapping of directory patterns to test scopes
DIR_PATTERN_SCOPE_MAP: dict[str, TestScope] = {
    "test_models": TestScope.MODEL,
    "test_views": TestScope.VIEW,
    "test_forms": TestScope.FORM,
    "test_behaviors": TestScope.BEHAVIOR,
    "test_components": TestScope.COMPONENT,
    "test_workflow": TestScope.WORKFLOW,
}

# Pytest markers to apply to different test types
MARKERS: dict[TestType, str] = {
    TestType.UNIT: "unit",
    TestType.INTEGRATION: "integration",
    TestType.E2E: "e2e",
    TestType.VISUAL: "visual",
    TestType.API: "api",
    TestType.PERFORMANCE: "performance",
}

# Set of tests that require browser automation
BROWSER_REQUIRED_TESTS: set[TestType] = {
    TestType.E2E,
    TestType.VISUAL,
}

# Set of tests that should run in CI
CI_TESTS: set[TestType] = {
    TestType.UNIT,
    TestType.INTEGRATION,
    TestType.API,
}

# Server URL for browser-based tests
SERVER_URL = "http://localhost:8000"

# Browser settings
BROWSER_SETTINGS = {
    "headless": True,  # Run in headless mode by default
    "slow_mo": 0,  # Slow motion in milliseconds (for debugging)
    "type": "chromium",  # Browser type (chromium, firefox, webkit)
}

# Viewport settings for responsive tests
VIEWPORTS = {
    "mobile": {"width": 375, "height": 667},  # iPhone 8
    "tablet": {"width": 768, "height": 1024},  # iPad
    "desktop": {"width": 1280, "height": 800},  # Small laptop
    "large": {"width": 1920, "height": 1080},  # Large desktop
}

# Screenshot settings
SCREENSHOT_DIR = "reports/screenshots"
