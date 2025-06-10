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


def _resolve_workspace_context(chat_id: int) -> Dict[str, Any]:
    """
    Resolve workspace context from chat_id using workspace configuration.
    
    Args:
        chat_id: Telegram chat ID
        
    Returns:
        Dictionary containing workspace information:
        - working_directory: Path to project directory
        - workspace_type: Type of workspace (yudame, psyoptimal, etc.)
        - database_id: Notion database ID if available
        - workspace_name: Human-readable workspace name
    """
    # Default workspace context (Yudame AI)
    default_context = {
        'working_directory': '/Users/valorengels/src/ai',
        'workspace_type': 'yudame',
        'database_id': None,
        'workspace_name': 'Yudame'
    }
    
    try:
        import json
        import os
        
        # Load workspace configuration
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'workspace_config.json')
        if not os.path.exists(config_path):
            logger.warning(f"Workspace config not found at {config_path}, using default")
            return default_context
            
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Look up chat_id in telegram_groups mapping
        chat_id_str = str(chat_id)
        workspace_name = config.get('telegram_groups', {}).get(chat_id_str)
        
        if not workspace_name:
            # Check if it's a DM (use default for DMs)
            logger.info(f"Chat {chat_id} not found in workspace mapping, using default workspace")
            return default_context
        
        # Get workspace details
        workspace = config.get('workspaces', {}).get(workspace_name)
        if not workspace:
            logger.warning(f"Workspace '{workspace_name}' not found in config, using default")
            return default_context
        
        # Extract database_id from notion_db_url and derive workspace_type from working_directory
        from integrations.notion.utils import extract_database_id_from_url, derive_workspace_type_from_directory
        notion_db_url = workspace.get('notion_db_url', '')
        database_id = extract_database_id_from_url(notion_db_url)
        working_directory = workspace.get('working_directory', default_context['working_directory'])
        workspace_type = derive_workspace_type_from_directory(working_directory)
        
        return {
            'working_directory': working_directory,
            'workspace_type': workspace_type,
            'database_id': database_id,
            'workspace_name': workspace_name
        }
        
    except Exception as e:
        logger.error(f"Failed to resolve workspace context for chat {chat_id}: {e}")
        return default_context


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
        
        # Resolve workspace context for this chat
        workspace_context = _resolve_workspace_context(chat_id)
        
        # Determine initial status
        status = 'waiting' if dependencies else 'pending'
        
        # Enhance metadata with workspace context
        enhanced_metadata = metadata.copy() if metadata else {}
        enhanced_metadata.update({
            'workspace_context': workspace_context,
            'dependencies': dependencies or [],
            'task_type': task_type,
            'priority': priority,
            'username': username,
            'user_id': user_id
        })
        
        # Create promise in database
        with get_database_connection() as conn:
            cursor = conn.cursor()
            
            # Need to add more fields to the promises table
            # For now, use the basic create_promise function
            promise_id = db_create_promise(chat_id, message_id, task_description)
            
            # Update with enhanced metadata and status
            cursor.execute("""
                UPDATE promises 
                SET status = ?, metadata = ?
                WHERE id = ?
            """, (status, json.dumps(enhanced_metadata), promise_id))
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
    
    def get_promise_status(self, promise_id: int) -> Optional[Dict[str, Any]]:
        """
        Get current status of a promise.
        
        Returns:
            Dictionary with promise details or None if not found
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT chat_id, message_id, task_description, task_type,
                       status, created_at, completed_at, result_summary,
                       error_message, metadata
                FROM promises
                WHERE id = ?
            """, (promise_id,))
            
            row = cursor.fetchone()
            
        if not row:
            return None
            
        return {
            'id': promise_id,
            'chat_id': row[0],
            'message_id': row[1],
            'task_description': row[2],
            'task_type': row[3],
            'status': row[4],
            'created_at': row[5],
            'completed_at': row[6],
            'result_summary': row[7],
            'error_message': row[8],
            'metadata': json.loads(row[9]) if row[9] else None
        }
    
    def cancel_promise(self, promise_id: int) -> bool:
        """
        Cancel a pending promise.
        
        Returns:
            True if promise was cancelled, False if not found or already completed
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()
            
            # Only cancel if still pending or waiting
            cursor.execute("""
                UPDATE promises
                SET status = 'cancelled',
                    completed_at = ?,
                    error_message = 'Cancelled by user'
                WHERE id = ? AND status IN ('pending', 'waiting')
            """, (datetime.utcnow().isoformat(), promise_id))
            
            success = cursor.rowcount > 0
            conn.commit()
            
        if success:
            self.logger.info(f"Cancelled promise {promise_id}")
        else:
            self.logger.warning(f"Could not cancel promise {promise_id} - may be already completed")
            
        return success
    
    def get_user_promises(self, chat_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent promises for a specific chat.
        
        Args:
            chat_id: Telegram chat ID
            limit: Maximum number of promises to return
            
        Returns:
            List of promise dictionaries
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, message_id, task_description, task_type,
                       status, created_at, completed_at, result_summary,
                       error_message
                FROM promises
                WHERE chat_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (chat_id, limit))
            
            rows = cursor.fetchall()
            
        promises = []
        for row in rows:
            promises.append({
                'id': row[0],
                'message_id': row[1],
                'task_description': row[2],
                'task_type': row[3],
                'status': row[4],
                'created_at': row[5],
                'completed_at': row[6],
                'result_summary': row[7],
                'error_message': row[8]
            })
            
        return promises
    
    def _update_huey_task_id(self, promise_id: int, task_id: str):
        """Store Huey task ID for tracking."""
        # Store in metadata for now since we don't have a dedicated column
        with get_database_connection() as conn:
            cursor = conn.cursor()
            
            # Get current metadata
            cursor.execute("SELECT metadata FROM promises WHERE id = ?", (promise_id,))
            row = cursor.fetchone()
            
            if row:
                metadata = json.loads(row[0]) if row[0] else {}
                metadata['huey_task_id'] = task_id
                
                cursor.execute("""
                    UPDATE promises 
                    SET metadata = ? 
                    WHERE id = ?
                """, (json.dumps(metadata), promise_id))
                conn.commit()
        
        self.logger.debug(f"Promise {promise_id} has Huey task ID: {task_id}")
    
    def _topological_sort(self, nodes: List[str], dependencies: Dict[str, List[str]]) -> List[str]:
        """
        Sort nodes based on dependencies (topological sort).
        
        Args:
            nodes: List of node names
            dependencies: Dict mapping node to list of nodes it depends on
            
        Returns:
            List of nodes in dependency order
        """
        # Build adjacency list (reverse of dependencies)
        graph = {node: [] for node in nodes}
        in_degree = {node: 0 for node in nodes}
        
        for node, deps in dependencies.items():
            if node in graph:
                in_degree[node] = len(deps)
                for dep in deps:
                    if dep in graph:
                        graph[dep].append(node)
        
        # Start with nodes that have no dependencies
        queue = [node for node in nodes if in_degree[node] == 0]
        result = []
        
        while queue:
            current = queue.pop(0)
            result.append(current)
            
            # Reduce in-degree for dependent nodes
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # If we haven't processed all nodes, there's a cycle
        if len(result) != len(nodes):
            # Return original order as fallback
            self.logger.warning("Dependency cycle detected, using original order")
            return nodes
            
        return result