from datetime import datetime

import pytest
from django.tasks import task


@task
def add(a: int, b: int) -> int:
    return a + b


@task
def fail() -> None:
    raise ValueError("boom")


@task
def process(data):
    return data


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
    with pytest.raises(TypeError):
        process.enqueue(data=datetime.now())
