"""
Internet Archive client for MCP server.

Provides access to search and download OCR text from archive.org.
"""

import asyncio
import gzip
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, quote

import httpx

from .paths import cache_dir as default_cache_dir
from .ratelimit import CrossProcessRateLimiter, configured_interval


# HTTP status codes
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
DOWNLOAD_TIMEOUT_SECONDS = 60.0
METADATA_TIMEOUT_SECONDS = 30.0
ARCHIVE_BASE_URL = "https://archive.org"

# Written in place of a document's text when archive.org confirms it does not
# exist. Only a true 404 earns this: a transient failure must never be recorded
# as permanent, or the document becomes invisible to every later search.
UNAVAILABLE_SENTINEL = "404"


# archive.org stores an item's language as whatever the uploader supplied: an
# English name ("French"), a MARC bibliographic code ("fre"), or the ISO 639-2/T
# terminology variant ("fra"). Those forms select different, only partly
# overlapping sets: a "Charles Dickens" search filtered to language:French
# returns 1,391 items, while language:fre returns 6,976 and the two together
# return 8,367. A filter that passed a single form through would therefore hide
# most of the material it claims to select, so every known form of a language is
# searched at once.
_LANGUAGE_FORMS: tuple[tuple[str, ...], ...] = (
    ("English", "eng"),
    ("French", "fre", "fra"),
    ("German", "ger", "deu"),
    ("Spanish", "spa"),
    ("Italian", "ita"),
    ("Dutch", "dut", "nld"),
    ("Portuguese", "por"),
    ("Latin", "lat"),
    ("Greek", "gre", "ell", "grc"),
    ("Russian", "rus"),
    ("Polish", "pol"),
    ("Czech", "cze", "ces"),
    ("Hungarian", "hun"),
    ("Romanian", "rum", "ron"),
    ("Swedish", "swe"),
    ("Danish", "dan"),
    ("Norwegian", "nor"),
    ("Finnish", "fin"),
    ("Icelandic", "ice", "isl"),
    ("Welsh", "wel", "cym"),
    ("Catalan", "cat"),
    ("Turkish", "tur"),
    ("Hebrew", "heb"),
    ("Yiddish", "yid"),
    ("Arabic", "ara"),
    ("Chinese", "chi", "zho"),
    ("Japanese", "jpn"),
    ("Hindi", "hin"),
    ("Urdu", "urd"),
    ("Sanskrit", "san"),
)

LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    form.lower(): group for group in _LANGUAGE_FORMS for form in group
}


class SearchServiceErrorType(str, Enum):
    NETWORK = "networkError"
    DECODING = "decodingError"
    SEARCH_ENGINE = "searchEngineError"
    UNKNOWN = "unknownError"


@dataclass
class SearchServiceError:
    error_type: SearchServiceErrorType
    message: str
    details: Any | None = None


@dataclass
class SortParam:
    field: str
    direction: str


@dataclass
class AggregationsParam:
    omit: bool = False
    advanced_params: list[dict[str, Any]] | None = None
    simple_params: list[str] | None = None


@dataclass
class SearchParams:
    query: str | None = None
    rows: int | None = None
    page: int | None = None
    sort: list[SortParam] | None = None
    fields: list[str] | None = None
    include_client_url: bool | None = True
    aggregations: AggregationsParam | None = None
    debugging: bool | None = None
    filters: dict[str, Any] | None = None
    page_type: str | None = None
    page_target: str | None = None
    page_elements: list[str] | None = None
    aggregations_size: int | None = None
    uid: str | None = None

    def __post_init__(self) -> None:
        if self.sort is None:
            object.__setattr__(self, "sort", [])
        if self.fields is None:
            object.__setattr__(self, "fields", [])


class SearchParamURLGenerator:
    @staticmethod
    def _aggregations_to_string(aggregations: AggregationsParam) -> str | None:
        if aggregations.omit:
            return "false"
        if aggregations.advanced_params:
            payload = [{"terms": param} for param in aggregations.advanced_params]
            return json.dumps(payload, separators=(",", ":"))
        if aggregations.simple_params:
            return ",".join(aggregations.simple_params)
        return None

    @staticmethod
    def generate(search_params: SearchParams) -> str:
        entries: list[tuple[str, str]] = []

        SearchParamURLGenerator._append_basic_values(entries, search_params)
        SearchParamURLGenerator._append_page_elements(entries, search_params)
        SearchParamURLGenerator._append_fields(entries, search_params)
        SearchParamURLGenerator._append_filters(entries, search_params)
        SearchParamURLGenerator._append_sort(entries, search_params)
        SearchParamURLGenerator._append_aggregations(entries, search_params)
        SearchParamURLGenerator._append_debugging(entries, search_params)
        SearchParamURLGenerator._append_client_url(entries, search_params)

        return urlencode(entries)

    @staticmethod
    def _append_basic_values(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        simple_values = [
            ("user_query", search_params.query),
            ("page_type", search_params.page_type),
            ("page_target", search_params.page_target),
            ("hits_per_page", search_params.rows),
            ("page", search_params.page),
            ("aggregations_size", search_params.aggregations_size),
            ("uid", search_params.uid),
        ]
        for key, value in simple_values:
            if value is None:
                continue
            entries.append((key, str(value)))

    @staticmethod
    def _append_page_elements(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if not search_params.page_elements:
            return
        quoted = [f'"{element}"' for element in search_params.page_elements]
        entries.append(("page_elements", f"[{','.join(quoted)}]"))

    @staticmethod
    def _append_fields(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if search_params.fields:
            entries.append(("fields", ",".join(search_params.fields)))

    @staticmethod
    def _append_filters(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if not search_params.filters:
            return
        filtered = {key: value for key, value in search_params.filters.items() if value is not None}
        if filtered:
            entries.append(("filter_map", json.dumps(filtered, separators=(",", ":"))))

    @staticmethod
    def _append_sort(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if not search_params.sort:
            return
        sort_strings = [f"{sort.field}:{sort.direction}" for sort in search_params.sort]
        entries.append(("sort", ",".join(sort_strings)))

    @staticmethod
    def _append_aggregations(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if not search_params.aggregations:
            return
        agg_value = SearchParamURLGenerator._aggregations_to_string(search_params.aggregations)
        if agg_value is not None:
            entries.append(("aggregations", agg_value))

    @staticmethod
    def _append_debugging(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if search_params.debugging:
            entries.append(("debugging", "true"))

    @staticmethod
    def _append_client_url(entries: list[tuple[str, str]], search_params: SearchParams) -> None:
        if search_params.include_client_url not in (False, None):
            entries.append(("client_url", "https://archive.org/"))


def _safe_int(value: Any, fallback: int) -> int:
    try:
        if value is None or isinstance(value, bool):
            return fallback
        result = int(value)
        if result < 0:
            return fallback
        return result
    except (TypeError, ValueError, OverflowError):
        return fallback


def extract_scalar(value: Any) -> Any | None:
    if isinstance(value, dict):
        if "value" in value:
            return extract_scalar(value["value"])
        if "values" in value:
            return extract_scalar(value["values"])

    if isinstance(value, list):
        for entry in value:
            extracted = extract_scalar(entry)
            if extracted not in (None, ""):
                return extracted
        return None

    if value is None:
        return None

    return value


def extract_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        if "values" in value and isinstance(value["values"], list):
            return [str(item) for item in value["values"] if item not in (None, "")]
        if "value" in value and value["value"] not in (None, ""):
            return [str(value["value"])]

    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]

    if value not in (None, ""):
        return [str(value)]

    return []


def sanitize_snippet(snippet: str | None, keep_highlights: bool = True) -> str:
    """Normalize a snippet's whitespace, optionally keeping match highlights.

    Internet Archive wraps matched terms in braces. Those markers are worth
    keeping by default: they show which token actually matched, which is how a
    reader spots substring false positives such as "Jo-{ann Hunter}" for an
    "Ann Hunter" query. Nested braces are collapsed to a single pair.
    """
    if snippet is None:
        return ""
    if not isinstance(snippet, str):
        snippet = str(snippet)
    # Highlighting braces arrive as {KEYWORD}, {{KEYWORD}} or {{{KEYWORD}}}
    replacement = r'{\1}' if keep_highlights else r'\1'
    snippet = re.sub(r'\{+([^{}]*?)\}+', replacement, snippet)
    return re.sub(r"\s+", " ", snippet).strip()


def get_identifier(hit: dict[str, Any]) -> str | None:
    identifier = hit.get("identifier")
    if isinstance(identifier, str) and identifier:
        return identifier

    fields = hit.get("fields")
    if isinstance(fields, dict):
        value = fields.get("identifier")
        scalar = extract_scalar(value)
        if scalar:
            return str(scalar)

    return None


def build_item_url(identifier: str) -> str:
    base = ARCHIVE_BASE_URL.rstrip("/")
    return f"{base}/details/{identifier}"


def quote_phrase(value: str) -> str:
    """Quote a metadata value so it matches as one phrase rather than loose words.

    Without the quotes, a two-word value would be split by the query parser and
    match items carrying only one of the words.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def year_clause(from_year: int | None, to_year: int | None) -> str | None:
    """Build the ``year`` range clause, open-ended on either side.

    Returns None when neither bound was given, so an unfiltered search keeps the
    query it had before.
    """
    if from_year is None and to_year is None:
        return None
    if from_year is not None and to_year is not None and from_year > to_year:
        raise ValueError(
            f"the year range runs backwards: {from_year} is later than {to_year}"
        )
    lower = "*" if from_year is None else str(from_year)
    upper = "*" if to_year is None else str(to_year)
    return f"year:[{lower} TO {upper}]"


def language_clause(language: str) -> str | None:
    """Build the ``language`` clause, covering every known spelling of a language.

    A value that matches no known language is passed through as given, since
    archive.org holds many languages this table does not name.
    """
    value = language.strip()
    if not value:
        return None
    forms = LANGUAGE_ALIASES.get(value.lower(), (value,))
    if len(forms) == 1:
        return f"language:{quote_phrase(forms[0])}"
    alternatives = " OR ".join(quote_phrase(form) for form in forms)
    return f"language:({alternatives})"


def metadata_clause(field: str, value: str | None) -> str | None:
    """Build a phrase clause against one metadata field, or None if unfiltered."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return f"{field}:{quote_phrase(text)}"


def coerce_page(fields: dict[str, Any]) -> int | None:
    raw_value = extract_scalar(fields.get("page_num"))
    if raw_value is None:
        return None

    if isinstance(raw_value, str):
        candidate = raw_value.strip()
        if not candidate:
            return None
        raw_value = candidate

    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


# The search gateway answers HTTP 400 when the full-text backend fails. The
# body then says "the FTS API request failed ... HTTP 502", both for a query
# that the backend cannot run and for a temporary failure.
BACKEND_RETRY_SECONDS = 5
BACKEND_FAILURE_ADVICE = (
    " The full-text backend failed, also on a second try. The cause is either the query "
    "(a field range such as date:[...], or a very long boolean query) or a temporary failure of archive.org: "
    "a long query that failed one day worked the next day. Try the same query once in a few minutes. "
    "If it fails again, shorten it: fewer OR terms in each group, or one group for each search."
)


class SearchError(RuntimeError):
    """archive.org refused a search; the message says why."""


def _backend_error(response: httpx.Response) -> str:
    """The error message in the body of a refused search, or ''."""
    try:
        error = (response.json().get("response") or {}).get("error") or {}
    except ValueError:
        return ""
    return str(error.get("message", "")) if isinstance(error, dict) else str(error)


class ArchivesClient:
    """Client for searching and downloading from Internet Archive"""

    def __init__(self, cache_dir: Optional[Path] = None, collection: Optional[str] = None):
        """
        Initialize the client

        Args:
            cache_dir: Directory for caching downloaded files
            collection: Optional collection filter (e.g., "iapsop")
        """
        self.client: Optional[httpx.AsyncClient] = None
        self.collection = collection
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._user_agent = 'archives-mcp/0.1.0'
        # Shared with every other process using this cache, so that concurrent
        # CLI invocations do not collectively hammer archive.org.
        self._rate_limiter = CrossProcessRateLimiter(
            state_file=self.cache_dir / ".rate-limit",
            min_interval=configured_interval(),
        )

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure async client is created"""
        if self.client is None:
            self.client = httpx.AsyncClient(
                headers={'User-Agent': self._user_agent},
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, connect=10.0)
            )
        return self.client

    async def close(self):
        """Close the async client"""
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def search(
        self,
        query: str,
        page: int = 1,
        rows: int = 100,
        from_year: Optional[int] = None,
        to_year: Optional[int] = None,
        language: Optional[str] = None,
        title: Optional[str] = None,
        creator: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Search Internet Archive for documents matching the query.

        Every filter is optional; omitting all of them searches exactly as
        before. Each one narrows on the *item's catalogue metadata*, not on the
        page the match was found on, and each therefore drops items whose
        metadata does not carry that field at all.

        Args:
            query: Search query string
            page: Page number (1-indexed)
            rows: Results per page (default 100, max 500)
            from_year: Earliest publication year, inclusive
            to_year: Latest publication year, inclusive
            language: Language of the item, as a name ("French") or a MARC code
                ("fre"); all known spellings of a named language are searched
            title: Phrase that must appear in the item's title
            creator: Phrase that must appear in the item's creator field
            subject: Phrase that must appear in the item's subject headings

        Returns:
            Dictionary containing:
            - page: Current page number
            - total_results: Total number of results
            - documents: List of documents with metadata and snippets

        Raises:
            ValueError: from_year is later than to_year
        """
        query_parts: list[str] = []
        if self.collection:
            query_parts.append(f"collection:{self.collection}")
        query_parts.append("mediatype:texts")

        # Filters are ANDed onto the query, which is the only mechanism the
        # full-text backend honours: it accepts the search service's
        # `filter_map` parameter and then silently ignores it, returning the
        # unfiltered result set.
        filter_clauses = [
            year_clause(from_year, to_year),
            language_clause(language) if language is not None else None,
            metadata_clause("title", title),
            metadata_clause("creator", creator),
            metadata_clause("subject", subject),
        ]
        query_parts.extend(clause for clause in filter_clauses if clause is not None)

        if query.strip():
            query_parts.append(f"({query.strip()})")

        search_fields = [
            "identifier",
            "title",
            "year",
            "date",
            "publicdate",
            "creator",
            "collection",
            "page_num",
            "filename",
            "file_basename",
        ]

        params = SearchParams(
            query=" AND ".join(query_parts),
            rows=min(rows, 500),
            page=page,
            fields=search_fields,
            include_client_url=False,
            aggregations=AggregationsParam(omit=True),
        )

        query_string = SearchParamURLGenerator.generate(params)
        url = f"https://archive.org/services/search/beta/page_production/?service_backend=fts&{query_string}"

        try:
            client = await self._ensure_client()
            for attempt in (1, 2):
                await self._rate_limiter.acquire()
                response = await client.get(url)
                if response.status_code != 400 or "FTS API request failed" not in (cause := _backend_error(response)):
                    break
                if attempt == 1:
                    await asyncio.sleep(BACKEND_RETRY_SECONDS)
            if response.status_code == 400:
                cause = _backend_error(response)
                raise SearchError(
                    f"archive.org refused the search (HTTP 400: {cause or 'no message'})."
                    + (BACKEND_FAILURE_ADVICE if "FTS API request failed" in cause else "")
                )
            response.raise_for_status()

            parsed = response.json()
            body = (parsed.get("response") or {}).get("body") or {}
            hits_container = body.get("hits") or {}
            hits = hits_container.get("hits") or []
            total_results = _safe_int(hits_container.get("total"), len(hits))

            # Process hits into documents
            documents = self._process_hits(hits)

            # An empty result set is one empty page, not zero pages, matching
            # how the other sources in this toolset report themselves.
            rows_per_page = max(1, min(rows, 500))
            total_pages = max(1, -(-total_results // rows_per_page))

            return {
                'page': page,
                'total_results': total_results,
                'total_pages': total_pages,
                'documents': documents
            }

        except SearchError as e:
            return {
                'error': str(e),
                'page': page,
                'total_results': 0,
                'total_pages': 1,
                'documents': []
            }
        except httpx.HTTPError as e:
            return {
                'error': f"HTTP error: {str(e)}",
                'page': page,
                'total_results': 0,
                'total_pages': 1,
                'documents': []
            }
        except Exception as e:
            return {
                'error': str(e),
                'page': page,
                'total_results': 0,
                'total_pages': 1,
                'documents': []
            }

    def _process_hits(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Process search hits into document format"""
        documents = []
        seen_items = set()

        for hit in hits:
            identifier = get_identifier(hit)
            if not identifier:
                continue

            fields = hit.get("fields") or {}

            # Extract metadata
            title = str(extract_scalar(fields.get("title")) or "Untitled")
            creators = extract_list(fields.get("creator"))
            date = extract_scalar(fields.get("date"))

            # Extract hit-specific info
            page = coerce_page(fields)

            # Extract snippets
            highlight = hit.get("highlight")
            snippets = []
            if isinstance(highlight, dict):
                text_values = extract_list(highlight.get("text"))
                snippets = [sanitize_snippet(text) for text in text_values if sanitize_snippet(text)]

            if not snippets:
                snippets = [title]

            # Create document entry (one per identifier, aggregating snippets)
            doc_key = identifier
            if doc_key not in seen_items:
                seen_items.add(doc_key)

                doc_url = build_item_url(identifier)

                doc = {
                    'identifier': identifier,
                    'title': title,
                    'url': doc_url,
                    'creators': creators,
                    'date': date,
                    'snippets': snippets[:5],  # Limit snippets
                    'page': page
                }

                documents.append(doc)

        return documents

    async def download_text(
        self,
        identifier: str,
        file_name: Optional[str] = None,
        refresh: bool = False
    ) -> Optional[Path]:
        """
        Download OCR text for a document and return path to cached file.

        Args:
            identifier: Internet Archive item identifier
            file_name: Specific file to download (optional, will auto-detect if not provided)
            refresh: Ignore any cached copy and fetch again

        Returns:
            Path to the cached text file, or None if the item genuinely has no
            text available.

        Raises:
            httpx.HTTPError: the request failed for a reason that may not repeat
            RuntimeError: the response could not be decoded

        A returned None means "archive.org says this does not exist". Anything
        that might succeed on a retry is raised instead, so that a network blip
        is never mistaken for an absent document.
        """
        # Check cache first
        cache_key = self._get_cache_key(identifier, file_name)
        cache_path = self.cache_dir / f"{cache_key}.txt"

        if cache_path.exists() and not refresh:
            if self._is_unavailable_marker(cache_path):
                return None
            return cache_path

        # Auto-detect file if not provided, or if the given one is not text
        if file_name is None or not self._is_valid_text_file(file_name):
            file_name = await self._find_text_file(identifier)
            if file_name is None:
                # The item exists but carries no usable text file.
                self._write_to_cache(cache_key, UNAVAILABLE_SENTINEL)
                return None

        # Download the file
        download_url = f"https://archive.org/download/{identifier}/{quote(file_name)}"

        client = await self._ensure_client()
        await self._rate_limiter.acquire()
        response = await client.get(download_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)

        if response.status_code == HTTP_NOT_FOUND:
            self._write_to_cache(cache_key, UNAVAILABLE_SENTINEL)
            return None

        if response.status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
            # Restricted *or* throttled - archive.org uses 403 for both, so this
            # must not be cached as permanently unavailable.
            raise httpx.HTTPStatusError(
                f"archive.org refused {identifier} with {response.status_code}. "
                "The item may be access-restricted, or the request may have been "
                "throttled; retry before concluding it is unavailable.",
                request=response.request,
                response=response,
            )

        response.raise_for_status()

        try:
            content_bytes = response.content
            if file_name.endswith('.gz'):
                content_bytes = gzip.decompress(content_bytes)
            content = content_bytes.decode('utf-8')
        except (gzip.BadGzipFile, UnicodeDecodeError) as error:
            raise RuntimeError(
                f"Could not decode the text file {file_name} for {identifier}: {error}"
            ) from error

        self._write_to_cache(cache_key, content)
        return cache_path

    def _is_unavailable_marker(self, cache_path: Path) -> bool:
        """Whether a cache entry records a confirmed-absent document.

        Checks the size first: the marker is a few bytes, while a real entry can
        run to megabytes that there is no reason to read.
        """
        if cache_path.stat().st_size > len(UNAVAILABLE_SENTINEL):
            return False
        return cache_path.read_text(encoding='utf-8').strip() == UNAVAILABLE_SENTINEL

    def _is_valid_text_file(self, file_name: str) -> bool:
        """Check if a filename indicates it's a plain text file"""
        file_name_lower = file_name.lower()

        if not (file_name_lower.endswith('.txt') or file_name_lower.endswith('.txt.gz')):
            return False

        suspicious_suffixes = [
            '_abbyy.gz', '_abbyy.txt.gz', '_abbyy.txt',
            '_djvu.xml.gz', '_djvu.xml.txt',
            '_meta.txt.gz', '_meta.txt',
            '_files.txt.gz', '_files.txt',
            '_scandata.txt',
        ]

        return not any(file_name_lower.endswith(suffix) for suffix in suspicious_suffixes)

    async def _find_text_file(self, identifier: str) -> Optional[str]:
        """Find and return a valid text file for an item.

        Returns None only when the item genuinely offers no text file. A failure
        to reach the metadata API propagates, because treating it as "no text"
        would cache the item as permanently unavailable.
        """
        files = await self._fetch_item_files_metadata(identifier)

        if not files:
            return None

        # Find best text alternative
        hocr_files: list[str] = []
        djvu_files: list[str] = []
        generic_files: list[str] = []

        for file_info in files:
            if not isinstance(file_info, dict):
                continue

            name = file_info.get("name", "")
            if not isinstance(name, str):
                continue

            if not self._is_valid_text_file(name):
                continue

            name_lower = name.lower()

            if '_hocr.txt' in name_lower:
                hocr_files.append(name)
            elif '_djvu.txt' in name_lower:
                djvu_files.append(name)
            else:
                generic_files.append(name)

        # Helper to prefer uncompressed within a list
        def prefer_uncompressed(file_list: list[str]) -> str | None:
            if not file_list:
                return None
            uncompressed = [f for f in file_list if f.endswith('.txt') and not f.endswith('.gz')]
            if uncompressed:
                return uncompressed[0]
            return file_list[0]

        # Return best file according to priority
        return (
            prefer_uncompressed(hocr_files)
            or prefer_uncompressed(djvu_files)
            or prefer_uncompressed(generic_files)
        )

    async def _fetch_item_files_metadata(self, identifier: str) -> list[dict[str, Any]]:
        """Fetch the list of files for an Internet Archive item"""
        metadata_url = f"https://archive.org/metadata/{identifier}/files"

        client = await self._ensure_client()
        await self._rate_limiter.acquire()
        response = await client.get(metadata_url, timeout=METADATA_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            raise RuntimeError(
                f"Metadata API returned an unexpected shape for {identifier}: "
                f"expected an object, got {type(data).__name__}"
            )

        if "error" in data or "result" not in data:
            # archive.org answers `{"error": "Couldn't locate item ..."}` for an
            # identifier that does not exist. That is a real answer - the item
            # has no files - not a malformed response.
            return []

        files = data["result"]
        if not isinstance(files, list):
            raise RuntimeError(
                f"Metadata API returned a non-list 'result' for {identifier}: "
                f"{type(files).__name__}"
            )

        return files

    def _get_cache_key(self, identifier: str, file_name: Optional[str]) -> str:
        """Generate cache key from identifier and optional filename"""
        if file_name:
            return f"{identifier}_{file_name}".replace('/', '_')
        return identifier.replace('/', '_')

    def _write_to_cache(self, cache_key: str, content: str) -> None:
        """Write to cache"""
        cache_file = self.cache_dir / f"{cache_key}.txt"
        cache_file.write_text(content, encoding='utf-8')
