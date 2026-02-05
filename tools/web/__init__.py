"""Unified web search and fetch tools."""

from tools.web.fetch import fetch, fetch_sync
from tools.web.search import web_search, web_search_sync

# Public API exports
__all__ = ["web_search", "web_search_sync", "fetch", "fetch_sync"]
