import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
# Celery Configuration Options
# CELERY_TIMEZONE = "Etc/UCT"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 3600 * 4  # 4 hours
# CELERY_RESULT_BACKEND = 'django-db'
# CELERY_CACHE_BACKEND = 'django-cache'


class MyCelery(Celery):
    def gen_task_name(self, name, module):
        module = ".".join(
            [dir for dir in module.split(".") if dir not in ("apps", "tasks")]
        )
        return super().gen_task_name(name, module)


celery_app = MyCelery("data")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
celery_app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
celery_app.autodiscover_tasks()
