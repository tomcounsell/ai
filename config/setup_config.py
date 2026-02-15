#!/usr/bin/env python3
"""
Configuration Setup Utility

This script helps users set up their AI Rebuild configuration by creating
the necessary files and validating the setup.
"""

import os
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

# Add the project root to the path so we can import our config
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config import config_loader, validate_configuration

console = Console()


def print_banner():
    """Print the setup banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════╗
    ║            AI Rebuild Configuration Setup             ║
    ║                                                       ║
    ║  This utility will help you set up your AI Rebuild   ║
    ║  configuration files and validate your setup.        ║
    ╚═══════════════════════════════════════════════════════╝
    """
    console.print(banner, style="bold blue")


def check_existing_config() -> bool:
    """Check if configuration files already exist.

    Returns:
        True if .env file exists, False otherwise.
    """
    env_file = Path(".env")
    return env_file.exists()


def create_env_file(force: bool = False) -> bool:
    """Create .env file from template.

    Args:
        force: Overwrite existing file if it exists.

    Returns:
        True if file was created successfully, False otherwise.
    """
    env_file = Path(".env")
    template_file = Path(".env.example")

    if not template_file.exists():
        console.print("❌ .env.example file not found!", style="bold red")
        return False

    if env_file.exists() and not force:
        console.print(
            "⚠️  .env file already exists. Use --force to overwrite.", style="yellow"
        )
        return False

    try:
        shutil.copy(template_file, env_file)
        console.print("✅ Created .env file from template", style="bold green")
        console.print(
            f"📝 Please edit {env_file} and add your API keys and configuration",
            style="cyan",
        )
        return True
    except Exception as e:
        console.print(f"❌ Failed to create .env file: {e}", style="bold red")
        return False


def interactive_env_setup():
    """Interactive setup of environment variables."""
    console.print("\n🔧 Interactive Environment Setup", style="bold yellow")
    console.print("Enter your configuration values (press Enter to skip):", style="dim")

    env_vars = {}

    # API Keys section
    console.print("\n📋 API Keys:", style="bold")

    claude_key = Prompt.ask("Claude API Key", default="", show_default=False)
    if claude_key:
        env_vars["CLAUDE_API_KEY"] = claude_key

    openai_key = Prompt.ask("OpenAI API Key", default="", show_default=False)
    if openai_key:
        env_vars["OPENAI_API_KEY"] = openai_key

    perplexity_key = Prompt.ask("Perplexity API Key", default="", show_default=False)
    if perplexity_key:
        env_vars["PERPLEXITY_API_KEY"] = perplexity_key

    notion_key = Prompt.ask("Notion API Key", default="", show_default=False)
    if notion_key:
        env_vars["NOTION_API_KEY"] = notion_key

    # Telegram section
    if Confirm.ask("\nConfigure Telegram integration?", default=False):
        console.print("\n📱 Telegram Configuration:", style="bold")
        tg_api_id = Prompt.ask("Telegram API ID", default="")
        if tg_api_id:
            env_vars["TELEGRAM_API_ID"] = tg_api_id

        tg_api_hash = Prompt.ask("Telegram API Hash", default="")
        if tg_api_hash:
            env_vars["TELEGRAM_API_HASH"] = tg_api_hash

    # Environment section
    console.print("\n🌍 Environment Configuration:", style="bold")
    environment = Prompt.ask(
        "Environment",
        choices=["development", "staging", "production"],
        default="development",
    )
    env_vars["ENVIRONMENT"] = environment

    debug = Confirm.ask("Enable debug mode?", default=False)
    env_vars["DEBUG"] = str(debug).lower()

    log_level = Prompt.ask(
        "Log Level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
    )
    env_vars["LOG_LEVEL"] = log_level

    # Save to .env file
    if env_vars and Confirm.ask(
        f"\nSave {len(env_vars)} configuration values to .env?", default=True
    ):
        try:
            env_file = Path(".env")

            # Read existing file if it exists
            existing_content = ""
            if env_file.exists():
                existing_content = env_file.read_text()

            # Append new variables
            with open(env_file, "a") as f:
                if existing_content and not existing_content.endswith("\n"):
                    f.write("\n")
                f.write("\n# Interactive setup configuration\n")
                for key, value in env_vars.items():
                    f.write(f"{key}={value}\n")

            console.print(f"✅ Saved configuration to {env_file}", style="bold green")
            return True

        except Exception as e:
            console.print(f"❌ Failed to save configuration: {e}", style="bold red")
            return False

    return False


def validate_setup() -> bool:
    """Validate the current configuration setup.

    Returns:
        True if configuration is valid, False otherwise.
    """
    console.print("\n🔍 Validating Configuration...", style="bold yellow")

    try:
        # Test configuration loading
        is_valid = validate_configuration()

        if is_valid:
            console.print("✅ Configuration validation passed!", style="bold green")
        else:
            console.print("❌ Configuration validation failed!", style="bold red")

        # Show configuration summary
        show_config_summary()

        return is_valid

    except Exception as e:
        console.print(f"❌ Configuration validation error: {e}", style="bold red")
        return False


def show_config_summary():
    """Show a summary of the current configuration."""
    console.print("\n📊 Configuration Summary:", style="bold cyan")

    try:
        summary = config_loader.get_configuration_summary()

        # Create summary table
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Component", style="dim", width=20)
        table.add_column("Status", width=50)

        # Environment info
        table.add_row(
            "Environment", f"{summary['environment']} (Debug: {summary['debug']})"
        )
        table.add_row("Log Level", summary["log_level"])

        # API Keys
        api_keys = summary["api_keys"]
        api_status = []
        for service, configured in api_keys.items():
            status = "✅" if configured else "❌"
            api_status.append(f"{service}: {status}")
        table.add_row("API Keys", " | ".join(api_status))

        # Directories
        directories = summary["directories"]
        dir_status = []
        for name, valid in directories.items():
            status = "✅" if valid else "❌"
            dir_status.append(f"{name}: {status}")
        table.add_row("Directories", " | ".join(dir_status))

        # Database
        table.add_row("Database", summary["database_path"])

        # Server
        server_info = summary["server"]
        table.add_row(
            "Server",
            f"{server_info['host']}:{server_info['port']} ({server_info['workers']} workers)",
        )

        # Workspace
        if "workspace" in summary and "error" not in summary["workspace"]:
            ws = summary["workspace"]
            workspace_info = f"{ws['name']} v{ws['version']} ({ws['agents_count']} agents, {ws['tools_count']} tools)"
            table.add_row("Workspace", workspace_info)
        else:
            table.add_row("Workspace", "❌ Configuration error")

        console.print(table)

    except Exception as e:
        console.print(f"❌ Failed to generate summary: {e}", style="bold red")


@click.command()
@click.option("--force", is_flag=True, help="Force overwrite existing files")
@click.option("--interactive", is_flag=True, help="Interactive configuration setup")
@click.option(
    "--validate-only", is_flag=True, help="Only validate existing configuration"
)
def main(force: bool, interactive: bool, validate_only: bool):
    """AI Rebuild Configuration Setup Utility."""
    print_banner()

    # Change to project directory
    os.chdir(project_root)

    if validate_only:
        success = validate_setup()
        sys.exit(0 if success else 1)

    # Check existing configuration
    has_env = check_existing_config()

    if has_env and not force:
        console.print("✅ Configuration files already exist.", style="bold green")
        if Confirm.ask("Do you want to validate the current setup?", default=True):
            success = validate_setup()
            if not success and Confirm.ask(
                "Configuration has issues. Run interactive setup?", default=True
            ):
                interactive = True
    else:
        console.print("🔧 Setting up configuration files...", style="bold yellow")

        # Create .env file from template
        create_env_file(force=force)

        if interactive or Confirm.ask(
            "Run interactive setup to configure API keys?", default=True
        ):
            interactive = True

    if interactive:
        interactive_env_setup()

    # Final validation
    console.print("\n🏁 Final Validation:", style="bold cyan")
    success = validate_setup()

    if success:
        console.print(
            Panel.fit(
                "🎉 Configuration setup completed successfully!\n\n"
                "You can now start using AI Rebuild with your configuration.\n"
                "Remember to keep your .env file secure and never commit it to version control.",
                title="Setup Complete",
                style="bold green",
            )
        )
    else:
        console.print(
            Panel.fit(
                "⚠️  Configuration setup completed with warnings.\n\n"
                "Some components may not work correctly until all required\n"
                "configuration values are provided. Please review the summary above.",
                title="Setup Complete with Warnings",
                style="bold yellow",
            )
        )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
