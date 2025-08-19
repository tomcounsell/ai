"""
Tests for the Document model and related functionality.
"""

from unittest import mock

from django.test import TestCase

from ...models import Document, Upload
from ...models.document import PDF, accepted_file_types, create
from ..test_behaviors import TimestampableTest


class DocumentTest(TimestampableTest, TestCase):
    """Test cases for the Document model."""

    model = Document

    def create_instance(self, **kwargs):
        """Create a Document instance with default test values."""
        default_kwargs = {
            "original": "https://example.com/document.pdf",
            "meta_data": {"mime_type": "application/pdf", "ext": "pdf"},
        }
        default_kwargs.update(kwargs)
        return Document.objects.create(**default_kwargs)

    def test_inheritance_from_upload(self):
        """Test that Document inherits from Upload."""
        document = self.create_instance()
        self.assertIsInstance(document, Upload)

        # Test that Upload fields and methods are accessible
        self.assertTrue(hasattr(document, "original"))
        self.assertTrue(hasattr(document, "name"))
        self.assertTrue(hasattr(document, "meta_data"))
        self.assertTrue(hasattr(document, "file_type"))

    def test_display_property_implementation(self):
        """Test that the display property is implemented in the base class."""
        document = self.create_instance()
        self.assertIsNone(document.display)

    def test_upload_properties_inheritance(self):
        """Test that Document inherits Upload properties correctly."""
        document = self.create_instance(
            original="https://example.com/document.pdf",
            name="test_document.pdf",
            meta_data={
                "mime_type": "application/pdf",
                "ext": "pdf",
                "meta": {"width": 800, "height": 600},
            },
        )

        # Test file_type property
        with mock.patch(
            "apps.common.models.upload.guess_type",
            return_value=("application/pdf", None),
        ):
            self.assertEqual(document.file_type, "application/pdf")

        # Test is_pdf property
        with mock.patch.object(
            Document, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            mock_file_type.return_value = "application/pdf"
            self.assertTrue(document.is_pdf)

        # Test file_extension property
        self.assertEqual(document.file_extension, "pdf")

        # Test link_title property
        self.assertEqual(document.link_title, "test_document.pdf .PDF")

    def test_with_different_file_types(self):
        """Test Document behavior with different file types."""
        # Test with doc file
        doc = self.create_instance(
            original="https://example.com/document.doc",
            meta_data={"mime_type": "application/msword", "ext": "doc"},
        )
        with mock.patch.object(
            Document, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            mock_file_type.return_value = "application/msword"
            self.assertFalse(doc.is_pdf)
            self.assertFalse(doc.is_image)

        # Test with docx file
        docx = self.create_instance(
            original="https://example.com/document.docx",
            meta_data={
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "ext": "docx",
            },
        )
        self.assertEqual(docx.file_extension, "docx")


class PDFTest(TestCase):
    """Test cases for the PDF model."""

    def test_inheritance_from_document(self):
        """Test that PDF inherits from Document."""
        pdf = PDF.objects.create(
            original="https://example.com/document.pdf",
            meta_data={"mime_type": "application/pdf", "ext": "pdf"},
        )
        self.assertIsInstance(pdf, Document)

    def test_display_property(self):
        """Test that the display property returns the correct value for PDF documents.

        The display property is overridden in the PDF class.
        """
        pdf = PDF.objects.create(
            original="https://example.com/document.pdf",
            meta_data={"mime_type": "application/pdf", "ext": "pdf"},
        )
        self.assertEqual(pdf.display, "PDF Document")

    def test_pdf_with_metadata(self):
        """Test PDF with various metadata."""
        pdf = PDF.objects.create(
            original="https://example.com/document.pdf",
            name="test_document.pdf",
            meta_data={
                "mime_type": "application/pdf",
                "ext": "pdf",
                "size": 1024,
                "type": "document",
                "etc": "important",
            },
        )

        # Test is_pdf property
        with mock.patch.object(
            PDF, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            mock_file_type.return_value = "application/pdf"
            self.assertTrue(pdf.is_pdf)

        # Test link_title with name provided
        self.assertEqual(pdf.link_title, "test_document.pdf .PDF")

        # Test link_title with no name but etc and ext
        pdf.name = ""
        self.assertEqual(pdf.link_title, "IMPORTANT .PDF")


class DocumentCreateTest(TestCase):
    """Test cases for the document create function."""

    def test_create_with_pdf_extension(self):
        """Test create function with PDF extension."""
        document = create("pdf")
        self.assertIsInstance(document, Document)

    def test_create_with_invalid_extension(self):
        """Test create function with invalid extension raises ValueError."""
        with self.assertRaises(ValueError) as context:
            create("invalid")
        self.assertIn("Extension invalid is not supported", str(context.exception))

    def test_create_with_uppercase_extension(self):
        """Test create function with uppercase extension works correctly."""
        document = create("PDF")
        self.assertIsInstance(document, Document)

    def test_create_with_edge_cases(self):
        """Test create function with edge cases."""
        # Test with empty string
        with self.assertRaises(ValueError):
            create("")

        # Test with None (should raise TypeError)
        with self.assertRaises(AttributeError):
            create(None)

    def test_accepted_file_types(self):
        """Test that the accepted_file_types list contains the expected values."""
        expected_types = ["pdf", "doc", "docx", "rtf", "pages"]
        self.assertEqual(accepted_file_types, expected_types)

        # Verify all file types are lowercase
        for file_type in accepted_file_types:
            self.assertEqual(file_type.lower(), file_type)
