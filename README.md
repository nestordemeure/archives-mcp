# Internet Archive MCP Server

MCP server for [Internet Archive](https://archive.org/).
Search and access OCR text from millions of digitized books and documents:

- **search_archive**: Paginated search (100 docs/page) with optional collection filtering
- **download_text**: Download full OCR text from documents with automatic file selection

## Installation

### Install the code

```bash
uv sync
```

### Install to MCP CLIs

Installs to Claude Code, Codex CLI, and Gemini CLI:

```bash
uv run archives-mcp-install
```

Verify the installation:

```bash
claude mcp list   # For Claude Code
codex mcp list    # For Codex CLI
gemini mcp list   # For Gemini CLI
```

## Usage

Run the server directly:

```bash
uv run archives-mcp
```

Test with MCP Inspector:

```bash
uv run fastmcp dev src/archives_mcp/server.py
```
