from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AbstractUser

from apps.public.views.helpers.main_content_view import MainContentView


class LoginView(auth_views.LoginView):
    template_name = "account/login.html"
    redirect_authenticated_user = True


# Assuming AbstractUser is being used directly
class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = AbstractUser
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(
                attrs={"placeholder": "John", "class": "input"}
            ),
            "last_name": forms.TextInput(
                attrs={"placeholder": "Smith", "class": "input"}
            ),
            "email": forms.EmailInput(attrs={"disabled": True, "class": "input"}),
        }


class SettingsView(LoginRequiredMixin, MainContentView):
    template_name = "account/settings.html"
    # url = "/account/settings/"

    def get(self, request, *args, **kwargs):
        if "user_form" not in self.context:
            self.context["user_form"] = UserSettingsForm(instance=request.user)
        if "password_form" not in self.context:
            self.context["password_form"] = auth_views.PasswordChangeForm(
                user=request.user
            )
        return self.render()

    def post(self, request, *args, **kwargs):
        if "first_name" in request.POST:
            self.context["user_form"] = UserSettingsForm(
                request.POST, instance=request.user
            )
            if self.context["user_form"].is_valid():
                self.context["user_form"].save()
                messages.success(request, "User settings updated.")
        else:
            self.context["password_form"] = auth_views.PasswordChangeForm(
                request.user, request.POST
            )
            if self.context["password_form"].is_valid():
                self.context["password_form"].save()
                update_session_auth_hash(request, self.context["password_form"].user)
                messages.success(request, "Password updated.")
            else:
                messages.error(
                    request,
                    "Password not updated.",
                )

        return self.get(request, *args, **kwargs)


class HomeView(LoginRequiredMixin, MainContentView):
    template_name = "pages/home.html"
    # url = "/"  #  root of host
