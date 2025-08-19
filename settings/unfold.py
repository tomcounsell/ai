"""
Django Unfold admin configuration settings
"""

from django.utils.translation import gettext_lazy as _

# Admin site configuration - used in urls.py
ADMIN_SITE_HEADER = "ProjectName Database"
ADMIN_SITE_TITLE = "ProjectName"
ADMIN_SITE_URL = None
ADMIN_INDEX_TITLE = "Database"

# Django Unfold settings - enhanced version
UNFOLD = {
    "SITE_TITLE": "ProjectName Admin",
    "SITE_HEADER": "ProjectName Database",
    "SITE_URL": "/",
    "SITE_ICON": None,
    "DASHBOARD_CALLBACK": "apps.common.admin_dashboard.get_admin_dashboard",
    "STYLES": [
        "/static/css/output.css",
    ],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
    },
    # Tabs configuration for detail pages
    "TABS": [
        {
            "model": "common.User",
            "items": [
                {
                    "title": _("Profile"),
                    "link": "profile",
                    "template": "admin/user/profile.html",
                },
                {
                    "title": _("Security"),
                    "link": "security",
                    "template": "admin/user/security.html",
                },
            ],
        },
        {
            "model": "common.Team",
            "items": [
                {
                    "title": _("Team Info"),
                    "link": "team-info",
                    "template": "admin/team/info.html",
                },
                {
                    "title": _("Members"),
                    "link": "members",
                    "template": "admin/team/members.html",
                },
            ],
        },
    ],
    "COLORS": {
        "primary": {
            "50": "239, 246, 255",
            "100": "219, 234, 254",
            "200": "191, 219, 254",
            "300": "147, 197, 253",
            "400": "96, 165, 250",
            "500": "59, 130, 246",
            "600": "37, 99, 235",
            "700": "29, 78, 216",
            "800": "30, 64, 175",
            "900": "30, 58, 138",
        },
    },
    "APP_LIST_CALLBACK": "apps.common.admin_dashboard.filter_admin_app_list",
}
