from django.db import models

accepted_file_types = ["pdf", "doc", "docx", "rtf", "pages"]


class Document(models.Model):
    """
    Represents a generic document that can be uploaded.

    This class serves as a base for specific document types and includes common attributes
    and behaviors related to uploading and timestamping.

    Attributes:
        upload: ForeignKey to the Upload model that contains the file data

    Methods:
        display: A property that must be implemented by subclasses to provide the HTML
            to display for the document.

    Note:
        This class is intended to be subclassed by specific document types.
    """

    upload = models.ForeignKey(
        "common.Upload", on_delete=models.CASCADE, related_name="documents"
    )

    @property
    def display(self):
        """
        Implemented by subclasses
        :return: string containing the HTML to display for this document
        """
        pass


class PDF(Document):
    """
    Represents a PDF document.

    This class extends the Document class and provides specific behavior for PDF documents.

    Methods:
        display: Returns a string representing the HTML to display for a PDF document.
    """

    @property
    def display(self):
        return "PDF Document"


def create(ext):
    """
    Factory function to create a database object based on the file extension and save it.

    :param ext: file extension (str). Must be one of the accepted_file_types.
    :return: Database object corresponding to the extension. Currently supports PDF only.
    :raises: ValueError if the extension is not in accepted_file_types.
    """
    ext = ext.lower()
    if ext == accepted_file_types[0]:
        doc_object = Document.objects.create()
        return doc_object
    else:
        raise ValueError(f"Extension {ext} is not supported.")
