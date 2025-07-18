"""
Workspace Validation System

Enforces strict chat-to-workspace mapping controls to ensure:
- Fuse chat can only access Fuse Notion DB
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
    AI = "ai"
    FUSE = "fuse"
    PSYOPTIMAL = "psyoptimal"
    FLEXTRIP = "flextrip"
    VERKSTAD = "verkstad"
    TEST = "test"


@dataclass
class WorkspaceConfig:
    """Configuration for a specific workspace"""
    name: str
    workspace_type: WorkspaceType
    notion_database_id: str
    allowed_directories: List[str]
    telegram_chat_id: Optional[str]
    aliases: List[str]


class WorkspaceAccessError(Exception):
    """Raised when workspace access validation fails"""
    pass


class WorkspaceResolver:
    """Unified workspace resolution for SWE tools"""
    
    @staticmethod
    def resolve_working_directory(
        chat_id: Optional[str] = None,
        username: Optional[str] = None,
        is_group_chat: bool = False,
        target_directory: str = ""
    ) -> tuple[str, str]:
        """
        Unified workspace resolution logic for both SWE tools.
        
        Args:
            chat_id: Chat ID for workspace detection
            username: Username for DM workspace detection  
            is_group_chat: Whether this is a group chat
            target_directory: Explicit directory override
            
        Returns:
            tuple[str, str]: (working_directory, context_description)
        """
        # Explicit directory takes highest priority
        if target_directory and target_directory.strip():
            return target_directory, f"Explicit directory: {target_directory}"
            
        # Try chat-based workspace resolution
        if chat_id:
            try:
                chat_id_int = int(chat_id)
                
                # Get workspace from consolidated config
                validator = get_workspace_validator()
                workspace_name = validator.get_workspace_for_chat(str(chat_id_int))
                
                if workspace_name and workspace_name in validator.workspaces:
                    workspace = validator.workspaces[workspace_name]
                    # Use first allowed directory as working directory
                    if workspace.allowed_directories:
                        workspace_dir = workspace.allowed_directories[0]
                        return workspace_dir, f"Workspace: {workspace_name}"
                    
                # Try DM directory for private chats
                if username and not is_group_chat:
                    dm_dir = get_dm_user_working_directory(username)
                    return dm_dir, f"DM directory for @{username}"
                    
            except (ValueError, Exception):
                pass  # Fall through to default
        
        # Try username-only resolution (fallback for DMs)
        if username and not is_group_chat:
            try:
                dm_dir = get_dm_user_working_directory(username)
                return dm_dir, f"DM directory for @{username}"
            except Exception:
                pass
        
        # Default fallback
        return ".", "Current directory (no workspace context)"
    
    @staticmethod
    def get_workspace_context_info(
        chat_id: Optional[str] = None,
        username: Optional[str] = None,
        is_group_chat: bool = False
    ) -> dict:
        """
        Get comprehensive workspace context information.
        
        Returns:
            dict: Context information including workspace name, directory, permissions
        """
        working_dir, context_desc = WorkspaceResolver.resolve_working_directory(
            chat_id, username, is_group_chat
        )
        
        workspace_name = "Unknown"
        has_write_permissions = True  # Default assumption
        
        if chat_id:
            try:
                chat_id_int = int(chat_id)
                validator = get_workspace_validator()
                workspace_name = validator.get_workspace_for_chat(str(chat_id_int))
                if not workspace_name:
                    workspace_name = "Unknown"
            except Exception:
                pass
        
        return {
            "working_directory": working_dir,
            "context_description": context_desc,
            "workspace_name": workspace_name,
            "has_write_permissions": has_write_permissions,
            "chat_id": chat_id,
            "username": username,
            "is_group_chat": is_group_chat
        }


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
            # Derive workspace_type from working_directory
            working_directory = workspace_data.get("working_directory", "")
            workspace_type_str = self._derive_workspace_type_from_directory(working_directory)
            
            # Map workspace_type string to enum
            if workspace_type_str == "fuse":
                workspace_type = WorkspaceType.FUSE
            elif workspace_type_str == "psyoptimal":
                workspace_type = WorkspaceType.PSYOPTIMAL
            elif workspace_type_str == "flextrip":
                workspace_type = WorkspaceType.FLEXTRIP
            elif workspace_type_str == "ai":
                workspace_type = WorkspaceType.AI
            elif workspace_type_str == "verkstad":
                workspace_type = WorkspaceType.VERKSTAD
            elif workspace_type_str == "test":
                workspace_type = WorkspaceType.TEST
            else:
                # Skip unknown workspace types
                continue
            
            # Get single telegram_chat_id
            telegram_chat_id = workspace_data.get("telegram_chat_id")
            if telegram_chat_id is not None:
                telegram_chat_id = str(telegram_chat_id)
            
            # Extract database_id from notion_db_url
            notion_db_url = workspace_data.get("notion_db_url", "")
            database_id = self._extract_database_id_from_url(notion_db_url)
            
            # Handle both working_directory and allowed_directories formats
            allowed_dirs = workspace_data.get("allowed_directories", [])
            if not allowed_dirs and "working_directory" in workspace_data:
                allowed_dirs = [workspace_data["working_directory"]]
            
            workspaces[workspace_name] = WorkspaceConfig(
                name=workspace_name,
                workspace_type=workspace_type,
                notion_database_id=database_id,
                allowed_directories=allowed_dirs,
                telegram_chat_id=telegram_chat_id,
                aliases=workspace_data.get("aliases", [])
            )
        
        return workspaces
    
    def _derive_workspace_type_from_directory(self, working_directory: str) -> str:
        """Derive workspace type from working directory path"""
        if not working_directory:
            return "unknown"
        
        # Extract the directory name from the path
        from pathlib import Path
        dir_name = Path(working_directory).name.lower()
        
        # Map directory names to workspace types
        if dir_name in ["deckfusion", "fuse"]:
            return "fuse"
        elif dir_name in ["psyoptimal"]:
            return "psyoptimal"
        elif dir_name in ["flextrip"]:
            return "flextrip"
        elif dir_name in ["ai", "yudame"]:
            return "ai"
        elif dir_name in ["verkstad"]:
            return "verkstad"
        elif dir_name in ["test"]:
            return "test"
        else:
            return "unknown"
    
    def _extract_database_id_from_url(self, notion_db_url: str) -> str:
        """Extract database ID from Notion database URL"""
        if not notion_db_url:
            return ""
        
        # Extract UUID from URL (format: https://www.notion.so/workspace/database_id?v=view_id)
        import re
        
        # Match UUID pattern with hyphens
        uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
        match = re.search(uuid_pattern, notion_db_url)
        
        if match:
            return match.group(0)
        
        # Fallback: try to extract from path segments
        parts = notion_db_url.split('/')
        for part in parts:
            if len(part) == 32 and all(c in '0123456789abcdef' for c in part.lower()):
                # Convert 32-char hex to UUID format
                return f"{part[:8]}-{part[8:12]}-{part[12:16]}-{part[16:20]}-{part[20:]}"
        
        return ""
    
    def _build_chat_mapping(self) -> Dict[str, str]:
        """Build mapping from chat IDs to workspace names"""
        chat_mapping = {}
        for workspace_name, workspace in self.workspaces.items():
            if workspace.telegram_chat_id:
                chat_mapping[workspace.telegram_chat_id] = workspace_name
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
        
        # Special exception: Allow access to workspace screenshot directories
        # This enables screenshot handoff from Claude Code sessions to main agent
        if not access_allowed:
            for allowed_dir in workspace.allowed_directories:
                workspace_screenshots_dir = os.path.join(allowed_dir, "tmp", "ai_screenshots")
                workspace_screenshots_normalized = os.path.abspath(workspace_screenshots_dir)
                if normalized_path.startswith(workspace_screenshots_normalized):
                    access_allowed = True
                    logger.info(f"Screenshot directory access granted: Chat {chat_id} -> {normalized_path}")
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
            "/Users/valorengels/src/fuse",
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
                "telegram_chat_id": workspace.telegram_chat_id,
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
                # Parse comma-separated workspace names and map to chat IDs
                workspace_names = [name.strip() for name in allowed_groups_env.split(",") if name.strip()]
                
                # Load workspace config to resolve names to chat IDs
                import json
                from pathlib import Path
                config_file = Path(__file__).parent.parent / "config" / "workspace_config.json"
                if config_file.exists():
                    with open(config_file) as f:
                        config = json.load(f)
                    
                    workspaces = config.get("workspaces", {})
                    group_ids = []
                    
                    for workspace_name in workspace_names:
                        if workspace_name in workspaces:
                            chat_id = workspaces[workspace_name].get("telegram_chat_id")
                            if chat_id:
                                group_ids.append(int(chat_id))
                    
                    validation_results["allowed_groups"] = "configured"
                    validation_results["group_count"] = str(len(group_ids))
                    logger.info(f"Telegram whitelist configured for {len(group_ids)} groups: {group_ids}")
                else:
                    raise ValueError("Workspace config not found")
            except (ValueError, FileNotFoundError) as e:
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


def validate_chat_whitelist_access(chat_id: int, is_private: bool = False, username: str = None) -> bool:
    """Validate if a chat ID is allowed based on environment whitelist configuration
    
    Args:
        chat_id: Telegram chat ID to validate
        is_private: Whether this is a private/DM chat
        username: Username for DM validation (optional)
        
    Returns:
        True if chat is allowed, False if rejected
    """
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        if is_private:
            # For DMs: first check if DMs are globally enabled, then check user whitelist
            allow_dms_env = os.getenv("TELEGRAM_ALLOW_DMS", "true").lower().strip()
            dms_globally_enabled = allow_dms_env in ("true", "1", "yes", "on")
            
            if not dms_globally_enabled:
                logger.info(f"DM access denied for chat {chat_id} (DMs globally disabled)")
                return False
            
            # DMs are globally enabled, now check user whitelist
            return validate_dm_user_access(username, chat_id)
        else:
            # For groups: check TELEGRAM_ALLOWED_GROUPS whitelist
            allowed_groups_env = os.getenv("TELEGRAM_ALLOWED_GROUPS", "").strip()
            
            if not allowed_groups_env:
                # No groups configured = no groups allowed
                logger.debug(f"Group access denied for chat {chat_id} (no groups configured)")
                return False
            
            try:
                # Parse workspace names and map to chat IDs
                workspace_names = [name.strip() for name in allowed_groups_env.split(",") if name.strip()]
                
                # Load workspace config to resolve names to chat IDs
                import json
                from pathlib import Path
                config_file = Path(__file__).parent.parent / "config" / "workspace_config.json"
                if config_file.exists():
                    with open(config_file) as f:
                        config = json.load(f)
                    
                    workspaces = config.get("workspaces", {})
                    allowed_group_ids = []
                    
                    for workspace_name in workspace_names:
                        if workspace_name in workspaces:
                            workspace_chat_id = workspaces[workspace_name].get("telegram_chat_id")
                            if workspace_chat_id:
                                allowed_group_ids.append(int(workspace_chat_id))
                    
                    is_allowed = chat_id in allowed_group_ids
                    
                    if is_allowed:
                        logger.debug(f"Group access granted for chat {chat_id} (whitelisted)")
                    else:
                        logger.debug(f"Group access denied for chat {chat_id} (not whitelisted)")
                    
                    return is_allowed
                else:
                    logger.error(f"Workspace config not found, denying access to chat {chat_id}")
                    return False
            except (ValueError, FileNotFoundError) as e:
                logger.error(f"Invalid TELEGRAM_ALLOWED_GROUPS format, denying access to chat {chat_id}: {e}")
                return False
    
    except Exception as e:
        logger.error(f"Chat whitelist validation failed for chat {chat_id}: {e}")
        return False


def validate_dm_user_access(username: str, chat_id: int) -> bool:
    """Validate if a user is allowed to send DMs based on DM whitelist
    
    Args:
        username: Telegram username of the user
        chat_id: Chat ID for logging purposes
        
    Returns:
        True if user is whitelisted for DMs, False if rejected
    """
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        # Load DM whitelist from workspace config
        validator = get_workspace_validator()
        config_path = validator.config_path
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        dm_whitelist = config.get("dm_whitelist", {})
        allowed_users = dm_whitelist.get("allowed_users", {})
        
        # If no username, check if user ID is whitelisted instead
        if not username:
            # Check if chat_id (which is user_id for DMs) is in allowed users by ID
            allowed_user_ids = dm_whitelist.get("allowed_user_ids", {})
            if str(chat_id) in allowed_user_ids:
                user_info = allowed_user_ids[str(chat_id)]
                logger.info(f"DM access granted for user ID {chat_id}: {user_info.get('description', 'Whitelisted user ID')}")
                return True
            else:
                logger.debug(f"DM access denied for chat {chat_id} (no username provided and user ID not whitelisted)")
                return False
        
        username_lower = username.lower()
        
        if username_lower in allowed_users:
            user_info = allowed_users[username_lower]
            logger.info(f"DM access granted for user @{username} (chat {chat_id}): {user_info.get('description', 'Whitelisted user')}")
            return True
        else:
            logger.debug(f"DM access denied for user @{username} (chat {chat_id}): not in whitelist")
            return False
            
    except Exception as e:
        logger.error(f"DM user validation failed for @{username} (chat {chat_id}): {e}")
        return False


def get_dm_user_working_directory(username: str) -> str:
    """Get the working directory for a whitelisted DM user
    
    Args:
        username: Telegram username of the user
        
    Returns:
        Working directory path for the user, or default if not specified
    """
    try:
        # Load DM whitelist from workspace config
        validator = get_workspace_validator()
        config_path = validator.config_path
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        dm_whitelist = config.get("dm_whitelist", {})
        default_dir = dm_whitelist.get("default_working_directory", "/Users/valorengels/src/ai")
        allowed_users = dm_whitelist.get("allowed_users", {})
        
        if not username:
            return default_dir
        
        username_lower = username.lower()
        
        if username_lower in allowed_users:
            user_info = allowed_users[username_lower]
            return user_info.get("working_directory", default_dir)
        else:
            # User not whitelisted, but return default for consistency
            return default_dir
            
    except Exception:
        # Fallback to default AI directory
        return "/Users/valorengels/src/ai"