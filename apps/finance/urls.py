from django.urls import path
from django.views.generic import TemplateView

from apps.finance.views import ticker

app_name = "finance"

urlpatterns = [

    # THINGS THAT NEED A UI
    path('ticker/<str:ticker_symbol>', ticker.Ticker.as_view(), name='ticker')

]
