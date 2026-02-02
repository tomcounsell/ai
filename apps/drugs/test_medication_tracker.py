"""
Tests for medication tracker MVP.

Covers models, services, and basic view functionality.
"""

from datetime import time

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.drugs.models import Medication, UserMealSchedule, UserMedication
from apps.drugs.services.interactions import InteractionChecker
from apps.drugs.services.scheduler import MedicationScheduler

User = get_user_model()


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(
        username="testuser", email="test@example.com", password="testpass123"
    )


@pytest.fixture
def warfarin(db):
    """Create Warfarin medication with interaction data."""
    return Medication.objects.create(
        name="Warfarin",
        generic_name="warfarin",
        medication_type="drug",
        food_timing="anytime",
        known_interactions={
            "medication_ids": [],  # Will be populated by test
            "warnings": [],
        },
    )


@pytest.fixture
def aspirin(db):
    """Create Aspirin medication."""
    return Medication.objects.create(
        name="Aspirin",
        generic_name="acetylsalicylic acid",
        medication_type="otc",
        food_timing="with_food",
    )


@pytest.fixture
def levothyroxine(db):
    """Create Levothyroxine medication."""
    return Medication.objects.create(
        name="Levothyroxine",
        generic_name="levothyroxine",
        medication_type="drug",
        food_timing="empty_stomach",
    )


# --- MODEL TESTS ---


@pytest.mark.django_db
class TestMedicationModel:
    """Tests for Medication model."""

    def test_medication_creation(self):
        """Test creating a medication."""
        med = Medication.objects.create(
            name="Test Med", medication_type="drug", food_timing="with_food"
        )
        assert med.name == "Test Med"
        assert str(med) == "Test Med"

    def test_medication_with_generic_name(self):
        """Test medication string representation with generic name."""
        med = Medication.objects.create(
            name="Advil", generic_name="ibuprofen", medication_type="otc"
        )
        assert str(med) == "Advil (ibuprofen)"

    def test_get_interactions_with(self, warfarin, aspirin):
        """Test interaction checking method."""
        # Set up interaction
        warfarin.known_interactions = {
            "medication_ids": [aspirin.id],
            "warnings": ["Warfarin + Aspirin: Bleeding risk"],
        }
        warfarin.save()

        warnings = warfarin.get_interactions_with([aspirin.id])
        assert len(warnings) == 1
        assert "Bleeding risk" in warnings[0]

    def test_no_interactions(self, warfarin, levothyroxine):
        """Test when medications don't interact."""
        warnings = warfarin.get_interactions_with([levothyroxine.id])
        assert len(warnings) == 0


@pytest.mark.django_db
class TestUserMedicationModel:
    """Tests for UserMedication model."""

    def test_user_medication_creation(self, user, aspirin):
        """Test creating a user medication."""
        user_med = UserMedication.objects.create(
            user=user,
            medication=aspirin,
            dosage="81mg",
            frequency="once daily",
            time_preference="morning",
        )
        assert user_med.user == user
        assert user_med.medication == aspirin
        assert user_med.is_active is True

    def test_user_medication_string(self, user, aspirin):
        """Test string representation."""
        user_med = UserMedication.objects.create(
            user=user, medication=aspirin, dosage="81mg", frequency="once daily"
        )
        str_repr = str(user_med)
        assert user.email in str_repr
        assert "Aspirin" in str_repr


@pytest.mark.django_db
class TestUserMealScheduleModel:
    """Tests for UserMealSchedule model."""

    def test_meal_schedule_creation(self, user):
        """Test creating meal schedule."""
        schedule = UserMealSchedule.objects.create(
            user=user,
            breakfast_time=time(8, 0),
            lunch_time=time(12, 0),
            dinner_time=time(18, 0),
        )
        assert schedule.breakfast_time == time(8, 0)

    def test_get_meal_times(self, user):
        """Test get_meal_times method."""
        schedule = UserMealSchedule.objects.create(
            user=user,
            breakfast_time=time(8, 0),
            lunch_time=None,  # Not set
            dinner_time=time(18, 0),
        )
        meals = schedule.get_meal_times()
        assert "breakfast" in meals
        assert "lunch" not in meals
        assert "dinner" in meals


# --- SERVICE TESTS ---


@pytest.mark.django_db
class TestInteractionChecker:
    """Tests for InteractionChecker service."""

    def test_check_interactions_finds_conflicts(self, user, warfarin, aspirin):
        """Test that interaction checker finds known interactions."""
        # Set up interaction
        warfarin.known_interactions = {
            "medication_ids": [aspirin.id],
            "warnings": ["Warfarin + Aspirin: Bleeding risk"],
        }
        warfarin.save()

        # Create user medications
        user_med_1 = UserMedication.objects.create(
            user=user, medication=warfarin, dosage="5mg", frequency="once daily"
        )
        user_med_2 = UserMedication.objects.create(
            user=user, medication=aspirin, dosage="81mg", frequency="once daily"
        )

        interactions = InteractionChecker.check_user_interactions(
            [user_med_1, user_med_2]
        )
        assert len(interactions) == 1
        assert "Bleeding risk" in interactions[0]["warning"]

    def test_no_interactions_found(self, user, levothyroxine, aspirin):
        """Test when no interactions exist."""
        user_med_1 = UserMedication.objects.create(
            user=user, medication=levothyroxine, dosage="50mcg", frequency="once daily"
        )
        user_med_2 = UserMedication.objects.create(
            user=user, medication=aspirin, dosage="81mg", frequency="once daily"
        )

        interactions = InteractionChecker.check_user_interactions(
            [user_med_1, user_med_2]
        )
        assert len(interactions) == 0


@pytest.mark.django_db
class TestMedicationScheduler:
    """Tests for MedicationScheduler service."""

    def test_generate_daily_schedule(self, user, aspirin, levothyroxine):
        """Test schedule generation."""
        # Create user medications
        user_med_morning = UserMedication.objects.create(
            user=user,
            medication=aspirin,
            dosage="81mg",
            frequency="once daily",
            time_preference="morning",
        )
        user_med_empty = UserMedication.objects.create(
            user=user,
            medication=levothyroxine,
            dosage="50mcg",
            frequency="once daily",
            time_preference="morning",
        )

        schedule = MedicationScheduler.generate_daily_schedule(
            [user_med_morning, user_med_empty]
        )

        # Should have meds in morning categories
        assert len(schedule["morning_with_breakfast"]) > 0
        assert len(schedule["morning_before_breakfast"]) > 0

    def test_schedule_with_meal_times(self, user, aspirin):
        """Test schedule generation with custom meal times."""
        meal_schedule = UserMealSchedule.objects.create(
            user=user,
            breakfast_time=time(7, 30),
            lunch_time=time(12, 30),
            dinner_time=time(19, 0),
        )

        user_med = UserMedication.objects.create(
            user=user,
            medication=aspirin,
            dosage="81mg",
            frequency="once daily",
            time_preference="morning",
        )

        schedule = MedicationScheduler.generate_daily_schedule(
            [user_med], meal_schedule
        )
        summary = MedicationScheduler.get_schedule_summary(schedule)

        # Should have at least one scheduled item
        assert len(summary) > 0
        assert summary[0]["time"] is not None


# --- VIEW TESTS ---


@pytest.mark.django_db
class TestMedicationViews:
    """Tests for medication tracker views."""

    def test_dashboard_requires_login(self, client):
        """Test that dashboard redirects unauthenticated users."""
        url = reverse("drugs:dashboard")
        response = client.get(url)
        assert response.status_code == 302  # Redirect to login

    def test_dashboard_loads_for_authenticated_user(self, client, user):
        """Test dashboard loads for logged-in user."""
        client.force_login(user)
        url = reverse("drugs:dashboard")
        response = client.get(url)
        assert response.status_code == 200

    def test_medication_form_get(self, client, user):
        """Test loading medication form."""
        client.force_login(user)
        url = reverse("drugs:medication_add")
        response = client.get(url)
        assert response.status_code == 200
        assert (
            b"Add Medication" in response.content or b"medication" in response.content
        )

    def test_medication_form_post(self, client, user, aspirin):
        """Test creating a medication via form."""
        client.force_login(user)
        url = reverse("drugs:medication_add")
        data = {
            "medication": aspirin.id,
            "dosage": "81mg",
            "frequency": "once daily",
            "time_preference": "morning",
            "notes": "Test note",
        }
        response = client.post(url, data)
        assert response.status_code == 204  # Success, no content

        # Verify medication was created
        assert UserMedication.objects.filter(user=user, medication=aspirin).exists()

    def test_meal_schedule_form_post(self, client, user):
        """Test saving meal schedule."""
        client.force_login(user)
        url = reverse("drugs:meal_schedule")
        data = {
            "breakfast_time": "08:00",
            "lunch_time": "12:30",
            "dinner_time": "18:00",
        }
        response = client.post(url, data)
        assert response.status_code == 204

        # Verify schedule was created
        schedule = UserMealSchedule.objects.get(user=user)
        assert schedule.breakfast_time == time(8, 0)
