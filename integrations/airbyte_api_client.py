import requests


class AirbyteApiClient:
    def __init__(self, base_url, api_key):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def get_workspaces(self):
        """Get all workspaces."""
        response = requests.get(f"{self.base_url}/workspaces", headers=self.headers, timeout=180)
        response.raise_for_status()
        return response.json()

    def get_sources(self, workspace_id):
        """Get all sources for a workspace."""
        response = requests.get(
            f"{self.base_url}/sources?workspaceId={workspace_id}", headers=self.headers, timeout=180
        )
        response.raise_for_status()
        return response.json()

    def get_destinations(self, workspace_id):
        """Get all destinations for a workspace."""
        response = requests.get(
            f"{self.base_url}/destinations?workspaceId={workspace_id}", headers=self.headers, timeout=180
        )
        response.raise_for_status()
        return response.json()

    def create_connection(self, connection_data):
        """Create a new connection."""
        response = requests.post(
            f"{self.base_url}/connections", headers=self.headers, json=connection_data, timeout=180
        )
        response.raise_for_status()
        return response.json()

    def trigger_sync(self, connection_id):
        """Trigger a sync for a connection."""
        response = requests.post(
            f"{self.base_url}/connections/sync",
            headers=self.headers,
            json={"connectionId": connection_id},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()


# Usage example:
# client = AirbyteApiClient("https://api.airbyte.com/v1", "your_api_key_here")
# workspaces = client.get_workspaces()
