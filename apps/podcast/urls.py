from django.urls import path

from apps.podcast.views import (
    EpisodeDetailView,
    EpisodeReportView,
    EpisodeSourcesView,
    EpisodeWorkflowView,
    PodcastDetailView,
    PodcastFeedView,
    PodcastListView,
)

app_name = "podcast"

urlpatterns = [
    path("", PodcastListView.as_view(), name="list"),
    path("<slug:slug>/", PodcastDetailView.as_view(), name="detail"),
    path("<slug:slug>/feed.xml", PodcastFeedView.as_view(), name="feed"),
    path(
        "<slug:slug>/<slug:episode_slug>/edit/<int:step>/",
        EpisodeWorkflowView.as_view(),
        name="episode_workflow",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/",
        EpisodeDetailView.as_view(),
        name="episode_detail",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/report/",
        EpisodeReportView.as_view(),
        name="episode_report",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/sources/",
        EpisodeSourcesView.as_view(),
        name="episode_sources",
    ),
]
