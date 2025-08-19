# Test Conventions

This document outlines the testing approach, conventions, and best practices for the Django Project Template. Following these guidelines will ensure consistency across the codebase and maintain high test coverage.

> **New Feature**: The project now supports end-to-end browser testing using browser-use/Playwright. For detailed information about this testing capability, see [E2E_TESTING.md](E2E_TESTING.md). The current document has been updated to include guidance on when to use different testing approaches.

## Core Testing Principles

1. **Test-Driven Development (TDD)**
   - Write tests BEFORE implementing features
   - Follow the Red-Green-Refactor cycle:
     1. Write a failing test (Red)
     2. Implement just enough code to pass the test (Green)
     3. Refactor while maintaining passing tests

2. **Coverage Goals**
   - Aim for 100% test coverage for:
     - Models
     - Behavior mixins
     - Utilities
   - Strive for 90%+ coverage for views and APIs
   - Use `.coveragerc` to exclude boilerplate code from coverage metrics

3. **Database Usage**
   - Always use PostgreSQL for tests, never SQLite
   - Tests should use transaction rollbacks for isolation and speed
   - Test fixtures should be minimal but sufficient

## Test Organization

### Directory Structure

```
apps/
  app_name/
    tests/
      __init__.py
      factories.py           # Model factories for this app
      test_forms/            # Form tests
        test_form_name.py
      test_models/           # Model tests
        test_model_name.py
      test_views/            # View tests
        test_view_name.py
      test_serializers/      # API serializer tests
        test_serializer.py
  common/
    behaviors/
      tests/
        test_behaviors.py    # Standalone behavior tests (Python 3.12 compatible)
    tests/
      factories.py           # Shared model factories
      test_behaviors/        # Django-based behavior mixin tests
        test_behavior_name.py
```

### Test Class Naming

- Test classes should be named with format: `{Subject}TestCase`
- Example: `UserModelTestCase`, `LoginViewTestCase`, `APIEndpointTestCase`

### Test Method Naming

- Test methods should follow the pattern: `test_{condition_being_tested}`
- Include the expected behavior and condition being tested
- Examples:
  - `test_user_creation_with_valid_data_succeeds`
  - `test_login_with_invalid_credentials_returns_error`
  - `test_api_returns_404_for_nonexistent_resource`

## Test Types and Their Implementation

### Choosing the Right Test Type

This project supports several types of tests, each appropriate for different scenarios:

1. **Model Tests**: For testing data models, properties, methods, and validations
2. **View Tests**: For testing view logic, template rendering, and basic user interactions
3. **API Tests**: For testing REST API endpoints, serialization, and authentication
4. **Behavior Tests**: For testing reusable behavior mixins
5. **End-to-End Tests**: For testing complete user workflows through the browser

When to use each approach:

| Test Type | When to Use | When Not to Use |
|-----------|-------------|-----------------|
| Model Tests | Testing properties, methods, validations, signals | Testing user interactions |
| View Tests | Testing view logic, context data, template rendering | Testing browser behavior, HTMX interactions |
| API Tests | Testing API endpoints, serializers, authentication | Testing user interfaces |
| Behavior Tests | Testing reusable mixins, inheritance | Testing application-specific logic |
| End-to-End Tests | Testing complete user flows, HTMX interactions, forms | Unit testing, performance-critical paths |

### Model Tests

Model tests verify that your data models work correctly, including:

```python
# Example model test
from django.test import TestCase
from apps.common.models import Address

class AddressModelTestCase(TestCase):
    def setUp(self):
        self.address = Address.objects.create(
            line_1="123 Main St",
            city="New York",
            region="NY",
            postal_code="10001"
        )
    
    def test_string_representation(self):
        expected = "123 Main St New York, NY"
        self.assertEqual(str(self.address), expected)
        
    def test_property_returns_expected_value(self):
        self.assertEqual(self.address.formatted_address, "123 Main St, New York, NY 10001")
```

### Behavior Mixin Tests

There are two types of behavior mixin tests:

1. **Django-integrated tests** - Test mixins with Django models
2. **Standalone tests** - Test mixins using mocks (Python 3.12 compatible)

#### Django-Integrated Tests

```python
# Example behavior test with Django
from django.test import TestCase
from apps.common.behaviors.annotatable import Annotatable
from apps.common.models.note import Note

class AnnotatableModel(Annotatable):
    class Meta:
        app_label = 'test_app'

class AnnotatableTest(TestCase):
    def setUp(self):
        self.obj = AnnotatableModel.objects.create()
        self.note = Note.objects.create(text="Test note")
    
    def test_has_notes_property_returns_true_when_notes_exist(self):
        self.obj.notes.add(self.note)
        self.assertTrue(self.obj.has_notes)
```

#### Standalone Tests

```python
# Example standalone behavior test
import unittest
from unittest import mock
from datetime import datetime, timedelta

# Import behavior to test
from apps.common.behaviors.timestampable import Timestampable

class MockModel(Timestampable):
    pass

class TimestampableTest(unittest.TestCase):
    def test_was_created_today_returns_true_for_today(self):
        model = MockModel()
        model.created_at = datetime.now()
        self.assertTrue(model.was_created_today)
        
    def test_was_created_today_returns_false_for_yesterday(self):
        model = MockModel()
        model.created_at = datetime.now() - timedelta(days=1)
        self.assertFalse(model.was_created_today)

if __name__ == "__main__":
    unittest.main()
```

### View Tests

View tests verify that views render correctly and process form submissions appropriately:

```python
# Example view test
from django.test import TestCase
from django.urls import reverse
from apps.common.tests.factories import UserFactory

class HomeViewTestCase(TestCase):
    def setUp(self):
        self.user = UserFactory()
        self.client.login(username=self.user.username, password='password')
        self.url = reverse('home')
    
    def test_get_authenticated_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/home.html')
        
    def test_context_contains_expected_data(self):
        response = self.client.get(self.url)
        self.assertIn('user', response.context)
        self.assertEqual(response.context['user'], self.user)
```

### API Tests

API tests verify that your API endpoints work correctly:

```python
# Example API test
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from apps.common.tests.factories import UserFactory

class UserAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = UserFactory()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('api:user-detail', kwargs={'pk': self.user.pk})
    
    def test_get_returns_expected_data(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.user.username)
```

### Admin Tests

Admin tests verify that Django admin customizations work correctly:

```python
# Example admin test
import warnings
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

# Filter out timezone warnings during tests
warnings.filterwarnings(
    "ignore", 
    message="DateTimeField .* received a naive datetime", 
    category=RuntimeWarning
)

@override_settings(ALLOWED_HOSTS=['testserver'])
class AdminTestCase(TestCase):
    def setUp(self):
        # Use get_or_create to avoid duplicate user errors
        self.user, created = get_user_model().objects.get_or_create(
            username='admin_test',
            defaults={
                'email': 'admin_test@example.com',
                'is_superuser': True,
                'is_staff': True,
                'date_joined': timezone.now(),  # Use timezone-aware datetime
            }
        )
        
        if created:
            self.user.set_password('password123')
            self.user.save()
        
        self.client = Client()
        self.client.login(username='admin_test', password='password123')
    
    def test_admin_index(self):
        """Test the admin index page loads successfully."""
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Database')
    
    def test_custom_dashboard(self):
        """Test the custom admin dashboard."""
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        
        # Check for expected admin interface elements
        self.assertContains(response, 'ProjectName Database')
        self.assertContains(response, 'output.css')
```

## Test Factories

Use [factory_boy](https://factoryboy.readthedocs.io/) to create test objects:

```python
# Example factory
import factory
from django.contrib.auth import get_user_model
from apps.common.models import Team

User = get_user_model()

class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
    
    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    
    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        password = extracted or "password"
        self.set_password(password)
        if create:
            self.save()

class TeamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Team
    
    name = factory.Sequence(lambda n: f"Team {n}")
    created_by = factory.SubFactory(UserFactory)
```

## Running Tests

### Using the Unified Test Runner

The project includes a unified test runner that can run all types of tests:

```bash
# Run all tests
./test_runner.py

# Run with coverage
./test_runner.py --coverage

# Run specific test types
./test_runner.py --type unit
./test_runner.py --type e2e
./test_runner.py --type htmx
./test_runner.py --type responsive

# Run with HTML coverage report
./test_runner.py --coverage --html-report

# Run with XML coverage report (for CI)
./test_runner.py --coverage --xml-report

# Run specific tests by path
./test_runner.py --path apps/common/tests/test_models/

# Run browser tests in non-headless mode
./test_runner.py --type e2e --no-headless
```

### Basic Pytest Commands

If you prefer direct pytest commands:

```bash
# Run all tests
DJANGO_SETTINGS_MODULE=settings pytest

# Run a specific test file
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_address.py

# Run a specific test class
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_address.py::AddressModelTestCase

# Run a specific test method
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_address.py::AddressModelTestCase::test_string_representation

# Run standalone behavior tests (Python 3.12 compatible)
python apps/common/behaviors/tests/test_behaviors.py

# Run tests with verbose output
DJANGO_SETTINGS_MODULE=settings pytest -v

# Run tests with detailed error output
DJANGO_SETTINGS_MODULE=settings pytest -vxs
```

### Targeted Test Execution

```bash
# Run all tests for a specific app
DJANGO_SETTINGS_MODULE=settings pytest apps/common/

# Run all model tests
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/

# Run tests matching a keyword
DJANGO_SETTINGS_MODULE=settings pytest -k "user"

# Skip specific test categories
DJANGO_SETTINGS_MODULE=settings pytest -k "not browser and not integration"

# Run tests in parallel (speeds up execution)
DJANGO_SETTINGS_MODULE=settings pytest -xvs -n auto
```

### Coverage Commands

```bash
# Run tests with coverage report
DJANGO_SETTINGS_MODULE=settings pytest --cov=apps

# Generate HTML coverage report
DJANGO_SETTINGS_MODULE=settings pytest --cov=apps --cov-report=html:reports/coverage_html

# Generate XML coverage report (for CI)
DJANGO_SETTINGS_MODULE=settings pytest --cov=apps --cov-report=xml:reports/coverage.xml

# Generate coverage report for specific module
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_user*.py --cov=apps.common.models.user --cov-report=term-missing
```

### Debugging Test Failures

```bash
# Show detailed output for failing tests
DJANGO_SETTINGS_MODULE=settings pytest -vxs

# Stop on first failing test
DJANGO_SETTINGS_MODULE=settings pytest -xvs --exitfirst

# Only run previously failed tests
DJANGO_SETTINGS_MODULE=settings pytest --failed-first

# Enable debug logging during tests
DJANGO_SETTINGS_MODULE=settings pytest --log-cli-level=DEBUG
```

## Mocking

Use the `unittest.mock` library for mocking external dependencies:

```python
from unittest import mock
from django.test import TestCase
from apps.integration.services import EmailService

class EmailServiceTestCase(TestCase):
    @mock.patch('apps.integration.services.requests.post')
    def test_send_email_makes_expected_api_call(self, mock_post):
        # Setup mock
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'id': '123'}
        
        # Call the method
        service = EmailService()
        result = service.send_email('user@example.com', 'Subject', 'Body')
        
        # Assertions
        self.assertTrue(result)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['to'], 'user@example.com')
```

### End-to-End Tests

End-to-end tests simulate real user interactions with the application through an actual browser. They're particularly valuable for testing HTMX interactions and complex user flows.

```python
# Example browser-based test using browser-use and Playwright
@browser_test
@asyncio_mark
class AccountSettingsBrowserTestCase(TestCase):
    """Tests for account settings using browser automation."""
    
    async def test_update_profile_browser(self, page):
        """Test updating profile information using a real browser."""
        # Check if server is running
        if not await self.is_server_running():
            pytest.skip("Django server not running")
        
        # Login
        login_success = await self.login_user(page)
        assert login_success, "Login failed"
        
        # Navigate to settings page and interact with it
        await page.goto(f"{self.server_url}/account/settings")
        await page.fill('input[name="first_name"]', 'Updated')
        await page.fill('input[name="last_name"]', 'FromBrowser')
        
        # Take screenshot for debugging
        await self.take_screenshot(page, "settings_filled.png")
        
        # Submit the form
        await page.click('button[type="submit"]')
        
        # Verify success
        page_content = await page.content()
        assert "success" in page_content.lower()
```

For detailed conventions on writing end-to-end tests, see [E2E_TESTING.md](E2E_TESTING.md).

## Common Pitfalls and Solutions

### General Issues

1. **Database Leakage**: Ensure tests clean up after themselves to avoid test interdependence
2. **Slow Tests**: Minimize database calls; use setUpTestData for class-level fixtures
3. **Brittle Tests**: Don't test implementation details, test outcomes and behavior
4. **Insufficient Coverage**: Ensure all edge cases are covered
5. **Overlooking Permissions**: For views and API endpoints, test different permission scenarios
6. **Timezone Issues**: Be explicit about datetime comparisons and aware of timezone settings
   - Use timezone.now() instead of naive datetime objects
   - Consider using filter_warnings in pyproject.toml for persistent warnings
7. **Browser Dependency**: For end-to-end tests, remember they depend on a running server
8. **URL Name Changes**: Using URL names with reverse() instead of hardcoded paths prevents tests from breaking when URLs change
9. **Over-reliance on E2E Tests**: Use them for integration points, not for testing every bit of functionality

### Common Test Failures and Solutions

#### 1. Database Integrity Errors (UniqueViolation)

**Problem**: Tests fail with `IntegrityError: duplicate key value violates unique constraint`

**Solution**: Always use unique identifiers in test data, especially for User objects:

```python
import uuid

def setUp(self):
    """Set up test data with unique identifiers."""
    # Generate a unique username to avoid UniqueViolation errors
    unique_username = f"testuser_{uuid.uuid4().hex[:8]}"
    self.user = User.objects.create_user(
        username=unique_username,
        email=f"{unique_username}@example.com",
        password="password123",
    )
```

#### 2. Missing Model Fields

**Problem**: Tests fail with `AttributeError: 'X' object has no attribute 'Y'`

**Solution**: Ensure models maintain database compatibility even when refactoring:

```python
# Maintain database compatibility with explicit comments
class User(AbstractUser):
    # Fields kept for database compatibility - use with caution
    # To be removed with proper migration
    legacy_field = models.CharField(max_length=255, blank=True, default="")
```

#### 3. Incorrect Mock Setup

**Problem**: Mocked functionality doesn't behave as expected

**Solution**: Use proper mock setup with side_effect or return_value:

```python
from unittest import mock

# Use PropertyMock for properties
with mock.patch.object(User, 'property_name', 
                      new_callable=mock.PropertyMock) as mock_prop:
    mock_prop.return_value = expected_value
    
    # For exceptions
    mock_prop.side_effect = Exception("Test exception")
```

#### 4. Broken Test Isolation

**Problem**: Tests pass when run individually but fail when run as part of a suite

**Solution**: 
- Ensure setUp/tearDown properly cleanup
- Use TestCase.setUpTestData for immutable fixtures
- Use UUIDs for unique identifiers
- Reset mocks between tests

```python
@classmethod
def setUpTestData(cls):
    """Set up data shared across all test methods."""
    # Create shared test data that won't be modified by tests
    cls.shared_data = {"constants": "value"}

def setUp(self):
    """Set up fresh data for each test."""
    self.unique_id = uuid.uuid4().hex
    self.patcher = mock.patch('path.to.dependency')
    self.mock_dependency = self.patcher.start()
    
def tearDown(self):
    """Clean up after each test."""
    self.patcher.stop()
    # Clear any created test data if needed
```

#### 5. Model Property Test Failures

**Problem**: Model property tests fail even though the property looks correct

**Solution**: Check default values and database constraints, ensure properties handle edge cases:

```python
@property
def full_name(self):
    """Returns the full name, gracefully handling empty fields."""
    if not self.first_name and not self.last_name:
        return ""
    return f"{self.first_name or ''} {self.last_name or ''}".strip()
```

## Best Practices

1. **Use Descriptive Assertions**: Prefer `assertEqual` over `assertTrue(a == b)` for better error messages
2. **Test Invalid Cases**: Test what should fail, not just what should succeed
3. **Keep Tests Independent**: Tests should not depend on each other
4. **Use Fixtures Wisely**: Keep fixtures minimal and focused
5. **Test Edge Cases**: Consider boundary values and special cases
6. **Follow AAA Pattern**: Arrange, Act, Assert - keep these sections clear and separate
7. **Parameterize Similar Tests**: Use `pytest.mark.parametrize` for testing multiple similar cases
8. **Layer Your Tests**: Use a combination of fast unit tests and more comprehensive E2E tests
9. **Duplicate Critical Tests**: Test critical paths with both Django TestCase and browser-use for maximum confidence
10. **Test HTMX Interactions**: Use browser-use for testing HTMX behavior that can't be tested with standard Django tests

## Choosing Between Browser and Django TestCase

| Feature | Django TestCase | Browser-Use/Playwright |
|---------|----------------|------------------------|
| Speed | Fast | Slow |
| Setup Complexity | Simple | Complex (requires running server) |
| Dependencies | Minimal | Multiple (browser-use, playwright, etc.) |
| HTMX Testing | Limited | Comprehensive |
| Visual Verification | None | Screenshots |
| Form Interactions | Basic | Advanced (clicks, typing, etc.) |
| JS Dependency | Cannot test JS | Can test JS-dependent features |
| Reliability | Highly reliable | More prone to timing/environment issues |
| Recommended For | Unit testing, basic form submission | Complex user flows, HTMX, visual testing |

By following these testing conventions, you'll ensure maintainable, reliable tests that provide confidence in the codebase.
