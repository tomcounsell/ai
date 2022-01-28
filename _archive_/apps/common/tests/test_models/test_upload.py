from django.test import TestCase

from ..test_behaviors import TimestampableTest
from ...models import Upload


class UploadTest(TimestampableTest, TestCase):
    model = Upload
