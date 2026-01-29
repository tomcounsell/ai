"""
Views for medication tracker.

Single-page dashboard with HTMX modals for add/edit operations.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.http import HttpResponse
from django.template.response import TemplateResponse

from apps.public.views.helpers.main_content_view import MainContentView
from apps.drugs.models import Medication, UserMedication, UserMealSchedule
from apps.drugs.services.interactions import InteractionChecker
from apps.drugs.services.scheduler import MedicationScheduler


class MedicationDashboardView(LoginRequiredMixin, MainContentView):
    """
    Main single-page dashboard for medication tracking.

    Displays:
    - User's medication list
    - Interaction warnings
    - Suggested daily schedule
    - Meal schedule configuration
    """

    template_name = "drugs/dashboard.html"
    url = "/drugs/"

    def get(self, request, *args, **kwargs):
        # Get user's active medications
        user_medications = UserMedication.objects.filter(
            user=request.user,
            is_active=True
        ).select_related('medication').order_by('medication__name')

        # Check for interactions
        interactions = InteractionChecker.check_user_interactions(user_medications)

        # Get meal schedule
        try:
            meal_schedule = UserMealSchedule.objects.get(user=request.user)
        except UserMealSchedule.DoesNotExist:
            meal_schedule = None

        # Generate daily schedule
        schedule = MedicationScheduler.generate_daily_schedule(
            user_medications,
            meal_schedule
        )
        schedule_summary = MedicationScheduler.get_schedule_summary(schedule)

        context = {
            'user_medications': user_medications,
            'interactions': interactions,
            'daily_schedule': schedule_summary,
            'meal_schedule': meal_schedule,
            'has_medications': user_medications.exists(),
        }

        return self.render(request, context=context)


class MedicationFormView(LoginRequiredMixin, View):
    """
    HTMX view for medication add/edit form modal.
    """

    def get(self, request, pk=None):
        """Return form for add or edit."""
        if pk:
            user_med = get_object_or_404(UserMedication, pk=pk, user=request.user)
            mode = 'edit'
        else:
            user_med = None
            mode = 'add'

        # Get all medications for dropdown
        medications = Medication.objects.all().order_by('name')

        context = {
            'user_medication': user_med,
            'medications': medications,
            'mode': mode,
        }
        return TemplateResponse(request, 'drugs/partials/medication_form.html', context)

    def post(self, request, pk=None):
        """Handle form submission."""
        medication_id = request.POST.get('medication')
        dosage = request.POST.get('dosage')
        frequency = request.POST.get('frequency')
        time_preference = request.POST.get('time_preference', 'anytime')
        notes = request.POST.get('notes', '')

        if not all([medication_id, dosage, frequency]):
            # Return error message
            return HttpResponse(
                '<div class="text-red-600">Please fill in all required fields</div>',
                status=400
            )

        medication = get_object_or_404(Medication, pk=medication_id)

        if pk:
            # Update existing
            user_med = get_object_or_404(UserMedication, pk=pk, user=request.user)
            user_med.medication = medication
            user_med.dosage = dosage
            user_med.frequency = frequency
            user_med.time_preference = time_preference
            user_med.notes = notes
            user_med.save()
        else:
            # Create new
            user_med = UserMedication.objects.create(
                user=request.user,
                medication=medication,
                dosage=dosage,
                frequency=frequency,
                time_preference=time_preference,
                notes=notes
            )

        # Return success and trigger page refresh
        response = HttpResponse(status=204)
        response['HX-Trigger'] = 'medicationUpdated'
        return response


class MedicationDeleteView(LoginRequiredMixin, View):
    """
    HTMX view to delete (mark inactive) a user medication.
    """

    def delete(self, request, pk):
        user_med = get_object_or_404(UserMedication, pk=pk, user=request.user)
        user_med.is_active = False
        user_med.save()

        # Return success and trigger page refresh
        response = HttpResponse(status=204)
        response['HX-Trigger'] = 'medicationUpdated'
        return response


class MealScheduleFormView(LoginRequiredMixin, View):
    """
    HTMX view for meal schedule configuration modal.
    """

    def get(self, request):
        """Return form for meal schedule."""
        try:
            meal_schedule = UserMealSchedule.objects.get(user=request.user)
        except UserMealSchedule.DoesNotExist:
            meal_schedule = None

        context = {'meal_schedule': meal_schedule}
        return TemplateResponse(request, 'drugs/partials/meal_schedule_form.html', context)

    def post(self, request):
        """Handle form submission."""
        breakfast_time = request.POST.get('breakfast_time')
        lunch_time = request.POST.get('lunch_time')
        dinner_time = request.POST.get('dinner_time')

        # Get or create meal schedule
        meal_schedule, created = UserMealSchedule.objects.get_or_create(
            user=request.user
        )

        # Update times (Django handles empty string -> None conversion)
        meal_schedule.breakfast_time = breakfast_time or None
        meal_schedule.lunch_time = lunch_time or None
        meal_schedule.dinner_time = dinner_time or None
        meal_schedule.save()

        # Return success and trigger page refresh
        response = HttpResponse(status=204)
        response['HX-Trigger'] = 'mealScheduleUpdated'
        return response
