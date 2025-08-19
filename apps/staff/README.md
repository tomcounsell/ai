# Staff App

This app provides tools and interfaces for staff members to manage internal operations. It's designed to be separate from the public-facing components and contains staff-only functionality.

## Purpose

The staff app serves as a hub for backend management tools, administrative functions, and other operations that should only be accessible to staff members.

## Features

- Staff-only access to all views
- Enhanced admin interface using Unfold admin
- Custom model-specific actions and filters

## Permissions

All views in this app require staff privileges to access. This is enforced at the view level using the `StaffRequiredMixin` which inherits from Django's `LoginRequiredMixin`.

## URL Structure

All URLs in this app are prefixed with `/staff/` to clearly separate them from public-facing URLs.

## Templates

Templates for this app are stored in the `/templates/staff/` directory following the Django convention.

## Usage

To access staff functionality, users must have `is_staff=True` set on their user account.
