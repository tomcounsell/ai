#!/bin/bash

# Comprehensive documentation build script
# This script generates all documentation, including:
# - API documentation from OpenAPI schema
# - Python code documentation using Sphinx

set -e

# Define script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_ROOT="$PROJECT_ROOT/docs"
SPHINX_DIR="$DOC_ROOT/sphinx_docs"
API_DOCS_DIR="$SPHINX_DIR/source/api/generated"

echo "=== Django Project Template Documentation Generator ==="
echo "Project root: $PROJECT_ROOT"

# Create necessary directories
mkdir -p "$API_DOCS_DIR"

# Check for virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Warning: No virtual environment detected. Using system Python."
    PYTHON="python"
else
    echo "Using Python from: $VIRTUAL_ENV"
    PYTHON="$VIRTUAL_ENV/bin/python"
fi

# Check for required dependencies
echo "Checking for required packages..."
$PYTHON -m pip install -q sphinx sphinx-rtd-theme sphinx-autodoc-typehints myst-parser sphinxcontrib-django requests

# Step 1: Generate API documentation from OpenAPI schema
echo "Generating API documentation from OpenAPI schema..."
$PYTHON "$DOC_ROOT/scripts/generate_api_docs.py"

# Step a: Update API doc index to include generated docs
API_INDEX_FILE="$SPHINX_DIR/source/api/index.rst"
if ! grep -q "generated/endpoints" "$API_INDEX_FILE"; then
    echo "Updating API index file to include generated endpoints..."
    # Use temp file to avoid issues with in-place editing
    grep -B 100 "serializers" "$API_INDEX_FILE" > "$API_INDEX_FILE.tmp"
    echo "   generated/endpoints" >> "$API_INDEX_FILE.tmp"
    grep -A 100 "serializers" "$API_INDEX_FILE" | tail -n +2 >> "$API_INDEX_FILE.tmp"
    mv "$API_INDEX_FILE.tmp" "$API_INDEX_FILE"
fi

# Step 2: Generate API docs from Python docstrings using sphinx-apidoc
echo "Generating module API documentation with sphinx-apidoc..."
cd "$SPHINX_DIR"
sphinx-apidoc -f -e -o source/api/modules "$PROJECT_ROOT/apps" --separate

# Step 3: Build HTML documentation
echo "Building HTML documentation..."
cd "$SPHINX_DIR"
make html

echo "Documentation build complete!"
echo "You can view the documentation at: $SPHINX_DIR/build/html/index.html"
