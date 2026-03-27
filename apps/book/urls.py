from django.urls import path

from apps.book.views import (
    AnnouncementListView,
    ChapterDetailView,
    ChapterListView,
    EarlyReaderSignupView,
    LandingView,
    SignupSuccessView,
    ValorChatView,
    ValorSendMessageView,
)

app_name = "book"

urlpatterns = [
    path("", LandingView.as_view(), name="landing"),
    path("announcements/", AnnouncementListView.as_view(), name="announcements"),
    path("signup/", EarlyReaderSignupView.as_view(), name="signup"),
    path("signup/success/", SignupSuccessView.as_view(), name="signup_success"),
    path("chat/", ValorChatView.as_view(), name="chat"),
    path("chat/send/", ValorSendMessageView.as_view(), name="chat_send"),
    path("chapters/", ChapterListView.as_view(), name="chapters"),
    path("chapters/<int:pk>/", ChapterDetailView.as_view(), name="chapter_detail"),
]
