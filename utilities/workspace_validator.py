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
    YUDAME = "yudame"
    VERKSTAD = "verkstad"


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
        return str(Path(__file__).parent.parent / "config" / "workspace_config.json")
    
    def _load_workspace_config(self) -> Dict[str, WorkspaceConfig]:
        """Load workspace configuration from consolidated config file"""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            raise WorkspaceAccessError(f"Workspace configuration not found: {self.config_path}")
        
        workspaces = {}
        
        # Load workspaces from the new consolidated config format
        for workspace_name, workspace_data in config.get("workspaces", {}).items():
            # Map workspace_type string to enum
            workspace_type_str = workspace_data.get("workspace_type", "").lower()
            if workspace_type_str == "deckfusion":
                workspace_type = WorkspaceType.DECKFUSION
            elif workspace_type_str == "psyoptimal":
                workspace_type = WorkspaceType.PSYOPTIMAL
            elif workspace_type_str == "flextrip":
                workspace_type = WorkspaceType.FLEXTRIP
            elif workspace_type_str == "yudame":
                workspace_type = WorkspaceType.YUDAME
            elif workspace_type_str == "verkstad":
                workspace_type = WorkspaceType.VERKSTAD
            else:
                # Skip unknown workspace types
                continue
            
            # Convert telegram_chat_ids list to set of strings
            telegram_chat_ids = set(str(chat_id) for chat_id in workspace_data.get("telegram_chat_ids", []))
            
            workspaces[workspace_name] = WorkspaceConfig(
                name=workspace_name,
                workspace_type=workspace_type,
                notion_database_id=workspace_data["database_id"],
                allowed_directories=workspace_data.get("allowed_directories", []),
                telegram_chat_ids=telegram_chat_ids,
                aliases=workspace_data.get("aliases", [])
            )
        
        return workspaces
    
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
        """Validate that a chat can access a specific Notion workspace with strict isolation
        
        Args:
            chat_id: Telegram chat ID making the request
            workspace_name: Requested workspace name
            
        Raises:
            WorkspaceAccessError: If access is not allowed
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Get the workspace this chat is mapped to
        allowed_workspace = self.get_workspace_for_chat(chat_id)
        
        if not allowed_workspace:
            error_msg = (
                f"Chat {chat_id} is not mapped to any workspace. "
                f"Configure chat mapping in {self.config_path}"
            )
            logger.warning(f"Workspace access denied: {error_msg}")
            raise WorkspaceAccessError(error_msg)
        
        # Normalize workspace names for comparison
        requested_workspace = self._normalize_workspace_name(workspace_name)
        allowed_workspace_normalized = self._normalize_workspace_name(allowed_workspace)
        
        # Strict validation: exact workspace match required
        if requested_workspace != allowed_workspace_normalized:
            error_msg = (
                f"STRICT ISOLATION VIOLATION: Chat {chat_id} attempted to access "
                f"workspace '{workspace_name}' but is only authorized for '{allowed_workspace}'. "
                f"Cross-workspace access is strictly prohibited."
            )
            logger.error(f"Security violation: {error_msg}")
            raise WorkspaceAccessError(error_msg)
        
        # Log successful access for audit trail
        logger.info(f"Workspace access granted: Chat {chat_id} -> {allowed_workspace}")
    
    def validate_directory_access(self, chat_id: str, file_path: str) -> None:
        """Validate that a chat can access a specific directory/file path with strict isolation
        
        Args:
            chat_id: Telegram chat ID making the request  
            file_path: Requested file or directory path
            
        Raises:
            WorkspaceAccessError: If directory access is not allowed
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Get the workspace this chat is mapped to
        workspace_name = self.get_workspace_for_chat(chat_id)
        
        if not workspace_name:
            error_msg = f"Chat {chat_id} is not mapped to any workspace"
            logger.warning(f"Directory access denied: {error_msg}")
            raise WorkspaceAccessError(error_msg)
        
        workspace = self.workspaces[workspace_name]
        
        # Normalize the file path
        normalized_path = os.path.abspath(file_path)
        
        # Strict directory validation - must be within allowed directories
        access_allowed = False
        for allowed_dir in workspace.allowed_directories:
            allowed_normalized = os.path.abspath(allowed_dir)
            if normalized_path.startswith(allowed_normalized):
                access_allowed = True
                break
        
        if not access_allowed:
            error_msg = (
                f"STRICT DIRECTORY ISOLATION VIOLATION: Chat {chat_id} (workspace: {workspace_name}) "
                f"attempted to access unauthorized path: {file_path}. "
                f"Only allowed directories: {workspace.allowed_directories}"
            )
            logger.error(f"Security violation: {error_msg}")
            raise WorkspaceAccessError(error_msg)
        
        # Additional check for cross-workspace directory access
        self._validate_no_cross_workspace_access(chat_id, normalized_path, workspace)
        
        # Log successful access for audit trail
        logger.info(f"Directory access granted: Chat {chat_id} ({workspace_name}) -> {file_path}")
    
    def _validate_no_cross_workspace_access(self, chat_id: str, normalized_path: str, current_workspace: WorkspaceConfig) -> None:
        """Ensure the path doesn't access other workspace directories
        
        Args:
            chat_id: Telegram chat ID making the request
            normalized_path: Normalized file path to check
            current_workspace: Current workspace configuration
            
        Raises:
            WorkspaceAccessError: If cross-workspace access is detected
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Define forbidden paths for each workspace type
        forbidden_paths = []
        
        # Get all workspace directories except current one
        all_workspace_dirs = [
            "/Users/valorengels/src/deckfusion",
            "/Users/valorengels/src/psyoptimal", 
            "/Users/valorengels/src/flextrip",
            "/Users/valorengels/src/ai",
            "/Users/valorengels/src/verkstad"
        ]
        
        # Remove directories allowed for current workspace
        for allowed_dir in current_workspace.allowed_directories:
            allowed_normalized = os.path.abspath(allowed_dir)
            if allowed_normalized in all_workspace_dirs:
                all_workspace_dirs.remove(allowed_normalized)
        
        forbidden_paths = all_workspace_dirs
        
        # Check if normalized path starts with any forbidden path
        for forbidden_path in forbidden_paths:
            forbidden_normalized = os.path.abspath(forbidden_path)
            if normalized_path.startswith(forbidden_normalized):
                error_msg = (
                    f"CROSS-WORKSPACE ACCESS VIOLATION: Chat {chat_id} "
                    f"({current_workspace.workspace_type.value}) attempted to access "
                    f"forbidden workspace directory: {forbidden_path}. "
                    f"Strict workspace isolation enforced."
                )
                logger.error(f"Critical security violation: {error_msg}")
                raise WorkspaceAccessError(error_msg)
    
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


def validate_telegram_environment() -> Dict[str, str]:
    """Validate Telegram environment configuration for whitelist controls
    
    Returns:
        Dict with validation results and configuration status
        
    Raises:
        WorkspaceAccessError: If environment validation fails
    """
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    validation_results = {
        "status": "unknown",
        "allowed_groups": "not_configured",
        "allow_dms": "unknown",
        "group_count": "0",
        "errors": []
    }
    
    try:
        # Check TELEGRAM_ALLOWED_GROUPS environment variable
        allowed_groups_env = os.getenv("TELEGRAM_ALLOWED_GROUPS", "").strip()
        
        if not allowed_groups_env:
            validation_results["allowed_groups"] = "not_configured"
            validation_results["errors"].append("TELEGRAM_ALLOWED_GROUPS not set - bot will handle no groups")
            logger.warning("TELEGRAM_ALLOWED_GROUPS environment variable not configured")
        else:
            try:
                # Parse comma-separated group chat IDs
                group_ids = [int(group_id.strip()) for group_id in allowed_groups_env.split(",") if group_id.strip()]
                validation_results["allowed_groups"] = "configured"
                validation_results["group_count"] = str(len(group_ids))
                logger.info(f"Telegram whitelist configured for {len(group_ids)} groups: {group_ids}")
            except ValueError as e:
                validation_results["allowed_groups"] = "invalid_format"
                validation_results["errors"].append(f"Invalid TELEGRAM_ALLOWED_GROUPS format: {e}")
                logger.error(f"Invalid TELEGRAM_ALLOWED_GROUPS format: {e}")
                raise WorkspaceAccessError(f"Invalid TELEGRAM_ALLOWED_GROUPS format: {e}")
        
        # Check TELEGRAM_ALLOW_DMS setting
        allow_dms_env = os.getenv("TELEGRAM_ALLOW_DMS", "true").lower().strip()
        if allow_dms_env in ("true", "1", "yes", "on"):
            validation_results["allow_dms"] = "enabled"
        elif allow_dms_env in ("false", "0", "no", "off"):
            validation_results["allow_dms"] = "disabled"
        else:
            validation_results["allow_dms"] = "invalid_value"
            validation_results["errors"].append(f"Invalid TELEGRAM_ALLOW_DMS value: {allow_dms_env}")
            logger.warning(f"Invalid TELEGRAM_ALLOW_DMS value: {allow_dms_env}, defaulting to enabled")
        
        if not validation_results["errors"]:
            validation_results["status"] = "valid"
            logger.info("Telegram environment validation passed")
        else:
            validation_results["status"] = "errors"
            logger.warning(f"Telegram environment validation completed with errors: {validation_results['errors']}")
        
        return validation_results
        
    except Exception as e:
        validation_results["status"] = "failed"
        validation_results["errors"].append(f"Environment validation failed: {str(e)}")
        logger.error(f"Telegram environment validation failed: {e}")
        raise WorkspaceAccessError(f"Environment validation failed: {str(e)}")


def validate_chat_whitelist_access(chat_id: int, is_private: bool = False) -> bool:
    """Validate if a chat ID is allowed based on environment whitelist configuration
    
    Args:
        chat_id: Telegram chat ID to validate
        is_private: Whether this is a private/DM chat
        
    Returns:
        True if chat is allowed, False if rejected
    """
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        if is_private:
            # For DMs: check TELEGRAM_ALLOW_DMS setting
            allow_dms = os.getenv("TELEGRAM_ALLOW_DMS", "true").lower().strip()
            is_allowed = allow_dms in ("true", "1", "yes", "on")
            
            if is_allowed:
                logger.info(f"DM access granted for chat {chat_id}")
            else:
                logger.info(f"DM access denied for chat {chat_id} (DMs disabled)")
            
            return is_allowed
        else:
            # For groups: check TELEGRAM_ALLOWED_GROUPS whitelist
            allowed_groups_env = os.getenv("TELEGRAM_ALLOWED_GROUPS", "").strip()
            
            if not allowed_groups_env:
                # No groups configured = no groups allowed
                logger.info(f"Group access denied for chat {chat_id} (no groups configured)")
                return False
            
            try:
                allowed_group_ids = [int(group_id.strip()) for group_id in allowed_groups_env.split(",") if group_id.strip()]
                is_allowed = chat_id in allowed_group_ids
                
                if is_allowed:
                    logger.info(f"Group access granted for chat {chat_id} (whitelisted)")
                else:
                    logger.warning(f"Group access denied for chat {chat_id} (not whitelisted)")
                
                return is_allowed
            except ValueError as e:
                logger.error(f"Invalid TELEGRAM_ALLOWED_GROUPS format, denying access to chat {chat_id}: {e}")
                return False
    
    except Exception as e:
        logger.error(f"Chat whitelist validation failed for chat {chat_id}: {e}")
        return False