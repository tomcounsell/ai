# Modal Dialog Patterns

This document outlines the best practices for implementing modal dialogs in the application.

## Overview

The modal system provides a consistent way to display overlay dialogs that focus user attention on a specific task, form, or confirmation. Our implementation supports:

- Primary and secondary modals (for layering)
- Consistent styling and behavior
- HTMX integration for dynamic loading
- Accessible design principles

## Structure

Our modal pattern consists of:

1. **Base Template**: `partials/modals/_modal_base.html` - Foundation for all modals
2. **Modal Types**:
   - Confirmation Modal: `partials/modals/modal_confirm.html`
   - Form Modal: `partials/modals/modal_form.html`
   - Secondary Modal: `partials/modals/modal_secondary.html`
3. **Modal Containers**: `layout/modals.html` - Includes containers for both primary and secondary modals
4. **CSS Styles**: `static/css/components/modals.css` - All modal styling

## Key Features

- **Modal Stacking**: Primary modals use z-index 1000, while secondary modals use z-index 1100
- **Backdrop Closing**: By default, clicking outside the modal closes it
- **Responsive Sizing**: Multiple size options (sm, md, lg, xl)
- **HTMX Integration**: Designed to work seamlessly with HTMX for dynamic loading and form submission

## Usage

### Setup

Include the modal containers in your base template:

```html
{% include "layout/modals.html" %}
```

### Loading a Modal with HTMX

```html
<button
  hx-get="{% url 'load_modal_view' %}"
  hx-target="#primary-modal-container"
  hx-swap="innerHTML"
>
  Open Modal
</button>
```

### Creating a Modal View

```python
from django.shortcuts import render

def load_confirmation_modal(request, item_id):
    item = get_object_or_404(Item, id=item_id)
    
    context = {
        'modal_title': 'Delete Item',
        'message': f'Are you sure you want to delete {item.name}?',
        'confirm_url': reverse('delete_item', args=[item.id]),
        'confirm_method': 'delete',
    }
    
    return render(request, 'partials/modals/modal_confirm.html', context)
```

### Form Modal Example

```python
def load_edit_form_modal(request, item_id):
    item = get_object_or_404(Item, id=item_id)
    form = ItemForm(instance=item)
    
    context = {
        'modal_title': f'Edit {item.name}',
        'form': form,
        'submit_url': reverse('update_item', args=[item.id]),
        'target': '#item-list',  # Replace item list with updated data
    }
    
    return render(request, 'partials/modals/modal_form.html', context)
```

### Using a Secondary Modal

For cases where you need a modal on top of another modal:

```html
<button
  hx-get="{% url 'secondary_confirmation' %}"
  hx-target="#secondary-modal-container"
  hx-swap="innerHTML"
>
  Confirm
</button>
```

## Best Practices

1. **Always use `innerHTML` swap**: When targeting modal containers, always use `hx-swap="innerHTML"` to ensure proper replacement

2. **Use proper containers**: Target `#primary-modal-container` for main modals and `#secondary-modal-container` for modals that should appear above other modals

3. **Provide all required context**: Ensure all required context variables are provided when rendering modal templates

4. **Keep modals focused**: Each modal should serve a single purpose with clear actions

5. **Consider accessibility**: Ensure modals work properly with keyboard navigation and screen readers

6. **Handle form errors**: When submitting forms in modals, handle validation errors appropriately to keep the user in the modal

## Available Modal Types

### 1. Confirmation Modal

Used for confirming user actions:

```python
context = {
    'modal_title': 'Confirm Action',
    'message': 'Are you sure you want to proceed?',
    'confirm_url': '/confirm-endpoint/',
    'confirm_text': 'Yes, Proceed',
    'cancel_text': 'No, Cancel',
}
return render(request, 'partials/modals/modal_confirm.html', context)
```

### 2. Form Modal

Used for forms within a modal:

```python
context = {
    'modal_title': 'Add New Item',
    'form': ItemForm(),
    'submit_url': '/items/create/',
    'submit_text': 'Create Item',
}
return render(request, 'partials/modals/modal_form.html', context)
```

### 3. Secondary Modal

Used when a modal needs to appear above another modal:

```python
context = {
    'modal_title': 'Additional Confirmation',
    'modal_id': 'secondary-confirmation',
    'z_index': 1100,  # Higher than primary modal
}
return render(request, 'partials/modals/modal_secondary.html', context)
```

## Customization

To create a custom modal type, extend the base modal template:

```html
{% extends "partials/modals/_modal_base.html" %}

{% block modal_body %}
  <!-- Your custom content here -->
{% endblock %}

{% block modal_footer %}
  <!-- Your custom footer actions here -->
{% endblock %}
```
