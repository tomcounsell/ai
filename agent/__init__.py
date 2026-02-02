"""Claude Agent SDK integration for Valor."""

from .branch_manager import (
    BranchState,
    WorkStatus,
    create_work_branch,
    format_branch_state_message,
    get_branch_state,
    initialize_work_branch,
    mark_work_done,
    return_to_main,
)
from .completion import (
    CompletionResult,
    CompletionStatus,
    load_completion_criteria,
    mark_work_complete,
    verify_completion,
)
from .job_queue import (
    Job,
    RedisJob,
    check_revival,
    cleanup_stale_branches,
    enqueue_job,
    get_project_config,
    queue_revival_job,
    record_revival_cooldown,
    register_callbacks,
    register_project_config,
)
from .messenger import BackgroundTask, BossMessenger
from .sdk_client import (
    ValorAgent,
    get_active_client,
    get_agent_response_sdk,
    get_all_active_sessions,
)
from .steering import (
    clear_steering_queue,
    has_steering_messages,
    pop_all_steering_messages,
    pop_steering_message,
    push_steering_message,
)

__all__ = [
    "ValorAgent",
    "get_agent_response_sdk",
    "get_active_client",
    "get_all_active_sessions",
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
    "record_revival_cooldown",
    "queue_revival_job",
    "push_steering_message",
    "pop_steering_message",
    "pop_all_steering_messages",
    "clear_steering_queue",
    "has_steering_messages",
]
