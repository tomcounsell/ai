"""
SWE Error Recovery System

Provides intelligent error recovery suggestions for software engineering tools
that use Claude Code execution. Analyzes error patterns and provides actionable
guidance for common failure scenarios.
"""

import re
from typing import Optional, Tuple
from enum import Enum


class ErrorCategory(Enum):
    """Categories of errors that can occur during SWE tool execution"""
    TIMEOUT = "timeout"
    PERMISSION = "permission"
    COMMAND_NOT_FOUND = "command_not_found"
    FILE_NOT_FOUND = "file_not_found"
    DIRECTORY_ERROR = "directory_error"
    SUBPROCESS_ERROR = "subprocess_error"
    CLAUDE_CODE_ERROR = "claude_code_error"
    WORKSPACE_ERROR = "workspace_error"
    UNKNOWN = "unknown"


class SWEErrorRecovery:
    """Intelligent error recovery for SWE tools"""
    
    @staticmethod
    def categorize_error(error_message: str) -> ErrorCategory:
        """
        Categorize an error message into a known error type.
        
        Args:
            error_message: The error message to categorize
            
        Returns:
            ErrorCategory: The category of error detected
        """
        error_lower = error_message.lower()
        
        # Timeout errors
        if any(keyword in error_lower for keyword in ["timeout", "timed out", "exceeded"]):
            return ErrorCategory.TIMEOUT
            
        # Permission errors
        if any(keyword in error_lower for keyword in ["permission denied", "access denied", "forbidden"]):
            return ErrorCategory.PERMISSION
            
        # Command not found errors (more specific patterns first)
        if any(keyword in error_lower for keyword in ["command not found", "claude: command", ": not found"]):
            return ErrorCategory.COMMAND_NOT_FOUND
            
        # File/directory errors
        if any(keyword in error_lower for keyword in ["no such file", "file not found", "cannot find"]):
            return ErrorCategory.FILE_NOT_FOUND
            
        if any(keyword in error_lower for keyword in ["not a directory", "directory not found", "invalid directory"]):
            return ErrorCategory.DIRECTORY_ERROR
            
        # Subprocess errors
        if any(keyword in error_lower for keyword in ["subprocess", "process failed", "exit code"]):
            return ErrorCategory.SUBPROCESS_ERROR
            
        # Claude Code specific errors
        if any(keyword in error_lower for keyword in ["claude code failed", "claude execution"]):
            return ErrorCategory.CLAUDE_CODE_ERROR
            
        # Workspace errors
        if any(keyword in error_lower for keyword in ["workspace", "working directory", "access denied"]):
            return ErrorCategory.WORKSPACE_ERROR
            
        return ErrorCategory.UNKNOWN
    
    @staticmethod
    def suggest_recovery(
        tool_name: str,
        error_message: str,
        task_description: str,
        working_directory: str = "."
    ) -> str:
        """
        Provide intelligent recovery suggestions based on error patterns.
        
        Args:
            tool_name: Name of the tool that failed ("delegate_coding_task" or "technical_analysis")
            error_message: The error message received
            task_description: Description of the task that failed
            working_directory: Working directory where the error occurred
            
        Returns:
            Formatted recovery suggestion message
        """
        error_category = SWEErrorRecovery.categorize_error(error_message)
        
        # Get specific suggestion based on error category
        suggestion = SWEErrorRecovery._get_category_suggestion(
            error_category, tool_name, task_description, working_directory
        )
        
        # Add alternative tool suggestion if appropriate
        alternative_suggestion = SWEErrorRecovery._get_alternative_tool_suggestion(
            tool_name, error_category, task_description
        )
        
        return f"{suggestion}\n\n{alternative_suggestion}" if alternative_suggestion else suggestion
    
    @staticmethod
    def _get_category_suggestion(
        category: ErrorCategory,
        tool_name: str,
        task_description: str,
        working_directory: str
    ) -> str:
        """Get specific suggestion based on error category"""
        
        suggestions = {
            ErrorCategory.TIMEOUT: (
                "💡 **Recovery Suggestion - Task Timeout**\n"
                f"The task '{task_description}' exceeded the execution timeout.\n\n"
                "**Possible solutions:**\n"
                "• Break the task into smaller, more focused steps\n"
                "• Use `technical_analysis` first to understand the scope\n"
                "• Specify more targeted requirements in the task description\n"
                "• Check if the working directory contains excessive files that slow processing"
            ),
            
            ErrorCategory.PERMISSION: (
                "💡 **Recovery Suggestion - Permission Denied**\n"
                f"Permission denied when accessing files in: `{working_directory}`\n\n"
                "**Possible solutions:**\n"
                "• Verify Claude Code has read/write access to the target directory\n"
                "• Check if files are locked by another process\n"
                "• Ensure the working directory path is correct and accessible\n"
                "• Try running with elevated permissions if appropriate"
            ),
            
            ErrorCategory.COMMAND_NOT_FOUND: (
                "💡 **Recovery Suggestion - Command Not Found**\n"
                "Claude Code command is not available or not in PATH.\n\n"
                "**Possible solutions:**\n"
                "• Verify Claude Code installation: `claude --version`\n"
                "• Ensure Claude Code is in your system PATH\n"
                "• Reinstall Claude Code if necessary\n"
                "• Check if you're using the correct command syntax"
            ),
            
            ErrorCategory.FILE_NOT_FOUND: (
                "💡 **Recovery Suggestion - File Not Found**\n"
                f"Required files not found in directory: `{working_directory}`\n\n"
                "**Possible solutions:**\n"
                "• Verify the working directory is correct for this task\n"
                "• Check if the project files exist in the expected location\n"
                "• Use `technical_analysis` to explore the project structure first\n"
                "• Ensure you're in the correct workspace for this task"
            ),
            
            ErrorCategory.DIRECTORY_ERROR: (
                "💡 **Recovery Suggestion - Directory Issue**\n"
                f"Problem with working directory: `{working_directory}`\n\n"
                "**Possible solutions:**\n"
                "• Verify the directory path exists and is accessible\n"
                "• Check workspace configuration for correct directory mapping\n"
                "• Ensure the path is a directory, not a file\n"
                "• Try using an absolute path instead of relative path"
            ),
            
            ErrorCategory.SUBPROCESS_ERROR: (
                "💡 **Recovery Suggestion - Execution Error**\n"
                "Claude Code subprocess execution failed.\n\n"
                "**Possible solutions:**\n"
                "• Check Claude Code logs for detailed error information\n"
                "• Verify system resources (memory, disk space) are available\n"
                "• Try simplifying the task description\n"
                "• Ensure no conflicting processes are running"
            ),
            
            ErrorCategory.CLAUDE_CODE_ERROR: (
                "💡 **Recovery Suggestion - Claude Code Error**\n"
                "Claude Code execution encountered an internal error.\n\n"
                "**Possible solutions:**\n"
                "• Try reformulating the task with clearer instructions\n"
                "• Break complex tasks into simpler steps\n"
                "• Check if Claude Code needs to be updated\n"
                "• Verify the task is within Claude Code's capabilities"
            ),
            
            ErrorCategory.WORKSPACE_ERROR: (
                "💡 **Recovery Suggestion - Workspace Error**\n"
                f"Workspace access issue in: `{working_directory}`\n\n"
                "**Possible solutions:**\n"
                "• Verify chat is mapped to correct workspace in configuration\n"
                "• Check workspace permissions and access controls\n"
                "• Ensure the workspace directory exists and is accessible\n"
                "• Try specifying an explicit target directory"
            ),
            
            ErrorCategory.UNKNOWN: (
                "💡 **Recovery Suggestion - Unknown Error**\n"
                "An unexpected error occurred during execution.\n\n"
                "**Possible solutions:**\n"
                "• Try rephrasing the task description\n"
                "• Check system resources and dependencies\n"
                "• Verify Claude Code installation and configuration\n"
                "• Consider breaking the task into smaller steps"
            )
        }
        
        return suggestions.get(category, suggestions[ErrorCategory.UNKNOWN])
    
    @staticmethod
    def _get_alternative_tool_suggestion(
        current_tool: str,
        error_category: ErrorCategory,
        task_description: str
    ) -> Optional[str]:
        """Suggest alternative tool if appropriate"""
        
        # Don't suggest alternatives only for command not found
        if error_category == ErrorCategory.COMMAND_NOT_FOUND:
            return None
        
        if current_tool == "delegate_coding_task":
            return (
                "🔄 **Alternative Approach:**\n"
                f"Consider using `technical_analysis` to research and understand "
                f"the requirements for: '{task_description}' before attempting implementation."
            )
        elif current_tool == "technical_analysis":
            return (
                "🔄 **Alternative Approach:**\n"
                f"If you need to make actual changes, try `delegate_coding_task` "
                f"with a more specific task description based on your research."
            )
        
        return None
    
    @staticmethod
    def extract_error_details(error_message: str) -> dict:
        """
        Extract structured details from error message for analysis.
        
        Args:
            error_message: Raw error message
            
        Returns:
            dict: Structured error details
        """
        details = {
            "original_message": error_message,
            "category": SWEErrorRecovery.categorize_error(error_message),
            "exit_code": None,
            "timeout_duration": None,
            "file_path": None,
            "directory_path": None
        }
        
        # Extract exit code
        exit_code_match = re.search(r"exit code (\d+)", error_message, re.IGNORECASE)
        if exit_code_match:
            details["exit_code"] = int(exit_code_match.group(1))
        
        # Extract timeout duration
        timeout_match = re.search(r"(\d+)\s*seconds?", error_message, re.IGNORECASE)
        if timeout_match and "timeout" in error_message.lower():
            details["timeout_duration"] = int(timeout_match.group(1))
        
        # Extract file paths
        path_patterns = [
            r"'([^']+)'",  # Single quoted paths
            r'"([^"]+)"',  # Double quoted paths
            r"(/[^\s]+)",  # Unix-style absolute paths
            r"([A-Z]:\\[^\s]+)"  # Windows-style absolute paths
        ]
        
        for pattern in path_patterns:
            matches = re.findall(pattern, error_message)
            for match in matches:
                if "/" in match or "\\" in match:  # Looks like a path
                    if not details["file_path"]:
                        details["file_path"] = match
                    break
        
        return details
    
    @staticmethod
    def format_recovery_response(
        tool_name: str,
        task_description: str,
        error_message: str,
        working_directory: str = ".",
        execution_time: Optional[float] = None
    ) -> str:
        """
        Format a complete recovery response including error details and suggestions.
        
        Args:
            tool_name: Name of the failed tool
            task_description: Description of the failed task
            error_message: The error that occurred
            working_directory: Working directory used
            execution_time: Time taken before failure (if available)
            
        Returns:
            Formatted recovery response
        """
        error_details = SWEErrorRecovery.extract_error_details(error_message)
        recovery_suggestion = SWEErrorRecovery.suggest_recovery(
            tool_name, error_message, task_description, working_directory
        )
        
        sections = [
            f"❌ **{tool_name} Failed**",
            "",
            f"**Task:** {task_description}",
            f"**Directory:** {working_directory}",
        ]
        
        if execution_time:
            sections.append(f"**Duration:** {execution_time:.1f}s")
        
        sections.extend([
            f"**Error Category:** {error_details['category'].value.title()}",
            "",
            "**Error Details:**",
            f"```\n{error_message}\n```",
            "",
            recovery_suggestion
        ])
        
        return "\n".join(sections)