"""
Team model for organizing users with roles and permissions.
"""

from enum import Enum

from django.conf import settings
from django.db import models
from django.db.models import QuerySet

from apps.common.behaviors.timestampable import Timestampable


class Role(Enum):
    """Enum defining possible roles in a team."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"

    @classmethod
    def choices(cls):
        """Return role choices for model field."""
        return [(role.value, role.name.title()) for role in cls]


class Team(Timestampable, models.Model):
    """
    A team represents a group of users who collaborate together.

    Teams have members with different roles (owner, admin, member)
    that determine their permissions within the team.

    Attributes:
        name (str): The team's display name
        slug (str): URL-friendly identifier, unique
        description (str): Optional team description
        is_active (bool): Whether the team is active
        members (M2M): Users who are members of this team
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)

    # M2M relationship with User through TeamMember
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="TeamMember", related_name="teams"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_owners(self) -> QuerySet:
        """Return QuerySet of team owners."""
        return self.members.filter(teammember__role=Role.OWNER.value)

    def get_admins(self) -> QuerySet:
        """Return QuerySet of team admins (including owners)."""
        return self.members.filter(
            teammember__role__in=[Role.OWNER.value, Role.ADMIN.value]
        )

    def user_is_member(self, user) -> bool:
        """Check if user is a member of this team."""
        if not user or not user.is_authenticated:
            return False
        return self.members.filter(id=user.id).exists()

    def user_can_manage(self, user) -> bool:
        """Check if user can manage team (admin or owner)."""
        if not user or not user.is_authenticated:
            return False
        return self.teammember_set.filter(
            user=user, role__in=[Role.OWNER.value, Role.ADMIN.value]
        ).exists()

    def user_can_edit(self, user) -> bool:
        """Check if user can edit team details (admin or owner)."""
        return self.user_can_manage(user)

    def user_can_delete(self, user) -> bool:
        """Check if user can delete team (owner only)."""
        if not user or not user.is_authenticated:
            return False
        return self.teammember_set.filter(user=user, role=Role.OWNER.value).exists()


class TeamMember(Timestampable, models.Model):
    """
    Represents a user's membership in a team with a specific role.

    This is the through model for the Team-User M2M relationship.

    Attributes:
        team (Team): The team this membership is for
        user (User): The user who is a member
        role (str): The user's role in the team (owner, admin, member)
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=20, choices=Role.choices(), default=Role.MEMBER.value
    )

    class Meta:
        unique_together = ("team", "user")
        ordering = ["team", "role", "user"]

    def __str__(self) -> str:
        return f"{self.user} ({self.get_role_display()}) in {self.team}"

    @property
    def is_owner(self) -> bool:
        """Check if member has owner role."""
        return self.role == Role.OWNER.value

    @property
    def is_admin(self) -> bool:
        """Check if member has admin or owner role."""
        return self.role in [Role.OWNER.value, Role.ADMIN.value]
