"""
Custom connector plugins directory.

Drop Python files here to add custom security tool connectors.
Each connector should extend one of the base classes:
- SASTConnector
- DASTConnector
- CSPMConnector
- ThreatIntelConnector
- PolicyConnector

Example:
    # apps/ai/mcp/security/connectors/my_tool.py
    from apps.ai.mcp.security.base_connector import SASTConnector
    from apps.ai.mcp.security.types import Alert

    class MyToolConnector(SASTConnector):
        name = "my_tool"

        def __init__(self):
            super().__init__(
                api_key=os.environ.get("MY_TOOL_API_KEY"),
                api_url=os.environ.get("MY_TOOL_URL")
            )

        async def fetch_alerts(self, time_window_hours=24):
            # Your implementation here
            return []

Connectors are auto-discovered and registered at startup.
"""
