"""Valor Context Management

This module contains the ValorContext Pydantic model and related utilities
for managing conversation context, user state, and session data.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, validator


class MessageEntry(BaseModel):
    """Individual message in conversation history."""
    
    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique message ID")
    role: str = Field(..., description="Message role (user, assistant, system)")
    content: str = Field(..., description="Message content")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional message metadata")
    token_count: Optional[int] = Field(None, description="Estimated token count for this message")
    importance_score: float = Field(default=1.0, description="Importance score for context management")
    
    @validator('role')
    def validate_role(cls, v):
        """Validate message role."""
        allowed_roles = {'user', 'assistant', 'system', 'tool'}
        if v not in allowed_roles:
            raise ValueError(f"Role must be one of {allowed_roles}")
        return v
    
    @validator('importance_score')
    def validate_importance_score(cls, v):
        """Validate importance score range."""
        if not 0.0 <= v <= 10.0:
            raise ValueError("Importance score must be between 0.0 and 10.0")
        return v
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ToolUsage(BaseModel):
    """Record of tool usage in conversation."""
    
    tool_name: str = Field(..., description="Name of the tool used")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Parameters passed to tool")
    result_summary: Optional[str] = Field(None, description="Summary of tool result")
    execution_time: Optional[float] = Field(None, description="Tool execution time in seconds")
    success: bool = Field(default=True, description="Whether tool execution was successful")
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class WorkspaceInfo(BaseModel):
    """Information about the current workspace."""
    
    name: str = Field(..., description="Workspace name")
    type: Optional[str] = Field(None, description="Workspace type (project, personal, etc.)")
    path: Optional[str] = Field(None, description="Workspace file system path")
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Workspace-specific config")
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class UserPreferences(BaseModel):
    """User preferences and settings."""
    
    communication_style: str = Field(default="balanced", description="Preferred communication style")
    technical_level: str = Field(default="intermediate", description="User's technical expertise level")
    preferred_tools: List[str] = Field(default_factory=list, description="User's preferred tools")
    notification_settings: Dict[str, bool] = Field(default_factory=dict, description="Notification preferences")
    custom_settings: Dict[str, Any] = Field(default_factory=dict, description="Custom user settings")
    
    @validator('communication_style')
    def validate_communication_style(cls, v):
        """Validate communication style."""
        allowed_styles = {'concise', 'balanced', 'detailed', 'technical'}
        if v not in allowed_styles:
            raise ValueError(f"Communication style must be one of {allowed_styles}")
        return v
    
    @validator('technical_level')
    def validate_technical_level(cls, v):
        """Validate technical level."""
        allowed_levels = {'beginner', 'intermediate', 'advanced', 'expert'}
        if v not in allowed_levels:
            raise ValueError(f"Technical level must be one of {allowed_levels}")
        return v


class ContextMetrics(BaseModel):
    """Context usage and performance metrics."""
    
    total_messages: int = Field(default=0, description="Total number of messages")
    total_tokens: int = Field(default=0, description="Total estimated tokens used")
    tools_used_count: int = Field(default=0, description="Number of tool invocations")
    average_response_time: Optional[float] = Field(None, description="Average response time in seconds")
    context_compressions: int = Field(default=0, description="Number of context compressions performed")
    last_compression: Optional[datetime] = Field(None, description="Last context compression timestamp")
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ValorContext(BaseModel):
    """
    Comprehensive context model for Valor agent conversations.
    
    This model maintains all necessary state for context-aware conversations,
    including message history, user preferences, workspace information,
    and session metadata.
    """
    
    # Core identification
    chat_id: str = Field(..., description="Unique conversation identifier")
    user_name: str = Field(..., description="User name or identifier")
    session_id: str = Field(default_factory=lambda: str(uuid4()), description="Unique session ID")
    
    # Workspace and environment
    workspace: str = Field(default="default", description="Current workspace name")
    workspace_info: Optional[WorkspaceInfo] = Field(None, description="Detailed workspace information")
    
    # Conversation state
    message_history: List[MessageEntry] = Field(default_factory=list, description="Complete message history")
    active_tools: List[str] = Field(default_factory=list, description="Currently active/available tools")
    tool_usage_history: List[ToolUsage] = Field(default_factory=list, description="History of tool usage")
    
    # User and session management
    user_preferences: UserPreferences = Field(default_factory=UserPreferences, description="User preferences")
    session_metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional session metadata")
    
    # Context management
    context_metrics: ContextMetrics = Field(default_factory=ContextMetrics, description="Context usage metrics")
    important_messages: List[str] = Field(default_factory=list, description="IDs of important messages to preserve")
    context_summary: Optional[str] = Field(None, description="Summary of conversation for context management")
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
    
    def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        importance_score: float = 1.0,
        token_count: Optional[int] = None
    ) -> MessageEntry:
        """
        Add a new message to the conversation history.
        
        Args:
            role: Message role (user, assistant, system, tool)
            content: Message content
            metadata: Optional message metadata
            importance_score: Importance score for context management (0.0-10.0)
            token_count: Estimated token count for the message
            
        Returns:
            MessageEntry: The created message entry
        """
        message = MessageEntry(
            role=role,
            content=content,
            metadata=metadata or {},
            importance_score=importance_score,
            token_count=token_count
        )
        
        self.message_history.append(message)
        self.last_activity = datetime.now(timezone.utc)
        self.last_updated = datetime.now(timezone.utc)
        
        # Update metrics
        self.context_metrics.total_messages += 1
        if token_count:
            self.context_metrics.total_tokens += token_count
        
        return message
    
    def add_tool_usage(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result_summary: Optional[str] = None,
        execution_time: Optional[float] = None,
        success: bool = True
    ) -> ToolUsage:
        """
        Record tool usage in the context.
        
        Args:
            tool_name: Name of the tool used
            parameters: Parameters passed to the tool
            result_summary: Summary of the tool result
            execution_time: Tool execution time in seconds
            success: Whether the tool execution was successful
            
        Returns:
            ToolUsage: The created tool usage record
        """
        tool_usage = ToolUsage(
            tool_name=tool_name,
            parameters=parameters,
            result_summary=result_summary,
            execution_time=execution_time,
            success=success
        )
        
        self.tool_usage_history.append(tool_usage)
        self.context_metrics.tools_used_count += 1
        self.last_updated = datetime.now(timezone.utc)
        
        # Add tool to active tools if not already present
        if tool_name not in self.active_tools:
            self.active_tools.append(tool_name)
        
        return tool_usage
    
    def mark_message_important(self, message_id: str) -> bool:
        """
        Mark a message as important for context preservation.
        
        Args:
            message_id: ID of the message to mark as important
            
        Returns:
            bool: True if message was found and marked, False otherwise
        """
        # Find the message
        message_found = False
        for message in self.message_history:
            if message.id == message_id:
                message.importance_score = max(message.importance_score, 8.0)
                message_found = True
                break
        
        if message_found and message_id not in self.important_messages:
            self.important_messages.append(message_id)
            self.last_updated = datetime.now(timezone.utc)
        
        return message_found
    
    def get_recent_messages(self, count: int = 20) -> List[MessageEntry]:
        """
        Get the most recent messages from the conversation.
        
        Args:
            count: Number of recent messages to return
            
        Returns:
            List[MessageEntry]: Recent messages
        """
        return self.message_history[-count:] if self.message_history else []
    
    def get_important_messages(self) -> List[MessageEntry]:
        """
        Get all messages marked as important.
        
        Returns:
            List[MessageEntry]: Important messages
        """
        important_msgs = []
        for message in self.message_history:
            if message.id in self.important_messages or message.importance_score >= 7.0:
                important_msgs.append(message)
        return important_msgs
    
    def get_messages_by_role(self, role: str) -> List[MessageEntry]:
        """
        Get all messages from a specific role.
        
        Args:
            role: Message role to filter by
            
        Returns:
            List[MessageEntry]: Messages from the specified role
        """
        return [msg for msg in self.message_history if msg.role == role]
    
    def get_tool_usage_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Get a summary of tool usage in this context.
        
        Returns:
            Dict[str, Dict[str, Any]]: Tool usage summary by tool name
        """
        summary = {}
        for usage in self.tool_usage_history:
            tool_name = usage.tool_name
            if tool_name not in summary:
                summary[tool_name] = {
                    'usage_count': 0,
                    'total_execution_time': 0.0,
                    'success_rate': 0.0,
                    'last_used': None
                }
            
            summary[tool_name]['usage_count'] += 1
            if usage.execution_time:
                summary[tool_name]['total_execution_time'] += usage.execution_time
            
            # Update last used timestamp
            if (summary[tool_name]['last_used'] is None or 
                usage.timestamp > summary[tool_name]['last_used']):
                summary[tool_name]['last_used'] = usage.timestamp
        
        # Calculate success rates
        for tool_name in summary:
            tool_usages = [u for u in self.tool_usage_history if u.tool_name == tool_name]
            successful_usages = len([u for u in tool_usages if u.success])
            summary[tool_name]['success_rate'] = successful_usages / len(tool_usages) if tool_usages else 0.0
        
        return summary
    
    def update_workspace(self, workspace_name: str, workspace_info: Optional[WorkspaceInfo] = None) -> None:
        """
        Update the current workspace.
        
        Args:
            workspace_name: New workspace name
            workspace_info: Optional detailed workspace information
        """
        self.workspace = workspace_name
        if workspace_info:
            self.workspace_info = workspace_info
        self.last_updated = datetime.now(timezone.utc)
    
    def update_preferences(self, preferences: Union[UserPreferences, Dict[str, Any]]) -> None:
        """
        Update user preferences.
        
        Args:
            preferences: New preferences as UserPreferences object or dict
        """
        if isinstance(preferences, dict):
            # Update existing preferences with new values
            for key, value in preferences.items():
                if hasattr(self.user_preferences, key):
                    setattr(self.user_preferences, key, value)
        else:
            self.user_preferences = preferences
        
        self.last_updated = datetime.now(timezone.utc)
    
    def compress_history(self, keep_recent: int = 20, keep_important: bool = True) -> int:
        """
        Compress message history by keeping only recent and important messages.
        
        Args:
            keep_recent: Number of recent messages to keep
            keep_important: Whether to preserve important messages
            
        Returns:
            int: Number of messages removed
        """
        original_count = len(self.message_history)
        
        # Get messages to keep
        recent_messages = self.get_recent_messages(keep_recent)
        important_messages = self.get_important_messages() if keep_important else []
        
        # Combine and deduplicate
        messages_to_keep = {}
        for msg in recent_messages + important_messages:
            messages_to_keep[msg.id] = msg
        
        # Update history
        self.message_history = list(messages_to_keep.values())
        self.message_history.sort(key=lambda x: x.timestamp)
        
        # Update metrics
        removed_count = original_count - len(self.message_history)
        self.context_metrics.context_compressions += 1
        self.context_metrics.last_compression = datetime.now(timezone.utc)
        self.last_updated = datetime.now(timezone.utc)
        
        return removed_count
    
    def export_data(self) -> Dict[str, Any]:
        """
        Export context data for backup or analysis.
        
        Returns:
            Dict[str, Any]: Complete context data
        """
        return self.dict()
    
    @classmethod
    def import_data(cls, data: Dict[str, Any]) -> "ValorContext":
        """
        Import context data from backup.
        
        Args:
            data: Context data dictionary
            
        Returns:
            ValorContext: Restored context instance
        """
        return cls(**data)
    
    def get_context_size_estimate(self) -> Dict[str, int]:
        """
        Get an estimate of the context size in various metrics.
        
        Returns:
            Dict[str, int]: Size estimates (messages, tokens, etc.)
        """
        total_tokens = sum(msg.token_count or 0 for msg in self.message_history)
        total_chars = sum(len(msg.content) for msg in self.message_history)
        
        return {
            'message_count': len(self.message_history),
            'total_tokens': total_tokens,
            'total_characters': total_chars,
            'important_messages': len(self.important_messages),
            'active_tools': len(self.active_tools),
            'tool_usage_records': len(self.tool_usage_history)
        }