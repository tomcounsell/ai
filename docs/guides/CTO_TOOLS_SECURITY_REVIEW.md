# CTO Tools Security Review - Implementation Guide

## Overview

The CTO Tools MCP server's `security_review` tool provides automated correlation of security alerts from multiple sources (SAST/DAST, CSPM, threat intelligence) with policy violations to identify, score, and prioritize security risks.

**Status**: Fully functional with built-in and custom connectors. Linear integration deferred to backlog (requires multi-tenant authentication).

## Architecture

### Components

```
apps/ai/mcp/cto_tools_server.py          # Main MCP server with tools
apps/ai/mcp/security/
├── __init__.py
├── types.py                              # Data models (Alert, Asset, Policy, etc.)
├── base_connector.py                     # Abstract base classes for connectors
├── connector_registry.py                 # Auto-discovery and connector management
├── builtin_connectors.py                 # Built-in Snyk & AWS Security Hub connectors
├── correlation_engine.py                 # Alert-asset-policy correlation
├── risk_scorer.py                        # Risk scoring (0-100 scale)
├── connectors/                           # Plugin directory for custom connectors
│   ├── __init__.py
│   └── README.md                         # How to add custom connectors
└── examples/                             # Example connector implementations
    ├── __init__.py
    └── demo_connectors.py                # Demo SAST/CSPM/Policy (for reference)
apps/ai/mcp/integrations/
└── linear_client.py                      # Linear GraphQL API client (future use)
```

### Data Flow

```
User Query → Parse Filters → Fetch Alerts (SAST/DAST/CSPM)
                           → Fetch Policies
                           → Correlate (alerts + assets + policies)
                           → Score Risks (0-100)
                           → Generate Summary + JSON (with ticket details for manual creation)
```

## Installation

### Local Development

1. **Ensure dependencies are installed:**
```bash
cd /path/to/cuttlefish
uv sync --all-extras
```

2. **Run the MCP server:**
```bash
uv run python -m apps.ai.mcp.cto_tools_server
```

3. **Run tests:**
```bash
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_cto_tools.py -v
```

### Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cto-tools": {
      "command": "uv",
      "args": ["run", "python", "-m", "apps.ai.mcp.cto_tools_server"],
      "cwd": "/path/to/cuttlefish"
    }
  }
}
```

## Configuration

The security review tool supports three types of connectors:

1. **Built-in Connectors** - Snyk and AWS Security Hub (activated via environment variables)
2. **Custom Plugin Connectors** - Your own Python connectors in `apps/ai/mcp/security/connectors/`
3. **Example Connectors** - Demo implementations in `apps/ai/mcp/security/examples/` (for reference only)

### Built-in Connectors

#### Snyk SAST Connector

Enable by setting environment variables in `.env.local`:

```bash
SNYK_API_KEY=your_snyk_api_token
SNYK_ORG_ID=your_organization_id
SNYK_API_URL=https://api.snyk.io/v1  # Optional, uses default if not set
```

The connector will auto-register on startup and fetch code vulnerabilities from your Snyk projects.

#### AWS Security Hub CSPM Connector

Enable by setting standard AWS credentials in `.env.local`:

```bash
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
AWS_SECURITY_HUB_ACCOUNT_ID=your_account_id  # Optional
```

The connector will auto-register and fetch security findings from AWS Security Hub.

**Note**: Requires `boto3` package and Security Hub enabled in your AWS account.

### Custom Plugin Connectors

Create your own connectors by adding Python files to `apps/ai/mcp/security/connectors/`.

**Example** (`apps/ai/mcp/security/connectors/my_tool.py`):

```python
import os
from apps.ai.mcp.security.base_connector import SASTConnector
from apps.ai.mcp.security.types import Alert, QueryFilters

class MyToolConnector(SASTConnector):
    name = "my_tool"

    def __init__(self):
        api_key = os.environ.get("MY_TOOL_API_KEY")
        if not api_key:
            raise ValueError("MY_TOOL_API_KEY required")

        super().__init__(
            api_key=api_key,
            api_url=os.environ.get("MY_TOOL_URL", "https://api.mytool.com")
        )

    async def test_connection(self) -> bool:
        # Test your API connection
        return True

    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        # Fetch alerts from your tool
        return []

    async def fetch_assets(self, asset_ids: list[str]) -> list:
        # Fetch asset details
        return []
```

Connectors are auto-discovered at startup. See `apps/ai/mcp/security/connectors/README.md` for full details.

### Example Connectors (Demo Mode)

Example connectors are available in `apps/ai/mcp/security/examples/demo_connectors.py`:
- **DemoSASTConnector** - Code vulnerabilities (SQL injection, hardcoded credentials, etc.)
- **DemoCSPMConnector** - Cloud misconfigurations (public S3 buckets, unencrypted RDS, etc.)
- **DemoPolicyConnector** - Compliance policies (PII handling, encryption, credentials, IAM)

**These are NOT auto-loaded**. They're examples for building custom connectors. To use them for testing, copy them to the `connectors/` directory or reference them in tests.

## Usage

### Basic Security Review

```
Use the security_review tool with:
query: "What are the critical security risks?"
```

**Example Output:**
```
Security review completed for query: 'What are the critical security risks?'.

Risk Summary: 2 risks identified:
- 2 Critical

Top Risk: Hardcoded credentials, Unencrypted S3 bucket with PII affecting customer-data-prod (Critical, Score: 92.3/100)

## Top Risks

### RISK-SAST-02: hardcoded, unencrypted affecting customer-data-prod, payment-service
**Severity**: Critical (Score: 92.3/100)
A critical severity vulnerability was detected: Hardcoded credentials in payment processing module.
A critical severity misconfiguration was detected: Publicly accessible S3 bucket contains PII data.

**Policy Violations**: PII Data Handling Policy, Credential Management Policy, Encryption at Rest Policy
**Affected Assets**: customer-data-prod, payment-service
**Linked Alerts**: SAST-a1b2c3d4, CSPM-e5f6g7h8

**Recommended Actions**:
- [Critical] Rotate exposed credentials and move to secret management system (→ platform-security)
- [Critical] Enable encryption at rest for affected data stores (→ platform-security)
- [Critical] Remove public access and implement proper access controls (→ platform-security)
- [High] Notify Data Protection Officer (DPO) of PII exposure (→ compliance)
```

### PII-Specific Review

```
Use the security_review tool with:
query: "Show me PII-related risks in the last 48 hours"
time_window_hours: 48
data_types: ["PII"]
min_severity: "Medium"
```

### Export for Manual Ticketing

The tool outputs detailed risk information in both human-readable and JSON formats. You can copy-paste this into your ticketing system (Linear, Jira, etc.):

```
Use the security_review tool with:
query: "Review all critical production risks"
min_severity: "Critical"
max_results: 10
```

The output includes:
- Risk ID, severity, and score
- Detailed description
- Affected assets
- Policy violations
- Recommended actions with assignees

**Note**: Automated Linear ticket creation requires multi-tenant authentication (see backlog in PRD)

### List Connectors

```
Use the list_connectors tool
```

Shows:
- All configured connectors
- Connection status
- Capabilities
- Last sync time

### Configure New Connector

```
Use the configure_connector tool with:
connector_type: "sast"
connector_name: "snyk-scanner"
api_key: "your_snyk_api_key"
api_url: "https://api.snyk.io"
```

## Risk Scoring Model

Each risk is scored 0-100 based on:

### 1. Alert Severity (40%)
- Uses CVSS scores and scanner criticality
- Critical: 4.0x weight
- High: 3.0x weight
- Medium: 2.0x weight
- Low: 1.0x weight

### 2. Exposure Risk (30%)
- **Environment**: Production (3.0x), Staging (1.5x), Development (0.5x)
- **Data Classification**:
  - Credentials: 3.0x
  - Financial: 2.8x
  - PII: 2.5x
  - PCI-DSS: 3.0x
- **Public Access**: 2.0x multiplier

### 3. Policy Violations (30%)
- Policy severity weights (configurable per policy)
- Multiple violations increase severity
- Typical weights: 1.0-3.0

### 4. Business Impact (multiplier)
- Sensitive data + production + public access can multiply up to 1.5x
- Correlation confidence affects final score

### Severity Mapping
- **80-100**: Critical
- **50-79**: High
- **20-49**: Medium
- **<20**: Low

## Adding Custom Connectors

### 1. Create Connector Class

Create `apps/ai/mcp/security/custom_connectors.py`:

```python
from .base_connector import SASTConnector
from .types import Alert, Asset, QueryFilters
import httpx

class SnykConnector(SASTConnector):
    """Snyk SAST connector."""

    async def test_connection(self) -> bool:
        """Test Snyk API connectivity."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url or 'https://api.snyk.io'}/v1/user/me",
                    headers={"Authorization": f"token {self.api_key}"},
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception:
            return False

    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        """Fetch vulnerabilities from Snyk."""
        alerts = []
        async with httpx.AsyncClient() as client:
            # Call Snyk API to get vulnerabilities
            response = await client.post(
                f"{self.api_url}/v1/org/{org_id}/projects",
                headers={"Authorization": f"token {self.api_key}"},
                timeout=30.0,
            )

            data = response.json()
            for issue in data.get("issues", []):
                alert = Alert(
                    alert_id=issue["id"],
                    source="Snyk",
                    severity=self._map_severity(issue["severity"]),
                    title=issue["title"],
                    description=issue.get("description", ""),
                    detected_at=datetime.fromisoformat(issue["publicationTime"]),
                    alert_type="vulnerability",
                    asset_id=issue.get("packageName"),
                    asset_type="code_module",
                    cvss_score=issue.get("cvssScore"),
                    cwe_id=issue.get("identifiers", {}).get("CWE", [None])[0],
                )
                alerts.append(alert)

        return alerts

    async def fetch_assets(self, asset_ids: list[str]) -> list[Asset]:
        """Fetch code module details from Snyk."""
        # Implement asset fetching
        pass

    def _map_severity(self, snyk_severity: str) -> str:
        """Map Snyk severity to standard levels."""
        mapping = {
            "critical": "Critical",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
        }
        return mapping.get(snyk_severity.lower(), "Medium")
```

### 2. Register Connector

In `apps/ai/mcp/security/connector_registry.py`, update `_initialize_demo_connectors()`:

```python
from .custom_connectors import SnykConnector

def _initialize_demo_connectors(self):
    # ... existing demo connectors ...

    # Add real connector if API key is available
    import os
    snyk_api_key = os.getenv("SNYK_API_KEY")
    if snyk_api_key:
        self._connectors["snyk"] = SnykConnector(
            api_key=snyk_api_key,
            api_url="https://api.snyk.io",
        )
```

### 3. Add Environment Variable

In `.env.local`:
```bash
SNYK_API_KEY=your_snyk_api_key_here
```

## Customizing Risk Scoring

Edit `apps/ai/mcp/security/risk_scorer.py`:

```python
class RiskScorer:
    # Adjust weights
    SEVERITY_WEIGHTS = {
        "Critical": 5.0,  # Increased from 4.0
        "High": 3.0,
        "Medium": 2.0,
        "Low": 1.0,
    }

    # Add custom data classification weights
    CLASSIFICATION_WEIGHTS = {
        "PII": 2.5,
        "PHI": 3.5,  # Healthcare data gets higher weight
        "credentials": 3.0,
        "financial": 2.8,
        # ... add more
    }
```

## Troubleshooting

### "No risks found"

**Causes:**
- Demo connectors have limited synthetic data
- Filters too restrictive (try min_severity="Low")
- Time window doesn't include when demo alerts were created

**Solution:**
```
Use security_review with:
query: "all risks"
min_severity: "Low"
time_window_hours: 168  # 7 days
max_results: 20
```

### "Failed to create Linear ticket"

**Causes:**
- `LINEAR_API_KEY` not set
- API key lacks permissions
- Network connectivity issue

**Solution:**
1. Verify environment variable: `echo $LINEAR_API_KEY`
2. Test Linear API key: https://linear.app/settings/api
3. Check Claude Desktop config includes the env var
4. Review logs for specific error messages

### Connector Connection Failures

**Check connector status:**
```
Use list_connectors tool
```

**Reconfigure connector:**
```
Use configure_connector with:
connector_type: "sast"
connector_name: "my_scanner"
api_key: "updated_key"
```

## Testing

### Run All Tests
```bash
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_cto_tools.py -v
```

### Test Specific Tool
```bash
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_cto_tools.py::test_security_review_basic_query -v
```

### Test with MCP Inspector
```bash
mcp-inspector uv run python -m apps.ai.mcp.cto_tools_server
```

## Production Deployment

### Via Render

1. **Deploy Django app** (already done for Cuttlefish)
2. **Add environment variables** in Render dashboard:
   - `LINEAR_API_KEY=lin_api_...`
   - Any custom connector API keys

3. **Web hosting URL**: https://ai.yuda.me/mcp/cto-tools

4. **Client configuration** (HTTP mode):
```json
{
  "mcpServers": {
    "cto-tools": {
      "url": "https://ai.yuda.me/mcp/cto-tools/serve"
    }
  }
}
```

### Standalone Mode

Run directly from URL:
```json
{
  "mcpServers": {
    "cto-tools": {
      "command": "uv",
      "args": ["run", "https://raw.githubusercontent.com/yudame/cuttlefish/main/apps/ai/mcp/cto_tools_server.py"],
      "env": {
        "LINEAR_API_KEY": "lin_api_..."
      }
    }
  }
}
```

## Security Considerations

### API Keys
- Store in environment variables, never in code
- Use separate API keys for dev/staging/production
- Rotate keys regularly
- Use read-only API keys when possible

### Data Privacy
- Tool doesn't store security data
- All correlation happens in-memory
- Linear tickets contain risk summaries (ensure your Linear workspace has appropriate access controls)

### Audit Trail
- All tool calls logged to standard output
- Consider forwarding logs to SIEM
- Linear tickets provide traceable records

## Future Enhancements

Planned features:
- [ ] Continuous monitoring mode (webhook-driven)
- [ ] Automated remediation triggers
- [ ] Trend analysis dashboards
- [ ] Multi-tenant support
- [ ] Cost/impact modeling
- [ ] Integration with AWS Security Hub
- [ ] Integration with Wiz CSPM
- [ ] Integration with Memory MCP for policy documents

## Support

- **Issues**: https://github.com/yudame/cuttlefish/issues
- **PRD**: `docs/plans/cto-tools-security-review.md`
- **Code**: `apps/ai/mcp/cto_tools_server.py`
