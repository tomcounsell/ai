"""Claude Agent SDK integration for Valor."""

from .sdk_client import ValorAgent, get_agent_response_sdk
from .messenger import BossMessenger, BackgroundTask
from .completion import (
    verify_completion,
    mark_work_complete,
    load_completion_criteria,
    CompletionResult,
    CompletionStatus,
)
from .branch_manager import (
    get_branch_state,
    initialize_work_branch,
    create_work_branch,
    return_to_main,
    mark_work_done,
    format_branch_state_message,
    BranchState,
    WorkStatus,
)
from .job_queue import (
    RedisJob,
    Job,
    enqueue_job,
    check_revival,
    register_callbacks,
    register_project_config,
    get_project_config,
    cleanup_stale_branches,
    get_revival_info,
    record_revival_notification,
    queue_revival_job,
)

__all__ = [
    "ValorAgent",
    "get_agent_response_sdk",
    "BossMessenger",
    "BackgroundTask",
    "verify_completion",
    "mark_work_complete",
    "load_completion_criteria",
    "CompletionResult",
    "CompletionStatus",
    "get_branch_state",
    "initialize_work_branch",
    "create_work_branch",
    "return_to_main",
    "mark_work_done",
    "format_branch_state_message",
    "BranchState",
    "WorkStatus",
    "RedisJob",
    "Job",
    "enqueue_job",
    "check_revival",
    "register_callbacks",
    "register_project_config",
    "get_project_config",
    "cleanup_stale_branches",
    "get_revival_info",
    "record_revival_notification",
    "queue_revival_job",
]
