from django.contrib.auth.models import User
from django.test import TestCase

from ..test_behaviors import AuthorableTest, TimestampableTest
from ...models import Note


class NoteTest(AuthorableTest, TimestampableTest, TestCase):
    model = Note

    def setUp(self):
        self.user = User.objects.create_user(
            username='prince',
            email='prince@p.com',
            password='password'
        )

    def create_instance(self, **kwargs):
        return Note.objects.create(
            author=self.user,
            **kwargs
        )
