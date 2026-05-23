# PAL Article Format

Compiled articles use a two-zone structural convention that separates current understanding from the evidence trail.

## Structure

```markdown
---
title: ...
sources:
  - url: ...
    hash: ...
    added: ...
---

## Overview
Current best understanding, rewritten when new evidence arrives.

## Key Concepts
Core ideas, terminology, mental model.

## Usage / Configuration / Gotchas / Related (optional sections)
Included when relevant to the topic.

<!-- TIMELINE -->

### 2026-04-12 - source.example.com
**Source:** https://source.example.com/article
**Added:** 2026-04-12T14:30:00+00:00
**Source hash:** abc12345

Thorough summary of what this source contributed.
```

## Update Semantics

The **compiled truth** (everything above the `<!-- TIMELINE -->` marker) is regenerated on each compile. When a new summary matches an existing article's topic, PAL rewrites this zone to incorporate the new evidence.

The **timeline** below is append-only. Each entry is self-contained with its own source URL, hash, and summary, so:

- Raw files can age out of `raw/archived/` without losing provenance.
- Multiple sources on the same topic accumulate chronologically as distinct entries.
- Retrieval can surface a single article's history without needing the original raw files.

## Frontmatter

| Field | Purpose |
|-------|---------|
| `title` | Display title; used by the chat read path (`cat` tool) and the search index |
| `sources` | List of source entries (url, hash, added timestamp) |
| `tags` | Optional; derived during categorization or added manually |
| `created` / `updated` | ISO 8601 timestamps managed by `WikiManager` |

## When to Use What

- `create_file`: for arbitrary scratch notes under `raw/notes/` (no source linkage)
- `compile_summary` / `compile_batch`: for promoting raw summaries into this article format
- `consolidate`: for fusing multiple existing wiki articles into a new one (preserves all source linkage across the union of their timelines)
- `update_scratch`: for short-term channel-scoped working state (not articles)

## Related

See also:
- [document-import.md](document-import.md) — how imports become articles in this format
- [../README.md#web-research-pipeline](../README.md#web-research-pipeline) — the pipeline that produces the summaries that compile into articles
