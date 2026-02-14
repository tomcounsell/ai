---
status: Done
type: chore
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/62
pr: https://github.com/yudame/cuttlefish/pull/68
---

# Background Task Service: Django 6.0 Tasks Framework

## Problem

The podcast workflow has 6+ operations that take 1-20 minutes (API calls to Perplexity, Gemini, NotebookLM, GPT-Researcher, OpenAI Whisper, cover art generation). On a web server with ~30s request timeouts, these must run in the background.

**Current behavior:**
Django 6.0 with native tasks framework. Tasks are defined with `@task`, enqueued with `.enqueue()`, and tracked via `TaskResult`. `ImmediateBackend` runs tasks inline in dev/test; `DatabaseBackend` via `django-tasks-db` processes tasks asynchronously in production with `manage.py db_worker`.

**Desired outcome:**
Upgrade to Django 6.0 and adopt its native tasks framework. Tasks are defined with `@task`, enqueued with `.enqueue()`, and tracked via `TaskResult`. Backend is swappable via `TASKS` setting — no custom abstraction needed.

## Appetite

**Size:** Small

**Team:** Solo dev. Upgrade Django, configure backends, validate with tests.

**Interactions:**
- Review rounds: 1

## Prerequisites

None — this is a foundational upgrade.

## Solution

### Key Elements

- **Django 6.0 upgrade** from 5.2
- **Native `@task` decorator** — no custom TaskBackend ABC, no custom model
- **Three backends**: `ImmediateBackend` (dev), `DummyBackend` (test), `DatabaseBackend` (prod)
- **`django-tasks-db`** for production — uses existing Postgres, worker via `manage.py db_worker`

### Technical Approach

1. **Upgrade Django and add `django-tasks-db`:**

   ```bash
   uv add "Django>=6.0"
   uv add django-tasks-db
   ```

2. **Add to `INSTALLED_APPS` in `settings/base.py`:**

   ```python
   THIRD_PARTY_APPS = [
       ...
       "django_tasks_db",
   ]
   ```

3. **Configure task backends in settings:**

   ```python
   # settings/base.py — default for dev
   TASKS = {
       "default": {
           "BACKEND": "django.tasks.backends.immediate.ImmediateBackend",
       }
   }

   # settings/production.py — DatabaseBackend with worker
   TASKS = {
       "default": {
           "BACKEND": "django_tasks_db.DatabaseBackend",
           "QUEUES": ["default"],
       }
   }
   ```

   No test-specific override needed — `ImmediateBackend` runs tasks synchronously, which is correct for both dev and test.

4. **Define tasks using `@task` in relevant app modules:**

   Example (not part of this issue — just showing the pattern callers will use):

   ```python
   # apps/podcast/tasks.py
   from django.tasks import task

   @task
   def run_perplexity_research(episode_id: int, query: str) -> dict:
       """Long-running research task."""
       # ... API call that takes minutes ...
       return {"status": "done", "results": [...]}
   ```

   Enqueue from a view or service:

   ```python
   result = run_perplexity_research.enqueue(episode_id=42, query="...")
   result_id = result.id  # UUID — store this for polling

   # Later, check status:
   result = run_perplexity_research.get_result(result_id)
   result.refresh()
   result.status      # NEW, RUNNING, SUCCESSFUL, FAILED
   result.return_value # available when SUCCESSFUL
   result.errors       # list of TaskError when FAILED
   ```

5. **Run migrations for `django_tasks_db`:**

   ```bash
   uv run python manage.py migrate django_tasks_db
   ```

6. **Add worker process for production (Render):**

   Add a Background Worker service on Render that runs:

   ```bash
   python manage.py db_worker
   ```

   This polls the database for enqueued tasks and executes them. In dev, `ImmediateBackend` runs tasks inline — no worker needed.

7. **Write validation tests in `apps/common/tests/test_tasks.py`:**

   ```python
   import pytest
   from django.tasks import task

   @task
   def add(a: int, b: int) -> int:
       return a + b

   @task
   def fail() -> None:
       raise ValueError("boom")

   @pytest.mark.django_db
   def test_enqueue_and_complete():
       """ImmediateBackend runs task synchronously and returns result."""
       result = add.enqueue(a=2, b=3)
       assert result.status.name == "SUCCESSFUL"

   @pytest.mark.django_db
   def test_enqueue_with_failure():
       """Failed tasks store error information."""
       result = fail.enqueue()
       assert result.status.name == "FAILED"
       assert len(result.errors) > 0
       assert "boom" in result.errors[0].traceback

   @pytest.mark.django_db
   def test_task_args_must_be_json_serializable():
       """Django tasks enforce JSON-serializable arguments."""
       from datetime import datetime

       @task
       def process(data):
           return data

       with pytest.raises(TypeError):
           process.enqueue(data=datetime.now())
   ```

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `pyproject.toml` | Modify | Upgrade `Django>=6.0`, add `django-tasks-db` |
| `settings/base.py` | Modify | Add `django_tasks_db` to `INSTALLED_APPS`, add `TASKS` config |
| `settings/production.py` | Modify | Override `TASKS` to use `DatabaseBackend` |
| `apps/common/tests/test_tasks.py` | Create | Validation tests for task framework |

**No custom models, no custom service module, no migrations in our code.**

### Render Deployment

Add a Background Worker service alongside the existing web service:

| Setting | Value |
|---------|-------|
| Name | `cuttlefish-worker` |
| Type | Background Worker |
| Build Command | (same as web service) |
| Start Command | `python manage.py db_worker` |
| Environment | Same env group as web service |

The worker shares the same Postgres database and `DATABASE_URL`.

## Django 6.0 Upgrade Notes

Check for breaking changes before upgrading:
- Review [Django 6.0 release notes](https://docs.djangoproject.com/en/6.0/releases/6.0/) for deprecation removals
- Run `python -Wa manage.py check` after upgrade to surface warnings
- Run full test suite to catch regressions
- Third-party packages may need updates — check compatibility of `django-unfold`, `drf-yasg`, `django-debug-toolbar`, etc.

## Rabbit Holes

- **Don't build a custom task abstraction** — Django 6.0 provides exactly this
- **Don't use Redis for the task backend** — DatabaseBackend uses Postgres (already available), simpler to operate. Redis stays for caching only
- **Don't add priority queues or task chaining** — not needed yet
- **Don't add Celery** — DatabaseBackend is sufficient at this scale

## No-Gos

- No distributed task locking (not needed yet)
- No scheduled/cron tasks (separate concern)
- No task retries in the framework (caller handles retry logic)
- No custom task status model (Django manages this)

## Acceptance Criteria

- [x] Django upgraded to 6.0+
- [x] `TASKS` setting configured with `ImmediateBackend` (dev) and `DatabaseBackend` (prod)
- [x] `django-tasks-db` installed and migrations applied
- [x] Tasks definable with `@task` decorator and enqueueable with `.enqueue()`
- [x] Task results trackable via `.get_result()` and `.refresh()`
- [x] Tests pass with `ImmediateBackend` — status is `SUCCESSFUL` (not `COMPLETE`)
- [x] Worker command `manage.py db_worker` documented for Render deployment
- [x] No podcast-specific code — general-purpose infrastructure
