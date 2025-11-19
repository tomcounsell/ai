"""Security Gate Component

This module implements comprehensive security validation including authentication,
rate limiting, threat detection, and content filtering for incoming messages.
"""

import asyncio
import hashlib
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class ThreatLevel(Enum):
    """Security threat levels"""
    LOW = "low"
    MEDIUM = "medium" 
    HIGH = "high"
    CRITICAL = "critical"


class SecurityAction(Enum):
    """Actions to take based on security assessment"""
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    QUARANTINE = "quarantine"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration"""
    requests_per_minute: int = 60
    burst_limit: int = 10
    window_size_seconds: int = 60
    penalty_duration_seconds: int = 300


@dataclass 
class UserSecurityProfile:
    """User security profile and history"""
    user_id: int
    trust_score: float = 0.5  # 0.0 = untrusted, 1.0 = fully trusted
    violation_count: int = 0
    last_violation_time: float = 0.0
    rate_limit_violations: int = 0
    content_violations: int = 0
    request_history: deque = field(default_factory=lambda: deque(maxlen=100))
    first_seen: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


class SecurityResult(BaseModel):
    """Result of security validation"""
    
    allowed: bool = Field(..., description="Whether request is allowed")
    action: SecurityAction = Field(default=SecurityAction.ALLOW)
    threat_level: ThreatLevel = Field(default=ThreatLevel.LOW)
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: Optional[str] = Field(None, description="Reason for decision")
    violations: List[str] = Field(default_factory=list)
    rate_limit_remaining: int = Field(default=60)
    user_trust_score: float = Field(default=0.5)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SecurityGate:
    """
    Comprehensive security gate with authentication, rate limiting, 
    threat detection, and content filtering capabilities.
    """
    
    def __init__(
        self,
        rate_limit_config: Optional[RateLimitConfig] = None,
        enable_content_filtering: bool = True,
        enable_threat_detection: bool = True,
        enable_user_profiling: bool = True,
        admin_user_ids: Optional[Set[int]] = None,
        blocked_user_ids: Optional[Set[int]] = None,
        allowed_chat_ids: Optional[Set[int]] = None,
        content_filter_patterns: Optional[List[str]] = None
    ):
        """
        Initialize the security gate.
        
        Args:
            rate_limit_config: Rate limiting configuration
            enable_content_filtering: Enable content filtering
            enable_threat_detection: Enable threat detection
            enable_user_profiling: Enable user profiling and trust scores
            admin_user_ids: Set of admin user IDs with elevated privileges
            blocked_user_ids: Set of blocked user IDs
            allowed_chat_ids: Set of allowed chat IDs (None = all allowed)
            content_filter_patterns: Custom regex patterns for content filtering
        """
        self.rate_limit_config = rate_limit_config or RateLimitConfig()
        self.enable_content_filtering = enable_content_filtering
        self.enable_threat_detection = enable_threat_detection
        self.enable_user_profiling = enable_user_profiling
        
        # Access control
        self.admin_user_ids = admin_user_ids or set()
        self.blocked_user_ids = blocked_user_ids or set()
        self.allowed_chat_ids = allowed_chat_ids
        
        # User profiles and rate limiting
        self.user_profiles: Dict[int, UserSecurityProfile] = {}
        self.rate_limit_buckets: Dict[int, deque] = defaultdict(lambda: deque())
        
        # Content filtering patterns
        self.content_filter_patterns = self._compile_filter_patterns(
            content_filter_patterns or self._get_default_filter_patterns()
        )
        
        # Threat detection
        self.suspicious_patterns = self._compile_threat_patterns()
        
        # Statistics
        self.total_requests = 0
        self.blocked_requests = 0
        self.threat_detections = 0
        self.rate_limit_violations = 0
        
        logger.info(
            f"SecurityGate initialized with content_filtering={enable_content_filtering}, "
            f"threat_detection={enable_threat_detection}, "
            f"rate_limit={self.rate_limit_config.requests_per_minute}/min"
        )
    
    async def validate_request(
        self,
        user_id: Optional[int],
        chat_id: int,
        message_text: Optional[str] = None,
        media_info: Optional[Dict[str, Any]] = None,
        forwarded_info: Optional[Dict[str, Any]] = None
    ) -> SecurityResult:
        """
        Validate an incoming request through comprehensive security checks.
        
        Args:
            user_id: ID of the user sending the message
            chat_id: ID of the chat/channel
            message_text: Text content of the message
            media_info: Information about attached media
            forwarded_info: Information about forwarded messages
            
        Returns:
            SecurityResult indicating whether request should be allowed
        """
        self.total_requests += 1
        current_time = time.time()
        violations = []
        risk_score = 0.0
        
        try:
            # Basic validation
            if user_id is None:
                return SecurityResult(
                    allowed=False,
                    action=SecurityAction.BLOCK,
                    threat_level=ThreatLevel.MEDIUM,
                    risk_score=0.8,
                    reason="Anonymous user not allowed"
                )
            
            # Check blocked users
            if user_id in self.blocked_user_ids:
                self.blocked_requests += 1
                return SecurityResult(
                    allowed=False,
                    action=SecurityAction.BLOCK,
                    threat_level=ThreatLevel.HIGH,
                    risk_score=1.0,
                    reason="User is blocked"
                )
            
            # Check allowed chats
            if self.allowed_chat_ids is not None and chat_id not in self.allowed_chat_ids:
                return SecurityResult(
                    allowed=False,
                    action=SecurityAction.BLOCK,
                    threat_level=ThreatLevel.MEDIUM,
                    risk_score=0.7,
                    reason="Chat not in allowed list"
                )
            
            # Get or create user profile
            user_profile = self._get_user_profile(user_id)
            user_profile.last_activity = current_time
            
            # Admin users bypass most checks
            is_admin = user_id in self.admin_user_ids
            if is_admin:
                return SecurityResult(
                    allowed=True,
                    action=SecurityAction.ALLOW,
                    threat_level=ThreatLevel.LOW,
                    risk_score=0.0,
                    reason="Admin user",
                    user_trust_score=1.0
                )
            
            # Rate limiting check
            rate_limit_result = await self._check_rate_limit(user_id, current_time)
            if not rate_limit_result["allowed"]:
                self.rate_limit_violations += 1
                user_profile.rate_limit_violations += 1
                violations.append("Rate limit exceeded")
                risk_score += 0.4
            
            # Content filtering
            content_violations = []
            if self.enable_content_filtering and message_text:
                content_violations = await self._check_content_filter(message_text)
                if content_violations:
                    violations.extend(content_violations)
                    user_profile.content_violations += 1
                    risk_score += 0.3 * len(content_violations)
            
            # Threat detection
            threat_violations = []
            if self.enable_threat_detection:
                threat_violations = await self._detect_threats(
                    user_id, message_text, media_info, forwarded_info
                )
                if threat_violations:
                    violations.extend(threat_violations)
                    self.threat_detections += 1
                    risk_score += 0.5 * len(threat_violations)
            
            # Update user trust score
            if self.enable_user_profiling:
                user_profile = await self._update_user_trust_score(
                    user_profile, violations, current_time
                )
                risk_score += (1.0 - user_profile.trust_score) * 0.2
            
            # Determine final action
            risk_score = min(risk_score, 1.0)
            action, threat_level = self._determine_action(risk_score, violations)
            allowed = action == SecurityAction.ALLOW
            
            # Log violations
            if violations:
                user_profile.violation_count += len(violations)
                user_profile.last_violation_time = current_time
                
                logger.warning(
                    f"Security violations for user {user_id}: {violations}, "
                    f"risk_score={risk_score:.2f}, action={action.value}"
                )
            
            # Update request history
            user_profile.request_history.append({
                "timestamp": current_time,
                "chat_id": chat_id,
                "violations": violations,
                "risk_score": risk_score,
                "allowed": allowed
            })
            
            if not allowed:
                self.blocked_requests += 1
            
            return SecurityResult(
                allowed=allowed,
                action=action,
                threat_level=threat_level,
                risk_score=risk_score,
                reason="; ".join(violations) if violations else "Request validated",
                violations=violations,
                rate_limit_remaining=rate_limit_result["remaining"],
                user_trust_score=user_profile.trust_score,
                metadata={
                    "total_violations": user_profile.violation_count,
                    "user_age_hours": (current_time - user_profile.first_seen) / 3600,
                    "is_repeat_offender": user_profile.violation_count > 5
                }
            )
            
        except Exception as e:
            logger.error(f"Security gate error: {str(e)}", exc_info=True)
            return SecurityResult(
                allowed=False,
                action=SecurityAction.BLOCK,
                threat_level=ThreatLevel.CRITICAL,
                risk_score=1.0,
                reason=f"Security gate error: {str(e)}"
            )
    
    async def _check_rate_limit(
        self, 
        user_id: int, 
        current_time: float
    ) -> Dict[str, Any]:
        """Check if user is within rate limits"""
        bucket = self.rate_limit_buckets[user_id]
        window_start = current_time - self.rate_limit_config.window_size_seconds
        
        # Remove old requests
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        
        # Check burst limit
        recent_requests = sum(
            1 for timestamp in bucket 
            if timestamp > current_time - 10  # Last 10 seconds
        )
        
        if recent_requests >= self.rate_limit_config.burst_limit:
            return {
                "allowed": False,
                "remaining": 0,
                "reset_time": current_time + 10,
                "violation_type": "burst_limit"
            }
        
        # Check window limit
        if len(bucket) >= self.rate_limit_config.requests_per_minute:
            return {
                "allowed": False,
                "remaining": 0,
                "reset_time": bucket[0] + self.rate_limit_config.window_size_seconds,
                "violation_type": "window_limit"
            }
        
        # Add current request
        bucket.append(current_time)
        
        return {
            "allowed": True,
            "remaining": self.rate_limit_config.requests_per_minute - len(bucket),
            "reset_time": window_start + self.rate_limit_config.window_size_seconds
        }
    
    async def _check_content_filter(self, message_text: str) -> List[str]:
        """Check message content against filter patterns"""
        violations = []
        
        # Check against filter patterns
        for pattern_name, pattern in self.content_filter_patterns.items():
            if pattern.search(message_text):
                violations.append(f"Content filter: {pattern_name}")
        
        # Additional heuristic checks
        if len(message_text) > 4000:
            violations.append("Message too long")
        
        # Check for excessive capitalization
        if len(message_text) > 50:
            capital_ratio = sum(1 for c in message_text if c.isupper()) / len(message_text)
            if capital_ratio > 0.7:
                violations.append("Excessive capitalization")
        
        # Check for spam patterns
        repeated_chars = max(len(match.group()) for match in re.finditer(r'(.)\1+', message_text)) if message_text else 0
        if repeated_chars > 10:
            violations.append("Repeated characters spam")
        
        return violations
    
    async def _detect_threats(
        self,
        user_id: int,
        message_text: Optional[str],
        media_info: Optional[Dict[str, Any]],
        forwarded_info: Optional[Dict[str, Any]]
    ) -> List[str]:
        """Detect potential security threats"""
        threats = []
        
        if message_text:
            # Check for suspicious patterns
            for threat_name, pattern in self.suspicious_patterns.items():
                if pattern.search(message_text):
                    threats.append(f"Threat detected: {threat_name}")
        
        # Check media threats
        if media_info:
            media_type = media_info.get("type", "")
            media_size = media_info.get("size", 0)
            
            # Suspicious file types
            if media_type in ["exe", "scr", "bat", "com", "pif"]:
                threats.append("Suspicious file type")
            
            # Large files
            if media_size > 50 * 1024 * 1024:  # 50MB
                threats.append("Unusually large file")
        
        # Check forwarded message patterns
        if forwarded_info:
            # Multiple forwards (potential spam)
            forward_count = forwarded_info.get("forward_count", 0)
            if forward_count > 5:
                threats.append("Excessive forwarding chain")
        
        # Check user behavior patterns
        user_profile = self.user_profiles.get(user_id)
        if user_profile:
            # Rapid message sending
            recent_requests = [
                req for req in user_profile.request_history
                if time.time() - req["timestamp"] < 60
            ]
            if len(recent_requests) > 20:
                threats.append("Rapid message pattern")
            
            # Repeated violations
            if user_profile.violation_count > 10:
                threats.append("Repeat offender pattern")
        
        return threats
    
    async def _update_user_trust_score(
        self,
        user_profile: UserSecurityProfile,
        violations: List[str],
        current_time: float
    ) -> UserSecurityProfile:
        """Update user trust score based on behavior"""
        # Decay trust score for violations
        if violations:
            violation_penalty = len(violations) * 0.1
            user_profile.trust_score = max(0.0, user_profile.trust_score - violation_penalty)
        
        # Increase trust score for good behavior over time
        time_since_last_violation = current_time - user_profile.last_violation_time
        if time_since_last_violation > 86400:  # 24 hours
            trust_boost = min(0.05, time_since_last_violation / 86400 * 0.01)
            user_profile.trust_score = min(1.0, user_profile.trust_score + trust_boost)
        
        # Account age bonus
        account_age_hours = (current_time - user_profile.first_seen) / 3600
        if account_age_hours > 168:  # 1 week
            age_bonus = min(0.1, account_age_hours / 8760 * 0.05)  # Max bonus over 1 year
            user_profile.trust_score = min(1.0, user_profile.trust_score + age_bonus)
        
        return user_profile
    
    def _determine_action(
        self, 
        risk_score: float, 
        violations: List[str]
    ) -> Tuple[SecurityAction, ThreatLevel]:
        """Determine security action based on risk score and violations"""
        if risk_score >= 0.9:
            return SecurityAction.BLOCK, ThreatLevel.CRITICAL
        elif risk_score >= 0.7:
            return SecurityAction.QUARANTINE, ThreatLevel.HIGH
        elif risk_score >= 0.4:
            return SecurityAction.WARN, ThreatLevel.MEDIUM
        else:
            return SecurityAction.ALLOW, ThreatLevel.LOW
    
    def _get_user_profile(self, user_id: int) -> UserSecurityProfile:
        """Get or create user security profile"""
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = UserSecurityProfile(user_id=user_id)
        return self.user_profiles[user_id]
    
    def _compile_filter_patterns(self, patterns: List[str]) -> Dict[str, re.Pattern]:
        """Compile content filter patterns"""
        compiled = {}
        for pattern in patterns:
            try:
                name = pattern.split(":")[0] if ":" in pattern else f"pattern_{len(compiled)}"
                regex = pattern.split(":", 1)[1] if ":" in pattern else pattern
                compiled[name] = re.compile(regex, re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                logger.warning(f"Invalid filter pattern '{pattern}': {e}")
        return compiled
    
    def _compile_threat_patterns(self) -> Dict[str, re.Pattern]:
        """Compile threat detection patterns"""
        patterns = {
            "phishing": r"(bit\.ly|tinyurl|t\.co|goo\.gl)\/[a-zA-Z0-9]+",
            "malware_indicators": r"(download|click|install|update).*(urgent|now|immediately)",
            "social_engineering": r"(win|won|prize|lottery|inheritance|beneficiary)",
            "suspicious_commands": r"(rm\s+-rf|del\s+\/|format\s+c:)",
            "crypto_scam": r"(bitcoin|btc|ethereum|crypto).*(send|transfer|wallet)",
            "credential_theft": r"(password|login|account).*(verify|update|confirm|suspended)"
        }
        
        compiled = {}
        for name, pattern in patterns.items():
            try:
                compiled[name] = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                logger.warning(f"Invalid threat pattern '{name}': {e}")
        
        return compiled
    
    def _get_default_filter_patterns(self) -> List[str]:
        """Get default content filter patterns"""
        return [
            "spam:(?i)(spam|advertisement|promotion).*(?:click|buy|sale|offer)",
            "profanity:(?i)(fuck|shit|damn|hell|ass|bitch)",  # Add more as needed
            "urls:http[s]?://[^\s]+",
            "phone:(?:(?:\+?1\s*(?:[.-]\s*)?)?(?:\(\s*([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9])\s*\)|([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9]))\s*(?:[.-]\s*)?)?([2-9]1[02-9]|[2-9][02-9]1|[2-9][02-9]{2})\s*(?:[.-]\s*)?([0-9]{4})(?:\s*(?:#|x\.?|ext\.?|extension)\s*(\d+))?",
            "email:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        ]
    
    async def get_status(self) -> Dict[str, Any]:
        """Get security gate status and statistics"""
        active_users = len(self.user_profiles)
        blocked_rate = (self.blocked_requests / self.total_requests) if self.total_requests > 0 else 0.0
        
        # Calculate average trust score
        if self.user_profiles:
            avg_trust_score = sum(p.trust_score for p in self.user_profiles.values()) / len(self.user_profiles)
        else:
            avg_trust_score = 0.0
        
        return {
            "total_requests": self.total_requests,
            "blocked_requests": self.blocked_requests,
            "blocked_rate": blocked_rate,
            "threat_detections": self.threat_detections,
            "rate_limit_violations": self.rate_limit_violations,
            "active_users": active_users,
            "avg_trust_score": avg_trust_score,
            "admin_users": len(self.admin_user_ids),
            "blocked_users": len(self.blocked_user_ids),
            "filter_patterns": len(self.content_filter_patterns),
            "threat_patterns": len(self.suspicious_patterns)
        }
    
    async def add_blocked_user(self, user_id: int) -> None:
        """Add user to blocked list"""
        self.blocked_user_ids.add(user_id)
        logger.info(f"Added user {user_id} to blocked list")
    
    async def remove_blocked_user(self, user_id: int) -> None:
        """Remove user from blocked list"""
        self.blocked_user_ids.discard(user_id)
        logger.info(f"Removed user {user_id} from blocked list")
    
    async def reset_user_violations(self, user_id: int) -> None:
        """Reset violation count for a user"""
        if user_id in self.user_profiles:
            profile = self.user_profiles[user_id]
            profile.violation_count = 0
            profile.rate_limit_violations = 0
            profile.content_violations = 0
            profile.trust_score = 0.5  # Reset to neutral
            logger.info(f"Reset violations for user {user_id}")
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the security gate"""
        logger.info("Shutting down security gate...")
        # Clear caches and save any persistent data if needed
        self.user_profiles.clear()
        self.rate_limit_buckets.clear()
        logger.info("Security gate shutdown complete")