"""
Internet Archive client for MCP server.

Provides access to search and download OCR text from archive.org.
"""

import asyncio
import gzip
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode, quote

import httpx


# HTTP status codes
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_CLIENT_ERROR_THRESHOLD = 400
MAX_DOWNLOAD_RETRIES = 3
INITIAL_RETRY_DELAY_SECONDS = 1
DOWNLOAD_TIMEOUT_SECONDS = 60.0
METADATA_MAX_RETRIES = 3
METADATA_TIMEOUT_SECONDS = 30.0
RETRYABLE_METADATA_STATUS_CODES = {408, 429, 500, 502, 503, 504}
ARCHIVE_BASE_URL = "https://archive.org"


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


def sanitize_snippet(snippet: str | None) -> str:
    if snippet is None:
        return ""
    if not isinstance(snippet, str):
        snippet = str(snippet)
    # Remove highlighting braces like {KEYWORD}, {{KEYWORD}}, or {{{KEYWORD}}}
    snippet = re.sub(r'\{+([^{}]*?)\}+', r'\1', snippet)
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


def resolve_file_name(fields: dict[str, Any]) -> str | None:
    """Get the full filename with extension for download purposes."""
    filename = extract_scalar(fields.get("filename"))
    if filename not in (None, ""):
        return str(filename)

    # Fallback to basename for backward compatibility
    basename = extract_scalar(fields.get("file_basename"))
    if basename not in (None, ""):
        return str(basename)

    return None


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
        self.cache_dir = cache_dir or Path.cwd() / "cache" / "archives"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._user_agent = 'archives-mcp/0.1.0'

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
        rows: int = 100
    ) -> dict[str, Any]:
        """
        Search Internet Archive for documents matching the query.

        Args:
            query: Search query string
            page: Page number (1-indexed)
            rows: Results per page (default 100, max 500)

        Returns:
            Dictionary containing:
            - page: Current page number
            - total_results: Total number of results
            - documents: List of documents with metadata and snippets
        """
        query_parts: list[str] = []
        if self.collection:
            query_parts.append(f"collection:{self.collection}")
        query_parts.append("mediatype:texts")
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
            response = await client.get(url)
            response.raise_for_status()

            parsed = response.json()
            body = (parsed.get("response") or {}).get("body") or {}
            hits_container = body.get("hits") or {}
            hits = hits_container.get("hits") or []
            total_results = _safe_int(hits_container.get("total"), len(hits))

            # Process hits into documents
            documents = self._process_hits(hits)

            return {
                'page': page,
                'total_results': total_results,
                'documents': documents
            }

        except httpx.HTTPError as e:
            return {
                'error': f"HTTP error: {str(e)}",
                'page': page,
                'total_results': 0,
                'documents': []
            }
        except Exception as e:
            return {
                'error': str(e),
                'page': page,
                'total_results': 0,
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

    async def download_text(self, identifier: str, file_name: Optional[str] = None) -> Optional[Path]:
        """
        Download OCR text for a document and return path to cached file.

        Args:
            identifier: Internet Archive item identifier
            file_name: Specific file to download (optional, will auto-detect if not provided)

        Returns:
            Path to the cached text file, or None if not available
        """
        # Check cache first
        cache_key = self._get_cache_key(identifier, file_name)
        cache_path = self.cache_dir / f"{cache_key}.txt"

        if cache_path.exists():
            cached_text = cache_path.read_text(encoding='utf-8')
            if cached_text == "404":
                return None
            return cache_path

        # Auto-detect file if not provided
        if file_name is None:
            file_name = await self._find_text_file(identifier)
            if file_name is None:
                self._write_to_cache(cache_key, "404")
                return None

        # Validate file type
        if not self._is_valid_text_file(file_name):
            file_name = await self._find_text_file(identifier)
            if file_name is None:
                self._write_to_cache(cache_key, "404")
                return None

        # Download the file
        download_url = f"https://archive.org/download/{identifier}/{quote(file_name)}"

        try:
            client = await self._ensure_client()
            response = await client.get(download_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)

            if response.status_code == HTTP_NOT_FOUND:
                self._write_to_cache(cache_key, "404")
                return None

            response.raise_for_status()
            content_bytes = response.content

            # Decode content
            if file_name.endswith('.gz'):
                content = gzip.decompress(content_bytes).decode('utf-8')
            else:
                content = content_bytes.decode('utf-8')

            # Cache and return path
            self._write_to_cache(cache_key, content)
            return cache_path

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
                self._write_to_cache(cache_key, "404")
            return None
        except Exception:
            return None

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
        """Find and return a valid text file for an item"""
        try:
            files = await self._fetch_item_files_metadata(identifier)
        except Exception:
            return None

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
        response = await client.get(metadata_url, timeout=METADATA_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict) or "result" not in data:
            raise RuntimeError(f"Unexpected metadata API response format")

        files = data["result"]
        if not isinstance(files, list):
            raise RuntimeError(f"Metadata API files field is not a list")

        return files

    def _get_cache_key(self, identifier: str, file_name: Optional[str]) -> str:
        """Generate cache key from identifier and optional filename"""
        if file_name:
            return f"{identifier}_{file_name}".replace('/', '_')
        return identifier.replace('/', '_')

    def _read_from_cache(self, cache_key: str) -> Optional[str]:
        """Read from cache if exists"""
        cache_file = self.cache_dir / f"{cache_key}.txt"
        if cache_file.exists():
            return cache_file.read_text(encoding='utf-8')
        return None

    def _write_to_cache(self, cache_key: str, content: str) -> None:
        """Write to cache"""
        cache_file = self.cache_dir / f"{cache_key}.txt"
        cache_file.write_text(content, encoding='utf-8')
