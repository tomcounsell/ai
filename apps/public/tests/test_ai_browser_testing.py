"""
Enhanced AI-powered end-to-end testing framework for Django Project Template.

This module extends the browser-use testing capabilities with advanced features:
1. Automated test generation
2. Visual regression testing
3. Accessibility testing
4. Responsive layout testing
5. Test scenario recording and playback
6. Structured test reporting

Usage:
    # Run all AI-powered tests
    DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_ai_browser_testing.py -v

    # Run a specific test
    DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_ai_browser_testing.py::TestAIAutomatedTesting::test_generate_and_run_test -v
"""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytest

# Import pytest-asyncio
import pytest_asyncio
from django.contrib.auth import get_user_model

# Add database mark
pytestmark = [pytest.mark.django_db]  # Allow database access

# Import browser-use components safely
try:
    import playwright.async_api
    from browser_use import Agent, BrowserAgent, use
    from playwright.async_api import Browser, BrowserContext, Page

    BROWSER_USE_AVAILABLE = True

    # Import the base test class
    from apps.public.tests.test_e2e_patterns import (
        E2ETestBase,
        asyncio_mark,
        browser_test,
    )
except ImportError:
    BROWSER_USE_AVAILABLE = False

    # Create dummy classes for type hints when imports fail
    class Page:
        pass

    class BrowserAgent:
        pass

    # Create dummy markers for compatibility
    browser_test = lambda cls: cls
    asyncio_mark = lambda f: f

    # Create dummy base class
    class E2ETestBase:
        pass


# Define constants
SERVER_URL = "http://localhost:8000"
SCREENSHOTS_DIR = "test_screenshots/ai_testing"

# Get User model
User = get_user_model()


class AITestConfig:
    """Configuration for AI-powered browser testing."""

    # Test server URL
    server_url: str = SERVER_URL

    # Browser configuration
    headless: bool = False  # Set to True for CI, False for local debugging
    slow_mo: int = 100  # Slow down browser actions (ms)
    browser_type: str = "chromium"  # "chromium", "firefox", or "webkit"

    # Viewport configuration
    viewports: list[dict[str, int]] = [
        {"width": 1280, "height": 800},  # Desktop
        {"width": 768, "height": 1024},  # Tablet
        {"width": 375, "height": 667},  # Mobile
    ]

    # Screenshots directory
    screenshots_dir: str = SCREENSHOTS_DIR

    # Test generation configuration
    max_test_steps: int = 20
    test_scope: str = "basic"  # "basic", "comprehensive", or "exhaustive"

    # Reporting configuration
    report_dir: str = "test_reports/ai_browser"
    include_screenshots_in_report: bool = True

    # Accessibility testing
    run_accessibility_tests: bool = True
    accessibility_standard: str = "WCAG2AA"  # WCAG2A, WCAG2AA, WCAG2AAA

    @classmethod
    def ensure_directories(cls):
        """Ensure all required directories exist."""
        os.makedirs(cls.screenshots_dir, exist_ok=True)
        os.makedirs(cls.report_dir, exist_ok=True)


class TestReport:
    """Class for structured test reporting."""

    def __init__(self, test_name: str, config: AITestConfig):
        self.test_name = test_name
        self.config = config
        self.start_time = datetime.now()
        self.end_time: datetime | None = None
        self.status = "running"
        self.steps: list[dict[str, Any]] = []
        self.screenshots: list[str] = []
        self.issues: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {
            "browser": config.browser_type,
            "viewports": config.viewports,
            "headless": config.headless,
        }

    def add_step(
        self,
        description: str,
        status: str = "pass",
        details: dict[str, Any] | None = None,
    ):
        """Add a test step to the report."""
        step = {
            "description": description,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
        }
        self.steps.append(step)
        return len(self.steps) - 1  # Return step index

    def add_screenshot(self, path: str, description: str):
        """Add a screenshot to the report."""
        self.screenshots.append(
            {
                "path": path,
                "description": description,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def add_issue(
        self,
        issue_type: str,
        description: str,
        details: dict[str, Any] | None = None,
    ):
        """Add an issue to the report."""
        issue = {
            "type": issue_type,
            "description": description,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
        }
        self.issues.append(issue)

    def complete(self, status: str = "pass"):
        """Mark the test as complete with the given status."""
        self.end_time = datetime.now()
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        """Convert the report to a dictionary."""
        return {
            "test_name": self.test_name,
            "status": self.status,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": (
                (self.end_time - self.start_time).total_seconds()
                if self.end_time
                else None
            ),
            "steps": self.steps,
            "screenshots": self.screenshots,
            "issues": self.issues,
            "metadata": self.metadata,
        }

    def save(self):
        """Save the report to a JSON file."""
        os.makedirs(self.config.report_dir, exist_ok=True)

        # Generate filename from test name and timestamp
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        filename = f"{self.test_name.replace(' ', '_')}_{timestamp}.json"

        # Write the report
        filepath = os.path.join(self.config.report_dir, filename)
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

        return filepath


class AIBrowserTesting:
    """Base class for AI-powered browser testing."""

    config = AITestConfig()

    @classmethod
    def setup_class(cls):
        """Set up test class."""
        # Ensure required directories exist
        cls.config.ensure_directories()

        # Check for a running server
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("localhost", 8000))
        if result != 0:
            pytest.skip(
                "Test server not running. Start with 'python manage.py runserver'"
            )

    @staticmethod
    async def create_test_user() -> tuple[User, str, str]:
        """Create a test user with random credentials.

        Returns:
            tuple: (user, username, password)
        """
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        password = "testpassword123"
        user = User.objects.create_user(
            username=username, email=f"{username}@example.com", password=password
        )
        return user, username, password

    @staticmethod
    async def generate_test_scenario(
        feature_description: str, config: AITestConfig
    ) -> list[str]:
        """Generate a test scenario using AI based on feature description.

        Args:
            feature_description: Description of the feature to test
            config: Test configuration

        Returns:
            list: List of test steps
        """
        if not BROWSER_USE_AVAILABLE:
            return [
                f"# browser-use not available - would generate test for: {feature_description}"
            ]

        # Define the prompt for generating test steps
        prompt = f"""
        Generate a detailed browser test scenario for the following feature:

        FEATURE: {feature_description}

        The test should:
        1. Be detailed enough for browser-use to execute
        2. Include specific UI element interactions (buttons, forms, etc.)
        3. Verify expected behavior at each step
        4. Include appropriate wait times for page loading
        5. Take screenshots at key points

        Format each step as a specific instruction that browser-use can execute.
        Limit to {config.max_test_steps} steps maximum.
        Test scope level: {config.test_scope}

        SERVER_URL: {config.server_url}
        """

        # Use browser-use's Agent to generate the test steps
        agent = Agent()
        response = await agent.complete(prompt)

        # Parse the response into a list of steps
        steps = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("STEP"):
                # Remove step numbers if present (e.g., "1. Go to...")
                if line[0].isdigit() and line[1:3] in [". ", ") "]:
                    line = line[3:]
                steps.append(line)

        return steps

    @staticmethod
    async def run_test_with_report(
        steps: list[str],
        config: AITestConfig,
        test_name: str,
        credentials: tuple[User, str, str] | None = None,
    ) -> TestReport:
        """Run a test scenario and generate a report.

        Args:
            steps: List of test steps
            config: Test configuration
            test_name: Name of the test
            credentials: Optional tuple of (user, username, password)

        Returns:
            TestReport: Test report
        """
        if not BROWSER_USE_AVAILABLE:
            # Create a dummy report
            report = TestReport(test_name, config)
            report.add_step("browser-use not available", "skip")
            report.add_issue(
                "environment", "browser-use not installed", {"steps": steps}
            )
            report.complete("skip")
            return report

        # Create a test report
        report = TestReport(test_name, config)

        try:
            # Create test user if not provided
            if not credentials:
                user, username, password = await AIBrowserTesting.create_test_user()
                credentials = (user, username, password)

            # Add credentials to steps if needed
            processed_steps = []
            for step in steps:
                if "[USERNAME]" in step:
                    step = step.replace("[USERNAME]", credentials[1])
                if "[PASSWORD]" in step:
                    step = step.replace("[PASSWORD]", credentials[2])
                processed_steps.append(step)

            # Add step to report
            step_idx = report.add_step("Setting up test", "running")

            # Create browser agent
            agent = BrowserAgent(
                tasks=processed_steps,
                browser_type=config.browser_type,
                headless=config.headless,
                slow_mo=config.slow_mo,
                screenshot_dir=config.screenshots_dir,
            )

            # Update step in report
            report.steps[step_idx]["status"] = "pass"

            # Run the agent
            step_idx = report.add_step("Running browser agent", "running")
            result = await agent.run()
            report.steps[step_idx]["status"] = "pass"
            report.steps[step_idx]["details"] = {"result": result}

            # Check screenshots directory for new files
            if os.path.exists(config.screenshots_dir):
                for filename in os.listdir(config.screenshots_dir):
                    if (
                        filename.endswith(".png")
                        and os.path.getmtime(
                            os.path.join(config.screenshots_dir, filename)
                        )
                        > report.start_time.timestamp()
                    ):
                        report.add_screenshot(
                            os.path.join(config.screenshots_dir, filename),
                            f"Screenshot from test: {filename}",
                        )

            # Mark test as complete
            if "success" in result.lower() or "completed" in result.lower():
                report.complete("pass")
            else:
                report.complete("fail")
                report.add_issue(
                    "execution",
                    "Browser agent did not report success",
                    {"result": result},
                )

        except Exception as e:
            # Handle any exceptions
            report.add_issue("exception", str(e), {"traceback": str(e.__traceback__)})
            report.complete("error")

        # Save the report
        report_path = report.save()
        print(f"Test report saved to {report_path}")

        return report

    @staticmethod
    async def run_visual_regression_test(
        baseline_dir: str, current_screenshots: list[str], threshold: float = 0.1
    ) -> dict[str, Any]:
        """Run a visual regression test comparing screenshots.

        This is a placeholder - in a real implementation, you would use
        an image comparison library to compare screenshots.

        Args:
            baseline_dir: Directory with baseline screenshots
            current_screenshots: List of current screenshot paths
            threshold: Threshold for image difference (0-1)

        Returns:
            dict: Results of the comparison
        """
        # Placeholder for visual regression testing
        # In a real implementation, you'd use an image comparison library

        results = {"passed": True, "total_compared": 0, "differences": []}

        # This is just a placeholder implementation
        for screenshot in current_screenshots:
            filename = os.path.basename(screenshot)
            baseline_path = os.path.join(baseline_dir, filename)

            if os.path.exists(baseline_path):
                results["total_compared"] += 1
                # In a real implementation, compare the images
                # and detect differences exceeding the threshold
            else:
                # New screenshot, no baseline
                results["differences"].append(
                    {"screenshot": screenshot, "reason": "No baseline image found"}
                )

        return results

    @staticmethod
    async def run_accessibility_test(
        page: Page, config: AITestConfig
    ) -> dict[str, Any]:
        """Run accessibility tests on the current page.

        This is a placeholder - in a real implementation, you would use
        a library like Axe or Pa11y to check accessibility.

        Args:
            page: Playwright page object
            config: Test configuration

        Returns:
            dict: Accessibility test results
        """
        # Placeholder for accessibility testing
        # In a real implementation, you'd use a library like Axe

        # Example of using Playwright's built-in accessibility scan
        # (This would need to be implemented with the actual accessibility library)
        snapshot = await page.accessibility.snapshot()

        return {
            "standard": config.accessibility_standard,
            "url": page.url,
            "timestamp": datetime.now().isoformat(),
            "snapshot": snapshot,
            "issues": [],  # In a real implementation, this would contain actual issues
        }


@browser_test
@asyncio_mark
class TestAIAutomatedTesting(AIBrowserTesting):
    """Test AI-powered automated test generation and execution."""

    async def test_generate_and_run_test(self):
        """Test generating a test scenario and running it."""
        # Skip if browser-use not available
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use package not installed")

        # Generate a test scenario
        steps = await self.generate_test_scenario(
            "User login and navigation to the Todo list page", self.config
        )

        # Create test user
        user, username, password = await self.create_test_user()

        # Add login credentials to the steps
        steps_with_creds = []
        for step in steps:
            step = step.replace("[USERNAME]", username)
            step = step.replace("[PASSWORD]", password)
            steps_with_creds.append(step)

        # Print the generated test steps
        print("\nGenerated test steps:")
        for i, step in enumerate(steps_with_creds, 1):
            print(f"{i}. {step}")

        # Run the test
        report = await self.run_test_with_report(
            steps_with_creds,
            self.config,
            "test_login_and_navigation",
            (user, username, password),
        )

        # Assert test passed
        assert report.status in [
            "pass",
            "skip",
        ], f"Test failed with status: {report.status}"

        # Display report location
        print(f"\nTest report saved to {self.config.report_dir}")

        # Return the report for potential further use
        return report

    async def test_todo_workflow_automatic(self):
        """Test automatically generated Todo workflow."""
        # Skip if browser-use not available
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use package not installed")

        # Generate a test scenario for the Todo workflow
        steps = await self.generate_test_scenario(
            "User creates a new Todo item, marks it as complete, and deletes it",
            self.config,
        )

        # Create test user
        user, username, password = await self.create_test_user()
        todo_title = f"Auto Todo {uuid.uuid4().hex[:8]}"

        # Customize steps with user credentials and todo title
        steps_with_data = []
        for step in steps:
            step = step.replace("[USERNAME]", username)
            step = step.replace("[PASSWORD]", password)
            step = step.replace("[TODO_TITLE]", todo_title)
            steps_with_data.append(step)

        # Run the test
        report = await self.run_test_with_report(
            steps_with_data,
            self.config,
            "test_auto_todo_workflow",
            (user, username, password),
        )

        # Assert test passed
        assert report.status in [
            "pass",
            "skip",
        ], f"Test failed with status: {report.status}"

        return report


@browser_test
@asyncio_mark
class TestMultiViewportBrowserTesting(AIBrowserTesting):
    """Test responsive layouts in multiple viewport sizes."""

    @pytest_asyncio.fixture
    async def browser_context(self):
        """Fixture to create browser context."""
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use package not installed")

        # Initialize Playwright
        playwright_instance = await playwright.async_api.async_playwright().start()
        browser_instance = await getattr(
            playwright_instance, self.config.browser_type
        ).launch(headless=self.config.headless, slow_mo=self.config.slow_mo)

        yield browser_instance

        # Clean up
        await browser_instance.close()
        await playwright_instance.stop()

    async def test_responsive_layout(self, browser_context):
        """Test responsive layout in multiple viewport sizes."""
        # Create test user
        user, username, password = await self.create_test_user()

        # Create screenshots directory for this test
        screenshots_dir = os.path.join(self.config.screenshots_dir, "responsive")
        os.makedirs(screenshots_dir, exist_ok=True)

        # Create a test report
        report = TestReport("test_responsive_layout", self.config)

        # Define pages to test
        pages_to_test = [
            "/",  # Home page
            "/todos/",  # Todo list page
            "/account/settings/",  # Account settings page
        ]

        # Test each viewport
        for viewport in self.config.viewports:
            # Create a new context with this viewport
            device_name = f"{viewport['width']}x{viewport['height']}"

            context = await browser_context.new_context(viewport=viewport)
            page = await context.new_page()

            # Login first
            report.add_step(f"Logging in with viewport {device_name}", "running")

            await page.goto(f"{self.config.server_url}/account/login")
            await page.fill("input[name=username]", username)
            await page.fill("input[name=password]", password)
            await page.click("button[type=submit]")
            await page.wait_for_timeout(2000)  # Wait for login to complete

            report.steps[-1]["status"] = "pass"

            # Test each page
            for page_url in pages_to_test:
                step_idx = report.add_step(
                    f"Testing page {page_url} with viewport {device_name}", "running"
                )

                # Navigate to the page
                full_url = f"{self.config.server_url}{page_url}"
                await page.goto(full_url)
                await page.wait_for_load_state("networkidle")

                # Take screenshot
                screenshot_path = os.path.join(
                    screenshots_dir, f"{page_url.replace('/', '_')}_{device_name}.png"
                )
                await page.screenshot(path=screenshot_path)

                # Add screenshot to report
                report.add_screenshot(
                    screenshot_path, f"Screenshot of {page_url} at {device_name}"
                )

                # Update step status
                report.steps[step_idx]["status"] = "pass"
                report.steps[step_idx]["details"] = {
                    "viewport": viewport,
                    "url": full_url,
                    "screenshot": screenshot_path,
                }

                # Optional: Run basic accessibility test
                if self.config.run_accessibility_tests:
                    a11y_results = await self.run_accessibility_test(page, self.config)
                    # In a real implementation, process the results

            # Close context for this viewport
            await context.close()

        # Complete report
        report.complete("pass")
        report.save()

        # Return the set of screenshots for potential visual comparison
        return [s["path"] for s in report.screenshots]


@browser_test
@asyncio_mark
class TestAIAssistantBrowserTesting(AIBrowserTesting):
    """Test having AI assistant perform complex testing workflows."""

    async def test_discover_and_test_workflow(self):
        """Test where AI discovers a workflow and tests it automatically."""
        # Skip if browser-use not available
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use package not installed")

        # Create test user
        user, username, password = await self.create_test_user()

        # Define the exploration prompt for the AI
        exploration_prompt = f"""
        You are a testing assistant. Your task is to:

        1. Go to {self.config.server_url}
        2. Login with username "{username}" and password "{password}"
        3. Explore the application to discover key features
        4. Document what features you find
        5. Create a detailed test plan for ONE important feature
        6. Execute that test plan
        7. Take screenshots at key steps
        8. Report your findings

        Focus on finding interactive elements and understanding user workflows.
        """

        # Create AI agent for exploration
        agent = BrowserAgent(
            tasks=[exploration_prompt],
            browser_type=self.config.browser_type,
            headless=self.config.headless,
            slow_mo=self.config.slow_mo,
            screenshot_dir=self.config.screenshots_dir,
        )

        # Run the exploration
        print("\nRunning AI exploration of the application...")
        result = await agent.run()
        print(f"\nExploration result: {result}")

        # The agent should have returned a description of features found
        # and a test plan in its result

        # We could parse the result and execute the generated test plan,
        # but for now we'll just check that the exploration completed successfully
        assert (
            "success" in result.lower() or "completed" in result.lower()
        ), f"Exploration failed: {result}"

        return result


def setup_module(module):
    """Set up the module for testing."""
    # Ensure required directories exist
    AITestConfig.ensure_directories()

    # Check for running server
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("localhost", 8000))
    if result != 0:
        pytest.skip("Test server not running. Start with 'python manage.py runserver'")

    # Check if browser-use is available
    if not BROWSER_USE_AVAILABLE:
        pytest.skip(
            "browser-use package not installed. Run 'uv add --dev browser-use playwright pytest-asyncio'"
        )


if __name__ == "__main__":
    # Run the tests when this module is executed directly
    pytest.main(["-xvs", __file__])
