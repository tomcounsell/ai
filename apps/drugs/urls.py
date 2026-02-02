"""
URL configuration for medication tracker.
"""

from django.urls import path

from apps.drugs import views

app_name = "drugs"

urlpatterns = [
    # Main dashboard
    path("", views.MedicationDashboardView.as_view(), name="dashboard"),
    # HTMX partials for modals
    path("medication/add/", views.MedicationFormView.as_view(), name="medication_add"),
    path(
        "medication/<int:pk>/edit/",
        views.MedicationFormView.as_view(),
        name="medication_edit",
    ),
    path(
        "medication/<int:pk>/delete/",
        views.MedicationDeleteView.as_view(),
        name="medication_delete",
    ),
    # Meal schedule
    path("schedule/", views.MealScheduleFormView.as_view(), name="meal_schedule"),
]
