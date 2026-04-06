# Chat Tool Use — Read-Only Vault Tools for Conversational Chat

**Date:** 2026-04-06
**Status:** Draft

## Overview

PAL's chat mode currently has no access to the vault. The LLM receives a system prompt (identity + profile + wisdom) and conversation history, but cannot read files, list directories, or search content. When a user says "look at the claude subagent file," PAL has to say "I can't access your files."

This feature adds read-only vault tools to chat via OpenAI-compatible function calling. The LLM can autonomously decide to read files, list directories, and search the vault mid-conversation, making PAL a librarian that can actually look things up.

## Goals

- Let the LLM read vault content during chat without requiring explicit slash commands
- Support multi-step tool use (e.g., list a directory, then read two files, then compare them)
- Show brief progress indicators so the user knows what's happening
- Keep it read-only — write operations stay in dedicated slash commands for now

## Non-Goals

- Write tools (create/edit/delete files) — future phase
- Replacing existing slash commands — `/read`, `/search` etc. remain as explicit alternatives
- RAG or automatic context injection — the LLM decides when to look things up
- Tool use in slash command handlers — only chat mode gets tools

## Architecture

```
User chat message
    |
    v
Daemon._handle_chat()
    |
    v
First call: InferenceClient.stream(messages, tools)
    |
    +-- LLM returns text --> stream tokens to user, done
    |
    +-- LLM returns tool_calls --> enter tool-use loop:
            |
            v
        ToolExecutor.run(call) + send ToolProgressMessage to CLI
            |
            v
        Append tool_call + result to messages
            |
            v
        InferenceClient.complete(messages, tools)  [non-streaming]
            |
            +-- tool_calls --> loop (max 10 iterations)
            +-- text --> send as ResponseMessage, done
```

### Key Design Decisions

**Streaming on first call, non-streaming in loop.** The first LLM call uses `stream()` so normal chat turns (no tools needed) stream as today. If tool calls are detected, subsequent loop iterations use `complete()` (non-streaming) since the user sees progress indicators instead of tokens. The final text response from the loop is sent as a single `ResponseMessage`.

**Tools always available.** Tool definitions are included on every chat turn. The token overhead is small and the LLM is good at knowing when not to use them. Client-side intent detection would be fragile.

**10-call cap.** The tool-use loop is capped at 10 iterations per turn to prevent runaway loops. If the cap is hit, whatever partial result exists is returned with a note.

**Read-only.** All tools are read-only. This keeps the blast radius small and avoids conflicts with existing write-oriented slash commands.

## Tool Definitions

### read_file

Read a file from the vault.

- **Parameters:**
  - `path` (string, required) — path relative to vault root
- **Returns:** file contents (frontmatter + body)
- **Limits:** truncated at ~8000 tokens to protect context window
- **Security:** rejects paths that escape vault root (path traversal)

### list_directory

List files and subdirectories in a vault directory.

- **Parameters:**
  - `path` (string, optional, default `""`) — path relative to vault root
- **Returns:** list of entries with type indicators (file/directory)
- **Filtering:** excludes `_*` system directories to match existing conventions

### search_content

Keyword search across vault files.

- **Parameters:**
  - `query` (string, required) — search term or pattern
- **Returns:** matching filenames with line snippets, capped at 20 results

### search_vault

Semantic search via the existing retrieval API.

- **Parameters:**
  - `query` (string, required) — natural language query
- **Returns:** ranked results from the retrieval server (same as `/search` command)

## Component Changes

### New: pal/tools.py

Contains:

- `TOOL_DEFINITIONS` — list of tool schemas in OpenAI function-calling format, ready to include in API payloads
- `ToolExecutor` class:
  - Initialized with vault path and retrieval client
  - `async run(name: str, arguments: dict) -> str` — dispatches to the appropriate handler, returns result as a string
  - Individual handlers: `_read_file()`, `_list_directory()`, `_search_content()`, `_search_vault()`
  - Path validation: resolve against vault root, reject if result is outside vault

### Modified: pal/inference.py

`InferenceClient` changes:

- `complete(messages, tools=None) -> CompletionResult` — accepts optional tools list, returns a dataclass:
  ```python
  @dataclass
  class CompletionResult:
      type: str          # "text" or "tool_calls"
      content: str | None
      tool_calls: list[ToolCall] | None

  @dataclass
  class ToolCall:
      id: str
      name: str
      arguments: dict
  ```
  Parses `choices[0].message` to determine if the response contains `tool_calls` or `content`.

- `stream(messages, tools=None)` — accepts optional tools list. Returns an async generator that yields either `str` tokens (text response) or a `list[ToolCall]` (tool call response). Detection: if any streaming chunk contains `delta.tool_calls` instead of `delta.content`, accumulate all tool call deltas (name, arguments fragments) until `[DONE]`, then yield the assembled `list[ToolCall]` as a single item. The caller checks the type of the first yielded value to determine the response type. This means tool-call responses are buffered (not truly streamed), which is fine since we switch to the non-streaming loop anyway.

### Modified: pal/protocol.py

New message type:

- `ToolProgressMessage(tool: str, arguments: dict)` — sent daemon-to-CLI during tool execution. Serialized with `type: "tool_progress"`.

### Modified: pal/daemon.py

`_handle_chat()` rewritten:

1. Build messages with system prompt + history
2. Call `stream(messages, tools=TOOL_DEFINITIONS)`
3. If streaming text: yield tokens to CLI as today, done
4. If tool calls returned: enter loop
   a. For each tool call: send `ToolProgressMessage`, execute via `ToolExecutor`
   b. Append assistant message (with tool_calls) and tool result messages to message list
   c. Call `complete(messages, tools=TOOL_DEFINITIONS)`
   d. If more tool calls: continue (up to 10 iterations)
   e. If text: send as `ResponseMessage`, done
5. On loop cap: send partial result + warning

### Modified: pal/cli.py

Chat receive loop handles `ToolProgressMessage`:

- Render as dim/grey text via Rich: `[reading Research/claude-subagent.md...]`
- Continue waiting for more messages

Formatting logic per tool:
- `read_file` -> `[reading {path}...]`
- `list_directory` -> `[listing {path or "vault"}...]`
- `search_content` -> `[searching for "{query}"...]`
- `search_vault` -> `[searching vault for "{query}"...]`

## Message Flow Example

User: "Hey, the claude subagent file, can you look at the one you made again? Compare it to the original in raw."

```
1. CLI sends ChatMessage("Hey, the claude subagent file...")
2. Daemon calls stream(messages, tools)
3. LLM returns tool_call: list_directory(path="")
4. Daemon sends ToolProgressMessage(tool="list_directory", arguments={path: ""})
   CLI shows: [listing vault...]
5. Daemon executes, appends result to messages
6. Daemon calls complete(messages, tools)
7. LLM returns tool_call: read_file(path="Research/claude-subagent.md")
8. Daemon sends ToolProgressMessage(...)
   CLI shows: [reading Research/claude-subagent.md...]
9. Daemon executes, appends result
10. Daemon calls complete(messages, tools)
11. LLM returns tool_call: read_file(path="raw/web/claude-subagent-abc123.md")
12. Daemon sends ToolProgressMessage(...)
    CLI shows: [reading raw/web/claude-subagent-abc123.md...]
13. Daemon executes, appends result
14. Daemon calls complete(messages, tools)
15. LLM returns text: "Looking at both files, the compiled version is..."
16. Daemon sends ResponseMessage with comparison
```

## Security

- **Path traversal:** `ToolExecutor` resolves all paths against vault root and rejects any that escape it (e.g., `../../etc/passwd`)
- **Read-only:** no tool can modify vault state
- **Token budget:** file reads are truncated to prevent context window exhaustion
- **Loop cap:** 10 iterations prevents runaway tool use

## Testing

- Unit tests for `ToolExecutor`: each tool handler, path traversal rejection, truncation
- Unit tests for `InferenceClient`: parsing text vs tool_call responses
- Integration test for the tool-use loop: mock inference returning a sequence of tool calls then text
- CLI rendering test: `ToolProgressMessage` formatting
