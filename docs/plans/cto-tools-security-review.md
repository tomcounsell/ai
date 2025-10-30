# Product Requirements Document: Security Review Tool

## 1. Overview

**Name**: `security_review`
**MCP Server**: cto-tools
**Purpose**: Enable CTOs, Heads of Security, and DevSecOps teams to automatically correlate alerts from multiple security/monitoring tools with policy/compliance context, score and prioritize risks, and trigger actionable issues in Linear.

## 2. Goals & Success Metrics

### Goals
- Reduce manual triage time for cross-platform security incidents
- Surface actionable risks instead of raw alerts
- Increase speed from detection → remediation
- Provide traceability from alert → policy violation → ticket

### Success Metrics
- Median time from new alert → correlated risk output < X hours
- % reduction in alerts requiring human cross-tool correlation
- % of critical risks created as Linear issues within Y minutes
- Reduction in mean time to remediation for PII-data related incidents
- User satisfaction (CTO/Head of Security) > NPS threshold

## 3. Target Users & Use Cases

### Users
- **CTO / Head of Engineering**: wants a dashboard of top risks mapped to business impact
- **Head of Security / DevSecOps Lead**: needs actionable, prioritized list of what to fix now
- **Compliance Officer**: wants documentation of policy violations with remediation steps

### Use Cases
- "Show me the top 3 high-severity risks related to PII data in the last 72 hours"
- "Which exposed cloud resources are referenced by vulnerable code modules?"
- "Generate a remediation ticket in Linear for the critical risk we found"
- "Provide a natural-language summary for executive reporting of current risk posture"

## 4. Functional Requirements

### 4.1 Inputs / Connectors
- SAST/DAST tool APIs (e.g., code vulnerability scanners)
- Cloud Posture / CSPM tool APIs (e.g., asset exposure like S3 buckets)
- Threat Intelligence Feeds (e.g., indicators of compromise)
- Compliance/Policy Docs via Memory MCP (e.g., "PII Data Handling Policy")
- Query from user (natural language) via MCP client

### 4.2 Processing Flow

1. User issues a query like:
```python
security_review(
    query="top 3 high-severity PII risks in last 72h"
)
```

2. The MCP server interprets the query, extracts filter criteria (severity, PII data, time window)
3. Parallel API calls to connectors to fetch relevant alerts
4. Fetch policy documents (Memory MCP) relevant to the query (e.g., "PII encryption required")
5. Provide input to the LLM (e.g., Claude Code) with: alert data + asset/context metadata + policy content
6. LLM correlates: links vulnerable code → exposed resource → policy violation; computes a risk score
7. Generate structured output: risk summary, severity, reason, remediation actions, suggested assignee/team
8. Optionally invoke ticket creation in Linear via Linear API
9. Provide natural-language summary + machine-readable JSON output

### 4.3 Outputs

**Natural-language summary**, e.g.:
```
Risk SEC-221: A publicly-accessible S3 bucket contains unencrypted customer PII
which is referenced by a code module in Repo X flagged by the SAST tool. This
violates the PII encryption policy. Recommendation: Encrypt bucket, rotate IAM
keys, notify DPO.
```

**Structured JSON**, e.g.:
```json
{
  "risk_id": "SEC-221",
  "severity": "Critical",
  "description": "Unencrypted S3 bucket with PII referenced by vulnerable code module in Repo X",
  "linked_alerts": ["AlertA", "AlertB"],
  "policy": "PII Data Handling – Encryption at Rest",
  "actions": [
    "Encrypt S3 bucket",
    "Rotate IAM keys",
    "Assign to DevSecOps Team",
    "Notify DPO"
  ],
  "ticket_id": "LIN-1054"
}
```

**Ticket creation in Linear**: using Linear GraphQL API: create issue, set team, priority, description, tags

**Dashboard view or feed**: list of top risks, status of tickets, closed vs open

## 5. Non-Functional Requirements

- **Security**: All connections to security tools must use secure credentials, encrypted at rest and in transit
- **Scalability**: Handle multiple sources, large volume of alerts, correlate in near real-time
- **Reliability**: Graceful fallback if one connector fails
- **Latency**: Correlation and action output within defined SLA (e.g., <30 min for critical query)
- **Auditability**: Maintain history of which alerts were correlated, what the LLM reasoning was, ticket creation details
- **Explainability**: Provide trace of LLM reasoning (which alerts + policy matched + why scored)
- **Extensibility**: Easy to add new connectors (e.g., new CSPM tool) and new policy documents

## 6. Risk Scoring Model

Define algorithm combining:
- **Alert severity** (e.g., CVSS score, scanner criticality)
- **Exposure risk** (public access, sensitive data classification)
- **Policy violation weight** (how severe the breached policy is)
- **Business impact** (asset in production, customer-facing, PII)

Compute: `risk_score = f(severity, exposure, policy_weight, business_impact)` producing 0-100 scale.

Map to labels:
- **80-100** → Critical
- **50-79** → High
- **20-49** → Medium
- **<20** → Low

## 7. User Interface & Interaction

- CLI or chat interface via MCP client (LLM conversational)
- Query format: `security_review(query="<natural language>")`
- Output: immediate summary + link to detailed view + ticket link if created
- Web dashboard (optional) showing risk list, statuses, filters (time window, data type, severity)
- Notifications to Slack/email when new Critical risks are surfaced

## 8. Integration with Linear

- Use Linear GraphQL API to create issues
- Fields: team, title, description, priority (e.g., "High"), labels ("security", "PII"), due date
- On creation output ticket ID in summary JSON
- Optional: Update ticket status when risk is mitigated (via webhook/connector)
- Ability to query Linear for existing issues to avoid duplicates

## 9. Data & Policy Management

- Policy documents stored in Memory MCP (RAG retrieval)
- Maintain mapping of policy IDs → applicable assets/risks
- Asset metadata (e.g., from cloud tools) should include classification (PII, regulated), environment (prod/staging)
- Alert metadata: timestamp, tool, type, asset reference, severity, remediation recommended
- Retain history of correlated risks for trend analysis

## 10. Flow Diagram (High-Level)

```
1. User query → MCP server
2. Connectors fetch alert data + asset data
3. Memory MCP fetches policy docs
4. LLM processes all context → risk correlations + scoring
5. System outputs summary + JSON → optional Linear ticket creation
6. Dashboard/notifications update
```

## 11. Milestones & Timeline

- **M1 (2 weeks)**: Define connector interface spec; stub connectors for one SAST tool and one cloud posture tool
- **M2 (4 weeks)**: Build Memory MCP ingestion pipeline for policy docs; integrate with LLM for simple correlation
- **M3 (6 weeks)**: Implement Linear API wrapper; enable ticket creation
- **M4 (8 weeks)**: Build risk scoring engine; UI/CLI prototype; dashboard mock-up
- **M5 (10 weeks)**: QA, security review, load testing; rollout MVP to internal team
- **M6 (12 weeks)**: Extend connectors to additional tools; add trend analysis; rollout to early customers

## 12. Dependencies

- Access/credentials to security tools APIs (SAST/DAST, CSPM)
- Linear workspace access with API permissions (GraphQL)
- LLM instance access (Claude Code or equivalent)
- Memory MCP infrastructure for policy storage and retrieval
- Dashboard/notification infrastructure (Slack/web)

## 13. Constraints & Assumptions

- Assume the security tools provide APIs and alert metadata with sufficient context (asset id, severity, timestamp)
- Assume the policy docs are in machine-readable form (text/pdf) and ingestion into Memory MCP is feasible
- LLM is capable of reasoning over structured alerts + policy text + asset context
- Ticket creation permissions are allowed in the target Linear workspace
- Privacy/regulatory constraints: PII data must be handled according to internal policy

## 14. Success/Exit Criteria

**Success**: The tool is accepted by the security leadership team, triage time drops by ≥40%, and at least 50 cross-tool correlations are automatically surfaced in first month.

**Exit**: If after 3 months adoption <20% of triage uses the tool or manual triage still dominates, reevaluate.

## 15. Future Enhancements

- Continuous monitoring (event-driven) rather than on user query
- Automated remediation triggers (e.g., server isolation, IAM policy update)
- Trend and benchmarking dashboards (risk count over time, closure time vs SLA)
- Prioritization by cost/impact modeling (business value at risk)
- Multi-tenant support (for MSPs) and role-based access

## 16. API Specification (Draft)

### Tool Definition

```python
@mcp.tool()
async def security_review(
    query: str,
    time_window_hours: int = 72,
    min_severity: Literal["Low", "Medium", "High", "Critical"] = "Medium",
    data_types: list[str] | None = None,
    create_tickets: bool = False,
    max_results: int = 10
) -> str:
    """
    Correlate security alerts across multiple tools with policy context.

    Args:
        query: Natural language query describing the security review scope
        time_window_hours: How many hours back to review alerts (default: 72)
        min_severity: Minimum severity level to include (default: Medium)
        data_types: Filter by data types (e.g., ["PII", "credentials"])
        create_tickets: Whether to auto-create Linear tickets for critical risks
        max_results: Maximum number of risks to return (default: 10)

    Returns:
        Natural language summary with structured JSON of correlated risks
    """
```

### Response Schema

```python
from pydantic import BaseModel
from typing import Literal

class RiskAction(BaseModel):
    action: str
    assignee: str | None = None
    priority: Literal["Low", "Medium", "High", "Critical"]

class Risk(BaseModel):
    risk_id: str
    severity: Literal["Low", "Medium", "High", "Critical"]
    title: str
    description: str
    linked_alerts: list[str]
    policy_violations: list[str]
    affected_assets: list[str]
    actions: list[RiskAction]
    ticket_id: str | None = None
    score: float  # 0-100

class SecurityReviewResponse(BaseModel):
    summary: str
    risks: list[Risk]
    total_alerts_reviewed: int
    correlation_confidence: float
    timestamp: str
```

## 17. Backlog / Future Enhancements

### Linear Integration (Multi-tenant Support)

**Status**: Not yet implemented - requires user authentication and secure credential storage

**Requirements**:
1. **User Authentication**: Integrate with Django auth system
2. **Secure Storage**: Store Linear API keys per-organization in encrypted fields
3. **OAuth Flow**: Consider Linear OAuth instead of API keys for better security
4. **Models Needed**:
   ```python
   class LinearConnection(Timestampable, models.Model):
       organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
       api_key = models.TextField()  # Should be encrypted at rest
       workspace_id = models.CharField(max_length=255)
       team_id = models.CharField(max_length=255, blank=True)
       is_active = models.BooleanField(default=True)
   ```
5. **MCP Session Auth**: Link MCP sessions to organizations for API key retrieval
6. **Security Considerations**:
   - Encrypt API keys at rest (use Django's `cryptography` package)
   - Implement key rotation mechanism
   - Audit log all ticket creation actions
   - Rate limiting per organization

**Implementation Effort**: 3-5 days
**Priority**: Low (tool is fully functional without it)

**Alternative**: Users can copy-paste risk summaries into Linear manually until multi-tenant support is built.
