from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.behaviors import timestampable


class SMS(timestampable.Timestampable, models.Model):
    """
    SMS model for storing text messages and their metadata.
    Used for sending SMS, tracking delivery status, and logging.
    """

    to_number = models.CharField(max_length=15)
    from_number = models.CharField(max_length=15, null=True, blank=True)
    body = models.TextField(default="")

    # Tracking fields
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    # Twilio integration fields
    external_id = models.CharField(
        max_length=34, null=True, blank=True, help_text="Message SID from Twilio"
    )
    status = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="Current status of the message (queued, sent, delivered, failed, etc.)",
    )
    error_code = models.CharField(max_length=20, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        verbose_name = "SMS"
        verbose_name_plural = "SMS Messages"

    def __str__(self):
        """String representation of the SMS."""
        return f"SMS to {self.to_number}: {self.body}"

    def mark_as_sent(self):
        """Marks the SMS as having been sent."""
        self.sent_at = timezone.now()
        self.save(update_fields=["sent_at"])

    def mark_as_read(self):
        """Marks the SMS as having been read by the recipient."""
        self.read_at = timezone.now()
        self.save(update_fields=["read_at"])

    def update_status(self, status, error_code=None, error_message=None):
        """
        Update the status of the SMS.

        Args:
            status: New status (queued, sent, delivered, failed, etc.)
            error_code: Error code if status is failed
            error_message: Error message if status is failed
        """
        self.status = status
        if error_code:
            self.error_code = error_code
        if error_message:
            self.error_message = error_message
        self.save(
            update_fields=["status", "error_code", "error_message", "modified_at"]
        )

        # If delivered, mark as read
        if status == "delivered" and not self.read_at:
            self.mark_as_read()

        return self

    def send(self):
        """
        Sends the SMS via Twilio integration.

        Returns:
            Dict with response data
        """
        try:
            # Import here to avoid circular imports
            from apps.integration.twilio.shortcuts import send_sms

            # Check if we have the necessary information
            if not self.to_number:
                return {
                    "success": False,
                    "error": "No recipient phone number specified",
                }

            if not self.body:
                return {"success": False, "error": "No message body specified"}

            # Use the from_number if specified, otherwise use default
            from_number = self.from_number or getattr(
                settings, "TWILIO_PHONE_NUMBER", None
            )

            # Send via Twilio but don't save to DB (we already have the record)
            result = send_sms(
                to_number=self.to_number,
                body=self.body,
                from_number=from_number,
                save_to_db=False,  # Don't create a new SMS record
            )

            # If successful, update the current record
            if result.get("success"):
                # Set external_id if present
                if "sid" in result:
                    self.external_id = result["sid"]
                elif "data" in result and "sid" in result["data"]:
                    self.external_id = result["data"]["sid"]

                # Update status if present
                if "status" in result:
                    self.status = result["status"]
                elif "data" in result and "status" in result["data"]:
                    self.status = result["data"]["status"]

                # Mark as sent
                self.mark_as_sent()

                # Save other fields if changed
                self.save()

            return result

        except Exception as e:
            import logging

            logging.getLogger(__name__).error(f"Error sending SMS: {str(e)}")
            return {"success": False, "error": str(e)}
