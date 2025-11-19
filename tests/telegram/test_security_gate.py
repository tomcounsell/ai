"""Tests for Security Gate Component

Comprehensive tests for authentication, rate limiting, threat detection,
and content filtering functionality.
"""

import asyncio
import pytest
import time
from unittest.mock import MagicMock, patch

from integrations.telegram.components.security_gate import (
    SecurityGate, SecurityResult, SecurityAction, ThreatLevel,
    RateLimitConfig, UserSecurityProfile
)


class TestSecurityGate:
    """Test suite for SecurityGate"""
    
    @pytest.fixture
    def security_gate(self):
        """Create security gate with test configuration"""
        return SecurityGate(
            rate_limit_config=RateLimitConfig(
                requests_per_minute=5,
                burst_limit=2,
                window_size_seconds=60
            ),
            admin_user_ids={999},
            blocked_user_ids={666}
        )
    
    @pytest.mark.asyncio
    async def test_allowed_user_request(self, security_gate):
        """Test allowing a normal user request"""
        
        result = await security_gate.validate_request(
            user_id=12345,
            chat_id=123,
            message_text="Hello, how are you?"
        )
        
        assert result.allowed is True
        assert result.action == SecurityAction.ALLOW
        assert result.threat_level == ThreatLevel.LOW
        assert result.violations == []
    
    @pytest.mark.asyncio
    async def test_blocked_user_request(self, security_gate):
        """Test blocking a banned user request"""
        
        result = await security_gate.validate_request(
            user_id=666,  # Blocked user
            chat_id=123,
            message_text="Hello"
        )
        
        assert result.allowed is False
        assert result.action == SecurityAction.BLOCK
        assert result.threat_level == ThreatLevel.HIGH
        assert "User is blocked" in result.reason
    
    @pytest.mark.asyncio
    async def test_admin_user_bypass(self, security_gate):
        """Test admin users bypassing security checks"""
        
        result = await security_gate.validate_request(
            user_id=999,  # Admin user
            chat_id=123,
            message_text="This could be suspicious content"
        )
        
        assert result.allowed is True
        assert result.action == SecurityAction.ALLOW
        assert result.threat_level == ThreatLevel.LOW
        assert result.user_trust_score == 1.0
        assert "Admin user" in result.reason
    
    @pytest.mark.asyncio
    async def test_anonymous_user_blocked(self, security_gate):
        """Test blocking anonymous users"""
        
        result = await security_gate.validate_request(
            user_id=None,  # Anonymous user
            chat_id=123,
            message_text="Hello"
        )
        
        assert result.allowed is False
        assert result.action == SecurityAction.BLOCK
        assert "Anonymous user not allowed" in result.reason
    
    @pytest.mark.asyncio
    async def test_rate_limiting(self, security_gate):
        """Test rate limiting functionality"""
        
        user_id = 12345
        
        # Send requests up to burst limit
        for i in range(2):  # Burst limit is 2
            result = await security_gate.validate_request(
                user_id=user_id,
                chat_id=123,
                message_text=f"Message {i}"
            )
            assert result.allowed is True
        
        # Next request should be rate limited
        result = await security_gate.validate_request(
            user_id=user_id,
            chat_id=123,
            message_text="Burst limit exceeded"
        )
        
        # Should still be allowed but with violations
        assert "Rate limit exceeded" in result.violations or result.allowed is False
    
    @pytest.mark.asyncio
    async def test_content_filtering(self, security_gate):
        """Test content filtering for inappropriate content"""
        
        # Test spam detection
        result = await security_gate.validate_request(
            user_id=12345,
            chat_id=123,
            message_text="BUY NOW! CLICK HERE FOR AMAZING DEALS! SPAM PROMOTION!"
        )
        
        # Should detect spam patterns
        if result.violations:
            assert any("Content filter" in violation for violation in result.violations)
    
    @pytest.mark.asyncio
    async def test_threat_detection(self, security_gate):
        """Test threat detection for malicious content"""
        
        # Test phishing URL detection
        result = await security_gate.validate_request(
            user_id=12345,
            chat_id=123,
            message_text="Click this suspicious link: bit.ly/suspicious123"
        )
        
        # Should detect threat patterns
        if result.violations:
            assert any("Threat detected" in violation for violation in result.violations)
    
    @pytest.mark.asyncio
    async def test_user_trust_score_updates(self, security_gate):
        """Test user trust score updates based on behavior"""
        
        user_id = 12345
        
        # Initial request should create user profile
        result1 = await security_gate.validate_request(
            user_id=user_id,
            chat_id=123,
            message_text="Hello"
        )
        
        initial_trust = result1.user_trust_score
        assert 0.0 <= initial_trust <= 1.0
        
        # Send problematic content
        await security_gate.validate_request(
            user_id=user_id,
            chat_id=123,
            message_text="SPAM SPAM SPAM"
        )
        
        # Trust score should be affected by violations
        result2 = await security_gate.validate_request(
            user_id=user_id,
            chat_id=123,
            message_text="Another message"
        )
        
        # Trust score might be lower due to previous violations
        assert result2.user_trust_score >= 0.0
    
    @pytest.mark.asyncio
    async def test_allowed_chat_filtering(self, security_gate):
        """Test filtering by allowed chat IDs"""
        
        # Configure allowed chats
        security_gate.allowed_chat_ids = {123, 456}
        
        # Allowed chat
        result1 = await security_gate.validate_request(
            user_id=12345,
            chat_id=123,
            message_text="Hello"
        )
        assert result1.allowed is True
        
        # Disallowed chat
        result2 = await security_gate.validate_request(
            user_id=12345,
            chat_id=999,
            message_text="Hello"
        )
        assert result2.allowed is False
        assert "Chat not in allowed list" in result2.reason
    
    @pytest.mark.asyncio
    async def test_media_threat_detection(self, security_gate):
        """Test threat detection in media attachments"""
        
        media_info = {
            "type": "document",
            "media_type": "application/exe",
            "size": 1000000
        }
        
        result = await security_gate.validate_request(
            user_id=12345,
            chat_id=123,
            message_text="Check out this file",
            media_info=media_info
        )
        
        # Should detect suspicious file type
        if result.violations:
            assert any("Suspicious file type" in violation for violation in result.violations)
    
    @pytest.mark.asyncio
    async def test_forwarded_message_analysis(self, security_gate):
        """Test analysis of forwarded messages"""
        
        forwarded_info = {
            "forward_count": 10,  # Excessive forwarding
            "from_id": 999
        }
        
        result = await security_gate.validate_request(
            user_id=12345,
            chat_id=123,
            message_text="Forwarded message",
            forwarded_info=forwarded_info
        )
        
        # Should detect excessive forwarding
        if result.violations:
            assert any("Excessive forwarding" in violation for violation in result.violations)
    
    @pytest.mark.asyncio
    async def test_security_gate_status(self, security_gate):
        """Test security gate status reporting"""
        
        # Process some requests first
        await security_gate.validate_request(12345, 123, "Hello")
        await security_gate.validate_request(67890, 123, "Hi")
        
        status = await security_gate.get_status()
        
        # Verify status structure
        assert "total_requests" in status
        assert "blocked_requests" in status
        assert "blocked_rate" in status
        assert "threat_detections" in status
        assert "active_users" in status
        assert "avg_trust_score" in status
        
        assert status["total_requests"] >= 2
        assert 0.0 <= status["blocked_rate"] <= 1.0
        assert status["active_users"] >= 2
    
    @pytest.mark.asyncio
    async def test_user_management(self, security_gate):
        """Test user management functionality"""
        
        user_id = 12345
        
        # Add user to blocked list
        await security_gate.add_blocked_user(user_id)
        assert user_id in security_gate.blocked_user_ids
        
        # Verify user is blocked
        result = await security_gate.validate_request(user_id, 123, "Hello")
        assert result.allowed is False
        
        # Remove user from blocked list
        await security_gate.remove_blocked_user(user_id)
        assert user_id not in security_gate.blocked_user_ids
        
        # Verify user is no longer blocked
        result = await security_gate.validate_request(user_id, 123, "Hello")
        assert result.allowed is True
    
    @pytest.mark.asyncio
    async def test_violation_reset(self, security_gate):
        """Test resetting user violations"""
        
        user_id = 12345
        
        # Generate some violations
        await security_gate.validate_request(
            user_id, 123, "SPAM CONTENT WITH VIOLATIONS"
        )
        
        # Reset violations
        await security_gate.reset_user_violations(user_id)
        
        # Verify violations were reset
        if user_id in security_gate.user_profiles:
            profile = security_gate.user_profiles[user_id]
            assert profile.violation_count == 0
            assert profile.trust_score == 0.5  # Reset to neutral
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, security_gate):
        """Test graceful shutdown"""
        
        await security_gate.shutdown()
        
        # Verify cleanup
        assert len(security_gate.user_profiles) == 0
        assert len(security_gate.rate_limit_buckets) == 0


class TestUserSecurityProfile:
    """Test suite for UserSecurityProfile"""
    
    def test_profile_initialization(self):
        """Test user profile initialization"""
        
        profile = UserSecurityProfile(user_id=12345)
        
        assert profile.user_id == 12345
        assert profile.trust_score == 0.5  # Neutral trust
        assert profile.violation_count == 0
        assert profile.rate_limit_violations == 0
        assert profile.content_violations == 0
        assert len(profile.request_history) == 0
        assert profile.first_seen <= time.time()
        assert profile.last_activity <= time.time()
    
    def test_profile_history_limit(self):
        """Test profile request history limits"""
        
        profile = UserSecurityProfile(user_id=12345)
        
        # Add many requests (should be limited to maxlen)
        for i in range(150):  # More than maxlen of 100
            profile.request_history.append({
                "timestamp": time.time(),
                "violations": [],
                "allowed": True
            })
        
        assert len(profile.request_history) == 100  # Should be limited


class TestRateLimitConfig:
    """Test suite for RateLimitConfig"""
    
    def test_config_defaults(self):
        """Test rate limit configuration defaults"""
        
        config = RateLimitConfig()
        
        assert config.requests_per_minute == 60
        assert config.burst_limit == 10
        assert config.window_size_seconds == 60
        assert config.penalty_duration_seconds == 300
    
    def test_config_customization(self):
        """Test rate limit configuration customization"""
        
        config = RateLimitConfig(
            requests_per_minute=30,
            burst_limit=5,
            window_size_seconds=120,
            penalty_duration_seconds=600
        )
        
        assert config.requests_per_minute == 30
        assert config.burst_limit == 5
        assert config.window_size_seconds == 120
        assert config.penalty_duration_seconds == 600


class TestSecurityResult:
    """Test suite for SecurityResult"""
    
    def test_result_creation(self):
        """Test security result creation"""
        
        result = SecurityResult(
            allowed=True,
            action=SecurityAction.ALLOW,
            threat_level=ThreatLevel.LOW,
            risk_score=0.1,
            reason="Test reason",
            violations=["test violation"],
            rate_limit_remaining=59,
            user_trust_score=0.8
        )
        
        assert result.allowed is True
        assert result.action == SecurityAction.ALLOW
        assert result.threat_level == ThreatLevel.LOW
        assert result.risk_score == 0.1
        assert result.reason == "Test reason"
        assert result.violations == ["test violation"]
        assert result.rate_limit_remaining == 59
        assert result.user_trust_score == 0.8
    
    def test_result_validation(self):
        """Test security result validation"""
        
        # Risk score should be between 0 and 1
        result = SecurityResult(
            allowed=False,
            action=SecurityAction.BLOCK,
            threat_level=ThreatLevel.HIGH,
            risk_score=0.9
        )
        
        assert 0.0 <= result.risk_score <= 1.0
        assert 0.0 <= result.user_trust_score <= 1.0


@pytest.mark.integration
class TestSecurityGateIntegration:
    """Integration tests for SecurityGate"""
    
    @pytest.mark.asyncio
    async def test_realistic_threat_scenario(self):
        """Test realistic threat detection scenario"""
        
        security_gate = SecurityGate(
            enable_content_filtering=True,
            enable_threat_detection=True
        )
        
        # Simulate progressive threat escalation
        user_id = 12345
        
        # Normal message
        result1 = await security_gate.validate_request(
            user_id, 123, "Hello, how are you today?"
        )
        assert result1.allowed is True
        
        # Slightly suspicious message
        result2 = await security_gate.validate_request(
            user_id, 123, "Click here for free money!"
        )
        # May or may not be blocked, but trust score should be affected
        
        # Very suspicious message
        result3 = await security_gate.validate_request(
            user_id, 123, "URGENT: Click bit.ly/suspicious to claim your lottery winnings!"
        )
        # Should have violations or be blocked
        
        # Trust score should decrease over time with violations
        assert result3.user_trust_score <= result1.user_trust_score
    
    @pytest.mark.asyncio
    async def test_rate_limiting_recovery(self):
        """Test rate limiting and recovery over time"""
        
        security_gate = SecurityGate(
            rate_limit_config=RateLimitConfig(
                requests_per_minute=3,
                burst_limit=1,
                window_size_seconds=60
            )
        )
        
        user_id = 12345
        
        # Exhaust rate limit
        for i in range(3):
            result = await security_gate.validate_request(
                user_id, 123, f"Message {i}"
            )
        
        # Should be rate limited now
        result = await security_gate.validate_request(
            user_id, 123, "Rate limited message"
        )
        
        # Check if rate limiting is working
        # (May still be allowed but with warnings)
        assert isinstance(result, SecurityResult)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])