import hashlib
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import datetime
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.behaviors import Timestampable, Locatable


class User(AbstractUser, Timestampable, Locatable):
    REQUIRED_FIELDS = AbstractUser.REQUIRED_FIELDS
    REQUIRED_FIELDS.remove('email')
    phone_number = models.CharField(max_length=15, default="", blank=True)

    # SERVICE SETTINGS
    email_is_verified = models.BooleanField(default=False)
    agreed_to_terms_at = models.DateTimeField(blank=True, null=True)
    # timezone = TimeZoneField(blank=True, null=True)  # https://github.com/mfogel/django-timezone-field
    # notifications_enabled = models.BooleanField(default=False)
    # beta_tester_since = models.DateTimeField(blank=True, null=True)

    # SOCIALS
    # FB_user_id = models.CharField(max_length=100, default="", blank=True)
    # FB_user_access_token = models.CharField(max_length=255, default="", blank=True)


    # HISTORY MANAGER
    # history = HistoricalRecords()  # requires django-simple-history


    # MODEL PROPERTIES

    @property
    def jwt_token(self):
        return str(RefreshToken.for_user(self).access_token)

    @property
    def four_digit_login_code(self):
        if self.email.endswith("@example.com"): return "1234"  # for test accounts
        hash_object = hashlib.md5(bytes(f"{self.id}{self.email}{self.last_login}", encoding='utf-8'))
        return str(int(hash_object.hexdigest(), 16))[-4:]

    @property
    def is_agreed_to_terms(self) -> bool:
        if self.agreed_to_terms_at and self.agreed_to_terms_at > timezone.make_aware(datetime(2019, 11, 1)):
            return True
        return False

    @is_agreed_to_terms.setter
    def is_agreed_to_terms(self, value: bool):
        if value is True:
            self.agreed_to_terms_at = timezone.now()
        elif value is False and self.is_agreed_to_terms:
            self.agreed_to_terms_at = None


    # MODEL FUNCTIONS
    def __str__(self):
        try:
            if self.first_name:
                return self.first_name
            if self.username and not "@" in self.username:
                return self.username
            if self.email_is_verified:
                return self.email.split("@")[0]
            else:
                return "anonymous"
        except:
            return f"User {self.id}" if self.id else "anonymous"


# @receiver(pre_save, sender=User)
# def pre_save(sender, instance, **kwargs):
#     # hopefully this never has to run because it's too late to alert the user that they need to verify their email
#     # if instance.email and instance.email_is_verified:
#     #     if User.objects.get(pk=instance.pk).email is not instance.email:
#     #         instance.email_is_verified = False
#
#     if instance.username and not instance.email:
#         from django.core.validators import validate_email
#         from django.core.exceptions import ValidationError
#         try:
#             validate_email(instance.username)
#         except ValidationError:
#             pass
#         else:
#             instance.email = instance.username
#             instance.email_is_verified = False
#
#     if not instance.username or instance.username == "random":
#         instance.username = str(uuid.uuid4())[-12:]
#
# @receiver(post_save, sender=User)
# def post_save(sender, instance, **kwargs):
#     pass
