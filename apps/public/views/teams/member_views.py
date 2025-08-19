"""
Views for team membership management (invitations, joining, role changes).
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.common.models.team import Role, Team, TeamMember

User = get_user_model()


@login_required
@require_POST
def add_team_member(request, team_slug):
    """Add a new member to a team."""
    team = get_object_or_404(
        Team,
        slug=team_slug,
        teammember__user=request.user,
        teammember__role__in=[Role.OWNER.value, Role.ADMIN.value],
    )

    email = request.POST.get("email")
    role = request.POST.get("role", Role.MEMBER.value)

    # Validate the role
    if role not in [r.value for r in Role]:
        role = Role.MEMBER.value

    # Only owners can add other owners
    if role == Role.OWNER.value:
        user_is_owner = team.teammember_set.filter(
            user=request.user, role=Role.OWNER.value
        ).exists()
        if not user_is_owner:
            messages.error(request, "Only team owners can add new owners.")
            return redirect("public:team-detail", team_slug=team_slug)

    try:
        user = User.objects.get(email=email)

        # Check if user is already a member
        if team.members.filter(id=user.id).exists():
            messages.warning(
                request, f"{user.get_full_name()} is already a member of this team."
            )
            return redirect("public:team-detail", team_slug=team_slug)

        # Add the user to the team
        TeamMember.objects.create(team=team, user=user, role=role)

        messages.success(request, f"{user.get_full_name()} has been added to the team.")
    except User.DoesNotExist:
        messages.error(request, f"No user found with email {email}.")

    return redirect("public:team-detail", team_slug=team_slug)


@login_required
@require_POST
def change_member_role(request, team_slug, member_id):
    """Change a team member's role."""
    # Get the team and verify the current user has permission
    team = get_object_or_404(
        Team,
        slug=team_slug,
        teammember__user=request.user,
        teammember__role__in=[Role.OWNER.value, Role.ADMIN.value],
    )

    # Get the target member
    team_member = get_object_or_404(TeamMember, id=member_id, team=team)

    # Get the new role
    new_role = request.POST.get("role")
    if new_role not in [r.value for r in Role]:
        messages.error(request, "Invalid role specified.")
        return redirect("public:team-detail", team_slug=team_slug)

    # Only owners can set/remove the owner role
    if new_role == Role.OWNER.value or team_member.role == Role.OWNER.value:
        is_owner = team.teammember_set.filter(
            user=request.user, role=Role.OWNER.value
        ).exists()
        if not is_owner:
            messages.error(request, "Only team owners can change owner status.")
            return redirect("public:team-detail", team_slug=team_slug)

    # Cannot remove the last owner
    if team_member.role == Role.OWNER.value and new_role != Role.OWNER.value:
        owner_count = team.teammember_set.filter(role=Role.OWNER.value).count()
        if owner_count <= 1:
            messages.error(request, "Cannot remove the last owner of a team.")
            return redirect("public:team-detail", team_slug=team_slug)

    # Update the role
    team_member.role = new_role
    team_member.save()

    messages.success(
        request,
        f"{team_member.user.get_full_name()}'s role has been updated to {new_role}.",
    )
    return redirect("public:team-detail", team_slug=team_slug)


@login_required
@require_POST
def remove_team_member(request, team_slug, member_id):
    """Remove a member from a team."""
    # Get the team and verify the current user has permission
    team = get_object_or_404(
        Team,
        slug=team_slug,
        teammember__user=request.user,
        teammember__role__in=[Role.OWNER.value, Role.ADMIN.value],
    )

    # Get the target member
    team_member = get_object_or_404(TeamMember, id=member_id, team=team)

    # Only owners can remove owners
    if team_member.role == Role.OWNER.value:
        is_owner = team.teammember_set.filter(
            user=request.user, role=Role.OWNER.value
        ).exists()
        if not is_owner:
            messages.error(request, "Only team owners can remove other owners.")
            return redirect("public:team-detail", team_slug=team_slug)

    # Cannot remove the last owner
    if team_member.role == Role.OWNER.value:
        owner_count = team.teammember_set.filter(role=Role.OWNER.value).count()
        if owner_count <= 1:
            messages.error(request, "Cannot remove the last owner of a team.")
            return redirect("public:team-detail", team_slug=team_slug)

    # Cannot remove yourself
    if team_member.user == request.user:
        messages.error(request, "You cannot remove yourself from the team.")
        return redirect("public:team-detail", team_slug=team_slug)

    # Remove the member
    user_name = team_member.user.get_full_name()
    team_member.delete()

    messages.success(request, f"{user_name} has been removed from the team.")
    return redirect("public:team-detail", team_slug=team_slug)


@login_required
@require_POST
def leave_team(request, team_slug):
    """Leave a team (self-removal from a team)."""
    team = get_object_or_404(Team, slug=team_slug)

    try:
        membership = team.teammember_set.get(user=request.user)

        # Cannot leave if you're the last owner
        if membership.role == Role.OWNER.value:
            owner_count = team.teammember_set.filter(role=Role.OWNER.value).count()
            if owner_count <= 1:
                messages.error(
                    request,
                    "You are the only owner of this team. "
                    "Please assign another owner before leaving.",
                )
                return redirect("public:team-detail", team_slug=team_slug)

        # Remove the membership
        membership.delete()
        messages.success(request, f'You have left the team "{team.name}".')

    except TeamMember.DoesNotExist:
        messages.error(request, "You are not a member of this team.")

    return redirect("public:team-list")
