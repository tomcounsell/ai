from apps.common.behaviors import Uploadable, Timestampable

accepted_file_types = ['pdf', 'doc', 'docx', 'rtf', 'pages']


class Document(Uploadable, Timestampable):
    # https://django-polymorphic.readthedocs.io/en/latest/

    @property
    def display(self):
        """
    Implemented by sub-classes
    :return: string containing the HTML to display for this document
    """
        pass


class PDF(Document):
    @property
    def display(self):
        return "PDF Document"


def create(ext):
    """
    Factory function. Creates a database object, saves.
    :param ext: file extension
    :return: Depends on extension
    """
    ext = ext.lower()
    if ext == accepted_file_types[0]:
        pdf = PDF.objects.create()
        return pdf
