# Chat Write Tools — edit_file and create_file for Conversational Chat

**Date:** 2026-04-06
**Status:** Draft

## Overview

PAL's chat mode has read-only vault tools (read_file, list_directory, search_content, search_vault). Users can ask PAL to look at files and compare them, but when PAL suggests changes — like restructuring an article — it can't actually do it. The user has to manually make the edits.

This feature adds two write tools to chat: `edit_file` (rewrite an existing file) and `create_file` (create a new file). Both git-commit after writing, providing a safety net for undo.

## Goals

- Let the LLM edit and create vault files during chat without slash commands
- Git-commit every write so changes are reversible
- Maintain existing safety boundaries (vault-only, no system dirs, path traversal protection)

## Non-Goals

- Delete operations — too destructive for autonomous use
- Rename/move — complex with Obsidian wiki links, better as a dedicated command
- Writing to system directories (`_*`) — those stay managed by slash commands
- User confirmation flow — git history provides the safety net instead

## Tool Definitions

### edit_file

Rewrite the body of an existing vault file. Preserves existing frontmatter (title, tags, created timestamp). Updates the `updated` timestamp.

- **Parameters:**
  - `path` (string, required) — file path relative to vault root
  - `content` (string, required) — new body content (not including frontmatter)
- **Returns:** confirmation message with path
- **Rejects:**
  - File does not exist (returns error suggesting `create_file`)
  - Path is in a system directory (`_*`)
  - Path escapes vault root
- **Side effects:** git commit with descriptive message

### create_file

Create a new file in the vault with proper frontmatter.

- **Parameters:**
  - `path` (string, required) — file path relative to vault root
  - `title` (string, required) — article title for frontmatter
  - `content` (string, required) — body content
  - `tags` (list of strings, optional) — tags for frontmatter
- **Returns:** confirmation message with path
- **Rejects:**
  - File already exists (returns error suggesting `edit_file`)
  - Path is in a system directory (`_*`)
  - Path escapes vault root
- **Side effects:** creates parent directories as needed, git commit with descriptive message

## Component Changes

### Modified: pal/tools.py

- Add `edit_file` and `create_file` to `TOOL_DEFINITIONS`
- Add `wiki: WikiManager` parameter to `ToolExecutor.__init__`
- Add `_edit_file()` handler:
  - Validate path (safe, exists, not system dir)
  - Read existing frontmatter via `WikiManager.read_article()`
  - Write updated body via `WikiManager.write_article()` preserving title, tags, created timestamp
  - Git commit via `WikiManager.git_commit()`
- Add `_create_file()` handler:
  - Validate path (safe, does not exist, not system dir)
  - Write via `WikiManager.write_article()` with provided title, content, tags
  - Git commit via `WikiManager.git_commit()`
- Update `run()` dispatch to include new tools
- Update module docstring to reflect read-write access

### Modified: pal/daemon.py

- Pass `wiki=self.wiki` when constructing `ToolExecutor`

### Modified: tests/test_tools.py

New tests:
- `test_edit_file` — edits existing file, verifies content changed and frontmatter preserved
- `test_edit_file_not_found` — rejects editing nonexistent file
- `test_edit_file_system_dir` — rejects editing in `_wisdom/` etc.
- `test_edit_file_path_traversal` — rejects `../../etc/passwd`
- `test_edit_file_git_commits` — verifies git commit happens after edit
- `test_create_file` — creates new file with frontmatter
- `test_create_file_already_exists` — rejects creating over existing file
- `test_create_file_system_dir` — rejects creating in `_*` dirs
- `test_create_file_creates_parent_dirs` — creates intermediate directories
- `test_create_file_git_commits` — verifies git commit happens after create

## Security

- Same path traversal protection as read tools (`_resolve_safe()`)
- Same system directory exclusion (`_*` prefix check)
- Vault-only — all paths resolved against vault root
- Git commit on every write — full history for review and revert
- No delete capability

## Example Flow

User: "Restructure that agent harness file with better headings"

```
1. LLM calls read_file(path="agent-harness-like-openclaw-or-pi-mono.md")
2. LLM reads content, plans restructured version
3. LLM calls edit_file(path="agent-harness-like-openclaw-or-pi-mono.md", content="# Agent Harness\n\n## Core Definition\n...")
4. CLI shows: [editing agent-harness-like-openclaw-or-pi-mono.md...]
5. ToolExecutor preserves frontmatter, writes new body, git commits
6. LLM responds: "Done — I've restructured the file with clear sections..."
```
