# Internet Archive MCP Server

MCP server for [Internet Archive](https://archive.org/).
Search digitized books and documents, accessing their full OCR text:

- **search_archive**: Full-text search across document contents. Returns paginated results (100 docs/page) with text snippets showing matches in context. Supports complex queries with AND/OR/NOT operators, exact phrases, and optional collection filtering.
- **download_text**: Downloads complete OCR text from any document. Automatically selects the best available text format (hOCR, DjVu, or plain text) and caches results locally for fast repeated access.

There are two ways to use it: an **MCP server** for clients that speak MCP, and an
**`archives` CLI** for agents driven through a shell. Both share one client, one cache and
one set of behaviours. The CLI is what the bundled `archives-search` skill uses.

## Installation

### Install the code

```bash
uv sync
```

### Install the CLI

```bash
uv tool install .        # puts `archives` on your PATH
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

### CLI

```bash
archives search '"Bert Reese"'                   # first page of results
archives search '"Bert Reese"' --pages all       # every page, one invocation
archives search 'billet' --collection iapsop     # restrict to one collection
archives get PsychicalResearchReview1917         # prints path to the cached OCR text
```

Search prints a total, then one block per document with snippets showing matched terms in
`{braces}`. Add `--json` for machine-readable output.

Downloads are cached in `$XDG_CACHE_HOME/mentalism-research/archives` (override with
`--cache-dir` or `ARCHIVES_CACHE_DIR`). The cache location does not depend on the working
directory, so the CLI can be run from anywhere.

### MCP server

Run the server directly:

```bash
uv run archives-mcp
```

Test with MCP Inspector:

```bash
uv run fastmcp dev src/archives_mcp/server.py
```
