"""Unified Message Processing Pipeline

This module implements a comprehensive 5-step pipeline for processing Telegram messages:
1. Security Gate - Authentication, rate limiting, threat detection  
2. Context Builder - Message history, user profile, workspace loading
3. Type Router - Message type detection, multi-modal routing
4. Agent Orchestrator - Agent selection, tool coordination
5. Response Manager - Formatting, splitting, media handling
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field
from telethon.tl.types import Message, User, Chat, Channel

from .components.security_gate import SecurityGate, SecurityResult
from .components.context_builder import ContextBuilder, MessageContext
from .components.type_router import TypeRouter, MessageType, RouteResult
from .components.agent_orchestrator import AgentOrchestrator, AgentResult
from .components.response_manager import ResponseManager, FormattedResponse


logger = logging.getLogger(__name__)


class ProcessingStage(Enum):
    """Pipeline processing stages"""
    SECURITY = "security"
    CONTEXT = "context"
    ROUTING = "routing"
    ORCHESTRATION = "orchestration" 
    RESPONSE = "response"


@dataclass
class PipelineMetrics:
    """Performance metrics for pipeline execution"""
    total_duration_ms: float = 0.0
    security_duration_ms: float = 0.0
    context_duration_ms: float = 0.0
    routing_duration_ms: float = 0.0
    orchestration_duration_ms: float = 0.0
    response_duration_ms: float = 0.0
    stage_count: int = 0
    memory_peak_mb: float = 0.0
    errors: List[str] = field(default_factory=list)


class ProcessingResult(BaseModel):
    """Result of the unified processing pipeline"""
    
    success: bool = Field(..., description="Whether processing succeeded")
    stage_reached: ProcessingStage = Field(..., description="Last stage reached")
    responses: List[FormattedResponse] = Field(default_factory=list)
    metrics: PipelineMetrics = Field(default_factory=PipelineMetrics)
    error: Optional[str] = Field(None, description="Error message if failed")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProcessingRequest(BaseModel):
    """Request for unified message processing"""
    
    message: Message = Field(..., description="Telegram message object")
    user: Union[User, Chat, Channel] = Field(..., description="Message sender")
    chat_id: int = Field(..., description="Chat identifier")
    message_id: int = Field(..., description="Message identifier") 
    raw_text: Optional[str] = Field(None, description="Raw message text")
    media_info: Optional[Dict[str, Any]] = Field(None, description="Media information")
    forwarded_info: Optional[Dict[str, Any]] = Field(None, description="Forward information")
    reply_info: Optional[Dict[str, Any]] = Field(None, description="Reply information")


class UnifiedProcessor:
    """
    Unified message processing pipeline that orchestrates the 5-step processing flow
    with comprehensive error handling, performance monitoring, and quality assurance.
    """
    
    def __init__(
        self,
        security_gate: Optional[SecurityGate] = None,
        context_builder: Optional[ContextBuilder] = None,
        type_router: Optional[TypeRouter] = None,
        agent_orchestrator: Optional[AgentOrchestrator] = None,
        response_manager: Optional[ResponseManager] = None,
        performance_target_ms: int = 2000,
        enable_metrics: bool = True,
        enable_parallel_processing: bool = True,
        max_concurrent_requests: int = 10
    ):
        """
        Initialize the unified processor with all pipeline components.
        
        Args:
            security_gate: Security validation component
            context_builder: Context building component
            type_router: Message type routing component
            agent_orchestrator: Agent selection and coordination component
            response_manager: Response formatting component
            performance_target_ms: Target response time in milliseconds
            enable_metrics: Whether to collect performance metrics
            enable_parallel_processing: Enable parallel processing where possible
            max_concurrent_requests: Maximum concurrent processing requests
        """
        self.security_gate = security_gate or SecurityGate()
        self.context_builder = context_builder or ContextBuilder()
        self.type_router = type_router or TypeRouter()
        self.agent_orchestrator = agent_orchestrator or AgentOrchestrator()
        self.response_manager = response_manager or ResponseManager()
        
        self.performance_target_ms = performance_target_ms
        self.enable_metrics = enable_metrics
        self.enable_parallel_processing = enable_parallel_processing
        
        # Concurrency control
        self.processing_semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.active_requests: Dict[str, ProcessingRequest] = {}
        
        # Performance monitoring
        self.processing_history: List[PipelineMetrics] = []
        self.success_count = 0
        self.failure_count = 0
        
        logger.info(
            f"UnifiedProcessor initialized with {max_concurrent_requests} "
            f"max concurrent requests, {performance_target_ms}ms target"
        )
    
    async def process_message(
        self, 
        request: ProcessingRequest,
        request_id: Optional[str] = None
    ) -> ProcessingResult:
        """
        Process a message through the complete 5-step pipeline.
        
        Args:
            request: Processing request containing message and metadata
            request_id: Optional request identifier for tracking
            
        Returns:
            ProcessingResult with responses and metrics
        """
        if request_id is None:
            request_id = f"req_{int(time.time() * 1000)}_{request.chat_id}"
        
        start_time = time.perf_counter()
        metrics = PipelineMetrics()
        
        async with self.processing_semaphore:
            try:
                self.active_requests[request_id] = request
                
                logger.info(
                    f"Processing message {request.message_id} from "
                    f"chat {request.chat_id} (request_id: {request_id})"
                )
                
                # Step 1: Security Gate
                security_start = time.perf_counter()
                security_result = await self._execute_security_gate(request)
                metrics.security_duration_ms = (time.perf_counter() - security_start) * 1000
                
                if not security_result.allowed:
                    return ProcessingResult(
                        success=False,
                        stage_reached=ProcessingStage.SECURITY,
                        metrics=metrics,
                        error=f"Security gate blocked: {security_result.reason}"
                    )
                
                # Step 2: Context Builder
                context_start = time.perf_counter()
                context = await self._execute_context_builder(request, security_result)
                metrics.context_duration_ms = (time.perf_counter() - context_start) * 1000
                
                # Step 3: Type Router
                routing_start = time.perf_counter()
                route_result = await self._execute_type_router(request, context)
                metrics.routing_duration_ms = (time.perf_counter() - routing_start) * 1000
                
                # Step 4: Agent Orchestrator
                orchestration_start = time.perf_counter()
                agent_result = await self._execute_agent_orchestrator(
                    request, context, route_result
                )
                metrics.orchestration_duration_ms = (time.perf_counter() - orchestration_start) * 1000
                
                # Step 5: Response Manager
                response_start = time.perf_counter()
                responses = await self._execute_response_manager(
                    request, context, agent_result
                )
                metrics.response_duration_ms = (time.perf_counter() - response_start) * 1000
                
                # Calculate final metrics
                total_time = time.perf_counter() - start_time
                metrics.total_duration_ms = total_time * 1000
                metrics.stage_count = 5
                
                if self.enable_metrics:
                    self.processing_history.append(metrics)
                    self._update_success_metrics(metrics)
                
                logger.info(
                    f"Message processed successfully in {metrics.total_duration_ms:.1f}ms "
                    f"(request_id: {request_id})"
                )
                
                return ProcessingResult(
                    success=True,
                    stage_reached=ProcessingStage.RESPONSE,
                    responses=responses,
                    metrics=metrics,
                    metadata={
                        "request_id": request_id,
                        "message_type": route_result.message_type.value,
                        "agent_used": agent_result.agent_name,
                        "tools_used": agent_result.tools_used
                    }
                )
                
            except Exception as e:
                logger.error(
                    f"Pipeline error for request {request_id}: {str(e)}", 
                    exc_info=True
                )
                
                metrics.total_duration_ms = (time.perf_counter() - start_time) * 1000
                metrics.errors.append(str(e))
                
                if self.enable_metrics:
                    self.failure_count += 1
                
                return ProcessingResult(
                    success=False,
                    stage_reached=ProcessingStage.SECURITY,
                    metrics=metrics,
                    error=str(e)
                )
            
            finally:
                self.active_requests.pop(request_id, None)
    
    async def _execute_security_gate(
        self, 
        request: ProcessingRequest
    ) -> SecurityResult:
        """Execute the security gate component"""
        try:
            return await self.security_gate.validate_request(
                user_id=request.user.id if hasattr(request.user, 'id') else None,
                chat_id=request.chat_id,
                message_text=request.raw_text,
                media_info=request.media_info
            )
        except Exception as e:
            logger.error(f"Security gate error: {str(e)}")
            return SecurityResult(
                allowed=False,
                reason=f"Security gate error: {str(e)}",
                risk_score=1.0
            )
    
    async def _execute_context_builder(
        self,
        request: ProcessingRequest,
        security_result: SecurityResult
    ) -> MessageContext:
        """Execute the context builder component"""
        return await self.context_builder.build_context(
            chat_id=request.chat_id,
            user_id=request.user.id if hasattr(request.user, 'id') else None,
            message=request.message,
            security_context=security_result
        )
    
    async def _execute_type_router(
        self,
        request: ProcessingRequest,
        context: MessageContext
    ) -> RouteResult:
        """Execute the type router component"""
        return await self.type_router.route_message(
            message=request.message,
            context=context,
            media_info=request.media_info
        )
    
    async def _execute_agent_orchestrator(
        self,
        request: ProcessingRequest,
        context: MessageContext,
        route_result: RouteResult
    ) -> AgentResult:
        """Execute the agent orchestrator component"""
        return await self.agent_orchestrator.orchestrate(
            message=request.message,
            context=context,
            message_type=route_result.message_type,
            route_metadata=route_result.metadata
        )
    
    async def _execute_response_manager(
        self,
        request: ProcessingRequest,
        context: MessageContext,
        agent_result: AgentResult
    ) -> List[FormattedResponse]:
        """Execute the response manager component"""
        return await self.response_manager.format_response(
            agent_result=agent_result,
            context=context,
            target_chat_id=request.chat_id,
            reply_to_message_id=request.message_id
        )
    
    def _update_success_metrics(self, metrics: PipelineMetrics) -> None:
        """Update success rate and performance metrics"""
        self.success_count += 1
        
        # Keep only recent history
        if len(self.processing_history) > 1000:
            self.processing_history = self.processing_history[-500:]
    
    async def get_pipeline_status(self) -> Dict[str, Any]:
        """Get current pipeline status and metrics"""
        total_requests = self.success_count + self.failure_count
        success_rate = (self.success_count / total_requests) if total_requests > 0 else 0.0
        
        recent_metrics = self.processing_history[-100:] if self.processing_history else []
        avg_duration = (
            sum(m.total_duration_ms for m in recent_metrics) / len(recent_metrics)
            if recent_metrics else 0.0
        )
        
        return {
            "active_requests": len(self.active_requests),
            "total_processed": total_requests,
            "success_rate": success_rate,
            "failure_count": self.failure_count,
            "avg_duration_ms": avg_duration,
            "target_duration_ms": self.performance_target_ms,
            "performance_ratio": avg_duration / self.performance_target_ms if avg_duration > 0 else 0.0,
            "component_status": {
                "security_gate": await self.security_gate.get_status(),
                "context_builder": await self.context_builder.get_status(),
                "type_router": await self.type_router.get_status(),
                "agent_orchestrator": await self.agent_orchestrator.get_status(),
                "response_manager": await self.response_manager.get_status()
            }
        }
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the processor"""
        logger.info("Shutting down unified processor...")
        
        # Wait for active requests to complete
        while self.active_requests:
            logger.info(f"Waiting for {len(self.active_requests)} active requests...")
            await asyncio.sleep(0.1)
        
        # Shutdown components
        await asyncio.gather(
            self.security_gate.shutdown(),
            self.context_builder.shutdown(),
            self.type_router.shutdown(),
            self.agent_orchestrator.shutdown(),
            self.response_manager.shutdown(),
            return_exceptions=True
        )
        
        logger.info("Unified processor shutdown complete")