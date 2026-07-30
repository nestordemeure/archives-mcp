"""
Internet Archive MCP Server

Provides Model Context Protocol tools for searching and accessing
OCR text from Internet Archive, a digital library of millions of free books,
movies, software, music, websites, and more.
"""

from typing import Optional

from fastmcp import FastMCP

from .client import ArchivesClient
from .paths import cache_dir


# Initialize MCP server
mcp = FastMCP("Internet Archive")

# Global client instance
_client: Optional[ArchivesClient] = None


async def get_client() -> ArchivesClient:
    """Get or create the global Archives client"""
    global _client
    if _client is None:
        _client = ArchivesClient(cache_dir=cache_dir())
    return _client


@mcp.tool()
async def search_archive(
    query: str,
    page: int = 1,
    collection: Optional[str] = None,
    from_year: Optional[int] = None,
    to_year: Optional[int] = None,
    language: Optional[str] = None,
    title: Optional[str] = None,
    creator: Optional[str] = None,
    subject: Optional[str] = None,
) -> dict:
    """
    Search Internet Archive for text documents matching a query.

    Returns paginated results (100 documents per page) with metadata and text snippets.
    Each document includes title, creators, publication date, and relevant text excerpts.

    Note: Fulltext search operates at document level - "A AND B" matches if both terms appear
    anywhere in the document, even hundreds of pages apart.

    Query Syntax:
    - Multiple terms separated by spaces are treated as AND (e.g., "magic tricks" finds documents with both words)
    - Use quotes for exact phrases (e.g., "card tricks")
    - Use OR for alternatives (e.g., "Houdini OR Kellar")
    - Use - for NOT (e.g., "magic -card" excludes documents with "card")
    - Use parentheses for grouping (e.g., "(Houdini OR Kellar) AND escape")

    Filters: every filter below is optional and narrows on the item's catalogue
    metadata, not on the page the match was found on. Each one therefore drops
    items whose metadata omits that field entirely - undated items vanish under
    a year range, uncatalogued ones under a subject.

    Args:
        query: Search query string
        page: Page number for pagination, 1-indexed (default: 1)
        collection: Optional collection filter (e.g., "iapsop" for International Association for the Preservation of Spiritualist and Occult Periodicals)
        from_year: Earliest publication year, inclusive
        to_year: Latest publication year, inclusive
        language: Language of the item, as a name ("French") or a MARC code
            ("fre"). archive.org records this inconsistently, so every known
            spelling of a named language is searched together; an unrecognised
            value is used verbatim
        title: Phrase that must appear in the item's title, e.g. "Household Words"
        creator: Phrase that must appear in the item's creator field, e.g.
            "Dickens, Charles". Selects works BY someone, not works that mention them
        subject: Phrase that must appear in the item's subject headings, e.g.
            "Historical Journals"

    Returns:
        Dictionary containing:
        - page: Current page number
        - total_results: Total number of matching documents
        - documents: List of documents with:
            - identifier: Unique identifier for the item (use with download_text)
            - title: Document title
            - url: URL to view document on archive.org
            - creators: List of authors/creators
            - date: Publication date (if available)
            - snippets: List of text excerpts showing search term matches
            - page: Page number where match was found (if available)

    Example:
        search_archive("spiritualism", page=1)
        search_archive("Houdini escape", page=2, collection="magicperiodicals")
        search_archive("second sight", from_year=1880, to_year=1900, language="English")
    """
    # Create a new client if collection is specified
    if collection:
        client = ArchivesClient(cache_dir=cache_dir(), collection=collection)
    else:
        client = await get_client()

    try:
        results = await client.search(
            query=query,
            page=page,
            rows=100,
            from_year=from_year,
            to_year=to_year,
            language=language,
            title=title,
            creator=creator,
            subject=subject,
        )
        return results
    except Exception as e:
        return {
            "error": str(e),
            "page": page,
            "total_results": 0,
            "documents": []
        }
    finally:
        if collection:
            await client.close()


@mcp.tool()
async def download_text(identifier: str) -> str:
    """
    Download OCR text from an Internet Archive item and save to cache.

    Downloads the full OCR-extracted text from a document and saves it to the local
    cache directory. The function automatically finds the best available text file.

    Text file priority (highest to lowest):
    1. hOCR format files (_hocr.txt) - structured OCR with positioning
    2. DjVuTXT format files (_djvu.txt) - high quality DjVu OCR
    3. Generic .txt files - fallback plain text

    Args:
        identifier: Internet Archive item identifier (e.g., from search results)

    Returns:
        Path to the cached text file (as string), or an error message if the
        document cannot be accessed or does not have text available.

    IMPORTANT:
        The downloaded files are VERY LARGE (typically 100KB-1MB+ of text).
        DO NOT attempt to read the entire file into context.
        Use the Read tool with offset/limit parameters to read specific portions.
        Reading the full file will waste tokens and may cause performance issues.

    Example:
        path = download_text("spiritualistmagaz00amer")
        # Then read the file: with open(path) as f: content = f.read()
    """
    client = await get_client()

    try:
        file_path = await client.download_text(identifier)

        if file_path is None:
            return f"Error: Document not found or not accessible: {identifier}"

        return str(file_path)

    except Exception as e:
        return f"Error downloading document {identifier}: {str(e)}"


@mcp.resource("archive://info")
def server_info() -> str:
    """
    Get information about the Internet Archive MCP server.
    """
    return """
Internet Archive MCP Server

This server provides access to Internet Archive (archive.org), a non-profit digital
library offering free access to millions of books, movies, software, music, and more.

Available Tools:
- search_archive: Search for text documents with pagination and collection filtering
- download_text: Download OCR text from a specific document

Query Syntax:
- Spaces = AND (e.g., "magic tricks" finds both words)
- Quotes = exact phrase (e.g., "card tricks")
- OR = alternatives (e.g., "Houdini OR Kellar")
- - = NOT (e.g., "magic -card")
- Parentheses for grouping (e.g., "(Houdini OR Kellar) AND escape")

Features:
- Paginated search results (100 documents per page)
- Full OCR text extraction from documents
- Automatic text file detection (prefers hOCR > DjVu > generic text)
- Metadata filters: collection, year range, language, title, creator, subject
  (each drops items whose metadata lacks that field)
- Local caching to minimize repeated downloads

Popular Collections:
- "iapsop" - International Association for the Preservation of Spiritualist and Occult Periodicals
- "americana" - American historical documents
- "opensource" - Open source books
- "prelinger" - Prelinger Archives
"""


def main():
    """Main entry point for the MCP server"""
    import sys

    # Check for command line arguments
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h"]:
        print("Internet Archive MCP Server")
        print("\nUsage:")
        print("  archives-mcp              # Run server in stdio mode")
        print("  archives-mcp --help       # Show this help")
        print("\nFor more information, see the README.md file")
        return

    # Run the server
    mcp.run()


if __name__ == "__main__":
    main()
