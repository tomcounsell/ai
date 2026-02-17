"""
Interaction checking service for medications.

Checks for drug-drug interactions based on stored interaction data.
For MVP, uses hardcoded JSON data from medication records.
"""

from apps.drugs.models import Medication, UserMedication


class InteractionChecker:
    """
    Service to check for drug interactions across a user's medication list.
    """

    @staticmethod
    def check_user_interactions(user_medications: list[UserMedication]) -> list[dict]:
        """
        Check for interactions among a list of user medications.

        Args:
            user_medications: QuerySet or list of UserMedication objects

        Returns:
            List of interaction warnings, each containing:
            {
                'medication_a': Medication object,
                'medication_b': Medication object,
                'warning': str (warning text),
                'severity': str (for future use, currently all are 'moderate')
            }
        """
        if not user_medications:
            return []

        interactions = []
        medication_ids = [um.medication.id for um in user_medications]

        # Check each medication against all others
        for user_med in user_medications:
            med = user_med.medication
            warnings = med.get_interactions_with(medication_ids)

            if warnings:
                # Find which specific medications triggered these warnings
                interacting_med_ids = set(
                    med.known_interactions.get("medication_ids", [])
                )
                user_med_ids = set(medication_ids)
                matching_ids = interacting_med_ids.intersection(user_med_ids)

                # Get the actual Medication objects for display
                interacting_meds = Medication.objects.filter(id__in=matching_ids)

                for warning_text in warnings:
                    # Parse medication names from warning text to match with correct med
                    # For MVP, we just associate with first matching med
                    for other_med in interacting_meds:
                        if other_med.id != med.id:  # Don't self-reference
                            interactions.append(
                                {
                                    "medication_a": med,
                                    "medication_b": other_med,
                                    "warning": warning_text,
                                    "severity": "moderate",  # MVP: all same severity
                                }
                            )
                            break  # One warning per pair

        # Deduplicate (A-B and B-A are the same interaction)
        unique_interactions = []
        seen_pairs = set()

        for interaction in interactions:
            med_a_id = interaction["medication_a"].id
            med_b_id = interaction["medication_b"].id
            pair = tuple(sorted([med_a_id, med_b_id]))

            if pair not in seen_pairs:
                seen_pairs.add(pair)
                unique_interactions.append(interaction)

        return unique_interactions
