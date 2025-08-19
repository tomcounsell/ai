import logging

from django.db import transaction
from django_celery_beat.models import PeriodicTask, PeriodicTasks
from django_celery_beat.schedulers import DatabaseScheduler


class DatabaseSchedulerWithCleanup(DatabaseScheduler):
    def setup_schedule(self):
        schedule = self.app.conf.beat_schedule
        with transaction.atomic():
            num, info = (
                PeriodicTask.objects.exclude(task__startswith="celery.")
                .exclude(name__in=schedule.keys())
                .delete()
            )
            logging.info("Removed %d obsolete periodic tasks.", num)
            if num > 0:
                PeriodicTasks.update_changed()
        super().setup_schedule()
