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

# Install Playfair Display fonts for podcast cover branding
echo "Installing Playfair Display fonts..."
mkdir -p fonts
curl -sfL -o /tmp/playfair.zip "https://gwfh.mranftl.com/api/fonts/playfair-display?download=zip&subsets=latin&variants=600,italic" && \
    unzip -o -j /tmp/playfair.zip -d fonts/ 2>/dev/null && \
    rm -f /tmp/playfair.zip && \
    echo "Fonts installed:" && ls fonts/ || \
    echo "Warning: font download failed, branding will use fallback fonts"

# Static files are not needed for the worker, but we collect them anyway
# to keep the environment consistent with the web service
echo "Collecting static files..."
uv run python manage.py collectstatic --no-input

# Skip migrations - the web service handles database schema
echo "Skipping migrations (handled by web service)..."

# Any additional production setup steps can be added here
echo "Worker build completed successfully!"
