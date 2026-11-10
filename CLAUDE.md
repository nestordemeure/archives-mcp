# Internet Archive MCP - Internal Documentation

This document explains how the Internet Archive implementation operates.

## Architecture Overview

`client.py` holds all the behaviour. Each other module is a thin presentation layer over it, so the search semantics, the cache and the error handling stay identical for each method of access.

1. **client.py** - the core Internet Archive client, with the search and download functions
2. **server.py** - the FastMCP server that shows the tools to the MCP clients
3. **cli.py** - the `archives` command-line interface, which the `archives-search` skill uses
4. **paths.py** - the cache location, shared by the server and the CLI
5. **install.py** - the helper that installs the MCP server

### Why there are two front ends

The MCP server suits a client that speaks MCP. The CLI suits an agent driven through a shell: its tool schema costs nothing until a person runs the command, so it does not occupy the context in a session that never uses this archive. The bundled skill under `.claude/skills/archives-search/` documents the CLI for that use.

### paths.py

The cache must not depend on the working directory. The CLI has a global installation and runs from whichever project the researcher is in, so a cache relative to the working directory would put the downloads across unrelated folders and would destroy the hit rate. `cache_dir()` resolves the location in this order: an explicit override, then `ARCHIVES_CACHE_DIR`, then `$XDG_CACHE_HOME/archives-mcp` (`~/.cache` by default).

## Design Decisions

### Core Architecture

This implementation follows a clean modular architecture:

- The FastMCP framework handles the MCP protocol
- Async/await for each I/O operation
- A local cache, to keep the repeated API calls low
- A global client instance, for connection pooling

### Implementation Approach

The core search logic and download logic implement the fulltext search API of Internet Archive:

- The search function uses the fulltext search API, which has no documentation
- The download logic retrieves the OCR text files directly
- Intelligent detection of the text file (hOCR > DjVu > generic)
- A local cache, for performance

Key decisions:
- Simplified to the MCP uses (search + download)
- No datasource abstraction layer — a direct integration with the API
- A synchronous tool interface (the implementation is async internally)
- A simple result format, optimised for the MCP tool output

## Component Details

### client.py

#### Search Implementation

The search function uses the fulltext search API of Internet Archive:

1. **SearchParamURLGenerator** - builds the search URLs with the correct query parameters
   - Mirrors the TypeScript implementation from @internetarchive/search-service
   - Supports complex queries: AND, OR, NOT, exact phrases, parentheses
   - Handles the pagination, the field selection, the sorting and the aggregations

2. **ArchivesClient.search()** - the main search method
   - Builds the query with a `mediatype:texts` filter
   - An optional collection filter (for example, "iapsop")
   - Optional metadata filters: `from_year`/`to_year`, `language`, `title`, `creator`, `subject`
   - Fetches the results from `/services/search/beta/page_production/`
   - Processes the hits into a simplified document format
   - Removes the duplicates by item identifier
   - Extracts the snippets with the search terms highlighted

3. **Metadata filters**

   The client ANDs each filter onto the Lucene `user_query`. That is the only mechanism that the full-text backend honours, and the alternatives are risks and not simply unsupported:

   - `filter_map`, the faceting parameter of the search service itself, is **accepted and then ignored** by the `fts` backend. A request that carries `filter_map={"language":{"French":"inc"}}` gives HTTP 200 with the *unfiltered* total, which is identical to a filter that matched everything. Never send a filter through it.
   - A `date:[… TO …]` range makes the backend fail: the FTS API answers HTTP 502, and the gateway converts that into an HTTP 400. Only `year` is usable for a date, which is why the flags are `--from-year` and `--to-year` and not full dates.
   - An unknown field name gives `total=0`. It does not give an error, and it does not give the unfiltered set. Thus a spelling error in a filter looks exactly like "this material does not exist". For this reason the client shows no filter that the tests did not confirm live against a real query.

   `year_clause()` builds an open range (`year:[1880 TO *]`), so a caller can omit either bound. It raises `ValueError` when the range runs backwards, which would otherwise give a quiet zero. `language_clause()` expands a language to each spelling that archive.org is known to use, because the people who upload items supply a name (`French`), a MARC code (`fre`) or the terminology variant (`fra`), and those select different items: 1,391, 6,976 and a further part of the same corpus, for one test query. `metadata_clause()` builds the remaining filters as phrase matches, and `quote_phrase()` puts quotation marks around the values so that the server does not divide a two-word value into two loose terms.

   **Each of these filters reads the item metadata, and a large part of archive.org has none.** Each item in the `iapsop` collection, for example, carries no year, no date, no language, no creator and no subject. It carries only a title equal to its identifier. Thus any of these filters reduces an `iapsop` search to zero results. The behaviour is correct, and the documentation says so loudly. There is nothing to correct in the client.

   `ocr_detected_lang` (for example `ocr_detected_lang:en`) *is* queryable, and it does narrow a search, and it survives on items with no catalogued language. The client deliberately does not connect it to `--language`: it is the guess of the scanner about a scan and not a statement of the catalogue, and it uses a third vocabulary again (ISO 639-1). The skill documents it as an escape route inside the query instead.

4. **Search Result Processing**
   - `get_identifier()` - extracts the item id from a hit
   - `extract_scalar()` - unwraps the nested values of the API response
   - `extract_list()` - extracts the lists from the various response formats
   - `sanitize_snippet()` - cleans the highlight braces and the whitespace
   - `coerce_page()` - parses the page numbers from the results

#### Download Implementation

The download function retrieves the OCR text from an archive.org item and **returns the file path**, not the content.

**Key decision: it returns a path, not the content**

The `download_text()` method returns a `Path` to the cached file, not the text. This is deliberate:
- The OCR text files are very large (100KB-1MB or more)
- To return the full text to an LLM is wasteful
- The LLM can read parts of the file as it needs them
- It is more efficient for the MCP protocol

1. **Caching Strategy**

   **IMPORTANT: the client caches the downloads only, NOT the searches.**

   It caches the downloads for these reasons:
   - The OCR text files are large (frequently 100KB-1MB or more)
   - The content is static (it does not change after publication)
   - It lowers the load on the servers of archive.org
   - It improves the response time greatly (immediate against seconds)

   Cache behaviour:
   - **Location**: `$XDG_CACHE_HOME/archives-mcp/` (see `paths.py`)
   - **Key format**: `{identifier}.txt` (the client detects the filename)
   - **Cache check**: the first action in `download_text()`
   - **What the client caches**:
     - A successful download → the full text content in a file
     - HTTP 404 Not Found → `UNAVAILABLE_SENTINEL` (which prevents repeated failed requests)
     - HTTP 401/403 Access Denied → **not cached**; raised instead, because archive.org gives 403 both for a restricted item and for a throttled request, and a throttled request must not mark a document permanently unavailable
   - **Cache write**: after a successful download, or after a confirmed 404
   - **Return value**: the path to the cached file (or None if the item is unavailable)

   It does NOT cache the searches, for these reasons:
   - The results change over time (people add new documents)
   - The search results are small (metadata and snippets only)
   - Users expect fresh results
   - Different queries and pages need different results

2. **Text File Selection.** The priority order for automatic detection of the file:
   - hOCR format (`_hocr.txt`) - structured OCR with position information
   - DjVuTXT format (`_djvu.txt`) - high-quality DjVu OCR
   - Generic `.txt` files - the last option

3. **File Validation**
   - `_is_valid_text_file()` - rejects a file that is not text
   - Removes the ABBYY XML, the metadata files and the scan data
   - Accepts only the `.txt` and `.txt.gz` extensions

4. **Metadata API**
   - `_fetch_item_files_metadata()` - gets the file list for an item
   - Endpoint: `https://archive.org/metadata/{identifier}/files`
   - Returns a list of the available files with their format information

5. **Download Process**
   - Build the download URL: `https://archive.org/download/{identifier}/{filename}`
   - Decompress a `.gz` file
   - Handle the 404, 401 and 403 errors correctly
   - Put the results in the cache, to prevent repeated downloads

#### Utility Functions

- `_safe_int()` - a safe conversion to an integer, with a fallback
- `build_item_url()` - builds the archive.org detail page URLs
- `sanitize_snippet()` - normalises the whitespace, and keeps the `{brace}` match highlights by default

### server.py

#### FastMCP Integration

The server uses the FastMCP decorators to show the tools:

1. **@mcp.tool()** - marks an async function as an MCP tool
2. **@mcp.resource()** - gives a static information resource

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
- Returns the results in pages (100 documents for each page)
- An optional collection filter
- Creates a collection-specific client where necessary

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

- Downloads the OCR text for an item and writes it to the cache
- Detects the text file automatically (hOCR > DjVu > generic)
- **Returns the path to the cached file** (as a string), not the content
- Returns an error message if the document is not accessible
- Uses the cache, to keep the downloads low

**Important**: the returned path points at a large text file. The LLM must read it with the Read tool, and must take the parts that it needs. It must not load the full file into the context.

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

- One global client instance, for connection pooling
- Lazy initialisation at the first use
- A persistent cache directory

### install.py

#### CLI Installation

The install script adds the MCP server to the supported CLIs:

1. **install_claude()** - installs to Claude Code
   ```bash
   claude mcp add --transport stdio --scope user archives -- \
     uv --directory <project_dir> run archives-mcp
   ```

2. **install_codex()** - installs to the Codex CLI
   ```bash
   codex mcp add archives -- \
     uv --directory <project_dir> run archives-mcp
   ```

3. **install_gemini()** - installs to the Gemini CLI
   ```bash
   gemini mcp add archives \
     uv --directory <project_dir> run archives-mcp
   ```

#### Reasons for the design

- It uses `uv --directory`, so that the commands run from the project directory
- Thus the server can find its dependencies and its cache directory
- It handles an absent CLI correctly (it gives a warning, and it does not fail)
- It uses absolute paths, to prevent ambiguity

## API Integration

### Internet Archive Fulltext Search

The search implementation uses the fulltext search API, which has no documentation:

- **Endpoint**: `https://archive.org/services/search/beta/page_production/`
- **Backend**: `service_backend=fts` (fulltext search)
- **Based on**: the TypeScript implementation from @internetarchive/search-service
  - SearchParamURLGenerator: https://github.com/internetarchive/iaux-search-service/blob/main/src/search-param-url-generator.ts
  - FulltextSearchBackend: https://github.com/internetarchive/iaux-search-service/blob/main/src/search-backend/fulltext-search-backend.ts

### Download URLs

The direct download URLs follow this pattern:
```
https://archive.org/download/{identifier}/{filename}
```

A public item needs no authentication.

### Metadata API

The client retrieves the file listings from:
```
https://archive.org/metadata/{identifier}/files
```

This gives JSON with the available files and their metadata.

## Caching Strategy

### What the client caches

**The downloads ONLY.** The client caches the OCR text files that it downloads.

**It does NOT cache the searches.** A search request always reaches the API, so that the results are fresh.

### Why

**The client must cache the downloads:**
- The files are large (100KB-1MB or more of text)
- The content is static (it does not change after publication)
- To fetch them repeatedly is expensive
- It lowers the server load on archive.org
- It gives an immediate response to a repeated request

**The client must NOT cache the searches:**
- The payloads are small (metadata and snippets)
- The results are dynamic (people add new documents regularly)
- Users expect fresh results
- The queries vary, so the cache would have a low hit rate

### Cache Directory Structure

```
$XDG_CACHE_HOME/archives-mcp/
├── {identifier}_{filename}.txt  # Cached text file content
└── {identifier}.txt              # Cached text when auto-detected filename
```

`paths.cache_dir()` resolves this; change it with `--cache-dir` or `ARCHIVES_CACHE_DIR`.

### Cache Keys

The `_get_cache_key()` method generates them (line 610-614):
- With a filename: `{identifier}_{filename}` (with `/` replaced by `_`)
- Without a filename: `{identifier}` (with `/` replaced by `_`)

Example:
- `nothingsostrange0000arth.txt`
- `spiritualistmagaz00amer_djvu.txt.txt`

### Cache Contents

The cache stores plain text in `.txt` files:

- **A successful download**: the full OCR text content
- **A failed download**: the string `"404"` (which prevents repeated failed requests)

This means:
- If you see `"404"` in the cache, the server did not find the file, or the file is restricted
- If you see real content, it is the full OCR text from archive.org

### Cache Flow

In `download_text()`:

1. Return the cached path unless the caller asked for `refresh`. A sentinel entry returns None.
2. Detect the text file automatically when the caller named none, or when the named file is not text.
3. Download the file, decode it (and decompress a `.gz` file), and write it to the cache.
4. Write the sentinel only for a confirmed 404, or for an item with no text file.

### Benefits

1. **Keeps the API calls low** - a repeated request returns immediately
2. **Respects archive.org** - it lowers the load on their servers
3. **Faster responses** - no network delay for a cached item
4. **Operates offline** - it can use the items that it downloaded earlier
5. **Prevents repeated failures** - the cached "404" stops the futile attempts

### Implementation Details

**Cache methods**:
- `_get_cache_key()` - generates the cache filename from the identifier and the filename
- `_write_to_cache()` - writes the text to the cache file
- `_is_unavailable_marker()` - detects the sentinel. It examines the file size first, so it never reads a multi-megabyte entry only to compare it against a 3-byte string.

**No cache eviction** - the files stay in the cache. A person must clean them manually if the disk space is a concern.

**Thread safety** - not implemented now. The code assumes single-threaded use, which is typical for an MCP server.

## Rate Limiting

A cross-process rate limiter (`ratelimit.py`) spaces the requests. The default is 0.5s, and `ARCHIVES_MIN_REQUEST_INTERVAL` changes it. The search requests, the metadata requests and the download requests all pass through it.

**Why the limiter operates across processes, and not as an instance attribute or a class attribute.** An in-process limiter was sufficient while the only caller was a long-lived MCP server that handled one request at a time. It is not sufficient now: each call of the CLI is a separate process with its own state, and we expect the callers to divide the work across several processes at the same time. Thus any in-process counter paces nothing. The limiter keeps its timestamp in `.rate-limit` inside the cache directory, with an exclusive `flock` to guard it, and each process that shares that cache obeys it.

The lock is never held across a sleep: a waiter releases it and tries again, so one slow caller cannot block the others behind it. The limiter uses wall-clock time and not monotonic time, because only the wall clock is comparable between processes.

## Error Handling

### Search Errors

- HTTP 400 with "the FTS API request failed ... HTTP 502" in `response.error.message` → the backend failed. The same body comes for a query that the backend cannot run (a `date:` range) and for a temporary failure: a long boolean query failed one day and worked the next. `search` tries again one time after `BACKEND_RETRY_SECONDS`, then returns the server message with `BACKEND_FAILURE_ADVICE`.
- HTTP errors → return the error in the response dict, with an empty documents list
- Parse errors → return the error in the response dict
- The method always returns a valid dict structure, for the MCP tool contract

### Download Errors

The governing rule: `None` means "archive.org says that this does not exist". The client raises anything that a second attempt can resolve, so it never mistakes a temporary failure for an absent document, and it never puts one in the cache as one.

- HTTP 404 Not Found → cache the sentinel, return None
- The item has no text file → cache the sentinel, return None
- HTTP 401/403 Access Denied → **raise** (restricted or throttled — the two are identical from here)
- Network errors → **raise**
- The Metadata API is unreachable → **raise** (an earlier version absorbed this, which cached readable documents as permanently unavailable)
- Gzip errors and decode errors → **raise** as a `RuntimeError`

An unknown identifier is not an error: archive.org answers the files endpoint with `{"error": "Couldn't locate item ..."}`, which the client reads as "no files" and caches as unavailable.

### Graceful Degradation

- The client detects the file automatically through the metadata API (it prefers hOCR > DjVu > generic .txt)
- An invalid file type → the client tries to find an alternative text file

## Testing

### Manual Testing

Run the server in development mode:

```bash
uv run fastmcp dev src/archives_mcp/server.py
```

This opens the MCP Inspector for interactive tests.

### Integration Testing

Test the full installation:

1. Install to a CLI: `uv run archives-mcp-install`
2. Restart the CLI
3. Use the tools in a conversation
4. Examine the cache directory for the downloaded files

## Future Improvements

### Possible Improvements

1. **Retry logic** - add exponential backoff for a temporary failure
2. **Batch downloads** - download several documents efficiently
3. **Result ranking** - improve the relevance score
4. **Snippet context** - include more text around the snippets

### Known Limitations

1. **No authentication** - the client cannot access a private or restricted item
2. **Text only** - it handles the OCR text only, not the images, the PDFs or other formats
3. **Focused on English** - it is better for English documents (the OCR quality varies)
4. **Cache management** - no automatic cleaning and no expiry
5. **Error messages** - they could give more specific guidance for a failure
6. **The metadata filters reach the catalogued items only** - the item metadata of archive.org is uneven, so a year, language, creator or subject filter excludes each item that a person uploaded without it. Nothing in the client can recover a field that the archive does not hold.

## Appendix: Query Syntax

The search supports the Lucene-style query syntax:

- **AND** (implicit): `magic tricks` = documents with both "magic" AND "tricks"
- **OR**: `Houdini OR Kellar` = documents with either name
- **NOT**: `magic -card` = documents with "magic" but not "card"
- **Exact phrase**: `"card tricks"` = exact phrase match
- **Grouping**: `(Houdini OR Kellar) AND escape` = compound logic
- **Field search**: `creator:Houdini` = search specific field
- **Wildcards**: `magi*` = matches "magic", "magician", etc.

The search engine of Internet Archive handles this. Our code does not.
