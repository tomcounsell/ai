#!/bin/bash

# Build the documentation using Sphinx
# This script should be run from the project root

set -e

# Create directories if they don't exist
mkdir -p docs/sphinx_docs/build

# Determine the activated Python environment
if [ -n "$VIRTUAL_ENV" ]; then
    echo "Using Python from virtual environment: $VIRTUAL_ENV"
    PYTHON="$VIRTUAL_ENV/bin/python"
else
    echo "Using system Python"
    PYTHON="python"
fi

# Ensure dependencies are installed
echo "Checking documentation dependencies..."
$PYTHON -m pip install -q sphinx sphinx-rtd-theme sphinx-autodoc-typehints myst-parser sphinxcontrib-django

# Generate API documentation
echo "Generating API documentation..."
cd docs/sphinx_docs
$PYTHON -m sphinx.ext.apidoc -o source/api ../../apps --separate --force

# Build HTML documentation
echo "Building HTML documentation..."
$PYTHON -m sphinx.cmd.build -b html source build/html

echo "Documentation built successfully."
echo "You can view the documentation by opening: docs/sphinx_docs/build/html/index.html"
