"""
Revolutionary Living Project Context System

This module provides always-on project awareness for the Valor agent,
replacing reactive querying with persistent project state management.
"""

from .project_context import LiveProjectContext, ProjectState
from .task_lifecycle import TaskManager, TaskStatus
from .team_coordination import TeamStatusTracker
from .workspace_sync import NotionSynchronizer

__all__ = [
    "LiveProjectContext",
    "ProjectState", 
    "TaskManager",
    "TaskStatus",
    "TeamStatusTracker",
    "NotionSynchronizer",
]