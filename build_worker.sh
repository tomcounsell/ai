#!/usr/bin/env bash
# Build script for background worker
# Skips database migrations since the web service handles them
set -o errexit

# Install uv package manager
echo "Installing uv..."
pip install uv

# Install dependencies using uv (project standard)
echo "Installing dependencies..."
uv pip install . --system

# Static files are not needed for the worker, but we collect them anyway
# to keep the environment consistent with the web service
echo "Collecting static files..."
uv run python manage.py collectstatic --no-input

# Skip migrations - the web service handles database schema
echo "Skipping migrations (handled by web service)..."

# Any additional production setup steps can be added here
echo "Worker build completed successfully!"
