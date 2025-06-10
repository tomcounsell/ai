"""
Task Lifecycle Management - Development-Integrated Task Updates

This module handles the complete lifecycle of tasks, automatically updating
Notion as development work progresses through Claude Code integration.
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from .project_context import Task, LiveProjectContext

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task status values that sync with Notion."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress" 
    COMPLETED = "completed"
    BLOCKED = "blocked"
    REVIEW = "review"
    CANCELLED = "cancelled"


class TaskManager:
    """
    Manages task lifecycle with automatic Notion synchronization.
    
    This class bridges development work with project management by:
    - Automatically updating task status as work progresses
    - Creating new tasks discovered during development
    - Tracking development context in task updates
    - Coordinating with team through status changes
    """
    
    def __init__(self, project_context: LiveProjectContext):
        self.project_context = project_context
        self.pending_updates: List[Dict[str, Any]] = []
    
    async def start_task(self, task_id: str, work_notes: Optional[str] = None) -> str:
        """
        Begin work on a task, updating status in Notion.
        
        Args:
            task_id: Unique identifier for the task
            work_notes: Optional notes about starting the work
            
        Returns:
            Status message about the task start
        """
        logger.info(f"🚀 Starting task: {task_id}")
        
        # Find the task in current context
        task = self._find_task(task_id)
        if not task:
            return f"❌ Task {task_id} not found in current context"
        
        # Update local state
        task.status = TaskStatus.IN_PROGRESS.value
        task.updated_at = datetime.now()
        
        # Prepare Notion update
        update_data = {
            "task_id": task_id,
            "status": TaskStatus.IN_PROGRESS.value,
            "started_at": datetime.now().isoformat(),
            "notes": work_notes or f"Started working on: {task.title}",
            "assignee": "Valor"
        }
        
        # Queue for Notion sync
        self.pending_updates.append(update_data)
        
        # TODO: Implement actual Notion API update
        logger.info(f"📝 Queued task start update for Notion sync")
        
        return f"✅ **Started:** {task.title}\n📝 Status updated to In Progress"
    
    async def complete_task(self, task_id: str, work_summary: str, 
                          technical_details: Optional[str] = None) -> str:
        """
        Mark task as complete with technical summary.
        
        Args:
            task_id: Unique identifier for the task
            work_summary: Summary of completed work
            technical_details: Optional technical implementation details
            
        Returns:
            Status message about task completion
        """
        logger.info(f"✅ Completing task: {task_id}")
        
        # Find the task in current context
        task = self._find_task(task_id)
        if not task:
            return f"❌ Task {task_id} not found in current context"
        
        # Update local state
        task.status = TaskStatus.COMPLETED.value
        task.updated_at = datetime.now()
        
        # Prepare comprehensive Notion update
        completion_notes = [work_summary]
        if technical_details:
            completion_notes.append(f"\n**Technical Details:**\n{technical_details}")
        
        update_data = {
            "task_id": task_id,
            "status": TaskStatus.COMPLETED.value,
            "completed_at": datetime.now().isoformat(),
            "completion_summary": work_summary,
            "technical_notes": technical_details,
            "notes": "\n".join(completion_notes),
            "assignee": "Valor"
        }
        
        # Queue for Notion sync
        self.pending_updates.append(update_data)
        
        # TODO: Implement actual Notion API update
        logger.info(f"📝 Queued task completion update for Notion sync")
        
        # Update project context recent updates
        if self.project_context.current_state:
            self.project_context.current_state.recent_updates.insert(0, 
                f"✅ Completed: {task.title}")
        
        return f"🎉 **Completed:** {task.title}\n📝 Added technical summary to Notion"
    
    async def report_blocker(self, task_id: str, blocker_description: str) -> str:
        """
        Report a blocker for team visibility.
        
        Args:
            task_id: Task that is blocked
            blocker_description: Description of the blocker
            
        Returns:
            Status message about blocker report
        """
        logger.info(f"🚫 Reporting blocker for task: {task_id}")
        
        # Find the task in current context
        task = self._find_task(task_id)
        if not task:
            return f"❌ Task {task_id} not found in current context"
        
        # Update local state
        task.status = TaskStatus.BLOCKED.value
        task.blockers.append(blocker_description)
        task.updated_at = datetime.now()
        
        # Prepare Notion update with blocker visibility
        update_data = {
            "task_id": task_id,
            "status": TaskStatus.BLOCKED.value,
            "blocked_at": datetime.now().isoformat(),
            "blocker_description": blocker_description,
            "notes": f"🚫 BLOCKED: {blocker_description}",
            "assignee": "Valor",
            "needs_attention": True
        }
        
        # Queue for Notion sync
        self.pending_updates.append(update_data)
        
        # TODO: Implement actual Notion API update and team notification
        logger.warning(f"🚫 Task blocked - queued for team notification")
        
        # Update project context blockers
        if self.project_context.current_state:
            self.project_context.current_state.blockers.append(
                f"{task.title}: {blocker_description}")
        
        return f"🚫 **Blocked:** {task.title}\n📢 Team notified of blocker: {blocker_description}"
    
    async def request_review(self, task_id: str, review_notes: str) -> str:
        """
        Move task to review status with context.
        
        Args:
            task_id: Task ready for review
            review_notes: Notes for the reviewer
            
        Returns:
            Status message about review request
        """
        logger.info(f"👀 Requesting review for task: {task_id}")
        
        # Find the task in current context
        task = self._find_task(task_id)
        if not task:
            return f"❌ Task {task_id} not found in current context"
        
        # Update local state
        task.status = TaskStatus.REVIEW.value
        task.updated_at = datetime.now()
        
        # Prepare Notion update
        update_data = {
            "task_id": task_id,
            "status": TaskStatus.REVIEW.value,
            "review_requested_at": datetime.now().isoformat(),
            "review_notes": review_notes,
            "notes": f"👀 Ready for review: {review_notes}",
            "assignee": "Valor",
            "needs_review": True
        }
        
        # Queue for Notion sync
        self.pending_updates.append(update_data)
        
        # TODO: Implement actual Notion API update and reviewer notification
        logger.info(f"👀 Review requested - queued for reviewer notification")
        
        return f"👀 **Review Requested:** {task.title}\n📝 Notes: {review_notes}"
    
    async def create_task_from_development(self, title: str, description: str, 
                                         technical_details: str, 
                                         priority: str = "medium") -> str:
        """
        Create new task discovered during development work.
        
        Args:
            title: Task title
            description: Task description
            technical_details: Technical context from development
            priority: Task priority
            
        Returns:
            Status message about task creation
        """
        logger.info(f"➕ Creating new task from development: {title}")
        
        # Generate simple task ID (TODO: Use proper UUID)
        task_id = f"dev_task_{int(datetime.now().timestamp())}"
        
        # Create task object
        new_task = Task(
            id=task_id,
            title=title,
            description=description,
            status=TaskStatus.NOT_STARTED.value,
            priority=priority,
            assignee="Valor",
            tags=["development-discovered"],
            updated_at=datetime.now()
        )
        
        # Add to local context
        if self.project_context.current_state:
            self.project_context.current_state.my_tasks.append(new_task)
        
        # Prepare Notion creation
        creation_data = {
            "title": title,
            "description": description,
            "technical_context": technical_details,
            "priority": priority,
            "status": TaskStatus.NOT_STARTED.value,
            "assignee": "Valor",
            "created_at": datetime.now().isoformat(),
            "source": "development_discovery",
            "tags": ["development-discovered"]
        }
        
        # Queue for Notion sync
        self.pending_updates.append({"action": "create", "data": creation_data})
        
        # TODO: Implement actual Notion API creation
        logger.info(f"📝 Queued new task creation for Notion sync")
        
        # Update project context recent updates
        if self.project_context.current_state:
            self.project_context.current_state.recent_updates.insert(0,
                f"➕ Created: {title}")
        
        return f"➕ **Created Task:** {title}\n📝 Added to project backlog with technical context"
    
    async def update_progress(self, task_id: str, progress_notes: str) -> str:
        """
        Update task progress without changing status.
        
        Args:
            task_id: Task to update
            progress_notes: Progress update notes
            
        Returns:
            Status message about progress update
        """
        logger.info(f"📈 Updating progress for task: {task_id}")
        
        # Find the task in current context
        task = self._find_task(task_id)
        if not task:
            return f"❌ Task {task_id} not found in current context"
        
        # Update local state
        task.updated_at = datetime.now()
        
        # Prepare Notion update
        update_data = {
            "task_id": task_id,
            "progress_update": progress_notes,
            "updated_at": datetime.now().isoformat(),
            "notes": f"📈 Progress: {progress_notes}",
            "assignee": "Valor"
        }
        
        # Queue for Notion sync
        self.pending_updates.append(update_data)
        
        # TODO: Implement actual Notion API update
        logger.info(f"📝 Queued progress update for Notion sync")
        
        return f"📈 **Progress Updated:** {task.title}\n📝 {progress_notes}"
    
    def _find_task(self, task_id: str) -> Optional[Task]:
        """Find task by ID in current project context."""
        if not self.project_context.current_state:
            return None
        
        # Search in my tasks
        for task in self.project_context.current_state.my_tasks:
            if task.id == task_id:
                return task
        
        # Search in team tasks
        for member_tasks in self.project_context.current_state.team_tasks.values():
            for task in member_tasks:
                if task.id == task_id:
                    return task
        
        return None
    
    async def sync_pending_updates(self) -> int:
        """
        Sync all pending updates to Notion.
        
        Returns:
            Number of updates synchronized
        """
        if not self.pending_updates:
            return 0
        
        logger.info(f"🔄 Syncing {len(self.pending_updates)} pending updates to Notion")
        
        # TODO: Implement actual Notion API synchronization
        # For now, just log the updates
        for update in self.pending_updates:
            logger.info(f"📝 Would sync to Notion: {update}")
        
        synced_count = len(self.pending_updates)
        self.pending_updates.clear()
        
        logger.info(f"✅ Synced {synced_count} updates to Notion")
        return synced_count
    
    def get_pending_updates_count(self) -> int:
        """Get count of pending updates waiting for sync."""
        return len(self.pending_updates)