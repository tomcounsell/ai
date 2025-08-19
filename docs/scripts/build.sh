#!/usr/bin/env bash
# exit on error
set -o errexit

# Install uv if not installed
if ! command -v uv &> /dev/null; then
  echo "Installing uv package manager..."
  pip install uv
fi

# Check if pyproject.toml exists
if [ -f pyproject.toml ]; then
  echo "Installing dependencies from pyproject.toml..."
  uv pip install -e ".[dev,test]"
else
  # Fallback to requirements.txt if pyproject.toml doesn't exist
  echo "pyproject.toml not found, falling back to requirements.txt..."

  # Generate requirements.txt if it doesn't exist
  if [ ! -f requirements.txt ]; then
    ./requirements/generate_deployment_requirements.sh
  fi

  # Install dependencies from the generated requirements.txt
  uv pip install -r requirements.txt
fi

# Run Django commands
echo "Running collectstatic..."
python manage.py collectstatic --no-input

echo "Running migrations..."
python manage.py migrate

echo "Build completed successfully!"
