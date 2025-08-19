# AI-Powered Browser Testing

This guide describes how to use AI-powered browser testing in the Django Project Template. This approach leverages browser-use and Playwright to create intelligent, automated end-to-end tests.

## Overview

The Django Project Template includes an enhanced AI-powered browser testing framework that extends beyond basic end-to-end (E2E) testing. This framework provides several advanced capabilities:

1. **Automated Test Generation**: AI generates test scenarios based on feature descriptions
2. **Visual Testing**: Capture screenshots and perform visual regression testing
3. **Responsive Design Testing**: Test layouts across multiple device viewports
4. **AI-Assisted Exploration**: Have an AI agent discover and test application workflows
5. **Structured Test Reporting**: Generate comprehensive test reports with screenshots
6. **Accessibility Testing**: (Stub implementation) Check for accessibility issues

## Prerequisites

To use the AI-powered browser testing framework, you need:

```bash
# Make sure you're in your virtual environment
source venv/bin/activate

# Install required packages
uv add --dev browser-use playwright pytest-asyncio

# Install playwright browser drivers
playwright install
```

Note: The browser-use package requires Python 3.11 or higher.

## Running AI-Powered Tests

First, make sure your Django development server is running:

```bash
# Terminal 1: Start the development server
python manage.py runserver
```

Then, in a separate terminal, run the AI-powered tests:

```bash
# Terminal 2: Run all AI-powered tests
DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_ai_browser_testing.py -v

# Run a specific test
DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_ai_browser_testing.py::TestAIAutomatedTesting::test_generate_and_run_test -v
```

## Test Categories

The AI browser testing framework includes several test classes:

### 1. TestAIAutomatedTesting

This class demonstrates how to:
- Generate test scenarios using AI
- Execute generated test steps
- Create structured test reports

```python
# Example test that generates and runs a login test
async def test_generate_and_run_test(self):
    # Generate steps for login and navigation
    steps = await self.generate_test_scenario(
        "User login and navigation to the Todo list page",
        self.config
    )
    
    # Create test user
    user, username, password = await self.create_test_user()
    
    # Run the test with generated steps
    report = await self.run_test_with_report(
        steps,
        self.config,
        "test_login_and_navigation",
        (user, username, password)
    )
```

### 2. TestMultiViewportBrowserTesting

This class tests the application's responsive design across multiple viewport sizes:
- Desktop: 1280x800
- Tablet: 768x1024 
- Mobile: 375x667

```python
# Example test that checks responsive layouts
async def test_responsive_layout(self, browser_context):
    # Test each viewport
    for viewport in self.config.viewports:
        # Create context with viewport
        context = await browser_context.new_context(viewport=viewport)
        page = await context.new_page()
        
        # Test pages with this viewport
        for page_url in pages_to_test:
            await page.goto(f"{self.config.server_url}{page_url}")
            
            # Take screenshot for this viewport
            screenshot_path = f"{page_url.replace('/', '_')}_{viewport['width']}x{viewport['height']}.png"
            await page.screenshot(path=screenshot_path)
```

### 3. TestAIAssistantBrowserTesting

This class demonstrates how AI can discover and test application workflows:
- Explores the application autonomously
- Discovers key features
- Generates and executes test plans

```python
# Example test where AI discovers and tests a workflow
async def test_discover_and_test_workflow(self):
    # Define exploration prompt
    exploration_prompt = """
    Go to the application, login, and:
    1. Explore to discover key features
    2. Create a test plan for an important feature
    3. Execute that test plan
    4. Report your findings
    """
    
    # Create and run AI agent
    agent = BrowserAgent(
        tasks=[exploration_prompt],
        browser_type=self.config.browser_type,
        headless=False  # Set to True for headless execution
    )
    
    result = await agent.run()
```

## Key Components

### AITestConfig

This class centralizes configuration for AI browser tests:

```python
class AITestConfig:
    """Configuration for AI-powered browser testing."""
    
    # Test server URL
    server_url: str = "http://localhost:8000" 
    
    # Browser configuration
    headless: bool = False
    slow_mo: int = 100  # ms
    browser_type: str = "chromium"
    
    # Viewport configurations for responsive testing
    viewports: List[Dict[str, int]] = [...]
    
    # Directories for screenshots and reports
    screenshots_dir: str = "test_screenshots/ai_testing"
    report_dir: str = "test_reports/ai_browser"
```

### TestReport

This class provides structured test reporting:

```python
class TestReport:
    """Class for structured test reporting."""
    
    def __init__(self, test_name: str, config: AITestConfig):
        self.test_name = test_name
        self.config = config
        self.start_time = datetime.now()
        self.steps = []
        self.screenshots = []
        self.issues = []
    
    # Methods to add test steps, screenshots, and issues
    def add_step(self, description: str, status: str = "pass", details: Dict = None): ...
    def add_screenshot(self, path: str, description: str): ...
    def add_issue(self, issue_type: str, description: str, details: Dict = None): ...
    
    # Save report to JSON file
    def save(self): ...
```

### AIBrowserTesting

This base class provides the core AI testing functionality:

```python
class AIBrowserTesting:
    """Base class for AI-powered browser testing."""
    
    # Generate test steps from feature description
    @staticmethod
    async def generate_test_scenario(feature_description: str, config: AITestConfig) -> List[str]: ...
    
    # Run test steps and generate report
    @staticmethod
    async def run_test_with_report(steps: List[str], config: AITestConfig, test_name: str) -> TestReport: ...
    
    # Visual regression testing (placeholder)
    @staticmethod
    async def run_visual_regression_test(baseline_dir: str, current_screenshots: List[str]): ...
    
    # Accessibility testing (placeholder)
    @staticmethod
    async def run_accessibility_test(page: Page, config: AITestConfig): ...
```

## Example Test Scenarios

The framework includes example test scenarios for:

1. **User Authentication**:
   - Login with valid/invalid credentials
   - Password reset flow
   - Account settings updates

2. **Todo Management**:
   - Create, read, update, delete Todo items
   - Mark Todo items as complete
   - Filter Todo items by status/priority

3. **Team Management**:
   - Create and manage teams
   - Add/remove team members
   - Team settings updates

## Best Practices

### 1. Test Structure

- **Organize by feature**: Keep tests organized by feature or workflow
- **Use test configuration**: Centralize test settings in `AITestConfig`
- **Generate unique test data**: Create unique users/data for each test run
- **Clean up test data**: Remove test data in test teardown

### 2. Test Execution

- **Run with live server**: Tests require a running Django server
- **Headless vs. visible mode**: Use `headless=False` for debugging
- **Use slow_mo**: Slow down browser actions during development
- **Capture screenshots**: Take screenshots at key points for verification

### 3. AI Test Generation

- **Be specific in prompts**: Give clear, detailed feature descriptions
- **Provide context**: Include information about UI structure and navigation
- **Review generated tests**: Always review AI-generated tests before using in CI
- **Use realistic data examples**: Provide examples of realistic test data

### 4. Test Reports

- **Structure reports** by test, step, screenshot, and issue
- **Include timing**: Track start/end times for performance analysis
- **Save reports**: Store reports for historical comparison
- **Include screenshots**: Link screenshots to specific test steps

## Example: Full Workflow Test

Here's an example of a complete test workflow using the AI browser testing framework:

```python
import pytest
from apps.public.tests.test_ai_browser_testing import TestAIAutomatedTesting

@pytest.mark.asyncio
async def test_todo_full_workflow():
    """Test the complete Todo workflow from creation to deletion."""
    tester = TestAIAutomatedTesting()
    
    # Generate test steps
    feature_description = """
    Todo management workflow:
    1. User logs in
    2. Creates a new Todo item with title, description, and priority
    3. Verifies it appears in the list
    4. Edits the Todo to change its description
    5. Marks it as complete
    6. Filters the list to show only completed items
    7. Deletes the Todo
    8. Verifies it's removed from the list
    """
    
    steps = await tester.generate_test_scenario(feature_description, tester.config)
    
    # Create test user
    user, username, password = await tester.create_test_user()
    
    # Run the test
    report = await tester.run_test_with_report(
        steps, 
        tester.config,
        "todo_full_workflow",
        (user, username, password)
    )
    
    # Verify test passed
    assert report.status == "pass"
    
    # Check database state
    from apps.common.models import TodoItem
    assert TodoItem.objects.filter(created_by=user).count() == 0
```

## Extending the Framework

You can extend the AI browser testing framework by:

1. **Adding specialized test classes** for specific features
2. **Implementing visual regression testing** using image comparison libraries
3. **Adding accessibility testing** with tools like axe-core
4. **Integrating with CI/CD** for automated testing
5. **Creating custom test generators** for specific test patterns

## Example Configuration Options

```python
# Configuration for comprehensive testing
comprehensive_config = AITestConfig()
comprehensive_config.headless = True
comprehensive_config.slow_mo = 0
comprehensive_config.viewports = [
    {"width": 1920, "height": 1080},  # Large Desktop
    {"width": 1280, "height": 800},   # Desktop
    {"width": 1024, "height": 768},   # Small Desktop
    {"width": 768, "height": 1024},   # Tablet Portrait
    {"width": 414, "height": 896},    # iPhone XR
    {"width": 375, "height": 667},    # iPhone 8
    {"width": 360, "height": 740},    # Samsung Galaxy S9
]
comprehensive_config.test_scope = "comprehensive"
comprehensive_config.max_test_steps = 30
comprehensive_config.run_accessibility_tests = True
```

## Troubleshooting

If you encounter issues with the AI browser testing framework:

1. **Check server status**: Make sure Django server is running at the configured URL
2. **Verify packages**: Ensure browser-use, playwright, and pytest-asyncio are installed
3. **Run in visible mode**: Set `headless=False` to see browser actions
4. **Add step delays**: Increase `slow_mo` for troubleshooting timing issues
5. **Check logs**: Look at console output for error messages
6. **Inspect screenshots**: Review screenshots to see what happened during test execution
7. **Check report**: Review the generated test report for detailed information

For detailed information on browser-use, see [the browser-use documentation](https://github.com/browser-use/browser-use).
