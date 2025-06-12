# Unified Daydream System & Cleanup Integration Plan

## Overview

This plan consolidates the fragmented daydream system into a unified architecture with integrated cleanup lifecycle management. The current system has 10+ scattered functions that will be merged into a single, well-structured class-based system.

## Current Architecture Problems

### Fragmented Function Structure (10+ functions)
```
daydream_and_reflect()              # Entry point
├── gather_system_health_data()     # Check if busy
├── gather_daydream_context()       # Main context gathering
│   ├── analyze_workspace_for_daydream()
│   ├── gather_system_metrics()     
│   └── gather_development_trends()
├── aider_daydream_analysis()       # AI analysis execution
│   ├── build_aider_daydream_prompt()
│   └── build_daydream_prompt()     # Duplicate functionality!
└── log_daydream_insights()         # Output logging
```

### Issues
- **Scattered Logic**: 10 functions across 600+ lines
- **No Cleanup Integration**: Claude Code processes accumulate indefinitely
- **Duplicate Code**: Multiple prompt builders with overlapping functionality
- **Poor Error Recovery**: No centralized error handling or emergency cleanup
- **Limited Monitoring**: No session tracking or performance metrics
- **Maintenance Burden**: Changes require touching multiple functions

## New Unified Architecture

### Core Components

#### 1. DaydreamSession Data Class
```python
@dataclass
class DaydreamSession:
    """Complete daydream session with integrated lifecycle"""
    
    # Session metadata
    session_id: str
    start_time: datetime
    phase: str  # Current execution phase
    
    # Context data
    system_health: Dict[str, Any] = field(default_factory=dict)
    workspace_analysis: Dict[str, Any] = field(default_factory=dict)
    development_trends: Dict[str, Any] = field(default_factory=dict)
    
    # Analysis results
    insights: str = ""
    analysis_duration: float = 0.0
    cleanup_summary: Dict[str, Any] = field(default_factory=dict)
```

#### 2. UnifiedDaydreamSystem Class
```python
class UnifiedDaydreamSystem:
    """Unified daydream system with integrated cleanup and analysis"""
    
    # Phase-based execution methods
    def _check_system_readiness(self, session: DaydreamSession) -> bool
    def _cleanup_before_analysis(self, session: DaydreamSession) -> None
    def _gather_comprehensive_context(self, session: DaydreamSession) -> None
    def _execute_ai_analysis(self, session: DaydreamSession) -> None
    def _process_insights_and_output(self, session: DaydreamSession) -> None
    def _cleanup_after_analysis(self, session: DaydreamSession) -> None
```

### Execution Flow (6 Phases)

#### Phase 1: System Readiness Check
- Check pending task queue (skip if >5 pending)
- Validate Ollama availability
- Ensure sufficient system resources
- **Decision Point**: Continue or skip cycle

#### Phase 2: Pre-Analysis Cleanup
- **Kill old Claude Code processes** (24+ hours old)
- **Kill orphaned Aider processes** from previous daydreams
- **Clean temp analysis files** in `/tmp/`
- **Rotate large log files** if needed
- **Track cleanup statistics**

#### Phase 3: Comprehensive Context Gathering
- **Workspace Analysis**: Git status, tech stack, complexity metrics
- **Development Trends**: Weekly patterns, completion rates, activity
- **System Metrics**: Success rates, task distribution, performance
- **Recent Activity**: Last 7 days of promise queue activity

#### Phase 4: AI Analysis Execution
- **Build unified prompt** (merge duplicate prompt builders)
- **Execute Aider + Ollama analysis** with timeout management
- **Monitor resource usage** during analysis
- **Handle analysis failures** gracefully

#### Phase 5: Insights Processing & Output
- **Log insights to console** with formatted output
- **Write insights to file** (`logs/daydream_insights.md`)
- **Archive old insights** (keep last 10 versions)
- **Generate session summary** for monitoring

#### Phase 6: Post-Analysis Cleanup
- **Kill current Aider session** if still running
- **Clean temporary prompt files**
- **Archive large insight files**
- **Reset session state** for next cycle

## Cleanup Integration Strategy

### Pre-Analysis Cleanup (Optimal Timing)
**Why Before Analysis:**
- System is confirmed idle (perfect cleanup timing)
- Before resource-intensive Ollama work (need maximum memory)
- Natural 6-hour checkpoint for housekeeping
- Clean environment for analysis (no interference)

**What to Clean:**
```python
def _cleanup_before_analysis(self, session: DaydreamSession) -> None:
    cleanup_stats = {
        'claude_processes_killed': self._cleanup_old_claude_processes(),
        'aider_processes_killed': self._cleanup_old_aider_processes(),
        'temp_files_cleaned': self._cleanup_temp_files(),
        'memory_freed_mb': self._calculate_memory_freed()
    }
    session.cleanup_summary['pre_analysis'] = cleanup_stats
```

### Post-Analysis Cleanup (Resource Management)
**Why After Analysis:**
- Clean up current analysis artifacts
- Prepare for next cycle
- Archive insights for long-term storage
- Reset session state

**What to Clean:**
```python
def _cleanup_after_analysis(self, session: DaydreamSession) -> None:
    cleanup_stats = {
        'current_aider_killed': self._cleanup_current_aider(),
        'insights_archived': self._archive_old_insights(),
        'temp_files_cleaned': self._cleanup_temp_files()
    }
    session.cleanup_summary['post_analysis'] = cleanup_stats
```

## Implementation Plan

### Phase 1: Create Core Infrastructure
- [ ] Create `DaydreamSession` dataclass in `tasks/promise_tasks.py`
- [ ] Create `UnifiedDaydreamSystem` class skeleton
- [ ] Add session ID generation and phase tracking
- [ ] Implement basic logging with session correlation

### Phase 2: Implement Cleanup Methods
- [ ] `_cleanup_old_claude_processes()` - Kill processes 24+ hours old
- [ ] `_cleanup_old_aider_processes()` - Kill orphaned Aider sessions
- [ ] `_cleanup_temp_files()` - Clean `/tmp/` analysis artifacts
- [ ] `_cleanup_current_aider()` - Terminate current analysis session
- [ ] `_archive_old_insights()` - Manage insights file rotation

### Phase 3: Migrate Existing Functionality
- [ ] Migrate `gather_system_health_data()` → `_check_system_readiness()`
- [ ] Migrate context gathering functions → `_gather_comprehensive_context()`
- [ ] Merge prompt builders → `_build_unified_analysis_prompt()`
- [ ] Migrate `aider_daydream_analysis()` → `_execute_ai_analysis()`
- [ ] Migrate `log_daydream_insights()` → `_process_insights_and_output()`

### Phase 4: Integration & Testing
- [ ] Replace `@huey.periodic_task` decorator with unified entry point
- [ ] Test each phase independently
- [ ] Validate cleanup effectiveness (process counts, memory usage)
- [ ] Test error recovery and emergency cleanup
- [ ] Performance validation (analysis timing, resource usage)

### Phase 5: Cleanup & Documentation
- [ ] Remove old scattered functions
- [ ] Update documentation references
- [ ] Add comprehensive logging examples
- [ ] Create monitoring dashboard integration points

## Benefits

### 1. Architectural Benefits
- **Single Source of Truth**: One class replaces 10+ functions
- **Clear Execution Model**: Phase-based progression with state tracking
- **Integrated Error Handling**: Centralized exception management
- **Better Testability**: Isolated phase methods for unit testing

### 2. Resource Management Benefits
- **Automatic Cleanup**: Integrated with natural system cycles
- **Memory Optimization**: Clean environment before intensive analysis
- **Process Management**: Prevent Claude Code process accumulation
- **Storage Management**: Automatic archival of large insight files

### 3. Operational Benefits
- **Session Tracking**: Full lifecycle monitoring with correlation IDs
- **Performance Metrics**: Analysis timing and resource usage
- **Better Debugging**: Phase-based logging with context
- **Health Monitoring**: Cleanup statistics and system state

### 4. Maintenance Benefits
- **Reduced Complexity**: Single class vs. scattered functions
- **Clear Separation**: Each phase has distinct responsibilities
- **Easy Extension**: Add new phases or modify existing ones
- **Configuration**: Centralized settings and thresholds

## Success Metrics

### Resource Management
- **Claude Code Processes**: <5 processes at any time (vs. current 25+)
- **Memory Usage**: <500MB for daydream system (vs. current 3GB+)
- **Disk Usage**: Insight files <50MB total (with archival)

### Performance
- **Analysis Time**: <5 minutes per session
- **Cleanup Time**: <30 seconds pre/post analysis
- **System Recovery**: <60 seconds for emergency cleanup

### Reliability
- **Session Success Rate**: >95% successful completions
- **Error Recovery**: <5% sessions requiring emergency cleanup
- **Resource Leaks**: 0 permanent process/memory leaks

## Risk Mitigation

### Technical Risks
- **Analysis Timeout**: 5-minute hard timeout with graceful fallback
- **Ollama Unavailability**: Skip analysis, perform cleanup only
- **Process Kill Failures**: Emergency cleanup with SIGKILL fallback
- **Disk Space**: Automatic archival before running out of space

### Operational Risks
- **Session Conflicts**: Unique session IDs prevent overlapping executions
- **Resource Starvation**: Pre-cleanup ensures available resources
- **Log Flooding**: Rate-limited logging with session correlation
- **Configuration Errors**: Graceful degradation with defaults

## Future Enhancements

### Monitoring Integration
- [ ] Export session metrics to monitoring dashboard
- [ ] Alert on cleanup failure or resource thresholds
- [ ] Track analysis quality and insight usefulness

### Advanced Cleanup
- [ ] Machine learning-based process lifecycle prediction
- [ ] Dynamic cleanup thresholds based on system load
- [ ] Integration with system monitoring for proactive cleanup

### Analysis Enhancement
- [ ] Multi-model analysis (different models for different workspaces)
- [ ] Incremental analysis (only analyze changed workspaces)
- [ ] Real-time insight streaming during analysis

---

This plan transforms the daydream system from a collection of scattered functions into a robust, maintainable, and resource-efficient unified system with integrated cleanup lifecycle management.