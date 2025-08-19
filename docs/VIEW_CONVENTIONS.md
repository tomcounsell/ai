# View Conventions

This document outlines the conventions and best practices for Django views in this project. These guidelines ensure consistency, maintainability, and proper integration with templates and HTMX.

## View Classes

The project provides several specialized view classes to handle different types of requests and rendering needs. These are located in the `apps.public.helpers` package. These reusable Django view classes and mixins are designed to simplify common view patterns, particularly for HTMX integration and team/user session management.

### 1. MainContentView

Base view class for rendering Django templates with standardized context handling. Use this for standard pages that render full HTML responses.

```python
from apps.public.helpers import MainContentView

class HomeView(MainContentView):
    template_name = "pages/home.html"
    
    def get(self, request, *args, **kwargs):
        self.context["items"] = Item.objects.all()
        return self.render(request)
```

Key features:
- Automatic context initialization
- Simplified template rendering
- Base template selection based on request type
- URL history management for HTMX requests

### 2. HTMXView

Specialized view for HTMX requests with support for out-of-band (OOB) updates, URL history management, and complex HTMX interactions.

```python
from apps.public.helpers import HTMXView, TeamSessionMixin

class UserProfileComponent(HTMXView):
    template_name = "components/user_profile.html"
    oob_templates = {
        "sidebar": "components/common/sidebar.html",
        "messages": "layout/messages/toast.html"
    }
    push_url = "/user/profile"
    
    def get(self, request, *args, **kwargs):
        self.context["user_data"] = get_user_data(request.user)
        return self.render(request)
```

Key features:
- Enforces HTMX-only requests
- Support for multiple template rendering in one response
- Out-of-band (OOB) updates for multiple page elements
- URL history management
- Automatic toast message handling

## Session Mixins

### 1. SessionStateMixin

Base mixin for managing user session state, like tracking login status.

### 2. TeamSessionMixin

Mixin for views that require team context. It loads the current team from URL parameters or session and ensures proper user access.

```python
from apps.public.helpers import MainContentView, TeamSessionMixin

class TeamSettingsView(TeamSessionMixin, MainContentView):
    template_name = "team/settings.html"
    
    def get(self, request, *args, **kwargs):
        # self.team is automatically loaded and available
        self.context["members"] = self.team.members.all()
        return self.render(request)
```


## View Organization

### Directory Structure

- `apps/{app_name}/views/`: Standard app views
- `apps/{app_name}/views/components/`: Component views for HTMX interactions
- `apps/public/helpers/`: Reusable view base classes and mixins

### View Naming

- Views should be named descriptively with a `View` suffix
- HTMX component views should include `Component` in the name
- API views should follow RESTful naming conventions

Examples:
- `UserProfileView`
- `TeamDashboardComponent`
- `TeamMemberListView`

## Core Principles

### 1. View Type Selection

- Use `MainContentView` for standard page rendering
- Use `HTMXView` for HTMX-specific components and interactions
- Use Django's generic class-based views for simple CRUD operations
- Add `TeamSessionMixin` when team context is needed

### Import Guidelines

- Import these components from `apps.public.helpers` (not from the views directory)
- Example: `from apps.public.helpers import MainContentView, HTMXView, TeamSessionMixin`

### 2. Context Handling

- Initialize context in `__init__` or `setup` methods
- Add view-specific context in `get_context_data` or directly to `self.context`
- Use descriptive context variable names

### 3. HTMX Integration

- For HTMX views, always validate that the request is coming from HTMX
- Use `HTMXView` for complex HTMX interactions with OOB
- Handle HTMX response headers properly (`HX-Redirect`, `HX-Trigger`, etc.)

### 4. Template Selection

- Set `template_name` explicitly as a class attribute
- For complex logic, override `get_template_names()` method
- Use consistent template paths matching the view's purpose

### 5. Request Handling

- Implement appropriate HTTP method handlers (`get`, `post`, etc.)
- Validate inputs and handle errors explicitly
- Use Django forms or serializers for data validation
- Return appropriate status codes and responses

## HTMX View Patterns

### Out-of-Band Updates

For views that need to update multiple parts of the page:

```python
class CreateTeamView(TeamSessionMixin, HTMXView):
    template_name = "components/team_form.html"
    oob_templates = {
        "team_list": "components/team_list.html",
        "messages": "messages/toast.html"
    }
    
    def post(self, request, *args, **kwargs):
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save()
            messages.success(request, "Team created successfully!")
            self.context["teams"] = request.user.teams.all()
            return self.render(request)
        
        self.context["form"] = form
        return self.render(request)
```

### URL History Management

For HTMX interactions that should update the browser URL:

```python
class TeamDetailComponent(TeamSessionMixin, HTMXView):
    template_name = "components/team_detail.html"
    
    def get(self, request, *args, **kwargs):
        team_id = kwargs.get("team_id")
        self.context["team"] = get_object_or_404(Team, id=team_id)
        return self.render(request, push_url=f"/teams/{team_id}/")
```

## Form Handling

### 1. Standard Forms

For regular form submission:

```python
class TeamCreateView(TeamSessionMixin, MainContentView):
    template_name = "team/create.html"
    
    def get(self, request, *args, **kwargs):
        self.context["form"] = TeamForm()
        return self.render(request)
    
    def post(self, request, *args, **kwargs):
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save(commit=False)
            team.save()
            messages.success(request, "Team created successfully!")
            return redirect("team:detail", team_id=team.id)
        
        self.context["form"] = form
        return self.render(request)
```

### 2. HTMX Form Submission

For HTMX-based form submission with partial updates:

```python
class TeamCreateComponent(TeamSessionMixin, HTMXView):
    template_name = "components/team_form.html"
    
    def get(self, request, *args, **kwargs):
        self.context["form"] = TeamForm()
        return self.render(request)
    
    def post(self, request, *args, **kwargs):
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save()
            messages.success(request, "Team created successfully!")
            return redirect("team:list")
        
        self.context["form"] = form
        return self.render(request)
```

## Error Handling

- Use Django's built-in error views for 404, 500, etc.
- For API endpoints, return proper status codes with error details
- Add proper error messages to the Django message framework
- Display user-friendly error messages in templates

## Testing

- Place view tests in `apps/{app_name}/tests/test_views/`
- Test both GET and POST methods
- Verify proper template usage and context variables
- Test form validation and error handling
- For HTMX views, test with proper request headers and verify response content

Example test for an HTMX view:

```python
from django.test import TestCase, Client

class TeamComponentTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.login(username="testuser", password="password")
        
    def test_team_list_component(self):
        response = self.client.get(
            "/teams/components/list/",
            HTTP_HX_REQUEST="true"  # Simulate HTMX request
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "components/team_list.html")
        self.assertContains(response, "team-list-container")
```

## Best Practices

1. **Keep Views Focused**: Each view should have a single responsibility
2. **Use Mixins Wisely**: Compose functionality using mixins, but avoid excessive nesting
3. **Context Consistency**: Use consistent context variable naming across views
4. **Template Coordination**: Ensure views provide all the context variables needed by templates
5. **Error Handling**: Handle all possible errors gracefully
6. **Security**: Always validate user permissions and input data
7. **Documentation**: Add docstrings to explain complex view logic

## Related Documentation

- [TEMPLATE_CONVENTIONS.md](TEMPLATE_CONVENTIONS.md): Conventions for Django templates used with these views
- [Django View Documentation](https://docs.djangoproject.com/en/stable/topics/class-based-views/): Official Django documentation on class-based views
- [HTMX Documentation](https://htmx.org/docs/): Official HTMX documentation
