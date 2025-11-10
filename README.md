# Internet Archive MCP Server

MCP server for [Internet Archive](https://archive.org/).
Search digitized books and documents, accessing their full OCR text:

- **search_archive**: Full-text search across document contents. Returns paginated results (100 docs/page) with text snippets showing matches in context. Supports complex queries with AND/OR/NOT operators, exact phrases, and optional collection filtering.
- **download_text**: Downloads complete OCR text from any document. Automatically selects the best available text format (hOCR, DjVu, or plain text) and caches results locally for fast repeated access.

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
