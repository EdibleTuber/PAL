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
- **Inference Server**: local LLM (Qwen 3.5 35B by default) via OpenAI-compatible API

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

All settings use environment variables with a `PAL_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `PAL_INFERENCE_URL` | `http://192.168.1.14:11434` | Inference server URL |
| `PAL_MODEL` | `Qwen3.5-35B-A3B-Q4_K_M` | Model name |
| `PAL_VAULT_PATH` | `~/vault` | Path to the Obsidian vault |
| `PAL_SOCKET_PATH` | `$XDG_RUNTIME_DIR/pal.sock` | Unix socket path |
| `PAL_HISTORY_DEPTH` | `50` | Conversation history window |
| `PAL_COLLECTION_ID` | `vault` | Retrieval collection ID |
| `PAL_USERNAME` | `user` | Profile username |
| `PAL_SEARXNG_URL` | `http://192.168.1.14:8080` | SearxNG instance for web search |
| `PAL_FETCH_MAX_BYTES` | `2000000` | Max bytes when fetching URLs |
| `PAL_FETCH_TIMEOUT` | `30` | Fetch timeout in seconds |
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
- A systemd service (`pal-discord.service`) is included, configured to start after the daemon

## Usage

### Chat

Type naturally. PAL streams responses with live markdown rendering. During conversation, it can use tools to read, search, edit, and create files in your vault.

### Slash Commands

| Command | Description |
|---------|-------------|
| `/note <topic>` | Generate and save a wiki article |
| `/read <path>` | Read a vault article |
| `/search <query>` | Semantic search across the vault |
| `/get <title>` | Fetch article by exact title |
| `/search-web <query>` | Web search via SearxNG |
| `/fetch <url>` | Fetch a URL into raw/ for processing |
| `/summarize <path>` | Summarize fetched content |
| `/compile <path>` | Compile a summary into a wiki article |
| `/learn` | Extract learnings from the conversation |
| `/learnings` | List saved learnings |
| `/promote <id>` | Promote a learning to active wisdom |
| `/rate <id> <1-5>` | Rate a learning |
| `/profile [set]` | View or update your profile |
| `/wisdom` | List, add, or remove wisdom entries |
| `/lint` | Run a vault health check |
| `/status` | Show model, server, and vault info |
| `/help` | Show all commands |
| `/quit` | End the session |

### Chat Tools

These are used automatically by the LLM during conversation. You don't invoke them directly.

| Tool | Description |
|------|-------------|
| `read_file` | Read a vault file |
| `list_directory` | List vault directory contents |
| `search_content` | Keyword search across files |
| `search_vault` | Semantic search via retrieval API |
| `edit_file` | Rewrite a file's body (preserves frontmatter) |
| `create_file` | Create a new file with frontmatter |

Write tools are restricted to non-system directories and every write is git-committed.

## Vault Structure

```
~/vault/
  _index.md            # Auto-maintained article index
  _profile/            # User profile (injected into system prompt)
  _wisdom/             # Active wisdom entries (injected into system prompt)
  _learning/           # Extracted learnings from conversations
  _config/             # Configuration (web search allowlist)
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

## Web Search Pipeline

PAL can research topics from the web with a controlled pipeline:

1. `/search-web <topic>` searches via a local SearxNG instance, filtered through a domain allowlist.
2. `/fetch <url>` downloads content into `raw/web/` with prompt injection defenses (GUID-boundary wrapping, content sanitization).
3. `/summarize <path>` produces a cleaned summary in `raw/summaries/`.
4. `/compile <path>` turns the summary into a grounded wiki article.

## Security

PAL is an agentic system that processes untrusted content and executes LLM-directed tool calls. Several layers of defense keep the vault and host safe.

### Prompt Injection

Fetched web content is wrapped in GUID-tagged `<untrusted-content>` boundaries with a per-request random UUID the attacker cannot predict. A sanitizer runs defense-in-depth before wrapping: Unicode NFC normalization, zero-width and bidirectional character stripping, model special-token removal, and token-budget truncation.

### Path Traversal

All file operations resolve paths through a safe-path check that rejects `..` components, leading `/`, and any resolved path outside the vault root. System directories (underscore-prefixed like `_wisdom/`, `_profile/`) are additionally blocked from write operations. The same guards apply to chat tools, slash commands, and the retrieval client.

### Web Fetch

- Domain allowlist (`_config/allowlist.md`) gates both `/search-web` results and `/fetch` targets
- Only `http` and `https` schemes are accepted
- Redirects are not followed (prevents SSRF via open redirects to internal hosts)
- Content-Type is validated and response size is capped

### Access Control

- The daemon listens on a Unix socket (filesystem permissions)
- Discord access is restricted to an explicit user ID allowlist (`PAL_DISCORD_ALLOWED_USERS`)
- Each Discord user gets an isolated daemon connection

### Git Safety Net

Every vault write is automatically git-committed, so any unwanted change can be reviewed and reverted.

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
