# Internet Archive MCP - Internal Documentation

This document explains the inner workings of the Internet Archive implementation.

## Architecture Overview

`client.py` holds all the behaviour. Everything else is a thin presentation layer over it, so search semantics, caching and error handling stay identical no matter how it is called.

1. **client.py** - Core Internet Archive client with search and download functionality
2. **server.py** - FastMCP server exposing tools to MCP clients
3. **cli.py** - `archives` command-line interface, used by the `archives-search` skill
4. **paths.py** - Cache location resolution, shared by the server and the CLI
5. **install.py** - MCP server installation helper

### Why two front-ends

The MCP server suits clients that speak MCP. The CLI suits agents driven through a shell: its tool schema costs nothing until the command is actually run, so it does not occupy context in sessions that never touch this archive. The bundled skill under `.claude/skills/archives-search/` documents the CLI for that use.

### paths.py

The cache must not depend on the working directory. The CLI is installed globally and run from whatever project the researcher is in, so a CWD-relative cache would scatter downloads across unrelated folders and destroy the hit rate. `cache_dir()` resolves, in order: an explicit override, `ARCHIVES_CACHE_DIR`, then `$XDG_CACHE_HOME/archives-mcp` (`~/.cache` by default).

## Design Decisions

### Core Architecture

This implementation follows a clean, modular architecture:

- FastMCP framework for MCP protocol handling
- Async/await for all I/O operations
- Local caching to minimize repeated API calls
- Global client instance for connection pooling

### Implementation Approach

The core search and download logic implements Internet Archive's fulltext search API:

- Search functionality uses the undocumented fulltext search API
- Download logic retrieves OCR text files directly
- Intelligent text file detection (hOCR > DjVu > generic)
- Local caching for performance

Key design choices:
- Simplified to focus on MCP use cases (search + download)
- No datasource abstraction layer - direct API integration
- Synchronous tool interface (async implementation internal)
- Simple result format optimized for MCP tool output

## Component Details

### client.py

#### Search Implementation

The search functionality uses Internet Archive's fulltext search API:

1. **SearchParamURLGenerator** - Builds search URLs with proper query parameters
   - Mirrors the TypeScript implementation from @internetarchive/search-service
   - Supports complex queries: AND, OR, NOT, exact phrases, parentheses
   - Handles pagination, field selection, sorting, aggregations

2. **ArchivesClient.search()** - Main search method
   - Constructs query with mediatype:texts filter
   - Optional collection filtering (e.g., "iapsop")
   - Optional metadata filters: `from_year`/`to_year`, `language`, `title`, `creator`, `subject`
   - Fetches results from `/services/search/beta/page_production/`
   - Processes hits into simplified document format
   - Deduplicates by item identifier
   - Extracts snippets with search term highlighting

3. **Metadata filters**

   Every filter is ANDed onto the Lucene `user_query`. That is the only mechanism the full-text backend honours, and the alternatives are traps rather than merely unsupported:

   - `filter_map`, the search service's own faceting parameter, is **accepted and then silently ignored** by the `fts` backend. A request carrying `filter_map={"language":{"French":"inc"}}` returns HTTP 200 with the *unfiltered* total, which is indistinguishable from a filter that matched everything. Never route a filter through it.
   - A `date:[… TO …]` range makes the backend fail: the FTS API answers 502 and the gateway turns that into a 400. Only `year` is usable for dating, which is why the flags are `--from-year`/`--to-year` rather than full dates.
   - An unknown field name yields `total=0`, not an error and not the unfiltered set. So a filter typo looks exactly like "this material does not exist" — hence no filter is exposed that has not been confirmed to narrow a real query live.

   `year_clause()` builds an open-ended range (`year:[1880 TO *]`) so either bound may be omitted, and raises `ValueError` when the range runs backwards, which would otherwise return a silent zero. `language_clause()` expands a language to every spelling archive.org is known to use, because uploaders supply a name (`French`), a MARC code (`fre`) or the terminology variant (`fra`) and those select different items: 1,391, 6,976 and a further slice of the same corpus respectively for one test query. The remaining filters are phrase matches built by `metadata_clause()`, with values quoted by `quote_phrase()` so that a two-word value is not split into two loose terms.

   **All of these filters read item metadata, and a great deal of archive.org has none.** Every item in the `iapsop` collection, for instance, carries no year, date, language, creator or subject whatsoever - only a title equal to its identifier. Any of these filters therefore reduces an iapsop search to zero results. The behaviour is correct and the docs say so loudly; there is nothing to fix in the client.

   `ocr_detected_lang` (e.g. `ocr_detected_lang:en`) *is* queryable and does narrow, and it survives on items with no catalogued language. It is deliberately not wired to `--language`: it is the scanner's guess about a scan rather than a catalogue statement, and it uses a third vocabulary (ISO 639-1) again. The skill documents it as an in-query escape hatch instead.

4. **Search Result Processing**
   - `get_identifier()` - Extracts item ID from hit
   - `extract_scalar()` - Unwraps nested API response values
   - `extract_list()` - Extracts lists from various response formats
   - `sanitize_snippet()` - Cleans up highlight braces and whitespace
   - `coerce_page()` - Parses page numbers from results

#### Download Implementation

The download functionality retrieves OCR text from archive.org items and **returns the file path**, not the content:

**Key Design: Returns Path, Not Content**

The `download_text()` method returns a `Path` to the cached file, not the text itself. This is intentional:
- OCR text files are very large (100KB-1MB+)
- Returning full text to LLM is wasteful
- LLM can read portions of the file as needed
- More efficient for MCP protocol

1. **Caching Strategy**

   **IMPORTANT: Only downloads are cached, NOT searches.**

   Downloads are cached because:
   - OCR text files are large (often 100KB-1MB+)
   - Content is static (doesn't change once published)
   - Reduces load on archive.org servers
   - Improves response time dramatically (instant vs. seconds)

   Cache behavior:
   - **Location**: `$XDG_CACHE_HOME/archives-mcp/` (see `paths.py`)
   - **Key format**: `{identifier}.txt` (filename auto-detected)
   - **Cache check**: First action in `download_text()`
   - **What's cached**:
     - Successful downloads → Full text content in file
     - 404 Not Found → `UNAVAILABLE_SENTINEL` (prevents repeated failed requests)
     - 401/403 Access Denied → **not cached**; raised instead, since archive.org returns 403 both for restricted items and for throttled requests, and a throttled request must not mark a document permanently unavailable
   - **Cache write**: After a successful download, or a confirmed 404
   - **Return value**: Path to cached file (or None if unavailable)

   Searches are NOT cached because:
   - Results change over time (new documents added)
   - Search results are small (just metadata + snippets)
   - Users expect fresh results
   - Different queries/pages need different results

2. **Text File Selection** Priority order for automatic file detection:
   - hOCR format (`_hocr.txt`) - structured OCR with positioning info
   - DjVuTXT format (`_djvu.txt`) - high-quality DjVu OCR
   - Generic `.txt` files - fallback

3. **File Validation**
   - `_is_valid_text_file()` - Rejects non-text files
   - Filters out ABBYY XML, metadata files, scan data
   - Only accepts `.txt` and `.txt.gz` extensions

4. **Metadata API**
   - `_fetch_item_files_metadata()` - Gets file list for an item
   - Endpoint: `https://archive.org/metadata/{identifier}/files`
   - Returns list of available files with format info

5. **Download Process**
   - Construct download URL: `https://archive.org/download/{identifier}/{filename}`
   - Handle gzip decompression for `.gz` files
   - Handle 404, 401, 403 errors gracefully
   - Cache results to avoid repeated downloads

#### Utility Functions

- `_safe_int()` - Safe integer conversion with fallback
- `build_item_url()` - Constructs archive.org detail page URLs
- `sanitize_snippet()` - Normalizes whitespace, keeping `{brace}` match highlights by default

### server.py

#### FastMCP Integration

The server uses FastMCP decorators to expose tools:

1. **@mcp.tool()** - Marks async functions as MCP tools
2. **@mcp.resource()** - Provides static information resources

#### Tools

##### search_archive

```python
async def search_archive(
    query: str,
    page: int = 1,
    collection: Optional[str] = None
) -> dict
```

- Searches Internet Archive for text documents
- Returns paginated results (100 docs/page)
- Optional collection filtering
- Handles collection-specific client creation

Return format:
```python
{
    'page': 1,
    'total_results': 1234,
    'documents': [
        {
            'identifier': 'item_id',
            'title': 'Document Title',
            'url': 'https://archive.org/details/item_id',
            'creators': ['Author Name'],
            'date': '2020-01-01',
            'snippets': ['...text excerpt...'],
            'page': 42
        }
    ]
}
```

##### download_text

```python
async def download_text(identifier: str) -> str
```

- Downloads OCR text for an item and saves to cache
- Automatic text file detection (hOCR > DjVu > generic)
- **Returns path to cached file** (as string), not the content
- Returns error message if document not accessible
- Uses caching to minimize downloads

**Important**: The returned path points to a large text file. The LLM should read it using Read tool to access portions as needed, rather than loading the entire file into context.

#### Global Client Management

```python
_client: Optional[ArchivesClient] = None

async def get_client() -> ArchivesClient:
    """Get or create the global Archives client"""
    global _client
    if _client is None:
        _client = ArchivesClient(cache_dir=cache_dir())
    return _client
```

- Single global client instance for connection pooling
- Lazy initialization on first use
- Persistent cache directory

### install.py

#### CLI Installation

The install script automatically adds the MCP server to supported CLIs:

1. **install_claude()** - Installs to Claude Code
   ```bash
   claude mcp add --transport stdio --scope user archives -- \
     uv --directory <project_dir> run archives-mcp
   ```

2. **install_codex()** - Installs to Codex CLI
   ```bash
   codex mcp add archives -- \
     uv --directory <project_dir> run archives-mcp
   ```

3. **install_gemini()** - Installs to Gemini CLI
   ```bash
   gemini mcp add archives \
     uv --directory <project_dir> run archives-mcp
   ```

#### Design Rationale

- Uses `uv --directory` to ensure commands run from project directory
- This allows the server to find dependencies and cache directory
- Gracefully handles missing CLIs (warns but doesn't fail)
- Uses absolute paths to avoid ambiguity

## API Integration

### Internet Archive Fulltext Search

The search implementation uses the undocumented fulltext search API:

- **Endpoint**: `https://archive.org/services/search/beta/page_production/`
- **Backend**: `service_backend=fts` (fulltext search)
- **Based on**: TypeScript implementation from @internetarchive/search-service
  - SearchParamURLGenerator: https://github.com/internetarchive/iaux-search-service/blob/main/src/search-param-url-generator.ts
  - FulltextSearchBackend: https://github.com/internetarchive/iaux-search-service/blob/main/src/search-backend/fulltext-search-backend.ts

### Download URLs

Direct download URLs follow the pattern:
```
https://archive.org/download/{identifier}/{filename}
```

No authentication required for public items.

### Metadata API

File listings are retrieved from:
```
https://archive.org/metadata/{identifier}/files
```

Returns JSON with available files and their metadata.

## Caching Strategy

### What Gets Cached?

**Downloads ONLY** - The system caches downloaded OCR text files.

**Searches are NOT cached** - Search requests always hit the API to get fresh results.

### Why This Approach?

**Downloads should be cached:**
- Large files (100KB-1MB+ of text)
- Static content (doesn't change after publication)
- Expensive to fetch repeatedly
- Reduces server load on archive.org
- Instant response for repeated requests

**Searches should NOT be cached:**
- Small payloads (metadata + snippets)
- Dynamic results (new documents added regularly)
- Users expect fresh results
- Query variations mean cache would have low hit rate

### Cache Directory Structure

```
$XDG_CACHE_HOME/archives-mcp/
├── {identifier}_{filename}.txt  # Cached text file content
└── {identifier}.txt              # Cached text when auto-detected filename
```

Resolved by `paths.cache_dir()`; override with `--cache-dir` or `ARCHIVES_CACHE_DIR`.

### Cache Keys

Generated by `_get_cache_key()` method (line 610-614):
- With filename: `{identifier}_{filename}` (with `/` replaced by `_`)
- Without filename: `{identifier}` (with `/` replaced by `_`)

Example:
- `nothingsostrange0000arth.txt`
- `spiritualistmagaz00amer_djvu.txt.txt`

### Cache Contents

The cache stores plain text in `.txt` files:

- **Successful downloads**: Full OCR text content
- **Failed downloads**: String `"404"` (to avoid repeated failed requests)

This means:
- If you see `"404"` in cache, the file was not found or is restricted
- If you see real content, it's the full OCR text from archive.org

### Cache Flow

In `download_text()`:

1. Return the cached path unless `refresh` was asked for; a sentinel entry returns None.
2. Auto-detect the text file when none was named, or the named one is not text.
3. Download, decode (decompressing `.gz`), and write to the cache.
4. Write the sentinel only for a confirmed 404 or an item with no text file.

### Benefits

1. **Minimizes API calls** - Repeated requests return instantly
2. **Respects archive.org** - Reduces load on their servers
3. **Faster responses** - No network latency for cached items
4. **Offline capability** - Can work with previously downloaded items
5. **Prevents repeated failures** - Caching "404" stops futile retry attempts

### Implementation Details

**Cache methods**:
- `_get_cache_key()` - Generates cache filename from identifier/filename
- `_write_to_cache()` - Writes text to cache file
- `_is_unavailable_marker()` - Detects the sentinel, checking file size first so a multi-megabyte entry is never read just to compare it against a 3-byte string

**No cache eviction** - Files remain cached indefinitely. Manual cleanup required if disk space is a concern.

**Thread safety** - Not currently implemented. Single-threaded usage assumed (typical for MCP servers).

## Rate Limiting

Requests are spaced by a cross-process rate limiter (`ratelimit.py`), default 0.5s, overridable with `ARCHIVES_MIN_REQUEST_INTERVAL`. Search, metadata and download requests all pass through it.

**Why cross-process rather than an instance or class attribute.** An in-process limiter was adequate while the only caller was a long-lived MCP server handling one request at a time. It is not adequate now: every CLI invocation is a separate process with its own state, and callers are expected to fan work out across several at once, so any in-process counter paces nothing. The limiter therefore keeps its timestamp in `.rate-limit` inside the cache directory, guarded by an exclusive `flock`, which every process sharing that cache observes.

The lock is never held across a sleep: a waiter releases it and retries, so one slow caller cannot wedge the others behind it. Wall-clock time is used rather than monotonic, since only wall clock is comparable between processes.

## Error Handling

### Search Errors

- HTTP errors → Return error in response dict, empty documents list
- Parse errors → Return error in response dict
- Always returns valid dict structure for MCP tool contract

### Download Errors

The governing rule: `None` means "archive.org says this does not exist". Anything that might succeed on a retry is raised, so that a transient failure is never mistaken for an absent document and never cached as one.

- 404 Not Found → cache the sentinel, return None
- Item has no text file → cache the sentinel, return None
- 401/403 Access Denied → **raise** (restricted or throttled - indistinguishable)
- Network errors → **raise**
- Metadata API unreachable → **raise** (previously swallowed, which cached readable documents as permanently unavailable)
- Gzip / decode errors → **raise** as `RuntimeError`

An unknown identifier is not an error: archive.org answers the files endpoint with `{"error": "Couldn't locate item ..."}`, which is read as "no files" and cached as unavailable.

### Graceful Degradation

- File auto-detection via metadata API (prefers hOCR > DjVu > generic .txt)
- Invalid file type → Attempt to find alternative text file

## Testing

### Manual Testing

Run the server in development mode:

```bash
uv run fastmcp dev src/archives_mcp/server.py
```

This opens the MCP Inspector for interactive testing.

### Integration Testing

Test the full installation:

1. Install to CLI: `uv run archives-mcp-install`
2. Restart CLI
3. Use the tools in a conversation
4. Check cache directory for downloaded files

## Future Improvements

### Potential Enhancements

1. **Retry logic** - Add exponential backoff for transient failures
2. **Batch downloads** - Download multiple documents efficiently
3. **Result ranking** - Improve relevance scoring
4. **Snippet context** - Include more surrounding text in snippets

### Known Limitations

1. **No authentication** - Cannot access private/restricted items
2. **Text only** - Only handles OCR text, not images/PDFs/etc.
3. **English-focused** - Better for English documents (OCR quality varies)
4. **Cache management** - No automatic cache cleanup/expiration
5. **Error messages** - Could provide more specific guidance for failures
6. **Metadata filters only reach catalogued items** - archive.org's item metadata is uneven, so a year, language, creator or subject filter excludes everything that was uploaded without it. Nothing in the client can recover a field the archive does not hold

## Appendix: Query Syntax

The search supports Lucene-style query syntax:

- **AND** (implicit): `magic tricks` = documents with both "magic" AND "tricks"
- **OR**: `Houdini OR Kellar` = documents with either name
- **NOT**: `magic -card` = documents with "magic" but not "card"
- **Exact phrase**: `"card tricks"` = exact phrase match
- **Grouping**: `(Houdini OR Kellar) AND escape` = compound logic
- **Field search**: `creator:Houdini` = search specific field
- **Wildcards**: `magi*` = matches "magic", "magician", etc.

This is handled by Internet Archive's search engine, not by our code.
