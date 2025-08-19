from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

from apps.common.models.team import Team


class SessionStateMixin:
    """
    Base mixin for managing user session state.

    This mixin handles common session operations like tracking login state
    and initializing context.
    """

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)

        # Ensure context is initialized
        self.context = getattr(self, "context", {})

        # Pass the flag to context before clearing it
        self.context["just_logged_in"] = request.session.get("just_logged_in", False)
        if request.session.get("just_logged_in"):
            del request.session["just_logged_in"]

    def handle_unauthenticated(self, request):
        """Redirect unauthenticated users to the login page with next parameter."""
        return redirect(f"{settings.LOGIN_URL}?next={request.get_full_path()}")


class TeamSessionMixin(SessionStateMixin):
    """
    Mixin for views that require team context.

    This mixin loads the current team from the session or URL parameters
    and ensures users have access to the requested team.
    """

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)

        self.team = None

        if request.user.is_anonymous or not request.user.is_authenticated:
            # Exit if user is not authenticated
            return

        # Attempt to get team from URL kwargs or session
        team_id = kwargs.get("team_id") or request.session.get("team_id")
        if team_id:
            self.team = Team.objects.filter(id=team_id).first()
            # Ensure user has access to this team
            if self.team and not self.team.user_is_member(request.user):
                self.team = None

        # If no team yet and user has teams, get their first team
        if not self.team and request.method != "POST" and request.user.teams.exists():
            self.team = request.user.teams.first()

        # Store team ID in session and add team to context
        if self.team:
            request.session["team_id"] = self.team.id
            self.context["team"] = self.team
        elif team := request.user.teams.first():
            # Store first team in session
            request.session["team_id"] = team.id
        else:
            # No teams, clear from session
            try:
                del request.session["team_id"]
            except KeyError:
                pass

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_anonymous or not request.user.is_authenticated:
            return self.handle_unauthenticated(request)

        # Check if teams are required
        require_team = getattr(self, "require_team", True)

        if require_team and not request.user.teams.exists():
            # Exclude onboarding views from the redirect
            from apps.public.views import onboarding

            if hasattr(onboarding, "TeamOnboardingView") and not isinstance(
                self, (onboarding.TeamOnboardingView,)
            ):
                return self.handle_no_team(request)

        return super().dispatch(request, *args, **kwargs)

    def handle_no_team(self, request):
        """Redirect users without teams to the team creation view."""
        messages.info(request, "Let's set up your team first.")
        return redirect("public:team-create")
