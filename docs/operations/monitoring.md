# Operations and Monitoring Documentation

## Overview

This document provides comprehensive operational procedures and monitoring strategies for the AI system. It covers health monitoring, logging, maintenance tasks, troubleshooting procedures, and operational runbooks for production deployment.

## Monitoring Systems

### 1. Health Check Endpoints

The system provides multiple health monitoring endpoints for comprehensive system status:

#### Core Health Endpoint
```http
GET /health
```
**Response**:
```json
{
  "status": "healthy",
  "telegram": "connected"
}
```

#### Detailed Status Endpoints

**Telegram Status**:
```http
GET /telegram/status
```
- Reports Telegram client connection state
- Shows authenticated user information
- Indicates initialization status

**Resource Status**:
```http
GET /resources/status
```
```json
{
  "health": {
    "current_resources": {
      "memory_mb": 345.2,
      "cpu_percent": 25.5,
      "active_sessions": 12,
      "memory_utilization_percent": 69.0
    },
    "health_score": 87.5,
    "alerts": [],
    "recommendations": ["System performing well"]
  },
  "emergency": {
    "emergency_mode": false,
    "cpu_throttling": false,
    "emergency_cleanups": 0
  }
}
```

**Restart Status**:
```http
GET /restart/status
```
- Shows auto-restart configuration
- Displays restart history
- Indicates scheduled restarts

### 2. Integrated Monitoring System

The `IntegratedMonitoringSystem` provides unified monitoring with automatic optimization:

```python
class IntegratedMonitoringSystem:
    """
    Unified monitoring and optimization system for production deployment.
    
    Components:
    - ContextWindowManager: Token and message optimization
    - StreamingOptimizer: Response streaming optimization
    - ResourceMonitor: System resource tracking
    """
```

**Key Features**:
- Health checks every 30 seconds
- Optimization cycles every 60 seconds
- Automatic cleanup every 5 minutes
- Alert generation and escalation

### 3. Performance Metrics Collection

**Resource Snapshots**:
```python
@dataclass
class ResourceSnapshot:
    timestamp: datetime
    memory_mb: float
    cpu_percent: float
    active_sessions: int
    total_processes: int
    disk_usage_percent: float
    network_io_bytes: Tuple[int, int]
```

**Health Score Calculation**:
```python
def calculate_health_score(self) -> float:
    """Calculate overall system health (0-100)."""
    memory_health = max(0, 100 - (memory_percent * 1.5))
    cpu_health = max(0, 100 - (cpu_percent * 1.2))
    session_health = max(0, 100 - (session_load * 100))
    
    return (memory_health * 0.4 + cpu_health * 0.3 + 
            session_health * 0.3)
```

**Performance Baselines**:
- Memory: 300MB baseline, 30MB per session
- CPU: 20% baseline, 80% sustained max
- Sessions: 50+ concurrent users
- Response time: <2s text, <5s media

### 4. Alert Generation and Escalation

**Alert Levels**:
- **Low**: Informational (logged only)
- **Medium**: Warning (logged + callback)
- **High**: Action required (immediate callback)
- **Critical**: Emergency action (auto-restart trigger)

**Alert Types**:
```python
@dataclass
class PerformanceAlert:
    alert_type: str  # memory_warning, cpu_spike, session_overflow
    severity: str    # low, medium, high, critical
    message: str
    timestamp: datetime
    resource_snapshot: ResourceSnapshot
    recommended_action: str
```

## Logging Strategy

### 1. Log Structure and Organization

**Log Files**:
- `logs/system.log` - Main application logs (rotating, 10MB max)
- `logs/tasks.log` - Background task execution logs
- `logs/telegram.log` - Telegram-specific operations

**Log Format**:
```
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

### 2. Log Levels and Categorization

**Level Usage**:
- **DEBUG**: Detailed execution flow (disabled in production)
- **INFO**: Normal operations, health checks, status updates
- **WARNING**: Recoverable issues, resource warnings
- **ERROR**: Failures requiring attention
- **CRITICAL**: System-threatening issues

**Log Categories**:
- 🚀 Startup/initialization
- 💓 Health checks
- 🔄 Resource management
- ⚠️ Warnings
- ❌ Errors
- 🛑 Shutdown

### 3. Log Rotation and Retention

**Rotation Configuration**:
```python
file_handler = logging.handlers.RotatingFileHandler(
    'logs/system.log', 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=3           # Keep 3 backups
)
```

**Retention Policy**:
- System logs: 3 rotations × 10MB = 30MB total
- Task logs: Daily rotation, 7 days retention
- Database cleanup: Tasks older than 7 days

### 4. Centralized Logging

All components log through Python's logging framework:
- Unified format across components
- Centralized configuration in `main.py`
- Both file and console output
- Structured logging for parsing

## Operational Procedures

### 1. Startup Procedures

**Standard Startup** (`scripts/start.sh`):
```bash
# 1. Check existing processes
check_server()      # Verify no existing instance
check_telegram_auth()  # Validate Telegram session

# 2. Database recovery
recover_database_locks()  # Release SQLite locks
test_database_connectivity()  # Verify DB access

# 3. Initialize services
initialize_database()  # Create/update tables
start_huey()          # Start task queue
start_server()        # Launch FastAPI

# 4. Enable monitoring
resource_monitor.start_monitoring()
auto_restart_manager.start_monitoring()
```

**Startup Sequence**:
1. Pre-flight checks (5s)
2. Database initialization (2s)
3. Huey consumer startup (3s)
4. FastAPI server launch (5s)
5. Telegram client connection (2-10s)
6. Health monitoring activation (1s)

### 2. Shutdown Procedures

**Graceful Shutdown** (`scripts/stop.sh`):
```bash
# 1. Stop services gracefully
stop_server()    # SIGTERM to FastAPI
stop_huey()      # SIGTERM to Huey

# 2. Wait for completion (2s timeout)
# 3. Force termination if needed (SIGKILL)
# 4. Cleanup orphaned processes
# 5. Release database locks
```

**Emergency Shutdown**:
- Triggered by resource limits
- Protects active sessions
- 5-minute grace period
- Automatic restart after

### 3. Database Maintenance

**Automatic Maintenance**:
```python
# Runs every 24 hours
def cleanup_old_tasks():
    """Delete tasks older than 7 days"""
    conn.execute("""
        DELETE FROM server_tasks 
        WHERE status IN ('completed', 'failed') 
        AND processed_at < datetime('now', '-7 days')
    """)
```

**Manual Maintenance**:
```bash
# WAL checkpoint and vacuum
sqlite3 data/system.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 data/system.db "VACUUM;"

# Analyze for query optimization
sqlite3 data/system.db "ANALYZE;"
```

### 4. Performance Tuning

**Memory Optimization**:
- Context window pruning at 100 messages
- Session cleanup after 24 hours
- Emergency cleanup at 600MB
- Auto-restart at 1000MB

**CPU Optimization**:
- Request throttling at 85% CPU
- Streaming interval adaptation
- Background task prioritization
- Process affinity settings

## Troubleshooting Guide

### 1. Common Issues and Solutions

#### Database Lock Errors
**Symptoms**: "database is locked" errors
**Solution**:
```bash
# Run database recovery
scripts/start.sh  # Includes automatic recovery

# Manual recovery
lsof data/*.db | awk '{print $2}' | xargs kill -9
sqlite3 data/system.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

#### High Memory Usage
**Symptoms**: Memory > 800MB, slow responses
**Solution**:
1. Check `/resources/status` for session count
2. Trigger manual cleanup via API
3. Review context window sizes
4. Consider restart if > 1GB

#### Telegram Disconnections
**Symptoms**: "Telegram: disconnected" in health checks
**Solution**:
```bash
# Re-authenticate
scripts/telegram_logout.sh
scripts/telegram_login.sh
scripts/start.sh
```

#### Slow Response Times
**Symptoms**: >5s response latency
**Solution**:
1. Check CPU usage (`/resources/status`)
2. Review active tool usage
3. Examine streaming optimizer metrics
4. Consider session limit reduction

### 2. Diagnostic Procedures

**System Health Check**:
```bash
# 1. Check all services
curl http://localhost:9000/health
curl http://localhost:9000/resources/status
curl http://localhost:9000/telegram/status

# 2. Review logs
tail -f logs/system.log | grep -E "(ERROR|WARNING|CRITICAL)"

# 3. Database health
sqlite3 data/system.db "PRAGMA integrity_check;"
```

**Performance Analysis**:
```python
# Get detailed metrics
GET /resources/status

# Review performance history
GET /sessions/report

# Check restart history  
GET /restart/status
```

### 3. Error Investigation

**Log Analysis Pattern**:
```bash
# Find error context (10 lines before/after)
grep -B 10 -A 10 "ERROR" logs/system.log

# Track specific session
grep "session_id_here" logs/system.log

# Resource warnings
grep "emergency\|critical\|warning" logs/system.log
```

**Common Error Patterns**:
- `ConnectionError`: Network/API issues
- `TimeoutError`: Slow external services
- `MemoryError`: Resource exhaustion
- `DatabaseError`: SQLite locks/corruption

### 4. Recovery Procedures

**Service Recovery**:
```bash
# Full restart
scripts/stop.sh
scripts/start.sh

# Selective restart
curl -X POST http://localhost:9000/restart/force
```

**Data Recovery**:
```bash
# Backup before recovery
cp data/system.db data/system.db.backup

# Recover from WAL
sqlite3 data/system.db "PRAGMA wal_checkpoint(RESTART);"

# Rebuild indexes
sqlite3 data/system.db "REINDEX;"
```

## Maintenance Tasks

### 1. Regular Maintenance Schedule

**Hourly**:
- Resource usage check
- Active session review
- Alert queue processing

**Daily**:
- Database task cleanup
- Log rotation check
- Performance baseline update

**Weekly**:
- Database optimization (VACUUM)
- Log archive compression
- Restart history review

**Monthly**:
- Full system health audit
- Performance trend analysis
- Capacity planning review

### 2. Database Optimization

**Automated Tasks**:
```python
# Context optimization (every 100 messages)
context_manager.optimize_context()

# Session cleanup (every 5 minutes)
resource_monitor.cleanup_inactive_sessions()

# Task deletion (daily)
cleanup_old_tasks()
```

**Manual Optimization**:
```sql
-- Analyze tables for query optimization
ANALYZE;

-- Rebuild indexes
REINDEX;

-- Full database vacuum
VACUUM;

-- Check integrity
PRAGMA integrity_check;
```

### 3. Log Management

**Rotation Management**:
- Automatic via RotatingFileHandler
- Manual compression of old logs
- Archive to cold storage after 30 days
- Delete archives after 90 days

**Log Analysis Tools**:
```bash
# Error frequency
grep -c "ERROR" logs/system.log

# Response time analysis
grep "Response time:" logs/system.log | awk '{print $NF}' | sort -n

# Memory usage trends
grep "memory_mb" logs/system.log | tail -100
```

### 4. Performance Monitoring

**Key Metrics to Track**:
- Memory usage per session
- Average response time
- Tool execution frequency
- Error rate by type
- Session duration distribution

**Performance Reports**:
```python
# Generate session report
GET /sessions/report

# Resource utilization trends
GET /resources/status  # Poll every 5 minutes

# Health score history
# Tracked in resource_history deque
```

## Operational Runbooks

### 1. Startup Runbook

```markdown
## System Startup Procedure

1. **Pre-checks** (2 min)
   - [ ] Verify no existing processes: `ps aux | grep -E "(uvicorn|huey)"`
   - [ ] Check disk space: `df -h`
   - [ ] Verify network connectivity: `ping -c 3 api.telegram.org`

2. **Start Services** (5 min)
   - [ ] Run startup script: `scripts/start.sh`
   - [ ] Monitor startup logs: `tail -f logs/system.log`
   - [ ] Verify all services started

3. **Post-startup Validation** (2 min)
   - [ ] Check health endpoint: `curl http://localhost:9000/health`
   - [ ] Verify Telegram connected: `curl http://localhost:9000/telegram/status`
   - [ ] Confirm resource monitoring: `curl http://localhost:9000/resources/status`

4. **Enable Monitoring** (1 min)
   - [ ] Start monitoring dashboard
   - [ ] Configure alerts
   - [ ] Note baseline metrics
```

### 2. Emergency Response Runbook

```markdown
## High Memory Emergency Response

**Trigger**: Memory usage > 800MB or health score < 60

1. **Immediate Actions** (2 min)
   - [ ] Check current status: `/resources/status`
   - [ ] Identify high-usage sessions: `/sessions/report`
   - [ ] Review recent errors: `tail -100 logs/system.log | grep ERROR`

2. **Mitigation** (5 min)
   - [ ] If memory > 900MB: Trigger immediate restart
   - [ ] If sessions > 80: Cleanup inactive sessions
   - [ ] If context overflow: Force context optimization

3. **Recovery** (10 min)
   - [ ] Monitor memory decrease
   - [ ] Verify service stability
   - [ ] Document incident cause

4. **Prevention** (ongoing)
   - [ ] Adjust resource limits if needed
   - [ ] Review session timeout settings
   - [ ] Update monitoring thresholds
```

### 3. Maintenance Window Runbook

```markdown
## Planned Maintenance Procedure

**Duration**: 30 minutes
**Frequency**: Monthly

1. **Preparation** (5 min)
   - [ ] Announce maintenance window
   - [ ] Backup databases: `cp -r data/ backup/`
   - [ ] Stop accepting new sessions

2. **Maintenance Tasks** (20 min)
   - [ ] Stop services: `scripts/stop.sh`
   - [ ] Database optimization:
     ```sql
     VACUUM;
     ANALYZE;
     REINDEX;
     ```
   - [ ] Log rotation and archival
   - [ ] System updates if needed
   - [ ] Configuration review

3. **Restart and Validation** (5 min)
   - [ ] Start services: `scripts/start.sh`
   - [ ] Run health checks
   - [ ] Verify full functionality
   - [ ] Clear maintenance notice
```

## Monitoring Best Practices

### 1. Proactive Monitoring

- Set up alerts for health score < 70
- Monitor memory growth rate, not just absolute value
- Track error frequency trends
- Review session patterns for anomalies

### 2. Capacity Planning

- Track peak usage times
- Monitor resource usage growth
- Plan for 2x current peak capacity
- Regular load testing

### 3. Incident Response

- Maintain runbooks for common scenarios
- Document all incidents and resolutions
- Regular drill exercises
- Clear escalation procedures

### 4. Continuous Improvement

- Weekly metrics review
- Monthly trend analysis
- Quarterly capacity planning
- Annual architecture review

## Conclusion

This comprehensive operations guide ensures reliable system operation through:
- **Proactive monitoring** with automatic optimization
- **Clear procedures** for all operational tasks
- **Effective troubleshooting** with detailed diagnostics
- **Regular maintenance** to prevent issues
- **Emergency response** plans for critical situations

The system is designed for autonomous operation with human oversight, providing multiple layers of protection against resource exhaustion and service degradation.