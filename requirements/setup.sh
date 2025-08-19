#!/bin/bash

# Quick setup script for new developers
# This script sets up the development environment using uv

set -e  # Exit on error

echo "🚀 Django Project Template - Development Setup"
echo "=============================================="
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "📦 uv is not installed. Installing uv..."
    pip install uv
    echo "✅ uv installed successfully"
else
    echo "✅ uv is already installed"
fi

# Check Python version
python_version=$(python3 --version | cut -d " " -f 2 | cut -d "." -f 1-2)
required_version="3.11"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Error: Python $required_version or higher is required (found $python_version)"
    exit 1
fi
echo "✅ Python version check passed ($python_version)"

# Sync dependencies based on environment
if [ "$1" == "prod" ]; then
    echo ""
    echo "📦 Installing production dependencies..."
    uv sync
elif [ "$1" == "test" ]; then
    echo ""
    echo "📦 Installing test dependencies..."
    uv sync --extra test
else
    echo ""
    echo "📦 Installing all development dependencies..."
    uv sync --all-extras
fi

echo ""
echo "✅ Dependencies installed successfully!"
echo ""
echo "Next steps:"
echo "  1. Copy .env.example to .env.local and configure"
echo "  2. Set up your database"
echo "  3. Run migrations: uv run python manage.py migrate"
echo "  4. Start development server: uv run python manage.py runserver"
echo ""
echo "Happy coding! 🎉"
