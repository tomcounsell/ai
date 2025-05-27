#!/usr/bin/env python3
"""
File reading utility for accessing local files with focus on docs/ directory.
Provides secure file access with proper error handling and logging.
"""

import os
import pathlib
from typing import Optional, Union
from utilities.logger import logger


class FileReaderError(Exception):
    """Custom exception for file reading errors"""
    pass


class FileReader:
    """Utility class for reading local files with security and error handling"""
    
    def __init__(self, base_path: Optional[Union[str, pathlib.Path]] = None):
        """
        Initialize FileReader with optional base path restriction
        
        Args:
            base_path: Optional base directory to restrict file access to
        """
        if base_path:
            self.base_path = pathlib.Path(base_path).resolve()
        else:
            self.base_path = pathlib.Path.cwd()
        
        logger.info(f"FileReader initialized with base path: {self.base_path}")
    
    def _validate_path(self, file_path: Union[str, pathlib.Path]) -> pathlib.Path:
        """
        Validate and resolve file path, ensuring it's within allowed boundaries
        
        Args:
            file_path: Path to the file to read
            
        Returns:
            Resolved pathlib.Path object
            
        Raises:
            FileReaderError: If path is invalid or outside allowed boundaries
        """
        try:
            resolved_path = pathlib.Path(file_path).resolve()
            
            # Ensure the resolved path is within the base path
            if not str(resolved_path).startswith(str(self.base_path)):
                raise FileReaderError(
                    f"File path '{resolved_path}' is outside allowed directory '{self.base_path}'"
                )
            
            return resolved_path
            
        except (OSError, ValueError) as e:
            raise FileReaderError(f"Invalid file path '{file_path}': {e}")
    
    def read_file(
        self, 
        file_path: Union[str, pathlib.Path], 
        encoding: str = 'utf-8'
    ) -> str:
        """
        Read and return the contents of a file
        
        Args:
            file_path: Path to the file to read
            encoding: Text encoding to use (default: utf-8)
            
        Returns:
            File contents as string
            
        Raises:
            FileReaderError: If file cannot be read or accessed
        """
        validated_path = self._validate_path(file_path)
        
        try:
            # Check if file exists
            if not validated_path.exists():
                raise FileReaderError(f"File not found: {validated_path}")
            
            # Check if it's actually a file
            if not validated_path.is_file():
                raise FileReaderError(f"Path is not a file: {validated_path}")
            
            # Read file contents
            with open(validated_path, 'r', encoding=encoding) as file:
                content = file.read()
                
            logger.info(f"Successfully read file: {validated_path} ({len(content)} chars)")
            return content
            
        except (OSError, UnicodeDecodeError, PermissionError) as e:
            error_msg = f"Error reading file '{validated_path}': {e}"
            logger.error(error_msg)
            raise FileReaderError(error_msg)
    
    def read_docs_file(self, filename: str, encoding: str = 'utf-8') -> str:
        """
        Convenience method to read files from the docs/ directory
        
        Args:
            filename: Name of the file in the docs/ directory
            encoding: Text encoding to use (default: utf-8)
            
        Returns:
            File contents as string
            
        Raises:
            FileReaderError: If file cannot be read or accessed
        """
        docs_path = self.base_path / 'docs' / filename
        return self.read_file(docs_path, encoding)
    
    def list_docs_files(self) -> list[str]:
        """
        List all files in the docs/ directory
        
        Returns:
            List of filenames in the docs/ directory
            
        Raises:
            FileReaderError: If docs directory cannot be accessed
        """
        docs_path = self.base_path / 'docs'
        
        try:
            if not docs_path.exists():
                raise FileReaderError(f"Docs directory not found: {docs_path}")
            
            if not docs_path.is_dir():
                raise FileReaderError(f"Docs path is not a directory: {docs_path}")
            
            files = [f.name for f in docs_path.iterdir() if f.is_file()]
            logger.info(f"Found {len(files)} files in docs directory")
            return sorted(files)
            
        except (OSError, PermissionError) as e:
            error_msg = f"Error listing docs directory '{docs_path}': {e}"
            logger.error(error_msg)
            raise FileReaderError(error_msg)
    
    def file_exists(self, file_path: Union[str, pathlib.Path]) -> bool:
        """
        Check if a file exists
        
        Args:
            file_path: Path to check
            
        Returns:
            True if file exists and is readable, False otherwise
        """
        try:
            validated_path = self._validate_path(file_path)
            return validated_path.exists() and validated_path.is_file()
        except FileReaderError:
            return False


# Convenience functions for direct usage
def read_file(file_path: Union[str, pathlib.Path], encoding: str = 'utf-8') -> str:
    """
    Read a file using default FileReader instance
    
    Args:
        file_path: Path to the file to read
        encoding: Text encoding to use (default: utf-8)
        
    Returns:
        File contents as string
    """
    reader = FileReader()
    return reader.read_file(file_path, encoding)


def read_docs_file(filename: str, encoding: str = 'utf-8') -> str:
    """
    Read a file from the docs/ directory using default FileReader instance
    
    Args:
        filename: Name of the file in the docs/ directory
        encoding: Text encoding to use (default: utf-8)
        
    Returns:
        File contents as string
    """
    reader = FileReader()
    return reader.read_docs_file(filename, encoding)


def list_docs_files() -> list[str]:
    """
    List all files in the docs/ directory using default FileReader instance
    
    Returns:
        List of filenames in the docs/ directory
    """
    reader = FileReader()
    return reader.list_docs_files()