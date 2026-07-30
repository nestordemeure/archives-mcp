# Internet Archive MCP Server

MCP server for [Internet Archive](https://archive.org/). Search digitized books and documents, accessing their full OCR text:

- **search_archive**: Full-text search across document contents. Returns paginated results (100 docs/page) with text snippets showing matches in context. Supports complex queries with AND/OR/NOT operators, exact phrases, and optional filtering by collection, publication year, language, title, creator and subject.
- **download_text**: Downloads complete OCR text from any document. Automatically selects the best available text format (hOCR, DjVu, or plain text) and caches results locally for fast repeated access.

There are two ways to use it: an **MCP server** for clients that speak MCP, and an **`archives` CLI** for agents driven through a shell. Both share one client, one cache and one set of behaviours. The CLI is what the bundled `archives-search` skill uses.

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
archives search '"Charles Dickens"'                    # first page of results
archives search '"Charles Dickens"' --pages all        # every page, one invocation
archives search 'lighthouse' --collection americana    # restrict to one collection
archives get lighthousesthei01hardgoog                 # prints path to the cached OCR text
```

Search prints a total, then one block per document with snippets showing matched terms in `{braces}`. Add `--json` for machine-readable output.

#### Filters

`--from-year` / `--to-year` bound the publication year, `--language` takes a name or a MARC code (`French` or `fre` — archive.org accepts both from uploaders, so every known spelling of a named language is searched at once), and `--title`, `--creator` and `--subject` each match a phrase against that metadata field. All are optional and can be combined.

```bash
archives search '"Charles Dickens"' --from-year 1880 --to-year 1890
archives search '"Charles Dickens"' --language French
archives search '"Charles Dickens"' --title 'Household Words'
archives search '"Charles Dickens"' --creator 'Dickens, Charles'   # works by him, not about him
archives search '"Charles Dickens"' --subject 'Historical Journals'
archives search '"Charles Dickens"' --from-year 1880 --to-year 1890 --language English
```

Filters read the item's catalogue metadata, not the page a match was found on, so each one also drops every item whose metadata omits that field — and archive.org's metadata is uneven. Whole collections carry no year at all, and a year range will return nothing for them rather than everything. The active filters are echoed in the result header so a small total can be traced to its cause.

Downloads are cached in `$XDG_CACHE_HOME/archives-mcp` (override with `--cache-dir` or `ARCHIVES_CACHE_DIR`). The cache location does not depend on the working directory, so the CLI can be run from anywhere.

### MCP server

Run the server directly:

```bash
uv run archives-mcp
```

Test with MCP Inspector:

```bash
uv run fastmcp dev src/archives_mcp/server.py
```
