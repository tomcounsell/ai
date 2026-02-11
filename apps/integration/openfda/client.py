"""
OpenFDA API client for drug labels and interaction data.

OpenFDA provides access to FDA drug labeling, adverse events, and recalls.
License: CC0 1.0 (public domain) - commercial use allowed.

API Docs: https://open.fda.gov/apis/drug/
Rate Limit: 240 req/min, 120k/day (with free API key)
"""

import logging
import ssl
from typing import Any

import aiohttp
import certifi
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class OpenFDAClient:
    """
    Async client for OpenFDA Drug API.

    Used for:
    - Fetching drug labels with interaction warnings
    - Getting adverse event data
    - Product information lookup
    """

    BASE_URL = "https://api.fda.gov/drug"
    CACHE_TTL = 86400  # 24 hours

    def __init__(self, api_key: str | None = None):
        """
        Initialize OpenFDA client.

        Args:
            api_key: Optional API key for higher rate limits.
                     Falls back to OPENFDA_API_KEY setting.
        """
        self.api_key = api_key or getattr(settings, "OPENFDA_API_KEY", None)
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
        Make request to OpenFDA API with caching.

        Args:
            endpoint: API endpoint (e.g., '/label.json')
            params: Query parameters

        Returns:
            JSON response as dict
        """
        params = params or {}

        # Add API key if available
        if self.api_key:
            params["api_key"] = self.api_key

        # Build cache key
        cache_params = {k: v for k, v in params.items() if k != "api_key"}
        cache_key = f"openfda:{endpoint}:{hash(frozenset(cache_params.items()))}"

        # Check cache first
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug(f"OpenFDA cache hit: {endpoint}")
            return cached

        session = await self._get_session()
        url = f"{self.BASE_URL}{endpoint}"

        try:
            async with session.get(url, params=params) as response:
                if response.status == 404:
                    # No results - return empty
                    return {"results": []}

                response.raise_for_status()
                data = await response.json()

                # Cache successful responses
                cache.set(cache_key, data, self.CACHE_TTL)
                logger.debug(f"OpenFDA API call: {endpoint} -> cached")

                return data

        except aiohttp.ClientError as e:
            logger.error(f"OpenFDA API error: {endpoint} - {e}")
            raise

    async def get_drug_label(self, drug_name: str) -> dict | None:
        """
        Get drug label information by name.

        Args:
            drug_name: Drug name (brand or generic)

        Returns:
            Drug label data including interactions, or None if not found
        """
        # Search by brand name or generic name
        search = f'(openfda.brand_name:"{drug_name}") OR (openfda.generic_name:"{drug_name}")'
        params = {"search": search, "limit": 1}

        data = await self._make_request("/label.json", params=params)
        results = data.get("results", [])

        if not results:
            return None

        label = results[0]
        return self._parse_label(label)

    async def get_drug_label_by_rxcui(self, rxcui: str) -> dict | None:
        """
        Get drug label by RxCUI (links to RxNorm).

        Args:
            rxcui: RxNorm Concept Unique Identifier

        Returns:
            Drug label data or None if not found
        """
        params = {"search": f'openfda.rxcui:"{rxcui}"', "limit": 1}

        data = await self._make_request("/label.json", params=params)
        results = data.get("results", [])

        if not results:
            return None

        return self._parse_label(results[0])

    def _parse_label(self, label: dict) -> dict:
        """
        Parse drug label into structured format.

        Args:
            label: Raw label data from API

        Returns:
            Structured label with key fields extracted
        """
        openfda = label.get("openfda", {})

        return {
            "brand_name": openfda.get("brand_name", [None])[0],
            "generic_name": openfda.get("generic_name", [None])[0],
            "manufacturer": openfda.get("manufacturer_name", [None])[0],
            "product_type": openfda.get("product_type", [None])[0],
            "route": openfda.get("route", []),
            "rxcui": openfda.get("rxcui", []),
            "ndc": openfda.get("product_ndc", []),
            # Interaction and safety data
            "drug_interactions": label.get("drug_interactions", []),
            "warnings": label.get("warnings", []),
            "warnings_and_cautions": label.get("warnings_and_cautions", []),
            "contraindications": label.get("contraindications", []),
            "precautions": label.get("precautions", []),
            # Dosing info
            "dosage_and_administration": label.get("dosage_and_administration", []),
            "indications_and_usage": label.get("indications_and_usage", []),
            # Food interactions often in precautions or drug_interactions
            "food_interactions": self._extract_food_interactions(label),
        }

    def _extract_food_interactions(self, label: dict) -> list[str]:
        """
        Extract food-related interaction warnings from label text.

        Args:
            label: Raw label data

        Returns:
            List of food interaction warnings
        """
        food_keywords = [
            "food",
            "meal",
            "grapefruit",
            "dairy",
            "milk",
            "alcohol",
            "caffeine",
            "empty stomach",
            "with food",
            "high-fat",
        ]

        food_warnings = []

        # Check relevant sections
        sections = [
            label.get("drug_interactions", []),
            label.get("precautions", []),
            label.get("dosage_and_administration", []),
        ]

        for section in sections:
            if not section:
                continue
            for text in section:
                text_lower = text.lower()
                if any(keyword in text_lower for keyword in food_keywords):
                    # Extract relevant sentence(s)
                    sentences = text.split(".")
                    for sentence in sentences:
                        if any(kw in sentence.lower() for kw in food_keywords):
                            food_warnings.append(sentence.strip() + ".")

        return food_warnings

    async def get_adverse_events(self, drug_name: str, limit: int = 10) -> list[dict]:
        """
        Get adverse event reports for a drug.

        Args:
            drug_name: Drug name
            limit: Max number of events to return

        Returns:
            List of adverse event summaries
        """
        params = {
            "search": f'patient.drug.medicinalproduct:"{drug_name}"',
            "limit": limit,
        }

        data = await self._make_request("/event.json", params=params)

        events = []
        for result in data.get("results", []):
            patient = result.get("patient", {})
            reactions = patient.get("reaction", [])

            events.append(
                {
                    "serious": result.get("serious"),
                    "reactions": [r.get("reactionmeddrapt") for r in reactions],
                    "outcome": patient.get("patientdeath"),
                    "drugs_involved": [
                        d.get("medicinalproduct") for d in patient.get("drug", [])
                    ],
                }
            )

        return events

    async def search_drugs(self, query: str, limit: int = 10) -> list[dict]:
        """
        Search for drugs by name.

        Args:
            query: Search term
            limit: Max results

        Returns:
            List of matching drug summaries
        """
        params = {
            "search": f'openfda.brand_name:"{query}" OR openfda.generic_name:"{query}"',
            "limit": limit,
        }

        data = await self._make_request("/label.json", params=params)

        results = []
        for label in data.get("results", []):
            openfda = label.get("openfda", {})
            results.append(
                {
                    "brand_name": openfda.get("brand_name", [None])[0],
                    "generic_name": openfda.get("generic_name", [None])[0],
                    "manufacturer": openfda.get("manufacturer_name", [None])[0],
                    "rxcui": openfda.get("rxcui", []),
                }
            )

        return results
