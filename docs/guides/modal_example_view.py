"""
Example view demonstrating modal dialog usage with HTMX.

NOTE: This is an example file not meant to be used directly.
It shows how to implement views that use the modal pattern.
"""

from django import forms
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.common.models import Item  # Assuming we have an Item model


class ItemForm(forms.ModelForm):
    """Example form for the Item model."""

    class Meta:
        model = Item
        fields = ["name", "description"]


# Example views for modal dialogs


def load_confirm_delete_modal(request, item_id):
    """View that renders a confirmation modal for deleting an item."""
    item = get_object_or_404(Item, id=item_id)

    context = {
        "modal_id": f"delete-item-{item.id}",
        "modal_title": "Delete Item",
        "message": f"Are you sure you want to delete {item.name}?",
        "confirm_url": reverse("delete_item", kwargs={"item_id": item.id}),
        "confirm_method": "delete",
        "confirm_text": "Delete",
        "target": "#item-list",  # Update the item list after deletion
    }

    return render(request, "components/modals/modal_confirm.html", context)


def load_item_form_modal(request, item_id=None):
    """View that renders a form modal for creating or editing an item."""
    # For edit: get existing item
    if item_id:
        item = get_object_or_404(Item, id=item_id)
        form = ItemForm(instance=item)
        title = f"Edit {item.name}"
        submit_url = reverse("update_item", kwargs={"item_id": item.id})
    # For create: initialize empty form
    else:
        form = ItemForm()
        title = "Create New Item"
        submit_url = reverse("create_item")

    context = {
        "modal_id": item_id and f"edit-item-{item_id}" or "create-item",
        "modal_title": title,
        "form": form,
        "submit_url": submit_url,
        "submit_text": item_id and "Save Changes" or "Create",
        "target": "#item-list",  # Update the item list after form submission
    }

    return render(request, "components/modals/modal_form.html", context)


def load_secondary_confirmation_modal(request, item_id):
    """View that renders a secondary confirmation modal on top of another modal."""
    item = get_object_or_404(Item, id=item_id)

    context = {
        "modal_id": "secondary-confirmation",
        "modal_title": "Confirm Permanent Deletion",
        "message": f"This will permanently delete {item.name} and cannot be undone!",
        "confirm_url": reverse("permanently_delete_item", kwargs={"item_id": item.id}),
        "confirm_method": "delete",
        "confirm_text": "Yes, Delete Permanently",
        "cancel_text": "No, Cancel",
        "target": "#item-list",  # Update the item list after deletion
        "z_index": 1100,  # Higher z-index to appear above the primary modal
    }

    return render(request, "components/modals/modal_secondary.html", context)


# Example HTMX action handlers


def delete_item(request, item_id):
    """Handle actual deletion of an item."""
    item = get_object_or_404(Item, id=item_id)
    item.delete()

    # Return updated item list as HTMX response
    items = Item.objects.all()
    return render(request, "components/lists/list_items.html", {"items": items})


def create_or_update_item(request, item_id=None):
    """Handle item creation or update from modal form."""
    if item_id:
        item = get_object_or_404(Item, id=item_id)
        form = ItemForm(request.POST, instance=item)
    else:
        form = ItemForm(request.POST)

    if form.is_valid():
        form.save()
        # Return updated item list as HTMX response
        items = Item.objects.all()
        return render(request, "components/lists/list_items.html", {"items": items})
    else:
        # Return form with errors in the same modal
        context = {
            "modal_id": item_id and f"edit-item-{item_id}" or "create-item",
            "modal_title": item_id and f"Edit {item.name}" or "Create New Item",
            "form": form,
            "submit_url": item_id
            and reverse("update_item", kwargs={"item_id": item_id})
            or reverse("create_item"),
            "submit_text": item_id and "Save Changes" or "Create",
            "target": "#item-list",
        }
        return render(request, "components/modals/modal_form.html", context)
