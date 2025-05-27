# tools/documentation_tool.py
"""
PydanticAI function tool for reading local documentation files.
This tool provides agents with access to project documentation using the FileReader utility.
"""

from typing import Optional
from pydantic import BaseModel, Field

from utilities.file_reader import FileReader, FileReaderError


class DocumentationRequest(BaseModel):
    """Request model for documentation reading"""
    
    filename: str = Field(..., description="Name of the documentation file to read")
    encoding: str = Field(default="utf-8", description="Text encoding to use for reading the file")


class DocumentationResponse(BaseModel):
    """Response model for documentation content"""
    
    success: bool = Field(..., description="Whether the file was successfully read")
    content: str = Field(..., description="Content of the documentation file")
    filename: str = Field(..., description="Name of the file that was read")
    error_message: Optional[str] = Field(None, description="Error message if reading failed")


def read_documentation(filename: str, encoding: str = "utf-8") -> str:
    """
    Read a documentation file from the local docs/ directory.
    
    This tool allows agents to access project documentation files like
    architecture guides, API documentation, and project specifications.
    
    Args:
        filename: Name of the documentation file to read (e.g., "agent-architecture.md")
        encoding: Text encoding to use for reading the file (default: utf-8)
    
    Returns:
        Content of the documentation file formatted for agent consumption
    
    Examples:
        - read_documentation("agent-architecture.md")
        - read_documentation("api-docs.md", encoding="utf-8")
        - read_documentation("README.md")
    """
    try:
        # Initialize FileReader with current working directory
        reader = FileReader()
        
        # Read the documentation file
        content = reader.read_docs_file(filename, encoding)
        
        # Format response for agent consumption
        formatted_content = f"📖 **{filename}**\n\n{content}"
        
        return formatted_content
        
    except FileReaderError as e:
        # Handle file reading errors gracefully
        error_msg = f"📖 Documentation read error for '{filename}': {str(e)}"
        return error_msg
    except Exception as e:
        # Handle unexpected errors
        error_msg = f"📖 Unexpected error reading '{filename}': {str(e)}"
        return error_msg


def list_documentation_files() -> str:
    """
    List all available documentation files in the docs/ directory.
    
    This tool helps agents discover what documentation is available
    before attempting to read specific files.
    
    Returns:
        Formatted list of available documentation files
    
    Example usage:
        Use this tool to see what documentation is available, then use
        read_documentation() to read specific files.
    """
    try:
        # Initialize FileReader with current working directory
        reader = FileReader()
        
        # Get list of documentation files
        files = reader.list_docs_files()
        
        if not files:
            return "📖 No documentation files found in docs/ directory."
        
        # Format the file list for agent consumption
        file_list = "\n".join(f"- {file}" for file in files)
        formatted_response = f"📖 **Available Documentation Files:**\n\n{file_list}"
        
        return formatted_response
        
    except FileReaderError as e:
        # Handle file listing errors gracefully
        error_msg = f"📖 Error listing documentation files: {str(e)}"
        return error_msg
    except Exception as e:
        # Handle unexpected errors
        error_msg = f"📖 Unexpected error listing documentation: {str(e)}"
        return error_msg


# Backward compatibility function for structured responses
def read_documentation_structured(request: DocumentationRequest) -> DocumentationResponse:
    """
    Read documentation with structured request/response models.
    
    This function provides backward compatibility and structured validation
    for more complex use cases that require detailed error handling.
    
    Args:
        request: DocumentationRequest with filename and encoding
        
    Returns:
        DocumentationResponse with success status and content or error
    """
    try:
        # Initialize FileReader with current working directory
        reader = FileReader()
        
        # Read the documentation file
        content = reader.read_docs_file(request.filename, request.encoding)
        
        return DocumentationResponse(
            success=True,
            content=content,
            filename=request.filename,
            error_message=None
        )
        
    except FileReaderError as e:
        return DocumentationResponse(
            success=False,
            content="",
            filename=request.filename,
            error_message=str(e)
        )
    except Exception as e:
        return DocumentationResponse(
            success=False,
            content="",
            filename=request.filename,
            error_message=f"Unexpected error: {str(e)}"
        )