import os

from django.contrib import admin
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import include, path
from django.views.generic import TemplateView

from settings.env import DEBUG, LOCAL
from settings.unfold import (
    ADMIN_INDEX_TITLE,
    ADMIN_SITE_HEADER,
    ADMIN_SITE_TITLE,
    ADMIN_SITE_URL,
)

# Configure Django's built-in admin
admin.autodiscover()
admin.site.site_header = ADMIN_SITE_HEADER
admin.site.site_title = ADMIN_SITE_TITLE
admin.site.site_url = ADMIN_SITE_URL
admin.site.index_title = ADMIN_INDEX_TITLE


def get_docs_base_path():
    """Return the absolute path to the docs directory."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")


def list_markdown_files(directory):
    """List all markdown files in a directory recursively."""
    markdown_files = []
    for root, _, files in os.walk(directory):
        rel_path = os.path.relpath(root, directory)
        rel_path = "" if rel_path == "." else rel_path

        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(rel_path, file[:-3]) if rel_path else file[:-3]
                markdown_files.append(file_path)

    return sorted(markdown_files)


def serve_docs_index(request):
    """Serve an index of available markdown documentation files."""
    base_path = get_docs_base_path()
    markdown_files = list_markdown_files(base_path)

    # Group files by directory
    grouped_files = {"root": [], "advanced": [], "guides": []}

    for file_path in markdown_files:
        if "/" in file_path:
            directory, _ = file_path.split("/", 1)
            if directory in grouped_files:
                grouped_files[directory].append(file_path)
            else:
                grouped_files["root"].append(file_path)
        else:
            grouped_files["root"].append(file_path)

    return JsonResponse(grouped_files)


def serve_markdown_file(request, filename):
    """Serve a markdown file from the docs directory as plain text."""
    base_path = get_docs_base_path()

    # Handle optional .md extension in the URL
    if filename.endswith(".md"):
        filename = filename[:-3]

    # Support for docs/guides/ and nested paths
    path_components = filename.split("/")
    if len(path_components) > 1:
        # Handle nested paths like "guides/SETUP_GUIDE"
        nested_dir = "/".join(path_components[:-1])
        filename = path_components[-1]

        # Remove .md extension from the last component if present
        if filename.endswith(".md"):
            filename = filename[:-3]

        potential_paths = [os.path.join(base_path, nested_dir, f"{filename}.md")]
    else:
        # Regular paths in root and advanced directories
        potential_paths = [
            os.path.join(base_path, f"{filename}.md"),
            os.path.join(base_path, "advanced", f"{filename}.md"),
            os.path.join(base_path, "guides", f"{filename}.md"),
        ]

    # Try to find the file in potential locations
    for file_path in potential_paths:
        if os.path.exists(file_path) and os.path.isfile(file_path):
            with open(file_path) as f:
                content = f.read()
            # Set proper content type for markdown
            return HttpResponse(content, content_type="text/markdown; charset=utf-8")

    # File not found
    raise Http404(f"Markdown file '{filename}' not found")


urlpatterns = [
    path("", TemplateView.as_view(template_name="pages/home.html"), name="home"),
    path("", include("apps.public.urls", namespace="public")),
    path("staff/", include("apps.staff.urls", namespace="staff")),
    path("ai/", include("apps.ai.urls", namespace="ai")),
    # Serve documentation index
    path("docs/", serve_docs_index, name="docs_index"),
    # Serve Markdown documentation directly - supports docs/FILENAME and nested paths
    path("docs/<path:filename>", serve_markdown_file, name="serve_markdown"),
    # API URLs - Removed
    # path('api/', include('apps.api.urls')),
    # API Schema and Documentation - Removed
    # path('api/schema/', get_schema_view(...), name='openapi-schema'),
    # path('api/docs/', TemplateView.as_view(...), name='swagger-ui'),
]

# Admin URL
# Admin configuration is in settings/unfold.py
urlpatterns += [
    path("admin/", admin.site.urls),
]

# DEBUG MODE
if DEBUG:
    import debug_toolbar
    from django.conf import settings
    from django.conf.urls.static import static

    dev_url_patterns = []
    dev_url_patterns.append(
        path("__debug__/", include(debug_toolbar.urls)),
    )
    if LOCAL:
        dev_url_patterns.append(
            path("__reload__/", include("django_browser_reload.urls")),
        )
    urlpatterns = dev_url_patterns + urlpatterns

    # Serve media files in development
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
