# Daydream System Documentation

## Overview

The Daydream System is an autonomous AI-powered analysis and reflection framework that performs deep codebase exploration and generates architectural insights. It operates on a 6-phase execution lifecycle, running during non-office hours to minimize system impact while providing continuous architectural awareness and improvement recommendations.

## System Architecture

### Core Components

```python
@dataclass
class DaydreamSession:
    """Complete daydream session with integrated lifecycle management."""
    session_id: str
    start_time: datetime
    phase: str = 'initializing'
    
    # Context data
    system_health: Dict[str, Any]
    workspace_analysis: Dict[str, Any]
    development_trends: Dict[str, Any]
    system_metrics: Dict[str, Any]
    recent_activity: List[Dict[str, Any]]
    
    # Analysis results
    insights: str = ""
    analysis_duration: float = 0.0
    cleanup_summary: Dict[str, Any]
```

### UnifiedDaydreamSystem Class

The `UnifiedDaydreamSystem` orchestrates the entire daydream lifecycle:
- Session timeout: 2 hours maximum per analysis
- Integrated cleanup and resource management
- Emergency recovery procedures
- Comprehensive logging and tracking

## 6-Phase Execution Lifecycle

### Phase 1: System Readiness Check

**Purpose**: Ensure system is ready for resource-intensive analysis

**Operations**:
```python
def _check_system_readiness(self, session: DaydreamSession) -> bool:
    # Get pending promise count
    # Check for stalled tasks
    # Evaluate system load
    
    if session.system_health['pending_count'] > 5:
        return False  # System too busy
    
    return True  # Ready for analysis
```

**Criteria**:
- Pending tasks < 5
- No critical system alerts
- Available memory > 400MB
- Office hours check (skip 9 AM - 6 PM)

### Phase 2: Pre-Analysis Cleanup

**Purpose**: Free resources before intensive AI analysis

**Operations**:
```python
def _cleanup_before_analysis(self, session: DaydreamSession) -> None:
    cleanup_stats = {
        'claude_processes_killed': 0,
        'aider_processes_killed': 0,
        'temp_files_cleaned': 0,
        'memory_freed_mb': 0
    }
```

**Cleanup Tasks**:
- Kill Claude Code processes > 24 hours old
- Terminate orphaned Aider sessions
- Remove temporary analysis files (`/tmp/*daydream*.md`)
- Archive old insight files (keep last 10)

### Phase 3: Comprehensive Context Gathering

**Purpose**: Collect rich context for AI analysis

**Data Collection**:

1. **Workspace Analysis**:
   ```python
   def _analyze_all_workspaces(self) -> Dict[str, Any]:
       # Load workspace_config.json
       # Analyze each configured workspace
       # Collect git status, tech stack, quality metrics
   ```

2. **System Metrics**:
   - Task success rates
   - Task type distribution
   - Performance trends
   - Error patterns

3. **Development Trends**:
   - Recent activity patterns
   - Workspace focus areas
   - Productivity metrics
   - Technology usage

4. **Recent Activity**:
   - Last 7 days of tasks
   - Success/failure patterns
   - User interaction trends

### Phase 4: AI Analysis Execution

**Purpose**: Perform deep codebase analysis using Aider

**Analysis Configuration**:
```python
cmd = [
    '/Users/valorengels/.local/bin/aider',
    '--model', 'ollama_chat/gemma3:4b-it-qat',
    '--no-git',
    '--yes',
    '--message', f'Read analysis prompt from {prompt_file}...'
] + key_files
```

**Key Files Analyzed**:
- `main.py` - Core application entry
- `tasks/promise_tasks.py` - Task management system
- `agents/valor/agent.py` - Agent implementation
- `mcp_servers/social_tools.py` - Tool integrations
- `integrations/telegram/handlers.py` - Message handling
- `utilities/database.py` - Data persistence
- `CLAUDE.md` - Project documentation

**Analysis Focus Areas**:
1. Architecture Patterns & Design
2. Code Quality & Technical Health
3. Development Velocity & Productivity
4. Technology Stack & Dependencies
5. Strategic Opportunities
6. Future Direction & Vision

### Phase 5: Output Processing and Archival

**Purpose**: Process insights and create persistent outputs

**Operations**:
```python
def _process_insights_and_output(self, session: DaydreamSession) -> None:
    # Log insights to console
    self._log_insights_to_console(session.insights)
    
    # Write to persistent file
    self._write_and_archive_insights(session)
```

**Output Format**:
```markdown
# Daydream Insights - Session {session_id}

**Generated:** {timestamp}
**Analysis Duration:** {duration}s
**Workspaces Analyzed:** {count}

---

{AI-generated insights}
```

**Archive Management**:
- Current insights: `logs/daydream_insights.md`
- Historical archives: `logs/daydream_insights_{timestamp}.md`
- Web interface: `/daydreams` endpoint

### Phase 6: Post-Analysis Cleanup

**Purpose**: Clean up analysis resources and prepare for next cycle

**Operations**:
```python
def _cleanup_after_analysis(self, session: DaydreamSession) -> None:
    # Kill active Aider process
    self._cleanup_current_aider()
    
    # Clean analysis artifacts
    self._cleanup_temp_files()
    
    # Archive insights
    self._archive_old_insights()
    
    # Generate session summary
    self._generate_session_summary(session)
```

## Resource Management

### Process Management

**Claude Code Cleanup**:
- Identifies processes by name pattern
- Targets processes > 24 hours old
- Uses SIGTERM for graceful shutdown
- Tracks cleanup statistics

**Aider Session Management**:
- pkill pattern: `aider.*daydream`
- Emergency cleanup for failed sessions
- Timeout enforcement (2 hours max)

### Memory Optimization

**Strategies**:
1. Pre-analysis cleanup frees ~100-200MB
2. Transaction-scoped database operations
3. Streaming file processing
4. Temporary file cleanup

**Resource Limits**:
```python
self.session_timeout = 7200  # 2 hours
max_workspaces_per_cycle = 3  # Limit workspace analysis
database_timeout = 5  # 5 second DB operations
```

### Temporary File Management

**Cleanup Patterns**:
```python
temp_patterns = [
    '/tmp/tmp*daydream*.md',
    '/tmp/tmp*analysis*.md',
    '/var/folders/*/T/tmp*daydream*.md'
]
```

**Archive Rotation**:
- Keep last 10 insight files
- Timestamp-based naming
- Automatic rotation on new analysis

## Analysis Capabilities

### Workspace Analysis

**Per-Workspace Metrics**:
```python
workspace_info = {
    'name': workspace_name,
    'directory': working_dir,
    'exists': Path(working_dir).exists(),
    'git_status': git_summary,
    'tech_stack': detected_technologies,
    'complexity_metrics': {
        'total_python_files': count,
        'files_analyzed': analyzed,
        'avg_lines_per_file': average,
        'max_complexity': score
    },
    'quality_indicators': {
        'has_tests': bool,
        'has_docs': bool,
        'has_ci': bool
    }
}
```

**Technology Detection**:
- Python (requirements.txt, setup.py)
- JavaScript/TypeScript (package.json)
- Configuration files
- Documentation presence

### System Metrics Analysis

**Collected Metrics**:
- Task completion rates
- Error frequency and types
- Performance trends
- User activity patterns
- Resource utilization

**Trend Analysis**:
- 7-day rolling windows
- Peak usage identification
- Pattern recognition
- Anomaly detection

### Development Pattern Recognition

**Analyzed Patterns**:
1. Code modification frequency
2. Feature development velocity
3. Bug fix patterns
4. Documentation updates
5. Test coverage changes

### Architectural Recommendations

**AI-Generated Insights**:
- Code organization improvements
- Performance optimization opportunities
- Security enhancements
- Technical debt identification
- Refactoring suggestions
- Tool and process improvements

## Operational Integration

### Execution Schedule

**Cron Configuration**:
```python
@huey.periodic_task(crontab(minute=0, hour='18,21,0,3,6'))
```

**Schedule Times**:
- 6:00 PM - Evening analysis
- 9:00 PM - Night analysis
- 12:00 AM - Midnight analysis
- 3:00 AM - Early morning analysis
- 6:00 AM - Dawn analysis

**Office Hours Protection**:
- Skip execution 9 AM - 6 PM
- Prevents resource competition
- Maintains system responsiveness

### System Load Management

**Load Balancing**:
```python
# Check system readiness
if pending_promises > 5:
    skip_analysis()

# Limit workspace analysis
max_workspaces = 3

# Database transaction limits
timeout = 5  # seconds
```

**Resource Protection**:
- Memory monitoring
- CPU usage checks
- Process count limits
- Emergency cleanup triggers

### Error Handling and Recovery

**Error Categories**:
1. **Timeout Errors**: 2-hour limit exceeded
2. **Resource Errors**: Memory/CPU exhaustion
3. **Analysis Errors**: Aider failures
4. **System Errors**: Database locks, file access

**Recovery Procedures**:
```python
try:
    # Normal execution
except Exception as e:
    logger.error(f"Session failed: {e}")
    daydream_system._emergency_cleanup(session)
```

**Emergency Cleanup**:
- Force kill all analysis processes
- Clean all temporary files
- Release database locks
- Log failure details

### Output Management

**File Outputs**:
- Primary: `logs/daydream_insights.md`
- Archives: `logs/daydream_insights_{timestamp}.md`
- Web view: Available at `/daydreams`

**Web Interface**:
```python
@app.get("/daydreams", response_class=HTMLResponse)
async def get_daydreams():
    """Beautiful HTML view of all daydream sessions"""
```

**Features**:
- Chronological session listing
- Formatted markdown rendering
- Session metadata display
- Search and filtering

## Configuration and Customization

### Workspace Configuration

**File**: `config/workspace_config.json`
```json
{
  "workspaces": {
    "project_name": {
      "working_directory": "/path/to/project",
      "daydream_priority": 8,
      "chat_id": "telegram_chat_id"
    }
  }
}
```

**Priority System**:
- Scale: 1-10 (10 = highest priority)
- Affects analysis order
- Higher priority = more detailed analysis

### Analysis Customization

**Prompt Templates**:
- Located in `build_unified_analysis_prompt()`
- Customizable focus areas
- Adjustable analysis depth

**Model Configuration**:
```python
'--model', 'ollama_chat/gemma3:4b-it-qat'
```
- Local model for privacy
- Configurable model selection
- Adjustable generation parameters

### Schedule Customization

**Modify Execution Times**:
```python
# Change to business hours analysis
@huey.periodic_task(crontab(minute=0, hour='9,12,15'))

# Increase frequency
@huey.periodic_task(crontab(minute=0, hour='*/3'))
```

### Resource Limits

**Adjustable Parameters**:
```python
class UnifiedDaydreamSystem:
    def __init__(self):
        self.session_timeout = 7200  # Seconds
        self.max_workspaces = 3
        self.cleanup_age_hours = 24
        self.archive_keep_count = 10
```

## Monitoring and Troubleshooting

### Log Analysis

**Key Log Patterns**:
```
🧠 Starting unified daydream session
🧠 Session {id}: {phase} → {new_phase}
🧠 System idle ✓ - Ready for daydream analysis
🧹 Pre-analysis cleanup starting...
🧠 ✨ AI Daydream Insights:
```

### Common Issues

**System Too Busy**:
- Reduce pending task threshold
- Adjust schedule to quieter times
- Increase cleanup aggressiveness

**Analysis Timeouts**:
- Reduce workspace count
- Simplify analysis prompt
- Use faster model

**Resource Exhaustion**:
- Increase pre-cleanup wait
- Reduce concurrent operations
- Monitor memory usage

### Performance Metrics

**Session Tracking**:
- Total duration
- Analysis duration
- Cleanup effectiveness
- Resource usage

**Quality Metrics**:
- Insight generation rate
- Actionable recommendations
- Error frequency
- Completion rate

## Best Practices

### Operational Guidelines

1. **Monitor Resource Usage**: Check `/resources/status` before heavy operations
2. **Review Insights Regularly**: Check `/daydreams` weekly
3. **Adjust Priorities**: Update workspace priorities based on activity
4. **Clean Archives**: Periodically remove old insight files

### Development Integration

1. **Act on Insights**: Review and implement recommendations
2. **Track Improvements**: Monitor metrics after changes
3. **Provide Feedback**: Adjust prompts based on quality
4. **Share Knowledge**: Distribute insights to team

### System Maintenance

1. **Update Models**: Keep AI models current
2. **Tune Parameters**: Adjust based on system growth
3. **Monitor Health**: Track long-term trends
4. **Plan Capacity**: Scale resources as needed

## Conclusion

The Daydream System provides autonomous architectural analysis and continuous improvement insights through:

- **Intelligent Scheduling**: Non-intrusive execution during off-hours
- **Comprehensive Analysis**: Deep codebase exploration with AI
- **Resource Protection**: Careful management and cleanup
- **Actionable Insights**: Specific recommendations for improvement
- **Historical Tracking**: Archive of architectural evolution

This self-reflective capability enables the system to continuously improve its own architecture and provide valuable insights for development direction.