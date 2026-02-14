---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/62
---

# Background Task Service: Abstract Interface for Async Task Execution

## Problem

The podcast workflow has 6+ operations that take 1-20 minutes (API calls to Perplexity, Gemini, NotebookLM, GPT-Researcher, OpenAI Whisper, cover art generation). On a web server with ~30s request timeouts, these must run in the background. The workflow code should not know whether the runner is Celery, Django-Q2, or a simple thread pool.

**Current behavior:**
No abstraction exists. Long-running operations would block request threads or require direct integration with a specific task queue.

**Desired outcome:**
A task execution abstraction that lets callers enqueue functions for background execution and poll for results, with the underlying worker configured via Django settings.

## Appetite

**Size:** Small

**Team:** Solo dev. Two backends (sync + async worker), status persistence in DB.

**Interactions:**
- PM check-ins: 0-1 (confirm Django-Q2 vs Celery choice)
- Review rounds: 1

## Prerequisites

None - this is a foundational service.

## Solution

### Key Elements

- **Abstract task interface**: `enqueue()`, `get_status()`, `cancel()`
- **Backend registry**: Select backend via `TASK_BACKEND` Django setting
- **Two backends**: `SyncTaskRunner` (dev) and `DjangoQRunner` (prod)
- **Status persistence**: Task model stores status for polling

### Technical Approach

1. **Create TaskStatus model at `apps/common/models/task_status.py`:**

   ```python
   from django.db import models
   from apps.common.behaviors import Timestampable

   class TaskStatus(Timestampable):
       class Status(models.TextChoices):
           PENDING = "pending"
           RUNNING = "running"
           COMPLETE = "complete"
           FAILED = "failed"
           CANCELLED = "cancelled"

       task_id = models.CharField(max_length=64, unique=True, db_index=True)
       status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
       func_name = models.CharField(max_length=255)
       result = models.JSONField(null=True, blank=True)
       error = models.TextField(blank=True)
       started_at = models.DateTimeField(null=True, blank=True)
       completed_at = models.DateTimeField(null=True, blank=True)
   ```

2. **Create task service module at `apps/common/services/tasks.py`:**

   ```python
   import uuid
   from abc import ABC, abstractmethod
   from typing import Callable, Any
   from django.conf import settings
   from django.utils import timezone
   from apps.common.models import TaskStatus

   class TaskBackend(ABC):
       @abstractmethod
       def submit(self, task_id: str, fn: Callable, *args, **kwargs) -> None:
           """Submit function for execution."""
           pass

       @abstractmethod
       def cancel(self, task_id: str) -> bool:
           """Attempt to cancel task."""
           pass

   class SyncTaskRunner(TaskBackend):
       """Synchronous execution for development. Runs immediately in-process."""

       def submit(self, task_id: str, fn: Callable, *args, **kwargs) -> None:
           task = TaskStatus.objects.get(task_id=task_id)
           task.status = TaskStatus.Status.RUNNING
           task.started_at = timezone.now()
           task.save()

           try:
               result = fn(*args, **kwargs)
               task.status = TaskStatus.Status.COMPLETE
               task.result = result
           except Exception as e:
               task.status = TaskStatus.Status.FAILED
               task.error = str(e)
           finally:
               task.completed_at = timezone.now()
               task.save()

       def cancel(self, task_id: str) -> bool:
           # Sync tasks complete immediately, can't cancel
           return False

   class DjangoQRunner(TaskBackend):
       """Django-Q2 backend for production async execution."""

       def submit(self, task_id: str, fn: Callable, *args, **kwargs) -> None:
           from django_q.tasks import async_task

           # Wrap function to update status
           def wrapper(task_id, fn, *args, **kwargs):
               task = TaskStatus.objects.get(task_id=task_id)
               task.status = TaskStatus.Status.RUNNING
               task.started_at = timezone.now()
               task.save()

               try:
                   result = fn(*args, **kwargs)
                   task.status = TaskStatus.Status.COMPLETE
                   task.result = result
               except Exception as e:
                   task.status = TaskStatus.Status.FAILED
                   task.error = str(e)
               finally:
                   task.completed_at = timezone.now()
                   task.save()

           async_task(wrapper, task_id, fn, *args, **kwargs)

       def cancel(self, task_id: str) -> bool:
           # Django-Q2 cancellation is limited, mark as cancelled
           task = TaskStatus.objects.filter(
               task_id=task_id, status=TaskStatus.Status.PENDING
           ).update(status=TaskStatus.Status.CANCELLED)
           return task > 0

   # Backend registry
   _backends = {
       'sync': SyncTaskRunner,
       'django_q': DjangoQRunner,
   }

   def _get_backend() -> TaskBackend:
       backend_name = getattr(settings, 'TASK_BACKEND', 'sync')
       return _backends[backend_name]()

   # Public API
   def enqueue(fn: Callable, *args, task_id: str = "", **kwargs) -> str:
       """Submit a function for background execution. Returns a task_id."""
       if not task_id:
           task_id = str(uuid.uuid4())[:8]

       TaskStatus.objects.create(
           task_id=task_id,
           func_name=f"{fn.__module__}.{fn.__name__}",
           status=TaskStatus.Status.PENDING,
       )

       _get_backend().submit(task_id, fn, *args, **kwargs)
       return task_id

   def get_status(task_id: str) -> dict:
       """Returns status dict with status, result, error."""
       try:
           task = TaskStatus.objects.get(task_id=task_id)
           return {
               "status": task.status,
               "result": task.result,
               "error": task.error,
               "started_at": task.started_at,
               "completed_at": task.completed_at,
           }
       except TaskStatus.DoesNotExist:
           return {"status": "unknown", "result": None, "error": "Task not found"}

   def cancel(task_id: str) -> bool:
       """Attempt to cancel a pending/running task."""
       return _get_backend().cancel(task_id)
   ```

3. **Add settings configuration:**

   ```python
   # settings/base.py
   TASK_BACKEND = env.str("TASK_BACKEND", default="sync")

   # settings/local.py
   TASK_BACKEND = "sync"

   # settings/production.py
   TASK_BACKEND = "django_q"
   Q_CLUSTER = {
       'name': 'cuttlefish',
       'workers': 2,
       'recycle': 500,
       'timeout': 1200,  # 20 minutes max
       'orm': 'default',
   }
   ```

4. **Write tests in `apps/common/tests/test_tasks.py`:**

   ```python
   import pytest
   from apps.common.services.tasks import enqueue, get_status, cancel
   from apps.common.models import TaskStatus

   @pytest.fixture
   def sync_backend(settings):
       settings.TASK_BACKEND = "sync"

   def test_enqueue_and_complete(sync_backend, db):
       def add(a, b):
           return a + b

       task_id = enqueue(add, 2, 3)
       status = get_status(task_id)

       assert status["status"] == "complete"
       assert status["result"] == 5

   def test_enqueue_with_error(sync_backend, db):
       def fail():
           raise ValueError("boom")

       task_id = enqueue(fail)
       status = get_status(task_id)

       assert status["status"] == "failed"
       assert "boom" in status["error"]

   def test_custom_task_id(sync_backend, db):
       task_id = enqueue(lambda: 42, task_id="my-task")
       assert task_id == "my-task"
       assert get_status("my-task")["result"] == 42
   ```

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/common/models/task_status.py` | Create | TaskStatus model |
| `apps/common/models/__init__.py` | Modify | Export TaskStatus |
| `apps/common/services/tasks.py` | Create | Task service + backends |
| `apps/common/tests/test_tasks.py` | Create | Unit tests |
| `apps/common/migrations/0004_task_status.py` | Create | Migration |
| `settings/base.py` | Modify | Add TASK_BACKEND setting |
| `settings/production.py` | Modify | Add Q_CLUSTER config |
| `pyproject.toml` | Modify | Add django-q2 dependency |

## Rabbit Holes

- **Don't add Celery backend** - Django-Q2 is simpler, uses Django ORM, no Redis required for basic setup
- **Don't add priority queues** - All podcast tasks are similar priority
- **Don't add task chaining** - Workflow orchestration is handled by the calling code

## No-Gos

- No distributed task locking (not needed yet)
- No scheduled/cron tasks (separate concern)
- No task retries (caller handles retry logic)

## Acceptance Criteria

- [ ] Module importable: `from apps.common.services.tasks import enqueue, get_status`
- [ ] At least two backends: synchronous (dev) and async worker (prod)
- [ ] Task status persisted in DB and queryable
- [ ] Backend selected by Django settings (`TASK_BACKEND`)
- [ ] Tests pass with synchronous backend
- [ ] No podcast-specific code — general-purpose utility
