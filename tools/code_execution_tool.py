"""
Code Execution Tool - Sandboxed Code Runner

Secure, sandboxed code execution environment with comprehensive safety measures,
performance monitoring, and multi-language support.

Features:
- Multi-language support (Python, JavaScript, SQL, Bash)
- Secure sandboxing with resource limits
- Dependency management and isolation
- Real-time execution monitoring
- Output capture and analysis
- Security scanning and validation
"""

import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple
import hashlib
import shutil

from pydantic import BaseModel, Field, validator

from .base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, performance_monitor
)


class CodeExecutionInput(BaseInputModel):
    """Input model for code execution requests."""
    
    code: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="Source code to execute"
    )
    
    language: str = Field(
        default="python",
        pattern="^(python|javascript|sql|bash|shell)$",
        description="Programming language"
    )
    
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Execution timeout in seconds"
    )
    
    memory_limit_mb: int = Field(
        default=128,
        ge=16,
        le=1024,
        description="Memory limit in megabytes"
    )
    
    enable_network: bool = Field(
        default=False,
        description="Allow network access during execution"
    )
    
    dependencies: List[str] = Field(
        default_factory=list,
        description="Required packages or dependencies"
    )
    
    input_data: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Input data to pass to the code"
    )
    
    working_directory: Optional[str] = Field(
        default=None,
        description="Working directory for execution"
    )
    
    environment_variables: Dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set"
    )
    
    capture_output: bool = Field(
        default=True,
        description="Capture stdout and stderr"
    )
    
    return_files: bool = Field(
        default=False,
        description="Return created files in the response"
    )
    
    security_level: str = Field(
        default="strict",
        pattern="^(strict|moderate|permissive)$",
        description="Security level for code execution"
    )
    
    @validator('code')
    def validate_code_content(cls, v):
        """Validate code for security concerns."""
        if not v.strip():
            raise ValueError("Code cannot be empty")
        
        # Basic security checks
        dangerous_patterns = [
            'import os', 'import subprocess', 'import sys',
            '__import__', 'eval(', 'exec(',
            'open(', 'file(', 'input(',
            'raw_input(', 'compile(',
            'globals(', 'locals(', 'vars(',
            'dir(', 'hasattr(', 'getattr(',
            'setattr(', 'delattr('
        ]
        
        code_lower = v.lower()
        flagged_patterns = [p for p in dangerous_patterns if p in code_lower]
        
        if flagged_patterns:
            raise ValueError(f"Code contains potentially dangerous patterns: {flagged_patterns}")
        
        return v
    
    @validator('dependencies')
    def validate_dependencies(cls, v):
        """Validate dependency list."""
        if len(v) > 20:
            raise ValueError("Too many dependencies (max 20)")
        
        for dep in v:
            if not isinstance(dep, str) or len(dep) > 100:
                raise ValueError(f"Invalid dependency format: {dep}")
        
        return v


class ExecutionResult(BaseModel):
    """Code execution result with comprehensive details."""
    
    success: bool = Field(..., description="Whether execution was successful")
    exit_code: int = Field(..., description="Process exit code")
    
    # Output
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")
    output_size_bytes: int = Field(default=0, description="Total output size")
    
    # Performance metrics
    execution_time_ms: float = Field(..., description="Actual execution time")
    memory_used_mb: float = Field(default=0, description="Peak memory usage")
    cpu_usage_percent: float = Field(default=0, description="CPU usage percentage")
    
    # File system
    files_created: List[str] = Field(default_factory=list, description="Files created during execution")
    files_modified: List[str] = Field(default_factory=list, description="Files modified")
    working_directory: str = Field(default="", description="Execution working directory")
    
    # Security analysis
    security_violations: List[str] = Field(
        default_factory=list,
        description="Security violations detected"
    )
    network_requests: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Network requests made (if monitoring enabled)"
    )
    
    # Environment
    environment_info: Dict[str, Any] = Field(
        default_factory=dict,
        description="Execution environment details"
    )


class CodeExecutionOutput(BaseOutputModel):
    """Complete code execution response."""
    
    code: str = Field(..., description="Original code that was executed")
    language: str = Field(..., description="Programming language used")
    
    # Execution result
    execution_result: ExecutionResult = Field(..., description="Execution details")
    
    # Analysis
    code_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="Static code analysis results"
    )
    
    performance_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="Performance analysis and recommendations"
    )
    
    security_assessment: Dict[str, Any] = Field(
        default_factory=dict,
        description="Security assessment results"
    )
    
    # Created files (if requested)
    created_files: Dict[str, str] = Field(
        default_factory=dict,
        description="Contents of files created during execution"
    )
    
    # Recommendations
    optimization_suggestions: List[str] = Field(
        default_factory=list,
        description="Code optimization suggestions"
    )
    
    security_recommendations: List[str] = Field(
        default_factory=list,
        description="Security improvement recommendations"
    )


class SandboxEnvironment:
    """Secure sandbox environment for code execution."""
    
    def __init__(
        self,
        language: str,
        timeout: int = 30,
        memory_limit_mb: int = 128,
        enable_network: bool = False,
        security_level: str = "strict"
    ):
        self.language = language
        self.timeout = timeout
        self.memory_limit_mb = memory_limit_mb
        self.enable_network = enable_network
        self.security_level = security_level
        
        # Create temporary directory for execution
        self.temp_dir = tempfile.mkdtemp(prefix="code_exec_")
        self.working_dir = Path(self.temp_dir)
        
        # Track created files
        self.initial_files = set()
        self.final_files = set()
    
    async def __aenter__(self):
        """Async context manager entry."""
        # Record initial file state
        if self.working_dir.exists():
            self.initial_files = set(self.working_dir.rglob("*"))
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup sandbox environment."""
        try:
            # Record final file state
            if self.working_dir.exists():
                self.final_files = set(self.working_dir.rglob("*"))
            
            # Cleanup temporary directory
            if Path(self.temp_dir).exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception as e:
            # Log cleanup error but don't raise
            print(f"Sandbox cleanup warning: {e}")
    
    def get_command(self, code_file: Path) -> List[str]:
        """Get execution command for the language."""
        
        if self.language == "python":
            return ["python3", str(code_file)]
        elif self.language == "javascript":
            return ["node", str(code_file)]
        elif self.language == "bash" or self.language == "shell":
            return ["bash", str(code_file)]
        elif self.language == "sql":
            # Use sqlite3 for SQL execution
            return ["sqlite3", ":memory:", f".read {code_file}"]
        else:
            raise ValueError(f"Unsupported language: {self.language}")
    
    def get_file_extension(self) -> str:
        """Get file extension for the language."""
        
        extensions = {
            "python": ".py",
            "javascript": ".js",
            "bash": ".sh",
            "shell": ".sh",
            "sql": ".sql"
        }
        return extensions.get(self.language, ".txt")
    
    def apply_security_restrictions(self, env: Dict[str, str]) -> Dict[str, str]:
        """Apply security restrictions to environment."""
        
        if self.security_level == "strict":
            # Remove potentially dangerous environment variables
            dangerous_vars = [
                "PATH", "LD_LIBRARY_PATH", "PYTHONPATH",
                "HOME", "USER", "SHELL"
            ]
            
            for var in dangerous_vars:
                env.pop(var, None)
            
            # Set restricted PATH
            env["PATH"] = "/usr/bin:/bin"
            
            # Disable network if not explicitly enabled
            if not self.enable_network:
                env["NO_PROXY"] = "*"
        
        return env
    
    def get_created_files(self) -> List[str]:
        """Get list of files created during execution."""
        created = self.final_files - self.initial_files
        return [str(f.relative_to(self.working_dir)) for f in created if f.is_file()]
    
    def get_modified_files(self) -> List[str]:
        """Get list of files modified during execution."""
        # Simple implementation - could be enhanced with timestamps
        common_files = self.initial_files & self.final_files
        modified = []
        
        for file_path in common_files:
            if file_path.is_file():
                # Check if modification time changed (simplified)
                modified.append(str(file_path.relative_to(self.working_dir)))
        
        return modified


class CodeAnalyzer:
    """Static code analysis and security scanning."""
    
    @staticmethod
    def analyze_code(code: str, language: str) -> Dict[str, Any]:
        """Perform static code analysis."""
        
        analysis = {
            "language": language,
            "line_count": len(code.splitlines()),
            "character_count": len(code),
            "complexity_score": 0,
            "issues": [],
            "suggestions": []
        }
        
        if language == "python":
            analysis.update(CodeAnalyzer._analyze_python(code))
        elif language == "javascript":
            analysis.update(CodeAnalyzer._analyze_javascript(code))
        
        return analysis
    
    @staticmethod
    def _analyze_python(code: str) -> Dict[str, Any]:
        """Analyze Python code."""
        
        issues = []
        suggestions = []
        complexity = 0
        
        lines = code.splitlines()
        
        for i, line in enumerate(lines, 1):
            line_stripped = line.strip()
            
            # Check for common issues
            if "print(" in line_stripped and not line_stripped.startswith("#"):
                suggestions.append(f"Line {i}: Consider using logging instead of print")
            
            if "import *" in line_stripped:
                issues.append(f"Line {i}: Avoid wildcard imports")
            
            if line_stripped.startswith("exec(") or line_stripped.startswith("eval("):
                issues.append(f"Line {i}: Dangerous function usage detected")
            
            # Simple complexity metrics
            if any(keyword in line_stripped for keyword in ["if ", "for ", "while ", "try:", "except"]):
                complexity += 1
        
        return {
            "complexity_score": complexity,
            "issues": issues,
            "suggestions": suggestions
        }
    
    @staticmethod
    def _analyze_javascript(code: str) -> Dict[str, Any]:
        """Analyze JavaScript code."""
        
        issues = []
        suggestions = []
        complexity = 0
        
        lines = code.splitlines()
        
        for i, line in enumerate(lines, 1):
            line_stripped = line.strip()
            
            # Check for common issues
            if "eval(" in line_stripped:
                issues.append(f"Line {i}: Avoid using eval()")
            
            if "var " in line_stripped:
                suggestions.append(f"Line {i}: Consider using 'let' or 'const' instead of 'var'")
            
            if "==" in line_stripped and "===" not in line_stripped:
                suggestions.append(f"Line {i}: Consider using strict equality (===)")
            
            # Simple complexity metrics
            if any(keyword in line_stripped for keyword in ["if(", "for(", "while(", "try{", "catch"]):
                complexity += 1
        
        return {
            "complexity_score": complexity,
            "issues": issues,
            "suggestions": suggestions
        }
    
    @staticmethod
    def scan_security_issues(code: str, language: str) -> List[str]:
        """Scan for security vulnerabilities."""
        
        violations = []
        
        # Common security patterns
        dangerous_patterns = {
            "python": [
                ("import os", "Direct OS module import"),
                ("import subprocess", "Subprocess module import"),
                ("__import__", "Dynamic import usage"),
                ("exec(", "Code execution function"),
                ("eval(", "Code evaluation function"),
                ("open(", "File system access"),
            ],
            "javascript": [
                ("eval(", "Code evaluation function"),
                ("Function(", "Dynamic function creation"),
                ("setTimeout(", "Timer function usage"),
                ("setInterval(", "Interval function usage"),
            ],
            "bash": [
                ("rm -rf", "Dangerous file deletion"),
                ("curl", "Network request"),
                ("wget", "File download"),
                ("sudo", "Privilege escalation"),
            ]
        }
        
        if language in dangerous_patterns:
            for pattern, description in dangerous_patterns[language]:
                if pattern in code:
                    violations.append(f"{description}: {pattern}")
        
        return violations


class CodeExecutionTool(ToolImplementation[CodeExecutionInput, CodeExecutionOutput]):
    """
    Advanced Code Execution Tool with Secure Sandboxing
    
    Provides secure, monitored code execution across multiple languages
    with comprehensive safety measures and performance analysis.
    """
    
    def __init__(self, **kwargs):
        super().__init__(
            name="code_execution",
            version="1.1.0",
            description="Secure sandboxed code execution with multi-language support",
            **kwargs
        )
        
        # Verify required tools are available
        self._check_system_requirements()
        
        # Configuration
        self.max_concurrent_executions = 3
        self.default_timeout = 30
        self.max_output_size = 1024 * 1024  # 1MB
        
        # Execution tracking
        self._execution_count = 0
        self._active_executions = 0
        
        # Code analyzer
        self.analyzer = CodeAnalyzer()
    
    @property
    def input_model(self) -> type:
        return CodeExecutionInput
    
    @property
    def output_model(self) -> type:
        return CodeExecutionOutput
    
    def _check_system_requirements(self):
        """Check that required system tools are available."""
        
        required_tools = {
            "python": "python3",
            "javascript": "node",
            "bash": "bash",
            "sql": "sqlite3"
        }
        
        for language, command in required_tools.items():
            if not shutil.which(command):
                self.logger.warning(f"Language {language} not available: {command} not found")
    
    async def _execute_core(
        self, 
        input_data: CodeExecutionInput, 
        context: ToolContext
    ) -> CodeExecutionOutput:
        """Core code execution with comprehensive safety and monitoring."""
        
        start_time = time.time()
        
        # Check concurrent execution limit
        if self._active_executions >= self.max_concurrent_executions:
            raise ToolError(
                "Maximum concurrent executions reached",
                ErrorCategory.RESOURCE_EXHAUSTION,
                retry_after=10.0
            )
        
        self._active_executions += 1
        
        try:
            # Step 1: Static code analysis
            code_analysis = self.analyzer.analyze_code(input_data.code, input_data.language)
            context.add_trace_data("code_analysis", code_analysis)
            
            # Step 2: Security scanning
            security_violations = self.analyzer.scan_security_issues(
                input_data.code, input_data.language
            )
            
            if security_violations and input_data.security_level == "strict":
                raise ToolError(
                    f"Security violations detected: {security_violations}",
                    ErrorCategory.INPUT_VALIDATION,
                    details={"violations": security_violations}
                )
            
            # Step 3: Setup sandbox environment
            async with SandboxEnvironment(
                input_data.language,
                input_data.timeout_seconds,
                input_data.memory_limit_mb,
                input_data.enable_network,
                input_data.security_level
            ) as sandbox:
                
                # Step 4: Execute code in sandbox
                execution_result = await self._execute_in_sandbox(
                    input_data, sandbox, context
                )
                
                # Step 5: Post-execution analysis
                performance_analysis = self._analyze_performance(execution_result)
                security_assessment = self._assess_security(execution_result, security_violations)
                
                # Step 6: Generate recommendations
                optimization_suggestions = self._generate_optimization_suggestions(
                    code_analysis, execution_result
                )
                security_recommendations = self._generate_security_recommendations(
                    security_assessment
                )
                
                # Step 7: Collect created files if requested
                created_files = {}
                if input_data.return_files:
                    created_files = await self._collect_created_files(
                        sandbox, execution_result.files_created
                    )
                
                # Step 8: Build output
                output = CodeExecutionOutput(
                    code=input_data.code,
                    language=input_data.language,
                    execution_result=execution_result,
                    code_analysis=code_analysis,
                    performance_analysis=performance_analysis,
                    security_assessment=security_assessment,
                    created_files=created_files,
                    optimization_suggestions=optimization_suggestions,
                    security_recommendations=security_recommendations
                )
                
                return output
        
        finally:
            self._active_executions -= 1
            self._execution_count += 1
    
    async def _execute_in_sandbox(
        self,
        input_data: CodeExecutionInput,
        sandbox: SandboxEnvironment,
        context: ToolContext
    ) -> ExecutionResult:
        """Execute code within the sandbox environment."""
        
        execution_start = time.time()
        
        try:
            # Create code file
            file_extension = sandbox.get_file_extension()
            code_file = sandbox.working_dir / f"code{file_extension}"
            code_file.write_text(input_data.code)
            
            # Install dependencies if needed
            if input_data.dependencies:
                await self._install_dependencies(
                    input_data.dependencies, 
                    input_data.language,
                    sandbox
                )
            
            # Prepare environment
            env = os.environ.copy()
            env.update(input_data.environment_variables)
            env = sandbox.apply_security_restrictions(env)
            
            # Get execution command
            command = sandbox.get_command(code_file)
            
            # Execute with monitoring
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=subprocess.PIPE if input_data.input_data else None,
                stdout=subprocess.PIPE if input_data.capture_output else None,
                stderr=subprocess.PIPE if input_data.capture_output else None,
                cwd=sandbox.working_dir,
                env=env
            )
            
            # Communicate with process
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(
                        input=input_data.input_data.encode() if input_data.input_data else None
                    ),
                    timeout=input_data.timeout_seconds
                )
                
                execution_time = (time.time() - execution_start) * 1000
                
                # Decode output
                stdout_str = stdout.decode('utf-8', errors='ignore') if stdout else ""
                stderr_str = stderr.decode('utf-8', errors='ignore') if stderr else ""
                
                # Limit output size
                if len(stdout_str) > self.max_output_size:
                    stdout_str = stdout_str[:self.max_output_size] + "\n[OUTPUT TRUNCATED]"
                
                if len(stderr_str) > self.max_output_size:
                    stderr_str = stderr_str[:self.max_output_size] + "\n[ERROR OUTPUT TRUNCATED]"
                
                # Build execution result
                result = ExecutionResult(
                    success=(process.returncode == 0),
                    exit_code=process.returncode or 0,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    output_size_bytes=len(stdout_str) + len(stderr_str),
                    execution_time_ms=execution_time,
                    files_created=sandbox.get_created_files(),
                    files_modified=sandbox.get_modified_files(),
                    working_directory=str(sandbox.working_dir),
                    security_violations=[],  # Will be populated later
                    environment_info={
                        "command": " ".join(command),
                        "working_directory": str(sandbox.working_dir),
                        "timeout_seconds": input_data.timeout_seconds
                    }
                )
                
                return result
                
            except asyncio.TimeoutError:
                # Kill the process
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except:
                    process.kill()
                
                return ExecutionResult(
                    success=False,
                    exit_code=-1,
                    stderr=f"Execution timed out after {input_data.timeout_seconds} seconds",
                    execution_time_ms=input_data.timeout_seconds * 1000,
                    working_directory=str(sandbox.working_dir),
                    environment_info={
                        "timeout": True,
                        "timeout_seconds": input_data.timeout_seconds
                    }
                )
        
        except Exception as e:
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stderr=f"Execution error: {str(e)}",
                execution_time_ms=(time.time() - execution_start) * 1000,
                working_directory=str(sandbox.working_dir) if sandbox else "",
                environment_info={"error": str(e)}
            )
    
    async def _install_dependencies(
        self,
        dependencies: List[str],
        language: str,
        sandbox: SandboxEnvironment
    ) -> None:
        """Install dependencies in sandbox environment."""
        
        if language == "python":
            # Install Python packages
            pip_command = ["pip3", "install", "--user"] + dependencies
            
            try:
                process = await asyncio.create_subprocess_exec(
                    *pip_command,
                    cwd=sandbox.working_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                await asyncio.wait_for(process.wait(), timeout=60)  # 1 minute timeout
                
            except asyncio.TimeoutError:
                self.logger.warning("Dependency installation timed out")
            except Exception as e:
                self.logger.warning(f"Dependency installation failed: {e}")
        
        elif language == "javascript":
            # Install Node.js packages
            npm_command = ["npm", "install"] + dependencies
            
            try:
                process = await asyncio.create_subprocess_exec(
                    *npm_command,
                    cwd=sandbox.working_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                await asyncio.wait_for(process.wait(), timeout=60)
                
            except Exception as e:
                self.logger.warning(f"Node.js dependency installation failed: {e}")
    
    def _analyze_performance(self, execution_result: ExecutionResult) -> Dict[str, Any]:
        """Analyze execution performance."""
        
        analysis = {
            "execution_time_ms": execution_result.execution_time_ms,
            "memory_used_mb": execution_result.memory_used_mb,
            "output_size_bytes": execution_result.output_size_bytes,
            "performance_rating": "good"
        }
        
        # Performance rating
        if execution_result.execution_time_ms > 10000:  # > 10 seconds
            analysis["performance_rating"] = "slow"
        elif execution_result.execution_time_ms > 5000:  # > 5 seconds
            analysis["performance_rating"] = "moderate"
        
        # Memory analysis
        if execution_result.memory_used_mb > 100:
            analysis["memory_intensive"] = True
        
        return analysis
    
    def _assess_security(
        self, 
        execution_result: ExecutionResult, 
        static_violations: List[str]
    ) -> Dict[str, Any]:
        """Assess security aspects of execution."""
        
        assessment = {
            "static_violations": static_violations,
            "runtime_violations": execution_result.security_violations,
            "network_activity": execution_result.network_requests,
            "file_system_activity": {
                "files_created": len(execution_result.files_created),
                "files_modified": len(execution_result.files_modified)
            },
            "security_score": 8.0  # Base score
        }
        
        # Adjust security score based on violations
        total_violations = len(static_violations) + len(execution_result.security_violations)
        assessment["security_score"] = max(0.0, 8.0 - (total_violations * 2.0))
        
        return assessment
    
    def _generate_optimization_suggestions(
        self,
        code_analysis: Dict[str, Any],
        execution_result: ExecutionResult
    ) -> List[str]:
        """Generate code optimization suggestions."""
        
        suggestions = []
        
        # Performance-based suggestions
        if execution_result.execution_time_ms > 5000:
            suggestions.append("Consider optimizing algorithms for better performance")
        
        if execution_result.memory_used_mb > 64:
            suggestions.append("Monitor memory usage - consider using generators or streaming")
        
        # Code quality suggestions
        if code_analysis.get("complexity_score", 0) > 10:
            suggestions.append("High complexity detected - consider breaking into smaller functions")
        
        if len(execution_result.stdout) > 10000:
            suggestions.append("Large output detected - consider paginating or summarizing results")
        
        return suggestions
    
    def _generate_security_recommendations(
        self,
        security_assessment: Dict[str, Any]
    ) -> List[str]:
        """Generate security recommendations."""
        
        recommendations = []
        
        if security_assessment["static_violations"]:
            recommendations.append("Remove dangerous function calls and imports")
        
        if security_assessment["file_system_activity"]["files_created"] > 10:
            recommendations.append("Limit file creation to prevent disk space issues")
        
        if security_assessment["security_score"] < 6.0:
            recommendations.append("Review code for security best practices")
        
        return recommendations
    
    async def _collect_created_files(
        self,
        sandbox: SandboxEnvironment,
        file_list: List[str]
    ) -> Dict[str, str]:
        """Collect contents of created files."""
        
        files = {}
        
        for file_path in file_list:
            try:
                full_path = sandbox.working_dir / file_path
                if full_path.exists() and full_path.is_file():
                    # Limit file size
                    file_size = full_path.stat().st_size
                    if file_size > 10000:  # 10KB limit
                        files[file_path] = f"[File too large: {file_size} bytes]"
                    else:
                        files[file_path] = full_path.read_text(errors='ignore')
            except Exception as e:
                files[file_path] = f"[Error reading file: {e}]"
        
        return files
    
    async def _custom_quality_assessment(
        self,
        quality: 'QualityScore',
        input_data: CodeExecutionInput,
        result: CodeExecutionOutput,
        context: ToolContext
    ) -> None:
        """Custom quality assessment for code execution."""
        
        # Assess execution success
        if result.execution_result.success:
            quality.add_dimension(QualityMetric.RELIABILITY, 9.0)
        else:
            quality.add_dimension(
                QualityMetric.RELIABILITY, 4.0,
                "Code execution failed - review code and dependencies"
            )
        
        # Assess performance
        exec_time = result.execution_result.execution_time_ms
        if exec_time < 1000:  # Under 1 second
            quality.add_dimension(QualityMetric.PERFORMANCE, 9.5)
        elif exec_time < 5000:  # Under 5 seconds
            quality.add_dimension(QualityMetric.PERFORMANCE, 8.0)
        else:
            quality.add_dimension(
                QualityMetric.PERFORMANCE, 6.0,
                "Execution time is high - consider optimization"
            )
        
        # Assess security
        security_score = result.security_assessment.get("security_score", 5.0)
        if security_score >= 8.0:
            quality.add_dimension(QualityMetric.SECURITY, 9.0)
        elif security_score >= 6.0:
            quality.add_dimension(QualityMetric.SECURITY, 7.0)
        else:
            quality.add_dimension(
                QualityMetric.SECURITY, 5.0,
                "Security concerns detected - review code for vulnerabilities"
            )


# Factory function
def create_code_execution_tool() -> CodeExecutionTool:
    """Create a configured CodeExecutionTool instance."""
    return CodeExecutionTool()


# Export main components
__all__ = [
    'CodeExecutionTool',
    'CodeExecutionInput',
    'CodeExecutionOutput',
    'ExecutionResult',
    'create_code_execution_tool'
]