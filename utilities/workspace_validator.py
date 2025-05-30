"""
Workspace Validation System

Enforces strict chat-to-workspace mapping controls to ensure:
- DeckFusion chat can only access DeckFusion Notion DB
- PsyOPTIMAL chat can only access PsyOPTIMAL Notion DB  
- Directory restrictions limit operations to correct workspace paths
- Validation occurs before any Notion queries or code operations
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum

class WorkspaceType(Enum):
    DECKFUSION = "deckfusion"
    PSYOPTIMAL = "psyoptimal"
    FLEXTRIP = "flextrip"


@dataclass
class WorkspaceConfig:
    """Configuration for a specific workspace"""
    name: str
    workspace_type: WorkspaceType
    notion_database_id: str
    allowed_directories: List[str]
    telegram_chat_ids: Set[str]
    aliases: List[str]


class WorkspaceAccessError(Exception):
    """Raised when workspace access validation fails"""
    pass


class WorkspaceValidator:
    """Validates and enforces workspace access controls"""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize validator with workspace configuration
        
        Args:
            config_path: Path to workspace mapping configuration file
        """
        self.config_path = config_path or self._get_default_config_path()
        self.workspaces = self._load_workspace_config()
        self.chat_to_workspace = self._build_chat_mapping()
        
    def _get_default_config_path(self) -> str:
        """Get default path to workspace configuration"""
        return str(Path(__file__).parent.parent / "integrations" / "notion" / "database_mapping.json")
    
    def _load_workspace_config(self) -> Dict[str, WorkspaceConfig]:
        """Load workspace configuration from mapping file"""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            raise WorkspaceAccessError(f"Workspace configuration not found: {self.config_path}")
        
        workspaces = {}
        
        # Define workspace mappings
        workspace_mappings = {
            "DeckFusion Dev": WorkspaceConfig(
                name="DeckFusion Dev",
                workspace_type=WorkspaceType.DECKFUSION,
                notion_database_id=config["projects"]["DeckFusion Dev"]["database_id"],
                allowed_directories=[
                    "/Users/valorengels/src/deckfusion",
                    "/Users/valorengels/src/deckfusion/"
                ],
                telegram_chat_ids=set(),  # Will be populated from config
                aliases=["deckfusion", "deck", "fusion", "deckfusion dev", "deck dev"]
            ),
            "PsyOPTIMAL": WorkspaceConfig(
                name="PsyOPTIMAL",
                workspace_type=WorkspaceType.PSYOPTIMAL,
                notion_database_id=config["projects"]["PsyOPTIMAL"]["database_id"],
                allowed_directories=[
                    "/Users/valorengels/src/psyoptimal",
                    "/Users/valorengels/src/psyoptimal/"
                ],
                telegram_chat_ids=set(),  # Will be populated from config
                aliases=["psyoptimal", "psy", "optimal"]
            ),
            "PsyOPTIMAL Dev": WorkspaceConfig(
                name="PsyOPTIMAL Dev", 
                workspace_type=WorkspaceType.PSYOPTIMAL,
                notion_database_id=config["projects"]["PsyOPTIMAL Dev"]["database_id"],
                allowed_directories=[
                    "/Users/valorengels/src/psyoptimal",
                    "/Users/valorengels/src/psyoptimal/"
                ],
                telegram_chat_ids=set(),  # Will be populated from config
                aliases=["psyoptimal dev", "psy dev", "optimal dev"]
            ),
            "FlexTrip": WorkspaceConfig(
                name="FlexTrip",
                workspace_type=WorkspaceType.FLEXTRIP,
                notion_database_id=config["projects"]["FlexTrip"]["database_id"],
                allowed_directories=[
                    "/Users/valorengels/src/flextrip", 
                    "/Users/valorengels/src/flextrip/"
                ],
                telegram_chat_ids=set(),  # Will be populated from config
                aliases=["flextrip", "flex", "trip"]
            )
        }
        
        # Add telegram chat mappings if available
        if "telegram_groups" in config:
            for chat_id, workspace_name in config["telegram_groups"].items():
                if workspace_name in workspace_mappings:
                    workspace_mappings[workspace_name].telegram_chat_ids.add(chat_id)
        
        return workspace_mappings
    
    def _build_chat_mapping(self) -> Dict[str, str]:
        """Build mapping from chat IDs to workspace names"""
        chat_mapping = {}
        for workspace_name, workspace in self.workspaces.items():
            for chat_id in workspace.telegram_chat_ids:
                chat_mapping[chat_id] = workspace_name
        return chat_mapping
    
    def get_workspace_for_chat(self, chat_id: str) -> Optional[str]:
        """Get workspace name for a given chat ID
        
        Args:
            chat_id: Telegram chat ID
            
        Returns:
            Workspace name if mapped, None otherwise
        """
        return self.chat_to_workspace.get(chat_id)
    
    def validate_notion_access(self, chat_id: str, workspace_name: str) -> None:
        """Validate that a chat can access a specific Notion workspace
        
        Args:
            chat_id: Telegram chat ID making the request
            workspace_name: Requested workspace name
            
        Raises:
            WorkspaceAccessError: If access is not allowed
        """
        # Get the workspace this chat is mapped to
        allowed_workspace = self.get_workspace_for_chat(chat_id)
        
        if not allowed_workspace:
            raise WorkspaceAccessError(
                f"Chat {chat_id} is not mapped to any workspace. "
                f"Configure chat mapping in {self.config_path}"
            )
        
        # Normalize workspace names for comparison
        requested_workspace = self._normalize_workspace_name(workspace_name)
        allowed_workspace_normalized = self._normalize_workspace_name(allowed_workspace)
        
        if requested_workspace != allowed_workspace_normalized:
            raise WorkspaceAccessError(
                f"Chat {chat_id} is not authorized to access workspace '{workspace_name}'. "
                f"Only allowed to access: {allowed_workspace}"
            )
    
    def validate_directory_access(self, chat_id: str, file_path: str) -> None:
        """Validate that a chat can access a specific directory/file path
        
        Args:
            chat_id: Telegram chat ID making the request  
            file_path: Requested file or directory path
            
        Raises:
            WorkspaceAccessError: If directory access is not allowed
        """
        # Get the workspace this chat is mapped to
        workspace_name = self.get_workspace_for_chat(chat_id)
        
        if not workspace_name:
            raise WorkspaceAccessError(
                f"Chat {chat_id} is not mapped to any workspace"
            )
        
        workspace = self.workspaces[workspace_name]
        
        # Normalize the file path
        normalized_path = os.path.abspath(file_path)
        
        # Check if path is within allowed directories
        for allowed_dir in workspace.allowed_directories:
            allowed_normalized = os.path.abspath(allowed_dir)
            if normalized_path.startswith(allowed_normalized):
                return
        
        raise WorkspaceAccessError(
            f"Chat {chat_id} (workspace: {workspace_name}) cannot access path: {file_path}. "
            f"Allowed directories: {workspace.allowed_directories}"
        )
    
    def get_allowed_notion_database(self, chat_id: str) -> str:
        """Get the Notion database ID that a chat is allowed to access
        
        Args:
            chat_id: Telegram chat ID
            
        Returns:
            Notion database ID
            
        Raises:
            WorkspaceAccessError: If chat is not mapped to a workspace
        """
        workspace_name = self.get_workspace_for_chat(chat_id)
        
        if not workspace_name:
            raise WorkspaceAccessError(
                f"Chat {chat_id} is not mapped to any workspace"
            )
        
        return self.workspaces[workspace_name].notion_database_id
    
    def get_allowed_directories(self, chat_id: str) -> List[str]:
        """Get allowed directories for a chat
        
        Args:
            chat_id: Telegram chat ID
            
        Returns:
            List of allowed directory paths
            
        Raises:
            WorkspaceAccessError: If chat is not mapped to a workspace
        """
        workspace_name = self.get_workspace_for_chat(chat_id)
        
        if not workspace_name:
            raise WorkspaceAccessError(
                f"Chat {chat_id} is not mapped to any workspace"
            )
        
        return self.workspaces[workspace_name].allowed_directories
    
    def _normalize_workspace_name(self, name: str) -> str:
        """Normalize workspace name for comparison"""
        # Check if it's an alias first
        normalized = name.lower().strip()
        for workspace_name, workspace in self.workspaces.items():
            if normalized in [alias.lower() for alias in workspace.aliases]:
                return workspace_name
        
        # If not an alias, return the name as-is
        return name
    
    def list_workspaces(self) -> Dict[str, Dict]:
        """List all configured workspaces with their details
        
        Returns:
            Dictionary of workspace configurations
        """
        result = {}
        for name, workspace in self.workspaces.items():
            result[name] = {
                "type": workspace.workspace_type.value,
                "notion_database_id": workspace.notion_database_id,
                "allowed_directories": workspace.allowed_directories,
                "telegram_chat_ids": list(workspace.telegram_chat_ids),
                "aliases": workspace.aliases
            }
        return result


# Global validator instance
_validator = None

def get_workspace_validator() -> WorkspaceValidator:
    """Get global workspace validator instance"""
    global _validator
    if _validator is None:
        _validator = WorkspaceValidator()
    return _validator


def validate_workspace_access(chat_id: str, workspace_name: str, file_path: Optional[str] = None) -> None:
    """Convenience function to validate both workspace and directory access
    
    Args:
        chat_id: Telegram chat ID
        workspace_name: Requested workspace name
        file_path: Optional file path to validate
        
    Raises:
        WorkspaceAccessError: If validation fails
    """
    validator = get_workspace_validator()
    
    # Validate Notion workspace access
    validator.validate_notion_access(chat_id, workspace_name)
    
    # Validate directory access if file path provided
    if file_path:
        validator.validate_directory_access(chat_id, file_path)