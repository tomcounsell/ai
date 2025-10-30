# Implementation Plan: Security Review Tool for CTO-Tools MCP Server

## Overview

Add a `security_review` tool to the existing CTO-Tools MCP server that helps CTOs and security teams systematically review codebases for security issues, correlate findings with policies, score risks, and create actionable remediation plans.

**Key Principle**: This is an **instructional tool** that guides an AI agent through a structured security review process, NOT an automated scanner. The agent uses its codebase understanding to identify issues following the provided framework.

## Architecture Summary

```
apps/ai/mcp/cto_tools_server.py  (Add security_review tool)
    ↓
apps/integration/linear/         (NEW - Linear GraphQL client)
    ├── client.py
    ├── models.py
    └── tests/
    ↓
apps/ai/models/security.py       (NEW - Risk & Finding models)
    ├── SecurityFinding
    ├── SecurityRisk
    └── SecurityReviewSession
    ↓
apps/ai/utils/                   (NEW - Helper utilities)
    ├── risk_scoring.py
    ├── markdown_generator.py
    └── policy_matcher.py
```

## Phase 1: Foundation & Data Models

### 1.1 Create Linear Integration (apps/integration/linear/)

**File**: `apps/integration/linear/client.py`

```python
"""
Linear GraphQL API Client

Provides async client for Linear issue creation and management.
Follows the same pattern as QuickBooks, Loops, Stripe integrations.
"""

import logging
from typing import Optional, Literal

import httpx
from settings import LINEAR_API_KEY

logger = logging.getLogger(__name__)


class LinearClient:
    """
    Async client for Linear GraphQL API.

    Docs: https://developers.linear.app/docs/graphql/working-with-the-graphql-api
    """

    BASE_URL = "https://api.linear.app/graphql"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or LINEAR_API_KEY
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json"
        }

    async def _execute_query(self, query: str, variables: dict | None = None) -> dict:
        """Execute GraphQL query."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.BASE_URL,
                headers=self.headers,
                json={"query": query, "variables": variables or {}}
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise LinearAPIError(f"GraphQL errors: {data['errors']}")

            return data.get("data", {})

    async def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        priority: Literal[0, 1, 2, 3, 4] = 2,  # 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low
        labels: list[str] | None = None,
    ) -> dict:
        """
        Create a Linear issue.

        Args:
            team_id: Linear team identifier
            title: Issue title
            description: Issue description (supports Markdown)
            priority: Priority level (1=Urgent, 2=High, 3=Medium, 4=Low)
            labels: List of label names to apply

        Returns:
            Created issue data with id and url
        """
        query = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id
              identifier
              title
              url
            }
          }
        }
        """

        variables = {
            "input": {
                "teamId": team_id,
                "title": title,
                "description": description,
                "priority": priority,
            }
        }

        if labels:
            # TODO: Add label handling (requires label ID lookup)
            pass

        result = await self._execute_query(query, variables)
        return result.get("issueCreate", {}).get("issue", {})

    async def search_issues(
        self,
        query: str,
        limit: int = 10
    ) -> list[dict]:
        """Search for existing issues to avoid duplicates."""
        graphql_query = """
        query SearchIssues($query: String!, $first: Int!) {
          issueSearch(query: $query, first: $first) {
            nodes {
              id
              identifier
              title
              description
              state {
                name
              }
            }
          }
        }
        """

        result = await self._execute_query(
            graphql_query,
            {"query": query, "first": limit}
        )
        return result.get("issueSearch", {}).get("nodes", [])

    async def get_teams(self) -> list[dict]:
        """Get available teams for the workspace."""
        query = """
        query Teams {
          teams {
            nodes {
              id
              key
              name
            }
          }
        }
        """
        result = await self._execute_query(query)
        return result.get("teams", {}).get("nodes", [])


class LinearAPIError(Exception):
    """Linear API error."""
    pass
```

**File**: `apps/integration/linear/models.py` (if needed for OAuth storage in future)

```python
"""
Linear integration models.

Currently no persistent models needed since we only use API keys.
Future: OAuth tokens if Linear implements OAuth flow.
"""
```

**File**: `apps/integration/linear/tests/test_client.py`

```python
"""Tests for Linear GraphQL client."""

import pytest
from apps.integration.linear.client import LinearClient

# TODO: Add comprehensive tests
# - Mock GraphQL responses
# - Test issue creation
# - Test search functionality
# - Test error handling
```

### 1.2 Create Security Review Models (apps/ai/models/security.py)

**File**: `apps/ai/models/security.py`

```python
"""
Security review data models.

Stores security findings, risks, and review sessions for audit trail.
"""

from django.db import models
from apps.common.behaviors import Timestampable, Authorable


class SecurityReviewSession(Timestampable, models.Model):
    """
    A security review session tracking a single review execution.

    Maintains audit trail of reviews performed.
    """

    query = models.TextField(
        help_text="Natural language query that initiated this review"
    )
    time_window_hours = models.IntegerField(default=72)
    min_severity = models.CharField(max_length=20, default="Medium")
    data_types = models.JSONField(default=list, blank=True)

    # Results
    total_findings = models.IntegerField(default=0)
    total_risks = models.IntegerField(default=0)
    critical_count = models.IntegerField(default=0)
    high_count = models.IntegerField(default=0)
    medium_count = models.IntegerField(default=0)
    low_count = models.IntegerField(default=0)

    # Output
    summary = models.TextField(blank=True)
    markdown_report_path = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
        ]


class SecurityFinding(Timestampable, models.Model):
    """
    Individual security finding from codebase analysis.

    Raw findings before correlation into risks.
    """

    CATEGORY_CHOICES = [
        ("auth", "Authentication"),
        ("crypto", "Cryptography"),
        ("injection", "Injection"),
        ("exposure", "Data Exposure"),
        ("access_control", "Access Control"),
        ("config", "Configuration"),
        ("dependency", "Dependency"),
        ("other", "Other"),
    ]

    session = models.ForeignKey(
        SecurityReviewSession,
        on_delete=models.CASCADE,
        related_name="findings"
    )

    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField()

    # Location
    file_path = models.CharField(max_length=500)
    line_number = models.IntegerField(null=True, blank=True)
    code_snippet = models.TextField(blank=True)

    # Context
    affected_assets = models.JSONField(default=list, blank=True)
    data_types_involved = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["file_path", "line_number"]


class SecurityRisk(Timestampable, models.Model):
    """
    Correlated security risk combining multiple findings.

    Represents actionable risk with scoring and remediation.
    """

    SEVERITY_CHOICES = [
        ("Critical", "Critical"),
        ("High", "High"),
        ("Medium", "Medium"),
        ("Low", "Low"),
    ]

    session = models.ForeignKey(
        SecurityReviewSession,
        on_delete=models.CASCADE,
        related_name="risks"
    )

    risk_id = models.CharField(max_length=50, unique=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    score = models.FloatField(help_text="Risk score 0-100")

    title = models.CharField(max_length=255)
    description = models.TextField()

    # Correlations
    linked_findings = models.ManyToManyField(SecurityFinding, related_name="risks")
    policy_violations = models.JSONField(default=list, blank=True)
    affected_assets = models.JSONField(default=list, blank=True)

    # Remediation
    recommended_actions = models.JSONField(default=list, blank=True)
    assigned_team = models.CharField(max_length=100, blank=True)

    # Linear integration
    linear_ticket_id = models.CharField(max_length=100, blank=True)
    linear_ticket_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-score", "-created_at"]
        indexes = [
            models.Index(fields=["severity", "-score"]),
            models.Index(fields=["-created_at"]),
        ]
```

### 1.3 Create Helper Utilities

**File**: `apps/ai/utils/risk_scoring.py`

```python
"""
Risk scoring algorithm for security findings.

Combines multiple factors to produce 0-100 risk score.
"""

from typing import Literal


def calculate_risk_score(
    severity: Literal["Critical", "High", "Medium", "Low"],
    exposure_level: Literal["public", "internal", "private"],
    has_pii: bool,
    in_production: bool,
    policy_weight: int = 1,  # 1-5 scale
) -> float:
    """
    Calculate risk score from 0-100.

    Args:
        severity: Finding severity level
        exposure_level: How exposed is the vulnerable asset
        has_pii: Whether PII data is involved
        in_production: Whether asset is in production
        policy_weight: Severity of violated policy (1-5)

    Returns:
        Risk score from 0-100
    """

    # Base severity scores
    severity_scores = {
        "Critical": 40,
        "High": 30,
        "Medium": 20,
        "Low": 10,
    }
    base_score = severity_scores.get(severity, 10)

    # Exposure multiplier
    exposure_multipliers = {
        "public": 1.5,
        "internal": 1.2,
        "private": 1.0,
    }
    exposure_mult = exposure_multipliers.get(exposure_level, 1.0)

    # PII bonus
    pii_bonus = 15 if has_pii else 0

    # Production bonus
    prod_bonus = 10 if in_production else 0

    # Policy weight factor (0-15 points)
    policy_points = (policy_weight / 5) * 15

    # Calculate final score
    score = (base_score * exposure_mult) + pii_bonus + prod_bonus + policy_points

    # Clamp to 0-100
    return min(100.0, max(0.0, score))


def get_severity_from_score(score: float) -> Literal["Critical", "High", "Medium", "Low"]:
    """Map risk score to severity label."""
    if score >= 80:
        return "Critical"
    elif score >= 50:
        return "High"
    elif score >= 20:
        return "Medium"
    else:
        return "Low"
```

**File**: `apps/ai/utils/markdown_generator.py`

```python
"""
Markdown report generator for security reviews.

Creates structured markdown reports for local backup.
"""

from datetime import datetime
from typing import Any


def generate_security_report(
    session_data: dict[str, Any],
    risks: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> str:
    """
    Generate markdown report for security review.

    Args:
        session_data: Review session metadata
        risks: List of correlated risks
        findings: List of individual findings

    Returns:
        Markdown-formatted report
    """

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""# Security Review Report

**Generated**: {timestamp}
**Query**: {session_data.get('query', 'N/A')}
**Time Window**: {session_data.get('time_window_hours', 72)} hours
**Min Severity**: {session_data.get('min_severity', 'Medium')}

---

## Executive Summary

{session_data.get('summary', 'No summary provided')}

**Risk Breakdown**:
- 🔴 Critical: {session_data.get('critical_count', 0)}
- 🟠 High: {session_data.get('high_count', 0)}
- 🟡 Medium: {session_data.get('medium_count', 0)}
- ⚪ Low: {session_data.get('low_count', 0)}

**Total Findings**: {session_data.get('total_findings', 0)}

---

## Top Risks

"""

    for i, risk in enumerate(risks, 1):
        severity_emoji = {
            "Critical": "🔴",
            "High": "🟠",
            "Medium": "🟡",
            "Low": "⚪",
        }.get(risk.get("severity", "Medium"), "⚪")

        report += f"""
### {severity_emoji} Risk {i}: {risk.get('title', 'Untitled')}

**ID**: {risk.get('risk_id', 'N/A')}
**Severity**: {risk.get('severity', 'N/A')} (Score: {risk.get('score', 0):.1f}/100)
**Linear Ticket**: {risk.get('linear_ticket_url', 'Not created')}

**Description**:
{risk.get('description', 'No description')}

**Affected Assets**:
"""
        for asset in risk.get('affected_assets', []):
            report += f"- {asset}\n"

        report += "\n**Policy Violations**:\n"
        for policy in risk.get('policy_violations', []):
            report += f"- {policy}\n"

        report += "\n**Recommended Actions**:\n"
        for action in risk.get('recommended_actions', []):
            report += f"- {action}\n"

        report += "\n---\n"

    # Detailed findings section
    report += """
## Detailed Findings

"""

    for i, finding in enumerate(findings, 1):
        report += f"""
### Finding {i}: {finding.get('title', 'Untitled')}

**Category**: {finding.get('category', 'other')}
**File**: {finding.get('file_path', 'N/A')}:{finding.get('line_number', '?')}

**Description**:
{finding.get('description', 'No description')}

"""
        if finding.get('code_snippet'):
            report += f"""
**Code Snippet**:
```
{finding.get('code_snippet')}
```

"""

    report += """
---

## Appendix: Review Metadata

**Data Types Filtered**: {data_types}
**Total Processing Time**: N/A (manual review)
**Reviewer**: AI Agent (Claude)

""".format(
        data_types=", ".join(session_data.get('data_types', [])) or "All"
    )

    return report
```

## Phase 2: Security Review Tool Implementation

### 2.1 Add security_review Tool to CTO Tools Server

**File**: `apps/ai/mcp/cto_tools_server.py` (append to existing file)

```python
from typing import Literal


@mcp.tool()
def security_review(
    query: str,
    time_window_hours: int = 72,
    min_severity: Literal["Low", "Medium", "High", "Critical"] = "Medium",
    data_types: list[str] | None = None,
    create_tickets: bool = False,
    max_results: int = 10,
) -> str:
    """
    Structured framework for conducting comprehensive security reviews of codebases.

    This tool guides you through a systematic security review process:
    1. Scanning codebase for common vulnerabilities
    2. Correlating findings with security policies
    3. Scoring and prioritizing risks
    4. Generating actionable remediation plans
    5. Optionally creating Linear tickets for critical issues
    6. Saving markdown report for audit trail

    **IMPORTANT**: This is an instructional framework, NOT an automated scanner.
    You (the AI agent) must use your codebase understanding to identify issues.

    Args:
        query: Natural language query describing security review scope
               Examples: "Review authentication for PII leaks",
                        "Check for SQL injection vulnerabilities",
                        "Find hardcoded credentials"
        time_window_hours: Focus on code changed in last N hours (default: 72)
        min_severity: Minimum severity to report (default: Medium)
        data_types: Filter by data types (e.g., ["PII", "credentials", "tokens"])
        create_tickets: Auto-create Linear tickets for Critical/High risks
        max_results: Maximum number of risks to return (default: 10)

    Returns:
        Step-by-step instructions for conducting security review with output format
    """

    data_types_str = ", ".join(data_types) if data_types else "all data types"

    instructions = f"""
# Security Review Framework

## GOAL
Systematically review the codebase for security vulnerabilities, correlate findings with
security policies, score risks, and generate actionable remediation plans.

**Review Scope**: {query}
**Time Window**: Changes in last {time_window_hours} hours
**Minimum Severity**: {min_severity}
**Data Types**: {data_types_str}
**Auto-create Linear Tickets**: {"Yes" if create_tickets else "No"}

---

## PHASE 1: RECONNAISSANCE

### 1.1 Understand the Query
Parse the natural language query to extract:
- **Attack surface**: What parts of the system to review (auth, APIs, data storage, etc.)
- **Threat types**: SQL injection, XSS, auth bypass, data leaks, etc.
- **Sensitive data**: PII, credentials, tokens, health data, financial data
- **Time constraints**: Recent changes or all code

### 1.2 Identify Review Scope
Use the following commands to understand the codebase structure:

```bash
# Get recent commits in scope
git log --since="{time_window_hours} hours ago" --oneline --stat

# Find relevant files based on query context
# Examples:
# - Authentication: find . -type f -name "*auth*" -o -name "*login*"
# - API endpoints: find . -type f -name "*views.py" -o -name "*api.py"
# - Database: find . -type f -name "*models.py" -o -name "*.sql"
# - Environment: find . -type f -name ".env*" -o -name "*.env"
```

### 1.3 Gather Security Policies (if available)
- Check for `SECURITY.md`, `docs/security/`, `.github/SECURITY.md`
- Review compliance requirements (GDPR, HIPAA, SOC2, etc.)
- Note organization-specific policies from Memory MCP or documentation

---

## PHASE 2: VULNERABILITY SCANNING

### 2.1 Common Vulnerability Categories
Systematically scan for the following vulnerability types:

#### 🔐 Authentication & Authorization
- [ ] Hardcoded credentials (passwords, API keys, tokens)
- [ ] Weak password policies or validation
- [ ] Missing authentication on sensitive endpoints
- [ ] Insecure session management
- [ ] JWT token issues (weak secrets, no expiration)
- [ ] Missing rate limiting on auth endpoints

**Search patterns**:
```bash
# Hardcoded secrets
grep -r "password.*=" . --include="*.py" --include="*.js"
grep -r "api_key.*=" . --include="*.py"
grep -r "SECRET_KEY.*=" . --include="*.py"

# Weak auth
grep -r "auth_required.*False" . --include="*.py"
grep -r "allow_any" . --include="*.py"
```

#### 💉 Injection Vulnerabilities
- [ ] SQL injection (raw queries, unsafe string formatting)
- [ ] Command injection (subprocess calls with user input)
- [ ] Template injection
- [ ] NoSQL injection
- [ ] LDAP injection

**Search patterns**:
```bash
# SQL injection
grep -r "raw(" . --include="*.py"  # Django raw queries
grep -r "execute(" . --include="*.py"
grep -r "cursor.execute.*%" . --include="*.py"

# Command injection
grep -r "subprocess" . --include="*.py"
grep -r "os.system" . --include="*.py"
grep -r "eval(" . --include="*.py"
```

#### 🌐 Cross-Site Scripting (XSS)
- [ ] Unescaped user input in templates
- [ ] Unsafe innerHTML/dangerouslySetInnerHTML
- [ ] Missing Content-Security-Policy headers
- [ ] User-controlled URLs without validation

**Search patterns**:
```bash
# Template safety
grep -r "safe\|autoescape.*off" . --include="*.html"
grep -r "dangerouslySetInnerHTML" . --include="*.jsx" --include="*.tsx"
```

#### 📦 Data Exposure
- [ ] PII logged to console/files
- [ ] Sensitive data in URLs or query params
- [ ] Unencrypted data storage
- [ ] Missing data at rest encryption
- [ ] Verbose error messages exposing internals
- [ ] Debug mode enabled in production

**Search patterns**:
```bash
# PII in logs
grep -r "print.*email\|logger.*password" . --include="*.py"

# Debug mode
grep -r "DEBUG.*=.*True" . --include="*.py"
grep -r "ALLOWED_HOSTS.*=.*\*" . --include="*.py"
```

#### 🔓 Access Control
- [ ] Missing permission checks
- [ ] Insecure direct object references (IDOR)
- [ ] Privilege escalation opportunities
- [ ] Missing CSRF protection
- [ ] CORS misconfiguration (allow all origins)

**Search patterns**:
```bash
# Permissive CORS
grep -r "CORS_ALLOW_ALL" . --include="*.py"
grep -r "Access-Control-Allow-Origin.*\*" .

# Missing permissions
grep -r "permission_classes.*=.*\[\]" . --include="*.py"
```

#### 🔧 Configuration Issues
- [ ] Exposed `.env` files
- [ ] Weak cryptographic algorithms
- [ ] Insecure defaults
- [ ] Missing security headers
- [ ] Outdated dependencies with known CVEs

**Search patterns**:
```bash
# Weak crypto
grep -r "MD5\|SHA1" . --include="*.py"
grep -r "DES\|RC4" . --include="*.py"

# Exposed secrets
find . -name ".env" -not -path "*/node_modules/*"
find . -name "*.pem" -o -name "*.key"
```

### 2.2 Document Each Finding
For each vulnerability found, record:

```python
Finding(
    category="<category>",  # auth, injection, exposure, etc.
    title="<Short descriptive title>",
    description="<Detailed explanation of the issue>",
    file_path="<path/to/file>",
    line_number=<line>,
    code_snippet="<relevant code>",
    affected_assets=["<asset1>", "<asset2>"],
    data_types_involved=["<PII>", "<credentials>", etc.]
)
```

---

## PHASE 3: CORRELATION & RISK SCORING

### 3.1 Group Related Findings
Combine related findings into cohesive risks:
- Same vulnerability type affecting multiple files
- Multiple issues in the same component
- Findings that together create a critical path

### 3.2 Calculate Risk Scores
For each correlated risk, calculate score (0-100) using:

**Risk Score Formula**:
```
score = (base_severity × exposure_multiplier) + pii_bonus + prod_bonus + policy_weight

Where:
- base_severity: Critical=40, High=30, Medium=20, Low=10
- exposure_multiplier: public=1.5, internal=1.2, private=1.0
- pii_bonus: 15 if PII involved, 0 otherwise
- prod_bonus: 10 if production code, 0 if dev/test
- policy_weight: 0-15 based on policy violation severity (1-5 scale × 3)
```

**Score to Severity Mapping**:
- 80-100 → Critical
- 50-79 → High
- 20-49 → Medium
- 0-19 → Low

### 3.3 Match to Security Policies
For each risk, identify:
- Which security policy/standard is violated (OWASP, CWE, internal policy)
- Compliance implications (GDPR Art. 32, HIPAA §164.312, etc.)
- Business impact (data breach, downtime, reputation)

---

## PHASE 4: REMEDIATION PLANNING

### 4.1 Generate Action Items
For each risk, provide specific remediation steps:

**Format**:
```
Risk SEC-<number>: <Title>

Severity: <Critical|High|Medium|Low> (Score: <0-100>)

Description:
<Clear explanation of the issue and why it's a risk>

Affected Assets:
- <asset 1>
- <asset 2>

Policy Violations:
- <policy/standard violated>

Recommended Actions:
1. <Immediate action> - <who should do it>
2. <Follow-up action>
3. <Long-term improvement>

Estimated Effort: <hours/days>
Suggested Assignee: <team/person>
Priority: <1-4>
```

### 4.2 Prioritize Risks
Order risks by:
1. Severity (Critical first)
2. Risk score (highest first)
3. Ease of exploitation
4. Business impact

---

## PHASE 5: OUTPUT GENERATION

### 5.1 Create Structured Summary
Generate a natural language executive summary:

```
Security Review Summary - {current_date}

Query: {query}
Scope: {scope_description}

Top Findings:
{summary_of_top_3_risks}

Overall Risk Posture: {assessment}
Immediate Actions Required: {count_critical_and_high}
Compliance Concerns: {if_any}
```

### 5.2 Generate JSON Output
Create machine-readable structured data:

```json
{{
  "session_id": "SEC-{timestamp}",
  "query": "{query}",
  "timestamp": "{iso_timestamp}",
  "summary": "...",
  "total_findings": {total_findings},
  "total_risks": {total_risks},
  "risk_breakdown": {{
    "critical": {critical_count},
    "high": {high_count},
    "medium": {medium_count},
    "low": {low_count}
  }},
  "risks": [
    {{
      "risk_id": "SEC-001",
      "severity": "Critical",
      "score": 92.5,
      "title": "...",
      "description": "...",
      "linked_findings": ["F-001", "F-002"],
      "policy_violations": ["..."],
      "affected_assets": ["..."],
      "actions": [
        {{
          "action": "...",
          "assignee": "...",
          "priority": "Urgent"
        }}
      ],
      "ticket_id": "{linear_ticket_id or null}"
    }}
  ]
}}
```

### 5.3 Save Markdown Report
Create detailed markdown report at `/tmp/security_review_{date}.md`:

```markdown
# Security Review Report - {date}

## Executive Summary
{summary}

## Risk Breakdown
- 🔴 Critical: {count}
- 🟠 High: {count}
- 🟡 Medium: {count}
- ⚪ Low: {count}

## Top Risks
{detailed_risk_descriptions}

## All Findings
{detailed_findings}

## Remediation Timeline
{prioritized_action_plan}
```

---

## PHASE 6: LINEAR TICKET CREATION (Optional)

**Only if `create_tickets = True`**:

### 6.1 Check for Duplicates
Search Linear for existing tickets:
```python
existing = linear_client.search_issues(
    query="{risk_title} {affected_asset}"
)
```

### 6.2 Create Tickets for Critical/High Risks
For each Critical or High severity risk without existing ticket:

```python
ticket = linear_client.create_issue(
    team_id="{security_team_id}",
    title="[Security] {risk_title}",
    description="{markdown_formatted_risk_details}",
    priority=1 if severity == "Critical" else 2,
    labels=["security", "vulnerability", "{data_type}"]
)
```

### 6.3 Link Tickets to Risks
Update risk records with Linear ticket IDs and URLs.

---

## OUTPUT EXPECTATIONS

Your final deliverable should include:

✅ **Executive Summary** (2-3 paragraphs)
✅ **Risk Breakdown** (count by severity)
✅ **Top {max_results} Risks** (detailed with remediation plans)
✅ **Structured JSON** (machine-readable)
✅ **Markdown Report** saved to `/tmp/security_review_{{timestamp}}.md`
✅ **Linear Tickets** (if create_tickets=True, only for Critical/High)

❌ **NOT** a list of every single code smell
❌ **NOT** generic security advice
❌ **NOT** automated scanner output dump

**Focus on**:
- Actionable, specific vulnerabilities
- Business impact and risk context
- Clear remediation steps with ownership
- Traceability from finding → risk → ticket

---

## FINAL STEP: SAVE & PRESENT

1. **Save markdown report**:
   ```bash
   # Save to /tmp/security_review_{{timestamp}}.md
   ```

2. **Present summary**:
   - Show executive summary
   - List top 3-5 risks
   - Provide path to full markdown report
   - List Linear tickets created (if any)

3. **Offer next steps**:
   - "Would you like me to create Linear tickets for the Critical risks?"
   - "Shall I generate a presentation-ready summary for leadership?"
   - "Do you want me to drill into a specific risk area?"

---

**Begin by asking clarifying questions if the query is ambiguous, then proceed with PHASE 1.**
"""

    return instructions
```

### 2.2 Update Server Imports

At the top of `apps/ai/mcp/cto_tools_server.py`, add:

```python
from typing import Literal
```

## Phase 3: Testing & Documentation

### 3.1 Test Files to Create

**File**: `apps/integration/linear/tests/test_client.py`
- Test GraphQL query execution
- Test issue creation
- Test search functionality
- Test error handling
- Mock Linear API responses

**File**: `apps/ai/tests/test_security_review.py`
- Test security_review tool returns instructions
- Test parameter validation
- Test markdown generation
- Test risk scoring algorithm
- Mock scenarios with findings

**File**: `apps/ai/utils/tests/test_risk_scoring.py`
- Test score calculation edge cases
- Test severity mapping
- Test score clamping (0-100)

### 3.2 Documentation Updates

**File**: `docs/specs/CTO_TOOLS_SECURITY_REVIEW.md`
- Complete feature specification
- Usage examples
- Integration guide
- Security considerations

**File**: `apps/ai/mcp/CTO_TOOLS_README.md` (update existing)
- Add security_review tool documentation
- Add usage examples
- Add Linear setup instructions

**File**: `CLAUDE.md` (update)
- Add security review testing commands
- Add Linear integration notes

## Phase 4: Environment & Dependencies

### 4.1 Environment Variables

Add to `.env.example` and `.env.local`:

```bash
# Linear Integration
LINEAR_API_KEY=lin_api_xxxxxxxxxxxxxxxxxxxx
LINEAR_DEFAULT_TEAM_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### 4.2 Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    # ... existing deps ...
    "httpx>=0.24.0",  # Already included for QuickBooks
]
```

No new dependencies needed! We already have:
- `httpx` for async HTTP
- `mcp` for MCP server
- Django models for data persistence

## Implementation Order & Timeline

### Week 1: Foundation
- [ ] Create Linear client (`apps/integration/linear/client.py`)
- [ ] Create security models (`apps/ai/models/security.py`)
- [ ] Create risk scoring utility (`apps/ai/utils/risk_scoring.py`)
- [ ] Create markdown generator (`apps/ai/utils/markdown_generator.py`)
- [ ] Add environment variables
- [ ] Write unit tests for utilities

### Week 2: Core Tool
- [ ] Implement `security_review` tool in `cto_tools_server.py`
- [ ] Test tool locally with `uv run python -m apps.ai.mcp.cto_tools_server`
- [ ] Validate instruction quality with sample queries
- [ ] Write integration tests

### Week 3: Integration & Polish
- [ ] Integrate Linear ticket creation flow
- [ ] Test end-to-end workflow
- [ ] Create example reviews for documentation
- [ ] Update documentation
- [ ] Create migration (if needed for models)

### Week 4: Deployment & Validation
- [ ] Deploy to staging
- [ ] Conduct real security review using tool
- [ ] Gather feedback
- [ ] Refine instructions based on usage
- [ ] Deploy to production

## Usage Example

Once implemented, usage would look like:

```python
# In Claude Desktop with CTO-Tools MCP server installed

User: "Review our authentication system for credential leaks and weak auth"

Claude: [Uses security_review tool]

# Tool returns instructions, Claude follows them:

1. RECONNAISSANCE
   - Scans auth-related files
   - Reviews recent commits
   - Identifies auth surface area

2. VULNERABILITY SCANNING
   - Searches for hardcoded credentials
   - Checks for weak password validation
   - Reviews session management
   - Tests auth bypass scenarios

3. CORRELATION
   - Groups 3 findings into 2 risks
   - Calculates risk scores
   - Maps to OWASP Top 10

4. REMEDIATION PLANNING
   - Creates action items for each risk
   - Assigns priorities and owners

5. OUTPUT
   - Executive summary
   - Top 2 risks detailed
   - Saves to /tmp/security_review_2025-01-15.md
   - Creates Linear ticket for Critical risk

User receives:
- Natural language summary
- Path to detailed markdown report
- Link to Linear ticket(s)
- Recommended next steps
```

## Success Criteria

✅ Tool provides clear, actionable instructions
✅ Guides systematic review of common vulnerability types
✅ Produces structured JSON + markdown outputs
✅ Creates Linear tickets for high-priority issues
✅ Maintains audit trail in database
✅ Reduces manual triage time by >40%
✅ Works across any codebase/tech stack

## Future Enhancements (Post-MVP)

- **Continuous Monitoring**: Run on cron/webhook for new commits
- **Policy Document Integration**: Use Memory MCP for RAG retrieval
- **Automated Fix Suggestions**: Generate pull requests with fixes
- **Trend Analysis**: Track risk score over time
- **Multi-tool Integration**: Pull from actual SAST/DAST tools
- **Custom Rules**: User-defined vulnerability patterns
- **Compliance Mapping**: Auto-map to SOC2/GDPR/HIPAA controls

## Risk & Mitigation

| Risk | Mitigation |
|------|------------|
| False positives overwhelming user | Implement severity filtering, max_results limit |
| Linear API rate limits | Implement retry logic, batch ticket creation |
| Inconsistent instruction quality | Extensive testing, gather user feedback, iterate |
| PII exposure in reports | Sanitize code snippets, warn before including in tickets |
| Tool misuse for malicious scanning | Document ethical use, consider audit logging |

## Appendix: File Structure Summary

```
apps/
├── integration/
│   └── linear/
│       ├── __init__.py
│       ├── client.py           # NEW - GraphQL client
│       ├── models.py           # NEW - (future OAuth models)
│       └── tests/
│           └── test_client.py  # NEW
│
├── ai/
│   ├── mcp/
│   │   └── cto_tools_server.py  # MODIFY - Add security_review tool
│   │
│   ├── models/
│   │   └── security.py          # NEW - SecurityReviewSession, SecurityFinding, SecurityRisk
│   │
│   ├── utils/                   # NEW directory
│   │   ├── __init__.py
│   │   ├── risk_scoring.py      # NEW - Risk calculation algorithm
│   │   ├── markdown_generator.py # NEW - Report generation
│   │   └── tests/
│   │       ├── test_risk_scoring.py
│   │       └── test_markdown_generator.py
│   │
│   └── tests/
│       └── test_security_review.py  # NEW
│
├── settings/
│   └── third_party.py          # MODIFY - Add LINEAR_API_KEY config
│
└── docs/
    ├── specs/
    │   └── CTO_TOOLS_SECURITY_REVIEW.md  # NEW
    └── plans/
        └── security-review-implementation-plan.md  # THIS FILE
```

## Feasibility Analysis

### Technical Feasibility: ✅ HIGH

**Strengths**:
- **No new dependencies required**: All necessary packages (`httpx`, `mcp`, Django) already in `pyproject.toml`
- **Proven patterns**: Linear client mirrors existing QuickBooks/Loops/Stripe integrations
- **FastMCP compatibility**: Tool follows same instructional pattern as `weekly_review`
- **PostgreSQL ready**: JSON fields for flexible data storage already supported
- **Existing infrastructure**: Django admin, REST framework, migrations all in place

**Challenges**:
1. **Linear API complexity**: GraphQL requires careful query construction
   - *Mitigation*: Start with simple queries, extensive testing, reference Linear SDK docs
2. **Django models in MCP context**: Security models need to work without Django request context
   - *Mitigation*: Models are standalone, no view/request dependencies
3. **Async/sync boundary**: Linear client is async, Django ORM is sync
   - *Mitigation*: Use `sync_to_async` / `async_to_sync` utilities (already in codebase)

**Risk Level**: LOW - All technical components proven in existing code

### Scope Feasibility: ✅ HIGH

**MVP is well-scoped**:
- ✅ Instructional tool (not automated scanner) = manageable complexity
- ✅ Focuses on guidance, not deep static analysis
- ✅ Linear integration is optional (works without it)
- ✅ Markdown output provides value even without database persistence

**Scope creep risks**:
- ⚠️ Temptation to add actual SAST/DAST tool integrations (out of MVP scope)
- ⚠️ Custom policy document parsing (defer to Memory MCP)
- ⚠️ Automated remediation/PR generation (future enhancement)

**Recommendation**: Stick to MVP scope, defer enhancements to post-launch

### Resource Feasibility: ⚠️ MEDIUM

**Development Time Estimate**:
- Linear client: 8-12 hours (including tests)
- Security models: 4-6 hours (including migration)
- Utility functions: 4-6 hours (scoring + markdown)
- Security review tool: 8-12 hours (instruction framework + refinement)
- Testing: 8-12 hours (unit + integration tests)
- Documentation: 4-6 hours
- **Total**: 36-54 hours (1-1.5 weeks full-time, 2-4 weeks part-time)

**Required Skills**:
- Django models and migrations ✅
- GraphQL API integration ⚠️ (learning curve for Linear API)
- FastMCP tool development ✅
- Security domain knowledge ⚠️ (for quality instructions)
- Testing (mocking GraphQL, async code) ⚠️

**Recommendation**: If GraphQL experience is limited, allocate extra time for Linear client development

### Operational Feasibility: ✅ HIGH

**Deployment**:
- ✅ No infrastructure changes needed
- ✅ CTO-Tools server already deployed
- ✅ Django migrations can be run safely
- ⚠️ Linear API key needs to be added to secrets management

**Maintenance**:
- ✅ Low maintenance burden (instructional tool, not complex logic)
- ⚠️ May need instruction refinement based on user feedback
- ⚠️ Linear API changes could break integration (monitor API changelog)

**User Adoption**:
- ✅ Target users (CTOs, security leads) already use CTO-Tools for `weekly_review`
- ✅ Instructional format proven successful
- ⚠️ Security reviews require security expertise to interpret results
- ⚠️ Linear adoption depends on organization using Linear (not universal)

**Recommendation**: Launch with Linear as optional, emphasize markdown output as primary value

### Business Feasibility: ✅ HIGH

**Value Proposition**:
- Reduces security triage time by 40%+ (per PRD success metrics)
- Provides audit trail for compliance (markdown + database)
- Scales to any codebase/tech stack (not tied to specific tools)
- Complements existing CTO-Tools offering

**Cost**:
- ⚠️ Linear API usage (check rate limits and pricing)
- ✅ No additional infrastructure costs
- ✅ Marginal increase in database storage

**ROI**:
- If saves 4+ hours per week for security team → breaks even quickly
- Compliance/audit value hard to quantify but significant
- Reputational benefit of preventing security incidents

**Recommendation**: PROCEED - High value, low cost, strong ROI

### Overall Feasibility Assessment: ✅ GO

**Verdict**: This feature is **highly feasible** and should proceed to implementation.

**Key Success Factors**:
1. Start with Linear client (highest risk component)
2. Test extensively with mock data before production
3. Gather early feedback on instruction quality
4. Keep MVP scope tight, defer enhancements
5. Document limitations clearly (not a replacement for dedicated security tools)

**Red Flags** (monitor during development):
- Linear API rate limiting becomes blocking issue
- Instruction quality feedback is negative
- Security models create performance issues
- User adoption lower than expected

---

## Build Plan: Step-by-Step Implementation Checklist

### Phase 0: Pre-Implementation (1-2 hours)

- [ ] **0.1** Review Linear API documentation thoroughly
  - Read: https://developers.linear.app/docs/graphql/working-with-the-graphql-api
  - Understand GraphQL schema for issues, teams, labels
  - Check rate limits and authentication requirements
  - Test API key in Linear API playground

- [ ] **0.2** Set up Linear API credentials
  - Generate Linear API key from workspace settings
  - Add `LINEAR_API_KEY` to `.env.local`
  - Add `LINEAR_API_KEY` to `settings/third_party.py`
  - Document key generation process for other developers

- [ ] **0.3** Create feature branch
  ```bash
  git checkout -b feature/security-review-tool
  ```

- [ ] **0.4** Review existing integration patterns
  - Read `apps/integration/quickbooks/client.py` for async client pattern
  - Read `apps/integration/loops/client.py` for simple API client pattern
  - Review `apps/ai/mcp/cto_tools_server.py` for instructional tool pattern

---

### Phase 1: Foundation - Linear Integration (8-12 hours)

#### 1.1 Create Linear Integration Structure (30 min)

- [ ] **1.1.1** Create directory structure
  ```bash
  mkdir -p apps/integration/linear/tests
  touch apps/integration/linear/__init__.py
  touch apps/integration/linear/client.py
  touch apps/integration/linear/models.py
  touch apps/integration/linear/tests/__init__.py
  touch apps/integration/linear/tests/test_client.py
  ```

- [ ] **1.1.2** Add Linear integration to `apps/integration/__init__.py` if needed

#### 1.2 Implement Linear GraphQL Client (6-8 hours)

- [ ] **1.2.1** Create `LinearClient` class with base structure
  - Define `__init__` with API key handling
  - Define `BASE_URL` constant
  - Define `_execute_query` helper method
  - Add `LinearAPIError` exception class

- [ ] **1.2.2** Implement `create_issue` method
  - Write GraphQL mutation for issue creation
  - Handle team_id, title, description, priority parameters
  - Return issue id, identifier, title, url
  - Add error handling for GraphQL errors

- [ ] **1.2.3** Implement `search_issues` method
  - Write GraphQL query for issue search
  - Handle pagination with `first` parameter
  - Return list of matching issues
  - Test deduplication logic

- [ ] **1.2.4** Implement `get_teams` method
  - Write GraphQL query for teams list
  - Return team id, key, name
  - Used for finding correct team_id for ticket creation

- [ ] **1.2.5** Add settings configuration
  - Add `LINEAR_API_KEY` to `settings/third_party.py`:
    ```python
    # Linear Integration
    LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")
    LINEAR_DEFAULT_TEAM_ID = os.environ.get("LINEAR_DEFAULT_TEAM_ID", "")
    ```

#### 1.3 Write Tests for Linear Client (2-3 hours)

- [ ] **1.3.1** Set up test fixtures and mocks
  - Create mock GraphQL responses for common queries
  - Set up `httpx` mock using `respx` or `pytest-httpx`

- [ ] **1.3.2** Test `create_issue` method
  - Test successful issue creation
  - Test with all priority levels (0-4)
  - Test error handling (invalid team_id, missing required fields)
  - Test GraphQL error responses

- [ ] **1.3.3** Test `search_issues` method
  - Test successful search with results
  - Test empty results
  - Test pagination

- [ ] **1.3.4** Test `get_teams` method
  - Test successful teams list retrieval
  - Test empty workspace

- [ ] **1.3.5** Run tests and verify coverage
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest apps/integration/linear/tests/ -v --cov=apps/integration/linear
  ```

#### 1.4 Manual Testing (1 hour)

- [ ] **1.4.1** Test Linear client in Django shell
  ```python
  from apps.integration.linear.client import LinearClient
  import asyncio

  client = LinearClient()
  teams = asyncio.run(client.get_teams())
  print(teams)

  # Create test issue
  issue = asyncio.run(client.create_issue(
      team_id="<your-team-id>",
      title="Test Security Review Issue",
      description="This is a test issue created by the security review tool",
      priority=2
  ))
  print(issue)
  ```

- [ ] **1.4.2** Verify issue appears in Linear workspace

- [ ] **1.4.3** Test search functionality
  ```python
  results = asyncio.run(client.search_issues("Test Security Review"))
  print(results)
  ```

---

### Phase 2: Security Models (4-6 hours)

#### 2.1 Create Security Models (2-3 hours)

- [ ] **2.1.1** Create `apps/ai/models/security.py`
  - Copy model definitions from implementation plan
  - Import required Django modules and behaviors
  - Define `SecurityReviewSession` model
  - Define `SecurityFinding` model
  - Define `SecurityRisk` model

- [ ] **2.1.2** Register models in `apps/ai/models/__init__.py`
  ```python
  from apps.ai.models.security import (
      SecurityReviewSession,
      SecurityFinding,
      SecurityRisk,
  )
  ```

- [ ] **2.1.3** Add models to Django admin
  - Create `apps/ai/admin.py` if not exists
  - Register models with basic admin configuration
  ```python
  from django.contrib import admin
  from apps.ai.models.security import SecurityReviewSession, SecurityFinding, SecurityRisk

  @admin.register(SecurityReviewSession)
  class SecurityReviewSessionAdmin(admin.ModelAdmin):
      list_display = ["query", "total_risks", "critical_count", "high_count", "created_at"]
      list_filter = ["min_severity", "created_at"]
      search_fields = ["query", "summary"]

  @admin.register(SecurityFinding)
  class SecurityFindingAdmin(admin.ModelAdmin):
      list_display = ["title", "category", "file_path", "line_number", "session"]
      list_filter = ["category", "session"]
      search_fields = ["title", "description", "file_path"]

  @admin.register(SecurityRisk)
  class SecurityRiskAdmin(admin.ModelAdmin):
      list_display = ["risk_id", "title", "severity", "score", "linear_ticket_id", "created_at"]
      list_filter = ["severity", "created_at"]
      search_fields = ["risk_id", "title", "description"]
  ```

#### 2.2 Create and Run Migration (1 hour)

- [ ] **2.2.1** Create migration
  ```bash
  uv run python manage.py makemigrations ai --name security_review_models
  ```

- [ ] **2.2.2** Review migration file
  - Check field definitions
  - Verify indexes are created
  - Ensure no unexpected changes

- [ ] **2.2.3** Run migration in local environment
  ```bash
  uv run python manage.py migrate
  ```

- [ ] **2.2.4** Verify models in Django admin
  - Run dev server: `uv run python manage.py runserver`
  - Navigate to `/admin/`
  - Verify security models appear and can be viewed

#### 2.3 Test Models (1-2 hours)

- [ ] **2.3.1** Create `apps/ai/tests/test_models_security.py`
  - Test SecurityReviewSession creation and validation
  - Test SecurityFinding creation and relationships
  - Test SecurityRisk creation and relationships
  - Test M2M relationship between Risk and Finding
  - Test model methods and properties

- [ ] **2.3.2** Run model tests
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_models_security.py -v
  ```

---

### Phase 3: Utility Functions (4-6 hours)

#### 3.1 Create Utils Directory Structure (15 min)

- [ ] **3.1.1** Create directory structure
  ```bash
  mkdir -p apps/ai/utils/tests
  touch apps/ai/utils/__init__.py
  touch apps/ai/utils/risk_scoring.py
  touch apps/ai/utils/markdown_generator.py
  touch apps/ai/utils/tests/__init__.py
  touch apps/ai/utils/tests/test_risk_scoring.py
  touch apps/ai/utils/tests/test_markdown_generator.py
  ```

#### 3.2 Implement Risk Scoring (1-2 hours)

- [ ] **3.2.1** Create `apps/ai/utils/risk_scoring.py`
  - Copy implementation from plan
  - Define `calculate_risk_score` function
  - Define `get_severity_from_score` function
  - Add docstrings with examples

- [ ] **3.2.2** Write tests for risk scoring
  - Test all severity levels
  - Test all exposure levels
  - Test PII bonus calculation
  - Test production bonus
  - Test policy weight scaling
  - Test score clamping (0-100 bounds)
  - Test severity mapping edge cases (exactly 80, 50, 20)

- [ ] **3.2.3** Run risk scoring tests
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest apps/ai/utils/tests/test_risk_scoring.py -v
  ```

#### 3.3 Implement Markdown Generator (2-3 hours)

- [ ] **3.3.1** Create `apps/ai/utils/markdown_generator.py`
  - Copy implementation from plan
  - Define `generate_security_report` function
  - Handle emoji rendering
  - Format risks and findings sections
  - Add proper markdown syntax

- [ ] **3.3.2** Write tests for markdown generator
  - Test with zero risks
  - Test with multiple risks of each severity
  - Test with multiple findings
  - Test markdown syntax validity
  - Test emoji rendering
  - Test long descriptions and code snippets

- [ ] **3.3.3** Manual validation of markdown output
  - Generate sample report
  - Save to `/tmp/test_report.md`
  - Open in markdown viewer to verify formatting
  - Check emoji display

- [ ] **3.3.4** Run markdown generator tests
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest apps/ai/utils/tests/test_markdown_generator.py -v
  ```

#### 3.4 Integration Testing (1 hour)

- [ ] **3.4.1** Test utilities together
  - Create sample findings
  - Calculate risk scores
  - Generate markdown report
  - Verify end-to-end flow

---

### Phase 4: Security Review Tool (8-12 hours)

#### 4.1 Implement Tool in CTO Tools Server (4-6 hours)

- [ ] **4.1.1** Add imports to `apps/ai/mcp/cto_tools_server.py`
  ```python
  from typing import Literal
  ```

- [ ] **4.1.2** Add `security_review` tool function
  - Copy implementation from plan
  - Define function signature with all parameters
  - Add comprehensive docstring
  - Build instruction string with proper formatting

- [ ] **4.1.3** Refine instruction quality
  - Review each phase for clarity
  - Ensure bash commands are correct
  - Verify grep patterns are accurate
  - Check that JSON/markdown output formats are valid

- [ ] **4.1.4** Add examples to instructions
  - Include 2-3 example findings
  - Show sample risk correlation
  - Demonstrate markdown output format

#### 4.2 Test Tool Locally (2-3 hours)

- [ ] **4.2.1** Test MCP server starts correctly
  ```bash
  uv run python -m apps.ai.mcp.cto_tools_server
  ```

- [ ] **4.2.2** Test with MCP Inspector
  ```bash
  npx @modelcontextprotocol/inspector uv run python -m apps.ai.mcp.cto_tools_server
  ```

- [ ] **4.2.3** Call `security_review` tool with various queries
  - "Review authentication for hardcoded credentials"
  - "Check for SQL injection vulnerabilities"
  - "Find PII data exposure in logging"
  - "Review CORS configuration for security issues"

- [ ] **4.2.4** Validate instruction output
  - Verify all 6 phases are present
  - Check bash commands are executable
  - Ensure output format is clear
  - Confirm parameters are interpolated correctly

#### 4.3 End-to-End Testing (2-3 hours)

- [ ] **4.3.1** Install CTO-Tools in Claude Desktop
  - Update `claude_desktop_config.json`
  - Restart Claude Desktop
  - Verify tool appears in available tools

- [ ] **4.3.2** Run full security review on sample codebase
  - Use cuttlefish project as test subject
  - Query: "Review for hardcoded credentials and secrets"
  - Follow all phases of instructions
  - Generate findings and risks
  - Save markdown report

- [ ] **4.3.3** Validate outputs
  - Check markdown report quality
  - Verify risk scores are calculated correctly
  - Ensure findings are structured properly
  - Confirm Linear ticket creation works (if enabled)

#### 4.4 Write Tool Tests (2 hours)

- [ ] **4.4.1** Create `apps/ai/tests/test_security_review.py`
  - Test tool returns instructions
  - Test parameter interpolation
  - Test with different severity levels
  - Test with and without data_types filter
  - Test create_tickets flag

- [ ] **4.4.2** Run tool tests
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_security_review.py -v
  ```

---

### Phase 5: Documentation (4-6 hours)

#### 5.1 Create Specification Document (2-3 hours)

- [ ] **5.1.1** Create `docs/specs/CTO_TOOLS_SECURITY_REVIEW.md`
  - Overview and purpose
  - Target users and use cases
  - Architecture diagram
  - Tool parameters and return values
  - Output format specifications
  - Linear integration details
  - Limitations and caveats

- [ ] **5.1.2** Add usage examples
  - Basic usage example
  - Advanced usage with all parameters
  - Example with Linear ticket creation
  - Example markdown report output

#### 5.2 Update CTO Tools README (1 hour)

- [ ] **5.2.1** Update `apps/ai/mcp/CTO_TOOLS_README.md`
  - Add `security_review` to tools list
  - Document installation requirements (Linear API key optional)
  - Add quick start guide
  - Include configuration examples

- [ ] **5.2.2** Add Linear setup instructions
  - How to generate Linear API key
  - How to find team ID
  - Environment variable configuration
  - Troubleshooting common issues

#### 5.3 Update Project Documentation (1-2 hours)

- [ ] **5.3.1** Update `CLAUDE.md`
  - Add security review testing commands
  - Document Linear integration setup
  - Add troubleshooting section

- [ ] **5.3.2** Update `.env.example`
  ```bash
  # Linear Integration (optional - for security_review tool)
  LINEAR_API_KEY=lin_api_xxxxxxxxxxxxxxxxxxxx
  LINEAR_DEFAULT_TEAM_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  ```

- [ ] **5.3.3** Add to main README if appropriate

---

### Phase 6: Testing & Quality Assurance (4-6 hours)

#### 6.1 Comprehensive Testing (2-3 hours)

- [ ] **6.1.1** Run all tests
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest apps/integration/linear/ apps/ai/utils/ apps/ai/tests/test_security_review.py -v --cov
  ```

- [ ] **6.1.2** Check test coverage
  - Ensure Linear client >90% coverage
  - Ensure utilities >95% coverage
  - Ensure models have basic coverage

- [ ] **6.1.3** Run code quality checks
  ```bash
  uv run black apps/integration/linear/ apps/ai/utils/ apps/ai/models/security.py
  uv run isort apps/integration/linear/ apps/ai/utils/ apps/ai/models/security.py --profile black
  uv run flake8 apps/integration/linear/ apps/ai/utils/ apps/ai/models/security.py --max-line-length=88
  ```

#### 6.2 Manual QA (2-3 hours)

- [ ] **6.2.1** Test complete workflow
  1. Start MCP server
  2. Connect from Claude Desktop
  3. Run security review on real codebase
  4. Verify findings are accurate
  5. Check markdown report quality
  6. Test Linear ticket creation
  7. Verify database records created

- [ ] **6.2.2** Test edge cases
  - Empty codebase (no findings)
  - Very large codebase (performance)
  - Invalid Linear credentials
  - Network errors during ticket creation
  - Malformed queries

- [ ] **6.2.3** Document known issues
  - Create GitHub issues for any bugs found
  - Document workarounds if applicable

---

### Phase 7: Deployment (2-4 hours)

#### 7.1 Prepare for Deployment (1 hour)

- [ ] **7.1.1** Review all changes
  ```bash
  git status
  git diff main
  ```

- [ ] **7.1.2** Ensure all tests pass
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest
  ```

- [ ] **7.1.3** Run pre-commit hooks
  ```bash
  uv run pre-commit run --all-files
  ```

- [ ] **7.1.4** Update CHANGELOG or release notes

#### 7.2 Create Pull Request (30 min)

- [ ] **7.2.1** Commit all changes
  ```bash
  git add .
  git commit -m "Add security_review tool to CTO-Tools MCP server

  - Implement Linear GraphQL client for issue creation
  - Add security review models (Session, Finding, Risk)
  - Create risk scoring and markdown generation utilities
  - Add comprehensive security_review instructional tool
  - Include full test coverage and documentation

  Generated with Claude Code
  Co-Authored-By: Claude <noreply@anthropic.com>"
  ```

- [ ] **7.2.2** Push feature branch
  ```bash
  git push origin feature/security-review-tool
  ```

- [ ] **7.2.3** Create pull request on GitHub
  - Title: "Add security_review tool to CTO-Tools"
  - Description: Link to implementation plan, highlight key features
  - Request review from team

#### 7.3 Staging Deployment (1-2 hours)

- [ ] **7.3.1** Deploy to staging environment
  - Merge to staging branch
  - Run migrations on staging database
  - Add Linear API key to staging secrets
  - Restart CTO-Tools server

- [ ] **7.3.2** Test on staging
  - Run security review on staging codebase
  - Verify Linear ticket creation works
  - Check markdown reports are saved correctly

- [ ] **7.3.3** Monitor for errors
  - Check application logs
  - Verify database records
  - Test from Claude Desktop staging config

#### 7.4 Production Deployment (1 hour)

- [ ] **7.4.1** Get approval on pull request

- [ ] **7.4.2** Merge to main branch

- [ ] **7.4.3** Deploy to production
  - Run migrations on production database
  - Add Linear API key to production secrets
  - Restart CTO-Tools server

- [ ] **7.4.4** Verify production deployment
  - Test tool availability in production Claude Desktop
  - Run sample security review
  - Monitor error rates

- [ ] **7.4.5** Announce feature to users
  - Update documentation site
  - Send notification to CTO-Tools users
  - Provide usage examples

---

### Phase 8: Post-Launch (Ongoing)

#### 8.1 Monitoring (First Week)

- [ ] **8.1.1** Monitor usage metrics
  - How many security reviews conducted?
  - How many Linear tickets created?
  - Average session duration
  - Error rates

- [ ] **8.1.2** Gather user feedback
  - Survey early adopters
  - Track support requests
  - Monitor GitHub issues

- [ ] **8.1.3** Review instruction quality
  - Are users finding the instructions clear?
  - Are findings accurate and actionable?
  - Any common confusion points?

#### 8.2 Iteration (First Month)

- [ ] **8.2.1** Address critical bugs immediately

- [ ] **8.2.2** Refine instructions based on feedback
  - Improve clarity where users get stuck
  - Add more examples
  - Adjust search patterns based on false positives/negatives

- [ ] **8.2.3** Update documentation
  - Add FAQ section
  - Include real-world examples
  - Document best practices

#### 8.3 Enhancement Planning (Month 2+)

- [ ] **8.3.1** Prioritize future enhancements from "Future Enhancements" section

- [ ] **8.3.2** Plan integration with Memory MCP for policy documents

- [ ] **8.3.3** Consider automated remediation features

- [ ] **8.3.4** Evaluate metrics against success criteria
  - Is triage time reduced by >40%?
  - Are >50 correlations surfaced in first month?
  - Is user adoption >20%?

---

## Build Plan Summary Checklist

**Pre-Implementation**:
- [ ] Review Linear API docs
- [ ] Set up API credentials
- [ ] Create feature branch
- [ ] Review existing patterns

**Phase 1 - Linear Integration** (8-12 hours):
- [ ] Create directory structure
- [ ] Implement LinearClient
- [ ] Write tests
- [ ] Manual testing

**Phase 2 - Security Models** (4-6 hours):
- [ ] Create models
- [ ] Create migration
- [ ] Add to admin
- [ ] Write tests

**Phase 3 - Utilities** (4-6 hours):
- [ ] Implement risk scoring
- [ ] Implement markdown generator
- [ ] Write tests
- [ ] Integration testing

**Phase 4 - Security Review Tool** (8-12 hours):
- [ ] Implement tool in MCP server
- [ ] Test locally
- [ ] End-to-end testing
- [ ] Write tests

**Phase 5 - Documentation** (4-6 hours):
- [ ] Create specification
- [ ] Update CTO Tools README
- [ ] Update project docs

**Phase 6 - QA** (4-6 hours):
- [ ] Comprehensive testing
- [ ] Manual QA
- [ ] Code quality checks

**Phase 7 - Deployment** (2-4 hours):
- [ ] Prepare for deployment
- [ ] Create PR
- [ ] Staging deployment
- [ ] Production deployment

**Phase 8 - Post-Launch** (Ongoing):
- [ ] Monitor usage
- [ ] Gather feedback
- [ ] Iterate on instructions
- [ ] Plan enhancements

**Estimated Total Time**: 36-54 hours (1-1.5 weeks full-time)

---

## Conclusion

This implementation plan provides a clear roadmap for adding the `security_review` tool to the CTO-Tools MCP server. The tool follows Cuttlefish patterns (FastMCP, Django models, async clients) and provides an instructional framework that guides AI agents through comprehensive security reviews while producing actionable, well-documented outputs.

**Feasibility Assessment**: ✅ HIGH - All components technically proven, reasonable scope, strong value proposition.

**Key Success Factors**:
1. Start with Linear client (highest risk component)
2. Test extensively with mock data before production
3. Gather early feedback on instruction quality
4. Keep MVP scope tight, defer enhancements
5. Document limitations clearly (not a replacement for dedicated security tools)

The phased approach ensures steady progress with testable milestones, and the modular design allows for future enhancements without major refactoring. The detailed build plan provides a step-by-step checklist to keep development on track and ensure nothing is missed.
