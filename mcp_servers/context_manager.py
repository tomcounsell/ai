"""
MCP Context Manager

This module provides comprehensive context injection and management for MCP servers:
- Workspace context injection with security validation
- User context injection with permission management
- Session context injection with state management
- Context serialization and deserialization
- Security context validation and sanitization
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union, Set
from enum import Enum, auto

from pydantic import BaseModel, Field, validator

from .base import ContextInjector, MCPError, MCPRequest
from agents.valor.context import ValorContext, WorkspaceInfo, UserPreferences
from utilities.exceptions import AISystemError as BaseError


class SecurityLevel(Enum):
    """Security clearance levels for context access."""
    
    PUBLIC = "public"           # No restrictions
    INTERNAL = "internal"       # Internal use only
    CONFIDENTIAL = "confidential"  # Confidential data
    RESTRICTED = "restricted"   # Restricted access
    SECRET = "secret"          # Secret clearance required


class ContextScope(Enum):
    """Scope definitions for context access."""
    
    GLOBAL = auto()      # Global system context
    WORKSPACE = auto()   # Workspace-specific context
    SESSION = auto()     # Session-specific context
    REQUEST = auto()     # Request-specific context


class WorkspaceContext(BaseModel):
    """Workspace context information for MCP servers."""
    
    workspace_id: str = Field(..., description="Unique workspace identifier")
    name: str = Field(..., description="Workspace name")
    type: str = Field(default="project", description="Workspace type")
    path: Optional[str] = Field(None, description="Workspace filesystem path")
    
    # Configuration and metadata
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Workspace configuration")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Workspace metadata")
    
    # Security and permissions
    security_level: SecurityLevel = Field(default=SecurityLevel.INTERNAL, description="Security level")
    allowed_tools: Set[str] = Field(default_factory=set, description="Allowed tools in this workspace")
    restricted_paths: Set[str] = Field(default_factory=set, description="Restricted filesystem paths")
    
    # State information
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    creation_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True, description="Whether workspace is currently active")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            set: lambda v: list(v)
        }


class UserContext(BaseModel):
    """User context information for MCP servers."""
    
    user_id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., description="Username")
    display_name: Optional[str] = Field(None, description="User display name")
    
    # Preferences and settings
    preferences: UserPreferences = Field(default_factory=UserPreferences, description="User preferences")
    settings: Dict[str, Any] = Field(default_factory=dict, description="User-specific settings")
    
    # Security and permissions
    security_clearance: SecurityLevel = Field(default=SecurityLevel.INTERNAL, description="User security clearance")
    permissions: Set[str] = Field(default_factory=set, description="User permissions")
    roles: Set[str] = Field(default_factory=set, description="User roles")
    
    # Session information
    active_sessions: Set[str] = Field(default_factory=set, description="Active session IDs")
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Usage tracking
    total_requests: int = Field(default=0, description="Total requests made by user")
    successful_requests: int = Field(default=0, description="Successful requests")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            set: lambda v: list(v)
        }


class SessionContext(BaseModel):
    """Session context information for MCP servers."""
    
    session_id: str = Field(..., description="Unique session identifier")
    user_id: str = Field(..., description="Associated user ID")
    workspace_id: Optional[str] = Field(None, description="Associated workspace ID")
    
    # Session state
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(None, description="Session expiration time")
    is_active: bool = Field(default=True, description="Whether session is active")
    
    # Context state
    conversation_context: Optional[ValorContext] = Field(None, description="Valor conversation context")
    session_metadata: Dict[str, Any] = Field(default_factory=dict, description="Session metadata")
    temporary_data: Dict[str, Any] = Field(default_factory=dict, description="Temporary session data")
    
    # Security
    security_token: Optional[str] = Field(None, description="Security token for session")
    ip_address: Optional[str] = Field(None, description="Client IP address")
    user_agent: Optional[str] = Field(None, description="Client user agent")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SecurityContext(BaseModel):
    """Security context for request processing."""
    
    security_level: SecurityLevel = Field(default=SecurityLevel.INTERNAL, description="Required security level")
    permissions: Set[str] = Field(default_factory=set, description="Required permissions")
    access_scopes: Set[ContextScope] = Field(default_factory=set, description="Access scopes")
    
    # Authentication information
    authenticated: bool = Field(default=False, description="Whether request is authenticated")
    authentication_method: Optional[str] = Field(None, description="Authentication method used")
    
    # Authorization tracking
    authorization_checks: List[str] = Field(default_factory=list, description="Authorization checks performed")
    denied_permissions: List[str] = Field(default_factory=list, description="Permissions that were denied")
    
    class Config:
        json_encoders = {
            set: lambda v: list(v)
        }


class EnrichedContext(BaseModel):
    """Complete enriched context for MCP request processing."""
    
    # Core identification
    request_id: str = Field(..., description="Request identifier")
    timestamp: datetime = Field(..., description="Request timestamp")
    
    # Context components
    workspace: Optional[WorkspaceContext] = Field(None, description="Workspace context")
    user: Optional[UserContext] = Field(None, description="User context") 
    session: Optional[SessionContext] = Field(None, description="Session context")
    security: SecurityContext = Field(default_factory=SecurityContext, description="Security context")
    
    # Server information
    server_info: Dict[str, Any] = Field(default_factory=dict, description="Server information")
    
    # Request-specific data
    request_metadata: Dict[str, Any] = Field(default_factory=dict, description="Request metadata")
    injected_data: Dict[str, Any] = Field(default_factory=dict, description="Data injected by context manager")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ContextValidationError(MCPError):
    """Error raised when context validation fails."""
    
    def __init__(self, message: str, validation_errors: List[str] = None):
        super().__init__(
            message,
            error_code="CONTEXT_VALIDATION_ERROR",
            details={"validation_errors": validation_errors or []},
            recoverable=False
        )


class MCPContextManager(ContextInjector):
    """
    Advanced MCP context manager with comprehensive context injection and security validation.
    
    Provides:
    - Multi-level context injection (workspace, user, session)
    - Security validation and permission checking
    - Context serialization and caching
    - Session management and expiration
    """
    
    def __init__(
        self,
        default_session_timeout: timedelta = timedelta(hours=24),
        enable_context_caching: bool = True,
        logger: Optional[logging.Logger] = None
    ):
        self.default_session_timeout = default_session_timeout
        self.enable_context_caching = enable_context_caching
        self.logger = logger or logging.getLogger("mcp.context_manager")
        
        # Context stores
        self._workspace_store: Dict[str, WorkspaceContext] = {}
        self._user_store: Dict[str, UserContext] = {}
        self._session_store: Dict[str, SessionContext] = {}
        
        # Context cache
        self._context_cache: Dict[str, EnrichedContext] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        self._cache_ttl = timedelta(minutes=5)
        
        # Security configuration
        self._security_policies: Dict[str, Dict[str, Any]] = {}
        
        self.logger.info("MCP Context Manager initialized")
    
    async def inject_context(
        self,
        request: MCPRequest,
        server_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Inject comprehensive context into MCP request processing.
        
        Args:
            request: The MCP request being processed
            server_context: Server-level context information
            
        Returns:
            Dict[str, Any]: Enriched context for request processing
        """
        try:
            # Check cache first
            cache_key = f"{request.id}_{hash(json.dumps(request.dict(), sort_keys=True))}"
            if self.enable_context_caching and cache_key in self._context_cache:
                cached_time = self._cache_timestamps.get(cache_key)
                if cached_time and (datetime.now(timezone.utc) - cached_time) < self._cache_ttl:
                    self.logger.debug(f"Using cached context for request {request.id}")
                    return self._context_cache[cache_key].dict()
            
            # Create base enriched context
            enriched_context = EnrichedContext(
                request_id=request.id,
                timestamp=request.timestamp,
                server_info=server_context.get("server_info", {})
            )
            
            # Extract context identifiers from request
            context_params = request.context or {}
            workspace_id = context_params.get("workspace_id")
            user_id = context_params.get("user_id") 
            session_id = context_params.get("session_id")
            
            # Inject workspace context
            if workspace_id:
                workspace_context = await self._get_workspace_context(workspace_id)
                if workspace_context:
                    enriched_context.workspace = workspace_context
                    enriched_context.injected_data["workspace_injected"] = True
            
            # Inject user context
            if user_id:
                user_context = await self._get_user_context(user_id)
                if user_context:
                    enriched_context.user = user_context
                    enriched_context.injected_data["user_injected"] = True
            
            # Inject session context
            if session_id:
                session_context = await self._get_session_context(session_id)
                if session_context and self._is_session_valid(session_context):
                    enriched_context.session = session_context
                    enriched_context.injected_data["session_injected"] = True
                    
                    # Update session activity
                    session_context.last_activity = datetime.now(timezone.utc)
                    await self._update_session_context(session_context)
            
            # Build security context
            security_context = await self._build_security_context(
                enriched_context, request, server_context
            )
            enriched_context.security = security_context
            
            # Add request metadata
            enriched_context.request_metadata = {
                "method": request.method,
                "params_keys": list(request.params.keys()) if request.params else [],
                "has_context": bool(request.context),
                "injection_timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Cache the enriched context
            if self.enable_context_caching:
                self._context_cache[cache_key] = enriched_context
                self._cache_timestamps[cache_key] = datetime.now(timezone.utc)
                
                # Clean old cache entries
                await self._cleanup_cache()
            
            self.logger.debug(
                f"Context injected for request {request.id}: "
                f"workspace={workspace_id}, user={user_id}, session={session_id}"
            )
            
            return enriched_context.dict()
            
        except Exception as e:
            self.logger.error(f"Context injection failed for request {request.id}: {str(e)}")
            raise MCPError(
                f"Context injection failed: {str(e)}",
                error_code="CONTEXT_INJECTION_ERROR",
                details={"request_id": request.id, "error": str(e)},
                request_id=request.id
            )
    
    async def validate_context(self, context: Dict[str, Any]) -> bool:
        """
        Validate context security and integrity.
        
        Args:
            context: Context to validate
            
        Returns:
            bool: True if context is valid and secure
        """
        try:
            validation_errors = []
            
            # Parse enriched context
            try:
                enriched_context = EnrichedContext(**context)
            except Exception as e:
                validation_errors.append(f"Context parsing failed: {str(e)}")
                raise ContextValidationError(
                    "Context validation failed", validation_errors
                )
            
            # Validate security context
            security_validation = await self._validate_security_context(enriched_context.security)
            if not security_validation["valid"]:
                validation_errors.extend(security_validation["errors"])
            
            # Validate workspace permissions
            if enriched_context.workspace:
                workspace_validation = await self._validate_workspace_permissions(
                    enriched_context.workspace, enriched_context.user
                )
                if not workspace_validation["valid"]:
                    validation_errors.extend(workspace_validation["errors"])
            
            # Validate session integrity
            if enriched_context.session:
                session_validation = await self._validate_session_integrity(enriched_context.session)
                if not session_validation["valid"]:
                    validation_errors.extend(session_validation["errors"])
            
            # Validate user permissions
            if enriched_context.user:
                user_validation = await self._validate_user_permissions(
                    enriched_context.user, enriched_context.security
                )
                if not user_validation["valid"]:
                    validation_errors.extend(user_validation["errors"])
            
            # Check if any validation errors occurred
            if validation_errors:
                self.logger.warning(
                    f"Context validation failed for request {enriched_context.request_id}: "
                    f"{', '.join(validation_errors)}"
                )
                return False
            
            return True
            
        except ContextValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Context validation error: {str(e)}")
            raise ContextValidationError(f"Validation error: {str(e)}")
    
    async def register_workspace(
        self,
        workspace_id: str,
        name: str,
        workspace_type: str = "project",
        configuration: Dict[str, Any] = None,
        security_level: SecurityLevel = SecurityLevel.INTERNAL
    ) -> WorkspaceContext:
        """Register a new workspace context."""
        workspace = WorkspaceContext(
            workspace_id=workspace_id,
            name=name,
            type=workspace_type,
            configuration=configuration or {},
            security_level=security_level
        )
        
        self._workspace_store[workspace_id] = workspace
        self.logger.info(f"Registered workspace context: {workspace_id}")
        return workspace
    
    async def register_user(
        self,
        user_id: str,
        username: str,
        display_name: str = None,
        security_clearance: SecurityLevel = SecurityLevel.INTERNAL,
        permissions: Set[str] = None
    ) -> UserContext:
        """Register a new user context."""
        user = UserContext(
            user_id=user_id,
            username=username,
            display_name=display_name,
            security_clearance=security_clearance,
            permissions=permissions or set()
        )
        
        self._user_store[user_id] = user
        self.logger.info(f"Registered user context: {user_id}")
        return user
    
    async def create_session(
        self,
        user_id: str,
        workspace_id: str = None,
        session_timeout: timedelta = None,
        conversation_context: ValorContext = None
    ) -> SessionContext:
        """Create a new session context."""
        session_id = str(uuid.uuid4())
        timeout = session_timeout or self.default_session_timeout
        expires_at = datetime.now(timezone.utc) + timeout
        
        session = SessionContext(
            session_id=session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            expires_at=expires_at,
            conversation_context=conversation_context
        )
        
        self._session_store[session_id] = session
        
        # Update user's active sessions
        if user_id in self._user_store:
            self._user_store[user_id].active_sessions.add(session_id)
        
        self.logger.info(f"Created session context: {session_id} for user: {user_id}")
        return session
    
    async def serialize_context(self, context: EnrichedContext) -> str:
        """Serialize context to JSON string."""
        try:
            return json.dumps(context.dict(), indent=2, default=str)
        except Exception as e:
            raise MCPError(
                f"Context serialization failed: {str(e)}",
                error_code="CONTEXT_SERIALIZATION_ERROR",
                details={"error": str(e)}
            )
    
    async def deserialize_context(self, context_json: str) -> EnrichedContext:
        """Deserialize context from JSON string."""
        try:
            context_data = json.loads(context_json)
            return EnrichedContext(**context_data)
        except Exception as e:
            raise MCPError(
                f"Context deserialization failed: {str(e)}",
                error_code="CONTEXT_DESERIALIZATION_ERROR", 
                details={"error": str(e)}
            )
    
    # Private helper methods
    
    async def _get_workspace_context(self, workspace_id: str) -> Optional[WorkspaceContext]:
        """Get workspace context by ID."""
        return self._workspace_store.get(workspace_id)
    
    async def _get_user_context(self, user_id: str) -> Optional[UserContext]:
        """Get user context by ID."""
        return self._user_store.get(user_id)
    
    async def _get_session_context(self, session_id: str) -> Optional[SessionContext]:
        """Get session context by ID."""
        return self._session_store.get(session_id)
    
    async def _update_session_context(self, session: SessionContext) -> None:
        """Update session context in store."""
        self._session_store[session.session_id] = session
    
    def _is_session_valid(self, session: SessionContext) -> bool:
        """Check if session is still valid."""
        if not session.is_active:
            return False
        
        if session.expires_at and datetime.now(timezone.utc) > session.expires_at:
            session.is_active = False
            return False
        
        return True
    
    async def _build_security_context(
        self,
        enriched_context: EnrichedContext,
        request: MCPRequest,
        server_context: Dict[str, Any]
    ) -> SecurityContext:
        """Build security context based on available information."""
        security_context = SecurityContext()
        
        # Determine security level from workspace or user
        if enriched_context.workspace:
            security_context.security_level = enriched_context.workspace.security_level
        elif enriched_context.user:
            security_context.security_level = enriched_context.user.security_clearance
        
        # Gather permissions from user
        if enriched_context.user:
            security_context.permissions = enriched_context.user.permissions.copy()
        
        # Add workspace-specific scopes
        security_context.access_scopes.add(ContextScope.REQUEST)
        if enriched_context.workspace:
            security_context.access_scopes.add(ContextScope.WORKSPACE)
        if enriched_context.session:
            security_context.access_scopes.add(ContextScope.SESSION)
        
        # Mark as authenticated if user context is present
        security_context.authenticated = enriched_context.user is not None
        if security_context.authenticated:
            security_context.authentication_method = "context_injection"
        
        return security_context
    
    async def _validate_security_context(self, security: SecurityContext) -> Dict[str, Any]:
        """Validate security context."""
        errors = []
        
        # Check authentication for restricted operations
        if security.security_level in [SecurityLevel.RESTRICTED, SecurityLevel.SECRET]:
            if not security.authenticated:
                errors.append("Authentication required for restricted operations")
        
        return {"valid": len(errors) == 0, "errors": errors}
    
    async def _validate_workspace_permissions(
        self, workspace: WorkspaceContext, user: Optional[UserContext]
    ) -> Dict[str, Any]:
        """Validate workspace access permissions."""
        errors = []
        
        if not workspace.is_active:
            errors.append("Workspace is not active")
        
        # Check security level compatibility
        if user and user.security_clearance.value < workspace.security_level.value:
            errors.append(
                f"User security clearance '{user.security_clearance.value}' "
                f"insufficient for workspace level '{workspace.security_level.value}'"
            )
        
        return {"valid": len(errors) == 0, "errors": errors}
    
    async def _validate_session_integrity(self, session: SessionContext) -> Dict[str, Any]:
        """Validate session integrity."""
        errors = []
        
        if not self._is_session_valid(session):
            errors.append("Session is expired or inactive")
        
        # Check if session user exists
        if session.user_id not in self._user_store:
            errors.append("Session user context not found")
        
        return {"valid": len(errors) == 0, "errors": errors}
    
    async def _validate_user_permissions(
        self, user: UserContext, security: SecurityContext
    ) -> Dict[str, Any]:
        """Validate user permissions against security requirements."""
        errors = []
        
        # Check if user has required permissions
        missing_permissions = security.permissions - user.permissions
        if missing_permissions:
            errors.append(f"Missing required permissions: {', '.join(missing_permissions)}")
        
        return {"valid": len(errors) == 0, "errors": errors}
    
    async def _cleanup_cache(self) -> None:
        """Clean up expired cache entries."""
        current_time = datetime.now(timezone.utc)
        expired_keys = []
        
        for cache_key, timestamp in self._cache_timestamps.items():
            if (current_time - timestamp) > self._cache_ttl:
                expired_keys.append(cache_key)
        
        for key in expired_keys:
            self._context_cache.pop(key, None)
            self._cache_timestamps.pop(key, None)
        
        if expired_keys:
            self.logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")


# Export key components
__all__ = [
    "MCPContextManager",
    "WorkspaceContext", 
    "UserContext",
    "SessionContext",
    "SecurityContext",
    "EnrichedContext",
    "SecurityLevel",
    "ContextScope",
    "ContextValidationError"
]