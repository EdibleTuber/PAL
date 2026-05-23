# PAL - Personal Agentic Librarian

A CLI conversational companion that manages an Obsidian wiki vault. PAL acts as a personal AI librarian: it can read, search, write, and organize your knowledge base through natural conversation, and it learns from interactions over time.

## How It Works

PAL runs as a daemon that connects to a local LLM inference server. You interact with it through a terminal REPL. During conversation, PAL can autonomously look up files, search your vault, edit articles, and create new ones, all without you needing to use explicit commands.

```
you> Hey, what's in the vault?
  [listing vault...]

Research/, Projects/, raw/, templates/
...

you> That quantum computing article is a wall of text. Can you restructure it with headings?
  [reading Research/quantum.md...]
  [editing Research/quantum.md...]

Done. I've reorganized the article with sections for...
```

Every write is git-committed automatically, so you can always review or revert changes.

## Quickstart

A 5-minute path from zero to a working PAL CLI. Assumes you have Python 3.12+ and git.

**1. Get an inference server running.** PAL talks to any OpenAI-compatible endpoint. The simplest option is [Ollama](https://ollama.com):

```bash
# Install and start Ollama (defaults to localhost:11434)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:32b     # or any model you want as your chat default
```

Already running a llama.cpp server, vLLM, LM Studio, or a remote manager? Skip this step and use its URL below.

**2. Clone and install PAL.**

```bash
git clone https://github.com/EdibleTuber/PAL.git
cd PAL
pip install -e .
```

**3. Create your vault.** PAL's vault is just a directory of markdown files. PAL will `git init` it on first write so your changes are tracked automatically:

```bash
mkdir -p ~/vault
```

**4. Point PAL at your inference server.** Set env vars (or put them in your shell rc):

```bash
export PAL_INFERENCE_URL=http://localhost:11434
export PAL_MODEL=qwen2.5:32b       # whatever you pulled in step 1
export PAL_VAULT_PATH=~/vault
```

**5. Start the daemon and CLI.** Two terminals:

```bash
# Terminal 1
pal-daemon

# Terminal 2
pal
```

You should see a prompt. Try:

```
you> make a note about mitmproxy cert pinning
  [creating raw/notes/mitmproxy-cert-pinning.md...]

Saved. The note covers common pinning strategies and how mitmproxy's
certificate injection sidesteps them.

you> what's in my vault?
  [listing vault...]

raw/notes/mitmproxy-cert-pinning.md
```

That's it. From here, explore `/help` for commands, or just chat with PAL. Ask it to research a topic, edit a note, or consolidate articles -- it'll use tools as needed and ask for approval before web fetches or wiki writes.

**Discord (optional).** If you want mobile access, set `PAL_DISCORD_TOKEN` and `PAL_DISCORD_ALLOWED_USERS` and run `pal-discord` in a third terminal. Each Discord channel keeps its own conversation and scratchpad — see [Per-Channel Context](#per-channel-context) below.

## Architecture

```
CLI (pal)  ----unix socket---->  Daemon (pal-daemon)  ----HTTP---->  Inference Server
                                      |                              (Ollama-compatible)
Discord (pal-discord)  --unix socket--+
                                      |
                                      v
                                  ~/vault (Obsidian wiki, git-tracked)
```

- **CLI**: thin REPL client with streaming markdown rendering
- **Discord**: bot that bridges Discord DMs and mentions to the daemon for mobile access
- **Daemon**: always-on process that manages conversations, tools, and the vault
- **Inference Server**: any OpenAI-compatible local LLM. Tested with `gemma-4-26b-a4b-it-q4_k_m` and `Qwen3.5-35B-A3B-Q4_K_M`. Model choice is configurable via `PAL_MODEL` or switched at runtime with `/model`.

## Security

PAL is an agentic system that processes untrusted content and executes LLM-directed tool calls. Defense layers include prompt-injection sanitization on web fetches, vault-scoped path traversal checks on every file operation, a domain allowlist on web fetches, Unix-socket filesystem ACLs, an explicit Discord user allowlist, channel ID validation before any filesystem use, and automatic git-commits on every vault write as a safety net.

Full breakdown in [docs/security.md](docs/security.md).

## Setup

### Requirements

- Python 3.12+
- An OpenAI-compatible inference server (Ollama, llama.cpp, vLLM, etc.)
- Git (for vault version control)

### Install

```bash
pip install -e .
```

### Configure

All settings use environment variables with a `PAL_` prefix. Defaults assume a local inference server and `~/vault` as the vault path.

**Core:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PAL_INFERENCE_URL` | `http://192.168.1.14:11434` | Inference server URL |
| `PAL_MODEL` | `Qwen3.5-35B-A3B-Q4_K_M` | Model name |
| `PAL_VAULT_PATH` | `~/vault` | Path to the Obsidian vault |
| `PAL_SOCKET_PATH` | `$XDG_RUNTIME_DIR/pal.sock` | Unix socket path |
| `PAL_HISTORY_DEPTH` | `50` | In-memory conversation window |
| `PAL_COLLECTION_ID` | `vault` | Retrieval collection ID on the inference server |
| `PAL_USERNAME` | `user` | Profile username |

**Web research:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PAL_SEARXNG_URL` | `http://192.168.1.14:8080` | SearxNG instance for web search |
| `PAL_FETCH_MAX_BYTES` | `2000000` | Max bytes when fetching URLs |
| `PAL_FETCH_TIMEOUT` | `30` | Fetch timeout in seconds |

**Per-channel context:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PAL_CHANNELS_DIR` | `~/.local/share/pal/channels` | Per-channel conversation history location |
| `PAL_SCRATCHPAD_MAX_BYTES` | `2048` | Size cap for per-channel scratchpad |

**Batch inference (Phase B):**

| Variable | Default | Description |
|----------|---------|-------------|
| `PAL_BATCH_ENABLED` | `false` | Enable batch slot for background workloads |
| `PAL_BATCH_INFERENCE_URL` | `http://192.168.1.14:11434` | Manager URL for the batch slot (usually same as main) |
| `PAL_BATCH_MODEL` | `gemma-4-E4B-it-Q4_K_M` | Default model for the batch slot |

**Discord:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PAL_DISCORD_TOKEN` | - | Discord bot token (required for Discord) |
| `PAL_DISCORD_ALLOWED_USERS` | - | Comma-separated Discord user IDs |

### Run

```bash
# Start the daemon (in one terminal or via systemd)
pal-daemon

# Connect with the CLI
pal
```

Systemd service files are included in `systemd/` for running as background services.

### Discord

PAL can also be accessed through Discord for mobile use. The Discord bot connects to the same daemon over a Unix socket.

```bash
# Start the Discord bot (requires PAL_DISCORD_TOKEN)
python -m pal.discord_main
```

- Responds to DMs and @mentions in channels
- Supports slash commands via `!command` syntax (e.g. `!note`, `!search`)
- Shows tool progress as the daemon works
- Access is restricted to user IDs listed in `PAL_DISCORD_ALLOWED_USERS`
- **Per-channel isolation**: each Discord channel keeps its own conversation history and scratchpad. Talking to PAL in `#gdb-mcp` doesn't leak into `#general` or your DMs. See [Per-Channel Context](#per-channel-context) below.
- A systemd service (`pal-discord.service`) is included, configured to start after the daemon

## Usage

### Chat

Type naturally. PAL streams responses with live markdown rendering and uses tools to read, search, edit, and write your vault.

Web fetches and most vault writes go through a consent-gated proposal flow. When PAL wants to research a topic or merge articles, it sends an approval prompt; you review and click approve, decline, or edit.

```
you> research the topics in raw/notes/queue.md
  [reading raw/notes/queue.md ...]
  [proposes research with 5 topics]

[Approve] [Decline] [Edit]

you> approve
  [Fetched: example.com/page-1 ...]
  [Summarized: example.com/page-1 ...]
  ...
  [5 summaries staged in raw/summaries/]

you> compile those into the wiki
  [proposes compile-batch with 5 summaries]

[Approve] [Decline] [Edit]
```

Single-source operations (a single research, a single compile) follow the same propose/approve pattern. The full set of tools PAL has during a chat turn is in [Chat Tools](#chat-tools) below.

### Slash Commands

| Command | Description |
|---------|-------------|
| `/scratch <text>` | Append a timestamped note to this channel's scratchpad (see [Per-Channel Context](#per-channel-context)) |
| `/learn` | Extract learnings from the conversation |
| `/learnings` | List saved learnings |
| `/promote <id>` | Promote a learning to active wisdom |
| `/rate <id> <1-5>` | Rate a learning |
| `/profile [set]` | View or update your profile |
| `/wisdom` | List, add, or remove wisdom entries |
| `/lint` | Run a vault health check |
| `/model [name\|list\|default]` | Show, switch, list, or reset the active model. A change applies to every subsequent inference call (chat, research summaries, article compilation, background tasks). |
| `/think [on\|off\|auto\|show\|hide]` | Control reasoning output for the current session |
| `/context` | Show context budget: last-turn tokens + component byte sizes |
| `/status` | Show active model, config default, server, and vault info |
| `/help` | Show all commands |
| `/quit` | End the session |

### Chat Tools

These are used automatically by the LLM during conversation. You don't invoke them directly.

**Vault reads:**

| Tool | Description |
|------|-------------|
| `read_file` | Read a vault file |
| `list_directory` | List vault directory contents (paginated) |
| `search_content` | Keyword search across files |
| `search_vault` | Semantic search via retrieval API |

**Vault writes:**

| Tool | Description |
|------|-------------|
| `edit_file` | Rewrite a file's body (preserves frontmatter) |
| `create_file` | Create a scratch note under `raw/notes/` |
| `move_file` | Move a single article between directories |

**Wiki promotion (consent-gated via proposal/approval flow):**

| Tool | Description |
|------|-------------|
| `compile_summary` | Promote one raw summary into a wiki article |
| `propose_compile_batch` | Propose promoting multiple summaries; blocks on user approval |
| `compile_batch` | Execute an approved compile batch |
| `propose_consolidate` | Propose fusing 2+ wiki articles into a new one |
| `consolidate` | Execute an approved consolidate |
| `propose_reorg` | Propose move/merge operations across the vault |
| `reorg` | Execute an approved reorg batch |

**Web research (consent-gated):**

| Tool | Description |
|------|-------------|
| `search_web` | SearxNG preview query; titles + snippets, no fetch |
| `propose_research` | Propose a full research run; blocks on user approval |
| `research_topic` | Execute an approved research run |

**Channel-scoped state:**

| Tool | Description |
|------|-------------|
| `update_scratch` | Replace the scratchpad for the current channel (<= 2 KB, auto-injected into system prompt) |

**Reindex:**

| Tool | Description |
|------|-------------|
| `wait_for_reindex` | Poll a reindex job until done or timeout (use only when freshness matters mid-turn) |

**Learning:**

| Tool | Description |
|------|-------------|
| `add_learning` | Save a durable lesson extracted from conversation |
| `propose_promote` | Propose promoting a learning to active wisdom |

Write tools are restricted to non-system directories and every write is git-committed.

Write tools (`compile_summary`, `compile_batch`, `consolidate`, `reorg`, `create_file`, `edit_file`, `move_file`) automatically trigger an incremental reindex on the inference server after success. The tool result includes a `reindex` field with a `job_id` and current status; the new content is typically searchable within a second or two without any further action. For mid-turn cases that require certainty, `wait_for_reindex` polls until the job completes.

## Vault Structure

```
~/vault/
  _index.md            # Auto-maintained article index
  _profile/            # User profile (injected into system prompt)
  _wisdom/             # Active wisdom entries (injected into system prompt)
  _learning/           # Extracted learnings from conversations
  _config/             # Configuration (web search allowlist)
  _channels/           # Per-channel scratchpads (scratch.md per channel)
  Research/            # Wiki articles organized by topic
  raw/
    web/               # Fetched URL content (quarantine)
    summaries/         # Sanitized summaries
  ...                  # More topic directories as needed
```

- **Underscore-prefixed directories** are system-managed. They are hidden from chat tools and managed through slash commands.
- **All files** are markdown with YAML frontmatter (title, created, updated, tags).
- **Git-tracked** with automatic commits on every change.

## Learning System

PAL learns from conversations over time:

1. **Learnings** are extracted from conversations via `/learn` and stored in `_learning/`.
2. **Wisdom** entries are promoted from learnings via `/promote` and stored in `_wisdom/`. Active wisdom is injected into the system prompt on every chat turn.
3. **Profile** facts about the user are stored in `_profile/` and also injected into every prompt.
4. **Ratings** via `/rate` help surface the most useful learnings for promotion.

## Per-Channel Context

PAL scopes conversation history and a per-channel scratchpad by Discord channel (or a `cli-default` sentinel for the CLI), so different channels stay isolated and context survives daemon restarts.

**Conversation history.** Each channel's turns are appended to a JSONL log at `$PAL_CHANNELS_DIR/<channel_id>/history.jsonl` (default `~/.local/share/pal/channels/`). On the first message to a channel after a daemon restart, the log is replayed to rebuild in-memory state. The on-disk log grows unbounded; the in-memory window still honors `PAL_HISTORY_DEPTH`. History is not git-tracked (mechanical state, not knowledge).

**Scratchpad.** Each channel has a free-form markdown file at `<vault>/_channels/<channel_id>/scratch.md`, git-tracked, auto-injected into PAL's system prompt for every turn in that channel. Two update paths:

- `update_scratch` tool: PAL replaces the scratchpad wholesale when it decides something is worth recording ("current phase: 2", "we chose FastMCP", etc.).
- `/scratch <text>` slash command: you append a timestamped line yourself.

The scratchpad is capped at `PAL_SCRATCHPAD_MAX_BYTES` (default 2 KB) to keep it as working state, not a second wiki. Oversized writes fail cleanly; PAL prunes and retries.

**CLI behavior.** The CLI always uses channel_id `cli-default`. Your CLI conversation now persists across daemon restarts (was ephemeral before). To start fresh, delete or rename `~/.local/share/pal/channels/cli-default/history.jsonl`.

**Discord behavior.** `on_message` forwards `message.channel.id`; each channel gets its own conversation and scratchpad on first use.

## Batch Inference (Phase B)

PAL can route latency-tolerant background workloads (categorize, learning scanner, PDF TOC detection) to a second inference slot, keeping the chat GPU undisturbed.

**How it works.** The inference server exposes two slots via one manager endpoint (default `http://192.168.1.14:11434`):
- `main`: Tesla P40 / CUDA, runs the chat model.
- `batch`: AMD Vega iGPU / Vulkan, runs a small fast model (default Gemma 4 E4B IT Q4_K_M).

The manager routes `/v1/chat/completions` to whichever slot has the requested model loaded. PAL exposes the dual-backend through `PAL_BATCH_*` config.

**Enabling.** Set `PAL_BATCH_ENABLED=true` and restart the daemon. With the flag off, PAL behaves identically to pre-Phase-B (everything on main). `PAL_BATCH_MODEL` picks the batch model name; `PAL_BATCH_INFERENCE_URL` overrides the batch manager URL (usually the same as main).

**Outage behavior.** If the batch slot returns `batch_unavailable`, user-facing callers (categorizer, PDF TOC detection) surface a `BatchFallbackProposal` with retry / run-on-main / skip options. Background callers (learning scanner) log and skip silently.

**Model management.** `/model --target batch <name>` swaps the batch slot without disturbing main. `/model` shows both slots' currently loaded models.

## Article Format

Compiled articles use a two-zone structure: a regenerated "compiled truth" zone (Overview, Key Concepts, etc.) above a `<!-- TIMELINE -->` marker, and an append-only timeline of dated source entries below it. Each timeline entry is self-contained with its own source URL, hash, and summary, so raw files can age out without losing provenance.

Full format specification, frontmatter fields, and update semantics in [docs/article-format.md](docs/article-format.md).

## Document Import

Drop a file (PDF, DOCX, XLSX, PPTX, HTML, EPUB, CSV) into `raw/` in your vault and run `/import raw/filename.pdf`. PAL converts, summarizes, compiles, and auto-categorizes each section into the best-fitting vault directory. For PDFs, a three-tier chapter detector (embedded TOC → typography → LLM-based) finds boundaries.

Details and limitations in [docs/document-import.md](docs/document-import.md).

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v
```

## Lineage

PAL draws from:

- **PAI** (danielmiessler/Personal_AI_Infrastructure) for the algorithm, TELOS, and learning loop methodology
- **Mother** (pi-mono) for the learning system implementation (ratings, learnings, wisdom, relationships)
- **Karpathy's LLM knowledge base pattern** for raw ingestion, LLM-compiled wiki, and index-based navigation
