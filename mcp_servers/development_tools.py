"""
Development Tools MCP Server

This module implements MCP server for development and debugging tools:
- Code execution in various environments
- Debugging and profiling tools
- Environment management
- Process monitoring
- Test execution
"""

import asyncio
import logging
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json
import os
import signal
import psutil

from pydantic import BaseModel, Field, validator

from .base import MCPServer, MCPToolCapability, MCPRequest, MCPError
from .context_manager import MCPContextManager, SecurityLevel


class CodeExecutionResult(BaseModel):
    """Code execution result structure."""
    
    execution_id: str = Field(..., description="Execution ID")
    language: str = Field(..., description="Programming language")
    code: str = Field(..., description="Executed code")
    
    # Execution results
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")
    return_code: int = Field(default=0, description="Process return code")
    
    # Execution metadata
    execution_time_ms: float = Field(..., description="Execution time in milliseconds")
    memory_usage_mb: Optional[float] = Field(None, description="Peak memory usage in MB")
    
    # Status
    success: bool = Field(default=True, description="Whether execution was successful")
    timeout: bool = Field(default=False, description="Whether execution timed out")
    error_message: Optional[str] = Field(None, description="Error message if execution failed")
    
    # Files created/modified
    created_files: List[str] = Field(default_factory=list, description="Files created during execution")
    
    # Timestamps
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProcessInfo(BaseModel):
    """Process information structure."""
    
    pid: int = Field(..., description="Process ID")
    name: str = Field(..., description="Process name")
    status: str = Field(..., description="Process status")
    
    # Resource usage
    cpu_percent: float = Field(default=0.0, description="CPU usage percentage")
    memory_mb: float = Field(default=0.0, description="Memory usage in MB")
    
    # Process details
    cmdline: List[str] = Field(default_factory=list, description="Command line arguments")
    cwd: Optional[str] = Field(None, description="Current working directory")
    username: Optional[str] = Field(None, description="Process owner username")
    
    # Timestamps
    create_time: datetime = Field(..., description="Process creation time")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class DebugSession(BaseModel):
    """Debug session structure."""
    
    session_id: str = Field(..., description="Debug session ID")
    language: str = Field(..., description="Programming language")
    target_file: str = Field(..., description="Target file being debugged")
    
    # Debug state
    is_active: bool = Field(default=False, description="Whether session is active")
    current_line: Optional[int] = Field(None, description="Current line number")
    breakpoints: List[int] = Field(default_factory=list, description="Set breakpoints")
    
    # Variables and stack
    local_variables: Dict[str, Any] = Field(default_factory=dict, description="Local variables")
    call_stack: List[str] = Field(default_factory=list, description="Call stack frames")
    
    # Session metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProfileResult(BaseModel):
    """Code profiling result structure."""
    
    profile_id: str = Field(..., description="Profile ID")
    language: str = Field(..., description="Programming language")
    code: str = Field(..., description="Profiled code")
    
    # Profiling data
    total_time: float = Field(..., description="Total execution time")
    function_calls: int = Field(default=0, description="Number of function calls")
    
    # Performance metrics
    top_functions: List[Dict[str, Any]] = Field(default_factory=list, description="Top time-consuming functions")
    memory_profile: Dict[str, Any] = Field(default_factory=dict, description="Memory usage profile")
    
    # Timestamps
    profiled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TestResult(BaseModel):
    """Test execution result structure."""
    
    test_id: str = Field(..., description="Test execution ID")
    test_framework: str = Field(..., description="Test framework used")
    test_path: str = Field(..., description="Path to test file or directory")
    
    # Test results
    total_tests: int = Field(default=0, description="Total number of tests")
    passed_tests: int = Field(default=0, description="Number of passed tests")
    failed_tests: int = Field(default=0, description="Number of failed tests")
    skipped_tests: int = Field(default=0, description="Number of skipped tests")
    
    # Test details
    test_details: List[Dict[str, Any]] = Field(default_factory=list, description="Individual test results")
    coverage_report: Optional[Dict[str, Any]] = Field(None, description="Code coverage report")
    
    # Execution metadata
    execution_time: float = Field(default=0.0, description="Total execution time")
    success: bool = Field(default=True, description="Whether test run was successful")
    
    # Timestamps
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class DevelopmentToolsServer(MCPServer):
    """
    MCP Server implementation for development and debugging tools.
    
    Provides stateless development functionality with context injection including:
    - Code execution in sandboxed environments
    - Process monitoring and management
    - Debugging capabilities
    - Performance profiling
    - Test execution and reporting
    """
    
    def __init__(
        self,
        name: str = "development_tools",
        version: str = "1.0.0",
        description: str = "Development and debugging tools MCP server",
        allowed_languages: List[str] = None,
        execution_timeout: int = 30,
        max_memory_mb: int = 512,
        sandbox_enabled: bool = True,
        **kwargs
    ):
        super().__init__(name, version, description, **kwargs)
        
        # Configuration
        self.allowed_languages = allowed_languages or ["python", "javascript", "bash", "sql"]
        self.execution_timeout = execution_timeout
        self.max_memory_mb = max_memory_mb
        self.sandbox_enabled = sandbox_enabled
        
        # Language configurations
        self._language_configs = {
            "python": {
                "command": [sys.executable, "-c"],
                "file_extension": ".py",
                "packages": ["psutil", "memory_profiler", "cProfile"]
            },
            "javascript": {
                "command": ["node", "-e"],
                "file_extension": ".js",
                "packages": []
            },
            "bash": {
                "command": ["bash", "-c"],
                "file_extension": ".sh",
                "packages": []
            },
            "sql": {
                "command": ["sqlite3", ":memory:"],
                "file_extension": ".sql",
                "packages": []
            }
        }
        
        # Active sessions and processes
        self._execution_history: Dict[str, CodeExecutionResult] = {}
        self._debug_sessions: Dict[str, DebugSession] = {}
        self._running_processes: Dict[str, subprocess.Popen] = {}
        
        # Security settings
        self._restricted_imports = [
            "os", "sys", "subprocess", "importlib", "__import__",
            "eval", "exec", "compile", "open", "file"
        ]
        
        self.logger.info(f"Development Tools Server initialized with languages: {self.allowed_languages}")
    
    async def initialize(self) -> None:
        """Initialize the development tools server."""
        try:
            # Register all tool capabilities
            await self._register_execution_tools()
            await self._register_debugging_tools()
            await self._register_profiling_tools()
            await self._register_process_tools()
            await self._register_test_tools()
            
            # Verify language availability
            await self._verify_language_availability()
            
            self.logger.info("Development Tools Server initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Development Tools Server: {str(e)}")
            raise MCPError(
                f"Development server initialization failed: {str(e)}",
                error_code="DEV_TOOLS_INIT_ERROR",
                details={"error": str(e)},
                recoverable=False
            )
    
    async def shutdown(self) -> None:
        """Shutdown the development tools server."""
        try:
            # Terminate any running processes
            for proc_id, process in self._running_processes.items():
                try:
                    if process.poll() is None:  # Process is still running
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                except Exception as e:
                    self.logger.warning(f"Error terminating process {proc_id}: {str(e)}")
            
            self._running_processes.clear()
            
            self.logger.info("Development Tools Server shut down successfully")
            
        except Exception as e:
            self.logger.error(f"Error during Development Tools Server shutdown: {str(e)}")
    
    # Code Execution Tools
    
    async def _register_execution_tools(self) -> None:
        """Register code execution tool capabilities."""
        
        # Execute code
        execute_code_capability = MCPToolCapability(
            name="execute_code",
            description="Execute code in a specified language",
            parameters={
                "language": {
                    "type": "string",
                    "required": True,
                    "enum": self.allowed_languages,
                    "description": "Programming language"
                },
                "code": {"type": "string", "required": True, "description": "Code to execute"},
                "timeout": {"type": "integer", "required": False, "description": f"Execution timeout in seconds (max {self.execution_timeout})"},
                "working_directory": {"type": "string", "required": False, "description": "Working directory for execution"},
                "environment": {"type": "object", "required": False, "description": "Environment variables"}
            },
            returns={"type": "object", "description": "Execution result"},
            tags=["development", "code", "execution"]
        )
        self.register_tool(execute_code_capability, self._handle_execute_code)
        
        # Execute code from file
        execute_file_capability = MCPToolCapability(
            name="execute_file",
            description="Execute code from a file",
            parameters={
                "file_path": {"type": "string", "required": True, "description": "Path to code file"},
                "arguments": {"type": "array", "items": "string", "required": False, "description": "Command line arguments"},
                "timeout": {"type": "integer", "required": False, "description": "Execution timeout in seconds"},
                "working_directory": {"type": "string", "required": False, "description": "Working directory for execution"}
            },
            returns={"type": "object", "description": "Execution result"},
            tags=["development", "code", "execution", "file"]
        )
        self.register_tool(execute_file_capability, self._handle_execute_file)
        
        # Get execution history
        get_execution_history_capability = MCPToolCapability(
            name="get_execution_history",
            description="Get history of code executions",
            parameters={
                "limit": {"type": "integer", "required": False, "default": 10, "description": "Maximum number of results"},
                "language": {"type": "string", "required": False, "description": "Filter by language"}
            },
            returns={"type": "array", "items": "CodeExecutionResult"},
            tags=["development", "code", "history"]
        )
        self.register_tool(get_execution_history_capability, self._handle_get_execution_history)
    
    async def _handle_execute_code(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle code execution requests."""
        language = request.params.get("language")
        code = request.params.get("code")
        timeout = min(request.params.get("timeout", self.execution_timeout), self.execution_timeout)
        working_directory = request.params.get("working_directory")
        environment = request.params.get("environment", {})
        
        if not language or not code:
            raise MCPError(
                "Language and code are required",
                error_code="MISSING_EXECUTION_PARAMS",
                request_id=request.id
            )
        
        if language not in self.allowed_languages:
            raise MCPError(
                f"Language '{language}' is not allowed",
                error_code="LANGUAGE_NOT_ALLOWED",
                details={"allowed_languages": self.allowed_languages},
                request_id=request.id
            )
        
        # Security check for restricted imports/commands
        if self.sandbox_enabled:
            security_issues = self._check_code_security(code, language)
            if security_issues:
                raise MCPError(
                    f"Security violations detected: {', '.join(security_issues)}",
                    error_code="SECURITY_VIOLATION",
                    details={"violations": security_issues},
                    request_id=request.id
                )
        
        try:
            execution_id = f"exec_{int(time.time())}_{id(request)}"
            
            # Prepare execution environment
            execution_result = CodeExecutionResult(
                execution_id=execution_id,
                language=language,
                code=code,
                started_at=datetime.now(timezone.utc)
            )
            
            # Execute code
            start_time = time.time()
            
            try:
                stdout, stderr, return_code = await self._execute_code_safely(
                    language, code, timeout, working_directory, environment
                )
                
                execution_time_ms = (time.time() - start_time) * 1000
                
                execution_result.stdout = stdout
                execution_result.stderr = stderr
                execution_result.return_code = return_code
                execution_result.execution_time_ms = execution_time_ms
                execution_result.success = (return_code == 0)
                execution_result.completed_at = datetime.now(timezone.utc)
                
            except asyncio.TimeoutError:
                execution_result.timeout = True
                execution_result.success = False
                execution_result.error_message = f"Execution timed out after {timeout} seconds"
                execution_result.completed_at = datetime.now(timezone.utc)
            
            except Exception as e:
                execution_result.success = False
                execution_result.error_message = str(e)
                execution_result.stderr = traceback.format_exc()
                execution_result.completed_at = datetime.now(timezone.utc)
            
            # Store execution result
            self._execution_history[execution_id] = execution_result
            
            # Limit history size
            if len(self._execution_history) > 100:
                oldest_keys = sorted(self._execution_history.keys())[:50]
                for key in oldest_keys:
                    del self._execution_history[key]
            
            self.logger.info(f"Code execution completed: {execution_id} (success={execution_result.success})")
            
            return execution_result.dict()
            
        except Exception as e:
            self.logger.error(f"Code execution failed: {str(e)}")
            raise MCPError(
                f"Code execution failed: {str(e)}",
                error_code="CODE_EXECUTION_ERROR",
                details={"language": language, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_execute_file(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle file execution requests."""
        file_path = request.params.get("file_path")
        arguments = request.params.get("arguments", [])
        timeout = min(request.params.get("timeout", self.execution_timeout), self.execution_timeout)
        working_directory = request.params.get("working_directory")
        
        if not file_path:
            raise MCPError(
                "File path is required",
                error_code="MISSING_FILE_PATH",
                request_id=request.id
            )
        
        # Verify file exists and is readable
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise MCPError(
                f"File not found: {file_path}",
                error_code="FILE_NOT_FOUND",
                request_id=request.id
            )
        
        if not file_path_obj.is_file():
            raise MCPError(
                f"Path is not a file: {file_path}",
                error_code="NOT_A_FILE",
                request_id=request.id
            )
        
        try:
            # Read file content for security check
            with open(file_path, 'r', encoding='utf-8') as f:
                code_content = f.read()
            
            # Determine language from file extension
            extension = file_path_obj.suffix.lower()
            language = None
            for lang, config in self._language_configs.items():
                if config["file_extension"] == extension:
                    language = lang
                    break
            
            if not language:
                raise MCPError(
                    f"Unsupported file extension: {extension}",
                    error_code="UNSUPPORTED_FILE_TYPE",
                    details={"file_extension": extension},
                    request_id=request.id
                )
            
            if language not in self.allowed_languages:
                raise MCPError(
                    f"Language '{language}' is not allowed",
                    error_code="LANGUAGE_NOT_ALLOWED",
                    details={"allowed_languages": self.allowed_languages},
                    request_id=request.id
                )
            
            # Security check
            if self.sandbox_enabled:
                security_issues = self._check_code_security(code_content, language)
                if security_issues:
                    raise MCPError(
                        f"Security violations detected in file: {', '.join(security_issues)}",
                        error_code="SECURITY_VIOLATION",
                        details={"violations": security_issues},
                        request_id=request.id
                    )
            
            execution_id = f"file_exec_{int(time.time())}_{id(request)}"
            
            execution_result = CodeExecutionResult(
                execution_id=execution_id,
                language=language,
                code=f"# File: {file_path}\n{code_content}",
                started_at=datetime.now(timezone.utc)
            )
            
            # Execute file
            start_time = time.time()
            
            try:
                # Prepare command
                config = self._language_configs[language]
                if language == "python":
                    command = [sys.executable, file_path] + arguments
                elif language == "javascript":
                    command = ["node", file_path] + arguments
                elif language == "bash":
                    command = ["bash", file_path] + arguments
                else:
                    command = config["command"] + [file_path] + arguments
                
                stdout, stderr, return_code = await self._run_process(
                    command, timeout, working_directory
                )
                
                execution_time_ms = (time.time() - start_time) * 1000
                
                execution_result.stdout = stdout
                execution_result.stderr = stderr
                execution_result.return_code = return_code
                execution_result.execution_time_ms = execution_time_ms
                execution_result.success = (return_code == 0)
                execution_result.completed_at = datetime.now(timezone.utc)
                
            except asyncio.TimeoutError:
                execution_result.timeout = True
                execution_result.success = False
                execution_result.error_message = f"Execution timed out after {timeout} seconds"
                execution_result.completed_at = datetime.now(timezone.utc)
            
            except Exception as e:
                execution_result.success = False
                execution_result.error_message = str(e)
                execution_result.stderr = traceback.format_exc()
                execution_result.completed_at = datetime.now(timezone.utc)
            
            # Store execution result
            self._execution_history[execution_id] = execution_result
            
            self.logger.info(f"File execution completed: {execution_id} (success={execution_result.success})")
            
            return execution_result.dict()
            
        except Exception as e:
            self.logger.error(f"File execution failed: {str(e)}")
            raise MCPError(
                f"File execution failed: {str(e)}",
                error_code="FILE_EXECUTION_ERROR",
                details={"file_path": file_path, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_get_execution_history(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle get execution history requests."""
        limit = request.params.get("limit", 10)
        language_filter = request.params.get("language")
        
        # Filter and sort executions
        executions = list(self._execution_history.values())
        
        if language_filter:
            executions = [e for e in executions if e.language == language_filter]
        
        # Sort by start time (most recent first)
        executions.sort(key=lambda e: e.started_at, reverse=True)
        
        # Limit results
        executions = executions[:limit]
        
        self.logger.info(f"Retrieved {len(executions)} execution history entries")
        
        return [execution.dict() for execution in executions]
    
    # Process Tools
    
    async def _register_process_tools(self) -> None:
        """Register process monitoring tool capabilities."""
        
        # List processes
        list_processes_capability = MCPToolCapability(
            name="list_processes",
            description="List running processes",
            parameters={
                "name_filter": {"type": "string", "required": False, "description": "Filter processes by name"},
                "user_filter": {"type": "string", "required": False, "description": "Filter processes by user"},
                "limit": {"type": "integer", "required": False, "default": 50, "description": "Maximum number of processes"}
            },
            returns={"type": "array", "items": "ProcessInfo"},
            tags=["development", "processes", "monitoring"]
        )
        self.register_tool(list_processes_capability, self._handle_list_processes)
        
        # Get process info
        get_process_capability = MCPToolCapability(
            name="get_process_info",
            description="Get detailed information about a specific process",
            parameters={
                "pid": {"type": "integer", "required": True, "description": "Process ID"}
            },
            returns={"type": "object", "description": "Process information"},
            tags=["development", "processes", "monitoring"]
        )
        self.register_tool(get_process_capability, self._handle_get_process_info)
        
        # Monitor system resources
        monitor_resources_capability = MCPToolCapability(
            name="monitor_system_resources",
            description="Monitor system resource usage",
            parameters={
                "duration": {"type": "integer", "required": False, "default": 10, "description": "Monitoring duration in seconds"}
            },
            returns={"type": "object", "properties": {"cpu_percent": "number", "memory_percent": "number", "disk_usage": "object"}},
            tags=["development", "monitoring", "resources"]
        )
        self.register_tool(monitor_resources_capability, self._handle_monitor_resources)
    
    async def _handle_list_processes(self, request: MCPRequest, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle list processes requests."""
        name_filter = request.params.get("name_filter")
        user_filter = request.params.get("user_filter")
        limit = request.params.get("limit", 50)
        
        try:
            processes = []
            
            for proc in psutil.process_iter(['pid', 'name', 'status', 'username', 'cmdline', 'cwd', 'create_time']):
                try:
                    proc_info = proc.info
                    
                    # Apply filters
                    if name_filter and name_filter.lower() not in proc_info['name'].lower():
                        continue
                    
                    if user_filter and proc_info['username'] != user_filter:
                        continue
                    
                    # Get resource usage
                    try:
                        cpu_percent = proc.cpu_percent()
                        memory_info = proc.memory_info()
                        memory_mb = memory_info.rss / 1024 / 1024
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        cpu_percent = 0.0
                        memory_mb = 0.0
                    
                    process_info = ProcessInfo(
                        pid=proc_info['pid'],
                        name=proc_info['name'],
                        status=proc_info['status'],
                        cpu_percent=cpu_percent,
                        memory_mb=memory_mb,
                        cmdline=proc_info['cmdline'] or [],
                        cwd=proc_info['cwd'],
                        username=proc_info['username'],
                        create_time=datetime.fromtimestamp(proc_info['create_time'], timezone.utc)
                    )
                    
                    processes.append(process_info)
                    
                    if len(processes) >= limit:
                        break
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            
            # Sort by CPU usage (descending)
            processes.sort(key=lambda p: p.cpu_percent, reverse=True)
            
            self.logger.info(f"Listed {len(processes)} processes")
            
            return [proc.dict() for proc in processes]
            
        except Exception as e:
            self.logger.error(f"Failed to list processes: {str(e)}")
            raise MCPError(
                f"Failed to list processes: {str(e)}",
                error_code="PROCESS_LIST_ERROR",
                details={"error": str(e)},
                request_id=request.id
            )
    
    async def _handle_get_process_info(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get process info requests."""
        pid = request.params.get("pid")
        
        if not pid:
            raise MCPError(
                "Process ID is required",
                error_code="MISSING_PID",
                request_id=request.id
            )
        
        try:
            proc = psutil.Process(pid)
            
            # Get process information
            proc_info = proc.as_dict([
                'pid', 'name', 'status', 'username', 'cmdline', 'cwd', 'create_time'
            ])
            
            # Get resource usage
            cpu_percent = proc.cpu_percent()
            memory_info = proc.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            process_info = ProcessInfo(
                pid=proc_info['pid'],
                name=proc_info['name'],
                status=proc_info['status'],
                cpu_percent=cpu_percent,
                memory_mb=memory_mb,
                cmdline=proc_info['cmdline'] or [],
                cwd=proc_info['cwd'],
                username=proc_info['username'],
                create_time=datetime.fromtimestamp(proc_info['create_time'], timezone.utc)
            )
            
            self.logger.info(f"Retrieved process info for PID {pid}")
            
            return process_info.dict()
            
        except psutil.NoSuchProcess:
            raise MCPError(
                f"Process with PID {pid} not found",
                error_code="PROCESS_NOT_FOUND",
                details={"pid": pid},
                request_id=request.id
            )
        except psutil.AccessDenied:
            raise MCPError(
                f"Access denied to process {pid}",
                error_code="PROCESS_ACCESS_DENIED",
                details={"pid": pid},
                request_id=request.id
            )
        except Exception as e:
            self.logger.error(f"Failed to get process info: {str(e)}")
            raise MCPError(
                f"Failed to get process info: {str(e)}",
                error_code="PROCESS_INFO_ERROR",
                details={"pid": pid, "error": str(e)},
                request_id=request.id
            )
    
    async def _handle_monitor_resources(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle monitor system resources requests."""
        duration = min(request.params.get("duration", 10), 60)  # Max 60 seconds
        
        try:
            # Initial measurements
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Monitor for specified duration
            cpu_samples = []
            memory_samples = []
            
            for _ in range(duration):
                cpu_samples.append(psutil.cpu_percent(interval=1))
                memory_samples.append(psutil.virtual_memory().percent)
            
            # Calculate averages
            avg_cpu = sum(cpu_samples) / len(cpu_samples)
            avg_memory = sum(memory_samples) / len(memory_samples)
            
            result = {
                "cpu_percent": avg_cpu,
                "memory_percent": avg_memory,
                "disk_usage": {
                    "total_gb": disk.total / 1024**3,
                    "used_gb": disk.used / 1024**3,
                    "free_gb": disk.free / 1024**3,
                    "percent": (disk.used / disk.total) * 100
                },
                "monitoring_duration": duration,
                "samples_collected": len(cpu_samples)
            }
            
            self.logger.info(f"System resource monitoring completed ({duration}s)")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to monitor resources: {str(e)}")
            raise MCPError(
                f"Failed to monitor resources: {str(e)}",
                error_code="RESOURCE_MONITOR_ERROR",
                details={"duration": duration, "error": str(e)},
                request_id=request.id
            )
    
    # Debugging Tools (simplified implementation)
    
    async def _register_debugging_tools(self) -> None:
        """Register debugging tool capabilities."""
        
        # Start debug session (placeholder)
        debug_session_capability = MCPToolCapability(
            name="start_debug_session",
            description="Start a debugging session for code analysis",
            parameters={
                "language": {"type": "string", "required": True, "enum": ["python"], "description": "Programming language"},
                "code": {"type": "string", "required": True, "description": "Code to debug"}
            },
            returns={"type": "object", "description": "Debug session information"},
            tags=["development", "debugging", "analysis"]
        )
        self.register_tool(debug_session_capability, self._handle_start_debug_session)
    
    async def _handle_start_debug_session(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle start debug session requests (simplified)."""
        language = request.params.get("language")
        code = request.params.get("code")
        
        if not language or not code:
            raise MCPError(
                "Language and code are required",
                error_code="MISSING_DEBUG_PARAMS",
                request_id=request.id
            )
        
        # This is a simplified debug session implementation
        # In a real implementation, this would integrate with actual debuggers
        session_id = f"debug_{int(time.time())}_{id(request)}"
        
        debug_session = DebugSession(
            session_id=session_id,
            language=language,
            target_file="<inline_code>",
            is_active=True
        )
        
        self._debug_sessions[session_id] = debug_session
        
        self.logger.info(f"Started debug session: {session_id}")
        
        return debug_session.dict()
    
    # Profiling Tools
    
    async def _register_profiling_tools(self) -> None:
        """Register profiling tool capabilities."""
        
        # Profile code performance
        profile_code_capability = MCPToolCapability(
            name="profile_code",
            description="Profile code performance and generate analysis",
            parameters={
                "language": {"type": "string", "required": True, "enum": ["python"], "description": "Programming language"},
                "code": {"type": "string", "required": True, "description": "Code to profile"}
            },
            returns={"type": "object", "description": "Profiling results"},
            tags=["development", "profiling", "performance"]
        )
        self.register_tool(profile_code_capability, self._handle_profile_code)
    
    async def _handle_profile_code(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle code profiling requests."""
        language = request.params.get("language")
        code = request.params.get("code")
        
        if not language or not code:
            raise MCPError(
                "Language and code are required",
                error_code="MISSING_PROFILE_PARAMS",
                request_id=request.id
            )
        
        if language != "python":
            raise MCPError(
                "Only Python profiling is currently supported",
                error_code="UNSUPPORTED_PROFILE_LANGUAGE",
                request_id=request.id
            )
        
        try:
            profile_id = f"profile_{int(time.time())}_{id(request)}"
            
            # Create profiling code wrapper
            profiling_code = f"""
import cProfile
import pstats
from io import StringIO
import time

# User code wrapped in profiler
def user_code():
{chr(10).join('    ' + line for line in code.split(chr(10)))}

# Profile the code
pr = cProfile.Profile()
start_time = time.time()
pr.enable()
user_code()
pr.disable()
end_time = time.time()

# Get profiling results
s = StringIO()
ps = pstats.Stats(pr, stream=s)
ps.sort_stats('cumulative')
ps.print_stats(10)  # Top 10 functions

print(f"PROFILE_TOTAL_TIME: {{end_time - start_time}}")
print(f"PROFILE_STATS_START")
print(s.getvalue())
print(f"PROFILE_STATS_END")
"""
            
            # Execute profiling code
            stdout, stderr, return_code = await self._execute_code_safely(
                "python", profiling_code, self.execution_timeout
            )
            
            if return_code != 0:
                raise MCPError(
                    f"Profiling execution failed: {stderr}",
                    error_code="PROFILING_EXECUTION_ERROR",
                    request_id=request.id
                )
            
            # Parse profiling results
            total_time = 0.0
            stats_lines = []
            
            lines = stdout.split('\n')
            capturing_stats = False
            
            for line in lines:
                if line.startswith("PROFILE_TOTAL_TIME:"):
                    total_time = float(line.split(":")[1].strip())
                elif line == "PROFILE_STATS_START":
                    capturing_stats = True
                elif line == "PROFILE_STATS_END":
                    capturing_stats = False
                elif capturing_stats:
                    stats_lines.append(line)
            
            # Create profiling result
            profile_result = ProfileResult(
                profile_id=profile_id,
                language=language,
                code=code,
                total_time=total_time,
                top_functions=[{"analysis": "\n".join(stats_lines)}]
            )
            
            self.logger.info(f"Code profiling completed: {profile_id}")
            
            return profile_result.dict()
            
        except Exception as e:
            self.logger.error(f"Code profiling failed: {str(e)}")
            raise MCPError(
                f"Code profiling failed: {str(e)}",
                error_code="CODE_PROFILING_ERROR",
                details={"language": language, "error": str(e)},
                request_id=request.id
            )
    
    # Test Tools
    
    async def _register_test_tools(self) -> None:
        """Register test execution tool capabilities."""
        
        # Run tests
        run_tests_capability = MCPToolCapability(
            name="run_tests",
            description="Execute tests using various testing frameworks",
            parameters={
                "test_path": {"type": "string", "required": True, "description": "Path to test file or directory"},
                "framework": {"type": "string", "required": False, "default": "auto", "enum": ["auto", "pytest", "unittest", "jest"], "description": "Test framework to use"},
                "coverage": {"type": "boolean", "required": False, "default": False, "description": "Enable code coverage reporting"}
            },
            returns={"type": "object", "description": "Test execution results"},
            tags=["development", "testing", "quality"]
        )
        self.register_tool(run_tests_capability, self._handle_run_tests)
    
    async def _handle_run_tests(self, request: MCPRequest, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle test execution requests."""
        test_path = request.params.get("test_path")
        framework = request.params.get("framework", "auto")
        enable_coverage = request.params.get("coverage", False)
        
        if not test_path:
            raise MCPError(
                "Test path is required",
                error_code="MISSING_TEST_PATH",
                request_id=request.id
            )
        
        try:
            test_id = f"test_{int(time.time())}_{id(request)}"
            
            # Auto-detect framework if needed
            if framework == "auto":
                if test_path.endswith('.py') or any(f.endswith('test.py') for f in os.listdir(os.path.dirname(test_path) or '.')):
                    framework = "pytest"
                elif test_path.endswith('.js') or test_path.endswith('.ts'):
                    framework = "jest"
                else:
                    framework = "pytest"  # Default
            
            # Prepare test command
            if framework == "pytest":
                command = ["python", "-m", "pytest", test_path, "-v"]
                if enable_coverage:
                    command.extend(["--cov=.", "--cov-report=json"])
            elif framework == "unittest":
                command = ["python", "-m", "unittest", "discover", "-s", test_path, "-v"]
            elif framework == "jest":
                command = ["npm", "test", "--", test_path]
            else:
                raise MCPError(
                    f"Unsupported test framework: {framework}",
                    error_code="UNSUPPORTED_TEST_FRAMEWORK",
                    request_id=request.id
                )
            
            # Execute tests
            start_time = time.time()
            stdout, stderr, return_code = await self._run_process(command, self.execution_timeout)
            execution_time = time.time() - start_time
            
            # Parse test results (simplified)
            test_result = TestResult(
                test_id=test_id,
                test_framework=framework,
                test_path=test_path,
                execution_time=execution_time,
                success=(return_code == 0)
            )
            
            # Simple parsing of pytest output
            if framework == "pytest" and stdout:
                lines = stdout.split('\n')
                for line in lines:
                    if " passed" in line or " failed" in line or " error" in line:
                        # Extract test counts (simplified)
                        parts = line.strip().split()
                        for i, part in enumerate(parts):
                            if part == "passed" and i > 0:
                                try:
                                    test_result.passed_tests = int(parts[i-1])
                                except ValueError:
                                    pass
                            elif part == "failed" and i > 0:
                                try:
                                    test_result.failed_tests = int(parts[i-1])
                                except ValueError:
                                    pass
                        break
            
            test_result.total_tests = test_result.passed_tests + test_result.failed_tests
            
            self.logger.info(f"Test execution completed: {test_id} (success={test_result.success})")
            
            return test_result.dict()
            
        except Exception as e:
            self.logger.error(f"Test execution failed: {str(e)}")
            raise MCPError(
                f"Test execution failed: {str(e)}",
                error_code="TEST_EXECUTION_ERROR",
                details={"test_path": test_path, "framework": framework, "error": str(e)},
                request_id=request.id
            )
    
    # Helper methods
    
    async def _execute_code_safely(
        self, 
        language: str, 
        code: str, 
        timeout: int,
        working_directory: str = None,
        environment: Dict[str, str] = None
    ) -> tuple[str, str, int]:
        """Execute code safely with security restrictions."""
        config = self._language_configs[language]
        
        if language == "python":
            # For Python, execute directly with the interpreter
            command = config["command"] + [code]
        elif language == "javascript":
            command = config["command"] + [code]
        elif language == "bash":
            command = config["command"] + [code]
        elif language == "sql":
            # For SQL, create a temporary file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
                f.write(code)
                temp_file = f.name
            try:
                command = ["sqlite3", ":memory:", f".read {temp_file}"]
            finally:
                os.unlink(temp_file)
        else:
            raise MCPError(
                f"Unsupported language: {language}",
                error_code="UNSUPPORTED_LANGUAGE"
            )
        
        return await self._run_process(command, timeout, working_directory, environment)
    
    async def _run_process(
        self, 
        command: List[str], 
        timeout: int,
        working_directory: str = None,
        environment: Dict[str, str] = None
    ) -> tuple[str, str, int]:
        """Run a process with timeout and resource limits."""
        try:
            # Prepare environment
            env = os.environ.copy()
            if environment:
                env.update(environment)
            
            # Run process
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_directory,
                env=env
            )
            
            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), 
                    timeout=timeout
                )
                
                return (
                    stdout.decode('utf-8', errors='replace'),
                    stderr.decode('utf-8', errors='replace'),
                    process.returncode
                )
                
            except asyncio.TimeoutError:
                # Kill the process if it times out
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
                raise asyncio.TimeoutError()
                
        except Exception as e:
            raise MCPError(
                f"Process execution failed: {str(e)}",
                error_code="PROCESS_EXECUTION_ERROR",
                details={"command": command, "error": str(e)}
            )
    
    def _check_code_security(self, code: str, language: str) -> List[str]:
        """Check code for security violations."""
        violations = []
        
        if language == "python":
            # Check for dangerous imports and functions
            for restricted in self._restricted_imports:
                if f"import {restricted}" in code or f"from {restricted}" in code:
                    violations.append(f"Restricted import: {restricted}")
                if f"{restricted}(" in code:
                    violations.append(f"Restricted function call: {restricted}")
        
        elif language == "bash":
            # Check for dangerous bash commands
            dangerous_commands = ["rm -rf", "chmod", "chown", "sudo", "su"]
            for cmd in dangerous_commands:
                if cmd in code:
                    violations.append(f"Dangerous bash command: {cmd}")
        
        return violations
    
    async def _verify_language_availability(self) -> None:
        """Verify that required language interpreters are available."""
        for language in self.allowed_languages:
            config = self._language_configs.get(language)
            if config:
                try:
                    # Test if interpreter is available
                    command = config["command"][0]
                    await self._run_process([command, "--version"], 5)
                    self.logger.info(f"Language {language} is available")
                except Exception as e:
                    self.logger.warning(f"Language {language} may not be available: {str(e)}")


# Export the server class
__all__ = ["DevelopmentToolsServer"]