import logging

from django.db import connection
from django.views.generic import TemplateView

from apps.book.models import Testimonial

logger = logging.getLogger(__name__)


def _table_exists(table_name: str) -> bool:
    """Check whether a database table exists without risking a transaction abort."""
    return table_name in connection.introspection.table_names()


class LandingView(TemplateView):
    """Rich book landing page with all structured sections."""

    template_name = "book/landing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if _table_exists("book_testimonial"):
            context["testimonials"] = list(Testimonial.objects.filter(is_featured=True))
        else:
            context["testimonials"] = []
        return context
