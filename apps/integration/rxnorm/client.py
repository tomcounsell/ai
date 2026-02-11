"""
RxNorm API client for drug identification and normalization.

RxNorm is a normalized naming system for medications from the
U.S. National Library of Medicine. Free for commercial use.

API Docs: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html
Rate Limit: 20 requests/second per IP
Attribution Required: Yes (see NLM terms)
"""

import logging
import ssl
from typing import Any

import aiohttp
import certifi
from django.core.cache import cache

logger = logging.getLogger(__name__)


class RxNormClient:
    """
    Async client for RxNorm REST API.

    Used for:
    - Normalizing user drug input to standard names
    - Getting RxCUI identifiers for cross-referencing with other APIs
    - Drug name autocomplete/spelling suggestions
    - Mapping brand names to generic equivalents
    """

    BASE_URL = "https://rxnav.nlm.nih.gov/REST"
    CACHE_TTL = 86400  # 24 hours (recommended by NLM)

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            # Create SSL context with certifi certificates
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _make_request(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """
        Make request to RxNorm API with caching.

        Args:
            endpoint: API endpoint path (e.g., '/drugs')
            params: Query parameters

        Returns:
            JSON response as dict
        """
        # Build cache key from endpoint and params
        cache_key = f"rxnorm:{endpoint}:{hash(frozenset((params or {}).items()))}"

        # Check cache first
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug(f"RxNorm cache hit: {endpoint}")
            return cached

        session = await self._get_session()
        url = f"{self.BASE_URL}{endpoint}.json"

        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                # Cache successful responses
                cache.set(cache_key, data, self.CACHE_TTL)
                logger.debug(f"RxNorm API call: {endpoint} -> cached")

                return data

        except aiohttp.ClientError as e:
            logger.error(f"RxNorm API error: {endpoint} - {e}")
            raise

    async def search_drugs(self, name: str) -> list[dict]:
        """
        Search for drugs by name.

        Args:
            name: Drug name to search (brand or generic)

        Returns:
            List of matching drugs with RxCUI and name info
        """
        data = await self._make_request("/drugs", params={"name": name})

        results = []
        drug_group = data.get("drugGroup", {})

        for concept_group in drug_group.get("conceptGroup", []):
            for concept in concept_group.get("conceptProperties", []):
                results.append(
                    {
                        "rxcui": concept.get("rxcui"),
                        "name": concept.get("name"),
                        "synonym": concept.get("synonym"),
                        "tty": concept.get("tty"),  # Term type (e.g., SBD, SCD, BN)
                    }
                )

        return results

    async def get_spelling_suggestions(self, term: str) -> list[str]:
        """
        Get spelling suggestions for drug name autocomplete.

        Args:
            term: Partial or misspelled drug name

        Returns:
            List of suggested drug names
        """
        data = await self._make_request("/spellingsuggestions", params={"name": term})

        suggestion_group = data.get("suggestionGroup", {})
        suggestion_list = suggestion_group.get("suggestionList", {})
        return suggestion_list.get("suggestion", [])

    async def get_rxcui(self, name: str) -> str | None:
        """
        Get the RxCUI identifier for a drug name.

        Args:
            name: Exact drug name

        Returns:
            RxCUI string or None if not found
        """
        data = await self._make_request("/rxcui", params={"name": name})

        id_group = data.get("idGroup", {})
        rxnorm_ids = id_group.get("rxnormId", [])

        return rxnorm_ids[0] if rxnorm_ids else None

    async def get_drug_properties(self, rxcui: str) -> dict | None:
        """
        Get properties for a drug by RxCUI.

        Args:
            rxcui: RxNorm Concept Unique Identifier

        Returns:
            Dict with drug properties or None if not found
        """
        data = await self._make_request(f"/rxcui/{rxcui}/properties")

        properties = data.get("properties")
        if not properties:
            return None

        return {
            "rxcui": properties.get("rxcui"),
            "name": properties.get("name"),
            "synonym": properties.get("synonym"),
            "tty": properties.get("tty"),
            "language": properties.get("language"),
            "suppress": properties.get("suppress"),
            "umlscui": properties.get("umlscui"),
        }

    async def get_related_drugs(
        self, rxcui: str, relation_types: list[str] | None = None
    ) -> list[dict]:
        """
        Get related drugs (e.g., brand to generic mapping).

        Args:
            rxcui: RxNorm Concept Unique Identifier
            relation_types: Filter by relation types (e.g., ['tradename_of', 'has_tradename'])

        Returns:
            List of related drug concepts
        """
        params = {}
        if relation_types:
            params["rela"] = "+".join(relation_types)

        data = await self._make_request(f"/rxcui/{rxcui}/related", params=params)

        results = []
        for group in data.get("relatedGroup", {}).get("conceptGroup", []):
            for concept in group.get("conceptProperties", []):
                results.append(
                    {
                        "rxcui": concept.get("rxcui"),
                        "name": concept.get("name"),
                        "tty": concept.get("tty"),
                    }
                )

        return results

    async def get_drug_interactions(self, rxcui: str) -> list[dict]:
        """
        Get known drug interactions for an RxCUI.

        NOTE: The NLM Drug Interaction API was discontinued in January 2024.
        This method now returns an empty list. Use DailyMed/OpenFDA instead.

        Args:
            rxcui: RxNorm Concept Unique Identifier

        Returns:
            Empty list (API discontinued)
        """
        logger.warning(
            "RxNorm Drug Interaction API discontinued Jan 2024. "
            "Use DailyMed or OpenFDA for interaction data."
        )
        return []

    async def normalize_drug_name(self, user_input: str) -> dict | None:
        """
        Normalize a user's drug input to standard form.

        Attempts exact match first, then fuzzy search.

        Args:
            user_input: User-entered drug name (may be misspelled or abbreviated)

        Returns:
            Normalized drug info with rxcui, or None if no match
        """
        # Try exact match first
        rxcui = await self.get_rxcui(user_input)
        if rxcui:
            return await self.get_drug_properties(rxcui)

        # Try search
        results = await self.search_drugs(user_input)
        if results:
            return results[0]

        # Try spelling suggestions
        suggestions = await self.get_spelling_suggestions(user_input)
        if suggestions:
            # Try first suggestion
            rxcui = await self.get_rxcui(suggestions[0])
            if rxcui:
                return await self.get_drug_properties(rxcui)

        return None
