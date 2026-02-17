from django.urls import path
from rest_framework import routers

from apps.api.views.worker_views import audio_callback, pending_audio

app_name = "api"
api_router = routers.DefaultRouter()

urlpatterns = api_router.urls + [
    path(
        "podcast/pending-audio/",
        pending_audio,
        name="pending-audio",
    ),
    path(
        "podcast/episodes/<int:episode_id>/audio-callback/",
        audio_callback,
        name="audio-callback",
    ),
]
