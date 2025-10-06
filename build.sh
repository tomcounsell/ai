#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install uv package manager
echo "Installing uv..."
pip install uv

# Install dependencies using uv  (project standard)
echo "Installing dependencies..."
uv pip install . --system

# Convert static asset files
echo "Collecting static files..."
uv run python manage.py collectstatic --no-input

# Apply any outstanding database migrations
echo "Applying database migrations..."
uv run python manage.py migrate

# Any additional production setup steps can be added here
echo "Build completed successfully!"
