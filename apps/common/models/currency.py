import uuid

from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.common.behaviors import Timestampable


class Currency(Timestampable, models.Model):
    """
    A model representing a currency used for financial transactions.

    This model stores information about currencies using the ISO 4217 standard.
    It tracks creation and modification times through the Timestampable behavior.

    Attributes:
        id (UUID): Unique identifier for the currency
        name (str): The full name of the currency (e.g., US Dollar, Euro)
        code (str): The ISO 4217 currency code (e.g., USD, EUR, JPY)
        created_at (datetime): When this currency record was created
        modified_at (datetime): When this currency record was last modified

    Note:
        The code field is automatically converted to lowercase before saving,
        but the string representation will display it in uppercase.

    Example:
        ```python
        usd = Currency.objects.create(
            name="US Dollar",
            code="USD"
        )
        ```
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    code = models.CharField(
        max_length=3,
        unique=True,
        help_text="ISO 4217 currency code (eg. USD, THB, GBP)",
    )

    # MODEL PROPERTIES

    # MODEL FUNCTIONS
    def __str__(self) -> str:
        """
        Get a string representation of the currency.

        Returns the currency code in uppercase for display purposes.

        Returns:
            str: The ISO currency code in uppercase (e.g., USD, EUR)
        """
        return str(self.code).upper()

    @property
    def symbol(self) -> str:
        """
        Get the currency symbol based on the currency code.

        This is a simple implementation that handles only the most common currencies.
        For a more comprehensive solution, consider using a dedicated library.

        Returns:
            str: The currency symbol (e.g., $, €, £) or the code if no symbol is defined
        """
        symbols = {
            "usd": "$",
            "eur": "€",
            "gbp": "£",
            "jpy": "¥",
            "cny": "¥",
            "krw": "₩",
            "inr": "₹",
            "thb": "฿",
        }
        return symbols.get(self.code.lower(), self.code.upper())

    class Meta:
        verbose_name_plural = "currencies"


@receiver(pre_save, sender=Currency)
def lowercase_code(sender, instance, **kwargs):
    """
    Signal handler that converts currency code to lowercase before saving.

    This ensures that all currency codes are stored in a consistent format,
    making lookups and comparisons easier.

    Args:
        sender: The model class (Currency)
        instance: The Currency instance being saved
        **kwargs: Additional keyword arguments
    """
    if instance.code:
        instance.code = instance.code.lower()
