#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install uv package manager
echo "Installing uv..."
pip install uv

# Install dependencies using uv  (project standard)
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

# Convert static asset files
echo "Collecting static files..."
uv run python manage.py collectstatic --no-input

# Apply any outstanding database migrations
echo "Applying database migrations..."
uv run python manage.py migrate

# Any additional production setup steps can be added here
echo "Build completed successfully!"
