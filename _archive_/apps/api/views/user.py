import hashlib
from typing import Union

from django.contrib.auth import login, authenticate
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import BooleanFilter, FilterSet
from rest_framework import mixins, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, BasePermission, IsAdminUser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from _archive_.apps.api.serializers.user import UserSerializer
from _archive_.apps.user.models import User
from _archive_.settings import logger
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


class CreateOnly(BasePermission):
    def has_permission(self, request, view):
        logger.info(request.resolver_match.url_name)
        return all([
            request.resolver_match.url_name == "user-list",
            request.method in "POST"
        ])  # allow if creating a new user


class UserFilter(FilterSet):
    is_beta_tester = BooleanFilter(method='filter_is_beta_tester', label='is_beta_tester')
    is_premium_member = BooleanFilter(method='filter_is_premium_member', label='is_premium_member')

    # slipdate = DateTimeFilter(method='filter_slipdate')
    # netweight = NumberFilter(method='filter_netweight')

    class Meta:
        model = User
        fields = ('is_active', 'is_beta_tester', 'is_premium_member',)

    def filter_is_beta_tester(self, queryset, name, value):
        return queryset.filter(beta_tester_since__isnull=False)

    def filter_is_premium_member(self, queryset, name, value):
        return queryset.exclude(
            premium_member_since__isnull=True
        ).filter(
            premium_member_since__lte=timezone.now()
        ).filter(
            Q(premium_member_until__gte=timezone.now()) | Q(premium_member_until__isnull=True)
        )


class UserViewSet(mixins.CreateModelMixin,
                  mixins.RetrieveModelMixin,
                  mixins.UpdateModelMixin,
                  # mixins.DestroyModelMixin,
                  mixins.ListModelMixin,
                  GenericViewSet):
    """
    LIST endpoints:

    - `/users/` ONLY superusers accounts can GET list of all users

    GET endpoint:

    - `/users/123/` returns user object where user_id=123

    POST endpoint:

    - `/users/` with JSON `{"username": char_string}`

        - use char_string="random" and system will assign a random username of 12 chars
        - limit 150 characters or fewer. Letters, digits and @.+-_ only.
        - can use email address as username 🤔
        - if username already exists, API returns `400` error with JSON `{"username": ["A user with that username already exists."]}`

    """
    permission_classes = [Union[CreateOnly, IsAuthenticated, IsAdminUser]]

    queryset = User.objects.all()
    serializer_class = UserSerializer

    filterset_class = UserFilter
    # filter_backends = (DjangoFilterBackend,)

    def update(self, request, pk=None, *args, **kwargs):
        if not pk:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

        response = super().update(request, pk, *args, **kwargs)

        user = get_object_or_404(User, id=pk)

        # todo: remove validation if email address is changed

        # prevent creating 2 accounts with the same verified email
        user_account_with_same_email = User.objects.filter(email=user.email, email_is_verified=True).first()
        if user_account_with_same_email and user_account_with_same_email.id != int(pk):
            return Response(status=status.HTTP_401_UNAUTHORIZED, data={"status": "user already registered, please login"})

        # if this was an attempt to update an unverified email address
        # if "email" in request.data and not user.email_is_verified:
        #
        #     # send an email to the user with a four digit code:
        #     email = SIBEmail(to_user=user, template_name="account-activate-mobile")
        #     email.to_email = user.email # because email address is not yet validated, so force it
        #     email.data['four_digit_code'] = user.four_digit_login_code
        #     email.data['iOS_login_deep_link'] = user.iOS_login_deep_link
        #     email.send()

        if "is_agreed_to_terms" in request.data:
            user.is_agreed_to_terms = True
            user.save()

        elif "is_recently_reviewed_app" in request.data:
            user.is_recently_reviewed_app = True
            user.save()

        if request.data.get("FB_user_id", None) and request.data.get("FB_user_access_token", None):
            # todo: validate the FB_user_access_token is real
            user.email_is_verified = True
            user.save()
            # todo: set email_is_verified = True in the response data

        return response


    def get_queryset(self):
        if self.request.user.is_superuser:
            return User.objects.all()
        else:
            return User.objects.filter(id=self.request.user.id)

    # @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    # def request_login_code(self, request, pk=None, *args, **kwargs):
    #     """
    #     use this endpoint to request a code sent to user to login to the app, equivalent to a one-time password
    #     will fail if the user is not active or the email is not verified
    #     """
    #     if 'email' in request.data:
    #         user = User.objects.filter(email=request.data['email'], email_is_verified=True).first()
    #     elif 'username' in request.data:
    #         user = User.objects.filter(username=request.data['username'], email__isnull=False, email_is_verified=True).first()
    #     else:
    #         user = User.objects.filter(id=pk, email__isnull=False, email_is_verified=True).first()
    #
    #     if user and user.is_active:
    #         email = SIBEmail(to_user=user, template_name="account-login-code")
    #         email.to_email = user.email  # because email address is not yet validated, so force it
    #         email.data['four_digit_code'] = user.four_digit_login_code
    #         email.data['iOS_login_deep_link'] = user.iOS_login_deep_link
    #         email.send()
    #         return Response(status=status.HTTP_202_ACCEPTED, data={"status": f"code sent to {user.email}"})
    #     else:
    #         return Response(status=status.HTTP_406_NOT_ACCEPTABLE,
    #                         data={"status": f"nope. user either not found, not active, or unverified email"})


    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def authenticate_with_password_or_code(self, request, pk=None, *args, **kwargs):
        """
        expecting 'four_digit_code' as a string and optionally username, email, or id in endpoint
        use this endpoint to login with a password or a code that was emailed to the user
        correct code passes and validates email, incorrect code fails
        correct password passes, unless email is not verified
        will also fail if not user.is_active (eg. blocked or banned)
        """

        user_accounts = [
            User.objects.filter(id=pk).first(),
            User.objects.filter(email=request.data.get('email', "not@email"), email_is_verified=True).first(),
            User.objects.filter(email=request.data.get('email', "not@email"), email_is_verified=False).first(),
            User.objects.filter(username=request.data.get('username', ""), email__isnull=False, email_is_verified=True).first(),
        ]
        success, auth_user = False, None

        if not any([user.is_active for user in user_accounts if user]):
            return Response(status=status.HTTP_401_UNAUTHORIZED, data={"status": "active account not found"})

        for user in user_accounts:
            if user and user.is_active:

                # validate four_digit_code and mark email address as verified
                if str(request.data.get('four_digit_code', "VOID")).strip() == str(user.four_digit_login_code):
                    success = True
                    auth_user = user

                elif request.data.get('password') and user.email_is_verified:
                    auth_user = authenticate(request, username=user.username, password=request.data.get('password'))
                    if auth_user:
                        success = True

                if success:
                    auth_user.email_is_verified = True
                    auth_user.last_login = timezone.now()  # note this will cause the four_digit_code to change 👍
                    auth_user.save()
                    serializer = self.get_serializer(auth_user, many=False)
                    return Response(serializer.data)

        if not success and not request.data.get('four_digit_code', None) and not user.email_is_verified:
            return Response(status=status.HTTP_401_UNAUTHORIZED,
                            data={"status": "email not verified, verify email and try again"})

        elif not success:
            return Response(status=status.HTTP_401_UNAUTHORIZED, data={"status": "password or code incorrect"})

        return Response(status=status.HTTP_401_UNAUTHORIZED, data={"status": "unknown authentication error"})
