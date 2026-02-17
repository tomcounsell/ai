"""
Medication scheduling service.

Generates simple daily medication schedules based on:
- Time preferences (morning/evening/anytime)
- Food timing requirements (with food/empty stomach/anytime)
- User's meal schedule
"""

from datetime import time

from apps.drugs.models import UserMealSchedule, UserMedication


class MedicationScheduler:
    """
    Service to generate suggested medication timing based on user preferences and requirements.
    """

    @staticmethod
    def generate_daily_schedule(
        user_medications: list[UserMedication], meal_schedule: UserMealSchedule = None
    ) -> dict:
        """
        Generate a simple daily medication schedule.

        Args:
            user_medications: List of UserMedication objects to schedule
            meal_schedule: Optional UserMealSchedule object

        Returns:
            Dict with schedule groups:
            {
                'morning_with_breakfast': [list of medications],
                'morning_before_breakfast': [list of medications],
                'afternoon_with_lunch': [list of medications],
                'evening_with_dinner': [list of medications],
                'evening': [list of medications],
                'anytime': [list of medications],
            }
        """
        schedule = {
            "morning_before_breakfast": [],
            "morning_with_breakfast": [],
            "afternoon_with_lunch": [],
            "evening_with_dinner": [],
            "evening": [],
            "anytime": [],
        }

        if not user_medications:
            return schedule

        # Default meal times if not configured
        default_breakfast = time(8, 0)  # 8:00 AM
        default_lunch = time(12, 0)  # 12:00 PM
        default_dinner = time(18, 0)  # 6:00 PM

        # Use user's meal times if available
        breakfast_time = (
            meal_schedule.breakfast_time if meal_schedule else default_breakfast
        )
        lunch_time = meal_schedule.lunch_time if meal_schedule else default_lunch
        dinner_time = meal_schedule.dinner_time if meal_schedule else default_dinner

        for user_med in user_medications:
            med = user_med.medication
            time_pref = user_med.time_preference
            food_timing = med.food_timing

            # Determine placement based on time preference + food timing
            if food_timing == "empty_stomach":
                # Empty stomach meds go before breakfast (typically)
                schedule["morning_before_breakfast"].append(
                    {
                        "user_medication": user_med,
                        "suggested_time": default_breakfast.replace(
                            hour=default_breakfast.hour - 1
                        ),  # 1 hour before
                        "note": "Take on empty stomach, at least 1 hour before breakfast",
                    }
                )

            elif time_pref == "morning":
                if food_timing == "with_food" and breakfast_time:
                    schedule["morning_with_breakfast"].append(
                        {
                            "user_medication": user_med,
                            "suggested_time": breakfast_time,
                            "note": "Take with breakfast",
                        }
                    )
                else:
                    schedule["morning_with_breakfast"].append(
                        {
                            "user_medication": user_med,
                            "suggested_time": breakfast_time or default_breakfast,
                            "note": "Take in the morning",
                        }
                    )

            elif time_pref == "evening":
                if food_timing == "with_food" and dinner_time:
                    schedule["evening_with_dinner"].append(
                        {
                            "user_medication": user_med,
                            "suggested_time": dinner_time,
                            "note": "Take with dinner",
                        }
                    )
                else:
                    schedule["evening"].append(
                        {
                            "user_medication": user_med,
                            "suggested_time": dinner_time or default_dinner,
                            "note": "Take in the evening",
                        }
                    )

            else:  # time_pref == 'anytime'
                if food_timing == "with_food":
                    # Default to lunch for "anytime + with food" meds
                    schedule["afternoon_with_lunch"].append(
                        {
                            "user_medication": user_med,
                            "suggested_time": lunch_time,
                            "note": "Take with a meal (lunch suggested)",
                        }
                    )
                else:
                    schedule["anytime"].append(
                        {
                            "user_medication": user_med,
                            "suggested_time": lunch_time or default_lunch,
                            "note": "Take any time of day",
                        }
                    )

        return schedule

    @staticmethod
    def get_display_time(suggested_time: time) -> str:
        """
        Format a time object for display.

        Args:
            suggested_time: time object

        Returns:
            Formatted time string (e.g., "8:00 AM")
        """
        return suggested_time.strftime("%-I:%M %p")  # "8:00 AM" format

    @staticmethod
    def get_schedule_summary(schedule: dict) -> list[dict]:
        """
        Convert schedule dict into a flat list for display, sorted by time.

        Args:
            schedule: Schedule dict from generate_daily_schedule()

        Returns:
            List of schedule items sorted by time
        """
        all_items = []
        for group_key, items in schedule.items():
            for item in items:
                all_items.append(
                    {
                        "group": group_key,
                        "time": item["suggested_time"],
                        "time_display": MedicationScheduler.get_display_time(
                            item["suggested_time"]
                        ),
                        "medication": item["user_medication"],
                        "note": item["note"],
                    }
                )

        # Sort by time
        all_items.sort(key=lambda x: x["time"])
        return all_items
