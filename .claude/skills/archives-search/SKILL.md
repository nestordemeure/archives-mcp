---
name: archives-search
description: Search the full text of Internet Archive books and periodicals with the `archives` CLI, and pull OCR text out of specific items. Use for English-language books, magic and spiritualist periodicals, and anything digitised by archive.org.
---

# Internet Archive

Full-text search across archive.org's digitised texts. Strongest on English-language
books and periodicals; the `iapsop` collection covers spiritualist and occult
periodicals specifically.

## Commands

```sh
archives search "<query>" [--pages N|N-M|all] [--snippets N] [--collection NAME] [--json]
archives get <identifier>     # download OCR text, prints path to the cached file
```

`search` prints a header with the total result count and page count, then one block per
document: identifier, date, title, URL, and snippets with the matched terms in `{braces}`.
`get` prints a path — the files run 100KB–1MB+, so read slices of them, never the whole thing.

## Query syntax

- Spaces mean AND: `magic tricks` needs both words
- `"quoted phrases"` match exactly
- `OR` for alternatives: `Houdini OR Kellar`
- `-` excludes: `magic -card`
- Parentheses group: `(Houdini OR Kellar) AND escape`
- `creator:Houdini` searches a specific field; `magi*` wildcards

**Matching is document-level, not page-level.** `"Harry Houdini" AND Edison` matches a
900-page volume containing both, hundreds of pages apart, with no relationship between
them. Treat multi-term AND results as leads, never as evidence of a connection.

Combine alternatives into one query rather than running several searches — one
`(A OR B OR C)` call costs a fraction of three separate ones.

## Being exhaustive

100 results per page. `--pages all` sweeps every page in one invocation; prefer it over
looping page-by-page when the total is manageable. Check the header's total first: at
several thousand results, tighten the query instead of sweeping, or the sift will cost
more than it returns.

## False positives to expect

The braces show which token actually matched — that is the single most useful signal here.

- **Substring matches inside longer names.** An `"Ann Hunter"` query returns
  `Jo{ann Hunter}` and `Le{ann Hunter}`. Read the braces before believing a hit.
- **Hyphenation across line breaks.** OCR splits words, so `Jo- {ann Hunter}` rejoins to
  "Joann Hunter" — a mismatch, despite looking like a clean hit.
- **OCR noise inside the match**, e.g. `{Ann) Hunter}`. Usually still the right person;
  judge from surrounding context.
- **Bibliographies and indexes.** A name in a citation list or a table of contents is a
  passing mention, not substantive content.
- **Wrong sense or wrong person entirely** — check that the surrounding context fits the
  domain before accepting.

Snippets are usually enough to reject a false positive. Only `get` a document once the
snippets suggest it is genuinely worth reading.

## Cost

Search is cheap. `get` is not: downloads are large and cached under
`$XDG_CACHE_HOME/archives-mcp`. When reading many documents, dispatch
subagents so the OCR text stays out of the main context, and have them report back
findings with page numbers and quotes.

Requests are paced to roughly two per second, and that pacing is shared across every
process — so parallel subagents share one budget rather than each getting their own.
Fanning out widely speeds up the reading, not the fetching.

If a `get` fails, read the message before concluding anything: "no OCR text" means the
item genuinely has none, while any other error means the request may simply be worth
retrying. Use `--refresh` to re-download something already cached.

**Over-querying gets you banned, and the ban outlasts the session.** This is a free public
service; a sweep that looks thorough from here looks like scraping from theirs. If requests
start failing or returning something that is not what you asked for, stop and say so rather
than retrying into a longer ban.
