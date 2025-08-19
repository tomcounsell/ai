from django.conf import settings
from django.urls import reverse
from icecream import ic

from apps.common.models import User
from apps.common.models.team import Role
from apps.integration.loops.client import LoopsClient


def send_password_reset_email(user: User, reset_url: str):
    """
    Send a password reset email

    :param User user: common.User object
    :param str reset_url: The URL to redirect the user to reset their password
    """
    # For testing, use debug_mode=True when in test environment
    is_test = settings.TESTING if hasattr(settings, "TESTING") else False
    loops_client = LoopsClient(debug_mode=is_test)
    loops_client.transactional_email(
        to_email=user.email,
        transactional_id="__loops_email_id__",
        data_variables={
            "passwordresetlink": reset_url,
        },
    )


def send_login_code_email(user: User, next_url=None):
    """
    Send a login URL via Loops.

    Args:
        user (User): The user to send the login email to
        next_url (str, optional): URL to redirect to after successful login
    """
    # Construct the login URL with code as parameter
    login_url = user.get_login_url(next_url)
    ic(f"Loops sending login link: {login_url} to {user.email}")

    # For testing, use debug_mode=True when in test environment
    is_test = settings.TESTING if hasattr(settings, "TESTING") else False
    loops_client = LoopsClient(debug_mode=is_test)
    loops_client.transactional_email(
        to_email=user.email,
        transactional_id="__loops_login_code_id__",
        data_variables={
            "login_url": login_url,
        },
    )


def send_team_membership_email(membership):
    """
    Send a team membership email via Loops.

    This function sends an email to a user about their membership in a team.
    The function assumes the user account and membership have already been created.

    Example email:

    Subject: Welcome to [Team Name]

    Hello [User Name],

    You've been added to [Team Name] as a [Role].

    [Parent Team: Organization Name] (if applicable)

    Click the link below to log in to your account:
    [Login Link]

    If you have any questions, please contact [Team Admin Email].

    Best regards,
    The ProjectName Team

    Args:
        membership: The Membership instance with user and team information
    """
    try:
        # For testing, use debug_mode=True when in test environment
        is_test = settings.TESTING if hasattr(settings, "TESTING") else False
        loops_client = LoopsClient(debug_mode=is_test)

        # Get necessary data
        team = membership.team
        user = membership.user
        email = user.email

        # Construct login URL with https://
        hostname = (
            settings.HOSTNAME
            if settings.HOSTNAME.startswith("https://")
            else f"https://{settings.HOSTNAME}"
        )
        login_url = f"{hostname}{reverse('public:account-login')}"

        # Get parent team name if such a field exists
        parent_team_name = getattr(team, "parent_team", None)
        if parent_team_name:
            parent_team_name = parent_team_name.name
        else:
            parent_team_name = ""

        # Get team admin for contact info
        team_admin = team.teammember_set.filter(role=Role.ADMIN.value).first()
        admin_email = team_admin.user.email if team_admin else ""

        # Get human-readable role name
        role_display = dict(Role.choices).get(membership.role, "Member")

        # Send transactional email with login link
        loops_client.transactional_email(
            # You need to create this template in Loops and replace with actual ID
            transactional_id="__loops_team_membership_id__",
            to_email=email,
            data_variables={
                "login_url": login_url,
                "team_name": team.name,
                "parent_team_name": parent_team_name,
                "role": role_display,
                "admin_email": admin_email,
                "user_name": user.get_full_name() or user.email,
            },
        )
        return True
    except Exception as e:
        # Log but don't raise
        print(f"Failed to send team membership email to {email}: {str(e)}")
        return False
