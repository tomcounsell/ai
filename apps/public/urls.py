from django.conf.urls import url
from django.urls import path
from django.views.generic import TemplateView

from apps.public.views import bing

app_name = "public"

urlpatterns = [

    # USER ACCOUNT
    # path('account', account.AccountView.as_view(), name='account'),


    # THINGS THAT NEED A UI
    path('bing', bing.BingView.as_view(), name='bing')

]
