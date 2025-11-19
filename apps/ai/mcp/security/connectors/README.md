# Custom Security Connectors

This directory is for **user-provided custom connector plugins**.

## How to Add a Custom Connector

1. Create a Python file in this directory (e.g., `my_sast_tool.py`)
2. Extend one of the base connector classes
3. Implement the required methods
4. Connectors are auto-discovered at startup

## Example: Custom SAST Connector

```python
# apps/ai/mcp/security/connectors/semgrep.py
import os
from apps.ai.mcp.security.base_connector import SASTConnector
from apps.ai.mcp.security.types import Alert

class SemgrepConnector(SASTConnector):
    """Custom connector for Semgrep."""

    name = "semgrep"  # Unique name for this connector

    def __init__(self):
        api_key = os.environ.get("SEMGREP_API_KEY")
        if not api_key:
            raise ValueError("SEMGREP_API_KEY environment variable required")

        super().__init__(
            api_key=api_key,
            api_url="https://semgrep.dev/api/v1"
        )

    async def test_connection(self) -> bool:
        """Test if we can connect to Semgrep API."""
        # Your implementation
        return True

    async def fetch_alerts(self, time_window_hours: int = 24) -> list[Alert]:
        """Fetch vulnerability alerts from Semgrep."""
        # Your implementation
        return []

    async def fetch_assets(self) -> list:
        """Fetch asset information."""
        # Your implementation
        return []
```

## Base Connector Types

- **SASTConnector** - Static Application Security Testing (code vulnerabilities)
- **DASTConnector** - Dynamic Application Security Testing (runtime vulnerabilities)
- **CSPMConnector** - Cloud Security Posture Management (cloud misconfigurations)
- **ThreatIntelConnector** - Threat Intelligence (IOCs, threat feeds)
- **PolicyConnector** - Compliance Policies (security policies, frameworks)

## Required Methods

All connectors must implement:
- `test_connection()` - Verify API connectivity
- `fetch_alerts()` - Retrieve security alerts
- `fetch_assets()` - Retrieve asset information

## Environment Variables

Use environment variables for sensitive credentials:
```bash
# .env.local
MY_TOOL_API_KEY=your_key_here
MY_TOOL_API_URL=https://api.mytool.com
```

## See Also

- `apps/ai/mcp/security/examples/` - Demo connector examples
- `apps/ai/mcp/security/base_connector.py` - Base connector classes
- `apps/ai/mcp/security/builtin_connectors.py` - Built-in connectors (Snyk, AWS Security Hub)
