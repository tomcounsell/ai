"""Notion Scout agent for database querying."""

import anthropic
import requests


class NotionScout:
    """Simple Notion database query agent for Telegram integration."""

    def __init__(self, notion_key: str, anthropic_key: str):
        self.notion_key = notion_key
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
        self.db_filter = None

    async def query_database_entries(self, database_id: str) -> dict:
        """Query actual entries from a specific Notion database."""
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        try:
            query_url = f"https://api.notion.com/v1/databases/{database_id}/query"
            response = requests.post(query_url, headers=headers, json={})

            if response.status_code != 200:
                return {"error": f"Error querying database: {response.status_code}"}

            return response.json()

        except Exception as e:
            return {"error": f"Error querying database entries: {str(e)}"}

    def extract_property_value(self, prop_value: dict) -> str:
        """Extract readable value from Notion property."""
        if not prop_value:
            return ""

        prop_type = prop_value.get("type", "")

        if prop_type == "title":
            return "".join([t.get("plain_text", "") for t in prop_value.get("title", [])])
        elif prop_type == "rich_text":
            return "".join([t.get("plain_text", "") for t in prop_value.get("rich_text", [])])
        elif prop_type == "select":
            select_obj = prop_value.get("select")
            return select_obj.get("name", "") if select_obj else ""
        elif prop_type == "multi_select":
            return ", ".join([s.get("name", "") for s in prop_value.get("multi_select", [])])
        elif prop_type == "status":
            status_obj = prop_value.get("status")
            return status_obj.get("name", "") if status_obj else ""
        elif prop_type == "checkbox":
            return "Yes" if prop_value.get("checkbox") else "No"
        elif prop_type == "number":
            return str(prop_value.get("number", ""))
        elif prop_type == "date":
            date_obj = prop_value.get("date")
            return date_obj.get("start", "") if date_obj else ""
        else:
            return str(prop_value.get(prop_type, ""))

    async def query_notion_directly(self, question: str) -> str:
        """Query Notion API directly to get actual database content."""
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        try:
            search_url = "https://api.notion.com/v1/search"
            search_payload = {"filter": {"value": "database", "property": "object"}}

            response = requests.post(search_url, headers=headers, json=search_payload)

            if response.status_code != 200:
                return f"Error accessing Notion API: {response.status_code}"

            data = response.json()
            databases = data.get("results", [])

            if self.db_filter:
                databases = [db for db in databases if self.db_filter in db["id"]]
                if not databases:
                    return f"No database found matching '{self.db_filter}'"

            if not databases:
                return "No databases found accessible to the integration."

            all_entries = []
            for db in databases:
                db_id = db["id"]
                db_title = "".join([t.get("plain_text", "") for t in db.get("title", [])])

                entries_data = await self.query_database_entries(db_id)
                if "error" in entries_data:
                    continue

                entries = entries_data.get("results", [])

                for entry in entries:
                    entry_data = {
                        "database": db_title,
                        "id": entry["id"],
                        "url": entry.get("url", ""),
                        "properties": {},
                    }

                    for prop_name, prop_value in entry.get("properties", {}).items():
                        entry_data["properties"][prop_name] = self.extract_property_value(
                            prop_value
                        )

                    all_entries.append(entry_data)

            return self.analyze_entries_with_claude(all_entries, question)

        except Exception as e:
            return f"Error querying Notion: {str(e)}"

    def analyze_entries_with_claude(self, entries: list, question: str) -> str:
        """Use Claude to analyze the database entries and answer the question."""
        if not entries:
            return "No database entries found to analyze."

        entries_text = "NOTION DATABASE ENTRIES:\n\n"
        for i, entry in enumerate(entries[:20], 1):  # Limit for Telegram
            entries_text += f"Entry {i}:\n  Database: {entry['database']}\n"
            for prop_name, prop_value in entry["properties"].items():
                if prop_value and prop_value.strip():
                    entries_text += f"  {prop_name}: {prop_value}\n"
            entries_text += "\n"

        system_prompt = """You are analyzing Notion database entries to answer questions. Provide concise, specific answers suitable for Telegram messages (under 300 words). Focus on the most relevant and actionable information."""

        try:
            response = self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=400,
                temperature=0.3,
                system=system_prompt,
                messages=[{"role": "user", "content": f"Question: {question}\n\n{entries_text}"}],
            )

            return response.content[0].text

        except Exception as e:
            return f"Error analyzing entries: {str(e)}"

    async def answer_question(self, question: str) -> str:
        """Answer a question by querying Notion database."""
        return await self.query_notion_directly(question)
