from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.artist.models import Artist
from apps.user.models import User
from apps.content.models import Video
from apps.content.models import Tag

class APITestCase(TestCase):

    def setUp(self, create_app_user=False, create_artist=False, create_videos=False):
        self.client = APIClient()
        if create_app_user:
            self.app_user = User.objects.create(username='random', is_active=True)

    def auth_with_app_user(self):
        assert self.app_user
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.app_user.jwt_token)

    def assert_equal(self, a, b):
        assert a == b, f'assertion error on {a} == {b}'

    def assert_not_equal(self, a, b):
        assert a != b, f'assertion error on {a} != {b}'

    def assert_endswith(self, a, b):
        assert a.endswith(b), f'assertion error on {a}.endswith({b})'

    def assert_fields_exist(self, data={}, fields_list=[]):
        for field in fields_list:
            assert field in data, f"Expected field `{field}` in user response data, but not found."
        assert 'password' not in data

    def assert_staus_200_OK(self, response):
        assert response.status_code == status.HTTP_200_OK, \
            f"Expected status 200 OK, but received {response.status_code}" \
            f" on route {response.request['PATH_INFO']}" \
            f" in {self.__class__.__name__}"

    def assert_staus_201_CREATED(self, response):
        assert response.status_code == status.HTTP_201_CREATED, \
            f"Expected status 201 CREATED, but received {response.status_code}" \
            f" on route {response.request['PATH_INFO']}" \
            f" in {self.__class__.__name__}"

    def assert_staus_204_DELETED(self, response):
        assert response.status_code == status.HTTP_204_NO_CONTENT, \
            f"Expected status 204 NO CONTENT, but received {response.status_code}" \
            f" on route {response.request['PATH_INFO']}" \
            f" in {self.__class__.__name__}"
