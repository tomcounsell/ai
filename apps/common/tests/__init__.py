"""
Test package for the common app.

This package contains tests for common app models, utilities, and behaviors.
Tests are organized into the following categories:

1. `test_models/`: Tests for individual model classes
2. `behaviors.py`: Consolidated tests for behavior mixins (both with and without database)
3. `factories.py`: Factory classes for creating test data
4. Other test files for specific features like admin, error handling, etc.

Test files follow Django's convention of starting with "test_" and have class
names with "Test" or "TestCase" suffix.
"""

# Import test classes for easy access
from apps.common.tests.behaviors import (  # noqa: F401
    AnnotatableTest,
    AuthorableTest,
    ExpirableTest,
    LocatableTest,
    PermalinkableTest,
    PublishableTest,
    TestAnnotatableDirect,
    TestAuthorableDirect,
    TestExpirableDirect,
    TestPermalinkableDirect,
    TestPublishableDirect,
    TimestampableTest,
)
