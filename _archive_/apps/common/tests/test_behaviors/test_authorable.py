from unittest import mock

from django.utils import timezone

from .test_mixins import BehaviorTestCaseMixin


class AuthorableTest(BehaviorTestCaseMixin):
    def test_save_authored_at_should_store_data_correctly(self):
        now = timezone.now()

        with mock.patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = now
            obj = self.create_instance(authored_at=now)
            self.assertEqual(obj.authored_at, now)

    def test_should_store_author(self):
        obj = self.create_instance()
        self.assertEqual(obj.author, self.user)
