#!/usr/bin/env python3
"""
Create a new tool from template.

Usage:
    python tools/new_tool.py <tool-name>

Example:
    python tools/new_tool.py search
"""

import shutil
import sys
from pathlib import Path


def create_tool(name: str) -> None:
    """Create a new tool from template."""
    tools_dir = Path(__file__).parent
    template_dir = tools_dir / "_template"
    new_tool_dir = tools_dir / name

    if new_tool_dir.exists():
        print(f"Error: Tool '{name}' already exists at {new_tool_dir}")
        sys.exit(1)

    if not template_dir.exists():
        print(f"Error: Template not found at {template_dir}")
        sys.exit(1)

    # Copy template
    shutil.copytree(template_dir, new_tool_dir)

    # Replace placeholders in all files
    placeholders = {
        "TOOL_NAME": name,
        "COMMAND_NAME": name,
        "DESCRIPTION": f"{name.title()} tool",
        "PACKAGE_NAME": name,
        "REPOSITORY_URL": f"https://github.com/org/{name}",
        "INSTALL_COMMAND": f"npm install -g {name}",
        "VERIFY_COMMAND": f"{name} --version",
        "HELP_COMMAND": f"{name} --help",
    }

    for file_path in new_tool_dir.rglob("*"):
        if file_path.is_file():
            # Rename test file if needed
            if "TOOL_NAME" in file_path.name:
                new_name = file_path.name.replace("TOOL_NAME", name)
                new_path = file_path.parent / new_name
                file_path.rename(new_path)
                file_path = new_path

            # Replace content
            try:
                content = file_path.read_text()
                for placeholder, value in placeholders.items():
                    content = content.replace(placeholder, value)
                file_path.write_text(content)
            except UnicodeDecodeError:
                # Skip binary files
                pass

    print(f"✓ Created tool '{name}' at {new_tool_dir}")
    print()
    print("Next steps:")
    print(f"  1. Edit tools/{name}/manifest.json with correct metadata")
    print(f"  2. Update tools/{name}/README.md with documentation")
    print(f"  3. Implement tests in tools/{name}/tests/test_{name}.py")
    print(f"  4. Validate: python tools/validate.py tools/{name}/")
    print(f"  5. Commit: git add tools/{name}/ && git commit -m 'Add {name} tool'")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    name = sys.argv[1].lower().replace(" ", "-")
    create_tool(name)


if __name__ == "__main__":
    main()
