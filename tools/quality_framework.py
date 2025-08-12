"""
Quality Framework - Comprehensive Tool Testing and Benchmarking

Advanced quality assurance framework for AI tools with automated testing,
performance benchmarking, and continuous quality monitoring.

Features:
- Automated test suite generation and execution
- Performance benchmarking with statistical analysis
- Quality metric collection and trend analysis
- AI-powered test result evaluation
- Regression detection and alerting
- Comprehensive reporting and visualization
"""

import asyncio
import json
import statistics
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Type, Union, Tuple
import logging
import hashlib

from pydantic import BaseModel, Field
import httpx

from .base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, QualityScore, PerformanceMetrics
)


class TestType(Enum):
    """Types of tests that can be performed."""
    
    FUNCTIONAL = auto()          # Basic functionality tests
    PERFORMANCE = auto()         # Performance benchmarking
    STRESS = auto()             # Stress and load testing
    INTEGRATION = auto()        # Integration testing
    REGRESSION = auto()         # Regression testing
    SECURITY = auto()           # Security testing
    USABILITY = auto()          # Usability testing
    COMPATIBILITY = auto()      # Compatibility testing


class TestStatus(Enum):
    """Test execution status."""
    
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class BenchmarkCategory(Enum):
    """Benchmark categories for performance testing."""
    
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    ACCURACY = "accuracy"
    RESOURCE_USAGE = "resource_usage"
    SCALABILITY = "scalability"
    RELIABILITY = "reliability"


@dataclass
class TestCase:
    """Individual test case definition."""
    
    id: str
    name: str
    description: str
    test_type: TestType
    input_data: Dict[str, Any]
    expected_output: Optional[Dict[str, Any]] = None
    validation_function: Optional[Callable] = None
    timeout_seconds: int = 60
    retry_count: int = 0
    tags: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)
    cleanup_function: Optional[Callable] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestResult:
    """Result of a test case execution."""
    
    test_case_id: str
    status: TestStatus
    execution_time_ms: float
    start_time: datetime
    end_time: datetime
    
    # Results
    actual_output: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    
    # Quality metrics
    quality_score: Optional[QualityScore] = None
    performance_metrics: Optional[PerformanceMetrics] = None
    
    # Validation results
    assertions_passed: int = 0
    assertions_failed: int = 0
    validation_details: Dict[str, Any] = field(default_factory=dict)
    
    # Additional metadata
    environment_info: Dict[str, Any] = field(default_factory=dict)
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def passed(self) -> bool:
        """Check if test passed."""
        return self.status == TestStatus.PASSED
    
    @property
    def duration(self) -> timedelta:
        """Get test duration."""
        return self.end_time - self.start_time


@dataclass
class BenchmarkResult:
    """Performance benchmark result."""
    
    category: BenchmarkCategory
    metric_name: str
    value: float
    unit: str
    timestamp: datetime
    
    # Statistical data
    sample_count: int = 1
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mean_value: Optional[float] = None
    std_deviation: Optional[float] = None
    percentiles: Dict[str, float] = field(default_factory=dict)
    
    # Context
    test_conditions: Dict[str, Any] = field(default_factory=dict)
    baseline_comparison: Optional[Dict[str, Any]] = None


class TestSuite:
    """Collection of test cases for a tool."""
    
    def __init__(
        self, 
        name: str, 
        tool_class: Type[ToolImplementation],
        description: str = ""
    ):
        self.name = name
        self.tool_class = tool_class
        self.description = description
        self.test_cases: List[TestCase] = []
        self.setup_functions: List[Callable] = []
        self.teardown_functions: List[Callable] = []
        self.shared_context: Dict[str, Any] = {}
    
    def add_test_case(self, test_case: TestCase) -> None:
        """Add a test case to the suite."""
        self.test_cases.append(test_case)
    
    def add_setup(self, func: Callable) -> None:
        """Add a setup function."""
        self.setup_functions.append(func)
    
    def add_teardown(self, func: Callable) -> None:
        """Add a teardown function."""
        self.teardown_functions.append(func)
    
    def get_tests_by_type(self, test_type: TestType) -> List[TestCase]:
        """Get all test cases of a specific type."""
        return [tc for tc in self.test_cases if tc.test_type == test_type]
    
    def get_tests_by_tags(self, tags: List[str]) -> List[TestCase]:
        """Get test cases that have any of the specified tags."""
        return [tc for tc in self.test_cases if any(tag in tc.tags for tag in tags)]


class QualityAssessmentEngine:
    """AI-powered quality assessment engine."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = httpx.AsyncClient(timeout=60.0) if self.api_key else None
    
    async def assess_test_result(
        self, 
        test_case: TestCase, 
        result: TestResult,
        tool_instance: ToolImplementation
    ) -> QualityScore:
        """Assess the quality of a test result using AI."""
        
        quality_score = QualityScore(overall_score=7.0)
        
        # Basic assessment based on test status
        if result.status == TestStatus.PASSED:
            quality_score.add_dimension(QualityMetric.RELIABILITY, 8.5)
        elif result.status == TestStatus.FAILED:
            quality_score.add_dimension(QualityMetric.RELIABILITY, 3.0, 
                                      "Test failed - investigate root cause")
        elif result.status == TestStatus.ERROR:
            quality_score.add_dimension(QualityMetric.RELIABILITY, 2.0,
                                      "Test encountered error - review implementation")
        
        # Performance assessment
        if result.execution_time_ms < 1000:  # Under 1 second
            quality_score.add_dimension(QualityMetric.PERFORMANCE, 9.0)
        elif result.execution_time_ms < 5000:  # Under 5 seconds
            quality_score.add_dimension(QualityMetric.PERFORMANCE, 7.5)
        else:
            quality_score.add_dimension(QualityMetric.PERFORMANCE, 6.0,
                                      "Slow execution time needs optimization")
        
        # AI-powered assessment if available
        if self.client and self.api_key:
            try:
                ai_assessment = await self._get_ai_quality_assessment(
                    test_case, result, tool_instance
                )
                self._merge_ai_assessment(quality_score, ai_assessment)
            except Exception as e:
                logging.warning(f"AI assessment failed: {str(e)}")
        
        return quality_score
    
    async def _get_ai_quality_assessment(
        self, 
        test_case: TestCase, 
        result: TestResult,
        tool_instance: ToolImplementation
    ) -> Dict[str, Any]:
        """Get AI-powered quality assessment."""
        
        prompt = f"""
        Analyze the following test execution result and provide quality assessment:
        
        Tool: {tool_instance.name}
        Test Case: {test_case.name}
        Description: {test_case.description}
        
        Input: {json.dumps(test_case.input_data, indent=2)}
        Expected Output: {json.dumps(test_case.expected_output, indent=2)}
        Actual Output: {json.dumps(result.actual_output, indent=2)}
        
        Execution Time: {result.execution_time_ms}ms
        Status: {result.status.value}
        Error: {result.error_message if result.error_message else "None"}
        
        Assess the quality on these dimensions (0-10 scale):
        1. Accuracy - How well does the output match expectations?
        2. Performance - Is the execution time acceptable?
        3. Reliability - Did the test execute without errors?
        4. Usability - Is the output format appropriate?
        
        Provide specific improvement suggestions.
        
        Respond in JSON format with:
        {
            "accuracy": <score>,
            "performance": <score>,
            "reliability": <score>,
            "usability": <score>,
            "suggestions": ["suggestion1", "suggestion2", ...],
            "confidence": <0-1>
        }
        """
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
            "temperature": 0.1
        }
        
        response = await self.client.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload,
            headers=headers
        )
        
        response.raise_for_status()
        data = response.json()
        
        content = data["choices"][0]["message"]["content"]
        
        # Parse JSON response
        try:
            return json.loads(content)
        except:
            # Fallback parsing
            return {"confidence": 0.5, "suggestions": ["AI assessment parsing failed"]}
    
    def _merge_ai_assessment(self, quality_score: QualityScore, ai_assessment: Dict[str, Any]):
        """Merge AI assessment into quality score."""
        
        confidence = ai_assessment.get("confidence", 0.5)
        
        # Only use AI assessment if confidence is reasonable
        if confidence >= 0.6:
            for metric_name, score in ai_assessment.items():
                if isinstance(score, (int, float)) and 0 <= score <= 10:
                    if metric_name == "accuracy":
                        quality_score.add_dimension(QualityMetric.ACCURACY, score)
                    elif metric_name == "performance":
                        quality_score.add_dimension(QualityMetric.PERFORMANCE, score)
                    elif metric_name == "reliability":
                        quality_score.add_dimension(QualityMetric.RELIABILITY, score)
                    elif metric_name == "usability":
                        quality_score.add_dimension(QualityMetric.USABILITY, score)
            
            # Add AI suggestions
            suggestions = ai_assessment.get("suggestions", [])
            quality_score.improvement_suggestions.extend(suggestions)
    
    async def close(self):
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()


class TestExecutor:
    """Test execution engine with comprehensive reporting."""
    
    def __init__(self, quality_engine: Optional[QualityAssessmentEngine] = None):
        self.quality_engine = quality_engine or QualityAssessmentEngine()
        self.logger = logging.getLogger(__name__)
        
        # Execution tracking
        self.execution_history: List[Dict[str, Any]] = []
        self.benchmark_history: List[BenchmarkResult] = []
        
        # Configuration
        self.parallel_execution = True
        self.max_parallel_tests = 5
        self.continue_on_failure = True
        
    async def execute_test_suite(
        self, 
        test_suite: TestSuite,
        tool_instance: ToolImplementation,
        context: Optional[ToolContext] = None
    ) -> Dict[str, Any]:
        """Execute a complete test suite."""
        
        start_time = datetime.utcnow()
        context = context or ToolContext()
        
        self.logger.info(f"Starting test suite: {test_suite.name}")
        
        try:
            # Run setup functions
            for setup_func in test_suite.setup_functions:
                try:
                    if asyncio.iscoroutinefunction(setup_func):
                        await setup_func(test_suite.shared_context)
                    else:
                        setup_func(test_suite.shared_context)
                except Exception as e:
                    self.logger.error(f"Setup function failed: {str(e)}")
                    if not self.continue_on_failure:
                        raise
            
            # Execute test cases
            if self.parallel_execution:
                test_results = await self._execute_tests_parallel(
                    test_suite.test_cases, tool_instance, context
                )
            else:
                test_results = await self._execute_tests_sequential(
                    test_suite.test_cases, tool_instance, context
                )
            
            # Run teardown functions
            for teardown_func in test_suite.teardown_functions:
                try:
                    if asyncio.iscoroutinefunction(teardown_func):
                        await teardown_func(test_suite.shared_context)
                    else:
                        teardown_func(test_suite.shared_context)
                except Exception as e:
                    self.logger.error(f"Teardown function failed: {str(e)}")
            
            # Generate summary
            end_time = datetime.utcnow()
            summary = self._generate_execution_summary(
                test_suite, test_results, start_time, end_time
            )
            
            # Store in history
            execution_record = {
                "timestamp": start_time,
                "test_suite": test_suite.name,
                "tool": tool_instance.name,
                "summary": summary,
                "results": test_results
            }
            self.execution_history.append(execution_record)
            
            return summary
            
        except Exception as e:
            self.logger.error(f"Test suite execution failed: {str(e)}")
            raise ToolError(
                f"Test suite execution failed: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"test_suite": test_suite.name}
            )
    
    async def _execute_tests_parallel(
        self, 
        test_cases: List[TestCase], 
        tool_instance: ToolImplementation,
        context: ToolContext
    ) -> List[TestResult]:
        """Execute test cases in parallel."""
        
        semaphore = asyncio.Semaphore(self.max_parallel_tests)
        
        async def execute_with_semaphore(test_case: TestCase) -> TestResult:
            async with semaphore:
                return await self._execute_single_test(test_case, tool_instance, context)
        
        tasks = [execute_with_semaphore(tc) for tc in test_cases]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _execute_tests_sequential(
        self, 
        test_cases: List[TestCase], 
        tool_instance: ToolImplementation,
        context: ToolContext
    ) -> List[TestResult]:
        """Execute test cases sequentially."""
        
        results = []
        for test_case in test_cases:
            result = await self._execute_single_test(test_case, tool_instance, context)
            results.append(result)
            
            # Stop on failure if configured
            if not self.continue_on_failure and not result.passed:
                break
        
        return results
    
    async def _execute_single_test(
        self, 
        test_case: TestCase, 
        tool_instance: ToolImplementation,
        context: ToolContext
    ) -> TestResult:
        """Execute a single test case."""
        
        start_time = datetime.utcnow()
        execution_start = time.time()
        
        self.logger.info(f"Executing test: {test_case.name}")
        
        result = TestResult(
            test_case_id=test_case.id,
            status=TestStatus.RUNNING,
            execution_time_ms=0,
            start_time=start_time,
            end_time=start_time  # Will be updated
        )
        
        try:
            # Check prerequisites
            if not await self._check_prerequisites(test_case):
                result.status = TestStatus.SKIPPED
                result.error_message = "Prerequisites not met"
                return result
            
            # Execute the tool with test input
            input_model = tool_instance.input_model.parse_obj(test_case.input_data)
            
            # Set timeout
            try:
                output = await asyncio.wait_for(
                    tool_instance.execute(input_model, context),
                    timeout=test_case.timeout_seconds
                )
                result.actual_output = output.dict()
                result.quality_score = output.quality_score
                result.performance_metrics = output.performance_metrics
                
            except asyncio.TimeoutError:
                result.status = TestStatus.ERROR
                result.error_message = f"Test timed out after {test_case.timeout_seconds} seconds"
                return result
            
            # Validate results
            validation_result = await self._validate_test_result(test_case, output)
            result.assertions_passed = validation_result["passed"]
            result.assertions_failed = validation_result["failed"]
            result.validation_details = validation_result["details"]
            
            # Determine final status
            if validation_result["failed"] == 0:
                result.status = TestStatus.PASSED
            else:
                result.status = TestStatus.FAILED
                result.error_message = f"{validation_result['failed']} validation(s) failed"
            
            # AI quality assessment
            if self.quality_engine:
                try:
                    ai_quality = await self.quality_engine.assess_test_result(
                        test_case, result, tool_instance
                    )
                    if not result.quality_score:
                        result.quality_score = ai_quality
                    else:
                        # Merge assessments
                        result.quality_score.improvement_suggestions.extend(
                            ai_quality.improvement_suggestions
                        )
                except Exception as e:
                    self.logger.warning(f"AI quality assessment failed: {str(e)}")
            
        except Exception as e:
            result.status = TestStatus.ERROR
            result.error_message = str(e)
            result.error_traceback = traceback.format_exc()
            
        finally:
            # Cleanup
            if test_case.cleanup_function:
                try:
                    if asyncio.iscoroutinefunction(test_case.cleanup_function):
                        await test_case.cleanup_function()
                    else:
                        test_case.cleanup_function()
                except Exception as e:
                    self.logger.warning(f"Cleanup failed for test {test_case.name}: {str(e)}")
            
            # Finalize result
            result.end_time = datetime.utcnow()
            result.execution_time_ms = (time.time() - execution_start) * 1000
            
            # Add environment info
            result.environment_info = {
                "tool_name": tool_instance.name,
                "tool_version": tool_instance.version,
                "execution_context": context.execution_id
            }
        
        return result
    
    async def _check_prerequisites(self, test_case: TestCase) -> bool:
        """Check if test prerequisites are met."""
        # Simple implementation - can be extended
        return True  # For now, assume all prerequisites are met
    
    async def _validate_test_result(
        self, 
        test_case: TestCase, 
        actual_output: BaseOutputModel
    ) -> Dict[str, Any]:
        """Validate test result against expected output."""
        
        validation_result = {
            "passed": 0,
            "failed": 0,
            "details": {}
        }
        
        # Use custom validation function if provided
        if test_case.validation_function:
            try:
                if asyncio.iscoroutinefunction(test_case.validation_function):
                    custom_result = await test_case.validation_function(
                        test_case.input_data, actual_output, test_case.expected_output
                    )
                else:
                    custom_result = test_case.validation_function(
                        test_case.input_data, actual_output, test_case.expected_output
                    )
                
                if isinstance(custom_result, dict):
                    validation_result.update(custom_result)
                elif custom_result:
                    validation_result["passed"] = 1
                else:
                    validation_result["failed"] = 1
                    validation_result["details"]["custom_validation"] = "Failed"
                
                return validation_result
                
            except Exception as e:
                validation_result["failed"] = 1
                validation_result["details"]["validation_error"] = str(e)
                return validation_result
        
        # Default validation based on expected output
        if test_case.expected_output:
            actual_dict = actual_output.dict()
            
            for key, expected_value in test_case.expected_output.items():
                if key in actual_dict:
                    if actual_dict[key] == expected_value:
                        validation_result["passed"] += 1
                        validation_result["details"][f"{key}_match"] = True
                    else:
                        validation_result["failed"] += 1
                        validation_result["details"][f"{key}_mismatch"] = {
                            "expected": expected_value,
                            "actual": actual_dict[key]
                        }
                else:
                    validation_result["failed"] += 1
                    validation_result["details"][f"{key}_missing"] = f"Key '{key}' not found in output"
        else:
            # No expected output specified - just check that we got some output
            if actual_output:
                validation_result["passed"] = 1
                validation_result["details"]["output_received"] = True
            else:
                validation_result["failed"] = 1
                validation_result["details"]["no_output"] = "No output received"
        
        return validation_result
    
    def _generate_execution_summary(
        self, 
        test_suite: TestSuite, 
        test_results: List[TestResult], 
        start_time: datetime, 
        end_time: datetime
    ) -> Dict[str, Any]:
        """Generate comprehensive execution summary."""
        
        # Filter out exception results
        valid_results = [r for r in test_results if isinstance(r, TestResult)]
        exception_results = [r for r in test_results if isinstance(r, Exception)]
        
        # Count results by status
        status_counts = {}
        for status in TestStatus:
            status_counts[status.value] = sum(1 for r in valid_results if r.status == status)
        
        # Calculate performance statistics
        execution_times = [r.execution_time_ms for r in valid_results]
        
        performance_stats = {}
        if execution_times:
            performance_stats = {
                "total_execution_time_ms": sum(execution_times),
                "average_execution_time_ms": statistics.mean(execution_times),
                "median_execution_time_ms": statistics.median(execution_times),
                "min_execution_time_ms": min(execution_times),
                "max_execution_time_ms": max(execution_times),
                "std_deviation_ms": statistics.stdev(execution_times) if len(execution_times) > 1 else 0
            }
        
        # Quality analysis
        quality_scores = [r.quality_score.overall_score for r in valid_results if r.quality_score]
        quality_stats = {}
        if quality_scores:
            quality_stats = {
                "average_quality_score": statistics.mean(quality_scores),
                "median_quality_score": statistics.median(quality_scores),
                "min_quality_score": min(quality_scores),
                "max_quality_score": max(quality_scores)
            }
        
        # Success metrics
        passed_tests = status_counts.get("passed", 0)
        total_tests = len(valid_results)
        success_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0
        
        return {
            "test_suite_name": test_suite.name,
            "execution_period": {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration_seconds": (end_time - start_time).total_seconds()
            },
            "test_counts": {
                "total_tests": len(test_suite.test_cases),
                "executed_tests": total_tests,
                "exception_count": len(exception_results)
            },
            "status_distribution": status_counts,
            "success_metrics": {
                "success_rate_percent": round(success_rate, 2),
                "passed_tests": passed_tests,
                "failed_tests": status_counts.get("failed", 0),
                "error_tests": status_counts.get("error", 0),
                "skipped_tests": status_counts.get("skipped", 0)
            },
            "performance_statistics": performance_stats,
            "quality_statistics": quality_stats,
            "test_results": [
                {
                    "test_id": r.test_case_id,
                    "status": r.status.value,
                    "execution_time_ms": r.execution_time_ms,
                    "quality_score": r.quality_score.overall_score if r.quality_score else None
                }
                for r in valid_results
            ]
        }
    
    async def run_performance_benchmark(
        self,
        tool_instance: ToolImplementation,
        test_cases: List[TestCase],
        iterations: int = 10,
        warmup_iterations: int = 3
    ) -> List[BenchmarkResult]:
        """Run comprehensive performance benchmarking."""
        
        benchmark_results = []
        
        self.logger.info(f"Starting performance benchmark with {iterations} iterations")
        
        for test_case in test_cases:
            self.logger.info(f"Benchmarking test case: {test_case.name}")
            
            # Warmup runs
            for _ in range(warmup_iterations):
                try:
                    input_model = tool_instance.input_model.parse_obj(test_case.input_data)
                    await tool_instance.execute(input_model)
                except:
                    pass  # Ignore warmup failures
            
            # Benchmark runs
            execution_times = []
            memory_usage = []
            
            for iteration in range(iterations):
                start_time = time.time()
                start_memory = self._get_memory_usage()
                
                try:
                    input_model = tool_instance.input_model.parse_obj(test_case.input_data)
                    result = await tool_instance.execute(input_model)
                    
                    execution_time = (time.time() - start_time) * 1000
                    end_memory = self._get_memory_usage()
                    
                    execution_times.append(execution_time)
                    memory_usage.append(max(0, end_memory - start_memory))
                    
                except Exception as e:
                    self.logger.warning(f"Benchmark iteration {iteration} failed: {str(e)}")
                    continue
            
            if execution_times:
                # Latency benchmark
                latency_result = BenchmarkResult(
                    category=BenchmarkCategory.LATENCY,
                    metric_name="execution_time",
                    value=statistics.mean(execution_times),
                    unit="milliseconds",
                    timestamp=datetime.utcnow(),
                    sample_count=len(execution_times),
                    min_value=min(execution_times),
                    max_value=max(execution_times),
                    mean_value=statistics.mean(execution_times),
                    std_deviation=statistics.stdev(execution_times) if len(execution_times) > 1 else 0,
                    percentiles={
                        "p50": statistics.median(execution_times),
                        "p95": self._calculate_percentile(execution_times, 95),
                        "p99": self._calculate_percentile(execution_times, 99)
                    },
                    test_conditions={"test_case": test_case.name, "iterations": iterations}
                )
                benchmark_results.append(latency_result)
                
                # Throughput benchmark
                avg_time_seconds = statistics.mean(execution_times) / 1000
                throughput = 1 / avg_time_seconds if avg_time_seconds > 0 else 0
                
                throughput_result = BenchmarkResult(
                    category=BenchmarkCategory.THROUGHPUT,
                    metric_name="requests_per_second",
                    value=throughput,
                    unit="ops/sec",
                    timestamp=datetime.utcnow(),
                    sample_count=len(execution_times),
                    test_conditions={"test_case": test_case.name, "iterations": iterations}
                )
                benchmark_results.append(throughput_result)
            
            if memory_usage and any(m > 0 for m in memory_usage):
                # Memory usage benchmark
                memory_result = BenchmarkResult(
                    category=BenchmarkCategory.RESOURCE_USAGE,
                    metric_name="memory_delta",
                    value=statistics.mean(memory_usage),
                    unit="MB",
                    timestamp=datetime.utcnow(),
                    sample_count=len(memory_usage),
                    min_value=min(memory_usage),
                    max_value=max(memory_usage),
                    mean_value=statistics.mean(memory_usage),
                    test_conditions={"test_case": test_case.name, "iterations": iterations}
                )
                benchmark_results.append(memory_result)
        
        # Store benchmark results
        self.benchmark_history.extend(benchmark_results)
        
        return benchmark_results
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0  # psutil not available
        except:
            return 0.0
    
    def _calculate_percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile value."""
        if not data:
            return 0.0
        
        sorted_data = sorted(data)
        index = (percentile / 100.0) * (len(sorted_data) - 1)
        
        if index.is_integer():
            return sorted_data[int(index)]
        
        lower = int(index)
        upper = lower + 1
        weight = index - lower
        
        if upper >= len(sorted_data):
            return sorted_data[-1]
        
        return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight
    
    def generate_quality_report(self, test_suite_name: str) -> Dict[str, Any]:
        """Generate comprehensive quality report."""
        
        # Filter execution history for the specified test suite
        suite_executions = [
            record for record in self.execution_history
            if record["test_suite"] == test_suite_name
        ]
        
        if not suite_executions:
            return {"error": f"No execution history found for test suite: {test_suite_name}"}
        
        # Latest execution
        latest_execution = max(suite_executions, key=lambda x: x["timestamp"])
        
        # Trend analysis
        trend_analysis = self._analyze_quality_trends(suite_executions)
        
        # Benchmark analysis
        relevant_benchmarks = [
            b for b in self.benchmark_history
            if test_suite_name in b.test_conditions.get("test_case", "")
        ]
        
        benchmark_summary = self._summarize_benchmarks(relevant_benchmarks)
        
        return {
            "test_suite": test_suite_name,
            "report_generated": datetime.utcnow().isoformat(),
            "latest_execution": latest_execution["summary"],
            "trend_analysis": trend_analysis,
            "benchmark_summary": benchmark_summary,
            "recommendations": self._generate_quality_recommendations(
                latest_execution, trend_analysis, benchmark_summary
            )
        }
    
    def _analyze_quality_trends(self, executions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze quality trends over time."""
        
        if len(executions) < 2:
            return {"insufficient_data": "Need at least 2 executions for trend analysis"}
        
        # Sort by timestamp
        sorted_executions = sorted(executions, key=lambda x: x["timestamp"])
        
        # Extract metrics
        success_rates = [e["summary"]["success_metrics"]["success_rate_percent"] for e in sorted_executions]
        avg_times = [
            e["summary"]["performance_statistics"].get("average_execution_time_ms", 0)
            for e in sorted_executions
        ]
        
        return {
            "success_rate_trend": {
                "current": success_rates[-1] if success_rates else 0,
                "previous": success_rates[-2] if len(success_rates) > 1 else 0,
                "change": success_rates[-1] - success_rates[-2] if len(success_rates) > 1 else 0,
                "direction": self._determine_trend_direction(success_rates[-3:] if len(success_rates) > 2 else success_rates)
            },
            "performance_trend": {
                "current_avg_ms": avg_times[-1] if avg_times else 0,
                "previous_avg_ms": avg_times[-2] if len(avg_times) > 1 else 0,
                "change_ms": avg_times[-1] - avg_times[-2] if len(avg_times) > 1 else 0,
                "direction": self._determine_trend_direction(avg_times[-3:] if len(avg_times) > 2 else avg_times, reverse=True)
            },
            "total_executions": len(executions),
            "date_range": {
                "earliest": sorted_executions[0]["timestamp"].isoformat(),
                "latest": sorted_executions[-1]["timestamp"].isoformat()
            }
        }
    
    def _determine_trend_direction(self, values: List[float], reverse: bool = False) -> str:
        """Determine trend direction (improving/declining/stable)."""
        
        if len(values) < 2:
            return "unknown"
        
        # Calculate simple linear trend
        changes = [values[i] - values[i-1] for i in range(1, len(values))]
        avg_change = statistics.mean(changes)
        
        threshold = 0.1  # Minimum change to consider significant
        
        if reverse:
            # For metrics where lower is better (like execution time)
            if avg_change < -threshold:
                return "improving"
            elif avg_change > threshold:
                return "declining"
        else:
            # For metrics where higher is better (like success rate)
            if avg_change > threshold:
                return "improving"
            elif avg_change < -threshold:
                return "declining"
        
        return "stable"
    
    def _summarize_benchmarks(self, benchmarks: List[BenchmarkResult]) -> Dict[str, Any]:
        """Summarize benchmark results."""
        
        if not benchmarks:
            return {"no_benchmarks": "No benchmark data available"}
        
        summary = {}
        
        # Group by category
        by_category = {}
        for benchmark in benchmarks:
            category = benchmark.category.value
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(benchmark)
        
        for category, category_benchmarks in by_category.items():
            category_summary = {}
            
            # Latest benchmark of this category
            latest = max(category_benchmarks, key=lambda x: x.timestamp)
            category_summary["latest_value"] = latest.value
            category_summary["unit"] = latest.unit
            category_summary["timestamp"] = latest.timestamp.isoformat()
            
            # Trend if multiple benchmarks
            if len(category_benchmarks) > 1:
                values = [b.value for b in sorted(category_benchmarks, key=lambda x: x.timestamp)]
                category_summary["trend"] = self._determine_trend_direction(
                    values[-3:], reverse=(category == "latency")
                )
            
            summary[category] = category_summary
        
        return summary
    
    def _generate_quality_recommendations(
        self, 
        latest_execution: Dict[str, Any],
        trend_analysis: Dict[str, Any],
        benchmark_summary: Dict[str, Any]
    ) -> List[str]:
        """Generate quality improvement recommendations."""
        
        recommendations = []
        
        # Success rate recommendations
        success_rate = latest_execution["summary"]["success_metrics"]["success_rate_percent"]
        if success_rate < 90:
            recommendations.append(f"Success rate is {success_rate:.1f}% - investigate failing tests")
        
        success_trend = trend_analysis.get("success_rate_trend", {}).get("direction", "unknown")
        if success_trend == "declining":
            recommendations.append("Success rate is declining - review recent changes")
        
        # Performance recommendations
        performance_trend = trend_analysis.get("performance_trend", {}).get("direction", "unknown")
        if performance_trend == "declining":
            recommendations.append("Performance is declining - consider optimization")
        
        # Benchmark recommendations
        latency_info = benchmark_summary.get("latency", {})
        if "latest_value" in latency_info and latency_info["latest_value"] > 5000:
            recommendations.append("High latency detected - investigate performance bottlenecks")
        
        throughput_info = benchmark_summary.get("throughput", {})
        if "latest_value" in throughput_info and throughput_info["latest_value"] < 1:
            recommendations.append("Low throughput - consider scaling optimizations")
        
        # General recommendations
        if len(recommendations) == 0:
            recommendations.append("Quality metrics look good - maintain current practices")
        
        return recommendations


# Factory functions
def create_quality_framework(
    api_key: Optional[str] = None,
    enable_ai_assessment: bool = True
) -> TestExecutor:
    """Create a configured quality framework instance."""
    
    quality_engine = None
    if enable_ai_assessment:
        quality_engine = QualityAssessmentEngine(api_key)
    
    return TestExecutor(quality_engine)


def create_test_suite(
    name: str,
    tool_class: Type[ToolImplementation],
    description: str = ""
) -> TestSuite:
    """Create a new test suite."""
    return TestSuite(name, tool_class, description)


# Export main components
__all__ = [
    'TestType', 'TestStatus', 'BenchmarkCategory',
    'TestCase', 'TestResult', 'BenchmarkResult',
    'TestSuite', 'TestExecutor', 'QualityAssessmentEngine',
    'create_quality_framework', 'create_test_suite'
]