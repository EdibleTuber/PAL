# PAL - Personal Agentic Librarian: Project Summary

## What It Is

A personal knowledge base and oracle of truth, with a long-term goal of a Jarvis-style assistant focused on reasoning and retrieval rather than task automation. PAL runs as a persistent daemon, manages an Obsidian vault, and is accessible via CLI terminal and Discord for mobile. It ingests documents, compiles them into a structured wiki, learns from conversations over time, and has full semantic search over the vault.

700 commits in under a week. This is not a prototype.

## Full Stack

### Inference Server (inference-server repo)

A custom two-layer inference backend:

```
Manager (FastAPI, :11434, LAN-facing)
├── /v1/chat/completions  → llama-server (:8081, GPU, localhost only)
├── /v1/embeddings        → llama-embeddings (:8082, CPU, localhost only)
├── /collections/*/search → SQLite-vec (in-process)
└── /collections/*/docs/* → SQLite-vec (in-process)
```

- llama-server: llama.cpp compiled with CUDA, runs on the P40, localhost only, never network-exposed
- llama-embeddings: separate CPU-only llama.cpp instance running nomic-embed-text-v1.5 Q8_0
- Model manager: FastAPI proxy with FIFO request queue (max 20), API-driven hot model swapping, GPU status reporting
- Vector store: SQLite-vec, in-process within the manager, SHA-256 hash-based change detection on ingest (only new/modified files re-embedded)
- Collections: defined in collections.json, indexed on manager startup, searchable via API

Hardware: Tesla P40, 24GB VRAM, RAM spillover for KV cache overflow. FIFO queue maximizes per-request throughput by dedicating full VRAM to one request at a time. Context window: 128k tokens. No latency requirement so long inference runs are fine.

Security: dedicated system users _llama and _llama-mgr with no shell/home and minimal permissions, narrow sudoers entry, llama-server localhost-only, no TLS (internal LAN + Tailscale for remote).

Models in use: Gemma 4 26B MoE (4B active parameters) as the primary workhorse, Qwen3 35B MoE (3B active parameters) available as an alternative. Both Q4_K_M quantized. The active model is a single global setting (see `/model` below) that applies to every inference call across chat, research, summarization, compilation, and the learning system.

### PAL (PAL repo)

```
CLI (pal) ----unix socket----> Daemon (pal-daemon) ----HTTP----> Inference Server (:11434)
                                     |
Discord (pal-discord) --unix socket--+
                                     |
                                     v
                                 ~/vault (Obsidian wiki, git-tracked)
```

- Daemon: always-on Python process, manages all conversations, tool calls, and vault writes
- CLI: thin REPL client with streaming markdown rendering
- Discord bridge: bot connecting Discord DMs/mentions to the daemon, isolated per-user connections, user ID allowlist
- Vault: flat markdown files with YAML frontmatter (title, created, updated, tags), git-committed on every write

## Vault Structure

```
~/vault/
  _index.md          # Auto-maintained article index
  _profile/          # User profile (injected into every system prompt)
  _wisdom/           # Active wisdom entries (injected into every system prompt)
  _learning/         # Extracted learnings from conversations
  _config/           # Web search domain allowlist
  raw/
    web/             # Fetched URL content (quarantine zone)
    summaries/       # Sanitized summaries
  Research/, Projects/, ...  # Wiki articles organized by topic
```

Underscore-prefixed directories are system-managed and hidden from chat tools.

## Ingest and Chunking

- markitdown converts source files (PDF, DOCX, PPTX, XLSX, etc.) to markdown
- Heading-based mechanical splitting produces separate named files per chunk
- Code-fence aware: heading detection ignores `#` inside fenced code blocks, so code comments do not get mistaken for section headings
- 292 chunks from a book PDF, correctly organized into chapters by the agent, confirms the heading-based split is working

## Article Format (Compiled Truth + Timeline)

Compiled wiki articles use a two-zone structural convention:

- Above `<!-- TIMELINE -->`: compiled truth. Current best understanding of the topic, regenerated on every compile pass. Uses a flexible template with required sections (Overview, Key Concepts) and optional sections (Usage, Configuration, Gotchas, Related).
- Below `<!-- TIMELINE -->`: append-only timeline. Each entry records one source with its URL, hash, timestamp, and a thorough summary of what that source contributed.

Frontmatter tracks a `sources` list as a machine-readable index of everything that has fed the article.

The merge-on-compile behavior is the payoff: when `compile_summary` runs on a summary that matches an existing article (detected by a lightweight index lookup plus model confirmation), the compiled truth is rewritten to incorporate the new source material while the timeline gains a new entry. The original created timestamp is preserved; `updated` and `compiled_at` advance. Timeline entries are self-contained, so raw files can age out of `raw/archived/` without breaking the provenance chain.

## Research Mode

The chat model invokes `propose_research` for topic-level research. Single-topic mode accepts a string; batch mode accepts a `topics: list[str]` with cross-topic URL deduplication. Approval is consent-gated; after approve, `research_topic` runs the SearxNG search + per-URL fetch + per-source summarize pipeline. Summaries land in `raw/summaries/` for review. Chat then proposes compilation via `propose_compile_batch` for batches or `compile_summary` for single articles.

## Retrieval

Fully implemented, not a gap:

1. nomic-embed-text embeds documents at ingest via the CPU-only llama-embeddings instance
2. Embeddings stored in SQLite-vec with SHA-256 change detection (only re-embeds modified files)
3. PAL's `search_vault` tool calls `/collections/{id}/search` on the inference server
4. Two-step retrieval: semantic search returns ranked summaries, full doc fetched by ID on demand
5. Keyword search (`search_content`) also available for exact-match cases

This is hybrid in practice: keyword search for precision, vector search for semantic queries.

## Security (PAL)

- Prompt injection: fetched web content wrapped in GUID-tagged `<untrusted-content>` boundaries with per-request random UUID. Pre-wrap sanitization: Unicode NFC normalization, zero-width and bidirectional character stripping, special token removal, token-budget truncation
- Path traversal: all file ops resolve through safe-path check rejecting `..`, leading `/`, anything outside vault root. System directories blocked from write operations
- Web fetch: domain allowlist, http/https only, no redirect following (prevents SSRF), Content-Type validation, response size cap
- Access control: unix socket with filesystem permissions, Discord restricted to explicit user ID allowlist
- Git safety net: every vault write auto-committed, fully reversible

## Learning and Memory System

Draws from PAI (danielmiessler), Mother (pi-mono), and Karpathy's LLM knowledge base pattern.

The loop:
1. `/learn` extracts learnings from conversations, stored in `_learning/`
2. `/rate` scores learnings to surface the most useful ones
3. `/promote` elevates high-value learnings to wisdom, stored in `_wisdom/`
4. Active wisdom and user profile injected into every system prompt

This is the persistent personal context layer that makes the oracle coherent across sessions without blowing the context window on raw history.

## Web Research Pipeline

Controlled ingestion for untrusted external content. The chat model drives it through proposal-gated tools:

1. `propose_research(topic=...)` for single topic OR `propose_research(topics=[...])` for batch with cross-topic URL dedup. Blocks until user approves.
2. After approve, `research_topic(proposal_id)` runs the SearxNG-filtered fetch + summarize pipeline. Per-URL progress events stream live.
3. Summaries land in `raw/summaries/` for review.
4. Chat then proposes compilation via `propose_compile_batch(summary_paths=...)` for batches or `compile_summary(summary_path=...)` for single articles. Both go through the propose/approve cycle.

Untrusted content stays quarantined until it has passed through the full sanitization and compilation pipeline. The review gate sits between `raw/summaries/` and any compile proposal, so no wiki writes happen without explicit user action.

## Proactive Behavior

Plans and todos written to disk. Intent is for the daemon to poll periodically for incomplete tasks and pick them up autonomously. Durable, survives restarts, no in-memory state loss.

Future consideration: task status beyond done/not-done (deferred, blocked, superseded) prevents the proactive loop from becoming noise. Dependency awareness between tasks eventually turns the todo list into a lightweight task graph.

## Actual Remaining Gaps

Retrieval is solved. Research ingestion is solved. Merge-on-compile is solved. The real remaining work:

1. Typed link graph: gbrain-style typed edges (`references`, `related`, `part_of`, `contradicts`, `supersedes`) between articles, stored in the inference server's SQLite-vec database with recursive CTE traversal. Enables relationship-aware retrieval across any future client.
2. Multi-query expansion: generate 2-3 alternate phrasings per semantic search query and fuse results. Quality improvement on the existing hybrid search.
3. Proactive task execution: the polling loop and task graph. Daemon polls for incomplete plans and picks them up autonomously.
4. Code documentation ingestion: structure-aware chunking (per function/class/section) with module/version/language metadata tags.
5. Wisdom and profile system maturation: the quality of the oracle scales directly with the quality of accumulated wisdom. Wisdom decay, confidence scoring, and automatic promotion of recurring learnings are still stubs.

## Build Order Assessment

The foundation is complete and correct. No debt that forces a rewrite. The pieces connect logically across both repos into a coherent system.

The gap between a query tool and something that feels like Jarvis is the proactive surfacing layer - the system knowing enough about the user to volunteer relevant information rather than wait to be asked. The wisdom and profile injection is already the foundation for this. It is now an orchestration problem, not an architecture problem.
