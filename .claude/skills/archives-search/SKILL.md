---
name: archives-search
description: Search the full text of Internet Archive books and periodicals with the `archives` CLI, and take the OCR text out of a specified item. Use it for English-language books, magic and spiritualist periodicals, and all material that archive.org digitised.
---

# Internet Archive

A full-text search of the digitised texts of archive.org. The source is strongest on English-language books and periodicals. The `iapsop` collection holds spiritualist and occult periodicals.

## Commands

```sh
archives search "<query>" [--pages N|N-M|all] [--snippets N] [--collection NAME] \
    [--from-year YEAR] [--to-year YEAR] [--language NAME] \
    [--title TEXT] [--creator NAME] [--subject HEADING] [--json]
archives get <identifier>     # download OCR text, prints path to the cached file
```

`search` prints a header with the total number of results and the number of pages. Then it prints one block for each document: the identifier, the date, the title, the URL, and snippets with the matched terms in `{braces}`. The header shows each active filter in `[brackets]`, so you can find the filter that caused a total that is smaller than you expect.

`get` prints a path. The files are 100KB–1MB or more, so read parts of them. Never read a full file.

## Query syntax

- A space means AND: `magic tricks` needs both words
- `"quoted phrases"` match exactly
- `OR` gives alternatives: `Houdini OR Kellar`
- `-` excludes a term: `magic -card`
- Parentheses group the terms: `(Houdini OR Kellar) AND escape`
- `creator:Houdini` searches one field; `magi*` is a wildcard

**A match is at document level, not at page level.** `"Harry Houdini" AND Edison` matches a 900-page volume that holds both terms hundreds of pages apart, with no relation between them. Treat a result from a multi-term AND query as a lead. Never accept it as evidence of a connection.

Put the alternatives into one query in place of several separate searches. One `(A OR B OR C)` call costs a fraction of three separate calls.

## Filters

Each filter is optional, and each filter reads the **catalogue metadata of the item**. No filter reads the page that holds the match.

Each filter selects items from the catalogue metadata:

- `--from-year 1880 --to-year 1900` — the items whose catalogued year is in that range
- `--language French` — the items catalogued as French
- `--title "Household Words"` — the items whose title holds that phrase
- `--creator "Dickens, Charles"` — the works *by* a person, not the works that mention the person
- `--subject "Historical Journals"` — a catalogued subject heading

Put quotation marks around a value that holds a space. The creator form and the subject form are different for each item. Copy them from a result line when you need precision.

**Warning: Do not trust a filtered total. A filter removes each item whose metadata does not hold that field, and it gives no message when it does this.** The metadata of archive.org is very uneven. These filters do not limit a set. They intersect it with "the items that a person catalogued correctly".

- `clairvoyance --collection iapsop` gives 11,632 results. Add `--from-year 1890 --to-year 1899` and it gives **0**. The collection does hold material from the 1890s, but `iapsop` items have no year, date, language, creator or subject metadata at all. Their only date is inside the identifier (`IAPSOP-light_v19_n940_jan_14_1899`), and the result lines show `n.d.` to report this.
- So: **never put a year, language, creator or subject filter on an `iapsop` search.** Filter `iapsop` by the dates that you read in the identifiers instead.
- More generally, a filter can reduce the total to zero or nearly zero. Suspect absent metadata before you conclude that the material does not exist. Do the search again without the filter. Then check whether the result lines carry real dates.

Where a collection has no catalogued language, you can often still use `ocr_detected_lang`. Put it directly into the query: `clairvoyance AND ocr_detected_lang:en --collection iapsop` keeps 10,529 of those 11,632 results. This field is the guess of the scanner about the scan. It is not a statement of the catalogue, so it improves recall but it is not evidence.

`--language` takes a name or a MARC code (`French`, `fre`). archive.org accepts both forms from the people who upload items, and the two forms select different items. Therefore the client searches each known spelling of a named language at the same time. It sends an unknown value without a change.

Filters have the most value when they make a very large query readable: `"second sight" --from-year 1880 --to-year 1900 --language English` reduces a six-figure total to 18,708. Filters have the least value on the collections that this project uses most, for the reason above.

## How to be complete

There are 100 results on each page. `--pages all` collects each page in one call. Use it in place of a loop over the pages when the total is small enough. Read the total in the header first. At several thousand results, make the query more narrow. Do not collect the results, because the rejection work will cost more than the results give.

## False positives to expect

The braces show which token matched. This is the most useful signal on this source.

- **A substring match inside a longer name.** The query `"Ann Hunter"` gives `Jo{ann Hunter}` and `Le{ann Hunter}`. Read the braces before you accept a result.
- **Hyphenation across a line break.** The OCR divides words, so `Jo- {ann Hunter}` joins to "Joann Hunter". This is an incorrect match that looks correct.
- **OCR errors inside the match**, for example `{Ann) Hunter}`. This is usually still the correct person. Judge it from the context.
- **Bibliographies and indexes.** A name in a list of citations or in a table of contents is a mention with no substance behind it.
- **An incorrect sense or an incorrect person.** Make sure that the context agrees with the topic of your research before you accept a result.

Snippets are usually enough to reject a false positive. Use `get` on a document only after the snippets suggest that the document is genuinely worth a reading.

## Cost

A search has a low cost. `get` does not. The downloads are large, and the client keeps them in a cache under `$XDG_CACHE_HOME/archives-mcp`. If you must read many documents, use subagents. The OCR text then stays out of the main context. Tell each subagent to report the findings with page numbers and quotations.

The client sends a maximum of approximately two requests each second, and all processes share this limit. Therefore parallel subagents share one budget. Many parallel subagents read the documents more quickly. They do not send the requests more quickly.

If a `get` command fails, read the message before you make a conclusion. "no OCR text" means that the item genuinely has none. Any other error means that the request can be worth one more attempt. Use `--refresh` to download an item again that is already in the cache.

**Warning: Do not send too many requests. Too many requests cause the archive to block you, and the block continues after this session.**

This is a free public service. A search that looks thorough to you looks like data collection to the archive operators. Sometimes the requests start to fail. Sometimes they give content that you did not ask for. If this occurs, stop. Tell the user. Do not send the request again, because a repeated request makes the block longer.
