#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install dependencies using uv (as required by project standards)
echo "Installing dependencies..."
pip install uv
uv pip install -r requirements/prod.lock.txt

# Convert static asset files
echo "Collecting static files..."
python manage.py collectstatic --no-input

# Apply any outstanding database migrations
echo "Applying database migrations..."
python manage.py migrate

# Any additional production setup steps can be added here
echo "Build completed successfully!"
