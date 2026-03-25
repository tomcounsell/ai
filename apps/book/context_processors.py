from django.conf import settings


def book_context(request):
    """Inject book-specific context into all book templates."""
    return {
        "feedback_url": getattr(
            settings, "BOOK_FEEDBACK_FORM_URL", "#feedback-form-coming-soon"
        ),
    }
