# PAL Research Mode

**Date:** 2026-04-12
**Status:** Draft

## Overview

Research mode automates the discovery and ingestion of web content into PAL's knowledge base. Given a topic (or a list of topics), PAL searches SearxNG, fetches the top results, summarizes them, and pauses for user review before compiling into the wiki. This replaces the manual loop of `/search-web` + `/fetch` + `/summarize` + `/compile` for each source.

## Goals

- Batch research: hand PAL a markdown list of topics, walk away, review results later
- Single-topic research from the CLI via `/research <topic>`
- Configurable depth (default 3 sources per topic, up to 10 with `deep` flag)
- Automatic query refinement when results are thin
- Review gate before wiki compilation
- Terse progress by default, verbose with a flag

## Non-Goals (v1)

- Natural language research triggers from chat (follow-up feature)
- Image downloading or handling
- Auto-compile without review
- Claude-routed search (SearxNG only)
- Changes to the allowlist for `/fetch` or other commands

## Command Interface

```
/research <topic>                  # single topic, default depth (3 URLs)
/research deep <topic>             # deeper search (up to 10 URLs)
/research <path-to-file.md>        # batch mode, reads topics from markdown list
/research deep <path-to-file.md>   # batch + deep
/research --verbose <topic>        # show each fetch/summary as it happens
/research --verbose deep <topic>   # both flags
```

Flags can appear in any order before the topic/path argument. PAL detects file vs topic by checking if the argument resolves to an existing file path in the vault. If it does, parse it as a topic list. Otherwise treat the entire remaining argument as a topic string.

### Batch Input Format

A plain markdown bullet list. The file can live anywhere (vault or local filesystem):

```markdown
# Research Queue
- Python asyncio
- FAISS indexing strategies
- Retrieval-Augmented Generation
- systemd unit file reference
```

Only top-level bullet items (`- `) are parsed as topics. Headings and other content are ignored.

### Output Report

After the fetch+summarize phase completes, PAL prints a structured report:

```
Research complete: 4 topics, 14 sources fetched, 12 summarized

  Python asyncio (3 sources)
    + docs.python.org - asyncio overview
    + stackoverflow.com - asyncio patterns
    + realpython.com - async IO in Python

  FAISS indexing strategies (3 sources)
    + github.com - FAISS wiki
    + pinecone.io - vector indexing
    x medium.com - failed to extract content

  ...

  ! No usable results for: quantum knitting

Summaries ready in raw/summaries/. Review and run /compile to add to wiki.
```

## Architecture

### New Module: `pal/researcher.py`

Core research logic, independent of the daemon command handler. This keeps it reusable for future tool integration (natural language triggers).

**Dependencies (injected):**
- `WebSearchClient` (existing)
- `URLFetcher` (existing)
- Summarize function (extracted from daemon's `/summarize` handler)
- Progress callback for reporting
- `InferenceClient` (for summarization LLM calls)

**Data structures:**

```python
@dataclass
class SourceResult:
    url: str
    title: str
    raw_path: Path | None       # None if fetch failed
    summary_path: Path | None   # None if summarize failed
    status: str                 # "ok", "fetch_failed", "summarize_failed", "extract_empty"
    error: str | None

@dataclass
class ResearchResult:
    topic: str
    sources: list[SourceResult]
    refined_query: str | None   # set if the original query needed refinement
    flagged: bool               # True if no usable results after refinement

@dataclass
class ResearchReport:
    results: list[ResearchResult]
    total_fetched: int
    total_summarized: int
    total_failed: int
    flagged_topics: list[str]
```

### Search Strategy

1. Query SearxNG with the topic string as-is
2. Filter results: skip duplicate URLs, skip non-HTTP schemes
3. If fewer than the requested depth in usable results, retry with refined queries:
   - `"{topic} tutorial"`
   - `"{topic} documentation"`
   - `"{topic} guide"`
4. Deduplicate across all query variants
5. Take top N unique URLs (default 3, deep mode up to 10)
6. If still no usable results after refinement, flag the topic

### Fetch Strategy

- For each URL, call `URLFetcher.fetch()` with allowlist bypass (see Security section)
- Save to `raw/web/{topic_slug}-{source_slug}-{hash8}.md` with standard frontmatter
- Existing protections apply: content-type validation, size limit (2MB), trafilatura extraction
- Failed fetches logged and reported, not fatal to the batch
- Concurrent fetches within a single topic (URLs are independent)
- Topics in a batch run sequentially for readable progress output and to avoid hammering SearxNG

### Summarize Strategy

- For each successful fetch, run through the existing summarize pipeline
- Summaries saved to `raw/summaries/` with metadata linking back to the raw file
- Sanitization and prompt-injection boundary wrapping still apply
- Failed summarizations logged and reported, not fatal

### Duplicate Detection

Track all fetched URLs across the entire batch. If two topics return the same URL from SearxNG, fetch it once and reference it from both topic results.

## Security

### Allowlist Bypass for Research

The existing domain allowlist (`_config/allowlist.md`) applies to `/fetch` in normal conversation. Research mode bypasses the allowlist since the whole point is discovering new sources. The allowlist for other commands is unchanged.

**Blocklist (new, always enforced):** The current fetcher relies on the allowlist plus no-redirect as its SSRF protection. With the allowlist bypassed for research, we need an explicit blocklist. This is new code added to `fetcher.py`:
- Private/reserved IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- localhost, ::1
- file://, ftp://, and other non-HTTP schemes
- DNS resolution check: resolve the hostname before connecting and reject if it points to a private IP (DNS rebinding protection)

The blocklist applies to ALL fetches (not just research), adding defense in depth alongside the existing allowlist and no-redirect policy.

### Defense in Depth

Research content passes through multiple safety layers before reaching the wiki:
1. **Fetch:** Content-type validation, size limits, trafilatura strips non-content
2. **Summarize:** Content sanitization, prompt-injection boundary wrapping
3. **Review gate:** User reviews summaries before compile
4. **Compile:** User explicitly triggers wiki writes

## Changes to Existing Code

### `pal/fetcher.py`

- Switch `trafilatura.extract(html)` to `trafilatura.extract(html, output_format="markdown")` to preserve code blocks, formatting, and structure in extracted content. This benefits all fetches, not just research mode.
- Add `skip_allowlist: bool = False` parameter to `fetch()`. When True, skip the allowlist check but still enforce the blocklist. Default False preserves current behavior for `/fetch`.
- Add URL blocklist validation (new): resolve hostname, reject private IPs (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1), reject non-HTTP schemes. This runs on all fetches regardless of the allowlist flag, adding defense in depth to the existing no-redirect policy.

### `pal/daemon.py`

- Add `/research` to the `_handle_command` dispatcher
- Add `/research` entry to the `/help` text
- Fix inconsistent dash styles in `/help` (some entries use `--`, most use `-`)
- New `_handle_research()` method that parses flags/arguments and delegates to `Researcher`

### Summarize extraction

Extract the summarize pipeline from the `/summarize` command handler into a standalone async function (or method on a small class) that both the `/summarize` handler and `Researcher` can call. This avoids duplicating the sanitization and LLM-call logic.

## Progress Reporting

Uses the existing `ToolProgressMessage` protocol message, which the CLI renders as live-updating status text.

### Terse Mode (default)

```
Researching 1/4: Python asyncio
  Searching... 8 results
  Fetching 3 sources... done
  Summarizing... done

Researching 2/4: FAISS indexing strategies
  Searching... 3 results
  Refining query... 6 results
  Fetching 3 sources... done
  Summarizing... done
```

### Verbose Mode (`--verbose`)

```
Researching 1/4: Python asyncio
  Searching SearxNG: "Python asyncio"... 8 results
  [1/3] Fetching docs.python.org/3/library/asyncio.html... 12KB
  [1/3] Summarizing... saved raw/summaries/python-asyncio-docs-python-org.md
  [2/3] Fetching realpython.com/async-io-python/... 28KB
  [2/3] Summarizing... saved raw/summaries/python-asyncio-realpython-com.md
  ...
```

Both modes end with the full structured report and the prompt to review and compile.

## Error Handling

- **SearxNG unreachable:** Fail fast with clear message. Do not silently skip.
- **Fetch failures:** Log, continue, report in final summary. A topic with all fetches failed is flagged.
- **Summarize failures:** Log, continue, report. The raw file still exists for manual review.
- **Empty extraction:** trafilatura returns empty string. Logged as `extract_empty`, reported.
- **Duplicate URLs across topics:** Fetched once, referenced from both topic results.
- **Empty topic list file:** Report "no topics found in file" and exit.
- **Path not found:** If argument looks like a path but doesn't exist, report the error clearly.

## Future Extensions

- **Natural language triggers:** Wire `Researcher` into the chat tool path so the model can invoke research from conversation (Approach C follow-up)
- **Image support:** Download and localize images referenced in fetched content
- **Auto-compile mode:** Optional flag to skip the review gate for trusted topic lists
- **Research history:** Track what's been researched to avoid re-fetching the same topics
