"""Tests for linting_tool.py - Code linting and formatting functionality."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from tools.linting_tool import (
    LintSeverity,
    LintIssue,
    LintResult,
    LintConfig,
    run_linting,
    lint_files,
    quick_lint_check,
    lint_python_project,
    strict_lint_check,
    quick_format_check,
    _run_ruff,
    _run_black,
    _run_mypy,
    _run_flake8
)


@pytest.fixture
def temp_python_file():
    """Create a temporary Python file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write('''def hello_world():
    print("Hello, World!")

if __name__ == "__main__":
    hello_world()
''')
        temp_file = f.name
    
    yield temp_file
    
    # Cleanup
    if os.path.exists(temp_file):
        os.unlink(temp_file)


@pytest.fixture 
def temp_project_dir():
    """Create a temporary project directory structure for testing."""
    temp_dir = tempfile.mkdtemp()
    
    # Create some Python files in the project
    test_file_1 = Path(temp_dir) / "module1.py"
    test_file_1.write_text('''def function_one():
    return "test"

class TestClass:
    def method(self):
        pass
''')
    
    test_file_2 = Path(temp_dir) / "module2.py"
    test_file_2.write_text('''import os
import sys

def function_two():
    print("hello")
''')
    
    yield temp_dir
    
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestLintSeverity:
    """Test LintSeverity enum."""
    
    def test_severity_values(self):
        """Test all severity values are valid."""
        assert LintSeverity.ERROR == "error"
        assert LintSeverity.WARNING == "warning"
        assert LintSeverity.INFO == "info"
        assert LintSeverity.STYLE == "style"


class TestLintIssue:
    """Test LintIssue model validation."""
    
    def test_valid_lint_issue(self):
        """Test creating valid lint issue."""
        issue = LintIssue(
            file_path="test.py",
            line=10,
            column=5,
            severity=LintSeverity.ERROR,
            code="E001",
            message="Syntax error",
            rule="syntax-check"
        )
        
        assert issue.file_path == "test.py"
        assert issue.line == 10
        assert issue.column == 5
        assert issue.severity == LintSeverity.ERROR
        assert issue.code == "E001"
        assert issue.message == "Syntax error"
        assert issue.rule == "syntax-check"
    
    def test_lint_issue_without_rule(self):
        """Test lint issue without optional rule."""
        issue = LintIssue(
            file_path="test.py",
            line=1,
            column=1,
            severity=LintSeverity.WARNING,
            code="W001",
            message="Warning message"
        )
        
        assert issue.rule is None


class TestLintResult:
    """Test LintResult model validation."""
    
    def test_valid_lint_result(self):
        """Test creating valid lint result."""
        issues = [
            LintIssue(
                file_path="test.py",
                line=1,
                column=1,
                severity=LintSeverity.ERROR,
                code="E001",
                message="Error"
            )
        ]
        
        result = LintResult(
            success=False,
            total_issues=1,
            issues_by_severity={LintSeverity.ERROR: 1, LintSeverity.WARNING: 0},
            issues=issues,
            execution_time=1.5,
            tools_run=["ruff"],
            summary="Found 1 error"
        )
        
        assert result.success == False
        assert result.total_issues == 1
        assert result.issues_by_severity[LintSeverity.ERROR] == 1
        assert len(result.issues) == 1
        assert result.execution_time == 1.5
        assert result.tools_run == ["ruff"]


class TestLintConfig:
    """Test LintConfig model validation."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = LintConfig()
        
        assert config.run_ruff == True
        assert config.run_black == True
        assert config.run_mypy == False
        assert config.run_flake8 == False
        assert config.fix_issues == False
        assert config.target_files is None
        assert config.ignore_patterns == []
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = LintConfig(
            run_ruff=False,
            run_mypy=True,
            fix_issues=True,
            target_files=["file1.py", "file2.py"],
            ignore_patterns=["*.pyc", "__pycache__"]
        )
        
        assert config.run_ruff == False
        assert config.run_mypy == True
        assert config.fix_issues == True
        assert config.target_files == ["file1.py", "file2.py"]
        assert config.ignore_patterns == ["*.pyc", "__pycache__"]


class TestTempFiles:
    """Helper class for creating temporary test files."""
    
    @pytest.fixture
    def temp_python_file(self):
        """Create temporary Python file with linting issues."""
        content = '''
import os,sys
def bad_function( ):
    x=1+2
    if x==3:print("hello")
    return x
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        try:
            os.unlink(temp_path)
        except:
            pass
    
    @pytest.fixture
    def temp_project_dir(self):
        """Create temporary project directory with Python files."""
        temp_dir = tempfile.mkdtemp()
        
        # Create main.py with issues
        main_content = '''
import   sys
def hello():
  print( "Hello World" )
'''
        main_path = Path(temp_dir) / "main.py"
        main_path.write_text(main_content)
        
        # Create utils.py with different issues
        utils_content = '''
def   calculate(x,y):
    return x+y
'''
        utils_path = Path(temp_dir) / "utils.py"
        utils_path.write_text(utils_content)
        
        yield temp_dir
        
        # Cleanup
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


class TestRunRuff:
    """Test _run_ruff functionality with mocking."""
    
    @patch('tools.linting_tool.subprocess.run')
    def test_ruff_success_with_issues(self, mock_run):
        """Test ruff execution with JSON output."""
        mock_output = [
            {
                "filename": "test.py",
                "location": {"row": 1, "column": 1},
                "severity": "error",
                "code": "E902",
                "message": "IndentationError",
                "rule": "syntax-error"
            },
            {
                "filename": "test.py", 
                "location": {"row": 5, "column": 10},
                "severity": "warning",
                "code": "W292",
                "message": "No newline at end of file",
                "rule": "missing-final-newline"
            }
        ]
        
        mock_run.return_value = MagicMock(
            returncode=1,  # Ruff returns non-zero when issues found
            stdout=json.dumps(mock_output)
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_ruff(project_path, config)
        
        assert len(issues) == 2
        assert issues[0].file_path == "test.py"
        assert issues[0].line == 1
        assert issues[0].severity == LintSeverity.ERROR
        assert issues[0].code == "E902"
        assert issues[1].severity == LintSeverity.WARNING
        
    @patch('tools.linting_tool.subprocess.run')
    def test_ruff_no_issues(self, mock_run):
        """Test ruff execution with no issues."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=""
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_ruff(project_path, config)
        assert len(issues) == 0
    
    @patch('tools.linting_tool.subprocess.run')
    def test_ruff_not_found(self, mock_run):
        """Test handling when ruff is not installed."""
        mock_run.side_effect = FileNotFoundError("ruff not found")
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_ruff(project_path, config)
        assert len(issues) == 0
    
    @patch('tools.linting_tool.subprocess.run')
    def test_ruff_with_fix_option(self, mock_run):
        """Test ruff execution with fix option."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        
        project_path = Path("/tmp/test")
        config = LintConfig(fix_issues=True)
        
        _run_ruff(project_path, config)
        
        # Check that --fix was included in command
        call_args = mock_run.call_args[0][0]
        assert "--fix" in call_args


class TestRunBlack:
    """Test _run_black functionality with mocking."""
    
    @patch('tools.linting_tool.subprocess.run')
    def test_black_formatting_needed(self, mock_run):
        """Test black when formatting is needed."""
        mock_output = '''--- test.py\t2023-01-01 12:00:00.000000 +0000
+++ test.py\t2023-01-01 12:00:01.000000 +0000
@@ -1,3 +1,3 @@
 def hello():
-    print("hello")
+    print("hello")
 '''
        
        mock_run.return_value = MagicMock(
            returncode=1,  # Black returns 1 when changes needed
            stdout=mock_output
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_black(project_path, config)
        
        assert len(issues) == 1
        assert issues[0].severity == LintSeverity.STYLE
        assert issues[0].code == "black"
        assert "formatting" in issues[0].message
    
    @patch('tools.linting_tool.subprocess.run')
    def test_black_no_changes_needed(self, mock_run):
        """Test black when no formatting is needed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=""
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_black(project_path, config)
        assert len(issues) == 0
    
    @patch('tools.linting_tool.subprocess.run')
    def test_black_not_found(self, mock_run):
        """Test handling when black is not installed."""
        mock_run.side_effect = FileNotFoundError("black not found")
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_black(project_path, config)
        assert len(issues) == 0


class TestRunMypy:
    """Test _run_mypy functionality with mocking."""
    
    @patch('tools.linting_tool.subprocess.run')
    def test_mypy_with_errors(self, mock_run):
        """Test mypy execution with type errors."""
        mock_output = '''test.py:10:5: error: Incompatible types in assignment [assignment]
test.py:15:1: warning: Unused import [import]
test.py:20:10: error: Argument 1 has incompatible type [arg-type]'''
        
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=mock_output
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_mypy(project_path, config)
        
        assert len(issues) == 3
        assert issues[0].line == 10
        assert issues[0].column == 5
        assert issues[0].severity == LintSeverity.ERROR
        assert "assignment" in issues[0].code
        assert issues[1].severity == LintSeverity.WARNING
    
    @patch('tools.linting_tool.subprocess.run')
    def test_mypy_no_errors(self, mock_run):
        """Test mypy execution with no errors."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=""
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_mypy(project_path, config)
        assert len(issues) == 0


class TestRunFlake8:
    """Test _run_flake8 functionality with mocking."""
    
    @patch('tools.linting_tool.subprocess.run')
    def test_flake8_with_issues(self, mock_run):
        """Test flake8 execution with issues."""
        mock_output = '''test.py:1:1:E302:expected 2 blank lines, found 1
test.py:5:80:E501:line too long (82 > 79 characters)
test.py:10:1:W293:blank line contains whitespace'''
        
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=mock_output
        )
        
        project_path = Path("/tmp/test")
        config = LintConfig()
        
        issues = _run_flake8(project_path, config)
        
        assert len(issues) == 3
        assert issues[0].file_path == "test.py"
        assert issues[0].line == 1
        assert issues[0].column == 1
        assert issues[0].code == "E302"
        assert issues[0].severity == LintSeverity.WARNING  # Flake8 issues are warnings
        assert "expected 2 blank lines" in issues[0].message


class TestRunLinting:
    """Test main run_linting functionality."""
    
    def test_invalid_project_path(self):
        """Test error handling for invalid project path."""
        with pytest.raises(FileNotFoundError):
            run_linting("/nonexistent/path")
    
    @patch('tools.linting_tool._run_ruff')
    @patch('tools.linting_tool._run_black')
    def test_successful_linting(self, mock_black, mock_ruff, temp_python_file):
        """Test successful linting execution."""
        # Mock tool responses
        mock_ruff.return_value = [
            LintIssue(
                file_path="test.py",
                line=1,
                column=1,
                severity=LintSeverity.ERROR,
                code="E001",
                message="Error"
            )
        ]
        mock_black.return_value = [
            LintIssue(
                file_path="test.py",
                line=1,
                column=1,
                severity=LintSeverity.STYLE,
                code="black",
                message="Formatting needed"
            )
        ]
        
        project_path = str(Path(temp_python_file).parent)
        config = LintConfig(run_mypy=False, run_flake8=False)
        
        result = run_linting(project_path, config)
        
        assert isinstance(result, LintResult)
        assert result.total_issues == 2
        assert result.success == False  # Has errors
        assert result.issues_by_severity[LintSeverity.ERROR] == 1
        assert result.issues_by_severity[LintSeverity.STYLE] == 1
        assert "ruff" in result.tools_run
        assert "black" in result.tools_run
        assert result.execution_time > 0
    
    @patch('tools.linting_tool._run_ruff')
    @patch('tools.linting_tool._run_black')
    def test_successful_linting_no_errors(self, mock_black, mock_ruff, temp_python_file):
        """Test successful linting with no issues."""
        mock_ruff.return_value = []
        mock_black.return_value = []
        
        project_path = str(Path(temp_python_file).parent)
        config = LintConfig(run_mypy=False, run_flake8=False)
        
        result = run_linting(project_path, config)
        
        assert result.success == True
        assert result.total_issues == 0
        assert "✅ All checks passed!" in result.summary


class TestLintFiles:
    """Test lint_files functionality."""
    
    @patch('tools.linting_tool.run_linting')
    def test_lint_specific_files(self, mock_run_linting, temp_python_file):
        """Test linting specific files."""
        mock_result = LintResult(
            success=True,
            total_issues=0,
            issues_by_severity={},
            issues=[],
            execution_time=1.0,
            tools_run=["ruff"],
            summary="All good"
        )
        mock_run_linting.return_value = mock_result
        
        result = lint_files([temp_python_file])
        
        assert result.success == True
        assert mock_run_linting.called
        
        # Check that target_files was set in config
        call_args = mock_run_linting.call_args
        config = call_args[0][1]  # Second argument is config
        assert config.target_files == [temp_python_file]
    
    def test_lint_files_empty_list(self):
        """Test linting empty file list."""
        with patch('tools.linting_tool.run_linting') as mock_run:
            mock_run.return_value = LintResult(
                success=True,
                total_issues=0,
                issues_by_severity={},
                issues=[],
                execution_time=0.1,
                tools_run=[],
                summary="Nothing to lint"
            )
            
            result = lint_files([])
            assert result.success == True


class TestQuickLintCheck:
    """Test quick_lint_check functionality."""
    
    @patch('tools.linting_tool.lint_files')
    def test_quick_check_pass(self, mock_lint_files, temp_python_file):
        """Test quick lint check that passes."""
        mock_lint_files.return_value = LintResult(
            success=True,
            total_issues=0,
            issues_by_severity={},
            issues=[],
            execution_time=0.5,
            tools_run=["ruff"],
            summary="Clean"
        )
        
        result = quick_lint_check(temp_python_file)
        assert result == True
    
    @patch('tools.linting_tool.lint_files')
    def test_quick_check_fail(self, mock_lint_files, temp_python_file):
        """Test quick lint check that fails."""
        mock_lint_files.return_value = LintResult(
            success=False,
            total_issues=3,
            issues_by_severity={LintSeverity.ERROR: 3},
            issues=[],
            execution_time=0.5,
            tools_run=["ruff"],
            summary="Issues found"
        )
        
        result = quick_lint_check(temp_python_file)
        assert result == False


class TestConvenienceFunctions:
    """Test convenience functions for common linting scenarios."""
    
    @patch('tools.linting_tool.run_linting')
    def test_lint_python_project(self, mock_run_linting, temp_project_dir):
        """Test lint_python_project convenience function."""
        mock_result = LintResult(
            success=True,
            total_issues=0,
            issues_by_severity={},
            issues=[],
            execution_time=2.0,
            tools_run=["ruff", "black"],
            summary="Project clean"
        )
        mock_run_linting.return_value = mock_result
        
        result = lint_python_project(temp_project_dir, fix_formatting=True)
        
        assert result.success == True
        assert mock_run_linting.called
        
        # Check configuration
        call_args = mock_run_linting.call_args
        config = call_args[0][1]
        assert config.run_ruff == True
        assert config.run_black == True
        assert config.run_mypy == False  # Should be disabled for basic check
        assert config.fix_issues == True  # fix_formatting=True
    
    @patch('tools.linting_tool.run_linting')
    def test_strict_lint_check(self, mock_run_linting, temp_project_dir):
        """Test strict_lint_check convenience function."""
        mock_result = LintResult(
            success=False,
            total_issues=5,
            issues_by_severity={LintSeverity.ERROR: 2, LintSeverity.WARNING: 3},
            issues=[],
            execution_time=5.0,
            tools_run=["ruff", "black", "mypy", "flake8"],
            summary="Multiple issues"
        )
        mock_run_linting.return_value = mock_result
        
        result = strict_lint_check(temp_project_dir)
        
        assert result.success == False
        assert mock_run_linting.called
        
        # Check that all tools are enabled
        call_args = mock_run_linting.call_args
        config = call_args[0][1]
        assert config.run_ruff == True
        assert config.run_black == True
        assert config.run_mypy == True
        assert config.run_flake8 == True
        assert config.fix_issues == False  # Strict mode doesn't auto-fix
    
    @patch('tools.linting_tool.lint_files')
    def test_quick_format_check(self, mock_lint_files, temp_python_file):
        """Test quick_format_check convenience function."""
        mock_lint_files.return_value = LintResult(
            success=True,
            total_issues=0,
            issues_by_severity={},
            issues=[],
            execution_time=0.3,
            tools_run=["black"],
            summary="Formatted correctly"
        )
        
        result = quick_format_check(temp_python_file)
        
        assert result == True
        assert mock_lint_files.called
        
        # Check that only black is run
        call_args = mock_lint_files.call_args
        config = call_args[0][1]
        assert config.run_ruff == False
        assert config.run_black == True
        assert config.run_mypy == False
        assert config.run_flake8 == False


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    @patch('tools.linting_tool._run_ruff')
    def test_malformed_ruff_output(self, mock_ruff, temp_python_file):
        """Test handling of malformed ruff JSON output."""
        # Simulate malformed JSON
        with patch('tools.linting_tool.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="invalid json {"
            )
            
            project_path = Path(temp_python_file).parent
            config = LintConfig()
            
            issues = _run_ruff(project_path, config)
            assert len(issues) == 0  # Should handle gracefully
    
    def test_config_with_target_files_and_ignore_patterns(self, temp_project_dir):
        """Test configuration with both target files and ignore patterns."""
        config = LintConfig(
            target_files=["main.py"],
            ignore_patterns=["*.pyc", "__pycache__"],
            run_mypy=False,
            run_flake8=False
        )
        
        with patch('tools.linting_tool._run_ruff') as mock_ruff:
            with patch('tools.linting_tool._run_black') as mock_black:
                mock_ruff.return_value = []
                mock_black.return_value = []
                
                result = run_linting(temp_project_dir, config)
                
                assert result.success == True
                assert mock_ruff.called
                assert mock_black.called


class TestIntegration:
    """Integration tests for the complete linting workflow."""
    
    def test_real_python_file_structure(self, temp_project_dir):
        """Test linting with realistic Python project structure."""
        # Create a more complex project structure
        project_path = Path(temp_project_dir)
        
        # Create subdirectory with module
        subdir = project_path / "mymodule"
        subdir.mkdir()
        
        # Create __init__.py
        init_file = subdir / "__init__.py"
        init_file.write_text('"""Module initialization."""\n')
        
        # Create module with various issues
        module_file = subdir / "core.py"
        module_content = '''
"""Core module with intentional linting issues."""

import sys
import os
import json  # unused import

def poorly_formatted_function(x,y,z):
    """Function with formatting issues."""
    result=x+y*z
    if result>100:
        print("Large result")
    return result

class   BadlyFormattedClass:
    """Class with spacing issues."""
    
    def __init__(self,value):
        self.value=value
        
    def method_with_issues(self):
        data={'key':'value','another_key':'another_value'}
        return data

# Missing final newline
'''
        module_file.write_text(module_content)
        
        # Test with mock to avoid external dependencies
        with patch('tools.linting_tool._run_ruff') as mock_ruff:
            with patch('tools.linting_tool._run_black') as mock_black:
                # Simulate realistic linting issues
                mock_ruff.return_value = [
                    LintIssue(
                        file_path=str(module_file),
                        line=6,
                        column=1,
                        severity=LintSeverity.WARNING,
                        code="F401",
                        message="'json' imported but unused",
                        rule="unused-import"
                    ),
                    LintIssue(
                        file_path=str(module_file),
                        line=8,
                        column=1,
                        severity=LintSeverity.ERROR,
                        code="E302",
                        message="expected 2 blank lines, found 1",
                        rule="blank-lines"
                    )
                ]
                
                mock_black.return_value = [
                    LintIssue(
                        file_path=str(module_file),
                        line=1,
                        column=1,
                        severity=LintSeverity.STYLE,
                        code="black",
                        message="File needs black formatting",
                        rule="black-formatting"
                    )
                ]
                
                config = LintConfig(run_mypy=False, run_flake8=False)
                result = run_linting(str(project_path), config)
                
                # Validate comprehensive result
                assert result.total_issues == 3
                assert result.success == False  # Has errors
                assert result.issues_by_severity[LintSeverity.ERROR] == 1
                assert result.issues_by_severity[LintSeverity.WARNING] == 1
                assert result.issues_by_severity[LintSeverity.STYLE] == 1
                assert len(result.tools_run) == 2
                assert result.execution_time > 0
                
                # Check summary content
                assert "Found 3 issues" in result.summary
                assert "❌ 1 error" in result.summary
                assert "⚠️ 1 warning" in result.summary
                assert "💅 1 style" in result.summary


import json  # For JSON parsing tests