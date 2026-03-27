import logging

from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from apps.book.forms import EarlyReaderSignupForm
from apps.book.models import EarlyReader

logger = logging.getLogger(__name__)


class EarlyReaderSignupView(CreateView):
    """Early reader signup form -- creates an EarlyReader and sends welcome email."""

    model = EarlyReader
    form_class = EarlyReaderSignupForm
    template_name = "book/signup.html"
    success_url = reverse_lazy("book:signup_success")

    def form_valid(self, form):
        response = super().form_valid(form)
        # Fire-and-forget welcome email via Loops
        try:
            from apps.integration.loops.shortcuts import send_early_reader_welcome_email

            send_early_reader_welcome_email(self.object)
        except Exception:
            logger.exception("Failed to send early reader welcome email")
        return response


class SignupSuccessView(TemplateView):
    """Thank-you page shown after successful signup."""

    template_name = "book/signup_success.html"
