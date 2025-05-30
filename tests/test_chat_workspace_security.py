#!/usr/bin/env python3
"""
Comprehensive test suite for chat-to-workspace mapping security.

Tests strict validation boundaries ensuring:
- DeckFusion chats can only access DeckFusion Notion DB and ~/src/deckfusion/
- PsyOPTIMAL chats can only access PsyOPTIMAL Notion DB and ~/src/psyoptimal/
- Environment variable validation for whitelisted groups
- Security boundary enforcement and violation detection
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from utilities.workspace_validator import (
    WorkspaceValidator, 
    WorkspaceAccessError, 
    WorkspaceType,
    validate_workspace_access,
    validate_telegram_environment,
    validate_chat_whitelist_access,
    get_workspace_validator
)
from integrations.telegram.client import TelegramClient
from integrations.telegram.utils import list_telegram_dialogs_safe
from mcp_servers.notion_tools import query_notion_projects


class TestChatWorkspaceSecurity:
    """Test suite for chat-to-workspace mapping security boundaries."""
    
    def setup_method(self):
        """Set up test fixtures with sample workspace configuration."""
        # Create temporary config file
        self.temp_config = {
            "projects": {
                "DeckFusion Dev": {
                    "database_id": "48a27df3-0342-4aa4-bd4c-0dec1ff908f4",
                    "url": "https://www.notion.so/deckfusion/48a27df303424aa4bd4c0dec1ff908f4",
                    "description": "DeckFusion development tasks and management"
                },
                "PsyOPTIMAL": {
                    "database_id": "1d22bc89-4d10-8079-8dcb-e7813b006c5c",
                    "url": "https://www.notion.so/yudame/1d22bc894d1080798dcbe7813b006c5c",
                    "description": "PsyOPTIMAL project tasks and management"
                },
                "PsyOPTIMAL Dev": {
                    "database_id": "1d22bc89-4d10-8079-8dcb-e7813b006c5c",
                    "url": "https://www.notion.so/yudame/1d22bc894d1080798dcbe7813b006c5c",
                    "description": "PsyOPTIMAL development tasks and management"
                },
                "FlexTrip": {
                    "database_id": "1ed2bc89-4d10-80e5-89e9-feefe994dddd",
                    "url": "https://www.notion.so/yudame/1ed2bc894d1080e589e9feefe994dddd",
                    "description": "FlexTrip project tasks and management"
                }
            },
            "telegram_groups": {
                "-1008888888888": "DeckFusion Dev",
                "-1001234567890": "PsyOPTIMAL",
                "-1009876543210": "FlexTrip"
            }
        }
        
        # Create temporary config file
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(self.temp_config, self.temp_file, indent=2)
        self.temp_file.close()
        
        # Create validator with temp config
        self.validator = WorkspaceValidator(self.temp_file.name)
    
    def teardown_method(self):
        """Clean up temporary files."""
        if hasattr(self, 'temp_file'):
            try:
                os.unlink(self.temp_file.name)
            except FileNotFoundError:
                pass
    
    def test_workspace_config_loading(self):
        """Test that workspace configuration loads correctly."""
        assert len(self.validator.workspaces) == 4
        assert "DeckFusion Dev" in self.validator.workspaces
        assert "PsyOPTIMAL" in self.validator.workspaces
        assert "PsyOPTIMAL Dev" in self.validator.workspaces
        assert "FlexTrip" in self.validator.workspaces
        
        # Check DeckFusion configuration
        deckfusion = self.validator.workspaces["DeckFusion Dev"]
        assert deckfusion.workspace_type == WorkspaceType.DECKFUSION
        assert deckfusion.notion_database_id == "48a27df3-0342-4aa4-bd4c-0dec1ff908f4"
        assert "/Users/valorengels/src/deckfusion" in deckfusion.allowed_directories
        assert "-1008888888888" in deckfusion.telegram_chat_ids
        
        # Check PsyOPTIMAL configuration
        psyoptimal = self.validator.workspaces["PsyOPTIMAL"]
        assert psyoptimal.workspace_type == WorkspaceType.PSYOPTIMAL
        assert psyoptimal.notion_database_id == "1d22bc89-4d10-8079-8dcb-e7813b006c5c"
        assert "/Users/valorengels/src/psyoptimal" in psyoptimal.allowed_directories
        assert "-1001234567890" in psyoptimal.telegram_chat_ids
    
    def test_chat_to_workspace_mapping(self):
        """Test chat ID to workspace mapping."""
        # Test DeckFusion chat
        workspace = self.validator.get_workspace_for_chat("-1008888888888")
        assert workspace == "DeckFusion Dev"
        
        # Test PsyOPTIMAL chat
        workspace = self.validator.get_workspace_for_chat("-1001234567890")
        assert workspace == "PsyOPTIMAL"
        
        # Test FlexTrip chat
        workspace = self.validator.get_workspace_for_chat("-1009876543210")
        assert workspace == "FlexTrip"
        
        # Test unmapped chat
        workspace = self.validator.get_workspace_for_chat("-1999999999")
        assert workspace is None
    
    def test_notion_access_validation_success(self):
        """Test successful Notion access validation."""
        # DeckFusion chat accessing DeckFusion workspace
        self.validator.validate_notion_access("-1008888888888", "DeckFusion Dev")
        
        # PsyOPTIMAL chat accessing PsyOPTIMAL workspace
        self.validator.validate_notion_access("-1001234567890", "PsyOPTIMAL")
        
        # Test alias support
        self.validator.validate_notion_access("-1008888888888", "deckfusion")
        self.validator.validate_notion_access("-1001234567890", "psy")
    
    def test_notion_access_validation_violations(self):
        """Test Notion access validation security violations."""
        # DeckFusion chat trying to access PsyOPTIMAL workspace
        with pytest.raises(WorkspaceAccessError, match="STRICT ISOLATION VIOLATION"):
            self.validator.validate_notion_access("-1008888888888", "PsyOPTIMAL")
        
        # PsyOPTIMAL chat trying to access DeckFusion workspace
        with pytest.raises(WorkspaceAccessError, match="STRICT ISOLATION VIOLATION"):
            self.validator.validate_notion_access("-1001234567890", "DeckFusion Dev")
        
        # Unmapped chat trying to access any workspace
        with pytest.raises(WorkspaceAccessError, match="not mapped to any workspace"):
            self.validator.validate_notion_access("-1999999999", "DeckFusion Dev")
    
    def test_directory_access_validation_success(self):
        """Test successful directory access validation."""
        # DeckFusion chat accessing DeckFusion directory
        self.validator.validate_directory_access("-1008888888888", "/Users/valorengels/src/deckfusion")
        self.validator.validate_directory_access("-1008888888888", "/Users/valorengels/src/deckfusion/src/main.py")
        
        # PsyOPTIMAL chat accessing PsyOPTIMAL directory
        self.validator.validate_directory_access("-1001234567890", "/Users/valorengels/src/psyoptimal")
        self.validator.validate_directory_access("-1001234567890", "/Users/valorengels/src/psyoptimal/config.py")
    
    def test_directory_access_validation_violations(self):
        """Test directory access validation security violations."""
        # DeckFusion chat trying to access PsyOPTIMAL directory
        with pytest.raises(WorkspaceAccessError, match="STRICT DIRECTORY ISOLATION VIOLATION"):
            self.validator.validate_directory_access("-1008888888888", "/Users/valorengels/src/psyoptimal")
        
        # PsyOPTIMAL chat trying to access DeckFusion directory
        with pytest.raises(WorkspaceAccessError, match="STRICT DIRECTORY ISOLATION VIOLATION"):
            self.validator.validate_directory_access("-1001234567890", "/Users/valorengels/src/deckfusion")
        
        # Chat trying to access unauthorized directory
        with pytest.raises(WorkspaceAccessError, match="STRICT DIRECTORY ISOLATION VIOLATION"):
            self.validator.validate_directory_access("-1008888888888", "/Users/valorengels/src/unauthorized")
        
        # Unmapped chat trying to access any directory
        with pytest.raises(WorkspaceAccessError, match="not mapped to any workspace"):
            self.validator.validate_directory_access("-1999999999", "/Users/valorengels/src/deckfusion")
    
    def test_mcp_notion_tools_security(self):
        """Test MCP Notion tools enforce security boundaries."""
        # Test successful access
        with patch('mcp_servers.notion_tools.query_notion_workspace_sync') as mock_query:
            mock_query.return_value = "✅ Query successful"
            
            result = query_notion_projects("DeckFusion Dev", "What tasks are ready?", "-1008888888888")
            assert "Query successful" in result
            mock_query.assert_called_once_with("DeckFusion Dev", "What tasks are ready?")
        
        # Test access violation
        result = query_notion_projects("PsyOPTIMAL", "What tasks are ready?", "-1008888888888")
        assert "❌ Access Denied" in result
        assert "STRICT ISOLATION VIOLATION" in result
    
    @patch.dict(os.environ, {
        'TELEGRAM_ALLOWED_GROUPS': '-1008888888888,-1001234567890',
        'TELEGRAM_ALLOW_DMS': 'true'
    })
    def test_telegram_environment_validation_success(self):
        """Test successful Telegram environment validation."""
        result = validate_telegram_environment()
        
        assert result["status"] == "valid"
        assert result["allowed_groups"] == "configured"
        assert result["group_count"] == "2"
        assert result["allow_dms"] == "enabled"
        assert not result["errors"]
    
    @patch.dict(os.environ, {
        'TELEGRAM_ALLOWED_GROUPS': '',
        'TELEGRAM_ALLOW_DMS': 'false'
    })
    def test_telegram_environment_validation_warnings(self):
        """Test Telegram environment validation with warnings."""
        result = validate_telegram_environment()
        
        assert result["status"] == "errors"
        assert result["allowed_groups"] == "not_configured"
        assert result["allow_dms"] == "disabled"
        assert len(result["errors"]) > 0
        assert any("TELEGRAM_ALLOWED_GROUPS not set" in error for error in result["errors"])
    
    @patch.dict(os.environ, {
        'TELEGRAM_ALLOWED_GROUPS': 'invalid,format,here',
        'TELEGRAM_ALLOW_DMS': 'maybe'
    })
    def test_telegram_environment_validation_errors(self):
        """Test Telegram environment validation with errors."""
        with pytest.raises(WorkspaceAccessError, match="Invalid TELEGRAM_ALLOWED_GROUPS format"):
            validate_telegram_environment()
    
    @patch.dict(os.environ, {
        'TELEGRAM_ALLOWED_GROUPS': '-1008888888888,-1001234567890',
        'TELEGRAM_ALLOW_DMS': 'true'
    })
    def test_chat_whitelist_access_validation(self):
        """Test chat whitelist access validation."""
        # Test allowed group
        assert validate_chat_whitelist_access(-1008888888888, is_private=False) is True
        assert validate_chat_whitelist_access(-1001234567890, is_private=False) is True
        
        # Test denied group
        assert validate_chat_whitelist_access(-1999999999, is_private=False) is False
        
        # Test allowed DM
        assert validate_chat_whitelist_access(123456789, is_private=True) is True
    
    @patch.dict(os.environ, {
        'TELEGRAM_ALLOWED_GROUPS': '-1008888888888',
        'TELEGRAM_ALLOW_DMS': 'false'
    })
    def test_chat_whitelist_access_validation_restricted(self):
        """Test chat whitelist access validation with restrictions."""
        # Test denied DM when DMs are disabled
        assert validate_chat_whitelist_access(123456789, is_private=True) is False
        
        # Test denied group not in whitelist
        assert validate_chat_whitelist_access(-1001234567890, is_private=False) is False
    
    async def test_list_active_dialogs_integration(self):
        """Test list_active_dialogs() integration and security boundaries."""
        # Create mock client
        client = TelegramClient()
        client.client = AsyncMock()
        client.client.is_connected = True
        
        from pyrogram.enums import ChatType
        from datetime import datetime
        
        # Mock dialogs with security test data
        mock_dialogs = [
            MockDialog(
                MockChat(
                    chat_id=-1008888888888,  # DeckFusion chat
                    chat_type=ChatType.SUPERGROUP,
                    title="DeckFusion Dev Team",
                    username="deckfusion_dev",
                    members_count=25
                ),
                unread_count=3
            ),
            MockDialog(
                MockChat(
                    chat_id=-1001234567890,  # PsyOPTIMAL chat
                    chat_type=ChatType.SUPERGROUP,
                    title="PsyOPTIMAL Team",
                    username="psyoptimal_team",
                    members_count=15
                ),
                unread_count=1
            ),
            MockDialog(
                MockChat(
                    chat_id=123456789,  # DM
                    chat_type=ChatType.PRIVATE,
                    first_name="Test",
                    last_name="User",
                    username="testuser"
                ),
                unread_count=0
            )
        ]
        
        # Mock the get_dialogs method
        async def mock_get_dialogs():
            for dialog in mock_dialogs:
                yield dialog
        
        client.client.get_dialogs = mock_get_dialogs
        
        # Test successful dialogs listing
        dialogs_data, error = await list_telegram_dialogs_safe(client)
        
        assert error is None
        assert dialogs_data is not None
        assert dialogs_data['total_groups'] == 2
        assert dialogs_data['total_dms'] == 1
        assert dialogs_data['total_dialogs'] == 3
        
        # Verify chat IDs are correctly categorized
        group_ids = [group['id'] for group in dialogs_data['groups']]
        assert -1008888888888 in group_ids  # DeckFusion
        assert -1001234567890 in group_ids  # PsyOPTIMAL
        
        dm_ids = [dm['id'] for dm in dialogs_data['dms']]
        assert 123456789 in dm_ids
    
    def test_complete_security_boundary_enforcement(self):
        """Test complete security boundary enforcement across all components."""
        # Test convenience function
        with pytest.raises(WorkspaceAccessError, match="STRICT ISOLATION VIOLATION"):
            validate_workspace_access(
                chat_id="-1008888888888",  # DeckFusion chat
                workspace_name="PsyOPTIMAL",  # Trying to access PsyOPTIMAL
                file_path="/Users/valorengels/src/psyoptimal"  # PsyOPTIMAL directory
            )
        
        # Test successful validation
        validate_workspace_access(
            chat_id="-1008888888888",  # DeckFusion chat
            workspace_name="DeckFusion Dev",  # Accessing DeckFusion
            file_path="/Users/valorengels/src/deckfusion"  # DeckFusion directory
        )
    
    def test_workspace_isolation_matrix(self):
        """Test comprehensive workspace isolation matrix."""
        # Define test matrix: (chat_id, workspace, should_succeed)
        test_cases = [
            # DeckFusion chat access tests
            ("-1008888888888", "DeckFusion Dev", True),
            ("-1008888888888", "deckfusion", True),  # Alias
            ("-1008888888888", "PsyOPTIMAL", False),
            ("-1008888888888", "FlexTrip", False),
            
            # PsyOPTIMAL chat access tests
            ("-1001234567890", "PsyOPTIMAL", True),
            ("-1001234567890", "psy", True),  # Alias
            ("-1001234567890", "DeckFusion Dev", False),
            ("-1001234567890", "FlexTrip", False),
            
            # FlexTrip chat access tests
            ("-1009876543210", "FlexTrip", True),
            ("-1009876543210", "flex", True),  # Alias
            ("-1009876543210", "DeckFusion Dev", False),
            ("-1009876543210", "PsyOPTIMAL", False),
        ]
        
        for chat_id, workspace, should_succeed in test_cases:
            if should_succeed:
                # Should not raise exception
                self.validator.validate_notion_access(chat_id, workspace)
            else:
                # Should raise WorkspaceAccessError
                with pytest.raises(WorkspaceAccessError):
                    self.validator.validate_notion_access(chat_id, workspace)
    
    def test_directory_isolation_matrix(self):
        """Test comprehensive directory isolation matrix."""
        # Define test matrix: (chat_id, directory_path, should_succeed)
        test_cases = [
            # DeckFusion chat directory access tests
            ("-1008888888888", "/Users/valorengels/src/deckfusion", True),
            ("-1008888888888", "/Users/valorengels/src/deckfusion/src", True),
            ("-1008888888888", "/Users/valorengels/src/psyoptimal", False),
            ("-1008888888888", "/Users/valorengels/src/flextrip", False),
            
            # PsyOPTIMAL chat directory access tests
            ("-1001234567890", "/Users/valorengels/src/psyoptimal", True),
            ("-1001234567890", "/Users/valorengels/src/psyoptimal/config", True),
            ("-1001234567890", "/Users/valorengels/src/deckfusion", False),
            ("-1001234567890", "/Users/valorengels/src/flextrip", False),
            
            # FlexTrip chat directory access tests
            ("-1009876543210", "/Users/valorengels/src/flextrip", True),
            ("-1009876543210", "/Users/valorengels/src/flextrip/models", True),
            ("-1009876543210", "/Users/valorengels/src/deckfusion", False),
            ("-1009876543210", "/Users/valorengels/src/psyoptimal", False),
        ]
        
        for chat_id, directory_path, should_succeed in test_cases:
            if should_succeed:
                # Should not raise exception
                self.validator.validate_directory_access(chat_id, directory_path)
            else:
                # Should raise WorkspaceAccessError with directory isolation violation
                with pytest.raises(WorkspaceAccessError, match="STRICT DIRECTORY ISOLATION VIOLATION"):
                    self.validator.validate_directory_access(chat_id, directory_path)


# Mock classes for testing (same as in existing tests)
class MockDialog:
    """Mock dialog object for testing."""
    
    def __init__(self, chat, unread_count=0, top_message=None):
        self.chat = chat
        self.unread_messages_count = unread_count
        self.top_message = top_message


class MockChat:
    """Mock chat object for testing."""
    
    def __init__(self, chat_id, chat_type, title=None, first_name=None, **kwargs):
        self.id = chat_id
        self.type = chat_type
        self.title = title
        self.first_name = first_name
        
        # Optional attributes
        for key, value in kwargs.items():
            setattr(self, key, value)


async def run_security_tests():
    """Run all security tests."""
    test_class = TestChatWorkspaceSecurity()
    
    print("🔐 Testing Chat-to-Workspace Security Boundaries")
    print("=" * 60)
    
    # Test methods
    test_methods = [
        'test_workspace_config_loading',
        'test_chat_to_workspace_mapping', 
        'test_notion_access_validation_success',
        'test_notion_access_validation_violations',
        'test_directory_access_validation_success',
        'test_directory_access_validation_violations',
        'test_mcp_notion_tools_security',
        'test_telegram_environment_validation_success',
        'test_telegram_environment_validation_warnings',
        'test_telegram_environment_validation_errors',
        'test_chat_whitelist_access_validation',
        'test_chat_whitelist_access_validation_restricted',
        'test_complete_security_boundary_enforcement',
        'test_workspace_isolation_matrix',
        'test_directory_isolation_matrix'
    ]
    
    async_test_methods = [
        'test_list_active_dialogs_integration'
    ]
    
    total_tests = len(test_methods) + len(async_test_methods)
    passed_tests = 0
    failed_tests = 0
    
    # Run sync tests
    for test_name in test_methods:
        test_instance = TestChatWorkspaceSecurity()
        test_instance.setup_method()
        
        try:
            print(f"Running {test_name}...")
            getattr(test_instance, test_name)()
            print(f"✅ {test_name} passed")
            passed_tests += 1
        except Exception as e:
            print(f"❌ {test_name} failed: {e}")
            failed_tests += 1
        finally:
            test_instance.teardown_method()
    
    # Run async tests
    for test_name in async_test_methods:
        test_instance = TestChatWorkspaceSecurity()
        test_instance.setup_method()
        
        try:
            print(f"Running {test_name}...")
            await getattr(test_instance, test_name)()
            print(f"✅ {test_name} passed")
            passed_tests += 1
        except Exception as e:
            print(f"❌ {test_name} failed: {e}")
            failed_tests += 1
        finally:
            test_instance.teardown_method()
    
    print("\n" + "=" * 60)
    print(f"🔐 Security Test Results: {passed_tests}/{total_tests} passed, {failed_tests} failed")
    
    if failed_tests == 0:
        print("🎉 All security tests passed! Boundaries are properly enforced.")
        return True
    else:
        print(f"💥 {failed_tests} security tests failed - SECURITY VULNERABILITIES DETECTED")
        return False


if __name__ == "__main__":
    success = asyncio.run(run_security_tests())
    sys.exit(0 if success else 1)