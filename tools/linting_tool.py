"""Code linting and formatting tool for automated code quality checks."""

import subprocess
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from enum import Enum


class LintSeverity(str, Enum):
    """Lint issue severity levels."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    STYLE = "style"


class LintIssue(BaseModel):
    """Individual linting issue."""
    file_path: str
    line: int
    column: int
    severity: LintSeverity
    code: str
    message: str
    rule: Optional[str] = None


class LintResult(BaseModel):
    """Complete linting result."""
    success: bool
    total_issues: int
    issues_by_severity: Dict[LintSeverity, int]
    issues: List[LintIssue]
    execution_time: float
    tools_run: List[str]
    summary: str


class LintConfig(BaseModel):
    """Configuration for linting tools."""
    run_ruff: bool = Field(default=True, description="Run ruff linter")
    run_black: bool = Field(default=True, description="Run black formatter check")
    run_mypy: bool = Field(default=False, description="Run mypy type checker")
    run_flake8: bool = Field(default=False, description="Run flake8 linter")
    fix_issues: bool = Field(default=False, description="Automatically fix fixable issues")
    target_files: Optional[List[str]] = Field(default=None, description="Specific files to lint")
    ignore_patterns: List[str] = Field(default_factory=list, description="Patterns to ignore")


def run_linting(
    project_path: str,
    config: Optional[LintConfig] = None
) -> LintResult:
    """
    Run comprehensive linting on Python codebase.
    
    Executes multiple linting tools (ruff, black, mypy, flake8) and aggregates results.
    Useful for automated code quality validation and CI/CD pipelines.
    """
    if config is None:
        config = LintConfig()
    
    import time
    start_time = time.time()
    
    all_issues = []
    tools_run = []
    
    project_path_obj = Path(project_path)
    if not project_path_obj.exists():
        raise FileNotFoundError(f"Project path does not exist: {project_path}")
    
    # Run ruff linter
    if config.run_ruff:
        ruff_issues = _run_ruff(project_path_obj, config)
        all_issues.extend(ruff_issues)
        tools_run.append("ruff")
    
    # Run black formatter check
    if config.run_black:
        black_issues = _run_black(project_path_obj, config)
        all_issues.extend(black_issues)
        tools_run.append("black")
    
    # Run mypy type checker
    if config.run_mypy:
        mypy_issues = _run_mypy(project_path_obj, config)
        all_issues.extend(mypy_issues)
        tools_run.append("mypy")
    
    # Run flake8 linter
    if config.run_flake8:
        flake8_issues = _run_flake8(project_path_obj, config)
        all_issues.extend(flake8_issues)
        tools_run.append("flake8")
    
    execution_time = time.time() - start_time
    
    # Aggregate results
    issues_by_severity = {severity: 0 for severity in LintSeverity}
    for issue in all_issues:
        issues_by_severity[issue.severity] += 1
    
    success = issues_by_severity[LintSeverity.ERROR] == 0
    total_issues = len(all_issues)
    
    summary = _generate_summary(issues_by_severity, tools_run, execution_time)
    
    return LintResult(
        success=success,
        total_issues=total_issues,
        issues_by_severity=issues_by_severity,
        issues=all_issues,
        execution_time=execution_time,
        tools_run=tools_run,
        summary=summary
    )


def lint_files(
    file_paths: List[str],
    config: Optional[LintConfig] = None
) -> LintResult:
    """Lint specific files rather than entire project."""
    if config is None:
        config = LintConfig()
    
    config.target_files = file_paths
    
    # Use the parent directory of the first file as project path
    if file_paths:
        project_path = str(Path(file_paths[0]).parent)
    else:
        project_path = "."
    
    return run_linting(project_path, config)


def quick_lint_check(file_path: str) -> bool:
    """Quick pass/fail lint check for a single file."""
    result = lint_files([file_path])
    return result.success


def _run_ruff(project_path: Path, config: LintConfig) -> List[LintIssue]:
    """Run ruff linter and parse results."""
    try:
        cmd = ["ruff", "check", "--output-format=json"]
        
        if config.target_files:
            cmd.extend(config.target_files)
        else:
            cmd.append(str(project_path))
        
        if config.fix_issues:
            cmd.append("--fix")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=project_path
        )
        
        issues = []
        if result.stdout:
            try:
                ruff_output = json.loads(result.stdout)
                for item in ruff_output:
                    issues.append(LintIssue(
                        file_path=item["filename"],
                        line=item["location"]["row"],
                        column=item["location"]["column"],
                        severity=LintSeverity.ERROR if item["severity"] == "error" else LintSeverity.WARNING,
                        code=item["code"],
                        message=item["message"],
                        rule=item.get("rule")
                    ))
            except json.JSONDecodeError:
                pass
        
        return issues
        
    except FileNotFoundError:
        return []


def _run_black(project_path: Path, config: LintConfig) -> List[LintIssue]:
    """Run black formatter check and parse results."""
    try:
        cmd = ["black", "--check", "--diff"]
        
        if config.target_files:
            cmd.extend(config.target_files)
        else:
            cmd.append(str(project_path))
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=project_path
        )
        
        issues = []
        if result.returncode != 0 and result.stdout:
            # Parse diff output to identify files that need formatting
            lines = result.stdout.split('\n')
            current_file = None
            
            for line in lines:
                if line.startswith("--- "):
                    current_file = line.split("--- ")[1].split("\t")[0]
                elif line.startswith("+++") and current_file:
                    issues.append(LintIssue(
                        file_path=current_file,
                        line=1,
                        column=1,
                        severity=LintSeverity.STYLE,
                        code="black",
                        message="File needs black formatting",
                        rule="black-formatting"
                    ))
        
        return issues
        
    except FileNotFoundError:
        return []


def _run_mypy(project_path: Path, config: LintConfig) -> List[LintIssue]:
    """Run mypy type checker and parse results."""
    try:
        cmd = ["mypy", "--show-error-codes", "--no-error-summary"]
        
        if config.target_files:
            cmd.extend(config.target_files)
        else:
            cmd.append(str(project_path))
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=project_path
        )
        
        issues = []
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if ':' in line and '(' in line and ')' in line:
                    try:
                        # Parse mypy output format: file:line:column: severity: message [code]
                        parts = line.split(':', 3)
                        if len(parts) >= 4:
                            file_path = parts[0]
                            line_num = int(parts[1])
                            col_num = int(parts[2]) if parts[2].isdigit() else 1
                            
                            rest = parts[3].strip()
                            if rest.startswith('error:'):
                                severity = LintSeverity.ERROR
                                message = rest[6:].strip()
                            elif rest.startswith('warning:'):
                                severity = LintSeverity.WARNING
                                message = rest[8:].strip()
                            else:
                                severity = LintSeverity.INFO
                                message = rest
                            
                            # Extract error code if present
                            code = "mypy"
                            if '[' in message and ']' in message:
                                code_match = message[message.rfind('['):message.rfind(']')+1]
                                code = code_match.strip('[]')
                                message = message[:message.rfind('[')].strip()
                            
                            issues.append(LintIssue(
                                file_path=file_path,
                                line=line_num,
                                column=col_num,
                                severity=severity,
                                code=code,
                                message=message,
                                rule="mypy-type-check"
                            ))
                    except (ValueError, IndexError):
                        continue
        
        return issues
        
    except FileNotFoundError:
        return []


def _run_flake8(project_path: Path, config: LintConfig) -> List[LintIssue]:
    """Run flake8 linter and parse results."""
    try:
        cmd = ["flake8", "--format=%(path)s:%(row)d:%(col)d:%(code)s:%(text)s"]
        
        if config.target_files:
            cmd.extend(config.target_files)
        else:
            cmd.append(str(project_path))
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=project_path
        )
        
        issues = []
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if ':' in line:
                    try:
                        parts = line.split(':', 4)
                        if len(parts) >= 5:
                            issues.append(LintIssue(
                                file_path=parts[0],
                                line=int(parts[1]),
                                column=int(parts[2]),
                                severity=LintSeverity.WARNING,  # Flake8 issues are typically warnings
                                code=parts[3],
                                message=parts[4],
                                rule="flake8"
                            ))
                    except (ValueError, IndexError):
                        continue
        
        return issues
        
    except FileNotFoundError:
        return []


def _generate_summary(
    issues_by_severity: Dict[LintSeverity, int],
    tools_run: List[str],
    execution_time: float
) -> str:
    """Generate human-readable summary of linting results."""
    total_issues = sum(issues_by_severity.values())
    
    if total_issues == 0:
        return f"✅ All checks passed! Ran {', '.join(tools_run)} in {execution_time:.2f}s"
    
    summary_parts = [
        f"Found {total_issues} issues in {execution_time:.2f}s using {', '.join(tools_run)}:"
    ]
    
    for severity, count in issues_by_severity.items():
        if count > 0:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "style": "💅"}[severity.value]
            summary_parts.append(f"{icon} {count} {severity.value}{'s' if count != 1 else ''}")
    
    return " ".join(summary_parts)


# Convenience functions for specific linting scenarios
def lint_python_project(project_path: str, fix_formatting: bool = False) -> LintResult:
    """Comprehensive Python project linting with ruff and black."""
    config = LintConfig(
        run_ruff=True,
        run_black=True,
        run_mypy=False,  # mypy can be slow and requires configuration
        run_flake8=False,  # ruff covers most flake8 rules
        fix_issues=fix_formatting
    )
    
    return run_linting(project_path, config)


def strict_lint_check(project_path: str) -> LintResult:
    """Strict linting with all tools enabled."""
    config = LintConfig(
        run_ruff=True,
        run_black=True,
        run_mypy=True,
        run_flake8=True,
        fix_issues=False
    )
    
    return run_linting(project_path, config)


def quick_format_check(file_path: str) -> bool:
    """Quick check if a file needs formatting (black only)."""
    config = LintConfig(
        run_ruff=False,
        run_black=True,
        run_mypy=False,
        run_flake8=False
    )
    
    result = lint_files([file_path], config)
    return result.success