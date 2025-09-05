"""
AI Test Judge Tool

AI-powered test evaluation and quality assessment system that analyzes
test results, identifies patterns, and provides intelligent insights
for continuous improvement of system quality.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import statistics
import re

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from tools.base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, 
    ToolContext, ToolError, ErrorCategory, QualityMetric
)


logger = logging.getLogger(__name__)


class TestResultSeverity(Enum):
    """Test result severity levels."""
    CRITICAL = "critical"
    HIGH = "high" 
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class TestCategory(Enum):
    """Test categories for analysis."""
    UNIT = "unit"
    INTEGRATION = "integration"
    PERFORMANCE = "performance"
    STRESS = "stress"
    MEMORY = "memory"
    E2E = "e2e"
    SECURITY = "security"
    LOAD = "load"


@dataclass
class TestResult:
    """Individual test result structure."""
    name: str
    category: TestCategory
    status: str  # passed, failed, skipped, error
    duration_seconds: float
    error_message: Optional[str] = None
    assertion_details: List[str] = field(default_factory=list)
    performance_metrics: Dict[str, float] = field(default_factory=dict)
    memory_usage_mb: Optional[float] = None
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestSuite:
    """Test suite aggregation."""
    name: str
    category: TestCategory
    tests: List[TestResult] = field(default_factory=list)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    error_tests: int = 0
    total_duration_seconds: float = 0.0
    coverage_percentage: Optional[float] = None
    
    def calculate_metrics(self):
        """Calculate suite-level metrics."""
        self.total_tests = len(self.tests)
        self.passed_tests = sum(1 for t in self.tests if t.status == "passed")
        self.failed_tests = sum(1 for t in self.tests if t.status == "failed")
        self.skipped_tests = sum(1 for t in self.tests if t.status == "skipped")
        self.error_tests = sum(1 for t in self.tests if t.status == "error")
        self.total_duration_seconds = sum(t.duration_seconds for t in self.tests)


@dataclass
class TestJudgment:
    """AI test judgment result."""
    overall_score: float
    severity: TestResultSeverity
    summary: str
    detailed_analysis: str
    recommendations: List[str] = field(default_factory=list)
    quality_gates_status: Dict[str, bool] = field(default_factory=dict)
    performance_assessment: Dict[str, Any] = field(default_factory=dict)
    risk_factors: List[str] = field(default_factory=list)
    improvement_priorities: List[Tuple[str, int]] = field(default_factory=list)  # (item, priority 1-10)
    confidence_score: float = 0.8
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TestJudgeInput(BaseInputModel):
    """Input for test judge analysis."""
    
    test_suites: List[Dict[str, Any]] = Field(..., description="Test suite results")
    performance_benchmarks: Dict[str, float] = Field(default_factory=dict, description="Performance benchmarks")
    quality_gates: Dict[str, Any] = Field(default_factory=dict, description="Quality gate criteria")
    previous_results: Optional[List[Dict[str, Any]]] = Field(None, description="Historical test results")
    system_context: Dict[str, Any] = Field(default_factory=dict, description="System context information")
    analysis_focus: List[str] = Field(default_factory=list, description="Areas to focus analysis on")
    
    class Config:
        schema_extra = {
            "example": {
                "test_suites": [
                    {
                        "name": "integration_tests",
                        "category": "integration", 
                        "tests": [
                            {
                                "name": "test_db_agent_integration",
                                "status": "passed",
                                "duration_seconds": 2.5,
                                "performance_metrics": {"response_time_ms": 250}
                            }
                        ]
                    }
                ],
                "quality_gates": {
                    "min_coverage": 90.0,
                    "max_failure_rate": 0.05,
                    "max_avg_response_time_ms": 1000
                }
            }
        }


class TestJudgeOutput(BaseOutputModel):
    """Output from test judge analysis."""
    
    judgment: TestJudgment = Field(..., description="AI test judgment")
    quality_score: float = Field(..., description="Overall quality score")
    pass_rate: float = Field(..., description="Test pass rate")
    performance_score: float = Field(..., description="Performance score") 
    reliability_score: float = Field(..., description="Reliability score")
    detailed_metrics: Dict[str, Any] = Field(default_factory=dict, description="Detailed analysis metrics")
    trend_analysis: Dict[str, Any] = Field(default_factory=dict, description="Trend analysis if historical data available")
    actionable_items: List[Dict[str, Any]] = Field(default_factory=list, description="Prioritized action items")


class TestJudgeTool(ToolImplementation):
    """
    AI-powered test judge that provides intelligent analysis and insights
    from test results to guide quality improvement efforts.
    """
    
    def __init__(self, ai_model: str = "openai:gpt-4"):
        super().__init__(
            name="test_judge",
            version="1.0.0",
            description="AI-powered test evaluation and quality assessment tool"
        )
        
        self.ai_model = ai_model
        self._initialize_ai_judge()
        
        # Quality benchmarks
        self.default_quality_gates = {
            "min_coverage": 85.0,
            "max_failure_rate": 0.10,  # 10%
            "max_avg_response_time_ms": 2000,
            "max_memory_growth_mb": 50,
            "min_performance_score": 7.0,
            "max_error_rate": 0.05  # 5%
        }
        
        # Performance benchmarks by category
        self.performance_benchmarks = {
            TestCategory.UNIT: {"max_duration_seconds": 0.1, "target_score": 9.0},
            TestCategory.INTEGRATION: {"max_duration_seconds": 5.0, "target_score": 8.0},
            TestCategory.PERFORMANCE: {"max_duration_seconds": 30.0, "target_score": 7.5},
            TestCategory.LOAD: {"max_duration_seconds": 120.0, "target_score": 7.0},
            TestCategory.STRESS: {"max_duration_seconds": 300.0, "target_score": 6.5},
            TestCategory.MEMORY: {"max_duration_seconds": 60.0, "target_score": 8.0}
        }
    
    def _initialize_ai_judge(self):
        """Initialize the AI judge agent."""
        system_prompt = """You are an expert AI test judge and quality assessor for software systems.

Your role is to analyze test results comprehensively and provide actionable insights for improving system quality. You have deep expertise in:

- Software testing methodologies and best practices
- Performance analysis and optimization
- System reliability and stability assessment  
- Quality metrics interpretation
- Risk assessment and mitigation strategies
- Continuous improvement recommendations

When analyzing test results, consider:

1. **Overall Quality Assessment**: Evaluate pass rates, coverage, and test distribution
2. **Performance Analysis**: Assess response times, throughput, and resource usage
3. **Reliability Indicators**: Look for patterns in failures, flaky tests, and error trends
4. **Risk Factors**: Identify potential system vulnerabilities and stability concerns
5. **Improvement Opportunities**: Provide specific, actionable recommendations

Provide balanced, constructive feedback that helps teams improve their systems while acknowledging what's working well.

Always include confidence levels in your assessments and explain your reasoning clearly."""

        self.judge_agent = Agent(
            model=self.ai_model,
            system_prompt=system_prompt
        )
    
    @property
    def input_model(self):
        return TestJudgeInput
    
    @property
    def output_model(self):
        return TestJudgeOutput
    
    async def _execute_core(self, input_data: TestJudgeInput, context: ToolContext) -> Dict[str, Any]:
        """Execute the AI test judgment analysis."""
        try:
            # Parse and validate test suites
            test_suites = self._parse_test_suites(input_data.test_suites)
            
            # Calculate aggregate metrics
            aggregate_metrics = self._calculate_aggregate_metrics(test_suites)
            
            # Apply quality gates
            quality_gates_results = self._evaluate_quality_gates(
                aggregate_metrics, 
                input_data.quality_gates or self.default_quality_gates
            )
            
            # Perform trend analysis if historical data available
            trend_analysis = {}
            if input_data.previous_results:
                trend_analysis = self._analyze_trends(input_data.previous_results, aggregate_metrics)
            
            # Generate AI judgment
            ai_judgment = await self._generate_ai_judgment(
                test_suites=test_suites,
                metrics=aggregate_metrics,
                quality_gates=quality_gates_results,
                trends=trend_analysis,
                system_context=input_data.system_context,
                focus_areas=input_data.analysis_focus
            )
            
            # Calculate component scores
            quality_score = self._calculate_quality_score(aggregate_metrics, quality_gates_results)
            performance_score = self._calculate_performance_score(test_suites, input_data.performance_benchmarks)
            reliability_score = self._calculate_reliability_score(aggregate_metrics, trend_analysis)
            
            # Generate actionable items
            actionable_items = self._generate_actionable_items(ai_judgment, aggregate_metrics, quality_gates_results)
            
            return {
                "judgment": ai_judgment,
                "quality_score": quality_score,
                "pass_rate": aggregate_metrics.get("overall_pass_rate", 0.0),
                "performance_score": performance_score,
                "reliability_score": reliability_score,
                "detailed_metrics": aggregate_metrics,
                "trend_analysis": trend_analysis,
                "actionable_items": actionable_items
            }
            
        except Exception as e:
            logger.error(f"Error in test judge analysis: {str(e)}")
            raise ToolError(
                f"Test judge analysis failed: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"input_data": str(input_data)[:500]}
            )
    
    def _parse_test_suites(self, raw_suites: List[Dict[str, Any]]) -> List[TestSuite]:
        """Parse raw test suite data into structured format."""
        suites = []
        
        for suite_data in raw_suites:
            try:
                category = TestCategory(suite_data.get("category", "unit"))
                suite = TestSuite(
                    name=suite_data.get("name", "unknown"),
                    category=category
                )
                
                # Parse individual tests
                for test_data in suite_data.get("tests", []):
                    test = TestResult(
                        name=test_data.get("name", "unknown_test"),
                        category=category,
                        status=test_data.get("status", "unknown"),
                        duration_seconds=test_data.get("duration_seconds", 0.0),
                        error_message=test_data.get("error_message"),
                        assertion_details=test_data.get("assertion_details", []),
                        performance_metrics=test_data.get("performance_metrics", {}),
                        memory_usage_mb=test_data.get("memory_usage_mb"),
                        resource_usage=test_data.get("resource_usage", {}),
                        metadata=test_data.get("metadata", {})
                    )
                    suite.tests.append(test)
                
                suite.calculate_metrics()
                suite.coverage_percentage = suite_data.get("coverage_percentage")
                suites.append(suite)
                
            except Exception as e:
                logger.warning(f"Error parsing test suite {suite_data.get('name', 'unknown')}: {str(e)}")
                continue
        
        return suites
    
    def _calculate_aggregate_metrics(self, test_suites: List[TestSuite]) -> Dict[str, Any]:
        """Calculate aggregate metrics across all test suites."""
        total_tests = sum(suite.total_tests for suite in test_suites)
        total_passed = sum(suite.passed_tests for suite in test_suites)
        total_failed = sum(suite.failed_tests for suite in test_suites)
        total_errors = sum(suite.error_tests for suite in test_suites)
        total_skipped = sum(suite.skipped_tests for suite in test_suites)
        total_duration = sum(suite.total_duration_seconds for suite in test_suites)
        
        # Calculate rates
        overall_pass_rate = total_passed / total_tests if total_tests > 0 else 0.0
        failure_rate = (total_failed + total_errors) / total_tests if total_tests > 0 else 0.0
        
        # Performance metrics
        all_tests = [test for suite in test_suites for test in suite.tests]
        response_times = []
        memory_usage = []
        
        for test in all_tests:
            if test.performance_metrics:
                if "response_time_ms" in test.performance_metrics:
                    response_times.append(test.performance_metrics["response_time_ms"])
                if "avg_response_time_ms" in test.performance_metrics:
                    response_times.append(test.performance_metrics["avg_response_time_ms"])
            
            if test.memory_usage_mb:
                memory_usage.append(test.memory_usage_mb)
        
        # Coverage analysis
        coverage_data = [suite.coverage_percentage for suite in test_suites if suite.coverage_percentage is not None]
        avg_coverage = statistics.mean(coverage_data) if coverage_data else None
        
        return {
            "total_tests": total_tests,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_errors": total_errors,
            "total_skipped": total_skipped,
            "total_duration_seconds": total_duration,
            "overall_pass_rate": overall_pass_rate,
            "failure_rate": failure_rate,
            "avg_test_duration": total_duration / total_tests if total_tests > 0 else 0.0,
            "avg_response_time_ms": statistics.mean(response_times) if response_times else None,
            "p95_response_time_ms": sorted(response_times)[int(len(response_times) * 0.95)] if response_times else None,
            "avg_memory_usage_mb": statistics.mean(memory_usage) if memory_usage else None,
            "peak_memory_usage_mb": max(memory_usage) if memory_usage else None,
            "avg_coverage_percentage": avg_coverage,
            "suite_count": len(test_suites),
            "category_distribution": self._calculate_category_distribution(test_suites)
        }
    
    def _calculate_category_distribution(self, test_suites: List[TestSuite]) -> Dict[str, int]:
        """Calculate test distribution by category."""
        distribution = {}
        for suite in test_suites:
            category = suite.category.value
            distribution[category] = distribution.get(category, 0) + suite.total_tests
        return distribution
    
    def _evaluate_quality_gates(self, metrics: Dict[str, Any], quality_gates: Dict[str, Any]) -> Dict[str, bool]:
        """Evaluate quality gates against metrics."""
        results = {}
        
        # Coverage gate
        if "min_coverage" in quality_gates and metrics.get("avg_coverage_percentage") is not None:
            results["coverage"] = metrics["avg_coverage_percentage"] >= quality_gates["min_coverage"]
        
        # Failure rate gate
        if "max_failure_rate" in quality_gates:
            results["failure_rate"] = metrics["failure_rate"] <= quality_gates["max_failure_rate"]
        
        # Response time gate
        if "max_avg_response_time_ms" in quality_gates and metrics.get("avg_response_time_ms") is not None:
            results["response_time"] = metrics["avg_response_time_ms"] <= quality_gates["max_avg_response_time_ms"]
        
        # Memory usage gate
        if "max_memory_growth_mb" in quality_gates and metrics.get("peak_memory_usage_mb") is not None:
            results["memory_usage"] = metrics["peak_memory_usage_mb"] <= quality_gates["max_memory_growth_mb"]
        
        # Error rate gate
        if "max_error_rate" in quality_gates:
            error_rate = (metrics["total_errors"] / metrics["total_tests"]) if metrics["total_tests"] > 0 else 0.0
            results["error_rate"] = error_rate <= quality_gates["max_error_rate"]
        
        return results
    
    def _analyze_trends(self, previous_results: List[Dict[str, Any]], current_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze trends from historical data."""
        if not previous_results:
            return {}
        
        try:
            # Extract key metrics from historical data
            historical_pass_rates = []
            historical_durations = []
            historical_coverage = []
            
            for result in previous_results[-10:]:  # Last 10 results
                if "pass_rate" in result:
                    historical_pass_rates.append(result["pass_rate"])
                if "total_duration_seconds" in result:
                    historical_durations.append(result["total_duration_seconds"])
                if "avg_coverage_percentage" in result:
                    historical_coverage.append(result["avg_coverage_percentage"])
            
            trends = {}
            
            # Pass rate trend
            if historical_pass_rates and current_metrics.get("overall_pass_rate") is not None:
                recent_avg = statistics.mean(historical_pass_rates[-3:]) if len(historical_pass_rates) >= 3 else historical_pass_rates[-1]
                current_rate = current_metrics["overall_pass_rate"]
                trends["pass_rate_trend"] = "improving" if current_rate > recent_avg else "declining" if current_rate < recent_avg else "stable"
                trends["pass_rate_change"] = current_rate - recent_avg
            
            # Performance trend
            if historical_durations and current_metrics.get("total_duration_seconds") is not None:
                recent_avg = statistics.mean(historical_durations[-3:]) if len(historical_durations) >= 3 else historical_durations[-1]
                current_duration = current_metrics["total_duration_seconds"]
                trends["performance_trend"] = "improving" if current_duration < recent_avg else "declining" if current_duration > recent_avg else "stable"
                trends["duration_change_seconds"] = current_duration - recent_avg
            
            # Coverage trend
            if historical_coverage and current_metrics.get("avg_coverage_percentage") is not None:
                recent_avg = statistics.mean(historical_coverage[-3:]) if len(historical_coverage) >= 3 else historical_coverage[-1]
                current_coverage = current_metrics["avg_coverage_percentage"]
                trends["coverage_trend"] = "improving" if current_coverage > recent_avg else "declining" if current_coverage < recent_avg else "stable"
                trends["coverage_change"] = current_coverage - recent_avg
            
            return trends
            
        except Exception as e:
            logger.warning(f"Error analyzing trends: {str(e)}")
            return {}
    
    async def _generate_ai_judgment(
        self, 
        test_suites: List[TestSuite],
        metrics: Dict[str, Any],
        quality_gates: Dict[str, bool],
        trends: Dict[str, Any],
        system_context: Dict[str, Any],
        focus_areas: List[str]
    ) -> TestJudgment:
        """Generate AI-powered test judgment."""
        
        # Prepare context for AI analysis
        analysis_context = {
            "test_summary": {
                "total_tests": metrics.get("total_tests", 0),
                "pass_rate": metrics.get("overall_pass_rate", 0.0),
                "failure_rate": metrics.get("failure_rate", 0.0),
                "avg_duration": metrics.get("avg_test_duration", 0.0),
                "coverage": metrics.get("avg_coverage_percentage"),
                "suite_count": len(test_suites)
            },
            "quality_gates": quality_gates,
            "trends": trends,
            "system_context": system_context,
            "focus_areas": focus_areas,
            "performance_metrics": {
                "avg_response_time_ms": metrics.get("avg_response_time_ms"),
                "p95_response_time_ms": metrics.get("p95_response_time_ms"),
                "peak_memory_mb": metrics.get("peak_memory_usage_mb")
            },
            "test_categories": metrics.get("category_distribution", {})
        }
        
        # Create prompt for AI analysis
        analysis_prompt = f"""
        Analyze the following test results and provide a comprehensive quality assessment:

        {json.dumps(analysis_context, indent=2, default=str)}

        Please provide:
        1. Overall quality score (0-10)
        2. Severity assessment (critical/high/medium/low/info)
        3. Brief summary of findings
        4. Detailed analysis covering:
           - Test coverage and completeness
           - Performance characteristics
           - Reliability indicators
           - Risk factors
        5. Specific recommendations for improvement
        6. Priority areas for attention
        
        Focus on actionable insights that help improve system quality.
        """
        
        try:
            # Get AI analysis
            ai_response = await self.judge_agent.run(analysis_prompt)
            
            # Parse AI response and create structured judgment
            judgment = self._parse_ai_response(ai_response, metrics, quality_gates, trends)
            
            return judgment
            
        except Exception as e:
            logger.error(f"Error generating AI judgment: {str(e)}")
            # Fallback to rule-based judgment
            return self._generate_fallback_judgment(metrics, quality_gates, trends)
    
    def _parse_ai_response(self, ai_response, metrics: Dict[str, Any], quality_gates: Dict[str, bool], trends: Dict[str, Any]) -> TestJudgment:
        """Parse AI response into structured judgment."""
        try:
            # Extract key information from AI response
            response_text = str(ai_response)
            
            # Extract overall score (look for patterns like "score: 8.5" or "8.5/10")
            score_match = re.search(r'(?:score|rating)[:=\s]*(\d+\.?\d*)', response_text, re.IGNORECASE)
            overall_score = float(score_match.group(1)) if score_match else self._calculate_rule_based_score(metrics, quality_gates)
            overall_score = max(0.0, min(10.0, overall_score))  # Clamp to 0-10
            
            # Extract severity
            severity = TestResultSeverity.MEDIUM  # Default
            if "critical" in response_text.lower():
                severity = TestResultSeverity.CRITICAL
            elif "high" in response_text.lower() and ("risk" in response_text.lower() or "priority" in response_text.lower()):
                severity = TestResultSeverity.HIGH
            elif "low" in response_text.lower() and ("risk" in response_text.lower() or "priority" in response_text.lower()):
                severity = TestResultSeverity.LOW
            elif overall_score >= 8.0:
                severity = TestResultSeverity.INFO
            elif overall_score < 5.0:
                severity = TestResultSeverity.HIGH
            
            # Extract recommendations (look for bullet points or numbered lists)
            recommendations = []
            lines = response_text.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith(('-', '*', '•')) or re.match(r'^\d+\.', line):
                    recommendation = re.sub(r'^[-*•\d\.\s]+', '', line).strip()
                    if recommendation and len(recommendation) > 10:
                        recommendations.append(recommendation)
            
            # Generate summary and detailed analysis
            summary_lines = response_text.split('\n')[:3]
            summary = ' '.join(line.strip() for line in summary_lines if line.strip())[:200]
            
            detailed_analysis = response_text[:1000]  # First 1000 chars
            
            # Extract risk factors
            risk_factors = []
            if metrics.get("failure_rate", 0) > 0.1:
                risk_factors.append(f"High failure rate: {metrics['failure_rate']:.1%}")
            if not quality_gates.get("coverage", True):
                risk_factors.append("Insufficient test coverage")
            if trends.get("pass_rate_trend") == "declining":
                risk_factors.append("Declining test pass rate trend")
            
            # Generate improvement priorities
            improvement_priorities = []
            if not quality_gates.get("coverage", True):
                improvement_priorities.append(("Increase test coverage", 9))
            if metrics.get("failure_rate", 0) > 0.05:
                improvement_priorities.append(("Fix failing tests", 8))
            if metrics.get("avg_response_time_ms", 0) > 2000:
                improvement_priorities.append(("Optimize performance", 7))
            
            return TestJudgment(
                overall_score=overall_score,
                severity=severity,
                summary=summary or "Test analysis completed",
                detailed_analysis=detailed_analysis,
                recommendations=recommendations[:10],  # Limit to 10 recommendations
                quality_gates_status=quality_gates,
                performance_assessment={
                    "response_time_score": min(10, max(0, 10 - (metrics.get("avg_response_time_ms", 1000) - 1000) / 200)),
                    "memory_score": 8.0 if metrics.get("peak_memory_usage_mb", 0) < 500 else 6.0,
                    "duration_score": 9.0 if metrics.get("total_duration_seconds", 0) < 60 else 7.0
                },
                risk_factors=risk_factors,
                improvement_priorities=improvement_priorities,
                confidence_score=0.85 if len(recommendations) > 0 else 0.7
            )
            
        except Exception as e:
            logger.warning(f"Error parsing AI response: {str(e)}")
            return self._generate_fallback_judgment(metrics, quality_gates, trends)
    
    def _generate_fallback_judgment(self, metrics: Dict[str, Any], quality_gates: Dict[str, bool], trends: Dict[str, Any]) -> TestJudgment:
        """Generate rule-based judgment as fallback."""
        overall_score = self._calculate_rule_based_score(metrics, quality_gates)
        
        severity = TestResultSeverity.INFO
        if overall_score < 5.0:
            severity = TestResultSeverity.HIGH
        elif overall_score < 7.0:
            severity = TestResultSeverity.MEDIUM
        elif metrics.get("failure_rate", 0) > 0.15:
            severity = TestResultSeverity.HIGH
        
        summary = f"Test analysis complete. Overall score: {overall_score:.1f}/10. "
        summary += f"Pass rate: {metrics.get('overall_pass_rate', 0):.1%}. "
        summary += f"Total tests: {metrics.get('total_tests', 0)}."
        
        recommendations = []
        if metrics.get("failure_rate", 0) > 0.1:
            recommendations.append("Address failing tests to improve reliability")
        if not quality_gates.get("coverage", True):
            recommendations.append("Increase test coverage to meet quality gates")
        if metrics.get("avg_response_time_ms", 0) > 2000:
            recommendations.append("Optimize performance to reduce response times")
        
        return TestJudgment(
            overall_score=overall_score,
            severity=severity,
            summary=summary,
            detailed_analysis="Rule-based analysis completed. Review specific metrics for detailed insights.",
            recommendations=recommendations,
            quality_gates_status=quality_gates,
            confidence_score=0.6  # Lower confidence for fallback
        )
    
    def _calculate_rule_based_score(self, metrics: Dict[str, Any], quality_gates: Dict[str, bool]) -> float:
        """Calculate rule-based quality score."""
        score = 10.0
        
        # Pass rate impact (40% of score)
        pass_rate = metrics.get("overall_pass_rate", 0.0)
        score -= (1 - pass_rate) * 4
        
        # Quality gates impact (30% of score)
        failed_gates = sum(1 for passed in quality_gates.values() if not passed)
        total_gates = len(quality_gates) if quality_gates else 1
        gate_score = (total_gates - failed_gates) / total_gates
        score -= (1 - gate_score) * 3
        
        # Performance impact (20% of score)
        avg_response_time = metrics.get("avg_response_time_ms", 1000)
        if avg_response_time > 2000:
            score -= min(2.0, (avg_response_time - 2000) / 1000)
        
        # Coverage impact (10% of score)
        coverage = metrics.get("avg_coverage_percentage", 80)
        if coverage and coverage < 80:
            score -= (80 - coverage) / 80
        
        return max(0.0, min(10.0, score))
    
    def _calculate_quality_score(self, metrics: Dict[str, Any], quality_gates: Dict[str, bool]) -> float:
        """Calculate overall quality score."""
        return self._calculate_rule_based_score(metrics, quality_gates)
    
    def _calculate_performance_score(self, test_suites: List[TestSuite], benchmarks: Dict[str, float]) -> float:
        """Calculate performance score based on test results."""
        scores = []
        
        for suite in test_suites:
            category = suite.category
            benchmark = self.performance_benchmarks.get(category, {})
            
            if benchmark:
                # Duration score
                max_duration = benchmark.get("max_duration_seconds", 10.0)
                if suite.total_duration_seconds <= max_duration:
                    duration_score = 10.0
                else:
                    duration_score = max(0.0, 10.0 - (suite.total_duration_seconds - max_duration) / max_duration * 5)
                
                scores.append(duration_score)
            
            # Response time scores from individual tests
            for test in suite.tests:
                if test.performance_metrics and "response_time_ms" in test.performance_metrics:
                    response_time = test.performance_metrics["response_time_ms"]
                    if response_time <= 1000:  # Under 1 second
                        scores.append(9.0)
                    elif response_time <= 2000:  # Under 2 seconds
                        scores.append(7.0)
                    else:
                        scores.append(max(0.0, 10.0 - response_time / 1000))
        
        return statistics.mean(scores) if scores else 7.0
    
    def _calculate_reliability_score(self, metrics: Dict[str, Any], trends: Dict[str, Any]) -> float:
        """Calculate reliability score."""
        base_score = 10.0
        
        # Failure rate impact
        failure_rate = metrics.get("failure_rate", 0.0)
        base_score -= failure_rate * 10  # 10% failure = -1 point
        
        # Error rate impact
        error_rate = (metrics.get("total_errors", 0) / max(metrics.get("total_tests", 1), 1))
        base_score -= error_rate * 15  # Errors are worse than failures
        
        # Trend impact
        if trends.get("pass_rate_trend") == "improving":
            base_score += 0.5
        elif trends.get("pass_rate_trend") == "declining":
            base_score -= 1.0
        
        return max(0.0, min(10.0, base_score))
    
    def _generate_actionable_items(
        self, 
        judgment: TestJudgment, 
        metrics: Dict[str, Any], 
        quality_gates: Dict[str, bool]
    ) -> List[Dict[str, Any]]:
        """Generate prioritized actionable items."""
        items = []
        
        # High priority items from judgment
        for i, (item, priority) in enumerate(judgment.improvement_priorities):
            items.append({
                "id": f"priority_{i}",
                "title": item,
                "priority": priority,
                "category": "improvement",
                "description": f"Priority {priority}/10 improvement item",
                "estimated_impact": "high" if priority >= 8 else "medium" if priority >= 6 else "low"
            })
        
        # Quality gate failures
        for gate_name, passed in quality_gates.items():
            if not passed:
                items.append({
                    "id": f"gate_{gate_name}",
                    "title": f"Fix {gate_name} quality gate",
                    "priority": 8,
                    "category": "quality_gate",
                    "description": f"Address {gate_name} quality gate failure",
                    "estimated_impact": "high"
                })
        
        # Performance issues
        if metrics.get("avg_response_time_ms", 0) > 2000:
            items.append({
                "id": "performance_optimization",
                "title": "Optimize system performance",
                "priority": 7,
                "category": "performance",
                "description": f"Average response time is {metrics['avg_response_time_ms']:.0f}ms, target is <2000ms",
                "estimated_impact": "medium"
            })
        
        # Sort by priority
        items.sort(key=lambda x: x["priority"], reverse=True)
        
        return items[:10]  # Return top 10 items
    
    async def _custom_quality_assessment(self, quality, input_data, result, context):
        """Custom quality assessment for the test judge tool."""
        quality.add_dimension(QualityMetric.ACCURACY, 9.0, "AI-powered analysis provides high accuracy")
        quality.add_dimension(QualityMetric.USABILITY, 8.5, "Clear structured output with actionable insights")
        quality.add_dimension(QualityMetric.RELIABILITY, 8.0, "Robust with fallback mechanisms")
        
        # Assess based on AI model availability
        if hasattr(self, 'judge_agent'):
            quality.add_dimension(QualityMetric.PERFORMANCE, 7.5, "AI processing adds latency but provides value")
        else:
            quality.add_dimension(QualityMetric.PERFORMANCE, 8.5, "Rule-based fallback is fast")


# Export the tool
__all__ = ["TestJudgeTool", "TestResult", "TestSuite", "TestJudgment", "TestCategory", "TestResultSeverity"]