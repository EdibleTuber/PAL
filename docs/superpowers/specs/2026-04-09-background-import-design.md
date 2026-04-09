# Background Import Jobs

**Date:** 2026-04-09
**Status:** Draft

## Overview

Three changes to PAL's daemon:

1. **Background import** - `/import` validates, converts, and chunks synchronously, then runs chunk processing as a background task. Returns immediately so the daemon stays responsive to other connections.
2. **Job tracking and notification** - `/jobs` command for status checks, plus passive notification on next interaction when a job completes.
3. **Vault write lock** - `asyncio.Lock` protecting vault write operations from concurrent access.

## Goals

- Running `/import` on a large document no longer blocks the daemon
- Users can chat or use commands while an import runs
- Discord and CLI sessions don't time out waiting for imports
- Concurrent vault writes are serialized to prevent race conditions

## Non-Goals

- Job persistence across daemon restarts (jobs are in-memory only)
- Cancelling running jobs
- Multiple simultaneous imports (one at a time is fine for now)
- Background processing for `/compile` or other commands (just `/import`)

## Background Import

### Phase 1: Synchronous (in the handler)

The existing path validation, MarkItDown conversion, and chunking are fast and stay synchronous. The handler:

1. Validates path (unchanged)
2. Converts via MarkItDown in executor (unchanged)
3. Chunks the markdown (unchanged)
4. Creates a job in the tracker with status "running"
5. Spawns `asyncio.create_task()` for the chunk processing loop
6. Returns immediately with a response like "Import started: 15 chunks from book.pdf"

### Phase 2: Background task

The chunk loop runs as a detached async task:

1. For each chunk:
   - Cleanup LLM call
   - Categorize LLM call
   - Acquire vault lock, save article, release lock
   - Update job progress (completed count)
2. After all chunks: acquire vault lock, rebuild index, git commit, archive source, release lock
3. Mark job as completed (or failed if all chunks failed)

### Changes to `_handle_import`

The current method (~200 lines) splits into:

- `_handle_import()` - validation, conversion, chunking, task spawn, immediate response
- `_run_import_job()` - the chunk processing loop, called via `asyncio.create_task()`

## Job Tracker

### New module: `pal/jobs.py`

```python
@dataclass
class Job:
    id: str            # UUID
    type: str          # "import"
    source: str        # "raw/book.pdf"
    status: str        # "running", "completed", "failed"
    total: int         # total chunks
    completed: int     # chunks done so far
    articles: list[str]  # saved article paths
    errors: list[str]    # failed/skipped chunk titles
    started_at: str    # ISO timestamp
    finished_at: str | None
    notified: bool     # has the user been told about completion?
```

**Class: `JobTracker`**

- `create(type, source, total) -> Job` - creates and stores a new job
- `get(job_id) -> Job | None` - retrieve by ID
- `list_all() -> list[Job]` - all jobs (recent first)
- `pop_unnotified() -> list[Job]` - returns completed/failed jobs not yet notified, marks them as notified

The daemon holds one `JobTracker` instance (`self.jobs`). Jobs are stored in a dict keyed by ID. No persistence - jobs are lost on daemon restart.

## `/jobs` Command

Lists all tracked jobs with status:

```
Import: book.pdf - running (8/15 chunks)
Import: report.pdf - completed (3 articles) - 10 min ago
```

Added to command dispatch and help text.

## Passive Notification

At the start of `_handle_chat` and `_handle_command`, call `self.jobs.pop_unnotified()`. For each completed job, send a `ResponseMessage` before handling the actual request:

```
[Import complete: book.pdf - 12 articles created]
```

For failed jobs:

```
[Import failed: book.pdf - all chunks failed]
```

This ensures the user sees results regardless of which client (CLI or Discord) they use next.

## Vault Write Lock

### New attribute: `self._vault_lock = asyncio.Lock()`

Created in `Daemon.__init__`. Used as an async context manager around any vault mutation.

### Protected operations

All vault write paths acquire the lock:

- `_handle_note`: write article + rebuild index + git commit
- `_handle_compile`: write article + rebuild index + git commit + archive
- `_run_import_job`: per-chunk save (lock per chunk, not for the whole loop), final rebuild + commit + archive
- `_handle_learn`: write learning file
- `_handle_promote`: move learning to wisdom
- Tool executor `_edit_file` and `_create_file`: vault file writes

The lock is async, so read-only operations (chat, search, read, list) are unaffected. Write operations queue up naturally.

### Granularity for imports

The background import acquires the lock per-chunk (for the save) and once at the end (for rebuild + commit + archive). It does NOT hold the lock for the entire import. This means other write operations can interleave between chunks.

## Error Handling

- If the background task raises an unhandled exception, a `try/except` wrapper marks the job as "failed" with the error message
- Individual chunk cleanup failures fall back to raw content (unchanged)
- If the daemon shuts down mid-import, the task is cancelled and the job is lost. Partially-written articles remain in the vault (git-tracked, so recoverable).

## Testing

- `pal/jobs.py`: unit tests for JobTracker (create, update, list, pop_unnotified)
- Vault lock: test that concurrent writes are serialized
- `/import` background: integration test that `/import` returns immediately and articles appear after the background task runs
- `/jobs` command: test that it lists running and completed jobs
- Notification: test that completed jobs are reported on next chat/command
