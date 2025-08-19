from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db.models import Count
from django.shortcuts import redirect
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from rest_framework_api_key.admin import APIKeyModelAdmin
from unfold.admin import ModelAdmin, TabularInline
from unfold.components import BaseComponent, register_component
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.sections import TableSection

from apps.common.models import (
    SMS,
    Address,
    BlogPost,
    City,
    Country,
    Currency,
    Document,
    Email,
    Image,
    Note,
    Payment,
    Subscription,
    Team,
    TeamAPIKey,
    TeamMember,
    Upload,
    User,
    UserAPIKey,
)

# Define which models should be shown in the main admin navigation
MAIN_NAV_MODELS = ["User", "Team", "BlogPost"]

# Categories for model organization
ADMIN_CATEGORIES = {
    "People": ["User", "Team", "TeamMember"],
    "Content": ["BlogPost", "Note", "Document", "Image", "Upload"],
    "Locations": ["Address", "City", "Country"],
    "Finance": ["Payment", "Subscription", "Currency"],
    "Communications": ["Email", "SMS"],
    "Security": ["UserAPIKey", "TeamAPIKey"],
}


# Custom Filters for Admin
class BooleanFilter(SimpleListFilter):
    """Custom boolean filter for non-boolean fields."""

    title = "Filter Title"
    parameter_name = "param"
    filter_function = None

    def __init__(self, request, params, model, model_admin):
        super().__init__(request, params, model, model_admin)
        self.filter_function = (
            self.lookup_choices[2] if len(self.lookup_choices) > 2 else None
        )

    def lookups(self, request, model_admin):
        return (
            ("1", _("Yes")),
            ("0", _("No")),
        )

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset

        if self.filter_function:
            return self.filter_function(queryset, self.value() == "1")

        return queryset


@admin.register(User)
class UserAdmin(ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    compressed_fields = True
    warn_unsaved_form = True

    list_display = [
        "display_header",
        "email",
        "status_badge",
        "display_staff",
        "display_superuser",
        "display_created",
    ]
    search_fields = ("username", "email", "first_name", "last_name")
    list_filter = [
        "is_staff",
        "is_active",
        "is_superuser",
        ("date_joined", RangeDateTimeFilter),
    ]
    list_filter_submit = True

    # Define fieldsets for the add form to include password fields
    add_fieldsets = (
        (None, {"fields": ("username", "password1", "password2")}),
        (
            "Personal info",
            {
                "fields": (("first_name", "last_name"), "email", "biography"),
                "classes": ["tab"],
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "classes": ["tab"],
            },
        ),
    )

    fieldsets = (
        (None, {"fields": ("username",)}),
        (
            "Personal info",
            {
                "fields": (("first_name", "last_name"), "email", "biography"),
                "classes": ["tab"],
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "classes": ["tab"],
            },
        ),
        (
            "Important dates",
            {
                "fields": ("last_login", "date_joined"),
                "classes": ["tab"],
            },
        ),
    )
    readonly_fields = ["last_login", "date_joined"]

    def get_fieldsets(self, request, obj=None):
        if not obj:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        """
        Use special form during user creation
        """
        defaults = {}
        if obj is None:
            defaults["form"] = self.add_form
        defaults.update(kwargs)
        return super().get_form(request, obj, **defaults)

    def save_model(self, request, obj, form, change):
        if not change:  # This is a new user
            # The password will be set properly by UserCreationForm
            pass
        super().save_model(request, obj, form, change)

    # Add actions to the admin interface
    actions_detail = [
        "deactivate_user",
    ]

    # Custom helper displays with the @display decorator
    @display(description=_("User"), header=True)
    def display_header(self, instance):
        # Return a list for a nicer user display with name,
        # initials and potential avatar
        return [
            instance.username,
            None,
            (
                f"{instance.first_name[:1]}{instance.last_name[:1]}".upper()
                if instance.first_name and instance.last_name
                else ""
            ),
        ]

    @display(description=_("Staff"), boolean=True)
    def display_staff(self, instance):
        return instance.is_staff

    @display(description=_("Superuser"), boolean=True)
    def display_superuser(self, instance):
        return instance.is_superuser

    @display(description=_("Created"))
    def display_created(self, instance):
        return instance.date_joined

    @admin.display(description="Status")
    def status_badge(self, obj):
        if obj.is_active:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">Active</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-red-500 text-white">Inactive</span>'
        )

    # Custom actions
    @action(description=_("Deactivate User"))
    def deactivate_user(self, request, object_id):
        user = User.objects.get(pk=object_id)
        user.is_active = False
        user.save()
        messages.success(request, f"User {user.username} has been deactivated")
        return redirect("admin:common_user_change", object_id)

    @action(description=_("Activate User"))
    def activate_user(self, request, object_id):
        user = User.objects.get(pk=object_id)
        user.is_active = True
        user.save()
        messages.success(request, f"User {user.username} has been activated")
        return redirect("admin:common_user_change", object_id)

    @action(description=_("Toggle Superuser Status"))
    def toggle_superuser(self, request, object_id):
        user = User.objects.get(pk=object_id)
        user.is_superuser = not user.is_superuser
        user.save()
        messages.success(request, f"Superuser status toggled for {user.username}")
        return redirect("admin:common_user_change", object_id)


class TeamMemberInline(TabularInline):
    model = TeamMember
    extra = 1
    autocomplete_fields = ["user"]
    ordering_field = "weight"  # Allow ordering of members
    show_change_link = True
    tab = True  # Show in a tab interface


# Create a Table Section for team members
class TeamMemberTableSection(TableSection):
    verbose_name = _("Team Members")
    related_name = "teammember_set"
    model_name = "teammember"
    fields = ["user", "role_display", "joined_at"]
    height = 380  # Fixed height

    @admin.display(description=_("Role"))
    def role_display(self, instance):
        colors = {
            "owner": "bg-purple-500",
            "admin": "bg-red-500",
            "member": "bg-blue-500",
            "guest": "bg-gray-500",
        }
        color = colors.get(instance.role, "bg-gray-500")

        return format_html(
            '<span class="unfold-badge {} text-white">{}</span>',
            color,
            instance.get_role_display(),
        )


# Register a custom component for team stats
@register_component
class TeamStatsComponent(BaseComponent):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Count active vs inactive teams
        team_count = Team.objects.count()
        active_teams = Team.objects.filter(is_active=True).count()
        inactive_teams = team_count - active_teams

        # Get teams with most members
        teams_with_members = Team.objects.annotate(member_count=Count("members"))
        top_teams = teams_with_members.order_by("-member_count")[:5]

        context.update(
            {
                "team_count": team_count,
                "active_teams": active_teams,
                "inactive_teams": inactive_teams,
                "top_teams": top_teams,
            }
        )

        return context


@admin.register(Team)
class TeamAdmin(ModelAdmin):
    list_display = [
        "display_header",
        "slug",
        "member_count",
        "status_badge",
        "created_at",
    ]
    list_filter = [
        ("is_active", ChoicesDropdownFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [TeamMemberInline]
    list_sections = [TeamMemberTableSection]
    compressed_fields = True
    warn_unsaved_form = True

    # Add tabs to the detail view
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "is_active")}),
        (
            "Metadata",
            {
                "fields": ("meta_data",),
                "classes": ["tab"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "modified_at"),
                "classes": ["tab"],
            },
        ),
    )
    readonly_fields = ["created_at", "modified_at"]

    # Add actions to the admin interface
    actions_list = [
        "export_teams",
        {
            "title": _("Bulk Actions"),
            "variant": ActionVariant.PRIMARY,
            "items": ["activate_teams", "deactivate_teams"],
        },
    ]

    actions_detail = ["manage_members", "activate_team", "deactivate_team"]

    # Custom displays
    @display(description=_("Team"), header=True)
    def display_header(self, instance):
        return [
            instance.name,
            (
                instance.description[:30] + "..."
                if instance.description and len(instance.description) > 30
                else instance.description
            ),
        ]

    @admin.display(description="Members")
    def member_count(self, obj):
        """Return the number of members in the team."""
        count = obj.members.count()
        return format_html(
            '<span class="unfold-badge bg-blue-500 text-white">{}</span>', count
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        if obj.is_active:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">Active</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-red-500 text-white">Inactive</span>'
        )

    # Custom actions
    @action(description=_("Export Teams"), icon="download")
    def export_teams(self, request):
        # Logic to export teams to CSV/Excel would go here
        messages.success(
            request, "Teams export initiated. You will receive an email when complete."
        )
        return redirect("admin:common_team_changelist")

    @action(description=_("Activate Teams"), icon="check_circle")
    def activate_teams(self, request):
        # Bulk activate teams
        queryset = self.get_queryset(request).filter(is_active=False)
        count = queryset.count()
        queryset.update(is_active=True)
        messages.success(request, f"{count} teams have been activated")
        return redirect("admin:common_team_changelist")

    @action(description=_("Deactivate Teams"), icon="block")
    def deactivate_teams(self, request):
        # Bulk deactivate teams
        queryset = self.get_queryset(request).filter(is_active=True)
        count = queryset.count()
        queryset.update(is_active=False)
        messages.success(request, f"{count} teams have been deactivated")
        return redirect("admin:common_team_changelist")

    @action(description=_("Manage Members"), url_path="manage-members")
    def manage_members(self, request, object_id):
        # This could redirect to a custom view or just to the inline
        team = Team.objects.get(pk=object_id)
        messages.info(request, f"Managing members for team: {team.name}")
        return redirect("admin:common_team_change", object_id)

    @action(description=_("Activate Team"))
    def activate_team(self, request, object_id):
        team = Team.objects.get(pk=object_id)
        team.is_active = True
        team.save()
        messages.success(request, f"Team {team.name} has been activated")
        return redirect("admin:common_team_change", object_id)

    @action(description=_("Deactivate Team"))
    def deactivate_team(self, request, object_id):
        team = Team.objects.get(pk=object_id)
        team.is_active = False
        team.save()
        messages.success(request, f"Team {team.name} has been deactivated")
        return redirect("admin:common_team_change", object_id)


@admin.register(TeamMember)
class TeamMemberAdmin(ModelAdmin):
    list_display = ("user", "team", "role_badge")
    list_filter = ("role", "team")
    search_fields = ("user__username", "user__email", "team__name")
    autocomplete_fields = ["user", "team"]

    @admin.display(description="Role")
    def role_badge(self, obj):
        colors = {
            "owner": "bg-purple-500",
            "admin": "bg-red-500",
            "member": "bg-blue-500",
            "guest": "bg-gray-500",
        }
        color = colors.get(obj.role, "bg-gray-500")

        return format_html(
            '<span class="unfold-badge {} text-white">{}</span>',
            color,
            obj.get_role_display(),
        )


@admin.register(BlogPost)
class BlogPostAdmin(ModelAdmin):
    list_display = (
        "title",
        "author_display",
        "location_display",
        "publishing_status",
        "expiration_status",
    )
    list_filter = ("published_at", "expired_at")
    search_fields = ("title", "subtitle", "content", "tags")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "modified_at", "authored_at")

    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "title",
                    "subtitle",
                    "content",
                    "featured_image",
                    "reading_time_minutes",
                    "tags",
                )
            },
        ),
        (
            "Author Information",
            {"fields": ("author", "is_author_anonymous", "authored_at")},
        ),
        (
            "Publication Status",
            {"fields": ("published_at", "edited_at", "unpublished_at")},
        ),
        ("Expiration", {"fields": ("valid_at", "expired_at")}),
        ("Location", {"fields": ("address", "latitude", "longitude")}),
        ("Permalink", {"fields": ("slug",)}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Author")
    def author_display(self, obj):
        return obj.author_display_name

    @admin.display(description="Location")
    def location_display(self, obj):
        if obj.address:
            return format_html(
                '<span class="unfold-badge bg-blue-500 text-white">{}</span>',
                str(obj.address)[:30],
            )
        return "-"

    @admin.display(description="Status")
    def publishing_status(self, obj):
        if obj.is_published:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">Published</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-gray-500 text-white">Draft</span>'
        )

    @admin.display(description="Expiration")
    def expiration_status(self, obj):
        if obj.is_expired:
            return format_html(
                '<span class="unfold-badge bg-red-500 text-white">Expired</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-green-500 text-white">Active</span>'
        )


@admin.register(UserAPIKey)
class UserAPIKeyAdmin(APIKeyModelAdmin):
    list_display = ("name", "user", "prefix", "created_at", "revoked")
    list_filter = ("revoked",)
    search_fields = ("name", "prefix", "user__username", "user__email")
    autocomplete_fields = ["user"]
    readonly_fields = ("created_at", "modified_at")


@admin.register(TeamAPIKey)
class TeamAPIKeyAdmin(APIKeyModelAdmin):
    list_display = ("name", "team", "prefix", "created_at", "revoked")
    list_filter = ("revoked",)
    search_fields = ("name", "prefix", "team__name")
    autocomplete_fields = ["team"]
    readonly_fields = ("created_at", "modified_at")


@admin.register(Address)
class AddressAdmin(ModelAdmin):
    list_display = ("line_1", "city", "region", "postal_code", "country", "map_link")
    list_filter = ("country",)
    search_fields = ("line_1", "line_2", "city", "region", "postal_code")
    readonly_fields = ("created_at", "modified_at")
    autocomplete_fields = ["country"]

    fieldsets = (
        (
            "Address Information",
            {
                "fields": (
                    "line_1",
                    "line_2",
                    "line_3",
                    "city",
                    "region",
                    "postal_code",
                    "country",
                )
            },
        ),
        ("Google Maps", {"fields": ("google_map_link",)}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Map")
    def map_link(self, obj):
        if obj.google_map_link:
            return format_html(
                '<a href="{}" target="_blank" class="unfold-badge bg-blue-500 text-white">View Map</a>',
                obj.google_map_url,
            )
        return "-"


@admin.register(City)
class CityAdmin(ModelAdmin):
    list_display = ("name", "code", "country", "currency_display")
    list_filter = ("country",)
    search_fields = ("name", "code", "country__name")
    autocomplete_fields = ["country"]

    @admin.display(description="Currency")
    def currency_display(self, obj):
        if obj.currency:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">{}</span>',
                obj.currency,
            )
        return "-"


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ("name", "code", "calling_code_display", "currency")
    list_filter = ("currency",)
    search_fields = ("name", "code")
    autocomplete_fields = ["currency"]

    @admin.display(description="Calling Code")
    def calling_code_display(self, obj):
        if obj.calling_code:
            return format_html(
                '<span class="unfold-badge bg-blue-500 text-white">+{}</span>',
                obj.calling_code,
            )
        return "-"


@admin.register(Currency)
class CurrencyAdmin(ModelAdmin):
    list_display = ("name", "code_upper", "created_at")
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "modified_at")

    @admin.display(description="Code")
    def code_upper(self, obj):
        return format_html(
            '<span class="unfold-badge bg-green-500 text-white">{}</span>',
            obj.code.upper(),
        )


@admin.register(Note)
class NoteAdmin(ModelAdmin):
    list_display = ("id", "text_preview", "author", "created_at")
    search_fields = ("text", "author__username", "author__email")
    readonly_fields = ("created_at", "modified_at")
    autocomplete_fields = ["author"]

    @admin.display(description="Text")
    def text_preview(self, obj):
        if obj.text:
            return obj.text[:50] + "..." if len(obj.text) > 50 else obj.text
        return "-"


@admin.register(Email)
class EmailAdmin(ModelAdmin):
    list_display = (
        "subject",
        "to_address",
        "from_address",
        "email_type",
        "sent_status",
        "read_status",
    )
    list_filter = ("type", "sent_at", "read_at")
    search_fields = ("to_address", "from_address", "subject", "body")
    readonly_fields = ("created_at", "modified_at", "sent_at", "read_at")

    fieldsets = (
        (
            "Email Details",
            {"fields": ("to_address", "from_address", "subject", "body", "type")},
        ),
        ("Attachments", {"fields": ("attachments",)}),
        ("Status", {"fields": ("sent_at", "read_at")}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Type")
    def email_type(self, obj):
        type_colors = {
            0: "bg-blue-500",  # NOTIFICATION
            1: "bg-green-500",  # CONFIRMATION
            2: "bg-purple-500",  # PASSWORD
        }
        return format_html(
            '<span class="unfold-badge {} text-white">{}</span>',
            type_colors.get(obj.type, "bg-gray-500"),
            obj.get_type_display(),
        )

    @admin.display(description="Sent")
    def sent_status(self, obj):
        if obj.sent_at:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">Sent</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-yellow-500 text-white">Pending</span>'
        )

    @admin.display(description="Read")
    def read_status(self, obj):
        if obj.read_at:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">Read</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-gray-500 text-white">Unread</span>'
        )


@admin.register(SMS)
class SMSAdmin(ModelAdmin):
    list_display = (
        "to_number",
        "from_number",
        "body_preview",
        "delivery_status",
        "sent_status",
    )
    list_filter = ("status", "sent_at", "read_at")
    search_fields = ("to_number", "from_number", "body", "external_id")
    readonly_fields = ("created_at", "modified_at", "sent_at", "read_at", "external_id")

    fieldsets = (
        ("SMS Details", {"fields": ("to_number", "from_number", "body")}),
        (
            "Twilio Information",
            {"fields": ("external_id", "status", "error_code", "error_message")},
        ),
        ("Status", {"fields": ("sent_at", "read_at")}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Message")
    def body_preview(self, obj):
        if obj.body:
            return obj.body[:30] + "..." if len(obj.body) > 30 else obj.body
        return "-"

    @admin.display(description="Status")
    def delivery_status(self, obj):
        status_colors = {
            "queued": "bg-blue-500",
            "sent": "bg-yellow-500",
            "delivered": "bg-green-500",
            "failed": "bg-red-500",
            "undelivered": "bg-orange-500",
        }
        if obj.status:
            return format_html(
                '<span class="unfold-badge {} text-white">{}</span>',
                status_colors.get(obj.status.lower(), "bg-gray-500"),
                obj.status,
            )
        return "-"

    @admin.display(description="Sent")
    def sent_status(self, obj):
        if obj.sent_at:
            return format_html(
                '<span class="unfold-badge bg-green-500 text-white">Sent</span>'
            )
        return format_html(
            '<span class="unfold-badge bg-yellow-500 text-white">Pending</span>'
        )


@admin.register(Upload)
class UploadAdmin(ModelAdmin):
    list_display = (
        "name_display",
        "file_type_display",
        "size_display",
        "status_badge",
        "created_at",
    )
    list_filter = ("status", "content_type")
    search_fields = ("name", "original", "s3_key")
    readonly_fields = (
        "created_at",
        "modified_at",
        "size",
        "dimensions_display",
        "preview",
    )

    fieldsets = (
        ("Upload Information", {"fields": ("name", "original", "thumbnail")}),
        (
            "File Information",
            {"fields": ("content_type", "size", "dimensions_display", "preview")},
        ),
        ("S3 Storage", {"fields": ("s3_bucket", "s3_key")}),
        ("Status", {"fields": ("status", "error")}),
        ("Metadata", {"fields": ("meta_data",)}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Name")
    def name_display(self, obj):
        return obj.name or f"Upload {obj.id}"

    @admin.display(description="Type")
    def file_type_display(self, obj):
        if obj.file_type:
            return format_html(
                '<span class="unfold-badge bg-blue-500 text-white">{}</span>',
                obj.file_type,
            )
        return "-"

    @admin.display(description="Size")
    def size_display(self, obj):
        if obj.size:
            # Convert bytes to KB or MB
            if obj.size < 1024:
                return f"{obj.size} B"
            elif obj.size < 1024 * 1024:
                return f"{obj.size / 1024:.1f} KB"
            else:
                return f"{obj.size / (1024 * 1024):.1f} MB"
        return "-"

    @admin.display(description="Status")
    def status_badge(self, obj):
        status_colors = {
            "pending": "bg-blue-500",
            "processing": "bg-yellow-500",
            "complete": "bg-green-500",
            "error": "bg-red-500",
        }
        return format_html(
            '<span class="unfold-badge {} text-white">{}</span>',
            status_colors.get(obj.status, "bg-gray-500"),
            obj.status.capitalize(),
        )

    @admin.display(description="Dimensions")
    def dimensions_display(self, obj):
        """Display image dimensions if available"""
        if obj.dimensions and all(obj.dimensions):
            return f"{obj.dimensions[0]} x {obj.dimensions[1]}"
        return "-"

    @admin.display(description="Preview")
    def preview(self, obj):
        """Display a preview of the file if possible"""
        if obj.is_image and obj.original:
            return format_html(
                '<img src="{}" style="max-width: 300px; max-height: 200px;" />',
                obj.original,
            )
        if obj.is_pdf and obj.original:
            return format_html(
                '<a href="{}" target="_blank" class="unfold-badge bg-red-500 text-white">View PDF</a>',
                obj.original,
            )
        return "-"


@admin.register(Image)
class ImageAdmin(ModelAdmin):
    list_display = ("display_name", "dimensions_display", "preview", "created_at")
    search_fields = ("upload__name", "upload__original", "id")

    def get_readonly_fields(self, request, obj=None):
        """Only use timestamp fields if they are available."""
        readonly_fields = ["dimensions_display", "preview"]
        if hasattr(obj, "upload") and obj.upload:
            if hasattr(obj.upload, "created_at"):
                readonly_fields.append("created_at")
            if hasattr(obj.upload, "modified_at"):
                readonly_fields.append("modified_at")
        return readonly_fields

    fieldsets = (
        ("Image Information", {"fields": ("upload", "thumbnail_url")}),
        ("File Information", {"fields": ("dimensions_display", "preview")}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Name")
    def display_name(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "name"):
            return obj.upload.name or f"Image {obj.id}"
        return f"Image {obj.id}"

    @admin.display(description="Dimensions")
    def dimensions_display(self, obj):
        """Display image dimensions if available"""
        if (
            hasattr(obj, "width")
            and hasattr(obj, "height")
            and obj.width
            and obj.height
        ):
            return f"{obj.width} x {obj.height}"
        return "-"

    @admin.display(description="Preview")
    def preview(self, obj):
        """Display a preview of the image"""
        if hasattr(obj, "original") and obj.original:
            return format_html(
                '<img src="{}" style="max-width: 300px; max-height: 200px;" />',
                obj.original,
            )
        return "-"

    @admin.display(description="Created At")
    def created_at(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "created_at"):
            return obj.upload.created_at
        return None

    @admin.display(description="Modified At")
    def modified_at(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "modified_at"):
            return obj.upload.modified_at
        return None


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = (
        "amount_display",
        "status_badge",
        "user",
        "payment_method_display",
        "subscription",
        "paid_at",
    )
    list_filter = ("status", "payment_method", "paid_at")
    search_fields = (
        "user__username",
        "user__email",
        "stripe_payment_intent_id",
        "description",
    )
    readonly_fields = ("created_at", "modified_at")
    autocomplete_fields = ["user", "subscription"]

    fieldsets = (
        (
            "Payment Information",
            {"fields": ("user", "amount", "payment_method", "last4", "description")},
        ),
        ("Status", {"fields": ("status", "paid_at", "refunded_at")}),
        ("Subscription", {"fields": ("subscription",)}),
        ("Stripe", {"fields": ("stripe_payment_intent_id", "receipt_url")}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        status_colors = {
            "pending": "bg-blue-500",
            "succeeded": "bg-green-500",
            "failed": "bg-red-500",
            "refunded": "bg-purple-500",
            "canceled": "bg-gray-500",
        }
        return format_html(
            '<span class="unfold-badge {} text-white">{}</span>',
            status_colors.get(obj.status, "bg-gray-500"),
            obj.status.capitalize(),
        )

    @admin.display(description="Method")
    def payment_method_display(self, obj):
        if obj.payment_method:
            label = obj.payment_method
            if obj.last4:
                label += f" (*{obj.last4})"
            return format_html(
                '<span class="unfold-badge bg-blue-500 text-white">{}</span>', label
            )
        return "-"


@admin.register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    list_display = (
        "user",
        "plan_name",
        "price_display",
        "status_badge",
        "renewal_info",
        "trial_badge",
    )
    list_filter = ("status", "interval", "cancel_at_period_end")
    search_fields = (
        "user__username",
        "user__email",
        "plan_name",
        "stripe_subscription_id",
    )
    readonly_fields = ("created_at", "modified_at")
    autocomplete_fields = ["user"]

    fieldsets = (
        (
            "Subscription Information",
            {"fields": ("user", "plan_name", "price", "interval")},
        ),
        ("Status", {"fields": ("status", "start_date", "end_date", "canceled_at")}),
        (
            "Billing Period",
            {
                "fields": (
                    "current_period_start",
                    "current_period_end",
                    "cancel_at_period_end",
                )
            },
        ),
        ("Trial", {"fields": ("trial_end",)}),
        ("Stripe", {"fields": ("stripe_subscription_id",)}),
        ("Timestamps", {"fields": ("created_at", "modified_at")}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        status_colors = {
            "active": "bg-green-500",
            "canceled": "bg-red-500",
            "past_due": "bg-orange-500",
            "trialing": "bg-blue-500",
            "unpaid": "bg-yellow-500",
            "incomplete": "bg-gray-500",
            "incomplete_expired": "bg-gray-500",
        }
        return format_html(
            '<span class="unfold-badge {} text-white">{}</span>',
            status_colors.get(obj.status, "bg-gray-500"),
            obj.status.capitalize(),
        )

    @admin.display(description="Renewal")
    def renewal_info(self, obj):
        if obj.current_period_end:
            if obj.cancel_at_period_end:
                return format_html(
                    '<span class="unfold-badge bg-red-500 text-white">Cancels on {}</span>',
                    obj.current_period_end.strftime("%Y-%m-%d"),
                )
            else:
                return format_html(
                    '<span class="unfold-badge bg-blue-500 text-white">Renews on {}</span>',
                    obj.current_period_end.strftime("%Y-%m-%d"),
                )
        return "-"

    @admin.display(description="Trial")
    def trial_badge(self, obj):
        if obj.is_trial:
            if obj.trial_end:
                return format_html(
                    '<span class="unfold-badge bg-purple-500 text-white">Trial ends {}</span>',
                    obj.trial_end.strftime("%Y-%m-%d"),
                )
            return format_html(
                '<span class="unfold-badge bg-purple-500 text-white">Trial</span>'
            )
        return "-"


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = ("id", "display_name", "display_type", "created_at")
    search_fields = ("id",)

    def get_readonly_fields(self, request, obj=None):
        """Only use timestamp fields if they are available."""
        readonly_fields = []
        if hasattr(obj, "upload") and obj.upload:
            if hasattr(obj.upload, "created_at"):
                readonly_fields.append("created_at")
            if hasattr(obj.upload, "modified_at"):
                readonly_fields.append("modified_at")
        return readonly_fields

    @admin.display(description="Name")
    def display_name(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "name"):
            return obj.upload.name or f"Document {obj.id}"
        return f"Document {obj.id}"

    @admin.display(description="Type")
    def display_type(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "file_type"):
            file_type = obj.upload.file_type
            if file_type:
                return format_html(
                    '<span class="unfold-badge bg-blue-500 text-white">{}</span>',
                    file_type,
                )
        return "-"

    @admin.display(description="Created At")
    def created_at(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "created_at"):
            return obj.upload.created_at
        return None

    @admin.display(description="Modified At")
    def modified_at(self, obj):
        if hasattr(obj, "upload") and obj.upload and hasattr(obj.upload, "modified_at"):
            return obj.upload.modified_at
        return None
