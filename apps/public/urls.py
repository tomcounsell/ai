from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from .views import ComponentExamplesView, account, pages
from .views.components.oob_examples import urlpatterns as oob_urlpatterns
from .views.teams.member_views import (
    add_team_member,
    change_member_role,
    leave_team,
    remove_team_member,
)
from .views.teams.team_views import (
    TeamCreateView,
    TeamDeleteView,
    TeamDetailView,
    TeamListView,
    TeamUpdateView,
)

app_name = "public"

urlpatterns = []

# Home page
urlpatterns += [
    path(
        "",
        account.HomeView.as_view(),
        name="home",
    ),
]

# Example pages
urlpatterns += [
    # Landing page
    path(
        "landing/",
        pages.LandingView.as_view(),
        name="landing",
    ),
    # Pricing page
    path(
        "pricing/",
        pages.PricingView.as_view(),
        name="pricing",
    ),
    # Blog pages
    path(
        "blog/",
        pages.BlogView.as_view(),
        name="blog",
    ),
    path(
        "blog/<slug:slug>/",
        pages.BlogPostView.as_view(),
        name="blog-post",
    ),
]

# Auth and Accounts
urlpatterns += [
    path(
        "account/login",
        account.LoginView.as_view(),
        name="account-login",
    ),
    path(
        "account/logout",
        auth_views.LogoutView.as_view(next_page="/"),
        name="account-logout",
    ),
    path(
        "account/settings",
        account.SettingsView.as_view(),
        name="account-settings",
    ),
    # path(
    #     "account/create",
    #     account.AccountCreateView.as_view(),
    #     name="account-create",
    # ),
    # Password reset routes
    path(
        "account/password/reset/",
        auth_views.PasswordResetView.as_view(
            template_name="account/password/reset.html",
            email_template_name="account/password/reset_email.html",
            success_url=reverse_lazy("public:password-reset-done"),
        ),
        name="password-reset",
    ),
    path(
        "account/password/reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="account/password/reset_done.html"
        ),
        name="password-reset-done",
    ),
    path(
        "account/password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="account/password/reset_confirm.html",
            success_url=reverse_lazy("public:password-reset-complete"),
        ),
        name="password-reset-confirm",
    ),
    path(
        "account/password/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="account/password/reset_complete.html"
        ),
        name="password-reset-complete",
    ),
    # Password change routes (for authenticated users)
    path(
        "account/password/change/",
        auth_views.PasswordChangeView.as_view(
            template_name="account/password/change.html",
            success_url=reverse_lazy("public:password-change-done"),
        ),
        name="password-change",
    ),
    path(
        "account/password/change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="account/password/change_done.html"
        ),
        name="password-change-done",
    ),
]

# Team URLs
urlpatterns += [
    path("team/", TeamListView.as_view(), name="team-list"),
    path("team/create/", TeamCreateView.as_view(), name="team-create"),
    path("team/<slug:team_slug>/", TeamDetailView.as_view(), name="team-detail"),
    path("team/<slug:team_slug>/edit/", TeamUpdateView.as_view(), name="team-edit"),
    path("team/<slug:team_slug>/delete/", TeamDeleteView.as_view(), name="team-delete"),
    # Team membership URLs
    path("team/<slug:team_slug>/members/add/", add_team_member, name="add-team-member"),
    path(
        "team/<slug:team_slug>/members/<int:member_id>/change-role/",
        change_member_role,
        name="change-member-role",
    ),
    path(
        "team/<slug:team_slug>/members/<int:member_id>/remove/",
        remove_team_member,
        name="remove-team-member",
    ),
    path("team/<slug:team_slug>/leave/", leave_team, name="leave-team"),
]


# Component Examples
urlpatterns += [
    path("ui/examples/", ComponentExamplesView.as_view(), name="ui-examples"),
]

# OOB Examples
urlpatterns += oob_urlpatterns
