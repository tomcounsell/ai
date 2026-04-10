"""
Management command to send a test email via the configured email backend.

Usage:
    python manage.py test_send_email --to ops@yuda.me
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a test email to verify email backend configuration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            required=True,
            help="Recipient email address",
        )
        parser.add_argument(
            "--subject",
            default="Test email from Cuttlefish",
            help="Email subject (default: 'Test email from Cuttlefish')",
        )

    def handle(self, *args, **options):
        recipient = options["to"]
        subject = options["subject"]
        from_email = settings.DEFAULT_FROM_EMAIL

        self.stdout.write(
            f"Sending test email via {settings.EMAIL_BACKEND}\n"
            f"  From: {from_email}\n"
            f"  To:   {recipient}\n"
            f"  Subject: {subject}\n"
        )

        result = send_mail(
            subject=subject,
            message=(
                "This is a test email sent from the Cuttlefish platform "
                "to verify that the email backend is configured correctly.\n\n"
                f"Backend: {settings.EMAIL_BACKEND}\n"
                f"From: {from_email}\n"
            ),
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )

        if result == 1:
            self.stdout.write(self.style.SUCCESS("Email sent successfully."))
        else:
            self.stderr.write(
                self.style.ERROR(f"send_mail returned {result} (expected 1).")
            )
