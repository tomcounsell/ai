from django.db import models

from .upload import Upload

ACCEPTED_FILE_TYPES = ["jpg", "gif", "png"]


class Image(Upload):
    """
    A model representing an image file.

    This model extends the Upload model to specifically handle image files.
    It provides additional image-specific fields and properties like thumbnail URL,
    width, and height.

    Attributes:
        thumbnail_url (str): URL to a thumbnail version of the image

    Properties:
        width (int): The width of the image in pixels
        height (int): The height of the image in pixels

    Note:
        This model restricts uploads to common image file types (jpg, gif, png).
        Metadata like width and height are stored in the meta_data JSON field.

    Example:
        ```python
        image = Image.objects.create(
            original="https://example.com/images/photo.jpg",
            meta_data={"mime_type": "image/jpeg", "meta": {"width": 800, "height": 600}},
            thumbnail_url="https://example.com/images/photo_thumb.jpg"
        )
        ```
    """

    thumbnail_url = models.URLField(default="", null=True, blank=True)

    # MODEL PROPERTIES
    @property
    def url(self):
        return self.original

    @property
    def aspect_ratio(self) -> float | None:
        """
        Calculate the aspect ratio of the image.

        Returns:
            float or None: The width/height ratio, or None if dimensions are not available
        """
        if self.width and self.height and self.height > 0:
            return self.width / self.height
        return None

    @property
    def orientation(self) -> str | None:
        """
        Determine the orientation of the image.

        Returns:
            str or None: "landscape", "portrait", "square", or None if dimensions are not available
        """
        if not self.width or not self.height:
            return None

        if self.width > self.height:
            return "landscape"
        elif self.height > self.width:
            return "portrait"
        else:
            return "square"
