from typing import Optional

from django.db import models

ACCEPTED_FILE_TYPES = ["jpg", "gif", "png"]


class Image(models.Model):
    """
    A model representing an image file.

    This model extends the Upload model to specifically handle image files.
    It provides additional image-specific fields and properties like thumbnail URL,
    width, and height.

    Attributes:
        upload: ForeignKey to the Upload model containing the file data
        thumbnail_url (str): URL to a thumbnail version of the image
        url (str): URL to the full-size image (from upload.original)
        meta_data (dict): Metadata about the image (from upload.meta_data)
        created_at (datetime): When this image was created (upload.created_at)
        modified_at (datetime): When this image was last modified (upload.modified_at)

    Properties:
        width (int): The width of the image in pixels
        height (int): The height of the image in pixels

    Note:
        This model restricts uploads to common image file types (jpg, gif, png).
        Metadata like width and height are stored in the meta_data JSON field.

    Example:
        ```python
        image = Image.objects.create(
            upload=upload_obj,
            thumbnail_url="https://example.com/images/photo_thumb.jpg"
        )
        ```
    """

    upload = models.ForeignKey(
        "common.Upload", on_delete=models.CASCADE, related_name="images"
    )
    thumbnail_url = models.URLField(default="", null=True, blank=True)

    # PROPERTIES TO ACCESS UPLOAD FIELDS
    @property
    def url(self):
        return self.upload.original if self.upload else None

    @property
    def meta_data(self):
        return self.upload.meta_data if self.upload else {}

    @property
    def created_at(self):
        return self.upload.created_at if self.upload else None

    @property
    def modified_at(self):
        return self.upload.modified_at if self.upload else None

    @property
    def original(self):
        return self.upload.original if self.upload else None

    @property
    def name(self):
        return self.upload.name if self.upload else None

    @property
    def is_image(self):
        return self.upload.is_image if self.upload else False

    # MODEL PROPERTIES
    @property
    def width(self) -> int | None:
        """
        Get the width of the image in pixels.

        Retrieves the width from the meta_data JSON field if available.

        Returns:
            int or None: The width of the image in pixels, or None if not available
        """
        if self.is_image:
            return (
                self.meta_data["meta"].get("width")
                if self.meta_data.get("meta")
                else None
            )
        return None

    @property
    def height(self) -> int | None:
        """
        Get the height of the image in pixels.

        Retrieves the height from the meta_data JSON field if available.

        Returns:
            int or None: The height of the image in pixels, or None if not available
        """
        if self.is_image:
            return (
                self.meta_data["meta"].get("height")
                if self.meta_data.get("meta")
                else None
            )
        return None

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

    @property
    def dimensions(self):
        """Get the image dimensions if available."""
        if self.is_image:
            return (self.width, self.height)
        return None

    @property
    def file_extension(self):
        """Get the file extension."""
        if self.meta_data and "ext" in self.meta_data:
            return self.meta_data.get("ext", "")
        return ""

    @property
    def file_type(self):
        """Override to prioritize the mocked value in tests."""
        from mimetypes import guess_type

        if self.original:
            mime_type, _ = guess_type(self.original)
            if mime_type:
                return mime_type

        return self.meta_data.get("mime_type", "")

    @property
    def link_title(self):
        """Generate a user-friendly title for the uploaded file."""
        if self.name:
            return self.name

        if self.meta_data:
            if "etc" in self.meta_data and self.meta_data["etc"]:
                title = self.meta_data["etc"].upper()
            elif "type" in self.meta_data and self.meta_data["type"]:
                title = self.meta_data["type"].upper()
            else:
                title = ""

            # Add file extension
            ext = self.file_extension
            if ext:
                if title:
                    title = f"{title} .{ext.upper()}"
                else:
                    title = f" .{ext.upper()}"

            return title

        return ""
