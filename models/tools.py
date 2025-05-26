# models/tools.py
"""
Tool definitions using Pydantic models for AI agents to use external capabilities.
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Literal, Union
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod
import asyncio
import subprocess
import json
import os
from pathlib import Path

class ToolStatus(str, Enum):
    """Tool operational status"""
    AVAILABLE = "available"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"

class ToolResult(BaseModel):
    """Result from tool execution"""
    success: bool = Field(..., description="Was tool execution successful")
    output: str = Field(..., description="Tool output/response")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    execution_time_ms: int = Field(..., ge=0, description="Execution time in milliseconds")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timestamp: datetime = Field(default_factory=datetime.now, description="Execution timestamp")

class ToolCapability(BaseModel):
    """Defines what a tool can do"""
    name: str = Field(..., description="Capability name")
    description: str = Field(..., description="What this capability does")
    input_schema: Dict[str, Any] = Field(..., description="JSON schema for inputs")
    output_schema: Dict[str, Any] = Field(..., description="JSON schema for outputs")
    required_permissions: List[str] = Field(default_factory=list, description="Required permissions")

class ToolConfig(BaseModel):
    """Configuration for a tool"""
    name: str = Field(..., description="Tool name")
    version: str = Field(..., description="Tool version")
    description: str = Field(..., description="Tool description")
    capabilities: List[ToolCapability] = Field(..., description="Tool capabilities")
    executable_path: Optional[str] = Field(None, description="Path to executable if applicable")
    environment_variables: Dict[str, str] = Field(default_factory=dict, description="Required env vars")
    timeout_seconds: int = Field(default=300, gt=0, description="Default timeout")
    retry_attempts: int = Field(default=3, ge=0, description="Number of retry attempts")

class Tool(BaseModel, ABC):
    """Base class for tools that agents can use"""
    config: ToolConfig = Field(..., description="Tool configuration")
    status: ToolStatus = Field(default=ToolStatus.AVAILABLE, description="Current status")
    last_used: Optional[datetime] = Field(None, description="Last execution time")
    usage_count: int = Field(default=0, description="Number of times used")
    error_count: int = Field(default=0, description="Number of errors")
    
    class Config:
        arbitrary_types_allowed = True
    
    @abstractmethod
    async def execute(self, input_data: Dict[str, Any]) -> ToolResult:
        """Execute the tool with given input"""
        pass
    
    @abstractmethod
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Validate input data against tool requirements"""
        pass
    
    def get_capabilities(self) -> List[ToolCapability]:
        """Get tool capabilities"""
        return self.config.capabilities
    
    async def health_check(self) -> bool:
        """Check if tool is healthy and available"""
        return self.status == ToolStatus.AVAILABLE

# Claude Code Tool Implementation

class ClaudeCodeInput(BaseModel):
    """Input for Claude Code tool execution"""
    prompt: str = Field(..., description="The prompt/task for Claude Code to execute")
    directory: str = Field(..., description="Working directory for Claude Code execution")
    files_to_include: List[str] = Field(default_factory=list, description="Specific files to include in context")
    timeout_seconds: int = Field(default=300, gt=0, le=1800, description="Execution timeout (max 30 minutes)")
    additional_args: List[str] = Field(default_factory=list, description="Additional CLI arguments")
    environment_vars: Dict[str, str] = Field(default_factory=dict, description="Additional environment variables")

class ClaudeCodeOutput(BaseModel):
    """Output from Claude Code execution"""
    response: str = Field(..., description="Claude Code's response/output")
    files_modified: List[str] = Field(default_factory=list, description="Files that were modified")
    files_created: List[str] = Field(default_factory=list, description="Files that were created")
    exit_code: int = Field(..., description="Process exit code")
    stderr: Optional[str] = Field(None, description="Standard error output if any")
    working_directory: str = Field(..., description="Directory where execution occurred")

class ClaudeCodeTool(Tool):
    """
    Tool for executing Claude Code CLI commands to perform development work
    
    This tool allows AI agents to invoke Claude Code with specific prompts
    to perform actual development tasks in designated directories.
    """
    
    def __init__(self, claude_code_path: Optional[str] = None):
        """Initialize Claude Code tool"""
        
        # Detect Claude Code installation
        if claude_code_path is None:
            claude_code_path = self._detect_claude_code_path()
        
        config = ToolConfig(
            name="ClaudeCode",
            version="1.0.0",
            description="Execute Claude Code CLI to perform development tasks in specified directories",
            capabilities=[
                ToolCapability(
                    name="code_generation",
                    description="Generate code files and implementations",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Development task description"},
                            "directory": {"type": "string", "description": "Target directory"},
                            "files_to_include": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["prompt", "directory"]
                    },
                    output_schema={
                        "type": "object", 
                        "properties": {
                            "response": {"type": "string"},
                            "files_modified": {"type": "array", "items": {"type": "string"}},
                            "files_created": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                    required_permissions=["file_read", "file_write", "process_execution"]
                ),
                ToolCapability(
                    name="code_review",
                    description="Review and analyze existing code",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Review request"},
                            "directory": {"type": "string", "description": "Code directory"},
                            "files_to_include": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["prompt", "directory"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "response": {"type": "string", "description": "Review findings"},
                            "files_analyzed": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                    required_permissions=["file_read"]
                ),
                ToolCapability(
                    name="refactoring",
                    description="Refactor and improve existing code",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Refactoring requirements"},
                            "directory": {"type": "string", "description": "Code directory"},
                            "files_to_include": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["prompt", "directory"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "response": {"type": "string"},
                            "files_modified": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                    required_permissions=["file_read", "file_write"]
                ),
                ToolCapability(
                    name="testing",
                    description="Generate tests and run test suites",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Testing requirements"},
                            "directory": {"type": "string", "description": "Project directory"},
                            "test_framework": {"type": "string", "description": "Preferred test framework"}
                        },
                        "required": ["prompt", "directory"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "response": {"type": "string"},
                            "test_files_created": {"type": "array", "items": {"type": "string"}},
                            "test_results": {"type": "string"}
                        }
                    },
                    required_permissions=["file_read", "file_write", "process_execution"]
                ),
                ToolCapability(
                    name="documentation",
                    description="Generate and update documentation",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Documentation requirements"},
                            "directory": {"type": "string", "description": "Project directory"},
                            "doc_format": {"type": "string", "enum": ["markdown", "rst", "html"]}
                        },
                        "required": ["prompt", "directory"]
                    },
                    output_schema={
                        "type": "object",
                        "properties": {
                            "response": {"type": "string"},
                            "doc_files_created": {"type": "array", "items": {"type": "string"}},
                            "doc_files_modified": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                    required_permissions=["file_read", "file_write"]
                )
            ],
            executable_path=claude_code_path,
            timeout_seconds=300,
            retry_attempts=2
        )
        
        super().__init__(config=config)
        self.claude_code_path = claude_code_path
    
    def _detect_claude_code_path(self) -> str:
        """Detect Claude Code CLI installation path"""
        
        # Common installation paths
        possible_paths = [
            "claude-code",  # If in PATH
            "/usr/local/bin/claude-code",
            "/opt/homebrew/bin/claude-code",
            os.path.expanduser("~/.local/bin/claude-code"),
            "npx @anthropic-ai/claude-code"  # npm global install
        ]
        
        for path in possible_paths:
            try:
                # Test if command exists
                result = subprocess.run(
                    [path.split()[0] if " " in path else path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    return path
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue
        
        # Default fallback
        return "claude-code"
    
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Validate Claude Code input data"""
        try:
            ClaudeCodeInput(**input_data)
            
            # Additional validation
            directory = input_data.get("directory", "")
            if not directory:
                return False
            
            # Check if directory exists or can be created
            dir_path = Path(directory).expanduser().resolve()
            if not dir_path.exists():
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                except OSError:
                    return False
            
            return True
            
        except Exception:
            return False
    
    async def execute(self, input_data: Dict[str, Any]) -> ToolResult:
        """Execute Claude Code with the given prompt and directory"""
        start_time = datetime.now()
        
        try:
            # Validate input
            if not self.validate_input(input_data):
                raise ValueError("Invalid input data for Claude Code tool")
            
            # Parse input
            claude_input = ClaudeCodeInput(**input_data)
            
            # Update tool status
            self.status = ToolStatus.BUSY
            
            # Prepare command
            cmd = self._build_command(claude_input)
            
            # Execute Claude Code
            result = await self._execute_command(cmd, claude_input)
            
            # Parse output
            claude_output = self._parse_output(result, claude_input)
            
            # Update usage stats
            self.usage_count += 1
            self.last_used = datetime.now()
            self.status = ToolStatus.AVAILABLE
            
            execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
            
            return ToolResult(
                success=claude_output.exit_code == 0,
                output=claude_output.response,
                execution_time_ms=execution_time,
                metadata={
                    "files_modified": claude_output.files_modified,
                    "files_created": claude_output.files_created,
                    "working_directory": claude_output.working_directory,
                    "exit_code": claude_output.exit_code,
                    "stderr": claude_output.stderr
                }
            )
            
        except Exception as e:
            self.error_count += 1
            self.status = ToolStatus.AVAILABLE
            execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
            
            return ToolResult(
                success=False,
                output="",
                error_message=str(e),
                execution_time_ms=execution_time,
                metadata={"error_type": type(e).__name__}
            )
    
    def _build_command(self, claude_input: ClaudeCodeInput) -> List[str]:
        """Build the Claude Code command"""
        
        cmd = []
        
        # Handle different command formats
        if " " in self.claude_code_path:
            # Handle commands like "npx @anthropic-ai/claude-code"
            cmd.extend(self.claude_code_path.split())
        else:
            cmd.append(self.claude_code_path)
        
        # Add the prompt
        cmd.extend(["--prompt", claude_input.prompt])
        
        # Add directory if different from current
        current_dir = os.getcwd()
        target_dir = str(Path(claude_input.directory).expanduser().resolve())
        if target_dir != current_dir:
            cmd.extend(["--directory", target_dir])
        
        # Add specific files if requested
        for file_path in claude_input.files_to_include:
            cmd.extend(["--file", file_path])
        
        # Add additional arguments
        cmd.extend(claude_input.additional_args)
        
        return cmd
    
    async def _execute_command(self, cmd: List[str], claude_input: ClaudeCodeInput) -> subprocess.CompletedProcess:
        """Execute the Claude Code command asynchronously"""
        
        # Prepare environment
        env = os.environ.copy()
        env.update(claude_input.environment_vars)
        
        # Change to target directory
        cwd = str(Path(claude_input.directory).expanduser().resolve())
        
        # Execute command
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=claude_input.timeout_seconds
            )
            
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=process.returncode,
                stdout=stdout.decode('utf-8', errors='replace'),
                stderr=stderr.decode('utf-8', errors='replace')
            )
            
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError(f"Claude Code execution timed out after {claude_input.timeout_seconds} seconds")
    
    def _parse_output(self, result: subprocess.CompletedProcess, claude_input: ClaudeCodeInput) -> ClaudeCodeOutput:
        """Parse the output from Claude Code execution"""
        
        # Extract file changes from output (this would need to be adapted based on actual Claude Code output format)
        files_modified = []
        files_created = []
        
        # Basic parsing - in reality, this would need to parse Claude Code's specific output format
        stdout = result.stdout
        
        # Look for file modification indicators in output
        # This is a simplified example - actual implementation would depend on Claude Code's output format
        import re
        
        # Extract file operations from output
        modified_pattern = r"Modified:\s+(.+)"
        created_pattern = r"Created:\s+(.+)"
        
        files_modified = re.findall(modified_pattern, stdout)
        files_created = re.findall(created_pattern, stdout)
        
        return ClaudeCodeOutput(
            response=stdout,
            files_modified=files_modified,
            files_created=files_created,
            exit_code=result.returncode,
            stderr=result.stderr if result.stderr else None,
            working_directory=claude_input.directory
        )

# Tool Registry

class ToolRegistry(BaseModel):
    """Registry for managing available tools"""
    tools: Dict[str, Tool] = Field(default_factory=dict, description="Available tools")
    
    def register_tool(self, tool: Tool) -> None:
        """Register a new tool"""
        self.tools[tool.config.name] = tool
    
    def get_tool(self, name: str) -> Optional[Tool]:
        """Get tool by name"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[str]:
        """List all available tool names"""
        return list(self.tools.keys())
    
    def get_tools_with_capability(self, capability_name: str) -> List[Tool]:
        """Get all tools that have a specific capability"""
        matching_tools = []
        for tool in self.tools.values():
            for capability in tool.get_capabilities():
                if capability.name == capability_name:
                    matching_tools.append(tool)
                    break
        return matching_tools

# Example usage in HG Wells agent

class HGWellsWithClaudeCode(HGWellsAgent):
    """Extended HG Wells agent with Claude Code tool integration"""
    
    def __init__(self, anthropic_client, notion_scout_agent: Optional[Agent] = None, claude_code_path: Optional[str] = None):
        super().__init__(anthropic_client, notion_scout_agent)
        
        # Initialize tool registry
        self.tool_registry = ToolRegistry()
        
        # Register Claude Code tool
        claude_code_tool = ClaudeCodeTool(claude_code_path)
        self.tool_registry.register_tool(claude_code_tool)
    
    async def execute_development_task(self, task_description: str, target_directory: str, files_to_include: List[str] = None) -> ToolResult:
        """Execute a development task using Claude Code"""
        
        claude_code_tool = self.tool_registry.get_tool("ClaudeCode")
        if not claude_code_tool:
            raise RuntimeError("Claude Code tool not available")
        
        input_data = {
            "prompt": task_description,
            "directory": target_directory,
            "files_to_include": files_to_include or [],
            "timeout_seconds": 600  # 10 minutes for development tasks
        }
        
        return await claude_code_tool.execute(input_data)
    
    async def process_request(self, invocation: HGWellsInvocation) -> HGWellsResponse:
        """Enhanced process_request that can use Claude Code for implementation tasks"""
        
        # Check if this is a request that needs actual code implementation
        if self._requires_code_implementation(invocation.user_input):
            # Extract development requirements
            dev_requirements = self._extract_development_requirements(invocation)
            
            # Execute development task with Claude Code
            for requirement in dev_requirements:
                try:
                    result = await self.execute_development_task(
                        task_description=requirement.description,
                        target_directory=requirement.directory,
                        files_to_include=requirement.files
                    )
                    
                    # Incorporate development results into operational analysis
                    invocation.context["development_results"] = result.metadata
                    
                except Exception as e:
                    # Handle development task failures
                    invocation.context["development_errors"] = str(e)
        
        # Continue with normal operational analysis
        return await super().process_request(invocation)
    
    def _requires_code_implementation(self, request: str) -> bool:
        """Determine if request requires actual code implementation"""
        implementation_keywords = [
            'implement', 'code', 'build', 'create', 'develop', 'write',
            'generate', 'scaffold', 'prototype', 'refactor', 'fix'
        ]
        return any(keyword in request.lower() for keyword in implementation_keywords)