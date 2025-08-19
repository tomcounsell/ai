# End-to-End Testing Guide

This document describes how to perform end-to-end (E2E) testing for the Django Project Template. E2E tests simulate real user interactions with the application through a browser.

## Overview

The Django Project Template supports E2E testing with two approaches:

1. **Standard Django Tests**: Using Django's TestCase with the test client
2. **Browser-Based Tests**: Using Selenium/Playwright to automate browser interactions

Both approaches are valuable and complement each other. The standard tests provide fast, reliable validation while browser tests offer more comprehensive interaction testing.

## Test Organization

The project has a structured approach to organizing tests:

1. **Test Categories**:
   - **Unit Tests**: Test individual functions, classes, or models
   - **Integration Tests**: Test interactions between components
   - **E2E Tests**: Test complete user workflows
   - **Visual Tests**: Test visual appearance of components
   - **API Tests**: Test API endpoints and responses
   - **HTMX Tests**: Test HTMX interactions and OOB swaps
   - **Responsive Tests**: Test responsive design across device sizes

2. **File Naming Conventions**:
   - E2E tests: `test_e2e_*.py` or `test_*_browser.py`
   - Visual tests: `test_visual_*.py` or `test_screenshot_*.py`
   - API tests: `test_api_*.py`
   - Integration tests: `test_integration_*.py`
   - HTMX tests: `test_htmx_*.py` or `test_*_oob.py`
   - Responsive tests: `test_responsive_*.py` 
   - Model tests: `test_models/test_*.py`
   - View tests: `test_views/test_*.py`

## Test Management Tools

The project includes several tools to help manage and run tests:

### Test Manager

The Test Manager categorizes and runs tests by type:

```bash
# List tests by category
python tools/testing/test_manager.py list --category e2e

# Run E2E tests
python tools/testing/test_manager.py run --category e2e

# Generate HTML report for E2E tests
python tools/testing/test_manager.py report --category e2e
```

### Browser Test Runner

The Browser Test Runner handles starting a Django server and running browser-based tests:

```bash
# Run E2E tests with browser automation
python tools/testing/browser_test_runner.py apps/public/tests/test_e2e_*.py

# Run with visible browser
python tools/testing/browser_test_runner.py --no-headless apps/public/tests/test_visual_*.py

# Run with Firefox instead of Chrome
python tools/testing/browser_test_runner.py --browser firefox apps/public/tests/test_e2e_*.py
```

### Unified Test Runner

The project provides a unified test runner script (`test_runner.py`) that supports all test types:

```bash
# Run all tests
./test_runner.py

# Run only E2E tests
./test_runner.py --type e2e

# Run HTMX tests
./test_runner.py --type htmx

# Run responsive design tests
./test_runner.py --type responsive

# Run with visible browser (non-headless mode)
./test_runner.py --type e2e --no-headless

# Generate coverage report
./test_runner.py --coverage

# Generate HTML coverage report
./test_runner.py --coverage --html-report
```

The test runner automatically ensures Django settings are properly configured and creates necessary directories for reports.

## Writing E2E Tests

### Standard Django Tests

```python
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

class TodoWorkflowTestCase(TestCase):
    """Test the todo workflow using Django's test client."""
    
    def setUp(self):
        # Create test user
        self.user = get_user_model().objects.create_user(
            username="testuser",
            password="testpass123"
        )
        self.client.login(username="testuser", password="testpass123")
    
    def test_create_todo_workflow(self):
        """Test creating a new todo item."""
        # Get initial todo count
        list_url = reverse("todos:list")
        response = self.client.get(list_url)
        initial_count = response.context["todos"].count()
        
        # Create new todo
        create_url = reverse("todos:create")
        response = self.client.post(
            create_url,
            {"title": "Test Todo"},
            follow=True
        )
        
        # Verify redirect and new todo creation
        self.assertRedirects(response, list_url)
        self.assertEqual(response.context["todos"].count(), initial_count + 1)
```

### Browser-Based Tests

```python
import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

@pytest.mark.e2e
def test_todo_workflow(driver, live_server):
    """Test the todo workflow using browser automation."""
    # Navigate to the todo list page
    driver.get(f"{live_server.url}/todos/")
    
    # Click the "Add Todo" button
    add_button = driver.find_element(By.LINK_TEXT, "Add Todo")
    add_button.click()
    
    # Wait for the form to load
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "id_title"))
    )
    
    # Fill in the form
    title_input = driver.find_element(By.ID, "id_title")
    title_input.send_keys("Browser Test Todo")
    
    # Submit the form
    submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    submit_button.click()
    
    # Verify the new todo was created
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".todo-item"))
    )
    
    # Check if our new todo is in the list
    todo_items = driver.find_elements(By.CSS_SELECTOR, ".todo-item")
    todo_titles = [item.text for item in todo_items]
    assert "Browser Test Todo" in " ".join(todo_titles)
```

## Visual Testing

Visual testing verifies the appearance of components:

```python
@pytest.mark.visual
def test_todo_appearance(driver, live_server):
    """Test the visual appearance of todo items."""
    # Navigate to the todo list page
    driver.get(f"{live_server.url}/todos/")
    
    # Wait for the page to load
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".todo-item"))
    )
    
    # Take a screenshot for visual inspection
    driver.save_screenshot("reports/screenshots/todo_list.png")
    
    # Check styling
    todo_item = driver.find_element(By.CSS_SELECTOR, ".todo-item")
    item_class = todo_item.get_attribute("class")
    
    # Todo items should have proper styling
    assert "p-" in item_class or "py-" in item_class, "Todo item should have padding"
```

## Test Fixtures

The project includes fixtures for typical testing needs:

### User Authentication

```python
@pytest.fixture
def authenticated_user(db):
    """Create an authenticated user for testing."""
    user = get_user_model().objects.create_user(
        username="testuser",
        email="test@example.com",
        password="password123"
    )
    return user

@pytest.fixture
def authenticated_client(client, authenticated_user):
    """Return a client with an authenticated user."""
    client.login(username="testuser", password="password123")
    return client
```

### Data Setup

```python
@pytest.fixture
def sample_todos(authenticated_user):
    """Create sample todo items for testing."""
    todos = [
        TodoItem.objects.create(
            title=f"Todo {i}",
            user=authenticated_user,
            completed=i % 2 == 0
        )
        for i in range(5)
    ]
    return todos
```

### Browser Session

```python
@pytest.fixture
def authenticated_browser(driver, live_server, authenticated_user):
    """Create an authenticated browser session."""
    # Login via the login page
    driver.get(f"{live_server.url}/accounts/login/")
    
    # Fill login form
    driver.find_element(By.NAME, "username").send_keys("testuser")
    driver.find_element(By.NAME, "password").send_keys("password123")
    
    # Submit form
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    
    # Wait for redirect after login
    WebDriverWait(driver, 10).until(
        lambda d: "/accounts/login/" not in d.current_url
    )
    
    return driver
```

## Best Practices

1. **Run Both Test Types**: Use both standard Django tests and browser-based tests for comprehensive coverage
2. **Keep Tests Independent**: Each test should set up its own data and not depend on other tests
3. **Use Explicit Waits**: Avoid time.sleep() in favor of explicit waits for elements or conditions
4. **Take Screenshots**: Capture screenshots at key points for debugging and documentation
5. **Test Real User Flows**: Focus on testing actual user journeys, not implementation details
6. **Use Page Objects**: For complex applications, use the Page Object pattern to organize test code
7. **Don't Overdo Browser Tests**: Use them for workflows and interactions that can't be tested with the Django test client
8. **CI Integration**: Configure your CI pipeline to run E2E tests and capture results

## Related Documentation

- [TEST_CONVENTIONS.md](TEST_CONVENTIONS.md): General test conventions
- [BROWSER_TESTING.md](BROWSER_TESTING.md): Advanced browser testing topics
