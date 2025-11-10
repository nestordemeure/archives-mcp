"""
Installation helper for MCP CLI integration
"""

import sys
import subprocess
from pathlib import Path


def install_claude():
    """Install to Claude Code using CLI command"""
    project_dir = Path.cwd().resolve()

    try:
        # Use the claude CLI to add the MCP server
        result = subprocess.run(
            [
                "claude", "mcp", "add",
                "--transport", "stdio",
                "--scope", "user",
                "archives",
                "--",
                "uv", "--directory", str(project_dir), "run", "archives-mcp"
            ],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✓ Installed to Claude Code")
        print(f"  Project directory: {project_dir}")
        if result.stdout:
            print(f"  {result.stdout.strip()}")
    except FileNotFoundError:
        raise Exception("'claude' command not found. Make sure Claude Code CLI is installed and in your PATH.")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to add MCP server: {e.stderr.strip() if e.stderr else str(e)}")


def install_codex():
    """Install to Codex CLI using CLI command"""
    project_dir = Path.cwd().resolve()

    try:
        # Use the codex CLI to add the MCP server
        result = subprocess.run(
            [
                "codex", "mcp", "add",
                "archives",
                "--",
                "uv", "--directory", str(project_dir), "run", "archives-mcp"
            ],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✓ Installed to Codex CLI")
        print(f"  Project directory: {project_dir}")
        if result.stdout:
            print(f"  {result.stdout.strip()}")
    except FileNotFoundError:
        raise Exception("'codex' command not found. Make sure Codex CLI is installed and in your PATH.")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to add MCP server: {e.stderr.strip() if e.stderr else str(e)}")


def install_gemini():
    """Install to Gemini CLI using CLI command"""
    project_dir = Path.cwd().resolve()

    try:
        # Use the gemini CLI to add the MCP server
        result = subprocess.run(
            [
                "gemini", "mcp", "add",
                "archives",
                "uv", "--directory", str(project_dir), "run", "archives-mcp"
            ],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✓ Installed to Gemini CLI")
        print(f"  Project directory: {project_dir}")
        if result.stdout:
            print(f"  {result.stdout.strip()}")
    except FileNotFoundError:
        raise Exception("'gemini' command not found. Make sure Gemini CLI is installed and in your PATH.")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to add MCP server: {e.stderr.strip() if e.stderr else str(e)}")


def main():
    """Install archives MCP server to all detected CLIs"""
    project_dir = Path.cwd().resolve()

    print(f"Installing archives MCP server...")
    print(f"Working directory: {project_dir}\n")

    installed = []

    # Try installing to all CLIs
    try:
        install_claude()
        installed.append("Claude Code")
    except Exception as e:
        print(f"⚠ Could not install to Claude Code: {e}")

    try:
        install_codex()
        installed.append("Codex CLI")
    except Exception as e:
        print(f"⚠ Could not install to Codex CLI: {e}")

    try:
        install_gemini()
        installed.append("Gemini CLI")
    except Exception as e:
        print(f"⚠ Could not install to Gemini CLI: {e}")

    if installed:
        print(f"\n✓ Successfully installed to: {', '.join(installed)}")
    else:
        print(f"\n✗ No CLIs were configured")
        sys.exit(1)

    print(f"\nRestart your CLI to use the server.")


if __name__ == "__main__":
    main()
