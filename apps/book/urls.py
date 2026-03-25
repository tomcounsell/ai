from django.urls import path

from apps.book.views import AnnouncementListView, LandingView

app_name = "book"

urlpatterns = [
    path("", LandingView.as_view(), name="landing"),
    path("announcements/", AnnouncementListView.as_view(), name="announcements"),
]
