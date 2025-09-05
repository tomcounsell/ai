"""
FastAPI Server for AI System
Production-ready server with WebSocket support, REST endpoints, and comprehensive monitoring.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, HTTPException,
    Depends, Security, status, Request, Response
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, validator
import uvicorn

from config import settings
from agents.valor.agent import ValorAgent
from agents.valor.context import ValorContext, MessageEntry
from agents.context_manager import ContextWindowManager
from agents.tool_registry import ToolRegistry
from mcp_servers.orchestrator import MCPOrchestrator
from integrations.telegram.unified_processor import UnifiedProcessor, ProcessingRequest
from utilities.database import DatabaseManager
from utilities.exceptions import AISystemError
from utilities.logging_config import get_logger

logger = get_logger(__name__)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    logger.info("Starting AI System FastAPI Server...")
    
    # Initialize database
    app.state.db = DatabaseManager(settings.DATABASE_PATH)
    await app.state.db.initialize()
    
    # Initialize agent
    app.state.agent = ValorAgent()
    await app.state.agent.initialize()
    
    # Initialize context manager
    app.state.context_manager = ContextWindowManager(
        max_tokens=100000,
        compression_threshold=0.8
    )
    
    # Initialize tool registry
    app.state.tool_registry = ToolRegistry()
    
    # Initialize MCP orchestrator
    app.state.mcp_orchestrator = MCPOrchestrator()
    await app.state.mcp_orchestrator.initialize()
    
    # Initialize unified processor
    app.state.processor = UnifiedProcessor()
    
    # Initialize WebSocket connections tracking
    app.state.websocket_connections = {}
    
    logger.info("AI System server initialized successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down AI System server...")
    
    # Close WebSocket connections
    for ws_id, ws in app.state.websocket_connections.items():
        await ws.close()
    
    # Cleanup resources
    await app.state.db.close()
    await app.state.mcp_orchestrator.shutdown()
    
    logger.info("AI System server shut down complete")


# Create FastAPI app
app = FastAPI(
    title="AI System API",
    description="Unified Conversational Development Environment",
    version="1.0.0",
    lifespan=lifespan
)

# Security
security = HTTPBearer()


# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS if hasattr(settings, 'ALLOWED_ORIGINS') else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS if hasattr(settings, 'ALLOWED_HOSTS') else ["*"]
)


# Request/Response Models
class ChatMessage(BaseModel):
    """Chat message model."""
    content: str = Field(..., description="Message content")
    role: str = Field(default="user", description="Message role")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    @validator('content')
    def validate_content(cls, v):
        if not v or not v.strip():
            raise ValueError("Message content cannot be empty")
        if len(v) > 100000:
            raise ValueError("Message content too long (max 100k chars)")
        return v


class ChatRequest(BaseModel):
    """Chat request model."""
    message: ChatMessage = Field(..., description="Chat message")
    session_id: Optional[str] = Field(None, description="Session identifier")
    context_id: Optional[str] = Field(None, description="Context identifier")
    stream: bool = Field(default=False, description="Enable streaming response")
    tools_enabled: bool = Field(default=True, description="Enable tool usage")
    
    @validator('session_id')
    def validate_session_id(cls, v):
        if v and not v.strip():
            raise ValueError("Session ID cannot be empty")
        return v or str(uuid.uuid4())


class ChatResponse(BaseModel):
    """Chat response model."""
    content: str = Field(..., description="Response content")
    session_id: str = Field(..., description="Session identifier")
    message_id: str = Field(..., description="Message identifier")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Response metadata")
    tools_used: List[str] = Field(default_factory=list, description="Tools used in response")
    processing_time_ms: float = Field(..., description="Processing time in milliseconds")


class HealthStatus(BaseModel):
    """Health check response model."""
    status: str = Field(..., description="Health status")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = Field(..., description="System version")
    services: Dict[str, str] = Field(..., description="Service statuses")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="System metrics")


class WebSocketMessage(BaseModel):
    """WebSocket message model."""
    type: str = Field(..., description="Message type")
    content: Any = Field(..., description="Message content")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Authentication
async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """Verify authentication token."""
    token = credentials.credentials
    
    # In production, validate against real auth system
    if not token or len(token) < 10:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    
    return token


# REST API Endpoints
@app.get("/health", response_model=HealthStatus)
async def health_check():
    """Health check endpoint."""
    try:
        # Check services
        services = {
            "database": "healthy" if app.state.db else "unavailable",
            "agent": "healthy" if app.state.agent else "unavailable",
            "mcp": "healthy" if app.state.mcp_orchestrator else "unavailable",
            "processor": "healthy" if app.state.processor else "unavailable"
        }
        
        # Get metrics
        metrics = {
            "websocket_connections": len(app.state.websocket_connections),
            "uptime_seconds": time.time() - app.state.get("start_time", time.time())
        }
        
        return HealthStatus(
            status="healthy" if all(s == "healthy" for s in services.values()) else "degraded",
            version="1.0.0",
            services=services,
            metrics=metrics
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail="Health check failed")


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    token: str = Depends(verify_token)
):
    """Process chat message."""
    start_time = time.time()
    
    try:
        # Get or create context
        context = await app.state.context_manager.get_or_create_context(
            request.session_id
        )
        
        # Add message to context
        context.add_message(MessageEntry(
            role=request.message.role,
            content=request.message.content,
            timestamp=datetime.now(timezone.utc)
        ))
        
        # Process with agent
        response = await app.state.agent.process(
            message=request.message.content,
            context=context,
            tools_enabled=request.tools_enabled
        )
        
        # Track tools used
        tools_used = []
        if hasattr(response, 'tools_used'):
            tools_used = response.tools_used
        
        # Calculate processing time
        processing_time = (time.time() - start_time) * 1000
        
        return ChatResponse(
            content=response.content if hasattr(response, 'content') else str(response),
            session_id=request.session_id,
            message_id=str(uuid.uuid4()),
            metadata=request.message.metadata,
            tools_used=tools_used,
            processing_time_ms=processing_time
        )
        
    except Exception as e:
        logger.error(f"Chat processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = 100,
    token: str = Depends(verify_token)
):
    """Get session chat history."""
    try:
        history = await app.state.db.get_chat_history(
            session_id=session_id,
            limit=limit
        )
        return {"session_id": session_id, "history": history}
    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tools")
async def list_tools(token: str = Depends(verify_token)):
    """List available tools."""
    try:
        tools = app.state.tool_registry.list_tools()
        return {"tools": tools}
    except Exception as e:
        logger.error(f"Failed to list tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/mcp/servers")
async def list_mcp_servers(token: str = Depends(verify_token)):
    """List MCP servers and their capabilities."""
    try:
        servers = app.state.mcp_orchestrator.get_registered_servers()
        capabilities = {}
        
        for name, server in servers.items():
            capabilities[name] = {
                "status": server.get_status().value,
                "tools": [cap.name for cap in server.get_capabilities()]
            }
        
        return {"servers": capabilities}
    except Exception as e:
        logger.error(f"Failed to list MCP servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# WebSocket Endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication."""
    ws_id = str(uuid.uuid4())
    await websocket.accept()
    
    # Track connection
    app.state.websocket_connections[ws_id] = websocket
    
    logger.info(f"WebSocket connection established: {ws_id}")
    
    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connection",
            "content": {"id": ws_id, "status": "connected"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        # Create session context
        context = await app.state.context_manager.get_or_create_context(ws_id)
        
        while True:
            # Receive message
            data = await websocket.receive_json()
            
            # Validate message
            try:
                ws_msg = WebSocketMessage(**data)
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "content": {"error": f"Invalid message format: {e}"},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                continue
            
            # Process message based on type
            if ws_msg.type == "chat":
                # Process chat message
                try:
                    # Add to context
                    context.add_message(MessageEntry(
                        role="user",
                        content=ws_msg.content,
                        timestamp=datetime.now(timezone.utc)
                    ))
                    
                    # Process with agent
                    response = await app.state.agent.process(
                        message=ws_msg.content,
                        context=context,
                        stream=True  # Enable streaming for WebSocket
                    )
                    
                    # Stream response
                    if hasattr(response, '__aiter__'):
                        # Streaming response
                        async for chunk in response:
                            await websocket.send_json({
                                "type": "stream",
                                "content": chunk,
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                    else:
                        # Non-streaming response
                        await websocket.send_json({
                            "type": "response",
                            "content": str(response),
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                        
                except Exception as e:
                    logger.error(f"WebSocket chat processing failed: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "content": {"error": str(e)},
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            
            elif ws_msg.type == "ping":
                # Respond to ping
                await websocket.send_json({
                    "type": "pong",
                    "content": ws_msg.content,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            
            elif ws_msg.type == "command":
                # Handle commands (tool execution, etc.)
                try:
                    result = await handle_command(ws_msg.content)
                    await websocket.send_json({
                        "type": "command_result",
                        "content": result,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "content": {"error": f"Command failed: {e}"},
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {ws_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Clean up connection
        if ws_id in app.state.websocket_connections:
            del app.state.websocket_connections[ws_id]


async def handle_command(command: Dict[str, Any]) -> Dict[str, Any]:
    """Handle WebSocket commands."""
    cmd_type = command.get("type")
    
    if cmd_type == "execute_tool":
        # Execute a specific tool
        tool_name = command.get("tool")
        params = command.get("params", {})
        
        tool = app.state.tool_registry.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool not found: {tool_name}")
        
        result = await tool.execute(params)
        return {"tool": tool_name, "result": result}
    
    elif cmd_type == "list_tools":
        # List available tools
        tools = app.state.tool_registry.list_tools()
        return {"tools": tools}
    
    else:
        raise ValueError(f"Unknown command type: {cmd_type}")


# Error handlers
@app.exception_handler(AISystemError)
async def ai_system_error_handler(request: Request, exc: AISystemError):
    """Handle AI system errors."""
    return JSONResponse(
        status_code=500,
        content={
            "error": exc.message,
            "error_code": exc.error_code,
            "details": exc.details
        }
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors."""
    return JSONResponse(
        status_code=400,
        content={"error": str(exc)}
    )


# Main entry point
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=settings.SERVER_HOST if hasattr(settings, 'SERVER_HOST') else "0.0.0.0",
        port=settings.SERVER_PORT if hasattr(settings, 'SERVER_PORT') else 8000,
        reload=settings.DEBUG if hasattr(settings, 'DEBUG') else False,
        log_level="debug" if (settings.DEBUG if hasattr(settings, 'DEBUG') else False) else "info"
    )