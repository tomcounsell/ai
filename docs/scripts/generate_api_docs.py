#!/usr/bin/env python
"""
Script to generate API documentation from OpenAPI schema.

This script:
1. Retrieves the OpenAPI schema from a running Django server
2. Formats it into ReStructuredText
3. Saves it to the Sphinx documentation directory
"""

import atexit
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

API_SCHEMA_URL = "http://localhost:8000/api/schema/"
API_DOCS_PATH = (
    Path(__file__).parent.parent / "sphinx_docs" / "source" / "api" / "generated"
)


def ensure_directory(path):
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def start_django_server():
    """Start Django server in background for schema retrieval."""
    print("Starting Django server...")
    process = subprocess.Popen(
        ["python", "manage.py", "runserver", "--noreload"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Register cleanup function to kill server when script exits
    def cleanup():
        if process.poll() is None:  # If process is still running
            print("Shutting down Django server...")
            process.terminate()
            process.wait(timeout=5)

    atexit.register(cleanup)

    # Wait for server to start
    time.sleep(3)
    return process


def fetch_openapi_schema():
    """Fetch OpenAPI schema from running Django server."""
    try:
        response = requests.get(API_SCHEMA_URL)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching API schema: {e}")
        sys.exit(1)


def format_endpoint_docs(schema):
    """Format OpenAPI schema as ReStructuredText."""
    output = []

    # Document info
    output.append("API Documentation\n================\n")
    if "info" in schema:
        info = schema["info"]
        if "title" in info:
            output.append(f"{info['title']}\n")
        if "description" in info:
            output.append(f"{info['description']}\n")
        if "version" in info:
            output.append(f"API Version: {info['version']}\n")

    # Endpoints by tag
    tags = {}

    # Group paths by tag
    for path, methods in schema.get("paths", {}).items():
        for method, details in methods.items():
            for tag in details.get("tags", ["default"]):
                if tag not in tags:
                    tags[tag] = []
                tags[tag].append((path, method, details))

    # Generate documentation for each tag
    for tag, endpoints in sorted(tags.items()):
        output.append(f"\n{tag.capitalize()}\n{'-' * len(tag)}\n")

        for path, method, details in sorted(endpoints, key=lambda x: x[0]):
            # Endpoint header
            method_upper = method.upper()
            output.append(
                f"\n``{method_upper} {path}``\n{'~' * (len(method_upper) + len(path) + 4)}\n"
            )

            # Description
            if "summary" in details:
                output.append(f"{details['summary']}\n")
            if "description" in details and details["description"]:
                output.append(f"{details['description']}\n")

            # Parameters
            if "parameters" in details and details["parameters"]:
                output.append("\nParameters:\n")
                for param in details["parameters"]:
                    name = param.get("name", "")
                    param_in = param.get("in", "")
                    required = (
                        "required" if param.get("required", False) else "optional"
                    )
                    description = param.get("description", "")
                    param_type = param.get("schema", {}).get("type", "")

                    output.append(
                        f"- ``{name}`` ({param_in}, {required}, {param_type}): {description}\n"
                    )

            # Request body
            if "requestBody" in details:
                output.append("\nRequest Body:\n")
                content = details["requestBody"].get("content", {})
                for content_type, content_details in content.items():
                    output.append(f"Content-Type: ``{content_type}``\n")
                    schema = content_details.get("schema", {})
                    if "$ref" in schema:
                        ref = schema["$ref"].split("/")[-1]
                        output.append(f"Schema: ``{ref}``\n")
                    else:
                        output.append("Schema properties:\n")
                        for prop_name, prop_details in schema.get(
                            "properties", {}
                        ).items():
                            prop_type = prop_details.get("type", "")
                            prop_desc = prop_details.get("description", "")
                            output.append(
                                f"- ``{prop_name}`` ({prop_type}): {prop_desc}\n"
                            )

            # Responses
            if "responses" in details:
                output.append("\nResponses:\n")
                for status, response in details["responses"].items():
                    desc = response.get("description", "")
                    output.append(f"- ``{status}``: {desc}\n")

                    content = response.get("content", {})
                    for content_type, content_details in content.items():
                        output.append(f"  Content-Type: ``{content_type}``\n")
                        schema = content_details.get("schema", {})
                        if "$ref" in schema:
                            ref = schema["$ref"].split("/")[-1]
                            output.append(f"  Schema: ``{ref}``\n")

    return "\n".join(output)


def write_documentation(content, filename):
    """Write formatted documentation to file."""
    with open(filename, "w") as f:
        f.write(content)


def main():
    # Ensure output directory exists
    ensure_directory(API_DOCS_PATH)

    # Start Django server
    server_process = start_django_server()

    try:
        # Fetch OpenAPI schema
        schema = fetch_openapi_schema()

        # Format as RST
        docs_content = format_endpoint_docs(schema)

        # Write to file
        docs_file = API_DOCS_PATH / "endpoints.rst"
        write_documentation(docs_content, docs_file)

        print(f"API documentation generated at {docs_file}")

    finally:
        # Shutdown server
        if server_process.poll() is None:
            server_process.terminate()
            server_process.wait(timeout=5)


if __name__ == "__main__":
    main()
