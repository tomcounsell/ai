"""Types and interfaces for web search and fetch tools."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Source:
    """A source from a web search result."""

    url: str
    title: str | None
    snippet: str | None


@dataclass
class SearchResult:
    """Result from a web search operation."""

    answer: str  # AI-generated summary/answer
    sources: list[Source]  # URLs with titles and snippets
    citations: list[str]  # Direct citation URLs
    query: str  # Original query
    provider: str  # Which provider answered


@dataclass
class FetchResult:
    """Result from fetching a URL."""

    content: str  # Clean markdown content
    title: str | None  # Page title
    url: str  # Final URL (after redirects)
    provider: str  # Which provider fetched


class SearchProvider(Protocol):
    """Protocol for web search providers."""

    name: str

    async def search(self, query: str, **kwargs) -> SearchResult | None:
        """Search for a query.

        Returns SearchResult on success, None on failure (triggers fallback).
        """
        ...


class FetchProvider(Protocol):
    """Protocol for URL fetch providers."""

    name: str

    async def fetch(self, url: str, **kwargs) -> FetchResult | None:
        """Fetch content from a URL.

        Returns FetchResult on success, None on failure (triggers fallback).
        """
        ...
