# Compiled Truth + Timeline

**Date:** 2026-04-12
**Status:** Draft

## Overview

Every compiled wiki article in PAL's vault gets a structural split between two zones: compiled truth (current best understanding of the topic, rewritten when evidence changes) and timeline (append-only evidence trail, never edited). This pattern comes from gbrain's architecture and solves the problem of knowledge drift as the vault scales - with hundreds of articles from research mode, there's no way to distinguish authoritative current understanding from stale first-pass compilations without an enforced convention.

The compiled truth section uses a flexible template with required and optional sections so articles are consistently structured for both human reading in Obsidian and agent consumption. Timeline entries are built by code (not the model) so their format is always correct. The model handles synthesis and prose; code handles structure and provenance.

## Goals

- Enforce a structural convention on compiled wiki articles that separates "what we know" from "how we learned it"
- Enable merge-on-compile: when new source material covers an existing topic, rewrite the compiled truth to incorporate it rather than creating duplicate articles
- Make timeline entries self-contained so raw files can be archived without losing provenance
- Structure compiled truth with a flexible template for agent-readable consistency
- Handle legacy articles gracefully (no migration required)

## Non-Goals (v1)

- Automatic re-compilation when new sources arrive
- Lint rules for the new format (follow-up)
- Migration of existing articles to new format
- Changes to `/import` (only `/compile` affected)
- Typed link graph between articles
- Changes to raw data retention policy

## Article Format

A compiled article has three zones: YAML frontmatter, compiled truth, and timeline. The zones are separated by a `<!-- TIMELINE -->` HTML comment marker. This marker is invisible in Obsidian rendering, unambiguous for parsing, and won't conflict with YAML frontmatter `---` delimiters or markdown horizontal rules.

```markdown
---
title: SQLite-vec Similarity Search
created: 2026-04-12T14:30:00Z
updated: 2026-04-12T16:45:00Z
compiled_at: 2026-04-12T16:45:00Z
status: compiled
sources:
  - url: https://github.com/asg017/sqlite-vec
    hash: abc12345
    added: 2026-04-12T14:30:00Z
  - url: https://docs.example.com/sqlite-vec
    hash: def67890
    added: 2026-04-12T16:45:00Z
---

## Overview

SQLite-vec is an extension for SQLite that adds vector search...

## Key Concepts

- **HNSW indexes** - approximate nearest neighbor...
- **Distance metrics** - cosine, L2, inner product...

## Usage

```sql
SELECT * FROM vec_articles WHERE embedding MATCH ?
```

## Gotchas

- Index must be rebuilt if ef_construction changes...

<!-- TIMELINE -->

### 2026-04-12 - github.com/asg017/sqlite-vec
**Source:** https://github.com/asg017/sqlite-vec
**Added:** 2026-04-12T14:30:00Z
**Source hash:** abc12345

SQLite-vec provides vector similarity search as a loadable SQLite extension.
Supports HNSW indexing with configurable ef_construction and ef_search
parameters. Distance metrics include cosine, L2, and inner product...

### 2026-04-12 - docs.example.com/sqlite-vec
**Source:** https://docs.example.com/sqlite-vec
**Added:** 2026-04-12T16:45:00Z
**Source hash:** def67890

Detailed API reference covering vec_search(), index creation options,
and performance tuning. Key finding: ef_construction=200 is recommended
for datasets under 1M vectors...
```

### Frontmatter

| Field | Description |
|-------|-------------|
| `title` | Article title |
| `created` | ISO timestamp, set once on first compile |
| `updated` | ISO timestamp, updated on every recompile |
| `compiled_at` | ISO timestamp, updated when compiled truth is rewritten |
| `status` | `"compiled"` (unchanged from current convention) |
| `sources` | List of source references, each with `url`, `hash`, `added` |

The `sources` list in frontmatter is the machine-readable provenance index. It mirrors the timeline entries but is queryable without parsing markdown body content.

### Compiled Truth Section

Uses a flexible template. Required sections appear on every article. Optional sections are included by the model when relevant to the topic.

**Required:**
- **Overview** - What this is, in 2-3 sentences
- **Key Concepts** - Core ideas, terminology, mental model

**Optional (model selects what's relevant):**
- **Usage** - How to use it (APIs, commands, patterns)
- **Configuration** - Setup, tuning, options
- **Gotchas** - Known issues, quirks, common mistakes
- **Related** - Links to related wiki articles

### Timeline Section

Append-only. Each entry is a structured record of one source that was incorporated into the compiled truth. Entries are built by code, not the model, so format is deterministic.

**Entry format:**
```markdown
### YYYY-MM-DD - source-label
**Source:** <full URL>
**Added:** <ISO timestamp>
**Source hash:** <hash from raw file>

<thorough summary of what this source contributed - self-contained,
survives raw file archival>
```

Timeline entries must be self-contained. After raw files are archived (30-day retention), the timeline entry is the permanent record of what that source contributed. The summary should capture the key facts, not just a one-liner.

## Architecture

### New Module: `pal/article.py`

Owns the compiled truth + timeline format. Parses, validates, assembles, and serializes articles. The model writes prose; this module owns structure.

**Data structures:**

```python
@dataclass
class TimelineEntry:
    date: str            # YYYY-MM-DD
    source_label: str    # hostname or short label
    source_url: str      # full URL
    source_hash: str     # content hash from raw file
    added: str           # ISO timestamp
    summary: str         # thorough summary text

@dataclass
class Article:
    meta: dict                      # YAML frontmatter
    compiled_truth: str             # everything above TIMELINE marker
    timeline: list[TimelineEntry]   # parsed timeline entries
```

**Functions:**

- `parse_article(text: str) -> Article` - Splits markdown into frontmatter, compiled truth, and timeline entries. If no `<!-- TIMELINE -->` marker exists (legacy article), treats entire body as compiled truth with empty timeline.

- `append_timeline_entry(article: Article, source_url: str, source_hash: str, summary: str) -> Article` - Creates a new TimelineEntry with current timestamp, appends to timeline list, adds source to frontmatter `sources` list. Returns updated Article.

- `validate_compiled_truth(text: str) -> list[str]` - Checks that model output contains at least `## Overview` and `## Key Concepts` headings. Returns list of issues (empty = valid). Does not reject extra sections.

- `serialize_article(article: Article) -> str` - Reassembles frontmatter + compiled truth + `<!-- TIMELINE -->` + timeline entries into a single markdown string.

### Compile Flow

#### First Compile (no existing article)

1. `/compile <summary-path>` triggered by user
2. Read summary file (frontmatter + body)
3. Categorizer picks target directory
4. Check wiki index for existing article on same topic (see Topic Matching below)
5. No match found - proceed with initial compile
6. Build compile prompt with template instructions (required: Overview, Key Concepts; optional sections)
7. Model produces compiled truth text
8. `validate_compiled_truth()` checks for required sections
9. `Article` created with compiled truth + one timeline entry (built from summary metadata)
10. `serialize_article()` produces final markdown
11. Wiki manager saves to target directory and git commits

#### Merge Compile (existing article found)

1. `/compile <summary-path>` triggered by user
2. Read summary file (frontmatter + body)
3. Categorizer picks target directory
4. Check wiki index for existing article on same topic
5. Match found - load existing article with `parse_article()`
6. Build merge prompt containing:
   - Existing compiled truth
   - Existing timeline entries (as context)
   - New source material (the summary being compiled)
   - Instructions: "Rewrite the compiled truth to incorporate the new information. Use the flexible template. Do not drop existing knowledge unless the new source contradicts it."
7. Model produces new compiled truth text
8. `validate_compiled_truth()` checks for required sections
9. `append_timeline_entry()` adds new source to timeline
10. Update frontmatter timestamps (`updated`, `compiled_at`)
11. `serialize_article()` produces final markdown
12. Wiki manager saves (overwrites existing) and git commits

#### Topic Matching

When compiling, PAL needs to determine if an existing article covers the same topic. The approach uses existing infrastructure:

1. Categorizer determines target directory (e.g., `Research/`)
2. List article titles in that directory from the wiki index (`_index.md`)
3. Ask the model: "Does any of these existing articles cover the same topic as this new source? If so, which one?" with the list of titles and the summary title/content
4. If the model identifies a match, load that article for merge
5. If no match, create a new article

This is lightweight (one cheap model call with a short title list) and handles fuzzy matching naturally ("SQLite-vec Similarity Search" matches "SQLite Vec Search").

## Changes to Existing Code

### `pal/daemon.py` - `/compile` handler

The current handler reads a summary, sends it to the model with a grounding prompt, saves the result. The new handler adds:

- Topic matching step before compilation (index lookup + model confirmation)
- Branch: first-compile vs merge-compile based on match result
- New compile prompts (initial template prompt, merge prompt)
- Uses `article.py` to build timeline entry and serialize final article
- Validation step before saving

### `pal/daemon.py` - Compile prompts

Two prompt variants:

**Initial compile prompt:**
```
You are writing a wiki article from source material. Use ONLY information
from the source. Structure the article with these sections:

Required:
- ## Overview (2-3 sentences, what this is)
- ## Key Concepts (core ideas, terminology)

Optional (include if relevant):
- ## Usage (APIs, commands, patterns)
- ## Configuration (setup, tuning)
- ## Gotchas (known issues, quirks)
- ## Related (links to other topics)

Write concisely and factually. If the source material is insufficient,
respond with: INSUFFICIENT: <reason>
```

**Merge compile prompt:**
```
You are updating a wiki article with new information. Below is the
current article content, followed by new source material.

Rewrite the compiled truth sections to incorporate the new information.
Keep the same section structure (Overview, Key Concepts, and any optional
sections). Do not drop existing knowledge unless the new source directly
contradicts it. If the new source adds a topic area not covered by
existing sections, add an appropriate optional section.

CURRENT ARTICLE:
{existing_compiled_truth}

PREVIOUS SOURCES (for context on what's already incorporated):
{timeline_summaries formatted as "- YYYY-MM-DD source-label: summary text" per entry}

NEW SOURCE MATERIAL:
{new_summary}
```

### No changes to

- `pal/fetcher.py`, `pal/researcher.py`, `pal/summarizer.py`
- `pal/chunker.py`, `pal/allowlist.py`, `pal/websearch.py`
- Raw data retention policy
- `/import` command (import path unchanged)
- `/fetch`, `/summarize`, `/research` commands
- Existing articles in the vault (legacy format handled by parser)

## Legacy Compatibility

Articles without a `<!-- TIMELINE -->` marker are treated as legacy: the entire body is compiled truth, timeline is empty. If a legacy article is later the target of a merge compile, the new compiled truth replaces the body and the first timeline entry is created. The article seamlessly transitions to the new format.

## Future Extensions

- **Auto-recompile suggestions:** After `/research` completes, PAL checks if summaries match existing articles and offers to recompile
- **Lint rules:** Validate new-format articles have required sections and well-formed timeline entries
- **Timeline search:** Search across timeline entries to find when/where PAL learned specific facts
- **Typed links:** Link articles to each other via the Related section, evolving toward the typed link graph
