"""
Pure Python Claude implementation without JavaScript CLI dependencies.

Replaces the problematic claude-code-sdk with direct Anthropic SDK usage
and Python-native file system tools.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Any
import logging

import anthropic

logger = logging.getLogger(__name__)


class PurePythonClaude:
    """Pure Python Claude implementation with file system access."""
    
    def __init__(self):
        self.client = anthropic.Anthropic()
    
    async def execute_task(
        self,
        prompt: str,
        working_directory: str = None,
        max_turns: int = 5,
        chat_id: str = None
    ) -> str:
        """
        Execute a task using pure Python with file system access.
        
        Args:
            prompt: The task description
            working_directory: Directory to work in
            max_turns: Maximum conversation turns
            chat_id: Chat context for workspace detection
            
        Returns:
            Complete response from Claude
        """
        try:
            # Validate working directory
            if working_directory:
                work_path = Path(working_directory).resolve()
                if not work_path.exists():
                    return f"❌ Working directory does not exist: {working_directory}"
                if not work_path.is_dir():
                    return f"❌ Path is not a directory: {working_directory}"
                os.chdir(work_path)
                logger.info(f"Changed to working directory: {work_path}")
            else:
                work_path = Path.cwd()
            
            # Build file-aware system prompt
            system_prompt = self._build_file_aware_system_prompt(work_path)
            
            # Search for relevant files first
            search_context = self._search_relevant_files(work_path, prompt)
            
            # Create message with context
            user_message = f"""Working directory: {work_path}

{prompt}

SEARCH CONTEXT:
{search_context}

Please analyze the files I found and provide the requested information. If you need to read specific files, let me know and I'll provide their contents."""

            # Execute with Anthropic SDK
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                temperature=0.1,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ]
            )
            
            # Extract response text
            result_parts = []
            for content_block in response.content:
                if content_block.type == "text":
                    result_parts.append(content_block.text)
            
            return "".join(result_parts)
            
        except Exception as e:
            logger.error(f"Pure Python Claude execution error: {e}")
            return f"❌ Task execution failed: {e}"
    
    def _build_file_aware_system_prompt(self, working_directory: Path) -> str:
        """Build a system prompt with file system awareness."""
        
        # Get directory overview
        try:
            files = list(working_directory.glob("*"))
            file_count = len([f for f in files if f.is_file()])
            dir_count = len([f for f in files if f.is_dir()])
            
            # Look for key project files
            key_files = []
            for pattern in ["*.py", "*.js", "*.md", "*.json", "*.yml", "*.yaml"]:
                key_files.extend(working_directory.glob(pattern))
            
            directory_context = f"""
CURRENT WORKING DIRECTORY: {working_directory}
- Files: {file_count}, Directories: {dir_count}
- Key files found: {[f.name for f in key_files[:10]]}
"""
        except Exception:
            directory_context = f"CURRENT WORKING DIRECTORY: {working_directory}"
        
        return f"""You are Claude, an AI assistant with file system access. You can help with development tasks by reading, analyzing, and searching through code files.

{directory_context}

CAPABILITIES:
- Read files using standard file operations
- Search through directories and files
- Analyze code structure and patterns
- Provide specific information from the codebase
- Answer questions about project structure and content

INSTRUCTIONS:
- Analyze the search results provided and extract the requested information
- If you see relevant files in the search context, read their contents
- Provide specific information from actual files, not generic guidance
- Be proactive in reading files that might contain the requested information
"""
    
    def _search_relevant_files(self, work_path: Path, prompt: str) -> str:
        """Search for files relevant to the prompt."""
        try:
            search_results = []
            prompt_lower = prompt.lower()
            
            # Extract search terms from prompt
            search_terms = []
            if "team analysis" in prompt_lower:
                search_terms.extend(["team", "analysis", "category", "description", "evaluation"])
            if "config" in prompt_lower:
                search_terms.extend(["config", "settings", "options"])
            
            # Default to common terms if no specific ones found
            if not search_terms:
                search_terms = ["category", "description", "config", "model", "analysis"]
            
            # Search for files recursively
            for root, dirs, files in os.walk(work_path):
                # Skip common non-relevant directories
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', '.git']]
                
                root_path = Path(root)
                for file in files:
                    file_path = root_path / file
                    relative_path = file_path.relative_to(work_path)
                    
                    # Check if file name or path contains search terms
                    file_str = str(relative_path).lower()
                    if any(term in file_str for term in search_terms):
                        search_results.append(f"📁 {relative_path}")
                        
                        # If it's a reasonable size, read and show full content for relevant files
                        try:
                            if file_path.suffix in ['.py', '.js', '.json', '.md', '.txt', '.yml', '.yaml'] and file_path.stat().st_size < 100000:
                                content = file_path.read_text(encoding='utf-8', errors='ignore')
                                # Show full content if it contains search terms
                                if any(term in content.lower() for term in search_terms):
                                    search_results.append(f"   📄 FULL CONTENT:\n{content}\n" + "="*50 + "\n")
                        except Exception:
                            pass
            
            if search_results:
                return f"RELEVANT FILES FOUND:\n" + "\n".join(search_results[:10])  # Limit results
            else:
                return "No obviously relevant files found. You may need to search more broadly."
                
        except Exception as e:
            return f"Search error: {e}"


# Simple function interface for compatibility
async def execute_pure_python_task(
    prompt: str,
    working_directory: str = None,
    chat_id: str = None
) -> str:
    """Execute a task using pure Python Claude."""
    claude = PurePythonClaude()
    return await claude.execute_task(prompt, working_directory, chat_id=chat_id)