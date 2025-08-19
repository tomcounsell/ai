# Browser Testing Guide

This guide explains how to write and run browser-based tests for the Django Project Template. These tests provide end-to-end validation of the application's functionality in a real browser environment.

## Overview

The Django Project Template includes a comprehensive browser testing framework that leverages:

1. **pytest** and **pytest-django** for test orchestration
2. **Playwright** for browser automation
3. **browser-use** for AI-powered testing capabilities

Browser tests are an important part of the testing strategy, providing:
- Validation of real user workflows
- Visual verification of UI components
- Testing of JavaScript and HTMX interactions
- End-to-end validation across multiple layers

## Test Organization

Browser tests are organized into several categories:

1. **Standard E2E Tests**: Basic browser tests using Playwright
2. **HTMX Interaction Tests**: Tests focusing on HTMX dynamic updates
3. **Visual Regression Tests**: Tests capturing screenshots for visual comparison
4. **Responsive Layout Tests**: Tests verifying layout across different viewport sizes
5. **AI-Powered Tests**: Tests using browser-use for automated test generation and execution

## Setting Up for Browser Testing

Before running browser tests, you need to:

1. Install dependencies:
   ```bash
   source venv/bin/activate
   uv add --dev browser-use playwright pytest-asyncio
   playwright install
   ```

2. Start the Django development server:
   ```bash
   python manage.py runserver
   ```

3. Open a second terminal for running tests:
   ```bash
   DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_account_browser.py -v
   ```

## Running Browser Tests

Browser tests require a running Django server and will be skipped if the server is not detected.

### Standard Test Categories

```bash
# Run account settings browser tests
DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_account_browser.py -v

# Run todo item browser tests
DJANGO_SETTINGS_MODULE=settings pytest apps/staff/tests/test_todo_e2e.py -v

# Run admin interface browser tests
DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_admin_e2e.py -v
```

### AI-Powered Browser Tests

```bash
# Run AI-powered browser tests
DJANGO_SETTINGS_MODULE=settings pytest apps/public/tests/test_ai_browser_testing.py -v
```

## Test Results and Screenshots

Browser tests generate the following artifacts:

1. **Screenshots**: Stored in `test_screenshots/` directory, organized by feature
2. **Test Reports**: Stored in `test_reports/browser/` directory (AI tests only)

These artifacts are useful for debugging and visual verification.

## Writing New Browser Tests

### Basic Browser Test Structure

```python
@browser_test
@pytest.mark.asyncio
class TestFeatureBrowser:
    """Browser-based tests for feature functionality."""
    
    @pytest.fixture
    def test_user(self):
        """Fixture to create a test user."""
        # Create and return test user
        
    @pytest.fixture
    async def page(self, browser):
        """Fixture to create a browser page."""
        # Setup page and yield it
        
    async def test_feature_workflow(self, page, test_user):
        """Test feature workflow."""
        # Implement test steps
```

### Using E2ETestBase

For tests that follow the standard pattern, inherit from `E2ETestBase`:

```python
from .test_e2e_patterns import E2ETestBase, browser_test, asyncio_mark

@browser_test
@asyncio_mark
class TestFeature(E2ETestBase):
    """Tests for feature using E2ETestBase."""
    
    async def test_feature(self, browser_page):
        """Test feature functionality."""
        page = browser_page
        # Test implementation
```

### AI-Powered Test Generation

For AI-powered tests, use the `AIBrowserTesting` class:

```python
from .test_ai_browser_testing import AIBrowserTesting, TestReport

@browser_test
@asyncio_mark
class TestFeatureAI(AIBrowserTesting):
    """AI-powered tests for feature."""
    
    async def test_feature_with_ai(self):
        """Test feature using AI test generation."""
        # Generate test steps
        steps = await self.generate_test_scenario(
            "Feature description",
            self.config
        )
        
        # Execute the test
        report = await self.run_test_with_report(
            steps,
            self.config,
            "test_feature"
        )
```

## Best Practices

1. **Test Independence**: Each test should be completely independent from others
2. **Explicit Waits**: Use explicit waits for elements and network requests
3. **Unique Test Data**: Use unique identifiers for test data
4. **Screenshots**: Take screenshots at key points for debugging
5. **Clean Setup/Teardown**: Ensure proper test setup and cleanup
6. **Descriptive Assertions**: Use descriptive assertion messages
7. **Follow Patterns**: Reuse existing patterns for consistency
8. **Use Configuration**: Use the central test configuration in `e2e_test_config.py`
9. **Headless Mode**: Run tests in headless mode in CI, visible mode for debugging

## Advanced Topics

### Testing HTMX Interactions

For HTMX-specific testing, use the following pattern:

```python
async def test_htmx_component(self, browser_page):
    page = browser_page
    
    # Find HTMX trigger
    trigger = page.locator('[hx-get="/api/data"]')
    
    # Click to trigger the request
    await trigger.click()
    
    # Wait for HTMX request to complete
    await self.wait_for_htmx_request(page)
    
    # Verify the target was updated
    target = page.locator('#target')
    assert await self.assert_element_contains_text(target, 'Expected response')
```

### Testing Responsive Layouts

To test responsive layouts:

```python
async def test_responsive_layout(self, browser_context):
    # Test each viewport size
    for name, viewport in VIEWPORTS.items():
        # Create a page with this viewport
        context = await browser_context.new_context(viewport=viewport)
        page = await context.new_page()
        
        # Navigate to page
        await page.goto(f"{SERVER_URL}/page/to/test")
        
        # Take screenshot for this viewport
        await self.take_screenshot(page, f"page_{name}.png")
        
        # Check viewport-specific elements
        if viewport["width"] < 768:  # Mobile
            assert await page.locator('.mobile-menu').is_visible()
        else:  # Desktop
            assert await page.locator('.desktop-menu').is_visible()
```

### Visual Regression Testing

For visual regression testing:

```python
async def test_visual_appearance(self, browser_page):
    page = browser_page
    
    # Navigate to page
    await page.goto(f"{SERVER_URL}/page")
    
    # Wait for all content to load
    await page.wait_for_load_state('networkidle')
    
    # Take screenshot for visual verification
    await self.take_screenshot(page, "page_visual.png")
    
    # For automated comparison, use AIBrowserTesting.run_visual_regression_test
```

## Troubleshooting

If you encounter issues with browser tests:

1. **Server Running**: Ensure Django server is running at the expected URL
2. **Dependencies**: Verify all dependencies are installed
3. **Headless Mode**: Set `headless=False` to see the browser in action
4. **Slow Down**: Increase `slow_mo` value to slow down browser actions
5. **Screenshots**: Check screenshots for visual debugging
6. **Element Selectors**: Verify element selectors are correct and unique
7. **Wait Times**: Adjust wait times for slower operations
8. **Browser Compatibility**: Try different browser types (chromium, firefox, webkit)

For more detailed information on AI-powered browser testing, see [AI Browser Testing](AI_BROWSER_TESTING.md).
