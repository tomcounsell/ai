# No need for datetime import as we use timezone.now() instead
import urllib.request

from django.core.mail import EmailMessage
from django.db import models
from django.utils import timezone

from apps.common.behaviors import timestampable
from apps.common.models.upload import Upload


class Email(timestampable.Timestampable, models.Model):
    """
    Email model for storing email messages and their metadata.
    Used for sending emails, tracking delivery status, and logging.
    """

    to_address = models.CharField(max_length=140)
    from_address = models.CharField(
        max_length=140, default="Support <support@example.com>"
    )
    subject = models.TextField(max_length=140)
    body = models.TextField(default="")
    attachments = models.ManyToManyField(
        Upload, blank=True, related_name="common_email_attachments"
    )

    # Email type options
    NOTIFICATION, CONFIRMATION, PASSWORD = 0, 1, 2
    TYPE_CHOICES = (
        (NOTIFICATION, "notification"),
        (CONFIRMATION, "confirmation"),
        (PASSWORD, "password"),
    )
    type = models.SmallIntegerField(
        choices=TYPE_CHOICES, null=True, blank=True, default=NOTIFICATION
    )

    # Tracking fields
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        """String representation of the Email."""
        return f"Email to {self.to_address}: {self.subject}"

    def createMessageObject(self, manager=None):
        """Creates the Django EmailMessage object."""
        self.email = EmailMessage()
        return self.email

    def createSubject(self):
        """Creates a subject line based on the email type."""
        if not self.subject:
            self.subject = self.get_type_display().title()
        return self.subject or ""

    def createBody(self):
        """Creates the email body content."""
        # Override this method in subclasses to generate dynamic content
        return self.body

    def sendToUser(self, user_object):
        """Convenience method to send email to a user object."""
        self.to_address = user_object.email
        return self.send()

    def send(self, require_confirmation=False):
        """
        Prepares and sends the email.

        Args:
            require_confirmation: If True, sends immediately and returns result.
                                 If False, queues for sending later.
        """
        if not (self.from_address and self.to_address and self.type > -1):
            return False

        # Prepare subject and body
        self.subject = self.subject or self.createSubject()
        self.body = self.body or self.createBody()

        # Save before sending
        self.save()

        # Create message object if not already created
        if not hasattr(self, "email"):
            self.email = self.createMessageObject()

        # Configure email message
        self.email.subject = self.subject
        self.email.body = self.body
        self.email.from_email = self.from_address
        self.email.to = [self.to_address]

        # Add attachments if any
        for attachment in self.attachments.all():
            file_name = f"{attachment.name or 'attachment'}{attachment.file_extension}"
            file_via_url = urllib.request.urlopen(attachment.original)
            self.email.attach(file_name, file_via_url.read())

        if require_confirmation:
            return self.send_now()
        else:
            self.send_later()
            return None

    def send_now(self):
        """
        Sends the email immediately.

        Returns:
            bool: True if successful, False if there was an error.
        """
        try:
            self.email.send(fail_silently=False)
            self.sent_at = timezone.now()
            self.save(update_fields=["sent_at"])
            return True
        except Exception:
            return False

    def send_later(self):
        """Queues the email for sending later (e.g., via Celery task)."""
        # In a real implementation, this would add the email to a queue
        # For now, we'll just simulate sending
        self.send_now()

    def mark_as_read(self):
        """Marks the email as having been read by the recipient."""
        self.read_at = timezone.now()
        self.save(update_fields=["read_at"])
