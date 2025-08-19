from typing import Optional

from django.db import models

# from timezone_field import TimeZoneField


class Locatable(models.Model):
    """
    A behavior mixin that adds location-related fields to a model.

    This mixin allows models to have a physical location represented by an address
    and geographic coordinates. It's useful for models that need to be associated
    with a specific location, such as events, businesses, or user profiles.

    Attributes:
        address (ForeignKey): A foreign key to the Address model, representing
            the physical address associated with this object. Can be null if
            the address is unknown or not applicable.

        longitude (float, optional): The longitude coordinate of the location.
            Used for mapping and geospatial queries.

        latitude (float, optional): The latitude coordinate of the location.
            Used for mapping and geospatial queries.

    Example:
        ```python
        class Event(Locatable, models.Model):
            name = models.CharField(max_length=100)
            start_time = models.DateTimeField()

            # Can now associate an address and coordinates with the event
        ```

    Note:
        This model is abstract and should be used as a mixin in other models.
        The commented timezone field can be uncommented if timezone information
        is needed for the location.
    """

    address = models.ForeignKey(
        "common.Address", null=True, blank=True, on_delete=models.SET_NULL
    )
    # timezone = TimeZoneField(blank=True, null=True)

    longitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)

    @property
    def has_coordinates(self) -> bool:
        """
        Check if the object has valid geographic coordinates.

        Returns:
            bool: True if both latitude and longitude are set, False otherwise.
        """
        return self.latitude is not None and self.longitude is not None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """
        Get the coordinates as a latitude/longitude tuple.

        Returns:
            tuple[float, float] or None: A tuple of (latitude, longitude) if both
                coordinates are available, None otherwise.
        """
        if self.has_coordinates:
            return (self.latitude, self.longitude)
        return None

    class Meta:
        abstract = True
