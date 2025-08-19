#!/usr/bin/env bash
# Simple script to install pre-commit hooks

# Check if pre-commit is installed, install if not
if ! command -v pre-commit &> /dev/null; then
    echo "pre-commit not found, installing..."
    pip install pre-commit
fi

# Install the pre-commit hooks
pre-commit install

echo "Pre-commit hooks installed successfully!"
echo "These hooks will now run automatically on each commit."
echo "To run hooks manually on all files: pre-commit run --all-files"
