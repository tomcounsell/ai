# Daily Health Check SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-20
**Owner**: Valor AI System
**Status**: Active

## Overview

This SOP defines the daily health check procedure for the Daydream autonomous maintenance system. It runs automatically at 6 AM Pacific to assess system health, identify issues, and generate reports.

## Prerequisites

- System running and accessible
- Sentry integration configured (SENTRY_API_KEY)
- Linear integration configured (LINEAR_API_KEY)
- Monitoring systems operational

## Parameters

### Required
- None (runs automatically with defaults)

### Optional
- **check_time** (timestamp): Time of health check
  - Default: Current time

- **severity_threshold** (string): Minimum severity to report
  - Values: `fatal` | `error` | `warning` | `info`
  - Default: `error`

- **time_range** (string): Period to analyze
  - Default: `24h`
  - Format: Duration string

- **notify_on_completion** (boolean): Send report via Telegram
  - Default: `true`

## Steps

### 1. Gather System Metrics

**Purpose**: Collect current system health metrics.

**Actions**:
- MUST collect memory usage statistics
- MUST collect CPU utilization
- MUST check disk space availability
- MUST verify service status (bridge, daemon)
- SHOULD collect response time metrics
- MAY collect network statistics

**Metrics to Collect**:
```python
metrics = {
    "memory_usage_mb": get_memory_usage(),
    "cpu_percent": get_cpu_usage(),
    "disk_free_gb": get_disk_space(),
    "uptime_hours": get_uptime(),
    "service_status": check_services(),
}
```

**Thresholds**:
| Metric | Warning | Critical |
|--------|---------|----------|
| Memory | 600MB | 800MB |
| CPU | 80% | 95% |
| Disk | 10GB | 5GB |

**Validation**:
- All metrics collected successfully
- No collection errors

### 2. Check Error Logs (Sentry)

**Purpose**: Review errors from the past 24 hours.

**Actions**:
- MUST query Sentry for new/unresolved issues
- MUST categorize issues by severity
- MUST identify recurring issues
- SHOULD calculate error rate trends
- MAY correlate with recent deployments

**Sentry Query**:
```python
issues = sentry.list_issues(
    status="unresolved",
    since="24h",
    sort="frequency"
)
```

**Categorization**:
- **Critical**: Fatal errors, data loss, security issues
- **High**: Errors affecting core functionality
- **Medium**: Errors with workarounds
- **Low**: Minor issues, edge cases

**Validation**:
- Sentry API accessible
- Issues retrieved successfully

**Error Handling**:
- If Sentry unavailable: Note in report, continue with other checks

### 3. Review Performance Trends

**Purpose**: Identify performance degradation or anomalies.

**Actions**:
- MUST compare current metrics to baseline
- MUST identify significant deviations (>20%)
- SHOULD track response time trends
- SHOULD monitor resource consumption patterns
- MAY predict future capacity needs

**Baseline Comparison**:
- Memory: 23-26MB baseline
- Response latency P95: <2s target
- Tool success rate: >95% target

**Validation**:
- Metrics within acceptable ranges
- No significant anomalies

### 4. Identify Action Items

**Purpose**: Determine what actions need to be taken.

**Actions**:
- MUST flag critical issues for immediate attention
- MUST categorize issues by urgency
- SHOULD suggest remediation steps
- MAY create Linear issues for high-priority items
- MUST NOT create duplicate issues

**Issue Creation Rules**:
- Create Linear issue if:
  - Error count > 10 in 24 hours
  - New error type not seen before
  - Critical/Fatal severity
- Do NOT create if:
  - Similar issue exists and is open
  - Issue is transient (single occurrence)

**Validation**:
- Action items are clear and actionable
- No duplicate issues created

### 5. Generate Health Report

**Purpose**: Compile findings into a comprehensive report.

**Actions**:
- MUST include overall health score (0-100)
- MUST summarize key metrics
- MUST list identified issues
- MUST include action items
- SHOULD compare to previous day
- MAY include trend visualizations

**Health Score Calculation**:
```python
score = 100
score -= error_count * 2
score -= warning_count * 0.5
score -= (memory_over_threshold * 5)
score -= (cpu_over_threshold * 5)
score = max(0, min(100, score))
```

**Report Format**:
```
=== DAILY HEALTH REPORT ===
Date: {date}
Health Score: {score}/100

SYSTEM METRICS:
- Memory: {memory}MB (threshold: 600MB)
- CPU: {cpu}% (threshold: 80%)
- Uptime: {uptime} hours
- Service Status: {status}

ERROR SUMMARY:
- Critical: {critical_count}
- High: {high_count}
- Medium: {medium_count}
- Total Events: {total_events}

TOP ISSUES:
1. {issue_1_title} - {count} occurrences
2. {issue_2_title} - {count} occurrences

ACTION ITEMS:
- [ ] {action_1}
- [ ] {action_2}

COMPARISON TO YESTERDAY:
- Errors: {trend} ({diff})
- Memory: {trend} ({diff})
```

### 6. Send Notifications

**Purpose**: Deliver report to appropriate channels.

**Actions**:
- MUST send report via Telegram if critical issues found
- SHOULD send daily summary regardless of status
- MAY send to additional channels if configured

**Notification Rules**:
- **Immediate**: Critical issues, health score < 60
- **Summary**: Daily report at completion
- **Silent**: No issues, health score > 90

**Validation**:
- Report delivered successfully
- Recipient confirmed (Telegram read receipt)

## Success Criteria

- All metrics collected
- Sentry issues reviewed
- Health score calculated
- Report generated and delivered
- Action items identified (if any)

## Error Recovery

| Error Type | Recovery Procedure |
|------------|-------------------|
| Sentry unavailable | Skip Sentry check, note in report |
| Metrics collection fails | Use last known values, note in report |
| Notification fails | Retry 3 times, log failure |
| Linear unavailable | Queue issue creation for later |

## Safety Constraints

- MUST NOT modify production code
- MUST NOT change database schema
- MUST NOT alter security configurations
- MUST NOT restart services without approval
- MAY auto-fix trivial issues (linting only)

## Examples

### Example 1: Healthy System

```
Input:
  severity_threshold: error
  time_range: 24h

Output:
  health_score: 95
  metrics:
    memory_mb: 24.5
    cpu_percent: 12
    uptime_hours: 168
  errors:
    critical: 0
    high: 1
    medium: 3
  action_items: []
  status: healthy
```

### Example 2: System with Issues

```
Input:
  severity_threshold: error
  time_range: 24h

Output:
  health_score: 68
  metrics:
    memory_mb: 450
    cpu_percent: 45
    uptime_hours: 24
  errors:
    critical: 2
    high: 5
    medium: 12
  action_items:
    - "Investigate NullPointerException in UserService"
    - "Review memory growth pattern"
  status: needs_attention
  notification_sent: true
```

## Related SOPs

- [Code Maintenance](code-maintenance.sop.md)
- [Performance Optimization](performance-optimization.sop.md)
- [Error Investigation](../subagents/sentry/error-investigation.sop.md)

## Version History

- v1.0.0 (2026-01-20): Initial version
