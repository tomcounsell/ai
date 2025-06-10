"""
Team Coordination - Real-Time Team Status Tracking

This module provides real-time awareness of team activities for
intelligent coordination and dependency management.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from .project_context import Task, TeamMember, LiveProjectContext

logger = logging.getLogger(__name__)


@dataclass
class Dependency:
    """Represents a task dependency between team members."""
    id: str
    dependent_task: str
    blocking_task: str
    dependent_assignee: str
    blocking_assignee: str
    description: str
    status: str = "active"  # "active", "resolved", "blocked"
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class TeamUpdate:
    """Represents a team member status update."""
    member_name: str
    update_type: str  # "task_start", "task_complete", "blocker", "status_change"
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    task_id: Optional[str] = None


class TeamStatusTracker:
    """
    Tracks real-time team status for intelligent coordination.
    
    This class enables Valor to:
    - Understand what teammates are working on
    - Identify dependencies and coordination opportunities
    - Provide proactive assistance and updates
    - Coordinate effectively without manual status meetings
    """
    
    def __init__(self, project_context: LiveProjectContext):
        self.project_context = project_context
        self.team_members: Dict[str, TeamMember] = {}
        self.dependencies: List[Dependency] = []
        self.recent_updates: List[TeamUpdate] = []
        self.coordination_opportunities: List[str] = []
    
    async def refresh_team_status(self) -> None:
        """Refresh team status from Notion."""
        logger.debug("🔄 Refreshing team status from Notion")
        
        # TODO: Implement actual Notion API calls to get team data
        # For now, create demo team status
        demo_team = {
            "Tom": TeamMember(
                name="Tom",
                current_tasks=[
                    Task(
                        id="tom_task_1",
                        title="Backend API optimization",
                        status="in_progress",
                        assignee="Tom",
                        priority="high"
                    )
                ],
                status="active",
                last_update=datetime.now() - timedelta(minutes=15)
            ),
            "Sarah": TeamMember(
                name="Sarah",
                current_tasks=[
                    Task(
                        id="sarah_task_1", 
                        title="Frontend component refactoring",
                        status="in_progress",
                        assignee="Sarah",
                        priority="medium"
                    )
                ],
                status="active",
                last_update=datetime.now() - timedelta(minutes=30)
            )
        }
        
        self.team_members = demo_team
        
        # Update project context with team data
        if self.project_context.current_state:
            self.project_context.current_state.team_tasks = {
                name: member.current_tasks 
                for name, member in self.team_members.items()
            }
        
        logger.info(f"✅ Refreshed status for {len(self.team_members)} team members")
    
    def get_live_team_status(self) -> Dict[str, str]:
        """Get real-time view of what everyone is doing."""
        status_map = {}
        
        for name, member in self.team_members.items():
            if member.current_tasks:
                active_task = next(
                    (task for task in member.current_tasks if task.status == "in_progress"),
                    member.current_tasks[0]
                )
                status_map[name] = f"{active_task.title} ({active_task.status})"
            else:
                status_map[name] = "No active tasks"
        
        return status_map
    
    def identify_dependencies(self) -> List[Dependency]:
        """Identify current dependencies between team members."""
        # TODO: Implement intelligent dependency detection
        # This would analyze task relationships, shared components, etc.
        
        # For now, return demo dependencies
        demo_dependencies = [
            Dependency(
                id="dep_1",
                dependent_task="Frontend integration",
                blocking_task="Backend API optimization", 
                dependent_assignee="Sarah",
                blocking_assignee="Tom",
                description="Frontend needs completed API endpoints"
            )
        ]
        
        self.dependencies = demo_dependencies
        return self.dependencies
    
    def check_coordination_opportunities(self) -> List[str]:
        """Identify opportunities for proactive coordination."""
        opportunities = []
        
        # Check for blockers that affect teammates
        if self.project_context.current_state:
            my_blockers = [
                task for task in self.project_context.current_state.my_tasks
                if task.status == "blocked"
            ]
            
            for blocked_task in my_blockers:
                # Check if any team member might be able to help
                for name, member in self.team_members.items():
                    if any("api" in task.title.lower() for task in member.current_tasks):
                        opportunities.append(
                            f"💡 {name} is working on API - could help with blocked task: {blocked_task.title}"
                        )
        
        # Check for dependencies that are ready
        for dep in self.dependencies:
            if dep.status == "active":
                blocking_member = self.team_members.get(dep.blocking_assignee)
                if blocking_member:
                    blocking_task = next(
                        (task for task in blocking_member.current_tasks 
                         if task.id == dep.blocking_task or dep.blocking_task in task.title),
                        None
                    )
                    if blocking_task and blocking_task.status == "completed":
                        opportunities.append(
                            f"🎉 {dep.blocking_assignee} completed {dep.blocking_task} - unblocks {dep.dependent_assignee}"
                        )
        
        # Check for similar work that could be coordinated
        my_current_work = []
        if self.project_context.current_state:
            my_current_work = [
                task.title.lower() for task in self.project_context.current_state.my_tasks
                if task.status == "in_progress"
            ]
        
        for name, member in self.team_members.items():
            for task in member.current_tasks:
                if task.status == "in_progress":
                    # Simple keyword matching for coordination opportunities
                    for my_task in my_current_work:
                        if any(keyword in my_task and keyword in task.title.lower() 
                               for keyword in ["auth", "api", "database", "frontend", "backend"]):
                            opportunities.append(
                                f"🤝 Coordinate with {name} - both working on similar: {task.title}"
                            )
        
        self.coordination_opportunities = opportunities
        return opportunities
    
    def get_team_updates_since(self, since: datetime) -> List[TeamUpdate]:
        """Get team updates since a specific time."""
        return [
            update for update in self.recent_updates
            if update.timestamp >= since
        ]
    
    def add_team_update(self, member_name: str, update_type: str, 
                       message: str, task_id: Optional[str] = None) -> None:
        """Add a team update to the activity feed."""
        update = TeamUpdate(
            member_name=member_name,
            update_type=update_type,
            message=message,
            task_id=task_id
        )
        
        self.recent_updates.insert(0, update)
        
        # Keep only last 50 updates
        self.recent_updates = self.recent_updates[:50]
        
        logger.info(f"📢 Team update: {member_name} - {message}")
    
    def get_member_status(self, member_name: str) -> Optional[str]:
        """Get specific team member's current status."""
        member = self.team_members.get(member_name)
        if not member:
            return None
        
        if not member.current_tasks:
            return f"{member_name} has no active tasks"
        
        active_tasks = [task for task in member.current_tasks if task.status == "in_progress"]
        if active_tasks:
            task = active_tasks[0]
            return f"{member_name} is working on: {task.title} ({task.priority} priority)"
        
        return f"{member_name} has {len(member.current_tasks)} pending tasks"
    
    def check_if_member_blocked(self, member_name: str) -> Optional[str]:
        """Check if a team member is currently blocked."""
        member = self.team_members.get(member_name)
        if not member:
            return None
        
        blocked_tasks = [task for task in member.current_tasks if task.status == "blocked"]
        if blocked_tasks:
            task = blocked_tasks[0]
            blocker_info = task.blockers[0] if task.blockers else "Unknown blocker"
            return f"{member_name} is blocked on: {task.title} - {blocker_info}"
        
        return None
    
    def find_coordination_for_task(self, task_description: str) -> List[str]:
        """Find team members who might help with a specific task."""
        suggestions = []
        task_lower = task_description.lower()
        
        # Simple keyword matching for relevant expertise
        expertise_keywords = {
            "Tom": ["backend", "api", "database", "server"],
            "Sarah": ["frontend", "ui", "react", "component"],
            "Valor": ["integration", "automation", "devops", "claude"]
        }
        
        for member_name, keywords in expertise_keywords.items():
            if member_name == "Valor":  # Don't suggest myself
                continue
                
            if any(keyword in task_lower for keyword in keywords):
                member_status = self.get_member_status(member_name)
                if member_status and "no active tasks" in member_status:
                    suggestions.append(f"💡 {member_name} might be available to help (expertise: {', '.join(keywords)})")
                else:
                    suggestions.append(f"🤔 {member_name} has relevant expertise but currently busy")
        
        return suggestions
    
    def get_team_summary(self) -> str:
        """Get a comprehensive team status summary."""
        if not self.team_members:
            return "👥 Team status not yet loaded"
        
        summary_lines = ["👥 **Team Status Summary:**"]
        
        # Active team members
        active_count = sum(1 for member in self.team_members.values() if member.status == "active")
        summary_lines.append(f"• **Active Members:** {active_count}/{len(self.team_members)}")
        
        # Current work
        for name, member in self.team_members.items():
            active_tasks = [task for task in member.current_tasks if task.status == "in_progress"]
            if active_tasks:
                task = active_tasks[0]
                summary_lines.append(f"• **{name}:** {task.title} ({task.priority})")
            else:
                summary_lines.append(f"• **{name}:** Available")
        
        # Dependencies
        active_deps = [dep for dep in self.dependencies if dep.status == "active"]
        if active_deps:
            summary_lines.append(f"• **Dependencies:** {len(active_deps)} active")
        
        # Coordination opportunities
        if self.coordination_opportunities:
            summary_lines.append(f"• **Opportunities:** {len(self.coordination_opportunities)} identified")
        
        return "\n".join(summary_lines)
    
    async def notify_team_of_update(self, update_message: str) -> None:
        """Notify team of important updates (placeholder for future implementation)."""
        # TODO: Implement actual team notification (Slack, email, etc.)
        logger.info(f"📢 Would notify team: {update_message}")
        
        # Add to team updates feed
        self.add_team_update("Valor", "status_update", update_message)