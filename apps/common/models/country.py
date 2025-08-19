import uuid

from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver


class Country(models.Model):
    """
    A model representing a country or nation.

    This model stores information about countries, including their name, ISO code,
    calling code, and currency. It serves as a reference for other models that
    need country-specific data.

    Attributes:
        id (UUID): Unique identifier for the country
        name (str): The full name of the country
        code (str): The ISO 3166-1 alpha-2 country code (e.g., US, JP, GB)
        calling_code (str): The international dialing code (e.g., 1 for USA, 44 for UK)
        currency (ForeignKey): The primary currency used in this country

    Note:
        The code field is automatically converted to lowercase before saving.
        This model could be expanded using django-countries for more comprehensive data.

    Example:
        ```python
        usa = Country.objects.create(
            name="United States",
            code="US",
            calling_code="1",
            currency=usd_currency
        )
        ```
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True, blank=True)
    code = models.CharField(
        max_length=3,
        unique=True,
        blank=True,
        help_text="ISO 3166-1 alpha-2 (eg. US, TH, GB)",
    )
    calling_code = models.CharField(
        max_length=3,
        blank=True,
        help_text="e.g. '1' for (+1 USA) or '66' (+66 TH)",
    )

    # assuming countries stick to one currency nationwide
    currency = models.ForeignKey(
        "common.Currency",
        related_name="countries",
        null=True,
        on_delete=models.SET_NULL,
    )

    # MODEL PROPERTIES

    # MODEL FUNCTIONS
    def __str__(self) -> str:
        """
        Get a string representation of the country.

        Returns:
            str: The name of the country
        """
        return self.name

    @property
    def formatted_calling_code(self) -> str:
        """
        Get the formatted international calling code.

        Returns:
            str: The calling code with a plus sign prefix (e.g., +1, +44)
        """
        if self.calling_code:
            return f"+{self.calling_code}"
        return ""

    class Meta:
        verbose_name_plural = "countries"


@receiver(pre_save, sender=Country)
def lowercase_code(sender, instance, **kwargs):
    """
    Signal handler that converts country code to lowercase before saving.

    This ensures that all country codes are stored in a consistent format,
    making lookups and comparisons easier.

    Args:
        sender: The model class (Country)
        instance: The Country instance being saved
        **kwargs: Additional keyword arguments
    """
    if instance.code:
        instance.code = instance.code.lower()
