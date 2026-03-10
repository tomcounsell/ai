from django.urls import path

from apps.podcast.views import (
    ArtifactContentView,
    EpisodeCreateView,
    EpisodeDetailView,
    EpisodeReportView,
    EpisodeSourcesView,
    EpisodeUpdateFieldView,
    EpisodeWorkflowView,
    PasteResearchView,
    PodcastDetailView,
    PodcastEditView,
    PodcastFeedView,
    PodcastListView,
    RegenerateCoverArtView,
    UploadCoverArtView,
)
from apps.podcast.workflow import WorkflowPollView

app_name = "podcast"

urlpatterns = [
    path("", PodcastListView.as_view(), name="list"),
    path("<slug:slug>/", PodcastDetailView.as_view(), name="detail"),
    path("<slug:slug>/edit/", PodcastEditView.as_view(), name="edit"),
    path("<slug:slug>/feed.xml", PodcastFeedView.as_view(), name="feed"),
    path("<slug:slug>/new/", EpisodeCreateView.as_view(), name="episode_create"),
    path(
        "<slug:slug>/<slug:episode_slug>/edit/<int:step>/status/",
        WorkflowPollView.as_view(),
        name="episode_workflow_poll",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/edit/<int:step>/",
        EpisodeWorkflowView.as_view(),
        name="episode_workflow",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/edit/<int:step>/update/",
        EpisodeWorkflowView.as_view(),
        name="episode_brief_update",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/artifacts/<int:artifact_id>/",
        ArtifactContentView.as_view(),
        name="artifact_content",
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
    path(
        "<slug:slug>/<slug:episode_slug>/update-field/",
        EpisodeUpdateFieldView.as_view(),
        name="episode_update_field",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/paste-research/",
        PasteResearchView.as_view(),
        name="paste_research",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/regenerate-cover/",
        RegenerateCoverArtView.as_view(),
        name="regenerate_cover_art",
    ),
    path(
        "<slug:slug>/<slug:episode_slug>/upload-cover/",
        UploadCoverArtView.as_view(),
        name="upload_cover_art",
    ),
]
