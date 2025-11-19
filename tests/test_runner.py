"""
Unified Test Runner

Comprehensive test execution framework with coverage reporting,
AI judge integration, performance benchmarking, and quality assessment.
"""

import asyncio
import pytest
import sys
import os
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import importlib
from concurrent.futures import ThreadPoolExecutor
import coverage
import xml.etree.ElementTree as ET

from tools.test_judge_tool import TestJudgeTool, TestCategory, TestResult, TestSuite


logger = logging.getLogger(__name__)


@dataclass
class TestRunConfig:
    """Configuration for test run."""
    
    # Test selection
    test_paths: List[str] = field(default_factory=lambda: ["tests/"])
    test_patterns: List[str] = field(default_factory=lambda: ["test_*.py"])
    exclude_patterns: List[str] = field(default_factory=list)
    test_categories: List[TestCategory] = field(default_factory=list)  # Empty = all categories
    
    # Execution options
    max_workers: int = 4
    timeout_seconds: int = 300
    fail_fast: bool = False
    verbose: bool = False
    capture_output: bool = True
    
    # Coverage options
    enable_coverage: bool = True
    coverage_threshold: float = 90.0
    coverage_paths: List[str] = field(default_factory=lambda: ["agents/", "tools/", "utilities/", "integrations/", "mcp_servers/"])
    
    # Performance options
    enable_performance_tracking: bool = True
    performance_baseline_file: Optional[str] = None
    performance_threshold_multiplier: float = 1.5  # 50% slower than baseline fails
    
    # AI Judge options
    enable_ai_judge: bool = True
    ai_judge_model: str = "openai:gpt-3.5-turbo"
    
    # Output options
    output_dir: str = "test_results"
    generate_html_report: bool = True
    generate_json_report: bool = True
    generate_junit_xml: bool = True
    
    # Quality gates
    min_pass_rate: float = 0.95  # 95%
    max_failure_rate: float = 0.05  # 5%
    quality_gates: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestExecutionResult:
    """Result of test execution."""
    
    # Basic results
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    error_tests: int = 0
    
    # Timing
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    total_duration_seconds: float = 0.0
    
    # Test details
    test_suites: List[TestSuite] = field(default_factory=list)
    failed_test_details: List[Dict[str, Any]] = field(default_factory=list)
    
    # Coverage
    coverage_percentage: Optional[float] = None
    coverage_report: Optional[Dict[str, Any]] = None
    
    # Performance
    performance_results: Dict[str, Any] = field(default_factory=dict)
    performance_regressions: List[str] = field(default_factory=list)
    
    # AI Judge results
    ai_judgment: Optional[Dict[str, Any]] = None
    
    # Quality assessment
    quality_score: float = 0.0
    quality_gates_passed: Dict[str, bool] = field(default_factory=dict)
    
    # Output files
    generated_reports: List[str] = field(default_factory=list)


class TestRunner:
    """Unified test runner with comprehensive reporting and analysis."""
    
    def __init__(self, config: TestRunConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize coverage if enabled
        self.coverage = None
        if config.enable_coverage:
            self.coverage = coverage.Coverage(
                source=config.coverage_paths,
                omit=["*/tests/*", "*/test_*", "*/__pycache__/*"],
                branch=True
            )
        
        # Initialize AI judge if enabled
        self.ai_judge = None
        if config.enable_ai_judge:
            try:
                self.ai_judge = TestJudgeTool(ai_model=config.ai_judge_model)
            except Exception as e:
                logger.warning(f"Failed to initialize AI judge: {e}")
        
        # Performance baseline
        self.performance_baseline = {}
        if config.performance_baseline_file and Path(config.performance_baseline_file).exists():
            try:
                with open(config.performance_baseline_file, 'r') as f:
                    self.performance_baseline = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load performance baseline: {e}")
    
    async def run_tests(self) -> TestExecutionResult:
        """Run all tests according to configuration."""
        result = TestExecutionResult()
        
        try:
            logger.info("Starting test execution...")
            
            # Start coverage if enabled
            if self.coverage:
                self.coverage.start()
            
            # Execute test suites
            await self._execute_test_suites(result)
            
            # Stop coverage and generate report
            if self.coverage:
                self.coverage.stop()
                await self._generate_coverage_report(result)
            
            # Analyze performance
            if self.config.enable_performance_tracking:
                await self._analyze_performance(result)
            
            # Run AI judge analysis
            if self.ai_judge and result.test_suites:
                await self._run_ai_judge(result)
            
            # Evaluate quality gates
            await self._evaluate_quality_gates(result)
            
            # Generate reports
            await self._generate_reports(result)
            
            # Finalize results
            result.end_time = datetime.now(timezone.utc)
            result.total_duration_seconds = (result.end_time - result.start_time).total_seconds()
            
            logger.info(f"Test execution completed in {result.total_duration_seconds:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"Test execution failed: {e}")
            result.error_tests += 1
            result.end_time = datetime.now(timezone.utc)
            return result
    
    async def _execute_test_suites(self, result: TestExecutionResult):
        """Execute all test suites."""
        
        # Discover test files
        test_files = self._discover_test_files()
        
        if not test_files:
            logger.warning("No test files discovered")
            return
        
        logger.info(f"Discovered {len(test_files)} test files")
        
        # Group tests by category/suite
        test_suites_map = self._group_tests_by_suite(test_files)
        
        # Execute each test suite
        for suite_name, test_paths in test_suites_map.items():
            suite_result = await self._execute_test_suite(suite_name, test_paths)
            result.test_suites.append(suite_result)
            
            # Update aggregate counts
            result.total_tests += suite_result.total_tests
            result.passed_tests += suite_result.passed_tests
            result.failed_tests += suite_result.failed_tests
            result.skipped_tests += suite_result.skipped_tests
            result.error_tests += suite_result.error_tests
    
    def _discover_test_files(self) -> List[Path]:
        """Discover test files based on configuration."""
        test_files = []
        
        for test_path_str in self.config.test_paths:
            test_path = Path(test_path_str)
            
            if not test_path.exists():
                logger.warning(f"Test path does not exist: {test_path}")
                continue
            
            if test_path.is_file():
                if self._should_include_file(test_path):
                    test_files.append(test_path)
            else:
                # Recursively find test files
                for pattern in self.config.test_patterns:
                    for file_path in test_path.rglob(pattern):
                        if self._should_include_file(file_path):
                            test_files.append(file_path)
        
        return test_files
    
    def _should_include_file(self, file_path: Path) -> bool:
        """Check if file should be included in test run."""
        # Check exclude patterns
        for exclude_pattern in self.config.exclude_patterns:
            if exclude_pattern in str(file_path):
                return False
        
        # Check if it's a Python test file
        if not file_path.name.endswith('.py'):
            return False
        
        return True
    
    def _group_tests_by_suite(self, test_files: List[Path]) -> Dict[str, List[Path]]:
        """Group test files by suite/category."""
        suites = {}
        
        for test_file in test_files:
            # Determine suite name from file path
            suite_name = self._determine_suite_name(test_file)
            
            if suite_name not in suites:
                suites[suite_name] = []
            
            suites[suite_name].append(test_file)
        
        return suites
    
    def _determine_suite_name(self, test_file: Path) -> str:
        """Determine test suite name from file path."""
        parts = test_file.parts
        
        # Look for category indicators
        if "integration" in parts:
            return "integration"
        elif "performance" in parts:
            return "performance"
        elif "unit" in parts:
            return "unit"
        elif "e2e" in parts or "end_to_end" in parts:
            return "e2e"
        elif "stress" in parts:
            return "stress"
        elif "memory" in parts:
            return "memory"
        elif "load" in parts:
            return "load"
        elif "telegram" in parts:
            return "telegram"
        else:
            return "unit"  # Default
    
    async def _execute_test_suite(self, suite_name: str, test_paths: List[Path]) -> TestSuite:
        """Execute a single test suite."""
        logger.info(f"Executing test suite: {suite_name}")
        
        # Determine category
        category = TestCategory.UNIT  # Default
        try:
            category = TestCategory(suite_name)
        except ValueError:
            if "integration" in suite_name:
                category = TestCategory.INTEGRATION
            elif "performance" in suite_name:
                category = TestCategory.PERFORMANCE
            elif "e2e" in suite_name:
                category = TestCategory.E2E
        
        suite = TestSuite(name=suite_name, category=category)
        
        # Build pytest command
        pytest_args = self._build_pytest_args(test_paths)
        
        # Execute pytest
        start_time = time.perf_counter()
        
        try:
            # Run pytest and capture results
            result = await self._run_pytest(pytest_args)
            
            # Parse pytest results
            await self._parse_pytest_results(suite, result, test_paths)
            
        except Exception as e:
            logger.error(f"Error executing test suite {suite_name}: {e}")
            # Create error test result
            error_test = TestResult(
                name=f"{suite_name}_execution_error",
                category=category,
                status="error",
                duration_seconds=0.0,
                error_message=str(e)
            )
            suite.tests.append(error_test)
        
        suite.total_duration_seconds = time.perf_counter() - start_time
        suite.calculate_metrics()
        
        logger.info(f"Completed test suite {suite_name}: {suite.passed_tests}/{suite.total_tests} passed")
        
        return suite
    
    def _build_pytest_args(self, test_paths: List[Path]) -> List[str]:
        """Build pytest command arguments."""
        args = ["pytest"]
        
        # Add test paths
        args.extend([str(path) for path in test_paths])
        
        # Add options
        if self.config.verbose:
            args.append("-v")
        
        if self.config.fail_fast:
            args.append("-x")
        
        if not self.config.capture_output:
            args.append("-s")
        
        # Add timeout
        args.extend(["--timeout", str(self.config.timeout_seconds)])
        
        # Add JUnit XML output
        junit_file = self.output_dir / "junit.xml"
        args.extend(["--junitxml", str(junit_file)])
        
        # Add JSON report
        json_file = self.output_dir / "pytest_report.json"
        args.extend(["--json-report", f"--json-report-file={json_file}"])
        
        return args
    
    async def _run_pytest(self, pytest_args: List[str]) -> Dict[str, Any]:
        """Run pytest and return results."""
        try:
            # Run pytest in subprocess
            process = await asyncio.create_subprocess_exec(
                *pytest_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path.cwd()
            )
            
            stdout, stderr = await process.communicate()
            
            result = {
                "returncode": process.returncode,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
            }
            
            # Try to load JSON report
            json_file = self.output_dir / "pytest_report.json"
            if json_file.exists():
                try:
                    with open(json_file) as f:
                        result["json_report"] = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to load pytest JSON report: {e}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to run pytest: {e}")
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": str(e),
                "error": str(e)
            }
    
    async def _parse_pytest_results(
        self, 
        suite: TestSuite, 
        pytest_result: Dict[str, Any], 
        test_paths: List[Path]
    ):
        """Parse pytest results into test suite."""
        
        # Parse JSON report if available
        json_report = pytest_result.get("json_report")
        if json_report:
            await self._parse_json_report(suite, json_report)
            return
        
        # Fallback: parse JUnit XML
        junit_file = self.output_dir / "junit.xml"
        if junit_file.exists():
            await self._parse_junit_xml(suite, junit_file)
            return
        
        # Last resort: parse stdout
        await self._parse_pytest_stdout(suite, pytest_result.get("stdout", ""))
    
    async def _parse_json_report(self, suite: TestSuite, json_report: Dict[str, Any]):
        """Parse pytest JSON report."""
        try:
            for test_data in json_report.get("tests", []):
                test_result = TestResult(
                    name=test_data.get("nodeid", "unknown"),
                    category=suite.category,
                    status=test_data.get("outcome", "unknown").lower(),
                    duration_seconds=test_data.get("duration", 0.0),
                    error_message=self._extract_error_message(test_data)
                )
                
                # Extract performance metrics if available
                if "call" in test_data and "setup" in test_data:
                    call_duration = test_data["call"].get("duration", 0.0)
                    setup_duration = test_data["setup"].get("duration", 0.0)
                    test_result.performance_metrics = {
                        "call_duration_ms": call_duration * 1000,
                        "setup_duration_ms": setup_duration * 1000
                    }
                
                suite.tests.append(test_result)
                
        except Exception as e:
            logger.error(f"Error parsing JSON report: {e}")
    
    async def _parse_junit_xml(self, suite: TestSuite, junit_file: Path):
        """Parse JUnit XML report."""
        try:
            tree = ET.parse(junit_file)
            root = tree.getroot()
            
            for testcase in root.findall(".//testcase"):
                name = testcase.get("name", "unknown")
                classname = testcase.get("classname", "")
                duration = float(testcase.get("time", "0"))
                
                # Determine status
                status = "passed"
                error_message = None
                
                failure = testcase.find("failure")
                error = testcase.find("error")
                skipped = testcase.find("skipped")
                
                if failure is not None:
                    status = "failed"
                    error_message = failure.text
                elif error is not None:
                    status = "error"
                    error_message = error.text
                elif skipped is not None:
                    status = "skipped"
                    error_message = skipped.text
                
                test_result = TestResult(
                    name=f"{classname}::{name}" if classname else name,
                    category=suite.category,
                    status=status,
                    duration_seconds=duration,
                    error_message=error_message
                )
                
                suite.tests.append(test_result)
                
        except Exception as e:
            logger.error(f"Error parsing JUnit XML: {e}")
    
    async def _parse_pytest_stdout(self, suite: TestSuite, stdout: str):
        """Parse pytest stdout as fallback."""
        # Basic parsing of pytest output
        lines = stdout.split('\n')
        
        for line in lines:
            if " PASSED " in line or " FAILED " in line or " ERROR " in line or " SKIPPED " in line:
                parts = line.split()
                if len(parts) >= 2:
                    test_name = parts[0]
                    status = "passed"
                    
                    if " FAILED " in line:
                        status = "failed"
                    elif " ERROR " in line:
                        status = "error"
                    elif " SKIPPED " in line:
                        status = "skipped"
                    
                    test_result = TestResult(
                        name=test_name,
                        category=suite.category,
                        status=status,
                        duration_seconds=0.0  # Not available from stdout
                    )
                    
                    suite.tests.append(test_result)
    
    def _extract_error_message(self, test_data: Dict[str, Any]) -> Optional[str]:
        """Extract error message from pytest test data."""
        if "call" in test_data:
            call_data = test_data["call"]
            if "longrepr" in call_data and call_data["longrepr"]:
                return str(call_data["longrepr"])[:500]  # Limit length
        
        return None
    
    async def _generate_coverage_report(self, result: TestExecutionResult):
        """Generate coverage report."""
        if not self.coverage:
            return
        
        try:
            # Generate coverage data
            self.coverage.save()
            
            # Get coverage percentage
            total_coverage = self.coverage.report(show_missing=False)
            result.coverage_percentage = total_coverage
            
            # Generate HTML report
            html_dir = self.output_dir / "coverage_html"
            self.coverage.html_report(directory=str(html_dir))
            result.generated_reports.append(str(html_dir / "index.html"))
            
            # Generate detailed coverage data
            coverage_data = {}
            for filename in self.coverage.get_data().measured_files():
                analysis = self.coverage.analysis2(filename)
                coverage_data[filename] = {
                    "statements": len(analysis.statements),
                    "missing": len(analysis.missing),
                    "excluded": len(analysis.excluded),
                    "coverage": (len(analysis.statements) - len(analysis.missing)) / len(analysis.statements) * 100 if analysis.statements else 100
                }
            
            result.coverage_report = coverage_data
            
            logger.info(f"Coverage: {total_coverage:.1f}%")
            
        except Exception as e:
            logger.error(f"Error generating coverage report: {e}")
    
    async def _analyze_performance(self, result: TestExecutionResult):
        """Analyze performance test results."""
        performance_data = {
            "suite_durations": {},
            "slow_tests": [],
            "performance_regressions": []
        }
        
        for suite in result.test_suites:
            performance_data["suite_durations"][suite.name] = suite.total_duration_seconds
            
            # Find slow tests
            for test in suite.tests:
                if test.duration_seconds > 5.0:  # Tests slower than 5 seconds
                    performance_data["slow_tests"].append({
                        "name": test.name,
                        "duration": test.duration_seconds,
                        "suite": suite.name
                    })
                
                # Check for performance regressions
                baseline_key = f"{suite.name}::{test.name}"
                if baseline_key in self.performance_baseline:
                    baseline_duration = self.performance_baseline[baseline_key]
                    threshold = baseline_duration * self.config.performance_threshold_multiplier
                    
                    if test.duration_seconds > threshold:
                        regression = {
                            "test": test.name,
                            "current": test.duration_seconds,
                            "baseline": baseline_duration,
                            "regression": (test.duration_seconds / baseline_duration - 1) * 100
                        }
                        performance_data["performance_regressions"].append(regression)
                        result.performance_regressions.append(
                            f"{test.name}: {regression['regression']:.1f}% slower than baseline"
                        )
        
        result.performance_results = performance_data
        
        # Save new baseline
        if self.config.performance_baseline_file:
            new_baseline = {}
            for suite in result.test_suites:
                for test in suite.tests:
                    if test.status == "passed":  # Only save baselines for passing tests
                        key = f"{suite.name}::{test.name}"
                        new_baseline[key] = test.duration_seconds
            
            try:
                with open(self.config.performance_baseline_file, 'w') as f:
                    json.dump(new_baseline, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save performance baseline: {e}")
    
    async def _run_ai_judge(self, result: TestExecutionResult):
        """Run AI judge analysis on test results."""
        if not self.ai_judge:
            return
        
        try:
            # Prepare data for AI judge
            test_suites_data = []
            
            for suite in result.test_suites:
                suite_data = {
                    "name": suite.name,
                    "category": suite.category.value,
                    "coverage_percentage": suite.coverage_percentage,
                    "tests": []
                }
                
                for test in suite.tests:
                    test_data = {
                        "name": test.name,
                        "status": test.status,
                        "duration_seconds": test.duration_seconds,
                        "error_message": test.error_message,
                        "performance_metrics": test.performance_metrics,
                        "memory_usage_mb": test.memory_usage_mb,
                        "metadata": test.metadata
                    }
                    suite_data["tests"].append(test_data)
                
                test_suites_data.append(suite_data)
            
            # Run AI judge
            judge_input = {
                "test_suites": test_suites_data,
                "quality_gates": self.config.quality_gates,
                "performance_benchmarks": {},
                "system_context": {
                    "total_test_time": result.total_duration_seconds,
                    "coverage": result.coverage_percentage
                }
            }
            
            from tools.test_judge_tool import TestJudgeInput, ToolContext
            
            judge_result = await self.ai_judge.execute(
                TestJudgeInput(**judge_input),
                ToolContext()
            )
            
            result.ai_judgment = {
                "overall_score": judge_result.quality_score,
                "judgment": judge_result.judgment.__dict__ if hasattr(judge_result.judgment, '__dict__') else str(judge_result.judgment),
                "actionable_items": judge_result.actionable_items,
                "detailed_metrics": judge_result.detailed_metrics
            }
            
            result.quality_score = judge_result.quality_score
            
            logger.info(f"AI Judge Score: {judge_result.quality_score:.1f}/10")
            
        except Exception as e:
            logger.error(f"Error running AI judge: {e}")
    
    async def _evaluate_quality_gates(self, result: TestExecutionResult):
        """Evaluate quality gates against test results."""
        gates = {}
        
        # Pass rate gate
        pass_rate = result.passed_tests / result.total_tests if result.total_tests > 0 else 0.0
        gates["pass_rate"] = pass_rate >= self.config.min_pass_rate
        
        # Failure rate gate
        failure_rate = (result.failed_tests + result.error_tests) / result.total_tests if result.total_tests > 0 else 0.0
        gates["failure_rate"] = failure_rate <= self.config.max_failure_rate
        
        # Coverage gate
        if result.coverage_percentage is not None:
            gates["coverage"] = result.coverage_percentage >= self.config.coverage_threshold
        
        # Performance regression gate
        gates["performance"] = len(result.performance_regressions) == 0
        
        # Custom quality gates
        for gate_name, gate_config in self.config.quality_gates.items():
            # Example: {"min_integration_tests": 10}
            if gate_name.startswith("min_") and gate_name.endswith("_tests"):
                category_name = gate_name[4:-6]  # Extract category from "min_X_tests"
                try:
                    category = TestCategory(category_name)
                    category_suites = [s for s in result.test_suites if s.category == category]
                    total_category_tests = sum(s.total_tests for s in category_suites)
                    gates[gate_name] = total_category_tests >= gate_config
                except ValueError:
                    pass  # Invalid category
        
        result.quality_gates_passed = gates
        
        passed_gates = sum(gates.values())
        total_gates = len(gates)
        gate_pass_rate = passed_gates / total_gates if total_gates > 0 else 1.0
        
        logger.info(f"Quality Gates: {passed_gates}/{total_gates} passed ({gate_pass_rate:.1%})")
        
        for gate_name, passed in gates.items():
            if not passed:
                logger.warning(f"Quality gate failed: {gate_name}")
    
    async def _generate_reports(self, result: TestExecutionResult):
        """Generate all requested reports."""
        
        # Generate JSON report
        if self.config.generate_json_report:
            await self._generate_json_report(result)
        
        # Generate HTML report
        if self.config.generate_html_report:
            await self._generate_html_report(result)
        
        logger.info(f"Generated {len(result.generated_reports)} report files")
    
    async def _generate_json_report(self, result: TestExecutionResult):
        """Generate comprehensive JSON report."""
        report_data = {
            "meta": {
                "timestamp": result.start_time.isoformat(),
                "duration_seconds": result.total_duration_seconds,
                "runner_version": "1.0.0"
            },
            "summary": {
                "total_tests": result.total_tests,
                "passed_tests": result.passed_tests,
                "failed_tests": result.failed_tests,
                "skipped_tests": result.skipped_tests,
                "error_tests": result.error_tests,
                "pass_rate": result.passed_tests / result.total_tests if result.total_tests > 0 else 0.0
            },
            "coverage": {
                "percentage": result.coverage_percentage,
                "details": result.coverage_report
            },
            "performance": result.performance_results,
            "quality_gates": result.quality_gates_passed,
            "ai_judgment": result.ai_judgment,
            "test_suites": []
        }
        
        # Add detailed test suite data
        for suite in result.test_suites:
            suite_data = {
                "name": suite.name,
                "category": suite.category.value,
                "total_tests": suite.total_tests,
                "passed_tests": suite.passed_tests,
                "failed_tests": suite.failed_tests,
                "duration_seconds": suite.total_duration_seconds,
                "tests": []
            }
            
            for test in suite.tests:
                test_data = {
                    "name": test.name,
                    "status": test.status,
                    "duration_seconds": test.duration_seconds,
                    "error_message": test.error_message,
                    "performance_metrics": test.performance_metrics
                }
                suite_data["tests"].append(test_data)
            
            report_data["test_suites"].append(suite_data)
        
        # Save JSON report
        json_file = self.output_dir / "test_report.json"
        with open(json_file, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        result.generated_reports.append(str(json_file))
    
    async def _generate_html_report(self, result: TestExecutionResult):
        """Generate HTML report."""
        html_content = self._create_html_report_content(result)
        
        html_file = self.output_dir / "test_report.html"
        with open(html_file, 'w') as f:
            f.write(html_content)
        
        result.generated_reports.append(str(html_file))
    
    def _create_html_report_content(self, result: TestExecutionResult) -> str:
        """Create HTML report content."""
        
        pass_rate = result.passed_tests / result.total_tests if result.total_tests > 0 else 0.0
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>AI Rebuild Test Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .summary {{ display: flex; gap: 20px; margin: 20px 0; }}
        .metric {{ background: white; padding: 15px; border: 1px solid #ddd; border-radius: 5px; flex: 1; }}
        .passed {{ background: #d4edda; color: #155724; }}
        .failed {{ background: #f8d7da; color: #721c24; }}
        .table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        .table th, .table td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        .table th {{ background: #f0f0f0; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>AI Rebuild Test Report</h1>
        <p>Generated: {result.start_time.isoformat()}</p>
        <p>Duration: {result.total_duration_seconds:.2f} seconds</p>
    </div>
    
    <div class="summary">
        <div class="metric">
            <h3>Total Tests</h3>
            <h2>{result.total_tests}</h2>
        </div>
        <div class="metric passed">
            <h3>Passed</h3>
            <h2>{result.passed_tests}</h2>
        </div>
        <div class="metric failed">
            <h3>Failed</h3>
            <h2>{result.failed_tests + result.error_tests}</h2>
        </div>
        <div class="metric">
            <h3>Pass Rate</h3>
            <h2>{pass_rate:.1%}</h2>
        </div>
    </div>
    
    {"<div class='metric'><h3>Coverage</h3><h2>" + f"{result.coverage_percentage:.1f}%" + "</h2></div>" if result.coverage_percentage else ""}
    
    {"<div class='metric'><h3>Quality Score</h3><h2>" + f"{result.quality_score:.1f}/10" + "</h2></div>" if result.quality_score > 0 else ""}
    
    <h2>Test Suites</h2>
    <table class="table">
        <tr>
            <th>Suite</th>
            <th>Category</th>
            <th>Tests</th>
            <th>Passed</th>
            <th>Failed</th>
            <th>Duration (s)</th>
        </tr>
"""
        
        for suite in result.test_suites:
            html += f"""
        <tr>
            <td>{suite.name}</td>
            <td>{suite.category.value}</td>
            <td>{suite.total_tests}</td>
            <td>{suite.passed_tests}</td>
            <td>{suite.failed_tests + suite.error_tests}</td>
            <td>{suite.total_duration_seconds:.2f}</td>
        </tr>
"""
        
        html += """
    </table>
    
    <h2>Quality Gates</h2>
    <table class="table">
        <tr><th>Gate</th><th>Status</th></tr>
"""
        
        for gate_name, passed in result.quality_gates_passed.items():
            status = "PASSED" if passed else "FAILED"
            status_class = "passed" if passed else "failed"
            html += f'<tr><td>{gate_name}</td><td class="{status_class}">{status}</td></tr>'
        
        html += """
    </table>
</body>
</html>
"""
        
        return html
    
    def print_summary(self, result: TestExecutionResult):
        """Print test run summary to console."""
        print("\n" + "="*60)
        print("TEST EXECUTION SUMMARY")
        print("="*60)
        print(f"Total Tests: {result.total_tests}")
        print(f"Passed: {result.passed_tests}")
        print(f"Failed: {result.failed_tests}")
        print(f"Errors: {result.error_tests}")
        print(f"Skipped: {result.skipped_tests}")
        
        if result.total_tests > 0:
            pass_rate = result.passed_tests / result.total_tests
            print(f"Pass Rate: {pass_rate:.1%}")
        
        print(f"Duration: {result.total_duration_seconds:.2f} seconds")
        
        if result.coverage_percentage:
            print(f"Coverage: {result.coverage_percentage:.1f}%")
        
        if result.quality_score > 0:
            print(f"Quality Score: {result.quality_score:.1f}/10")
        
        print("\nQuality Gates:")
        for gate_name, passed in result.quality_gates_passed.items():
            status = "PASSED" if passed else "FAILED"
            print(f"  {gate_name}: {status}")
        
        if result.performance_regressions:
            print("\nPerformance Regressions:")
            for regression in result.performance_regressions:
                print(f"  ⚠️  {regression}")
        
        if result.generated_reports:
            print("\nGenerated Reports:")
            for report in result.generated_reports:
                print(f"  📊 {report}")
        
        print("="*60)


async def main():
    """Main entry point for test runner."""
    import argparse
    
    parser = argparse.ArgumentParser(description="AI Rebuild Test Runner")
    parser.add_argument("--paths", nargs="+", default=["tests/"], help="Test paths")
    parser.add_argument("--coverage", action="store_true", help="Enable coverage reporting")
    parser.add_argument("--ai-judge", action="store_true", help="Enable AI judge analysis")
    parser.add_argument("--output-dir", default="test_results", help="Output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--categories", nargs="+", help="Test categories to run")
    
    args = parser.parse_args()
    
    # Create configuration
    config = TestRunConfig(
        test_paths=args.paths,
        enable_coverage=args.coverage,
        enable_ai_judge=args.ai_judge,
        output_dir=args.output_dir,
        verbose=args.verbose
    )
    
    if args.categories:
        config.test_categories = [TestCategory(cat) for cat in args.categories]
    
    # Run tests
    runner = TestRunner(config)
    result = await runner.run_tests()
    
    # Print summary
    runner.print_summary(result)
    
    # Exit with appropriate code
    if result.failed_tests > 0 or result.error_tests > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())