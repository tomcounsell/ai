"""
Drug lookup service for lazy loading medication data from external APIs.

This service implements the lazy population strategy:
1. Check if we have the drug in our database
2. If not, fetch from external APIs (RxNorm, OpenFDA)
3. Cache the data for future lookups
4. Fall back to AI for data not available from APIs
"""

import contextlib
import logging

from asgiref.sync import async_to_sync

from apps.drugs.models import Medication
from apps.integration.openfda import OpenFDAClient
from apps.integration.rxnorm import RxNormClient

logger = logging.getLogger(__name__)


class DrugLookupService:
    """
    Service to look up and populate drug information from external sources.

    Implements lazy loading: only fetches data when a drug is first encountered.
    """

    def __init__(self):
        self.rxnorm_client = RxNormClient()
        self.openfda_client = OpenFDAClient()

    async def close(self):
        """Close all API client sessions."""
        await self.rxnorm_client.close()
        await self.openfda_client.close()

    async def lookup_drug(self, drug_name: str) -> Medication | None:
        """
        Look up a drug by name, creating a new Medication record if needed.

        Flow:
        1. Check if drug exists in database
        2. If not, query RxNorm for normalization and RxCUI
        3. Query OpenFDA for label data and interactions
        4. Create Medication record with gathered data

        Args:
            drug_name: User-entered drug name

        Returns:
            Medication object (existing or newly created)
        """
        # 1. Check database first
        existing = await self._find_existing_medication(drug_name)
        if existing:
            logger.debug(f"Found existing medication: {drug_name}")
            return existing

        # 2. Normalize via RxNorm
        logger.info(f"Looking up new drug: {drug_name}")
        rxnorm_data = await self.rxnorm_client.normalize_drug_name(drug_name)

        if not rxnorm_data:
            logger.warning(f"Drug not found in RxNorm: {drug_name}")
            # Could fall back to AI here in the future
            return None

        rxcui = rxnorm_data.get("rxcui")
        normalized_name = rxnorm_data.get("name", drug_name)

        # 3. Fetch label data from OpenFDA
        label_data = None
        if rxcui:
            label_data = await self.openfda_client.get_drug_label_by_rxcui(rxcui)
        if not label_data:
            label_data = await self.openfda_client.get_drug_label(normalized_name)

        # 4. Create medication record
        medication = await self._create_medication(
            name=drug_name,
            rxnorm_data=rxnorm_data,
            label_data=label_data,
        )

        return medication

    async def _find_existing_medication(self, drug_name: str) -> Medication | None:
        """
        Check if medication exists in database (case-insensitive).

        Args:
            drug_name: Name to search for

        Returns:
            Medication if found, None otherwise
        """
        # Try exact match first
        with contextlib.suppress(Medication.DoesNotExist):
            return await Medication.objects.aget(name__iexact=drug_name)

        # Try generic name match
        with contextlib.suppress(Medication.DoesNotExist):
            return await Medication.objects.aget(generic_name__iexact=drug_name)

        return None

    async def _create_medication(
        self,
        name: str,
        rxnorm_data: dict,
        label_data: dict | None,
    ) -> Medication:
        """
        Create a Medication record from API data.

        Args:
            name: Original user-entered name
            rxnorm_data: Data from RxNorm API
            label_data: Data from OpenFDA API (may be None)

        Returns:
            Created Medication object
        """
        # Determine medication type
        medication_type = "drug"  # Default to prescription
        if label_data:
            product_type = label_data.get("product_type", "").lower()
            if "otc" in product_type or "human otc" in product_type:
                medication_type = "otc"

        # Determine food timing from label
        food_timing = "anytime"  # Default
        if label_data:
            food_warnings = label_data.get("food_interactions", [])
            dosage_info = label_data.get("dosage_and_administration", [])

            # Check for food requirements
            all_text = " ".join(food_warnings + dosage_info).lower()
            if "empty stomach" in all_text or "before meals" in all_text:
                food_timing = "empty_stomach"
            elif "with food" in all_text or "with meals" in all_text:
                food_timing = "with_food"

        # Build external IDs
        external_ids = {}
        if rxnorm_data.get("rxcui"):
            external_ids["rxcui"] = rxnorm_data["rxcui"]
        if label_data:
            if label_data.get("rxcui"):
                external_ids["rxcui_list"] = label_data["rxcui"]
            if label_data.get("ndc"):
                external_ids["ndc"] = label_data["ndc"]

        # Extract interaction data
        known_interactions = {"medication_ids": [], "warnings": [], "sources": []}
        if label_data:
            drug_interactions = label_data.get("drug_interactions", [])
            if drug_interactions:
                known_interactions["warnings"] = drug_interactions[:5]  # Limit to 5
                known_interactions["sources"].append("openfda")

        # Use brand name from label if available, otherwise RxNorm name
        # Prefer simpler/shorter names - use RxNorm name over long product names
        display_name = rxnorm_data.get("name", name)
        generic_name = rxnorm_data.get("name", "")

        if label_data:
            label_brand = label_data.get("brand_name", "")
            label_generic = label_data.get("generic_name", "")

            # Use label generic if simpler (shorter) than current
            if label_generic and len(label_generic) < len(generic_name):
                generic_name = label_generic

            # Only use label brand if it's a reasonable length
            if label_brand and len(label_brand) <= 100:
                display_name = label_brand

        # Truncate to model field limits
        display_name = display_name[:200]
        generic_name = generic_name[:200]

        medication = Medication(
            name=display_name,
            generic_name=generic_name,
            medication_type=medication_type,
            food_timing=food_timing,
            known_interactions=known_interactions,
            common_dosages=[],  # Could parse from label in future
        )

        await medication.asave()
        logger.info(
            f"Created new medication: {medication.name} (rxcui: {external_ids.get('rxcui')})"
        )

        return medication

    def lookup_drug_sync(self, drug_name: str) -> Medication | None:
        """
        Synchronous wrapper for lookup_drug.

        Use this from Django views that don't support async.

        Args:
            drug_name: User-entered drug name

        Returns:
            Medication object or None
        """
        return async_to_sync(self.lookup_drug)(drug_name)


# Convenience function for simple lookups
def get_or_create_medication(drug_name: str) -> Medication | None:
    """
    Look up a drug, fetching from APIs if not in database.

    Args:
        drug_name: Drug name to look up

    Returns:
        Medication object or None if not found
    """
    service = DrugLookupService()
    try:
        return service.lookup_drug_sync(drug_name)
    finally:
        async_to_sync(service.close)()
