#!/usr/bin/env python3
"""
Performance Metrics Collection

Provides PerformanceMetrics class for collecting and analyzing system performance data.
Used by the integrated monitoring system for production readiness validation.
"""

import psutil
import statistics
import time
from typing import Dict, List


class PerformanceMetrics:
    """Collects and analyzes performance metrics."""
    
    def __init__(self):
        self.response_times: List[float] = []
        self.streaming_intervals: List[float] = []
        self.tool_success_rates: Dict[str, List[bool]] = {}
        self.memory_usage: List[float] = []
        self.start_memory = self._get_memory_usage()
        self.successful_requests = 0
        self.total_requests = 0
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024
    
    def record_response_time(self, response_time: float):
        """Record a response time measurement."""
        self.response_times.append(response_time)
    
    def record_streaming_interval(self, interval: float):
        """Record a streaming update interval."""
        self.streaming_intervals.append(interval)
    
    def record_tool_result(self, tool_name: str, success: bool):
        """Record tool execution result."""
        if tool_name not in self.tool_success_rates:
            self.tool_success_rates[tool_name] = []
        self.tool_success_rates[tool_name].append(success)
    
    def record_memory_usage(self):
        """Record current memory usage."""
        self.memory_usage.append(self._get_memory_usage())
    
    def get_summary(self) -> Dict:
        """Get comprehensive performance summary."""
        summary = {
            "response_times": self._analyze_response_times(),
            "streaming_performance": self._analyze_streaming(),
            "tool_performance": self._analyze_tools(),
            "memory_usage": self._analyze_memory(),
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "success_rate": self.successful_requests / self.total_requests if self.total_requests > 0 else 0.0
        }
        
        return summary
    
    def _analyze_response_times(self) -> Dict:
        """Analyze response time performance."""
        if not self.response_times:
            return {"mean": 0, "p95": 0, "max": 0, "under_2s_percent": 0}
        
        mean_time = statistics.mean(self.response_times)
        p95_time = self._percentile(self.response_times, 95)
        max_time = max(self.response_times)
        under_2s = sum(1 for t in self.response_times if t < 2.0)
        under_2s_percent = (under_2s / len(self.response_times)) * 100
        
        return {
            "mean": mean_time,
            "p95": p95_time,
            "max": max_time,
            "under_2s_percent": under_2s_percent,
            "count": len(self.response_times)
        }
    
    def _analyze_streaming(self) -> Dict:
        """Analyze streaming performance."""
        if not self.streaming_intervals:
            return {"mean_interval": 0, "in_target_range": 0}
        
        mean_interval = statistics.mean(self.streaming_intervals)
        in_range = sum(1 for i in self.streaming_intervals if 2.0 <= i <= 3.0)
        in_range_percent = (in_range / len(self.streaming_intervals)) * 100
        
        return {
            "mean_interval": mean_interval,
            "in_target_range": in_range_percent,
            "count": len(self.streaming_intervals)
        }
    
    def _analyze_tools(self) -> Dict:
        """Analyze tool performance."""
        tool_analysis = {}
        overall_success = 0
        overall_total = 0
        
        for tool_name, results in self.tool_success_rates.items():
            successes = sum(results)
            total = len(results)
            success_rate = successes / total if total > 0 else 0
            
            tool_analysis[tool_name] = {
                "success_rate": success_rate,
                "successes": successes,
                "total": total
            }
            
            overall_success += successes
            overall_total += total
        
        overall_rate = overall_success / overall_total if overall_total > 0 else 0
        
        return {
            "overall_success_rate": overall_rate,
            "tools": tool_analysis
        }
    
    def _analyze_memory(self) -> Dict:
        """Analyze memory usage."""
        if not self.memory_usage:
            return {"current": self._get_memory_usage(), "peak": 0, "growth": 0}
        
        current = self._get_memory_usage()
        peak = max(self.memory_usage)
        growth = current - self.start_memory
        
        return {
            "current": current,
            "peak": peak,
            "growth": growth,
            "start": self.start_memory
        }
    
    def _percentile(self, data: List[float], percentile: float) -> float:
        """Calculate percentile of data."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int((percentile / 100) * len(sorted_data))
        return sorted_data[min(index, len(sorted_data) - 1)]