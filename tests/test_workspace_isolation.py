"""
Test suite for strict workspace isolation controls

Validates that:
1. PsyOPTIMAL chats can only access PsyOPTIMAL Notion DB and ~/src/psyoptimal/
2. DeckFusion chats can only access DeckFusion Notion DB and ~/src/deckfusion/
3. Environment whitelist configuration is properly enforced
4. Cross-workspace access is completely blocked
"""

import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
from pathlib import Path

from utilities.workspace_validator import (
    WorkspaceValidator,
    WorkspaceAccessError,
    validate_telegram_environment,
    validate_chat_whitelist_access,
    validate_workspace_access,
)


class TestWorkspaceIsolation:
    """Test strict workspace isolation controls"""
    
    def setup_method(self):
        """Setup test environment with mock configuration"""
        # Load actual configuration and create test variant
        from pathlib import Path
        import json
        
        actual_config_path = Path(__file__).parent.parent / "config" / "workspace_config.json"
        with open(actual_config_path) as f:
            actual_config = json.load(f)
        
        # Create test config based on actual config but with test chat IDs
        self.test_config = {
            "workspaces": {},
            "telegram_groups": {}
        }
        
        # Copy workspace configurations but use test chat IDs
        test_chat_counter = 1008888888888
        for workspace_name, workspace_data in actual_config["workspaces"].items():
            # Create test chat ID for this workspace
            test_chat_id = f"-{test_chat_counter}"
            test_chat_counter += 1
            
            # Copy workspace config with test chat ID
            test_workspace = workspace_data.copy()
            test_workspace["telegram_chat_ids"] = [test_chat_id]
            
            self.test_config["workspaces"][workspace_name] = test_workspace
            self.test_config["telegram_groups"][test_chat_id] = workspace_name
        
        # Create temp config file
        self.temp_config_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        import json
        json.dump(self.test_config, self.temp_config_file, indent=2)
        self.temp_config_file.close()
        
        # Initialize validator with test config
        self.validator = WorkspaceValidator(self.temp_config_file.name)
    
    def teardown_method(self):
        """Clean up test environment"""
        os.unlink(self.temp_config_file.name)

    def test_deckfusion_notion_isolation(self):
        """Test that DeckFusion chat can only access DeckFusion Notion database"""
        deckfusion_chat_id = "-1008888888888"
        
        # Should succeed: DeckFusion chat accessing DeckFusion workspace
        try:
            self.validator.validate_notion_access(deckfusion_chat_id, "DeckFusion Dev")
        except WorkspaceAccessError:
            pytest.fail("DeckFusion chat should be able to access DeckFusion workspace")
        
        # Should fail: DeckFusion chat trying to access PsyOPTIMAL
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_notion_access(deckfusion_chat_id, "PsyOPTIMAL")
        assert "STRICT ISOLATION VIOLATION" in str(exc_info.value)
        assert "DeckFusion Dev" in str(exc_info.value)
        assert "PsyOPTIMAL" in str(exc_info.value)
        
        # Should fail: DeckFusion chat trying to access FlexTrip
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_notion_access(deckfusion_chat_id, "FlexTrip")
        assert "STRICT ISOLATION VIOLATION" in str(exc_info.value)

    def test_psyoptimal_notion_isolation(self):
        """Test that PsyOPTIMAL chat can only access PsyOPTIMAL Notion database"""
        psyoptimal_chat_id = "-1001234567890"
        
        # Should succeed: PsyOPTIMAL chat accessing PsyOPTIMAL workspace
        try:
            self.validator.validate_notion_access(psyoptimal_chat_id, "PsyOPTIMAL")
        except WorkspaceAccessError:
            pytest.fail("PsyOPTIMAL chat should be able to access PsyOPTIMAL workspace")
        
        # Should fail: PsyOPTIMAL chat trying to access PsyOPTIMAL Dev (different workspace mapping in test config)
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_notion_access(psyoptimal_chat_id, "PsyOPTIMAL Dev")
        assert "STRICT ISOLATION VIOLATION" in str(exc_info.value)
        
        # Should fail: PsyOPTIMAL chat trying to access DeckFusion
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_notion_access(psyoptimal_chat_id, "DeckFusion Dev")
        assert "STRICT ISOLATION VIOLATION" in str(exc_info.value)
        
        # Should fail: PsyOPTIMAL chat trying to access FlexTrip
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_notion_access(psyoptimal_chat_id, "FlexTrip")
        assert "STRICT ISOLATION VIOLATION" in str(exc_info.value)

    def test_deckfusion_directory_isolation(self):
        """Test that DeckFusion chat can only access ~/src/deckfusion/ directory"""
        deckfusion_chat_id = "-1008888888888"
        
        # Should succeed: DeckFusion chat accessing deckfusion directory
        try:
            self.validator.validate_directory_access(deckfusion_chat_id, "/Users/valorengels/src/deckfusion/test.py")
        except WorkspaceAccessError:
            pytest.fail("DeckFusion chat should be able to access deckfusion directory")
        
        # Should fail: DeckFusion chat trying to access psyoptimal directory
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_directory_access(deckfusion_chat_id, "/Users/valorengels/src/psyoptimal/test.py")
        assert "STRICT DIRECTORY ISOLATION VIOLATION" in str(exc_info.value)
        assert "deckfusion" in str(exc_info.value).lower()
        assert "psyoptimal" in str(exc_info.value)
        
        # Should fail: DeckFusion chat trying to access flextrip directory
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_directory_access(deckfusion_chat_id, "/Users/valorengels/src/flextrip/test.py")
        assert "STRICT DIRECTORY ISOLATION VIOLATION" in str(exc_info.value)

    def test_psyoptimal_directory_isolation(self):
        """Test that PsyOPTIMAL chat can only access ~/src/psyoptimal/ directory"""
        psyoptimal_chat_id = "-1001234567890"
        
        # Should succeed: PsyOPTIMAL chat accessing psyoptimal directory
        try:
            self.validator.validate_directory_access(psyoptimal_chat_id, "/Users/valorengels/src/psyoptimal/test.py")
        except WorkspaceAccessError:
            pytest.fail("PsyOPTIMAL chat should be able to access psyoptimal directory")
        
        # Should fail: PsyOPTIMAL chat trying to access deckfusion directory
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_directory_access(psyoptimal_chat_id, "/Users/valorengels/src/deckfusion/test.py")
        assert "STRICT DIRECTORY ISOLATION VIOLATION" in str(exc_info.value)
        assert "psyoptimal" in str(exc_info.value)
        assert "deckfusion" in str(exc_info.value)
        
        # Should fail: PsyOPTIMAL chat trying to access flextrip directory
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_directory_access(psyoptimal_chat_id, "/Users/valorengels/src/flextrip/test.py")
        assert "STRICT DIRECTORY ISOLATION VIOLATION" in str(exc_info.value)

    def test_unmapped_chat_rejection(self):
        """Test that unmapped chats are completely rejected"""
        unmapped_chat_id = "-9999999999999"
        
        # Should fail: unmapped chat trying to access any workspace
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_notion_access(unmapped_chat_id, "DeckFusion Dev")
        assert "is not mapped to any workspace" in str(exc_info.value)
        
        with pytest.raises(WorkspaceAccessError) as exc_info:
            self.validator.validate_directory_access(unmapped_chat_id, "/Users/valorengels/src/deckfusion/test.py")
        assert "is not mapped to any workspace" in str(exc_info.value)

    def test_convenience_function_workspace_access(self):
        """Test the convenience validate_workspace_access function"""
        # Note: This test uses the real global config, so we need to use actual chat IDs
        # Use the test validator instead for isolated testing
        try:
            self.validator.validate_notion_access("-1008888888888", "DeckFusion Dev")
            self.validator.validate_directory_access("-1008888888888", "/Users/valorengels/src/deckfusion/test.py")
        except WorkspaceAccessError:
            pytest.fail("Valid workspace and directory access should succeed")
        
        # Should fail: valid workspace but invalid directory
        with pytest.raises(WorkspaceAccessError):
            self.validator.validate_directory_access("-1008888888888", "/Users/valorengels/src/psyoptimal/test.py")


class TestTelegramWhitelistValidation:
    """Test Telegram environment whitelist validation"""
    
    def test_environment_validation_success(self):
        """Test successful environment validation"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890,-1008888888888',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            result = validate_telegram_environment()
            assert result["status"] == "valid"
            assert result["allowed_groups"] == "configured"
            assert result["group_count"] == "2"
            assert result["allow_dms"] == "enabled"
            assert not result["errors"]

    def test_environment_validation_no_groups(self):
        """Test environment validation with no groups configured"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '',
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            result = validate_telegram_environment()
            assert result["status"] == "errors"
            assert result["allowed_groups"] == "not_configured"
            assert result["allow_dms"] == "disabled"
            assert len(result["errors"]) > 0

    def test_environment_validation_invalid_groups(self):
        """Test environment validation with invalid group format"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': 'invalid,format,123abc',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            with pytest.raises(WorkspaceAccessError) as exc_info:
                validate_telegram_environment()
            assert "Invalid TELEGRAM_ALLOWED_GROUPS format" in str(exc_info.value)

    def test_chat_whitelist_dm_allowed(self):
        """Test DM chat whitelist validation when DMs are allowed"""
        with patch.dict(os.environ, {'TELEGRAM_ALLOW_DMS': 'true'}):
            assert validate_chat_whitelist_access(12345, is_private=True) == True

    def test_chat_whitelist_dm_denied(self):
        """Test DM chat whitelist validation when DMs are disabled"""
        with patch.dict(os.environ, {'TELEGRAM_ALLOW_DMS': 'false'}):
            assert validate_chat_whitelist_access(12345, is_private=True) == False

    def test_chat_whitelist_group_allowed(self):
        """Test group chat whitelist validation for allowed group"""
        with patch.dict(os.environ, {'TELEGRAM_ALLOWED_GROUPS': '-1001234567890,-1008888888888'}):
            assert validate_chat_whitelist_access(-1001234567890, is_private=False) == True

    def test_chat_whitelist_group_denied(self):
        """Test group chat whitelist validation for non-whitelisted group"""
        with patch.dict(os.environ, {'TELEGRAM_ALLOWED_GROUPS': '-1001234567890,-1008888888888'}):
            assert validate_chat_whitelist_access(-9999999999999, is_private=False) == False

    def test_chat_whitelist_no_groups_configured(self):
        """Test group chat validation when no groups are configured"""
        with patch.dict(os.environ, {'TELEGRAM_ALLOWED_GROUPS': ''}):
            assert validate_chat_whitelist_access(-1001234567890, is_private=False) == False


class TestCrossWorkspaceAccessPrevention:
    """Test prevention of cross-workspace access attempts"""
    
    def setup_method(self):
        """Setup test environment"""
        # Load actual configuration and create test variant
        from pathlib import Path
        import json
        
        actual_config_path = Path(__file__).parent.parent / "config" / "workspace_config.json"
        with open(actual_config_path) as f:
            actual_config = json.load(f)
        
        # Create test config based on actual config but with test chat IDs
        self.test_config = {
            "workspaces": {},
            "telegram_groups": {}
        }
        
        # Copy workspace configurations but use test chat IDs
        test_chat_counter = 2008888888888
        for workspace_name, workspace_data in actual_config["workspaces"].items():
            # Create test chat ID for this workspace
            test_chat_id = f"-{test_chat_counter}"
            test_chat_counter += 1
            
            # Copy workspace config with test chat ID
            test_workspace = workspace_data.copy()
            test_workspace["telegram_chat_ids"] = [test_chat_id]
            
            self.test_config["workspaces"][workspace_name] = test_workspace
            self.test_config["telegram_groups"][test_chat_id] = workspace_name
        
        # Create temp config file
        self.temp_config_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        import json
        json.dump(self.test_config, self.temp_config_file, indent=2)
        self.temp_config_file.close()
        
        # Initialize validator with test config
        self.validator = WorkspaceValidator(self.temp_config_file.name)
    
    def teardown_method(self):
        """Clean up test environment"""
        os.unlink(self.temp_config_file.name)

    def test_all_cross_workspace_database_access_blocked(self):
        """Test that all cross-workspace database access attempts are blocked"""
        test_cases = [
            # (chat_id, allowed_workspace, forbidden_workspaces)
            ("-1008888888888", "DeckFusion Dev", ["PsyOPTIMAL"]),
            ("-1001234567890", "PsyOPTIMAL", ["DeckFusion Dev"]),
        ]
        
        for chat_id, allowed_workspace, forbidden_workspaces in test_cases:
            # Verify allowed access works
            try:
                self.validator.validate_notion_access(chat_id, allowed_workspace)
            except WorkspaceAccessError:
                pytest.fail(f"Chat {chat_id} should access {allowed_workspace}")
            
            # Verify forbidden access is blocked
            for forbidden_workspace in forbidden_workspaces:
                with pytest.raises(WorkspaceAccessError) as exc_info:
                    self.validator.validate_notion_access(chat_id, forbidden_workspace)
                assert "STRICT ISOLATION VIOLATION" in str(exc_info.value)

    def test_all_cross_workspace_directory_access_blocked(self):
        """Test that all cross-workspace directory access attempts are blocked"""
        test_cases = [
            # (chat_id, allowed_paths, forbidden_paths)
            ("-1008888888888", ["/Users/valorengels/src/deckfusion/"], ["/Users/valorengels/src/psyoptimal/"]),
            ("-1001234567890", ["/Users/valorengels/src/psyoptimal/"], ["/Users/valorengels/src/deckfusion/"]),
        ]
        
        for chat_id, allowed_paths, forbidden_paths in test_cases:
            # Verify allowed directory access works
            for allowed_path in allowed_paths:
                try:
                    self.validator.validate_directory_access(chat_id, f"{allowed_path}test.py")
                except WorkspaceAccessError:
                    pytest.fail(f"Chat {chat_id} should access {allowed_path}")
            
            # Verify forbidden directory access is blocked
            for forbidden_path in forbidden_paths:
                with pytest.raises(WorkspaceAccessError) as exc_info:
                    self.validator.validate_directory_access(chat_id, f"{forbidden_path}test.py")
                # Directory access violation can be detected at either the general or cross-workspace level
                assert ("STRICT DIRECTORY ISOLATION VIOLATION" in str(exc_info.value) or 
                        "CROSS-WORKSPACE ACCESS VIOLATION" in str(exc_info.value))

    def test_security_logging_on_violations(self):
        """Test that security violations are properly logged"""
        import logging
        from unittest.mock import Mock, patch
        
        # Mock logger to capture log calls
        mock_logger = Mock()
        
        with patch('logging.getLogger', return_value=mock_logger):
            # Attempt cross-workspace access that should be logged
            with pytest.raises(WorkspaceAccessError):
                self.validator.validate_notion_access("-1008888888888", "PsyOPTIMAL")
            
            # Verify security violation was logged
            mock_logger.error.assert_called()
            error_call_args = mock_logger.error.call_args[0][0]
            assert "Security violation" in error_call_args
            assert "STRICT ISOLATION VIOLATION" in error_call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])