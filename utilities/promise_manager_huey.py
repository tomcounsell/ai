"""
Promise management with Huey integration.

DESIGN PRINCIPLE: This manager provides a clean interface between
the main application and the Huey task queue.
"""
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from tasks.huey_config import huey
from tasks.promise_tasks import (
    execute_promise_by_type, 
    check_promise_dependencies
)
from .database import get_database_connection, create_promise as db_create_promise

logger = logging.getLogger(__name__)


class HueyPromiseManager:
    """
    Manages promise lifecycle with Huey task queue.
    
    BEST PRACTICE: Encapsulate task queue details behind a
    clean interface. The rest of the app shouldn't need to
    know about Huey specifics.
    """
    
    def __init__(self):
        """Initialize the promise manager."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def create_promise(
        self,
        chat_id: int,
        message_id: int,
        task_description: str,
        task_type: str = 'code',
        username: Optional[str] = None,
        user_id: Optional[int] = None,
        priority: int = 5,
        metadata: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[int]] = None
    ) -> int:
        """
        Create a new promise and queue it for execution.
        
        IMPLEMENTATION NOTE: This method handles both simple promises
        and promises with dependencies. The task queue determines
        when to actually execute based on dependency status.
        
        Args:
            chat_id: Telegram chat ID for responses
            message_id: Original message that triggered this promise
            task_description: Human-readable description
            task_type: Type of task ('code', 'search', etc.)
            username: Telegram username for logging
            user_id: Telegram user ID
            priority: 1 (highest) to 10 (lowest)
            metadata: Additional task-specific data
            dependencies: List of promise IDs that must complete first
            
        Returns:
            Promise ID from database
        """
        # Validate inputs
        if task_type not in ['code', 'search', 'analysis']:
            raise ValueError(f"Invalid task type: {task_type}")
        
        # Determine initial status
        status = 'waiting' if dependencies else 'pending'
        
        # Create promise in database
        with get_database_connection() as conn:
            cursor = conn.cursor()
            
            # Need to add more fields to the promises table
            # For now, use the basic create_promise function
            promise_id = db_create_promise(chat_id, message_id, task_description)
            
            # Update with additional fields if needed
            if metadata or dependencies:
                cursor.execute("""
                    UPDATE promises 
                    SET status = ?
                    WHERE id = ?
                """, (status, promise_id))
                conn.commit()
        
        self.logger.info(
            f"Created promise {promise_id}: {task_type} - "
            f"{task_description[:50]}... (status: {status})"
        )
        
        # Queue for execution
        if dependencies:
            # Schedule dependency check
            # BEST PRACTICE: Use delay to avoid race conditions
            check_promise_dependencies.schedule(
                args=(promise_id,),
                delay=2
            )
        else:
            # Execute immediately
            self.logger.info(f"Scheduling promise {promise_id} for immediate execution")
            # Use delay=0 for immediate execution
            result = execute_promise_by_type.schedule(args=(promise_id,), delay=0)
            
            # Store Huey task ID if available
            if hasattr(result, 'id'):
                self._update_huey_task_id(promise_id, result.id)
                self.logger.info(f"Queued promise {promise_id} with Huey task ID: {result.id}")
        
        return promise_id
    
    def create_parallel_promises(
        self,
        chat_id: int,
        message_id: int,
        tasks: List[Dict[str, Any]],
        username: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> List[int]:
        """
        Create multiple promises that execute in parallel.
        
        BEST PRACTICE: Batch database operations when creating
        multiple related items.
        
        Example:
            tasks = [
                {'description': 'Review auth.py', 'type': 'code'},
                {'description': 'Review api.py', 'type': 'code'},
                {'description': 'Review db.py', 'type': 'code'}
            ]
        """
        promise_ids = []
        
        for task in tasks:
            promise_id = self.create_promise(
                chat_id=chat_id,
                message_id=message_id,
                task_description=task['description'],
                task_type=task.get('type', 'code'),
                username=username,
                user_id=user_id,
                priority=task.get('priority', 5),
                metadata=task.get('metadata')
            )
            promise_ids.append(promise_id)
        
        self.logger.info(f"Created {len(promise_ids)} parallel promises")
        return promise_ids
    
    def create_dependent_promises(
        self,
        chat_id: int,
        message_id: int,
        tasks: List[Dict[str, Any]],
        dependency_map: Dict[str, List[str]],
        username: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> Dict[str, int]:
        """
        Create promises with dependencies between them.
        
        IMPLEMENTATION NOTE: This uses a simple dependency model where
        promises wait for specific other promises to complete.
        
        Example:
            tasks = [
                {'name': 'setup', 'description': 'Set up environment', 'type': 'code'},
                {'name': 'test', 'description': 'Write tests', 'type': 'code'},
                {'name': 'run', 'description': 'Run tests', 'type': 'code'}
            ]
            dependency_map = {
                'test': ['setup'],  # test depends on setup
                'run': ['test']     # run depends on test
            }
        """
        # First pass: Create all promises
        name_to_id = {}
        
        for task in tasks:
            task_name = task['name']
            dependencies = None
            
            # Check if this task has dependencies
            if task_name in dependency_map:
                dep_names = dependency_map[task_name]
                # Convert dependency names to IDs (if already created)
                dependencies = [
                    name_to_id[dep_name] 
                    for dep_name in dep_names 
                    if dep_name in name_to_id
                ]
            
            promise_id = self.create_promise(
                chat_id=chat_id,
                message_id=message_id,
                task_description=task['description'],
                task_type=task.get('type', 'code'),
                username=username,
                user_id=user_id,
                priority=task.get('priority', 5),
                metadata=task.get('metadata'),
                dependencies=dependencies if dependencies else None
            )
            
            name_to_id[task_name] = promise_id
        
        self.logger.info(
            f"Created {len(name_to_id)} promises with dependencies: "
            f"{list(name_to_id.keys())}"
        )
        
        return name_to_id
    
    def resume_pending_promises(self):
        """
        Resume all pending promises after restart.
        
        BEST PRACTICE: Call this on application startup to
        recover from crashes or restarts.
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, task_description, status 
                FROM promises 
                WHERE status IN ('pending', 'waiting', 'in_progress')
                ORDER BY created_at ASC
            """)
            
            pending_promises = cursor.fetchall()
        
        resumed_count = 0
        
        for promise_id, task_desc, status in pending_promises:
            if status == 'waiting':
                # Check dependencies
                check_promise_dependencies.schedule(args=(promise_id,))
            else:
                # Execute directly
                execute_promise_by_type.schedule(args=(promise_id,))
            
            resumed_count += 1
        
        if resumed_count > 0:
            self.logger.info(f"Resumed {resumed_count} pending promises")
        
        return resumed_count
    
    def _update_huey_task_id(self, promise_id: int, task_id: str):
        """Store Huey task ID for tracking."""
        # Note: We'd need to add huey_task_id column to promises table
        # For now, just log it
        self.logger.debug(f"Promise {promise_id} has Huey task ID: {task_id}")