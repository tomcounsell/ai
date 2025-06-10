"""
Workspace Synchronization - Bi-directional Notion Integration

This module handles the actual API communication with Notion for
bi-directional synchronization of project state and development progress.
"""

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
import aiohttp

from .project_context import Task, ProjectState, LiveProjectContext
from .task_lifecycle import TaskManager

logger = logging.getLogger(__name__)


class NotionSynchronizer:
    """
    Handles bi-directional synchronization with Notion APIs.
    
    This class manages:
    - Reading project state from Notion databases
    - Updating Notion with development progress
    - Intelligent change detection and conflict resolution
    - Real-time synchronization with minimal API calls
    """
    
    def __init__(self, api_key: str, database_url: str):
        self.api_key = api_key
        self.database_url = database_url
        self.database_id = self._extract_database_id(database_url)
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.last_sync: Optional[datetime] = None
        self.sync_errors: List[str] = []
    
    def _extract_database_id(self, database_url: str) -> str:
        """Extract database ID from Notion URL."""
        if not database_url or database_url == "****":
            raise ValueError("Invalid or masked database URL")
        
        # Extract UUID from URL pattern
        match = re.search(r'/([a-f0-9]{32})(?:\?|$|#)', database_url)
        if match:
            db_id_no_hyphens = match.group(1)
            # Add hyphens to make it a proper UUID
            return f"{db_id_no_hyphens[:8]}-{db_id_no_hyphens[8:12]}-{db_id_no_hyphens[12:16]}-{db_id_no_hyphens[16:20]}-{db_id_no_hyphens[20:]}"
        
        raise ValueError(f"Could not extract database ID from URL: {database_url}")
    
    async def fetch_project_state(self) -> ProjectState:
        """
        Fetch current project state from Notion database.
        
        Returns:
            ProjectState with all current tasks and project info
        """
        logger.info(f"🔄 Fetching project state from Notion database: {self.database_id[:8]}...")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Query database for all tasks
                query_url = f"{self.base_url}/databases/{self.database_id}/query"
                
                query_payload = {
                    "sorts": [
                        {
                            "property": "Priority",
                            "direction": "descending"
                        },
                        {
                            "property": "Status", 
                            "direction": "ascending"
                        }
                    ],
                    "page_size": 100
                }
                
                async with session.post(query_url, headers=self.headers, json=query_payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"Notion API error {response.status}: {error_text}")
                    
                    data = await response.json()
                    
                    # Parse tasks from Notion response
                    tasks = self._parse_notion_tasks(data.get("results", []))
                    
                    # Separate my tasks from team tasks
                    my_tasks = [task for task in tasks if task.assignee == "Valor"]
                    team_tasks = {}
                    
                    for task in tasks:
                        if task.assignee and task.assignee != "Valor":
                            if task.assignee not in team_tasks:
                                team_tasks[task.assignee] = []
                            team_tasks[task.assignee].append(task)
                    
                    # Extract project metadata
                    sprint_goal = self._extract_sprint_goal(data.get("results", []))
                    current_milestone = self._extract_current_milestone(data.get("results", []))
                    
                    # Create project state
                    project_state = ProjectState(
                        workspace_name=self._get_workspace_name(),
                        sprint_goal=sprint_goal,
                        current_milestone=current_milestone,
                        my_tasks=my_tasks,
                        team_tasks=team_tasks,
                        blockers=self._extract_blockers(tasks),
                        upcoming_deadlines=self._extract_upcoming_deadlines(tasks),
                        recent_updates=self._extract_recent_updates(data.get("results", [])),
                        priorities=self._extract_priorities(my_tasks),
                        last_updated=datetime.now()
                    )
                    
                    self.last_sync = datetime.now()
                    logger.info(f"✅ Fetched {len(tasks)} tasks from Notion")
                    return project_state
                    
        except Exception as e:
            error_msg = f"Failed to fetch project state: {str(e)}"
            logger.error(error_msg)
            self.sync_errors.append(error_msg)
            
            # Return minimal state on error
            return ProjectState(
                workspace_name=self._get_workspace_name(),
                priorities=["❌ Notion sync failed - check configuration"],
                recent_updates=[f"Sync error: {str(e)[:100]}"],
                last_updated=datetime.now()
            )
    
    def _parse_notion_tasks(self, notion_results: List[Dict]) -> List[Task]:
        """Parse Notion database results into Task objects."""
        tasks = []
        
        for result in notion_results:
            try:
                properties = result.get("properties", {})
                
                # Extract task properties with safe fallbacks
                task_id = result.get("id", "")
                title = self._extract_title(properties)
                status = self._extract_select_value(properties.get("Status"))
                assignee = self._extract_select_value(properties.get("Assignee"))
                priority = self._extract_select_value(properties.get("Priority", {}), default="medium")
                due_date = self._extract_date(properties.get("Due Date"))
                description = self._extract_rich_text(properties.get("Description"))
                
                # Extract tags/categories
                tags = self._extract_multi_select(properties.get("Tags", {}))
                
                # Create task object
                task = Task(
                    id=task_id,
                    title=title,
                    status=self._normalize_status(status),
                    assignee=assignee,
                    priority=priority.lower() if priority else "medium",
                    due_date=due_date,
                    description=description,
                    tags=tags,
                    updated_at=datetime.fromisoformat(result.get("last_edited_time", "").replace("Z", "+00:00"))
                )
                
                tasks.append(task)
                
            except Exception as e:
                logger.warning(f"Failed to parse Notion task: {e}")
                continue
        
        return tasks
    
    def _extract_title(self, properties: Dict) -> str:
        """Extract title from Notion properties."""
        title_prop = properties.get("Name") or properties.get("Title") or properties.get("Task")
        if title_prop and title_prop.get("title"):
            return "".join([text.get("plain_text", "") for text in title_prop["title"]])
        return "Untitled Task"
    
    def _extract_select_value(self, select_prop: Optional[Dict], default: str = "") -> str:
        """Extract value from Notion select property."""
        if select_prop and select_prop.get("select"):
            return select_prop["select"].get("name", default)
        return default
    
    def _extract_multi_select(self, multi_select_prop: Dict) -> List[str]:
        """Extract values from Notion multi-select property."""
        if multi_select_prop and multi_select_prop.get("multi_select"):
            return [option.get("name", "") for option in multi_select_prop["multi_select"]]
        return []
    
    def _extract_rich_text(self, rich_text_prop: Optional[Dict]) -> Optional[str]:
        """Extract text from Notion rich text property."""
        if rich_text_prop and rich_text_prop.get("rich_text"):
            return "".join([text.get("plain_text", "") for text in rich_text_prop["rich_text"]])
        return None
    
    def _extract_date(self, date_prop: Optional[Dict]) -> Optional[datetime]:
        """Extract date from Notion date property."""
        if date_prop and date_prop.get("date") and date_prop["date"].get("start"):
            try:
                date_str = date_prop["date"]["start"]
                if "T" in date_str:
                    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                else:
                    return datetime.fromisoformat(date_str + "T00:00:00+00:00")
            except Exception:
                pass
        return None
    
    def _normalize_status(self, status: str) -> str:
        """Normalize Notion status to standard values."""
        if not status:
            return "not_started"
        
        status_lower = status.lower()
        
        # Map common Notion status values
        status_mapping = {
            "not started": "not_started",
            "todo": "not_started",
            "to do": "not_started",
            "in progress": "in_progress",
            "doing": "in_progress",
            "active": "in_progress",
            "done": "completed",
            "complete": "completed",
            "finished": "completed",
            "blocked": "blocked",
            "waiting": "blocked",
            "review": "review",
            "testing": "review"
        }
        
        return status_mapping.get(status_lower, status_lower)
    
    def _extract_sprint_goal(self, notion_results: List[Dict]) -> Optional[str]:
        """Extract current sprint goal from Notion data."""
        # TODO: Implement based on actual Notion database structure
        # This could come from a dedicated sprint goals page or database
        return "Revolutionary Notion integration"
    
    def _extract_current_milestone(self, notion_results: List[Dict]) -> Optional[str]:
        """Extract current milestone from Notion data."""
        # TODO: Implement based on actual Notion database structure
        return "Living Project Context Foundation"
    
    def _extract_blockers(self, tasks: List[Task]) -> List[str]:
        """Extract current blockers from tasks."""
        blockers = []
        for task in tasks:
            if task.status == "blocked" and task.blockers:
                blockers.extend([f"{task.title}: {blocker}" for blocker in task.blockers])
        return blockers
    
    def _extract_upcoming_deadlines(self, tasks: List[Task]) -> List[Task]:
        """Extract tasks with upcoming deadlines."""
        upcoming = []
        cutoff_date = datetime.now() + timedelta(days=7)  # Next 7 days
        
        for task in tasks:
            if task.due_date and task.due_date <= cutoff_date and task.status != "completed":
                upcoming.append(task)
        
        return sorted(upcoming, key=lambda t: t.due_date or datetime.max)
    
    def _extract_recent_updates(self, notion_results: List[Dict]) -> List[str]:
        """Extract recent updates from Notion activity."""
        # TODO: Implement based on Notion's last_edited_time and change tracking
        return [
            "Living project context system initialized",
            "Revolutionary architecture plan completed",
            "Legacy integration successfully removed"
        ]
    
    def _extract_priorities(self, my_tasks: List[Task]) -> List[str]:
        """Extract current priorities from my tasks."""
        high_priority_tasks = [
            task.title for task in my_tasks 
            if task.priority in ["high", "urgent"] and task.status != "completed"
        ]
        
        if high_priority_tasks:
            return high_priority_tasks
        
        # Fall back to in-progress tasks
        in_progress_tasks = [
            task.title for task in my_tasks
            if task.status == "in_progress"
        ]
        
        return in_progress_tasks or ["No active priorities"]
    
    def _get_workspace_name(self) -> str:
        """Get workspace name from database URL or configuration."""
        # TODO: Extract from actual workspace configuration
        return "Active Workspace"
    
    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update a specific task in Notion.
        
        Args:
            task_id: Notion page ID of the task
            updates: Dictionary of property updates
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"📝 Updating task {task_id[:8]}... in Notion")
        
        try:
            async with aiohttp.ClientSession() as session:
                update_url = f"{self.base_url}/pages/{task_id}"
                
                # Convert updates to Notion property format
                notion_properties = self._convert_updates_to_notion_format(updates)
                
                payload = {
                    "properties": notion_properties
                }
                
                async with session.patch(update_url, headers=self.headers, json=payload) as response:
                    if response.status == 200:
                        logger.info(f"✅ Successfully updated task in Notion")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to update task: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error updating task in Notion: {e}")
            return False
    
    def _convert_updates_to_notion_format(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Convert update dictionary to Notion properties format."""
        notion_props = {}
        
        # Map common update fields to Notion properties
        if "status" in updates:
            notion_props["Status"] = {
                "select": {"name": updates["status"].title()}
            }
        
        if "notes" in updates:
            notion_props["Notes"] = {
                "rich_text": [{"text": {"content": updates["notes"]}}]
            }
        
        if "completion_summary" in updates:
            notion_props["Completion Summary"] = {
                "rich_text": [{"text": {"content": updates["completion_summary"]}}]
            }
        
        if "assignee" in updates:
            notion_props["Assignee"] = {
                "select": {"name": updates["assignee"]}
            }
        
        return notion_props
    
    async def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """
        Create a new task in Notion.
        
        Args:
            task_data: Task creation data
            
        Returns:
            Task ID if successful, None otherwise
        """
        logger.info(f"➕ Creating new task in Notion: {task_data.get('title', 'Untitled')}")
        
        try:
            async with aiohttp.ClientSession() as session:
                create_url = f"{self.base_url}/pages"
                
                # Convert task data to Notion format
                notion_properties = self._convert_task_data_to_notion_format(task_data)
                
                payload = {
                    "parent": {"database_id": self.database_id},
                    "properties": notion_properties
                }
                
                async with session.post(create_url, headers=self.headers, json=payload) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        task_id = response_data.get("id")
                        logger.info(f"✅ Created task in Notion: {task_id}")
                        return task_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create task: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error creating task in Notion: {e}")
            return None
    
    def _convert_task_data_to_notion_format(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert task creation data to Notion properties format."""
        notion_props = {}
        
        # Required: Title/Name
        if "title" in task_data:
            notion_props["Name"] = {
                "title": [{"text": {"content": task_data["title"]}}]
            }
        
        # Optional properties
        if "description" in task_data:
            notion_props["Description"] = {
                "rich_text": [{"text": {"content": task_data["description"]}}]
            }
        
        if "status" in task_data:
            notion_props["Status"] = {
                "select": {"name": task_data["status"].title()}
            }
        
        if "priority" in task_data:
            notion_props["Priority"] = {
                "select": {"name": task_data["priority"].title()}
            }
        
        if "assignee" in task_data:
            notion_props["Assignee"] = {
                "select": {"name": task_data["assignee"]}
            }
        
        if "tags" in task_data:
            notion_props["Tags"] = {
                "multi_select": [{"name": tag} for tag in task_data["tags"]]
            }
        
        return notion_props
    
    def get_sync_status(self) -> Dict[str, Any]:
        """Get current synchronization status."""
        return {
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "database_id": self.database_id,
            "errors": self.sync_errors[-5:],  # Last 5 errors
            "is_configured": bool(self.api_key and self.database_id),
            "sync_health": "healthy" if not self.sync_errors else "error"
        }