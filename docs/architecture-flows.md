# PAL Architecture Flows

## Ingestion Pipeline

Content enters the vault through three paths: direct creation, web fetch pipeline, and document import. All paths end with the article indexed and git-committed.

```
Direct Creation (/note)
  User: /note <topic>
    |
    v
  Daemon builds prompt --> inference server (chat completion)
    |
    v
  Categorizer scans vault dirs --> picks category
    |
    v
  WikiManager.write_article() --> {category}/{slug}.md (with frontmatter)
    |
    v
  WikiManager.rebuild_index() --> _index.md updated
    |
    v
  git commit


Web Fetch Pipeline (/fetch --> /summarize --> /compile)

  User: /fetch <url>
    |
    v
  AllowlistManager.is_allowed() -- domain check against _config/allowlist.md
    |
    v
  URLFetcher.fetch() -- HTTP GET with prompt injection defenses
    |                    (GUID boundaries, sanitization, size cap)
    v
  raw/web/{slug}.md -- quarantine zone, untrusted content
    |
    |  User: /summarize <raw-path>
    v
  Daemon reads raw file --> inference server (summarize prompt)
    |
    v
  raw/summaries/{slug}.md -- sanitized summary
    |
    |  User: /compile <summary-path>
    v
  Daemon reads summary
    |
    v
  Categorizer --> picks target category
    |
    v
  find_existing_article() -- does a sibling article already cover this topic?
    |                         (index lookup + model confirmation)
    |
    +-- no match: first compile --> inference server (compile prompt)
    |               |
    |               v
    |             new Article with single timeline entry
    |
    +-- match found: merge compile --> inference server (merge prompt
                    |                   with existing compiled truth +
                    |                   new source material)
                    v
                  existing Article rewritten compiled truth + new
                  timeline entry appended (created date preserved)
    |
    v
  Article serialized with <!-- TIMELINE --> marker separating
    compiled truth from append-only timeline entries
    |
    v
  WikiManager writes to {category}/{slug}.md
    |
    v
  WikiManager.rebuild_index() --> _index.md updated
    |
    v
  archive_raw_files() -- moves raw + summary to raw/archived/
    |
    v
  git commit


Batch Research and Compilation (/research --> /compile-batch)

  User: /research <topic or path/to/topics.md>
    |
    v
  If path: parse_topic_file() extracts bullet items as topics
  If topic: wrap as single-item list
    |
    v
  For each topic:
    Researcher._search_with_refinement()
      |
      v
    WebSearchClient --> SearxNG /search
      |
      |  if thin results, retry with "{topic} tutorial",
      |  "{topic} documentation", "{topic} guide"
      v
    Top N unique URLs (default 3, deep mode up to 10)
      |
      v
    Researcher fetches each URL concurrently (asyncio.gather)
      |
      |  cross-topic dedup: skip URLs already fetched in this batch
      v
    For each fetch: raw/web/{topic-slug}-{source-slug}-{hash8}.md
      |
      v
    Researcher summarizes each raw file
      |
      v
    raw/summaries/{topic-slug}-{source-slug}-{hash8}.md
    |
    v
  Report: topic/source counts, flagged topics with no usable results.
  Review gate: summaries stay in raw/summaries/ until explicit compile.


  User: /compile-batch
    |
    v
  List all summaries in raw/summaries/
    |
    v
  For each summary, sequentially:
    Daemon._compile_one() -- same flow as /compile above
      |
      |  includes topic match, so multiple sources on the same
      |  topic produce ONE article with multiple timeline entries
      v
    Save / merge / archive / git commit
    |
    v
  Final report: new articles, merged-into-existing,
  insufficient content, errors.


Document Import (/import)

  User: /import <file-path>
    |
    v
  DocumentConverter.convert() -- markitdown: PDF/DOCX/PPTX/XLSX --> markdown
    |
    v
  chunk_markdown() -- heading-based split into named chunks
    |
    v
  For each chunk:
    Categorizer --> picks category
    WikiManager.write_article() --> {category}/{slug}.md
    |
    v
  WikiManager.rebuild_index() --> _index.md updated
    |
    v
  git commit
```

Separately from PAL, the inference server re-embeds on startup or when its collections are reindexed. It watches the vault directory configured in `collections.json`, hashes each file with SHA-256, and only re-embeds files that changed since the last run. This happens in the manager process, not in PAL.

## Retrieval Paths

When PAL's agent needs information from the vault, it has two paths. The agent chooses based on the query type.

```
User asks a question
  |
  v
Agent decides which tool to call
  |
  +---> search_vault (semantic)        search_content (keyword)
  |       |                               |
  |       v                               v
  |     RetrievalClient.search()        vault_path.rglob("*.md")
  |       |                               |
  |       | HTTP POST                     | filesystem scan
  |       v                               v
  |     Manager :11434                  line-by-line text match
  |       /collections/{id}/search        |
  |       |                               v
  |       v                             Up to 20 matching lines
  |     SQLite-vec cosine similarity      with file:line snippets
  |       (nomic-embed-text embeddings)
  |       |
  |       v
  |     Ranked results (score, name, summary)
  |       |
  |       +---> Agent reads a summary, wants full content
  |               |
  |               v
  |             get_document (by ID)
  |               |
  |               | HTTP GET
  |               v
  |             Manager :11434
  |               /collections/{id}/docs/{doc_id}
  |               |
  |               v
  |             Full document content
  |
  +---> read_file (direct)             list_directory
  |       |                               |
  |       v                               v
  |     Path.read_text()               Path.iterdir()
  |       (up to 32KB)                   (sorted, filtered)
  |
  +---> edit_file / create_file
          |
          v
        WikiManager.write_article()
          |
          v
        git commit
```

## Vault Layout

```
~/vault/
  _index.md              Auto-maintained article index (WikiManager.rebuild_index)
  _profile/              User profile -- injected into every system prompt
    {username}.md
  _wisdom/               Promoted learnings -- injected into every system prompt
    *.md
  _learning/             Extracted learnings from conversations
    *.md
    ratings.md           Append-only rating log
  _config/
    allowlist.md         Domain allowlist for /fetch
  raw/
    web/                 Fetched URL content (quarantine)
    summaries/           Sanitized summaries of raw content
    archived/            Post-compile archive (auto-deleted after 30 days)
  Research/              \
  Projects/               } User articles organized by topic
  Security/              /  (categories chosen by Categorizer)
  ...
```

Underscore-prefixed directories are system-managed. They are hidden from the agent's `list_directory` and `search_content` tools but are read by the daemon for system prompt injection (profile, wisdom) and internal operations (learning, allowlist).

## Data Access Summary

| Operation | Access method | Path |
|---|---|---|
| Semantic search | HTTP to manager | /collections/{id}/search |
| Full doc by ID | HTTP to manager | /collections/{id}/docs/{id} |
| Keyword search | Filesystem rglob | vault_path/**/*.md |
| Read file | Filesystem read | vault_path/{path} |
| Write/edit file | Filesystem write | vault_path/{path} |
| List directory | Filesystem iterdir | vault_path/{dir} |
| Profile/wisdom injection | Filesystem read | _profile/, _wisdom/ |
| Learning management | Filesystem read/write | _learning/ |
| Index rebuild | Filesystem scan + write | _index.md |
| Embedding + indexing | Manager-side (not PAL) | SQLite-vec in manager process |
