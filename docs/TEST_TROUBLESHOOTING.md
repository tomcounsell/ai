# Test Troubleshooting Guide

This guide helps resolve common test failures in the Django Project Template. It complements the [Test Conventions](TEST_CONVENTIONS.md) by providing specific solutions to frequently encountered problems.

## Database-Related Issues

### 1. UniqueViolation Errors

**Error Message**: 
```
django.db.utils.IntegrityError: duplicate key value violates unique constraint "common_user_username_key"
DETAIL: Key (username)=(testuser) already exists.
```

**Cause**: Multiple tests creating models with the same unique field values.

**Solution**: 
1. Use UUID-based unique identifiers in tests:

```python
import uuid

class TestCase(TestCase):
    def setUp(self):
        unique_id = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            username=f"testuser_{unique_id}",
            email=f"test_{unique_id}@example.com",
        )
```

2. Apply this pattern to all test files creating User objects:
   - Note model tests
   - Payment model tests 
   - Subscription model tests
   - Address model tests

### 2. Field DoesNotExist Error

**Error Message**:
```
django.core.exceptions.FieldDoesNotExist: Model has no field 'field_name'
```

**Cause**: Database schema includes fields that have been removed from the model.

**Solution**:
1. Keep field definitions in the model, but mark them as deprecated:

```python
class Model(models.Model):
    # Database compatibility field - DO NOT USE
    # To be removed with migration XXXX
    legacy_field = models.CharField(max_length=255, blank=True, default="")
```

2. Create a migration plan document in the migrations directory:

```markdown
# Migration Plan for Legacy Fields

## Current Status
Fields maintained for database compatibility:
- Model.legacy_field

## Migration Plan
1. Create data migration to copy necessary data
2. Remove fields from model
3. Create schema migration to remove fields
```

## Integration Test Issues

### 1. External Service Mocking

**Error Message**:
```
ConnectionError: Error connecting to external service
```

**Cause**: Tests attempting to connect to real external services.

**Solution**:
1. Use `unittest.mock` to patch external service clients:

```python
from unittest import mock

class StripeTestCase(TestCase):
    def setUp(self):
        self.stripe_patcher = mock.patch('apps.integration.stripe.client.stripe')
        self.mock_stripe = self.stripe_patcher.start()
        # Configure mock responses
        self.mock_stripe.Customer.create.return_value = {'id': 'cus_mock123'}
        
    def tearDown(self):
        self.stripe_patcher.stop()
```

2. For AWS services:

```python
@mock.patch('apps.integration.aws.s3.boto3.client')
def test_s3_upload(self, mock_s3_client):
    mock_s3 = mock.MagicMock()
    mock_s3_client.return_value = mock_s3
    mock_s3.upload_file.return_value = None
    
    # Run test with mocked S3 client
```

### 2. Exception in Exception Handler

**Error Message**:
```
AttributeError: 'NoneType' object has no attribute 'X'
```

**Cause**: Often occurs when handling errors in exception handlers.

**Solution**:
1. Ensure your error handling is defensive:

```python
try:
    # Code that might fail
    result = service.do_something()
except ServiceError as e:
    # Always check for None or provide defaults
    error_code = getattr(e, 'code', 'unknown')
    # Use dict.get with default
    error_data = getattr(e, 'response', {}).get('error', {})
```

## Model Property Testing

### 1. Property Logic Errors

**Error Message**:
```
AssertionError: 'actual_value' != 'expected_value'
```

**Cause**: Property logic doesn't handle all edge cases.

**Solution**:
1. Ensure properties have comprehensive test cases:

```python
def test_property_with_normal_data(self):
    # Test with typical data
    self.assertEqual(self.obj.property_name, "expected")
    
def test_property_with_edge_cases(self):
    # Test with empty values
    self.obj.field = ""
    self.assertEqual(self.obj.property_name, "default")
    
    # Test with None values
    self.obj.field = None
    self.assertEqual(self.obj.property_name, "default")
```

2. Implement defensive property logic:

```python
@property
def some_property(self):
    """Handle all edge cases gracefully."""
    if not hasattr(self, 'related_field') or self.related_field is None:
        return default_value
    
    # Continue with normal logic
```

## Test Isolation Problems

### 1. Database State Leakage

**Error Message**: Various unexpected failures when running the full test suite.

**Cause**: Tests modifying shared database data without cleanup.

**Solution**:
1. Use transaction rollbacks:

```python
from django.test import TransactionTestCase

class MyTest(TransactionTestCase):
    def setUp(self):
        # Create isolated test data
    
    def tearDown(self):
        # Explicit cleanup
        MyModel.objects.all().delete()
```

2. Use `setUpTestData` for immutable shared test data:

```python
@classmethod
def setUpTestData(cls):
    """Create shared test data once for all test methods."""
    cls.constant_data = {...}  # Data that won't be modified
    
def setUp(self):
    """Create data that might be modified."""
    self.test_data = {...}  # Fresh data for each test
```

## Mock-Related Issues

### 1. Mock Not Resetting

**Error Message**: `AssertionError: Expected mock to be called once. Called 3 times.`

**Cause**: Mocks persisting between tests.

**Solution**:
1. Reset mocks in tearDown:

```python
def setUp(self):
    self.patcher = mock.patch('module.function')
    self.mock_func = self.patcher.start()
    
def tearDown(self):
    self.patcher.stop()
    # Or reset all mocks
    mock.reset_all()
```

2. Use context managers for contained mocking:

```python
def test_function(self):
    with mock.patch('module.function') as mock_func:
        # Mock only exists within this context
        mock_func.return_value = "test"
        # Run test code
```

## Testing Complex HTMX Interactions

### 1. Incomplete Testing of HTMX Behavior

**Problem**: Standard Django tests can't verify HTMX triggers and responses.

**Solution**:
1. Use test-specific HTMX views:

```python
class TestHTMXViews(TestCase):
    def test_htmx_response_header(self):
        response = self.client.get('/path/', HTTP_HX_REQUEST='true')
        self.assertEqual(response['HX-Trigger'], '{"showMessage": "Success"}')
        
    def test_htmx_target_content(self):
        response = self.client.get('/path/', HTTP_HX_REQUEST='true')
        self.assertContains(response, '<div id="target">')
```

2. For complete browser testing, use browser-use with Playwright:

```python
@browser_test
async def test_htmx_swap(self, page):
    await page.goto('/path/')
    await page.click('#trigger-button')
    await page.wait_for_selector('#swapped-content')
    content = await page.inner_text('#swapped-content')
    assert "Expected Content" in content
```

## Command Reference for Test Troubleshooting

```bash
# Run only the failing tests
DJANGO_SETTINGS_MODULE=settings pytest --failed-first

# Show more detailed output for failed tests
DJANGO_SETTINGS_MODULE=settings pytest -vxs

# Run with debug logging enabled
DJANGO_SETTINGS_MODULE=settings pytest --log-cli-level=DEBUG

# Run with pytest-xdist for parallel execution
DJANGO_SETTINGS_MODULE=settings pytest -n auto

# Run with coverage to identify untested code
DJANGO_SETTINGS_MODULE=settings pytest --cov=apps.common.models --cov-report=term-missing
```

By systematically applying these solutions, you can resolve most test issues in the codebase.
