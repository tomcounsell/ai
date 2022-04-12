from datetime import datetime
import requests
from django.core.mail import EmailMessage
from django.db import models

from apps.common.models import Upload
from apps.common.behaviors import Timestampable


class Email(Timestampable, models.Model):
    to_address = models.CharField(max_length=140)
    from_address = models.CharField(
        max_length=140,
        default="Info <bookings@example.com>"
    )
    subject = models.TextField(max_length=140)
    body = models.TextField(default="")
    attachments = models.ManyToManyField(Upload)

    (NOTIFICATION, CONFIRMATION, PASSWORD, ) = range(3)
    ''' text values here are used as subject line in email notifications,
        duplicate values allowed '''
    TYPE_CHOICES = (
        (NOTIFICATION, 'notification'),
        (CONFIRMATION, 'confirmation'),
        (PASSWORD, 'password'),
    )
    type = models.SmallIntegerField(
        choices=TYPE_CHOICES, null=True, blank=True, default=NOTIFICATION
    )

    # UPDATE HISTORY
    sent_at = models.DateTimeField(null=True)
    read_at = models.DateTimeField(null=True)

    # MODEL PROPERTIES

    # MODEL FUNCTIONS
    def createMessageObject(self, manager=None):
        self.email = EmailMessage()
        return self.email

    def createSubject(self):
        self.subject = self.get_type_display().title()
        self.subject = self.subject or ""
        return self.subject

    def createBody(self):
        # needs logic
        return self.body

    def sendToUser(self, user_object):
        self.to_address = user_object.email
        self.send()

    def send(self, require_confirmation=False):
        if not (self.from_address and self.to_address and self.type > -1):
            print ("from, to, type required")
            return False

            # createSubject and createBody require self.url_base
            self.subject = self.subject or self.createSubject()
            self.body = self.body or self.createBody()

            '''
            these actions requires the body to already be constructed
            if STAGE or DEBUG:non-production emails
                self.body = ("""*on %s server*
                         From: % s
                         To: %s
                         %s
                     """ % (server_name, self.from_address,
                     self.to_address, self.body))
            '''
            # in case it hasn't been saved yet and doesn't have an id
            self.save()
            if not hasattr(self, 'email'):
                self.email = self.createMessageObject()
            self.email.subject = self.subject
            self.email.body = self.body
            self.email.from_email = self.from_address
            self.email.to = [self.to_address]

            for attachment in self.attachments.all():
                file_name = (
                    attachment.name | "file_upload"
                ) + attachment.file_extension
                file_via_url = requests.get(attachment.original)
                self.email.attach(file_name, file_via_url.read())

            if require_confirmation:
                return self.send_now()
            else:
                self.send_later()
                return None

    def send_now(self):
        try:
            self.email.send(fail_silently=False)
            self.sent_at = datetime.now()
            self.save()
            return True
        except Exception as e:
            print (str(e))
            return False
