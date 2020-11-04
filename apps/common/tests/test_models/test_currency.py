from django.test import TestCase

from ..test_behaviors import TimestampableTest
from ...models import Currency


class CurrencyTest(TimestampableTest, TestCase):
    model = Currency
