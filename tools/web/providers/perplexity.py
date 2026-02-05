"""Perplexity search provider."""

import os

import httpx

from tools.web.types import SearchResult, Source

name = "perplexity"

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"


async def search(query: str, **kwargs) -> SearchResult | None:
    """Search using Perplexity API.

    Args:
        query: Search query
        **kwargs: Additional parameters (search_type, max_results, time_filter, domain_filter)

    Returns:
        SearchResult on success, None on failure
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return None

    if not query or not query.strip():
        return None

    # Extract kwargs
    search_type = kwargs.get("search_type", "conversational")
    max_results = max(1, min(50, kwargs.get("max_results", 10)))
    time_filter = kwargs.get("time_filter")
    domain_filter = kwargs.get("domain_filter")

    # Build system prompt based on search type
    system_prompts = {
        "conversational": "Be precise and concise. Provide a helpful summary of the search results.",
        "factual": "Provide factual, verifiable information. Be precise and cite specific data points.",
        "citations": "Always cite your sources. Include URLs for verification. Format citations clearly.",
    }
    system_prompt = system_prompts.get(search_type, system_prompts["conversational"])

    # Add domain filter to query if specified
    search_query = query
    if domain_filter:
        domain_str = " OR ".join(f"site:{d}" for d in domain_filter)
        search_query = f"{query} ({domain_str})"

    # Add time filter context
    if time_filter:
        time_contexts = {
            "day": "Focus on results from the last 24 hours.",
            "week": "Focus on results from the last week.",
            "month": "Focus on results from the last month.",
            "year": "Focus on results from the last year.",
        }
        if time_filter in time_contexts:
            system_prompt += f" {time_contexts[time_filter]}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                PERPLEXITY_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": search_query},
                    ],
                    "max_tokens": 1024,
                    "return_citations": True,
                    "return_related_questions": True,
                },
            )

            response.raise_for_status()
            result = response.json()

            if "choices" not in result or len(result["choices"]) == 0:
                return None

            message = result["choices"][0].get("message", {})
            content = message.get("content", "")

            if not content:
                return None

            # Extract citations
            citations = result.get("citations", [])
            sources = []
            for citation in citations[:max_results]:
                sources.append(
                    Source(
                        url=citation,
                        title=citation.split("/")[-1] or citation,
                        snippet=None,
                    )
                )

            return SearchResult(
                answer=content,
                sources=sources,
                citations=citations,
                query=query,
                provider=name,
            )

    except Exception:
        # Any error returns None to trigger fallback
        return None
