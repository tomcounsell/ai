"""Agent Orchestrator Component

This module orchestrates agent selection, tool coordination, and execution
for complex multi-step tasks with intelligent workload distribution and
performance optimization.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

from pydantic import BaseModel, Field
from telethon.tl.types import Message

from .context_builder import MessageContext
from .type_router import MessageType, ProcessingPriority
from ...agents.valor.agent import ValorAgent
from ...tools.base import BaseTool
from ...agents.tool_registry import ToolRegistry


logger = logging.getLogger(__name__)


class OrchestrationStrategy(Enum):
    """Agent orchestration strategies"""
    SINGLE_AGENT = "single_agent"           # Use one primary agent
    MULTI_AGENT_PARALLEL = "multi_agent_parallel"   # Multiple agents in parallel
    MULTI_AGENT_SEQUENTIAL = "multi_agent_sequential" # Multiple agents in sequence
    AGENT_PIPELINE = "agent_pipeline"       # Structured pipeline of agents
    ADAPTIVE_ROUTING = "adaptive_routing"   # Dynamic routing based on context


class ExecutionMode(Enum):
    """Execution modes for agent tasks"""
    SYNCHRONOUS = "synchronous"     # Wait for completion
    ASYNCHRONOUS = "asynchronous"   # Fire and forget
    STREAMING = "streaming"         # Stream results as available
    BATCH = "batch"                # Process in batches


class AgentStatus(Enum):
    """Agent status tracking"""
    IDLE = "idle"
    BUSY = "busy"
    OVERLOADED = "overloaded"
    ERROR = "error"
    MAINTENANCE = "maintenance"


@dataclass
class AgentInstance:
    """Agent instance with performance tracking"""
    agent_id: str
    agent: ValorAgent
    agent_type: str
    status: AgentStatus = AgentStatus.IDLE
    current_tasks: int = 0
    max_concurrent_tasks: int = 3
    total_tasks_completed: int = 0
    total_processing_time: float = 0.0
    error_count: int = 0
    last_used: float = field(default_factory=time.time)
    performance_score: float = 1.0
    specializations: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolAssignment:
    """Tool assignment for agent execution"""
    tool_name: str
    tool_instance: BaseTool
    priority: int = 1
    required: bool = True
    estimated_time: float = 1.0
    dependencies: List[str] = field(default_factory=list)
    configuration: Dict[str, Any] = field(default_factory=dict)


class AgentResult(BaseModel):
    """Result from agent orchestration and execution"""
    
    success: bool = Field(..., description="Whether orchestration succeeded")
    agent_name: str = Field(..., description="Primary agent used")
    agent_instances: List[str] = Field(default_factory=list, description="All agents involved")
    
    # Execution results
    primary_response: str = Field(..., description="Main response content")
    supplementary_responses: Dict[str, str] = Field(default_factory=dict)
    tool_outputs: Dict[str, Any] = Field(default_factory=dict)
    
    # Tool usage
    tools_used: List[str] = Field(default_factory=list)
    tool_execution_times: Dict[str, float] = Field(default_factory=dict)
    tool_success_rates: Dict[str, bool] = Field(default_factory=dict)
    
    # Performance metrics
    total_execution_time: float = Field(default=0.0)
    agent_execution_times: Dict[str, float] = Field(default_factory=dict)
    orchestration_overhead: float = Field(default=0.0)
    
    # Quality metrics
    response_quality_score: float = Field(default=0.8, ge=0.0, le=1.0)
    coherence_score: float = Field(default=0.8, ge=0.0, le=1.0)
    completeness_score: float = Field(default=0.8, ge=0.0, le=1.0)
    
    # Error handling
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    fallback_used: bool = Field(default=False)
    
    # Metadata
    orchestration_strategy: OrchestrationStrategy = Field(default=OrchestrationStrategy.SINGLE_AGENT)
    execution_mode: ExecutionMode = Field(default=ExecutionMode.SYNCHRONOUS)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentOrchestrator:
    """
    Advanced agent orchestrator that manages multiple AI agents,
    coordinates tool usage, and optimizes task distribution for
    complex multi-modal processing.
    """
    
    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        max_concurrent_orchestrations: int = 5,
        agent_timeout_seconds: int = 30,
        enable_performance_optimization: bool = True,
        enable_adaptive_routing: bool = True,
        quality_threshold: float = 0.7,
        default_model: str = "openai:gpt-4"
    ):
        """
        Initialize the agent orchestrator.
        
        Args:
            tool_registry: Registry of available tools
            max_concurrent_orchestrations: Maximum concurrent orchestrations
            agent_timeout_seconds: Timeout for agent operations
            enable_performance_optimization: Enable performance-based routing
            enable_adaptive_routing: Enable adaptive routing strategies
            quality_threshold: Minimum quality threshold for responses
            default_model: Default model for new agents
        """
        self.tool_registry = tool_registry or ToolRegistry()
        self.max_concurrent_orchestrations = max_concurrent_orchestrations
        self.agent_timeout_seconds = agent_timeout_seconds
        self.enable_performance_optimization = enable_performance_optimization
        self.enable_adaptive_routing = enable_adaptive_routing
        self.quality_threshold = quality_threshold
        self.default_model = default_model
        
        # Agent management
        self.agent_instances: Dict[str, AgentInstance] = {}
        self.agent_specializations: Dict[str, List[str]] = defaultdict(list)
        self.active_orchestrations: Dict[str, Dict[str, Any]] = {}
        
        # Performance tracking
        self.orchestration_history: List[Dict[str, Any]] = []
        self.performance_metrics: Dict[str, List[float]] = defaultdict(list)
        self.tool_usage_stats: Dict[str, Dict[str, Any]] = defaultdict(dict)
        
        # Concurrency control
        self.orchestration_semaphore = asyncio.Semaphore(max_concurrent_orchestrations)
        self.orchestration_count = 0
        
        # Load balancing
        self.agent_load: Dict[str, int] = defaultdict(int)
        self.routing_cache: Dict[str, str] = {}  # message_hash -> agent_id
        
        # Initialize default agents
        asyncio.create_task(self._initialize_default_agents())
        
        logger.info(
            f"AgentOrchestrator initialized with {max_concurrent_orchestrations} "
            f"max concurrent orchestrations, performance_optimization={enable_performance_optimization}"
        )
    
    async def orchestrate(
        self,
        message: Message,
        context: MessageContext,
        message_type: MessageType,
        route_metadata: Optional[Dict[str, Any]] = None
    ) -> AgentResult:
        """
        Orchestrate agent selection and execution for a message.
        
        Args:
            message: Telegram message object
            context: Message context
            message_type: Detected message type
            route_metadata: Additional routing metadata
            
        Returns:
            AgentResult with orchestration results
        """
        orchestration_id = f"orch_{int(time.time() * 1000)}_{context.chat_id}"
        start_time = time.perf_counter()
        
        async with self.orchestration_semaphore:
            try:
                self.orchestration_count += 1
                
                # Initialize orchestration tracking
                self.active_orchestrations[orchestration_id] = {
                    "start_time": start_time,
                    "context": context,
                    "message_type": message_type,
                    "agents_used": [],
                    "tools_used": []
                }
                
                logger.info(
                    f"Starting orchestration {orchestration_id} for "
                    f"message_type={message_type.value}, chat_id={context.chat_id}"
                )
                
                # Determine orchestration strategy
                strategy = await self._determine_orchestration_strategy(
                    message_type, context, route_metadata
                )
                
                # Select agents based on strategy
                selected_agents = await self._select_agents(
                    message_type, context, strategy, route_metadata
                )
                
                # Identify required tools
                required_tools = await self._identify_required_tools(
                    message_type, context, selected_agents
                )
                
                # Execute orchestration based on strategy
                if strategy == OrchestrationStrategy.SINGLE_AGENT:
                    result = await self._execute_single_agent(
                        selected_agents[0], message, context, required_tools
                    )
                elif strategy == OrchestrationStrategy.MULTI_AGENT_PARALLEL:
                    result = await self._execute_parallel_agents(
                        selected_agents, message, context, required_tools
                    )
                elif strategy == OrchestrationStrategy.MULTI_AGENT_SEQUENTIAL:
                    result = await self._execute_sequential_agents(
                        selected_agents, message, context, required_tools
                    )
                elif strategy == OrchestrationStrategy.AGENT_PIPELINE:
                    result = await self._execute_agent_pipeline(
                        selected_agents, message, context, required_tools
                    )
                else:  # ADAPTIVE_ROUTING
                    result = await self._execute_adaptive_routing(
                        selected_agents, message, context, required_tools
                    )
                
                # Calculate orchestration metrics
                total_time = time.perf_counter() - start_time
                result.total_execution_time = total_time
                result.orchestration_overhead = await self._calculate_overhead(
                    orchestration_id, total_time
                )
                result.orchestration_strategy = strategy
                
                # Update agent performance
                await self._update_agent_performance(selected_agents, result)
                
                # Quality assessment
                await self._assess_result_quality(result, context)
                
                # Record orchestration
                self._record_orchestration(orchestration_id, result, selected_agents)
                
                logger.info(
                    f"Orchestration {orchestration_id} completed in {total_time:.2f}s, "
                    f"quality_score={result.response_quality_score:.2f}"
                )
                
                return result
                
            except Exception as e:
                logger.error(
                    f"Orchestration {orchestration_id} failed: {str(e)}", 
                    exc_info=True
                )
                
                # Return fallback result
                return AgentResult(
                    success=False,
                    agent_name="fallback",
                    primary_response=f"I apologize, but I encountered an error processing your request: {str(e)}",
                    errors=[str(e)],
                    fallback_used=True,
                    total_execution_time=time.perf_counter() - start_time
                )
            
            finally:
                self.active_orchestrations.pop(orchestration_id, None)
    
    async def _determine_orchestration_strategy(
        self,
        message_type: MessageType,
        context: MessageContext,
        route_metadata: Optional[Dict[str, Any]]
    ) -> OrchestrationStrategy:
        """Determine the best orchestration strategy for the request"""
        
        # Complex technical questions benefit from multiple agents
        if message_type == MessageType.TEXT_TECHNICAL and context.text_content:
            if len(context.text_content) > 500 or "compare" in context.text_content.lower():
                return OrchestrationStrategy.MULTI_AGENT_PARALLEL
        
        # Image analysis with text description needs pipeline
        if message_type in [MessageType.IMAGE_PHOTO, MessageType.IMAGE_SCREENSHOT]:
            return OrchestrationStrategy.AGENT_PIPELINE
        
        # Code analysis with multiple files needs sequential processing
        if message_type == MessageType.DOCUMENT_CODE:
            return OrchestrationStrategy.MULTI_AGENT_SEQUENTIAL
        
        # Voice messages need transcription then processing
        if message_type == MessageType.AUDIO_VOICE:
            return OrchestrationStrategy.AGENT_PIPELINE
        
        # Use adaptive routing for complex contexts
        if (context.conversation_history and len(context.conversation_history) > 10 or
            context.workspace_context):
            return OrchestrationStrategy.ADAPTIVE_ROUTING
        
        # Default to single agent for simple requests
        return OrchestrationStrategy.SINGLE_AGENT
    
    async def _select_agents(
        self,
        message_type: MessageType,
        context: MessageContext,
        strategy: OrchestrationStrategy,
        route_metadata: Optional[Dict[str, Any]]
    ) -> List[AgentInstance]:
        """Select appropriate agents based on message type and strategy"""
        
        selected_agents = []
        
        # Get message hash for caching
        message_hash = self._get_message_hash(message_type, context.text_content or "")
        
        # Check routing cache for optimization
        if self.enable_performance_optimization and message_hash in self.routing_cache:
            cached_agent_id = self.routing_cache[message_hash]
            if cached_agent_id in self.agent_instances:
                cached_agent = self.agent_instances[cached_agent_id]
                if cached_agent.status == AgentStatus.IDLE:
                    selected_agents.append(cached_agent)
                    return selected_agents
        
        # Agent selection based on message type
        primary_agent = await self._select_primary_agent(message_type, context)
        selected_agents.append(primary_agent)
        
        # Add secondary agents based on strategy
        if strategy == OrchestrationStrategy.MULTI_AGENT_PARALLEL:
            secondary_agents = await self._select_secondary_agents(
                message_type, context, exclude=[primary_agent.agent_id]
            )
            selected_agents.extend(secondary_agents[:2])  # Limit to 3 total
        
        elif strategy == OrchestrationStrategy.MULTI_AGENT_SEQUENTIAL:
            sequential_agents = await self._select_sequential_agents(
                message_type, context, primary_agent
            )
            selected_agents.extend(sequential_agents)
        
        elif strategy == OrchestrationStrategy.AGENT_PIPELINE:
            pipeline_agents = await self._select_pipeline_agents(
                message_type, context
            )
            selected_agents = pipeline_agents
        
        # Cache routing decision
        if self.enable_performance_optimization and selected_agents:
            self.routing_cache[message_hash] = selected_agents[0].agent_id
            
            # Limit cache size
            if len(self.routing_cache) > 1000:
                # Remove oldest entries
                oldest_keys = list(self.routing_cache.keys())[:100]
                for key in oldest_keys:
                    del self.routing_cache[key]
        
        return selected_agents
    
    async def _select_primary_agent(
        self,
        message_type: MessageType,
        context: MessageContext
    ) -> AgentInstance:
        """Select the primary agent for processing"""
        
        # Check for specialized agents
        if message_type == MessageType.TEXT_TECHNICAL:
            return await self._get_or_create_agent("technical_specialist", "technical")
        
        elif message_type == MessageType.TEXT_CREATIVE:
            return await self._get_or_create_agent("creative_specialist", "creative")
        
        elif message_type.value.startswith("image_"):
            return await self._get_or_create_agent("vision_specialist", "vision")
        
        elif message_type.value.startswith("document_"):
            return await self._get_or_create_agent("document_specialist", "document")
        
        elif message_type == MessageType.AUDIO_VOICE:
            return await self._get_or_create_agent("audio_specialist", "audio")
        
        # Default to general agent
        return await self._get_or_create_agent("general_agent", "general")
    
    async def _get_or_create_agent(
        self,
        agent_id: str,
        agent_type: str
    ) -> AgentInstance:
        """Get existing agent or create new one"""
        
        if agent_id in self.agent_instances:
            return self.agent_instances[agent_id]
        
        # Create new agent
        agent = ValorAgent(
            model=self.default_model,
            debug=True
        )
        
        # Initialize agent with tools from registry
        await self._initialize_agent_tools(agent, agent_type)
        
        # Create agent instance
        agent_instance = AgentInstance(
            agent_id=agent_id,
            agent=agent,
            agent_type=agent_type,
            specializations=[agent_type],
            max_concurrent_tasks=3 if agent_type == "general" else 2
        )
        
        self.agent_instances[agent_id] = agent_instance
        self.agent_specializations[agent_type].append(agent_id)
        
        logger.info(f"Created new agent: {agent_id} of type {agent_type}")
        return agent_instance
    
    async def _initialize_agent_tools(
        self,
        agent: ValorAgent,
        agent_type: str
    ) -> None:
        """Initialize agent with appropriate tools"""
        
        # Get all available tools
        available_tools = self.tool_registry.list_tools()
        
        # Tool selection based on agent type
        if agent_type == "technical":
            preferred_tools = ["search_tool", "knowledge_search", "code_execution"]
        elif agent_type == "creative":
            preferred_tools = ["image_generation", "search_tool"]
        elif agent_type == "vision":
            preferred_tools = ["image_analysis", "search_tool"]
        elif agent_type == "document":
            preferred_tools = ["knowledge_search", "search_tool", "code_execution"]
        elif agent_type == "audio":
            preferred_tools = ["search_tool", "knowledge_search"]
        else:  # general
            preferred_tools = ["search_tool", "knowledge_search", "image_analysis"]
        
        # Register preferred tools
        for tool_name in preferred_tools:
            if tool_name in available_tools:
                try:
                    tool_instance = self.tool_registry.get_tool(tool_name)
                    if tool_instance:
                        agent.register_tool(tool_instance)
                except Exception as e:
                    logger.warning(f"Failed to register tool {tool_name} for agent {agent_type}: {e}")
    
    async def _select_secondary_agents(
        self,
        message_type: MessageType,
        context: MessageContext,
        exclude: List[str]
    ) -> List[AgentInstance]:
        """Select secondary agents for parallel processing"""
        
        secondary_agents = []
        
        # For technical questions, add search specialist
        if message_type == MessageType.TEXT_TECHNICAL:
            search_agent = await self._get_or_create_agent("search_specialist", "search")
            if search_agent.agent_id not in exclude:
                secondary_agents.append(search_agent)
        
        # For complex questions, add knowledge specialist
        if context.text_content and len(context.text_content) > 200:
            knowledge_agent = await self._get_or_create_agent("knowledge_specialist", "knowledge")
            if knowledge_agent.agent_id not in exclude:
                secondary_agents.append(knowledge_agent)
        
        return secondary_agents
    
    async def _select_sequential_agents(
        self,
        message_type: MessageType,
        context: MessageContext,
        primary_agent: AgentInstance
    ) -> List[AgentInstance]:
        """Select agents for sequential processing"""
        
        sequential_agents = []
        
        # For document processing, add analysis then summary agent
        if message_type == MessageType.DOCUMENT_CODE:
            analysis_agent = await self._get_or_create_agent("code_analyzer", "analysis")
            summary_agent = await self._get_or_create_agent("code_summarizer", "summary")
            sequential_agents.extend([analysis_agent, summary_agent])
        
        return sequential_agents
    
    async def _select_pipeline_agents(
        self,
        message_type: MessageType,
        context: MessageContext
    ) -> List[AgentInstance]:
        """Select agents for pipeline processing"""
        
        pipeline_agents = []
        
        # Image processing pipeline
        if message_type.value.startswith("image_"):
            vision_agent = await self._get_or_create_agent("vision_processor", "vision")
            description_agent = await self._get_or_create_agent("description_generator", "description")
            pipeline_agents.extend([vision_agent, description_agent])
        
        # Voice processing pipeline
        elif message_type == MessageType.AUDIO_VOICE:
            transcription_agent = await self._get_or_create_agent("transcription_processor", "transcription")
            response_agent = await self._get_or_create_agent("response_generator", "response")
            pipeline_agents.extend([transcription_agent, response_agent])
        
        return pipeline_agents
    
    async def _identify_required_tools(
        self,
        message_type: MessageType,
        context: MessageContext,
        selected_agents: List[AgentInstance]
    ) -> List[ToolAssignment]:
        """Identify tools required for processing"""
        
        required_tools = []
        
        # Message type specific tools
        if message_type == MessageType.TEXT_QUESTION:
            required_tools.append(ToolAssignment(
                tool_name="search_tool",
                tool_instance=self.tool_registry.get_tool("search_tool"),
                priority=1,
                estimated_time=2.0
            ))
        
        if message_type.value.startswith("image_"):
            image_tool = self.tool_registry.get_tool("image_analysis")
            if image_tool:
                required_tools.append(ToolAssignment(
                    tool_name="image_analysis",
                    tool_instance=image_tool,
                    priority=1,
                    estimated_time=3.0
                ))
        
        if message_type == MessageType.DOCUMENT_CODE:
            code_tool = self.tool_registry.get_tool("code_execution")
            if code_tool:
                required_tools.append(ToolAssignment(
                    tool_name="code_execution",
                    tool_instance=code_tool,
                    priority=2,
                    estimated_time=5.0
                ))
        
        # Context-based tools
        if context.text_content and any(keyword in context.text_content.lower() 
                                       for keyword in ["search", "find", "lookup"]):
            search_tool = self.tool_registry.get_tool("search_tool")
            if search_tool and not any(t.tool_name == "search_tool" for t in required_tools):
                required_tools.append(ToolAssignment(
                    tool_name="search_tool",
                    tool_instance=search_tool,
                    priority=1,
                    estimated_time=2.0
                ))
        
        return required_tools
    
    async def _execute_single_agent(
        self,
        agent_instance: AgentInstance,
        message: Message,
        context: MessageContext,
        tools: List[ToolAssignment]
    ) -> AgentResult:
        """Execute processing with a single agent"""
        
        start_time = time.perf_counter()
        
        try:
            # Update agent status
            agent_instance.status = AgentStatus.BUSY
            agent_instance.current_tasks += 1
            
            # Prepare message for agent
            message_text = context.text_content or "[Media message]"
            
            # Process message
            response = await asyncio.wait_for(
                agent_instance.agent.process_message(
                    message=message_text,
                    chat_id=f"telegram_{context.chat_id}",
                    user_name=f"user_{context.user_id}" if context.user_id else "anonymous"
                ),
                timeout=self.agent_timeout_seconds
            )
            
            execution_time = time.perf_counter() - start_time
            
            # Extract tool usage information
            tools_used = []
            tool_outputs = {}
            tool_execution_times = {}
            tool_success_rates = {}
            
            if hasattr(response, 'tools_used'):
                tools_used = response.tools_used
            
            # Create result
            result = AgentResult(
                success=True,
                agent_name=agent_instance.agent_id,
                agent_instances=[agent_instance.agent_id],
                primary_response=response.content if hasattr(response, 'content') else str(response),
                tools_used=tools_used,
                tool_outputs=tool_outputs,
                tool_execution_times=tool_execution_times,
                tool_success_rates=tool_success_rates,
                agent_execution_times={agent_instance.agent_id: execution_time}
            )
            
            return result
            
        except asyncio.TimeoutError:
            logger.warning(f"Agent {agent_instance.agent_id} timed out")
            agent_instance.error_count += 1
            
            return AgentResult(
                success=False,
                agent_name=agent_instance.agent_id,
                primary_response="Request timed out. Please try again with a simpler query.",
                errors=["Agent execution timeout"],
                fallback_used=True
            )
        
        except Exception as e:
            logger.error(f"Agent {agent_instance.agent_id} execution failed: {str(e)}")
            agent_instance.error_count += 1
            
            return AgentResult(
                success=False,
                agent_name=agent_instance.agent_id,
                primary_response=f"I encountered an error: {str(e)}",
                errors=[str(e)],
                fallback_used=True
            )
        
        finally:
            agent_instance.status = AgentStatus.IDLE
            agent_instance.current_tasks = max(0, agent_instance.current_tasks - 1)
            agent_instance.last_used = time.time()
    
    async def _execute_parallel_agents(
        self,
        agent_instances: List[AgentInstance],
        message: Message,
        context: MessageContext,
        tools: List[ToolAssignment]
    ) -> AgentResult:
        """Execute processing with multiple agents in parallel"""
        
        start_time = time.perf_counter()
        
        # Execute all agents in parallel
        tasks = []
        for agent_instance in agent_instances:
            task = asyncio.create_task(
                self._execute_single_agent(agent_instance, message, context, tools)
            )
            tasks.append((agent_instance.agent_id, task))
        
        # Wait for all to complete
        results = []
        for agent_id, task in tasks:
            try:
                result = await task
                results.append((agent_id, result))
            except Exception as e:
                logger.error(f"Parallel agent {agent_id} failed: {str(e)}")
                results.append((agent_id, AgentResult(
                    success=False,
                    agent_name=agent_id,
                    primary_response=f"Agent {agent_id} failed: {str(e)}",
                    errors=[str(e)]
                )))
        
        # Combine results
        primary_result = None
        supplementary_responses = {}
        all_tools_used = []
        all_agent_names = []
        all_errors = []
        all_warnings = []
        
        for agent_id, result in results:
            all_agent_names.append(agent_id)
            all_tools_used.extend(result.tools_used)
            all_errors.extend(result.errors)
            all_warnings.extend(result.warnings)
            
            if result.success and primary_result is None:
                primary_result = result
            elif result.success:
                supplementary_responses[agent_id] = result.primary_response
        
        # Create combined result
        if primary_result:
            combined_result = AgentResult(
                success=True,
                agent_name=primary_result.agent_name,
                agent_instances=all_agent_names,
                primary_response=primary_result.primary_response,
                supplementary_responses=supplementary_responses,
                tools_used=list(set(all_tools_used)),
                errors=all_errors,
                warnings=all_warnings,
                total_execution_time=time.perf_counter() - start_time
            )
        else:
            # All agents failed
            combined_result = AgentResult(
                success=False,
                agent_name="parallel_execution",
                agent_instances=all_agent_names,
                primary_response="All agents failed to process the request.",
                errors=all_errors,
                fallback_used=True,
                total_execution_time=time.perf_counter() - start_time
            )
        
        return combined_result
    
    async def _execute_sequential_agents(
        self,
        agent_instances: List[AgentInstance],
        message: Message,
        context: MessageContext,
        tools: List[ToolAssignment]
    ) -> AgentResult:
        """Execute processing with multiple agents in sequence"""
        
        start_time = time.perf_counter()
        current_context = context
        accumulated_response = ""
        all_tools_used = []
        all_agent_names = []
        execution_times = {}
        
        for i, agent_instance in enumerate(agent_instances):
            try:
                # Execute agent with current context
                result = await self._execute_single_agent(
                    agent_instance, message, current_context, tools
                )
                
                all_agent_names.append(agent_instance.agent_id)
                all_tools_used.extend(result.tools_used)
                execution_times[agent_instance.agent_id] = result.total_execution_time
                
                if result.success:
                    if i == 0:
                        accumulated_response = result.primary_response
                    else:
                        accumulated_response += f"\n\n{result.primary_response}"
                    
                    # Update context for next agent (simplified)
                    # In a real implementation, you'd update the context with new information
                    
                else:
                    # Agent failed, but continue with others
                    logger.warning(f"Sequential agent {agent_instance.agent_id} failed: {result.errors}")
                    
            except Exception as e:
                logger.error(f"Sequential agent {agent_instance.agent_id} error: {str(e)}")
                continue
        
        return AgentResult(
            success=len(accumulated_response) > 0,
            agent_name=all_agent_names[0] if all_agent_names else "sequential_execution",
            agent_instances=all_agent_names,
            primary_response=accumulated_response or "Sequential processing failed",
            tools_used=list(set(all_tools_used)),
            agent_execution_times=execution_times,
            total_execution_time=time.perf_counter() - start_time
        )
    
    async def _execute_agent_pipeline(
        self,
        agent_instances: List[AgentInstance],
        message: Message,
        context: MessageContext,
        tools: List[ToolAssignment]
    ) -> AgentResult:
        """Execute processing through an agent pipeline"""
        
        # For now, implement as sequential processing
        # In a full implementation, this would include data transformation between stages
        return await self._execute_sequential_agents(agent_instances, message, context, tools)
    
    async def _execute_adaptive_routing(
        self,
        agent_instances: List[AgentInstance],
        message: Message,
        context: MessageContext,
        tools: List[ToolAssignment]
    ) -> AgentResult:
        """Execute with adaptive routing based on dynamic conditions"""
        
        # Choose strategy based on current system load and performance
        if len(self.active_orchestrations) > 3:
            # High load, use single agent
            return await self._execute_single_agent(agent_instances[0], message, context, tools)
        else:
            # Normal load, use parallel processing if multiple agents
            if len(agent_instances) > 1:
                return await self._execute_parallel_agents(agent_instances, message, context, tools)
            else:
                return await self._execute_single_agent(agent_instances[0], message, context, tools)
    
    async def _calculate_overhead(
        self,
        orchestration_id: str,
        total_time: float
    ) -> float:
        """Calculate orchestration overhead"""
        
        orchestration_data = self.active_orchestrations.get(orchestration_id, {})
        
        # Simple overhead calculation - difference between total time and agent execution time
        # In a real implementation, this would be more sophisticated
        return min(total_time * 0.1, 0.5)  # Max 0.5 seconds or 10% of total time
    
    async def _update_agent_performance(
        self,
        agent_instances: List[AgentInstance],
        result: AgentResult
    ) -> None:
        """Update agent performance metrics"""
        
        for agent_instance in agent_instances:
            agent_instance.total_tasks_completed += 1
            agent_instance.total_processing_time += result.total_execution_time
            
            # Update performance score based on success and quality
            if result.success:
                quality_factor = result.response_quality_score
                speed_factor = min(1.0, 5.0 / result.total_execution_time)  # Faster is better
                
                performance_score = (quality_factor + speed_factor) / 2
                
                # Moving average
                agent_instance.performance_score = (
                    agent_instance.performance_score * 0.9 + performance_score * 0.1
                )
            else:
                # Reduce performance score for failures
                agent_instance.performance_score *= 0.95
    
    async def _assess_result_quality(
        self,
        result: AgentResult,
        context: MessageContext
    ) -> None:
        """Assess and update result quality scores"""
        
        # Simple quality assessment - in production this would be more sophisticated
        
        # Response completeness
        if len(result.primary_response) < 50:
            result.completeness_score = 0.3
        elif len(result.primary_response) < 200:
            result.completeness_score = 0.7
        else:
            result.completeness_score = 1.0
        
        # Tool usage appropriateness
        if result.tools_used:
            result.response_quality_score = min(1.0, result.response_quality_score + 0.1)
        
        # Error handling
        if result.errors:
            result.response_quality_score = max(0.2, result.response_quality_score - 0.2)
    
    def _record_orchestration(
        self,
        orchestration_id: str,
        result: AgentResult,
        agents_used: List[AgentInstance]
    ) -> None:
        """Record orchestration for performance analysis"""
        
        record = {
            "orchestration_id": orchestration_id,
            "timestamp": time.time(),
            "success": result.success,
            "agents_used": [a.agent_id for a in agents_used],
            "tools_used": result.tools_used,
            "execution_time": result.total_execution_time,
            "quality_score": result.response_quality_score,
            "strategy": result.orchestration_strategy.value
        }
        
        self.orchestration_history.append(record)
        
        # Keep only recent history
        if len(self.orchestration_history) > 1000:
            self.orchestration_history = self.orchestration_history[-500:]
    
    def _get_message_hash(self, message_type: MessageType, content: str) -> str:
        """Generate hash for message caching"""
        import hashlib
        hash_input = f"{message_type.value}:{content[:100]}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:8]
    
    async def _initialize_default_agents(self) -> None:
        """Initialize default agent instances"""
        try:
            # Create general purpose agent
            await self._get_or_create_agent("general_agent", "general")
            
            # Create specialized agents
            await self._get_or_create_agent("technical_specialist", "technical")
            
            logger.info("Default agents initialized")
        except Exception as e:
            logger.error(f"Failed to initialize default agents: {str(e)}")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get orchestrator status and statistics"""
        
        active_agents = sum(1 for agent in self.agent_instances.values() 
                           if agent.status == AgentStatus.BUSY)
        
        avg_performance = (
            sum(agent.performance_score for agent in self.agent_instances.values()) /
            len(self.agent_instances)
            if self.agent_instances else 0.0
        )
        
        recent_success_rate = 0.0
        if self.orchestration_history:
            recent_orchestrations = self.orchestration_history[-50:]  # Last 50
            successful = sum(1 for record in recent_orchestrations if record["success"])
            recent_success_rate = successful / len(recent_orchestrations)
        
        return {
            "total_agents": len(self.agent_instances),
            "active_agents": active_agents,
            "active_orchestrations": len(self.active_orchestrations),
            "total_orchestrations": self.orchestration_count,
            "recent_success_rate": recent_success_rate,
            "avg_agent_performance": avg_performance,
            "routing_cache_size": len(self.routing_cache),
            "specializations": dict(self.agent_specializations),
            "performance_optimization": self.enable_performance_optimization,
            "adaptive_routing": self.enable_adaptive_routing
        }
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the agent orchestrator"""
        logger.info("Shutting down agent orchestrator...")
        
        # Wait for active orchestrations to complete
        while self.active_orchestrations:
            logger.info(f"Waiting for {len(self.active_orchestrations)} active orchestrations...")
            await asyncio.sleep(0.5)
        
        # Shutdown all agents
        for agent_instance in self.agent_instances.values():
            try:
                if hasattr(agent_instance.agent, 'shutdown'):
                    await agent_instance.agent.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down agent {agent_instance.agent_id}: {e}")
        
        logger.info("Agent orchestrator shutdown complete")