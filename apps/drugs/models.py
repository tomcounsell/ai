from django.conf import settings
from django.db import models

from apps.common.behaviors.authorable import Authorable
from apps.common.behaviors.timestampable import Timestampable


class Medication(models.Model):
    """
    Shared medication catalog.

    Each medication represents a drug, supplement, or OTC medicine that can be
    added to users' personal medication lists. Interaction data is stored as JSON
    for MVP simplicity.
    """

    MEDICATION_TYPES = [
        ("drug", "Prescription Drug"),
        ("otc", "OTC Medicine"),
        ("supplement", "Supplement/Vitamin"),
    ]

    FOOD_TIMING_CHOICES = [
        ("with_food", "Take with food"),
        ("empty_stomach", "Take on empty stomach"),
        ("anytime", "Any time"),
    ]

    name = models.CharField(
        max_length=200, help_text="Brand or common name of the medication"
    )
    generic_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Generic/scientific name if different from brand name",
    )
    medication_type = models.CharField(
        max_length=20, choices=MEDICATION_TYPES, default="drug"
    )

    # Interaction data stored as JSON for MVP
    # Format: {"medication_ids": [1, 2, 3], "warnings": ["text warning"]}
    known_interactions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Known drug-drug interactions (medication IDs and warnings)",
    )

    food_timing = models.CharField(
        max_length=20,
        choices=FOOD_TIMING_CHOICES,
        default="anytime",
        help_text="When this medication should be taken relative to meals",
    )

    # Common dosage information (optional, for autocomplete suggestions)
    common_dosages = models.JSONField(
        default=list,
        blank=True,
        help_text="Common dosage forms and strengths (e.g., ['10mg tablet', '20mg tablet'])",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["medication_type"]),
        ]

    def __str__(self):
        if self.generic_name and self.generic_name != self.name:
            return f"{self.name} ({self.generic_name})"
        return self.name

    def get_interactions_with(self, medication_ids):
        """
        Check if this medication interacts with any of the given medication IDs.

        Args:
            medication_ids: List of Medication IDs to check against

        Returns:
            List of warning strings for any matching interactions
        """
        if not self.known_interactions:
            return []

        interacting_ids = set(self.known_interactions.get("medication_ids", []))
        matching_ids = interacting_ids.intersection(set(medication_ids))

        if matching_ids:
            return self.known_interactions.get("warnings", [])

        return []


class UserMedication(Timestampable, Authorable):
    """
    User's personal medication list.

    Represents a medication that a specific user is taking, including their
    dosage, frequency, and personal notes.
    """

    TIME_PREFERENCES = [
        ("morning", "Morning"),
        ("evening", "Evening"),
        ("anytime", "Any time"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="medications"
    )
    medication = models.ForeignKey(
        Medication, on_delete=models.CASCADE, related_name="user_medications"
    )

    dosage = models.CharField(
        max_length=100, help_text="e.g., '10mg', '2 tablets', '1 capsule'"
    )
    frequency = models.CharField(
        max_length=100, help_text="e.g., 'once daily', 'twice daily', 'as needed'"
    )
    time_preference = models.CharField(
        max_length=20,
        choices=TIME_PREFERENCES,
        default="anytime",
        help_text="Preferred time of day to take this medication",
    )

    is_active = models.BooleanField(
        default=True, help_text="Whether user is currently taking this medication"
    )
    notes = models.TextField(
        blank=True,
        help_text="Personal notes about this medication (why taking it, prescribing doctor, etc.)",
    )

    class Meta:
        ordering = ["-is_active", "medication__name"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
        ]
        # User can have the same medication multiple times (different dosages/schedules)
        # but we'll display a warning in the UI if they do

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.user.email}: {self.medication.name} {self.dosage} ({status})"


class UserMealSchedule(Timestampable, Authorable):
    """
    User's typical meal times.

    Stores when a user typically eats breakfast, lunch, and dinner to help
    generate optimal medication timing recommendations.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meal_schedule"
    )

    breakfast_time = models.TimeField(
        null=True, blank=True, help_text="Typical breakfast time"
    )
    lunch_time = models.TimeField(null=True, blank=True, help_text="Typical lunch time")
    dinner_time = models.TimeField(
        null=True, blank=True, help_text="Typical dinner time"
    )

    class Meta:
        verbose_name = "User Meal Schedule"
        verbose_name_plural = "User Meal Schedules"

    def __str__(self):
        return f"{self.user.email}'s meal schedule"

    def get_meal_times(self):
        """
        Return a dict of defined meal times.

        Returns:
            dict: {meal_name: time} for non-null meal times
        """
        meals = {}
        if self.breakfast_time:
            meals["breakfast"] = self.breakfast_time
        if self.lunch_time:
            meals["lunch"] = self.lunch_time
        if self.dinner_time:
            meals["dinner"] = self.dinner_time
        return meals
