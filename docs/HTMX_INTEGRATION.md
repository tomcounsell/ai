# HTMX Integration Guide

This document explains how to use HTMX with the Django Project Template, focusing on the component directory structure, naming conventions, and common patterns.

## Template Directory Structure

The project's template structure is organized as follows:

```
templates/
│
├── base.html          # Main base template for full pages
├── partial.html       # Base template for HTMX partial updates
│
├── components/        # Reusable UI components
│   ├── _component_base.html     # Base template for all components
│   ├── forms/         # Form components
│   │   ├── form_user.html
│   │   └── ...
│   ├── lists/         # List components
│   │   ├── list_team_members.html
│   │   └── ...
│   ├── cards/         # Card components
│   │   ├── card_team.html
│   │   └── ...
│   ├── modals/        # Modal dialogs
│   │   ├── modal_base.html
│   │   ├── modal_confirm.html
│   │   └── ...
│   └── common/        # Common UI components
│       ├── notification_toast.html
│       └── ...
│
└── layout/           # Page layout elements
    ├── footer.html
    ├── modals.html   # Modal containers
    ├── navbar.html   # Main navigation bar
    ├── messages/     # Notification/toast templates
    │   └── toast.html
    └── nav/          # Navigation components
        ├── account_menu.html
        ├── navbar.html
        └── ...
```

## Naming Conventions

Component templates follow this naming pattern:

- `{type}_{name}.html` (e.g., `form_user.html`, `list_team_members.html`)
- Each file should be in a directory matching its type (e.g., form components go in `components/forms/`)
- Base or utility templates start with underscore (e.g., `_component_base.html`)

## Using Components with HTMX

### 1. Loading Components Dynamically

```html
<!-- Button to load a form via HTMX -->
<button
  hx-get="{% url 'team:add_member_form' team.id %}"
  hx-target="#form-container"
  hx-swap="innerHTML"
>
  Add Member
</button>

<!-- Target container for the form -->
<div id="form-container"></div>
```

### 2. Form Submission with HTMX

```html
<form
  hx-post="{% url 'team:add_member' team.id %}"
  hx-target="#member-list"
  hx-swap="outerHTML"
>
  {% csrf_token %}
  <!-- Form fields -->
  <button type="submit">Add</button>
</form>
```

### 3. Out-of-Band Updates

For updating multiple parts of a page simultaneously:

```html
<!-- Server response containing multiple updates -->
<div id="primary-content" hx-swap-oob="true">
  <!-- Updated primary content -->
</div>

<div id="sidebar" hx-swap-oob="true">
  <!-- Updated sidebar content -->
</div>

<div id="toast-container" hx-swap-oob="true">
  <!-- Messages/notifications -->
</div>
```

### 4. HTMX Events

For triggering actions based on events:

```html
<!-- Trigger a custom event -->
<button
  hx-post="{% url 'team:add_member' team.id %}"
  hx-target="#member-list"
  hx-swap="outerHTML"
  hx-on::after-request="htmx.trigger('#toast-container', 'showMessage')"
>
  Add Member
</button>

<!-- Listen for the event -->
<div
  id="toast-container"
  hx-get="{% url 'messages' %}"
  hx-trigger="showMessage from:body"
></div>
```

## Common Components

### Form Components

Form components handle input collection and submission:

```html
{% extends "components/_component_base.html" %}

{% block content %}
<form hx-post="{{ submit_url }}" hx-target="{{ target|default:'#form-response' }}">
  {% csrf_token %}
  <!-- Form fields -->
  <button type="submit">{{ submit_text|default:"Submit" }}</button>
</form>
{% endblock %}
```

### List Components

List components display collections of items:

```html
{% extends "components/_component_base.html" %}

{% block content %}
<div id="item-list">
  <ul>
    {% for item in items %}
    <li>
      <!-- Item details -->
      <button hx-delete="/items/{{ item.id }}" hx-target="closest li">Remove</button>
    </li>
    {% empty %}
    <li>No items found</li>
    {% endfor %}
  </ul>
</div>
{% endblock %}
```

### Modal Components

Modal components provide dialogs for confirmations:

```html
{% extends "components/modals/modal_base.html" %}

{% block modal_content %}
<div class="modal-content">
  <!-- Modal content -->
  <button hx-post="{{ confirm_url }}" hx-target="{{ target }}">
    {{ confirm_text|default:"Confirm" }}
  </button>
</div>
{% endblock %}
```

## Best Practices

1. **Focused Components**: Each component should have a single responsibility
2. **Clear Documentation**: Include comments describing purpose, required context, and usage examples
3. **Progressive Enhancement**: Ensure components work without JavaScript when possible
4. **Clear Targeting**: Use explicit `id` attributes for HTMX targets
5. **Error Handling**: Include error states and validation feedback
6. **Consistent Structure**: Follow the established naming and directory conventions
7. **Reusability**: Design components to be reusable across the application

## Integration with Views

Components work with the `HTMXView` class from `apps.public.helpers`:

```python
from apps.public.helpers import HTMXView

class TeamMemberListView(HTMXView):
    template_name = "components/lists/list_team_members.html"
    
    def get_context_data(self):
        context = super().get_context_data()
        context["members"] = self.team.members.all()
        context["team"] = self.team
        context["can_manage"] = self.request.user.has_perm("manage_team", self.team)
        return context
```

## Testing HTMX Components

Test both the rendering and HTMX interaction:

```python
from django.test import TestCase, Client

class ComponentTestCase(TestCase):
    def test_form_component_renders(self):
        response = self.client.get('/form/user/', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'components/forms/form_user.html')
    
    def test_form_submission_via_htmx(self):
        response = self.client.post(
            '/form/user/',
            {'name': 'Test User', 'email': 'test@example.com'},
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        # Check for expected content in response
```

## Out-of-Band Updates with HTMXView

For views that need to update multiple parts of the page:

```python
class CreateTeamView(TeamSessionMixin, HTMXView):
    template_name = "components/forms/team_form.html"
    oob_templates = {
        "team_list": "components/lists/team_list.html",
        "messages": "layout/messages/toast.html"
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

## Related Documentation

- [VIEW_CONVENTIONS.md](VIEW_CONVENTIONS.md): Details on view classes for HTMX integration
- [TEMPLATE_CONVENTIONS.md](TEMPLATE_CONVENTIONS.md): General template conventions
- [HTMX Documentation](https://htmx.org/docs/): Official HTMX documentation
