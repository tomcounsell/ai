from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.drugs.models import Medication, UserMedication, UserMealSchedule


@admin.register(Medication)
class MedicationAdmin(ModelAdmin):
    list_display = ["name", "generic_name", "medication_type", "food_timing"]
    list_filter = ["medication_type", "food_timing"]
    search_fields = ["name", "generic_name"]
    ordering = ["name"]


@admin.register(UserMedication)
class UserMedicationAdmin(ModelAdmin):
    list_display = [
        "user",
        "medication",
        "dosage",
        "frequency",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "time_preference", "created_at"]
    search_fields = ["user__email", "medication__name", "notes"]
    raw_id_fields = ["user", "medication"]
    ordering = ["-created_at"]


@admin.register(UserMealSchedule)
class UserMealScheduleAdmin(ModelAdmin):
    list_display = ["user", "breakfast_time", "lunch_time", "dinner_time"]
    search_fields = ["user__email"]
    raw_id_fields = ["user"]
