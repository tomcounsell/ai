#!/bin/bash

# Export requirements for deployment platforms that need requirements.txt

echo "📦 Exporting requirements for deployment..."

# Export production dependencies only
uv export --no-dev --no-emit-project > ../requirements.txt

echo "✅ Created requirements.txt with production dependencies"
echo ""
echo "File location: requirements.txt"
echo "Use this file for deployment platforms that don't support pyproject.toml"
