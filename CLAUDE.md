# Internet Archive MCP - Internal Documentation

This document explains the inner workings of the Internet Archive MCP server implementation.

## Architecture Overview

The Internet Archive MCP server is built on three main components:

1. **client.py** - Core Internet Archive client with search and download functionality
2. **server.py** - FastMCP server exposing tools to MCP clients
3. **install.py** - CLI installation helper

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
   - Fetches results from `/services/search/beta/page_production/`
   - Processes hits into simplified document format
   - Deduplicates by item identifier
   - Extracts snippets with search term highlighting

3. **Search Result Processing**
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
   - **Location**: `./cache/archives/`
   - **Key format**: `{identifier}.txt` (filename auto-detected)
   - **Cache check**: First action in `download_text()` method (line 467-474)
   - **What's cached**:
     - Successful downloads → Full text content in file
     - 404 Not Found → String "404" in file (prevents repeated failed requests)
     - 401/403 Access Denied → String "404" in file (treated as unavailable)
   - **Cache write**: After successful download or confirmed failure (lines 480, 487, 498, 511, 516)
   - **Return value**: Path to cached file (or None if unavailable)

   Searches are NOT cached because:
   - Results change over time (new documents added)
   - Search results are small (just metadata + snippets)
   - Users expect fresh results
   - Different queries/pages need different results

2. **Text File Selection**
   Priority order for automatic file detection:
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
- `sanitize_snippet()` - Removes highlight markers and normalizes whitespace

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
        cache_dir = Path.cwd() / "cache" / "archives"
        _client = ArchivesClient(cache_dir=cache_dir)
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
cache/
└── archives/
    ├── {identifier}_{filename}.txt  # Cached text file content
    └── {identifier}.txt              # Cached text when auto-detected filename
```

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

In `download_text()` method (client.py:455-519):

1. **Lines 467-474**: Check cache first
   ```python
   cache_key = self._get_cache_key(identifier, file_name)
   cache_path = self.cache_dir / f"{cache_key}.txt"

   if cache_path.exists():
       cached_text = cache_path.read_text(encoding='utf-8')
       if cached_text == "404":
           return None  # Previously failed
       return cache_path  # Return path to cached file
   ```

2. **Lines 476-488**: If not cached, auto-detect and validate text file

3. **Lines 490-512**: Download from archive.org

4. **Line 511**: Write to cache and return path
   ```python
   self._write_to_cache(cache_key, content)
   return cache_path  # Return path, not content
   ```

5. **Lines 480, 487, 498, 516**: Cache "404" on failures

### Benefits

1. **Minimizes API calls** - Repeated requests return instantly
2. **Respects archive.org** - Reduces load on their servers
3. **Faster responses** - No network latency for cached items
4. **Offline capability** - Can work with previously downloaded items
5. **Prevents repeated failures** - Caching "404" stops futile retry attempts

### Implementation Details

**Cache methods** (client.py:610-626):
- `_get_cache_key()` - Generates cache filename from identifier/filename
- `_read_from_cache()` - Reads cached text if exists
- `_write_to_cache()` - Writes text to cache file

**No cache eviction** - Files remain cached indefinitely. Manual cleanup required if disk space is a concern.

**Thread safety** - Not currently implemented. Single-threaded usage assumed (typical for MCP servers).

## Rate Limiting and Concurrency Control

### Current Implementation

**NO rate limiting or concurrency control is implemented.**

Both search and download requests go directly to archive.org without delays or throttling:

- **Search** (client.py:370-372): Direct API call, no delay
  ```python
  client = await self._ensure_client()
  response = await client.get(url)  # No rate limiting
  ```

- **Download** (client.py:497-498): Direct download, no delay
  ```python
  client = await self._ensure_client()
  response = await client.get(download_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)  # No rate limiting
  ```

### Why No Concurrency Control?

Some Internet Archive client implementations include concurrency control such as:

1. **Search lock**: `_search_lock = asyncio.Lock()` - Only one search at a time across all instances
2. **Download semaphore**: `_download_semaphore = asyncio.Semaphore(10)` - Max 10 concurrent downloads

These were intentionally omitted because:
- MCP servers typically handle one request at a time (single-threaded)
- archive.org is relatively permissive (no strict rate limits for normal usage)
- Caching reduces repeated requests significantly
- Simpler code for MCP use case

### Why This Is Probably OK

1. **MCP is single-threaded**: Claude Code sends one request at a time
2. **Search is infrequent**: Users typically search once, get results, then download
3. **Downloads are cached**: Repeated downloads return instantly from cache
4. **archive.org is permissive**: No documented rate limits for reasonable usage
5. **Timeouts exist**: 60s for downloads, 10s for searches prevent hanging

### Risks

1. **Bulk operations**: If a user tries to download many documents rapidly, could overwhelm archive.org
2. **Multiple clients**: If multiple MCP clients connect, could send concurrent requests
3. **Getting blocked**: archive.org might temporarily block aggressive usage

### Future Improvement

If rate limiting becomes necessary, add:

```python
class ArchivesClient:
    _last_request_time: Optional[float] = None
    _request_lock = asyncio.Lock()

    async def _rate_limit(self):
        """Ensure at least 1 second between requests"""
        async with self._request_lock:
            if self._last_request_time is not None:
                elapsed = time.time() - self._last_request_time
                if elapsed < 1.0:
                    await asyncio.sleep(1.0 - elapsed)
            self._last_request_time = time.time()
```

Call before each request:
```python
await self._rate_limit()
response = await client.get(url)
```

## Error Handling

### Search Errors

- HTTP errors → Return error in response dict, empty documents list
- Parse errors → Return error in response dict
- Always returns valid dict structure for MCP tool contract

### Download Errors

- 404 Not Found → Cache "404", return None
- 401/403 Access Denied → Cache "404", return None
- Network errors → Return None
- Gzip decompression errors → Bubble up as exceptions

### Graceful Degradation

- File auto-detection via metadata API (prefers hOCR > DjVu > generic .txt)
- No valid text file → Return error message
- Invalid file type → Attempt to find alternative text file
- File not found (404) → Cache failure to prevent retries

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
2. **Rate limiting** - Implement request throttling to be respectful
3. **Batch downloads** - Download multiple documents efficiently
4. **Advanced filters** - Support more search parameters (date range, language, etc.)
5. **Result ranking** - Improve relevance scoring
6. **Snippet context** - Include more surrounding text in snippets

### Known Limitations

1. **No authentication** - Cannot access private/restricted items
2. **Text only** - Only handles OCR text, not images/PDFs/etc.
3. **English-focused** - Better for English documents (OCR quality varies)
4. **Cache management** - No automatic cache cleanup/expiration
5. **Error messages** - Could provide more specific guidance for failures

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
