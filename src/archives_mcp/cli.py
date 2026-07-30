"""Command-line interface for Internet Archive full-text search.

A thin wrapper over :class:`ArchivesClient` that formats results for reading in a
terminal or by an agent driving the command through a shell. Output is compact
and greppable by default; ``--json`` emits the raw client structures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from .client import ArchivesClient
from .paths import cache_dir

RESULTS_PER_PAGE = 100
DEFAULT_SNIPPETS_SHOWN = 2
PROGRAM_NAME = "archives"


class PageRange:
    """A 1-indexed, inclusive range of result pages. ``last is None`` means all."""

    def __init__(self, first: int, last: int | None) -> None:
        self.first = first
        self.last = last

    def contains(self, page: int) -> bool:
        return page >= self.first and (self.last is None or page <= self.last)

    def __str__(self) -> str:
        if self.last is None:
            return f"{self.first}-all"
        if self.last == self.first:
            return str(self.first)
        return f"{self.first}-{self.last}"


def parse_page_range(value: str) -> PageRange:
    """Parse a ``--pages`` value: ``3``, ``2-5`` or ``all``."""
    text = value.strip().lower()

    if text == "all":
        return PageRange(1, None)

    try:
        if "-" in text:
            first_text, _, last_text = text.partition("-")
            first, last = int(first_text), int(last_text)
        else:
            first = last = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a page number, a range like 2-5, or 'all'; got {value!r}"
        ) from None

    if first < 1:
        raise argparse.ArgumentTypeError(f"page numbers start at 1; got {value!r}")
    if last < first:
        raise argparse.ArgumentTypeError(f"page range runs backwards: {value!r}")

    return PageRange(first, last)


def format_date(raw: Any) -> str:
    """Trim an archive.org timestamp to its date part."""
    if not raw:
        return "n.d."
    return str(raw).split("T", 1)[0]


def format_document(position: int, document: dict[str, Any], max_snippets: int) -> str:
    """Render one search result as an indented, greppable block."""
    lines = []

    identifier = document["identifier"]
    header = f"[{position}] {identifier}  ({format_date(document.get('date'))})"
    if (page := document.get("page")) is not None:
        header += f"  p.{page}"
    lines.append(header)

    # Many items carry no real title; the client falls back to the identifier,
    # which the header already shows.
    title = document.get("title") or ""
    if title == identifier:
        title = ""
    if creators := document.get("creators"):
        title = f"{title} — {', '.join(creators)}" if title else ", ".join(creators)
    if title:
        lines.append(f"    {title}")
    lines.append(f"    {document['url']}")

    snippets = document.get("snippets") or []
    for snippet in snippets[:max_snippets]:
        lines.append(f"    · {snippet}")
    if (hidden := len(snippets) - max_snippets) > 0:
        lines.append(f"    · … {hidden} more snippet{'s' if hidden > 1 else ''}")

    return "\n".join(lines)


def describe_filters(args: argparse.Namespace) -> str:
    """Summarise the active filters for the result header, or "" if there are none.

    Worth printing: a filtered total looks like a small archive until you can see
    what was excluded to produce it.
    """
    parts: list[str] = []
    if args.from_year is not None or args.to_year is not None:
        first = str(args.from_year) if args.from_year is not None else "…"
        last = str(args.to_year) if args.to_year is not None else "…"
        parts.append(f"{first}–{last}")
    for label, value in (
        ("collection", args.collection),
        ("language", args.language),
        ("title", args.title),
        ("creator", args.creator),
        ("subject", args.subject),
    ):
        if value:
            parts.append(f"{label}={value}")
    if not parts:
        return ""
    return f" [{', '.join(parts)}]"


async def run_search(args: argparse.Namespace) -> int:
    """Fetch the requested pages, streaming results as each page arrives."""
    client = ArchivesClient(cache_dir=cache_dir(args.cache_dir), collection=args.collection)
    collected: list[dict[str, Any]] = []
    total_results = 0
    total_pages = 1

    try:
        page = args.pages.first
        while args.pages.contains(page):
            try:
                result = await client.search(
                    query=args.query,
                    page=page,
                    rows=RESULTS_PER_PAGE,
                    from_year=args.from_year,
                    to_year=args.to_year,
                    language=args.language,
                    title=args.title,
                    creator=args.creator,
                    subject=args.subject,
                )
            except ValueError as error:
                print(f"{PROGRAM_NAME}: {error}", file=sys.stderr)
                return 2

            if error := result.get("error"):
                print(f"{PROGRAM_NAME}: page {page} failed: {error}", file=sys.stderr)
                return 1

            total_results = result["total_results"]
            total_pages = result["total_pages"]
            documents = result["documents"]

            if page > total_pages:
                break

            if args.json:
                collected.extend(documents)
            else:
                scope = f"page {page} of {total_pages}"
                print(
                    f"# {args.query}{describe_filters(args)} — "
                    f"{total_results} results, {scope}"
                )
                if not documents:
                    print("  (no documents on this page)")
                for offset, document in enumerate(documents):
                    position = (page - 1) * RESULTS_PER_PAGE + offset + 1
                    print(format_document(position, document, args.snippets))
                print()

            if page >= total_pages:
                break
            page += 1
    finally:
        await client.close()

    if args.json:
        filters = {
            name: value
            for name, value in (
                ("from_year", args.from_year),
                ("to_year", args.to_year),
                ("language", args.language),
                ("title", args.title),
                ("creator", args.creator),
                ("subject", args.subject),
            )
            if value is not None
        }
        json.dump(
            {
                "query": args.query,
                "collection": args.collection,
                "filters": filters,
                "total_results": total_results,
                "total_pages": total_pages,
                "pages_fetched": str(args.pages),
                "documents": collected,
            },
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        print()

    return 0


async def run_get(args: argparse.Namespace) -> int:
    """Download an item's OCR text and print the path to the cached file."""
    client = ArchivesClient(cache_dir=cache_dir(args.cache_dir))

    try:
        path = await client.download_text(args.identifier, refresh=args.refresh)
    except (httpx.HTTPError, RuntimeError) as error:
        # Distinct from the None case below: this one may well succeed on a
        # retry, and must not be read as "the document does not exist".
        print(f"{PROGRAM_NAME}: could not fetch {args.identifier}: {error}", file=sys.stderr)
        return 1
    finally:
        await client.close()

    if path is None:
        print(
            f"{PROGRAM_NAME}: archive.org has no OCR text for {args.identifier}",
            file=sys.stderr,
        )
        return 1

    print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="Full-text search over Internet Archive texts.",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="override the download cache location (default: $XDG_CACHE_HOME/archives-mcp)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser(
        "search",
        help="search full text across archive.org",
        description=(
            "Search the full text of archive.org texts. Terms separated by spaces are "
            "ANDed, \"quoted phrases\" match exactly, OR offers alternatives, a leading "
            "- excludes, and parentheses group. Matching is document-level: 'A AND B' "
            "hits a document containing both, even hundreds of pages apart."
        ),
    )
    search.add_argument("query", help="search query")
    search.add_argument(
        "--pages",
        type=parse_page_range,
        default=PageRange(1, 1),
        metavar="SPEC",
        help="which result pages to fetch: N, N-M, or 'all' (default: 1)",
    )
    search.add_argument(
        "--snippets",
        type=int,
        default=DEFAULT_SNIPPETS_SHOWN,
        metavar="N",
        help=f"snippets shown per document (default: {DEFAULT_SNIPPETS_SHOWN})",
    )
    search.add_argument(
        "--collection",
        default=None,
        help="restrict to one archive.org collection, e.g. iapsop",
    )
    search.add_argument(
        "--from-year",
        type=int,
        metavar="YEAR",
        help=(
            "earliest publication year, inclusive. Reads the item's year metadata, "
            "so items with no year at all are silently dropped"
        ),
    )
    search.add_argument(
        "--to-year",
        type=int,
        metavar="YEAR",
        help=(
            "latest publication year, inclusive. Same caveat as --from-year: "
            "undated items disappear from the results"
        ),
    )
    search.add_argument(
        "--language",
        metavar="NAME",
        help=(
            "language of the item, e.g. French or fre. archive.org records this "
            "inconsistently, as a name or a MARC code, so every spelling of a "
            "named language is searched at once; an unrecognised value is used "
            "verbatim. Items with no language metadata are dropped"
        ),
    )
    search.add_argument(
        "--title",
        metavar="TEXT",
        help=(
            "phrase that must appear in the item's title, e.g. 'Household Words'. "
            "This is the catalogue title of the whole volume or issue, not the "
            "page the match sits on"
        ),
    )
    search.add_argument(
        "--creator",
        metavar="NAME",
        help=(
            "phrase that must appear in the item's creator field, e.g. "
            "'Dickens, Charles'. Selects works BY someone rather than works "
            "mentioning them; the form varies per item, so copy it from a "
            "result line when precision matters"
        ),
    )
    search.add_argument(
        "--subject",
        metavar="HEADING",
        help=(
            "phrase that must appear in the item's subject headings, e.g. "
            "'Historical Journals'. Many items carry no subject at all, and "
            "this excludes every one of them"
        ),
    )
    search.add_argument("--json", action="store_true", help="emit JSON instead of text")
    search.set_defaults(handler=run_search)

    get = subparsers.add_parser(
        "get",
        help="download an item's OCR text, printing the cache path",
        description=(
            "Download the best available OCR text for an item and print the path to the "
            "cached file. Files are large (100KB-1MB+); read slices of them rather than "
            "loading them whole."
        ),
    )
    get.add_argument("identifier", help="archive.org item identifier, from search results")
    get.add_argument(
        "--refresh",
        action="store_true",
        help="re-download even if a cached copy exists",
    )
    get.set_defaults(handler=run_get)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        exit_code = asyncio.run(args.handler(args))
    except KeyboardInterrupt:
        exit_code = 130

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
