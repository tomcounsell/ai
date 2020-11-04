from django.conf.urls import url
from django.urls import path
from django.views.generic import TemplateView

from apps.dashboard.views import subscribers, analytics, account, search

app_name = "public"

urlpatterns = [

    # USER ACCOUNT
    url(r'^account$',
        account.AccountView.as_view(),
        name='account'),

]
