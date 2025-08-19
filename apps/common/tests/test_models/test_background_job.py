"""
Tests for the BackgroundJob model and related functionality.
"""

from datetime import datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from ...models.background_job import BackgroundJob


# Create a concrete class for testing since BackgroundJob is abstract
class ConcreteBackgroundJob(BackgroundJob):
    """Concrete implementation of the abstract BackgroundJob model for testing."""

    class Meta:
        app_label = "common"
        # Add managed = False to avoid creating table in database
        managed = False


# Create a mock BackgroundJob for testing execution_time_humanized
class MockBackgroundJob:
    """Mock implementation for testing calculation methods without database."""

    def __init__(self, start_run_at=None, end_run_at=None):
        self.start_run_at = start_run_at
        self.end_run_at = end_run_at
        # Add property implementation directly from BackgroundJob
        self.execution_time_humanized = BackgroundJob.execution_time_humanized.fget(
            self
        )


class BackgroundJobTest(TestCase):
    """Test cases for the BackgroundJob model."""

    def setUp(self):
        """Set up the in-memory database for ConcreteBackgroundJob."""
        from django.db import connection

        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(ConcreteBackgroundJob)

    def test_model_fields(self):
        """Test the basic fields of the BackgroundJob model."""
        job = ConcreteBackgroundJob.objects.create(
            name="Test Job", description="Test description"
        )

        self.assertEqual(job.name, "Test Job")
        self.assertEqual(job.description, "Test description")
        self.assertIsNotNone(job.start_run_at)
        self.assertIsNone(job.end_run_at)
        self.assertFalse(job.has_errors)
        self.assertTrue(job.is_failed)  # Default is True
        self.assertEqual(job.logs, [])

    def test_execution_time_humanized_with_end_time(self):
        """Test the execution_time_humanized property with an end time."""
        # Use fixed dates with timezone to avoid timing issues
        start_time = timezone.make_aware(datetime(2023, 1, 1, 10, 0, 0))
        end_time = timezone.make_aware(datetime(2023, 1, 1, 12, 30, 15))

        # Create mock job with fixed timestamps
        job = MockBackgroundJob(start_time, end_time)

        self.assertEqual(job.execution_time_humanized, "2 hr, 30 min, 15 s")

    def test_execution_time_humanized_without_end_time(self):
        """Test the execution_time_humanized property without an end time."""
        # Use fixed date with timezone
        start_time = timezone.make_aware(datetime(2023, 1, 1, 10, 0, 0))

        with mock.patch("django.utils.timezone.now") as mock_now:
            # Mock timezone.now() to return a fixed time 1h, 15m, 30s later
            mock_now.return_value = timezone.make_aware(
                datetime(2023, 1, 1, 11, 15, 30)
            )

            # Create mock job
            job = MockBackgroundJob(start_time, None)

            # Test the property
            self.assertEqual(job.execution_time_humanized, "1 hr, 15 min, 30 s")

    def test_execution_time_humanized_minutes_only(self):
        """Test the execution_time_humanized property with only minutes and seconds."""
        # Use fixed dates with timezone
        start_time = timezone.make_aware(datetime(2023, 1, 1, 10, 0, 0))
        end_time = timezone.make_aware(datetime(2023, 1, 1, 10, 15, 30))

        # Create mock job
        job = MockBackgroundJob(start_time, end_time)

        self.assertEqual(job.execution_time_humanized, "15 min, 30 s")

    def test_execution_time_humanized_seconds_only(self):
        """Test the execution_time_humanized property with only seconds."""
        # Use fixed dates with timezone
        start_time = timezone.make_aware(datetime(2023, 1, 1, 10, 0, 0))
        end_time = timezone.make_aware(datetime(2023, 1, 1, 10, 0, 45))

        # Create mock job
        job = MockBackgroundJob(start_time, end_time)

        self.assertEqual(job.execution_time_humanized, "45 s")

    def test_execution_time_humanized_without_start_time(self):
        """Test the execution_time_humanized property without a start time."""
        # Create mock job with no start time
        job = MockBackgroundJob(None, None)

        # Test the property
        self.assertEqual(job.execution_time_humanized, "")

    def test_log_handling(self):
        """Test handling of logs in the BackgroundJob model."""
        job = ConcreteBackgroundJob.objects.create(name="Log Test Job")

        # Initially logs should be empty
        self.assertEqual(job.logs, [])

        # Update logs
        job.logs = ["Log entry 1", "Log entry 2"]
        job.save()

        # Retrieve the job and check logs
        updated_job = ConcreteBackgroundJob.objects.get(pk=job.pk)
        self.assertEqual(updated_job.logs, ["Log entry 1", "Log entry 2"])

    def test_job_success_state(self):
        """Test setting a background job to successful state."""
        job = ConcreteBackgroundJob.objects.create(name="Success Job")

        # Default state is failed=True
        self.assertTrue(job.is_failed)

        # Set to successful state
        job.is_failed = False
        job.end_run_at = timezone.now()
        job.save()

        # Verify state
        updated_job = ConcreteBackgroundJob.objects.get(pk=job.pk)
        self.assertFalse(updated_job.is_failed)
        self.assertIsNotNone(updated_job.end_run_at)

    def test_job_error_state(self):
        """Test setting a background job to error state."""
        job = ConcreteBackgroundJob.objects.create(
            name="Error Job",
            is_failed=False,  # Start with non-failed state
            has_errors=False,
        )

        # Set to error state
        job.has_errors = True
        job.logs = ["An error occurred", "Details: Test failure"]
        job.save()

        # Verify state
        updated_job = ConcreteBackgroundJob.objects.get(pk=job.pk)
        self.assertTrue(updated_job.has_errors)
        self.assertEqual(
            updated_job.logs, ["An error occurred", "Details: Test failure"]
        )
