import uuid

from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver


class City(models.Model):
    """
    A model representing a city or urban area.

    This model stores information about cities, including their name,
    code (typically airport code), and country. It provides a standard
    way to reference locations at the city level throughout the application.

    Attributes:
        id (UUID): Unique identifier for the city
        name (str): The name of the city
        code (str): A short code for the city, typically the airport code
        country (ForeignKey): The country where this city is located

    Properties:
        currency (Currency): The currency used in this city, inherited from the country

    Note:
        The code field is automatically converted to lowercase before saving.

    Example:
        ```python
        san_francisco = City.objects.create(
            name="San Francisco",
            code="SFO",
            country=usa_country
        )
        ```
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True, blank=True)
    code = models.CharField(
        max_length=3, unique=True, blank=True
    )  # typically the airport code

    # assuming cities stick to one country nationwide
    country = models.ForeignKey(
        "common.Country", related_name="cities", null=False, on_delete=models.PROTECT
    )

    # MODEL PROPERTIES
    @property
    def currency(self):
        """
        Get the currency used in this city.

        This is a convenience property that returns the currency associated
        with the country of this city.

        Returns:
            Currency: The currency used in this city
        """
        return self.country.currency

    # MODEL FUNCTIONS
    def __str__(self) -> str:
        """
        Get a string representation of the city.

        Returns:
            str: A formatted string with city name, country name, and code
        """
        return f"{self.name}, {self.country.name} ({self.code})"

    class Meta:
        verbose_name_plural = "cities"


@receiver(pre_save, sender=City)
def lowercase_code(sender, instance, **kwargs):
    """
    Signal handler that converts city code to lowercase before saving.

    This ensures that all city codes are stored in a consistent format,
    making lookups and comparisons easier.

    Args:
        sender: The model class (City)
        instance: The City instance being saved
        **kwargs: Additional keyword arguments
    """
    if instance.code:
        instance.code = instance.code.lower()
