"""Link tracking functionality for Telegram messages."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup


class LinkTracker:
    """Handles URL detection, metadata fetching, and storage."""

    def __init__(self, storage_file: str = "links.json"):
        """Initialize the link tracker with storage file path."""
        self.storage_file = Path(storage_file)
        self._ensure_storage_file()

    def _ensure_storage_file(self):
        """Ensure the storage file exists and is valid JSON."""
        if not self.storage_file.exists():
            self._save_links([])
        else:
            try:
                self._load_links()
            except (OSError, json.JSONDecodeError):
                # File exists but is corrupted, reset it
                self._save_links([])

    def _load_links(self) -> list[dict[str, Any]]:
        """Load links from the storage file."""
        try:
            with open(self.storage_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

    def _save_links(self, links: list[dict[str, Any]]):
        """Save links to the storage file."""
        with open(self.storage_file, "w", encoding="utf-8") as f:
            json.dump(links, f, indent=2, ensure_ascii=False)

    def is_url_only_message(self, text: str) -> bool:
        """Check if message contains only a URL (and optional whitespace)."""
        if not text or not text.strip():
            return False

        # Remove whitespace and check if it's a single URL
        clean_text = text.strip()
        urls = self.extract_urls(clean_text)

        # Must have exactly one URL and the cleaned text should be just that URL
        if len(urls) == 1:
            # Check if the entire message is just the URL (allowing for protocol differences)
            url = urls[0]
            return clean_text == url or clean_text.replace("https://", "http://") == url.replace(
                "https://", "http://"
            )

        return False

    def extract_urls(self, text: str) -> list[str]:
        """Extract URLs from text using regex."""
        # Comprehensive URL regex pattern
        url_pattern = re.compile(
            r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
        )
        return url_pattern.findall(text)

    def validate_url(self, url: str) -> bool:
        """Validate if a URL is properly formatted."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    async def fetch_url_metadata(self, url: str) -> dict[str, Any]:
        """Fetch metadata for a URL asynchronously."""
        metadata = {
            "title": None,
            "description": None,
            "domain": None,
            "status_code": None,
            "error": None,
        }

        try:
            # Parse URL to get domain
            parsed = urlparse(url)
            metadata["domain"] = parsed.netloc

            # Set up session with reasonable timeout and headers
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {"User-Agent": "Mozilla/5.0 (Telegram Link Tracker Bot) AppleWebKit/537.36"}

            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    metadata["status_code"] = response.status

                    # Only process successful responses
                    if response.status == 200:
                        # Check content type
                        content_type = response.headers.get("content-type", "").lower()
                        if "text/html" in content_type:
                            html = await response.text()
                            metadata.update(self._parse_html_metadata(html))
                        else:
                            # For non-HTML content, just use filename if available
                            if "/" in url:
                                filename = url.split("/")[-1]
                                if "." in filename:
                                    metadata["title"] = filename
                    else:
                        metadata["error"] = f"HTTP {response.status}"

        except TimeoutError:
            metadata["error"] = "Request timeout"
        except aiohttp.ClientError as e:
            metadata["error"] = f"Client error: {str(e)}"
        except Exception as e:
            metadata["error"] = f"Unknown error: {str(e)}"

        return metadata

    def _parse_html_metadata(self, html: str) -> dict[str, str | None]:
        """Parse HTML to extract title and description."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Extract title
            title = None
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text().strip()

            # Extract description from meta tags
            description = None

            # Try Open Graph description first
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                description = og_desc.get("content", "").strip()

            # Fall back to standard meta description
            if not description:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc:
                    description = meta_desc.get("content", "").strip()

            # Limit description length
            if description and len(description) > 300:
                description = description[:297] + "..."

            return {"title": title, "description": description}

        except Exception:
            return {"title": None, "description": None}

    async def store_link(
        self, url: str, chat_id: int, message_id: int | None = None, username: str | None = None
    ) -> dict[str, Any]:
        """Store a link with its metadata."""
        # Validate URL first
        if not self.validate_url(url):
            raise ValueError(f"Invalid URL: {url}")

        # Fetch metadata
        metadata = await self.fetch_url_metadata(url)

        # Create link entry
        link_entry = {
            "url": url,
            "timestamp": datetime.now().isoformat(),
            "chat_id": chat_id,
            "message_id": message_id,
            "username": username,
            "metadata": metadata,
        }

        # Load existing links and add new one
        links = self._load_links()
        links.append(link_entry)

        # Save updated links
        self._save_links(links)

        return link_entry

    def get_links(
        self, chat_id: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve stored links, optionally filtered by chat_id."""
        links = self._load_links()

        # Filter by chat_id if specified
        if chat_id is not None:
            links = [link for link in links if link.get("chat_id") == chat_id]

        # Sort by timestamp (newest first)
        links.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # Apply limit if specified
        if limit:
            links = links[:limit]

        return links

    def search_links(self, query: str, chat_id: int | None = None) -> list[dict[str, Any]]:
        """Search links by title, description, or URL."""
        links = self.get_links(chat_id)
        query_lower = query.lower()

        matching_links = []
        for link in links:
            # Search in URL
            if query_lower in link["url"].lower():
                matching_links.append(link)
                continue

            # Search in metadata
            metadata = link.get("metadata", {})
            title = metadata.get("title", "") or ""
            description = metadata.get("description", "") or ""

            if (
                query_lower in title.lower()
                or query_lower in description.lower()
                or query_lower in metadata.get("domain", "").lower()
            ):
                matching_links.append(link)

        return matching_links
