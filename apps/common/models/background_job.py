from django.db import models


class BackgroundJob(models.Model):
    """
    An abstract model for tracking the execution of background tasks or jobs.

    This model provides a foundation for monitoring asynchronous jobs, capturing their
    execution time, status, and any logs or errors that occur during processing.
    It's designed to be extended by specific job types.

    Attributes:
        name (str): The name or identifier of the job
        description (str): A detailed description of the job's purpose
        start_run_at (datetime): When the job started execution
        end_run_at (datetime): When the job completed execution
        has_errors (bool): Whether any errors occurred during execution
        is_failed (bool): Whether the job failed to complete successfully
        logs (JSONField): A collection of log entries generated during execution

    Properties:
        execution_time_humanized (str): A human-readable representation of the job's execution time

    Example:
        ```python
        class DataImportJob(BackgroundJob):
            source_file = models.FileField(upload_to='imports/')
            records_processed = models.IntegerField(default=0)

            def process(self):
                try:
                    # Process data
                    self.is_failed = False
                except Exception as e:
                    self.has_errors = True
                    self.is_failed = True
                    self.logs.append(str(e))
                finally:
                    self.end_run_at = timezone.now()
                    self.save()
        ```
    """

    name = models.CharField(max_length=64, null=True)
    description = models.CharField(max_length=1000, null=True, blank=True)

    start_run_at = models.DateTimeField(auto_now_add=True)
    end_run_at = models.DateTimeField(null=True)

    has_errors = models.BooleanField(null=False, default=False)
    is_failed = models.BooleanField(null=False, default=True)
    logs = models.JSONField(default=list, blank=True)

    class Meta:
        abstract = True

    @property
    def execution_time_humanized(self) -> str:
        """
        Get a human-readable representation of the job's execution time.

        This property calculates the time elapsed between the job's start and end times
        and formats it as a string with hours, minutes, and seconds. If the job is still
        running, it calculates the time elapsed so far.

        Returns:
            str: A formatted string like "2 hr, 30 min, 45 s" or an empty string if the
                 job hasn't started.
        """
        if not self.start_run_at:
            return ""

        from django.utils import timezone

        # Use timezone.now() which returns an aware datetime
        end_time = self.end_run_at or timezone.now()

        # Calculate the time difference in seconds
        delta = end_time - self.start_run_at
        total_seconds = delta.total_seconds()

        # Calculate hours, minutes, and seconds
        hours, remainder = divmod(int(total_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)

        # Format the string
        return (
            (f"{hours} hr, " if hours else "")
            + (f"{minutes} min, " if minutes or hours else "")
            + f"{seconds} s"
        )
