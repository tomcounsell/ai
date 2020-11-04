from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import ModelForm
from django.shortcuts import render
from django.views.generic import View

from apps.common.models.address import AddressForm
from apps.user.models import User


class UserForm(ModelForm):
    class Meta:
        model = User
        fields = [
            'phone_number',
        ]


class AccountView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        self.context["user_form"] = UserForm(instance=request.user)
        return render(request, 'account.html', self.context)

    def post(self, request, *args, **kwargs):

        user_form = UserForm(request.POST, instance=request.user)
        if user_form.is_valid():
            user = user_form.save()
            user.save()
            self.context["user_form"] = user_form

        address_form = AddressForm(request.POST, instance=request.user.address)
        if address_form.is_valid():
            user.address = address_form.save()
            user.address.save()
            self.context["address_form"] = address_form

        if user_form.is_valid() and address_form.is_valid():
            messages.success(request, "Account details saved.")
        else:
            messages.warning(request, "One or more errors. See below")

        return render(request, 'account.html', self.context)
