from django.test import TestCase

from ..test_behaviors import TimestampableTest
from ...models import Address


class AddressTest(TimestampableTest, TestCase):
    model = Address
