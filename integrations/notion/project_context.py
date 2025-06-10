"""
Living Project Context - Always-On Project Awareness

This module maintains persistent, real-time awareness of project state,
replacing reactive querying with continuous context management.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Represents a project task with all relevant context."""
    id: str
    title: str
    status: str  # "not_started", "in_progress", "completed", "blocked"
    assignee: Optional[str] = None
    priority: str = "medium"  # "low", "medium", "high", "urgent"
    due_date: Optional[datetime] = None
    description: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class TeamMember:
    """Represents a team member and their current work."""
    name: str
    current_tasks: List[Task] = field(default_factory=list)
    status: str = "active"  # "active", "offline", "blocked"
    last_update: datetime = field(default_factory=datetime.now)


@dataclass
class ProjectState:
    """Complete project state snapshot."""
    workspace_name: str
    sprint_goal: Optional[str] = None
    current_milestone: Optional[str] = None
    my_tasks: List[Task] = field(default_factory=list)
    team_tasks: Dict[str, List[Task]] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)
    upcoming_deadlines: List[Task] = field(default_factory=list)
    recent_updates: List[str] = field(default_factory=list)
    priorities: List[str] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)


class LiveProjectContext:
    """
    Maintains always-current project state for intelligent development workflow.
    
    This class provides the foundation for Valor's project awareness, enabling:
    - Instant context for any development decision
    - Automatic progress tracking and updates
    - Real-time team coordination awareness
    - Intelligent work prioritization
    """
    
    def __init__(self):
        self.current_state: Optional[ProjectState] = None
        self.workspace_name: Optional[str] = None
        self.notion_api_key: Optional[str] = None
        self.database_url: Optional[str] = None
        self.refresh_interval: int = 300  # 5 minutes
        self._refresh_task: Optional[asyncio.Task] = None
        self._last_refresh: Optional[datetime] = None
        
    async def initialize(self, workspace_name: str) -> None:
        """
        Initialize living context for a specific workspace.
        
        Args:
            workspace_name: Name of the workspace to track
        """
        logger.info(f"🚀 Initializing living project context for {workspace_name}")
        
        self.workspace_name = workspace_name
        
        # Load workspace configuration
        await self._load_workspace_config()
        
        # Initial context refresh
        await self.refresh_context()
        
        # Start continuous refresh
        self._start_continuous_refresh()
        
        logger.info(f"✅ Living project context active for {workspace_name}")
    
    async def _load_workspace_config(self) -> None:
        """Load workspace configuration from config file."""
        try:
            config_path = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
            
            if not config_path.exists():
                logger.warning(f"Workspace config not found at {config_path}")
                return
                
            with open(config_path) as f:
                config = json.load(f)
            
            workspace_config = config.get("workspaces", {}).get(self.workspace_name)
            if not workspace_config:
                logger.warning(f"No configuration found for workspace {self.workspace_name}")
                return
            
            # Extract Notion database URL
            self.database_url = workspace_config.get("notion_db_url")
            if self.database_url == "****":
                self.database_url = None
                logger.warning(f"Notion database URL masked for {self.workspace_name}")
            
            # Load API key from environment
            import os
            self.notion_api_key = os.getenv("NOTION_API_KEY")
            
            logger.info(f"📁 Loaded config for {self.workspace_name}")
            logger.info(f"   Database: {'✅ Available' if self.database_url else '❌ Not configured'}")
            logger.info(f"   API Key: {'✅ Available' if self.notion_api_key else '❌ Not configured'}")
            
        except Exception as e:
            logger.error(f"Failed to load workspace config: {e}")
    
    async def refresh_context(self) -> None:
        """Refresh project context from Notion."""
        if not self.database_url or not self.notion_api_key:
            logger.warning("Cannot refresh context - Notion not configured")
            # Create minimal state for development
            self.current_state = ProjectState(
                workspace_name=self.workspace_name or "Unknown",
                priorities=["🚧 Notion integration setup needed"],
                recent_updates=["Revolutionary living context system initialized"]
            )
            return
        
        try:
            logger.debug(f"🔄 Refreshing project context for {self.workspace_name}")
            
            # TODO: Implement actual Notion API calls
            # For now, create a realistic demo state
            demo_tasks = [
                Task(
                    id="task_1",
                    title="Implement living project context",
                    status="in_progress",
                    assignee="Valor",
                    priority="high",
                    description="Build revolutionary always-on project awareness system"
                ),
                Task(
                    id="task_2", 
                    title="Integrate development workflow",
                    status="not_started",
                    assignee="Valor",
                    priority="medium",
                    description="Connect Claude Code with Notion for seamless updates"
                )
            ]
            
            self.current_state = ProjectState(
                workspace_name=self.workspace_name,
                sprint_goal="Revolutionary Notion integration",
                my_tasks=demo_tasks,
                priorities=[
                    "Complete living project context foundation",
                    "Implement development workflow integration",
                    "Add team coordination features"
                ],
                recent_updates=[
                    f"Living context initialized for {self.workspace_name}",
                    "Revolutionary architecture plan completed",
                    "Legacy Notion integration removed"
                ],
                last_updated=datetime.now()
            )
            
            self._last_refresh = datetime.now()
            logger.info(f"✅ Context refreshed for {self.workspace_name}")
            
        except Exception as e:
            logger.error(f"Failed to refresh context: {e}")
    
    def _start_continuous_refresh(self) -> None:
        """Start background task for continuous context refresh."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        
        self._refresh_task = asyncio.create_task(self._continuous_refresh_loop())
    
    async def _continuous_refresh_loop(self) -> None:
        """Background loop for keeping context fresh."""
        while True:
            try:
                await asyncio.sleep(self.refresh_interval)
                await self.refresh_context()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in continuous refresh: {e}")
                await asyncio.sleep(60)  # Wait before retrying
    
    def get_current_focus(self) -> str:
        """Get what I should be working on right now."""
        if not self.current_state:
            return "🚧 Project context not yet initialized"
        
        # Find highest priority task assigned to me
        my_high_priority_tasks = [
            task for task in self.current_state.my_tasks 
            if task.priority in ["urgent", "high"] and task.status != "completed"
        ]
        
        if my_high_priority_tasks:
            task = my_high_priority_tasks[0]
            return f"🎯 **{task.title}** ({task.priority} priority)\n{task.description or 'No description'}"
        
        # Fall back to any in-progress task
        in_progress_tasks = [
            task for task in self.current_state.my_tasks
            if task.status == "in_progress"
        ]
        
        if in_progress_tasks:
            task = in_progress_tasks[0]
            return f"⚡ Continue: **{task.title}**\n{task.description or 'No description'}"
        
        # Fall back to next unstarted task
        unstarted_tasks = [
            task for task in self.current_state.my_tasks
            if task.status == "not_started"
        ]
        
        if unstarted_tasks:
            task = unstarted_tasks[0]
            return f"🚀 Start: **{task.title}**\n{task.description or 'No description'}"
        
        return "✅ No pending tasks - check for new work or help teammates"
    
    def get_team_status(self) -> str:
        """Get real-time view of what team members are doing."""
        if not self.current_state:
            return "🚧 Team status not yet available"
        
        if not self.current_state.team_tasks:
            return "👥 Team status will be available once connected to Notion"
        
        status_lines = ["👥 **Team Status:**"]
        for member, tasks in self.current_state.team_tasks.items():
            active_tasks = [t for t in tasks if t.status == "in_progress"]
            if active_tasks:
                status_lines.append(f"• **{member}**: {active_tasks[0].title}")
            else:
                status_lines.append(f"• **{member}**: No active tasks")
        
        return "\n".join(status_lines)
    
    def get_blockers_and_dependencies(self) -> List[str]:
        """Get current impediments and waiting-for items."""
        if not self.current_state:
            return ["🚧 Blockers not yet loaded"]
        
        all_blockers = []
        
        # Add project-level blockers
        all_blockers.extend(self.current_state.blockers)
        
        # Add task-specific blockers
        for task in self.current_state.my_tasks:
            if task.blockers:
                all_blockers.extend([f"{task.title}: {blocker}" for blocker in task.blockers])
        
        return all_blockers if all_blockers else ["✅ No current blockers"]
    
    def get_context_for_decision(self, technical_question: str) -> str:
        """Get project context relevant to a technical decision."""
        if not self.current_state:
            return "🚧 Project context loading..."
        
        context_parts = []
        
        # Current sprint goal
        if self.current_state.sprint_goal:
            context_parts.append(f"**Sprint Goal:** {self.current_state.sprint_goal}")
        
        # Current focus
        current_focus = self.get_current_focus()
        context_parts.append(f"**Current Focus:** {current_focus}")
        
        # Relevant priorities
        if self.current_state.priorities:
            context_parts.append(f"**Priorities:** {', '.join(self.current_state.priorities)}")
        
        # Blockers that might affect decision
        blockers = self.get_blockers_and_dependencies()
        if blockers and blockers != ["✅ No current blockers"]:
            context_parts.append(f"**Consider Blockers:** {', '.join(blockers[:3])}")
        
        return "\n".join(context_parts)
    
    def check_for_updates(self) -> List[str]:
        """Get what's changed since last check."""
        if not self.current_state:
            return ["🚧 Updates not yet available"]
        
        # For now, return recent updates
        # TODO: Implement actual change tracking
        return self.current_state.recent_updates[-5:]  # Last 5 updates
    
    def is_context_fresh(self) -> bool:
        """Check if context is still fresh (< 5 minutes old)."""
        if not self._last_refresh:
            return False
        
        return datetime.now() - self._last_refresh < timedelta(minutes=5)
    
    def get_context_age(self) -> str:
        """Get human-readable age of current context."""
        if not self._last_refresh:
            return "Never refreshed"
        
        age = datetime.now() - self._last_refresh
        if age.seconds < 60:
            return f"{age.seconds}s ago"
        elif age.seconds < 3600:
            return f"{age.seconds // 60}m ago"
        else:
            return f"{age.seconds // 3600}h ago"
    
    async def shutdown(self) -> None:
        """Clean shutdown of the living context system."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        
        logger.info(f"🛑 Living project context shut down for {self.workspace_name}")