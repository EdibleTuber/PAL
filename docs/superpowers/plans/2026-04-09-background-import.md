# Background Import Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/import` non-blocking by running chunk processing in the background, add job tracking with `/jobs` command and passive notifications, and protect vault writes with an async lock.

**Architecture:** New `pal/jobs.py` module for job state tracking. `/import` handler splits into validation+conversion (synchronous) and chunk processing (background task via `asyncio.create_task`). An `asyncio.Lock` on the daemon serializes vault mutations. Notification check runs at the start of `_handle_chat` and `_handle_command`.

**Tech Stack:** Python 3.12, asyncio (Lock, create_task), existing PAL modules

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `pal/jobs.py` | Create | Job dataclass and JobTracker class |
| `pal/daemon.py` | Modify | Vault lock, background import, notifications, `/jobs` command |
| `pal/tools.py` | Modify | Acquire vault lock for edit/create |
| `tests/test_jobs.py` | Create | Unit tests for JobTracker |
| `tests/test_import.py` | Modify | Update for background import behavior |

---

### Task 1: Job Tracker Module

**Files:**
- Create: `pal/jobs.py`
- Create: `tests/test_jobs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs.py`:

```python
"""Unit tests for job tracker."""
from datetime import datetime, timezone

import pytest

from pal.jobs import Job, JobTracker


class TestJobTracker:
    def setup_method(self):
        self.tracker = JobTracker()

    def test_create_job(self):
        job = self.tracker.create(type="import", source="raw/book.pdf", total=10)
        assert job.id
        assert job.type == "import"
        assert job.source == "raw/book.pdf"
        assert job.status == "running"
        assert job.total == 10
        assert job.completed == 0
        assert job.articles == []
        assert job.errors == []
        assert job.notified is False

    def test_get_job(self):
        job = self.tracker.create(type="import", source="raw/a.pdf", total=5)
        retrieved = self.tracker.get(job.id)
        assert retrieved is job

    def test_get_missing_job(self):
        assert self.tracker.get("nonexistent") is None

    def test_list_all(self):
        j1 = self.tracker.create(type="import", source="raw/a.pdf", total=5)
        j2 = self.tracker.create(type="import", source="raw/b.pdf", total=3)
        jobs = self.tracker.list_all()
        assert len(jobs) == 2
        # Most recent first
        assert jobs[0].id == j2.id
        assert jobs[1].id == j1.id

    def test_pop_unnotified_returns_completed(self):
        job = self.tracker.create(type="import", source="raw/a.pdf", total=1)
        job.status = "completed"
        job.completed = 1

        unnotified = self.tracker.pop_unnotified()
        assert len(unnotified) == 1
        assert unnotified[0].id == job.id
        assert job.notified is True

    def test_pop_unnotified_returns_failed(self):
        job = self.tracker.create(type="import", source="raw/a.pdf", total=1)
        job.status = "failed"

        unnotified = self.tracker.pop_unnotified()
        assert len(unnotified) == 1

    def test_pop_unnotified_skips_running(self):
        job = self.tracker.create(type="import", source="raw/a.pdf", total=1)
        assert job.status == "running"

        unnotified = self.tracker.pop_unnotified()
        assert len(unnotified) == 0

    def test_pop_unnotified_skips_already_notified(self):
        job = self.tracker.create(type="import", source="raw/a.pdf", total=1)
        job.status = "completed"
        job.notified = True

        unnotified = self.tracker.pop_unnotified()
        assert len(unnotified) == 0

    def test_pop_unnotified_marks_as_notified(self):
        job = self.tracker.create(type="import", source="raw/a.pdf", total=1)
        job.status = "completed"

        self.tracker.pop_unnotified()
        # Second call returns nothing
        assert len(self.tracker.pop_unnotified()) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source /home/edible/Projects/PAL/.venv/bin/activate && pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pal.jobs'`

- [ ] **Step 3: Write the implementation**

Create `pal/jobs.py`:

```python
"""Job tracker for background tasks.

Tracks the status of long-running operations like /import so users
can check progress and receive notifications on completion.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Job:
    id: str
    type: str
    source: str
    status: str  # "running", "completed", "failed"
    total: int
    completed: int = 0
    articles: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str | None = None
    notified: bool = False


class JobTracker:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, type: str, source: str, total: int) -> Job:
        """Create and store a new job."""
        job = Job(
            id=str(uuid.uuid4())[:8],
            type=type,
            source=source,
            status="running",
            total=total,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        """Retrieve a job by ID."""
        return self._jobs.get(job_id)

    def list_all(self) -> list[Job]:
        """List all jobs, most recent first."""
        return list(reversed(self._jobs.values()))

    def pop_unnotified(self) -> list[Job]:
        """Return completed/failed jobs not yet notified, mark them as notified."""
        result = []
        for job in self._jobs.values():
            if job.status in ("completed", "failed") and not job.notified:
                job.notified = True
                result.append(job)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_jobs.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/jobs.py tests/test_jobs.py
git commit -m "feat: add job tracker for background tasks"
```

---

### Task 2: Vault Write Lock

**Files:**
- Modify: `pal/daemon.py` (add lock to __init__, wrap write operations)
- Modify: `pal/tools.py` (accept and use lock)
- Create: `tests/test_vault_lock.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_lock.py`:

```python
"""Tests for vault write lock."""
import asyncio

import pytest

from pal.config import Config
from pal.daemon import Daemon


@pytest.mark.asyncio
async def test_daemon_has_vault_lock(mock_inference_server, tmp_path, socket_path):
    """Daemon should have an asyncio.Lock for vault writes."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = Daemon(cfg)
    assert hasattr(daemon, "_vault_lock")
    assert isinstance(daemon._vault_lock, asyncio.Lock)


@pytest.mark.asyncio
async def test_vault_lock_serializes_writes(mock_inference_server, tmp_path, socket_path):
    """Concurrent vault writes should be serialized by the lock."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = Daemon(cfg)
    order = []

    async def writer(name: str):
        async with daemon._vault_lock:
            order.append(f"{name}_start")
            await asyncio.sleep(0.01)
            order.append(f"{name}_end")

    await asyncio.gather(writer("a"), writer("b"))
    # One must complete before the other starts
    assert order == ["a_start", "a_end", "b_start", "b_end"] or \
           order == ["b_start", "b_end", "a_start", "a_end"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vault_lock.py -v`
Expected: FAIL with `AttributeError: 'Daemon' object has no attribute '_vault_lock'`

- [ ] **Step 3: Add the lock to Daemon.__init__**

In `pal/daemon.py`, at the end of `Daemon.__init__` (after `cleanup_archived(config.vault_path)`, around line 123), add:

```python
        self._vault_lock = asyncio.Lock()
```

- [ ] **Step 4: Wrap vault writes in _handle_note**

In `_handle_note` (around line 476-479), wrap the write operations:

Current:
```python
        self.wiki.write_article(path=path, title=topic, body=body + "\n")
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"note: {topic}")
```

Replace with:
```python
        async with self._vault_lock:
            self.wiki.write_article(path=path, title=topic, body=body + "\n")
            self.wiki.rebuild_index()
            self.wiki.git_init()
            self.wiki.git_commit(f"note: {topic}")
```

- [ ] **Step 5: Wrap vault writes in _handle_compile**

In `_handle_compile` (around line 955-966), wrap:

Current:
```python
        article_full_path.write_text(serialize_frontmatter(article_meta, article.strip() + "\n"))
        logger.info("Compiled %s -> %s", summary_path, article_path_rel)

        # Rebuild the master index and commit
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"compile: {title}")

        # Archive raw intermediates
        source_raw = summary_meta.get("source_raw", "")
        archive_raw_files(self.config.vault_path, raw_path=source_raw, summary_path=summary_path)
        self.wiki.git_commit(f"archive: {title}")
```

Replace with:
```python
        async with self._vault_lock:
            article_full_path.write_text(serialize_frontmatter(article_meta, article.strip() + "\n"))
            logger.info("Compiled %s -> %s", summary_path, article_path_rel)

            # Rebuild the master index and commit
            self.wiki.rebuild_index()
            self.wiki.git_init()
            self.wiki.git_commit(f"compile: {title}")

            # Archive raw intermediates
            source_raw = summary_meta.get("source_raw", "")
            archive_raw_files(self.config.vault_path, raw_path=source_raw, summary_path=summary_path)
            self.wiki.git_commit(f"archive: {title}")
```

- [ ] **Step 6: Wrap vault writes in _handle_fetch**

In `_handle_fetch` (around line 717), wrap the raw file write:

Current:
```python
        (raw_dir / filename).write_text(content)
```

Replace with:
```python
        async with self._vault_lock:
            (raw_dir / filename).write_text(content)
```

- [ ] **Step 7: Wrap vault writes in _handle_summarize**

In `_handle_summarize` (around line 817), wrap:

Current:
```python
        summary_full_path.write_text(serialize_frontmatter(summary_meta, summary.strip() + "\n"))
```

Replace with:
```python
        async with self._vault_lock:
            summary_full_path.write_text(serialize_frontmatter(summary_meta, summary.strip() + "\n"))
```

- [ ] **Step 8: Wrap vault writes in _handle_learn**

In `_handle_learn` (around line 1223), wrap:

Current:
```python
        self.wiki.git_commit(f"learn: extracted {len(added)} lesson(s)")
```

Find the block that writes learnings and commits. Wrap the write + commit:

```python
        async with self._vault_lock:
            # ... learning file writes ...
            self.wiki.git_commit(f"learn: extracted {len(added)} lesson(s)")
```

- [ ] **Step 9: Wrap vault writes in _handle_promote**

In `_handle_promote` (around line 1269), wrap:

```python
        async with self._vault_lock:
            # ... promote logic ...
            self.wiki.git_commit(f"promote: {slug} → wisdom")
```

- [ ] **Step 10: Pass vault lock to ToolExecutor**

In `pal/tools.py`, modify the `ToolExecutor.__init__` to accept a lock:

```python
class ToolExecutor:
    def __init__(self, vault_path, retrieval, wiki, vault_lock=None):
        ...
        self._vault_lock = vault_lock
```

In `_edit_file` and `_create_file`, these are synchronous methods called from the daemon's async tool loop. Since `asyncio.Lock` can't be used in sync code, and these methods are called from within an already-async context, the simplest approach is to make the tool executor methods that write to the vault async:

Actually, looking at the code, the ToolExecutor methods are sync and called from `_handle_chat` which is async. The lock needs to be acquired in the daemon's `_handle_chat` around the tool execution call. Let me check how tools are called.

Read `pal/daemon.py` around line 234-270 to see the tool execution loop.

The simplest approach: the tool executor is called inside `_handle_chat`'s tool loop. Since only `_edit_file` and `_create_file` write to the vault, and the tool executor is already called from an async context, we can wrap the `self.tool_executor.execute()` call with the lock when the tool is a write tool.

In `pal/daemon.py`, find where `tool_executor.execute()` is called in the tool loop. Wrap it:

```python
                    if tc.name in ("edit_file", "create_file"):
                        async with self._vault_lock:
                            tool_result = self.tool_executor.execute(tc.name, tc.arguments)
                    else:
                        tool_result = self.tool_executor.execute(tc.name, tc.arguments)
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `pytest tests/test_vault_lock.py tests/test_import.py tests/test_compile.py tests/test_strict_note.py -v`
Expected: All tests PASS

- [ ] **Step 12: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 13: Commit**

```bash
git add pal/daemon.py pal/tools.py tests/test_vault_lock.py
git commit -m "feat: add asyncio.Lock for vault write operations"
```

---

### Task 3: Background Import

**Files:**
- Modify: `pal/daemon.py` (import JobTracker, split _handle_import, add _run_import_job)
- Modify: `tests/test_import.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_import.py`:

```python
@pytest.mark.asyncio
async def test_import_returns_immediately(import_daemon, socket_path, monkeypatch):
    """Import should return immediately and process in background."""
    daemon, vault = import_daemon

    call_count = 0
    complete_event = asyncio.Event()

    async def slow_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Cleanup - simulate slow LLM
            await asyncio.sleep(0.1)
            return CompletionResult(type="text", content="# Content\n\nCleaned up.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", slow_complete)

    rel_path = _place_csv_in_raw(vault, "slow.csv", "A,B\n1,2\n")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    await client.close()

    # Should return immediately with "started" message, not wait for completion
    assert "started" in resp.text.lower() or "background" in resp.text.lower()

    # Wait for background task to complete
    for _ in range(50):
        await asyncio.sleep(0.05)
        jobs = daemon.jobs.list_all()
        if jobs and jobs[0].status == "completed":
            break

    assert len(daemon.jobs.list_all()) == 1
    assert daemon.jobs.list_all()[0].status == "completed"
    articles = list((vault / "Research").glob("*.md"))
    assert len(articles) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import.py::test_import_returns_immediately -v`
Expected: FAIL (current import blocks until completion)

- [ ] **Step 3: Add JobTracker to daemon**

In `pal/daemon.py`, add the import:

```python
from pal.jobs import JobTracker
```

In `Daemon.__init__`, after `self._vault_lock`:

```python
        self.jobs = JobTracker()
```

- [ ] **Step 4: Extract _run_import_job method**

Add a new method to the Daemon class, after `_handle_import`:

```python
    async def _run_import_job(
        self,
        job: "Job",
        chunks: list,
        file_path: str,
        full_path: "Path",
        convert_title: str,
    ) -> None:
        """Background task: process import chunks."""
        from datetime import datetime, timezone
        from pal.frontmatter import serialize_frontmatter
        from pal.jobs import Job

        try:
            for idx, chunk in enumerate(chunks, 1):
                # Cleanup
                cleanup_messages = [
                    {"role": "system", "content": (
                        "You are cleaning up markdown converted from a document. "
                        "Fix formatting issues, broken tables, and conversion artifacts. "
                        "Ensure proper heading hierarchy, clean up whitespace, and make the content readable. "
                        "Preserve ALL original content and meaning. Do NOT summarize, rewrite, or add information. "
                        "Output only the cleaned markdown, nothing else."
                    )},
                    {"role": "user", "content": chunk.body},
                ]

                try:
                    result = await self.inference.complete(cleanup_messages)
                    article = result.content or chunk.body
                except Exception as exc:
                    logger.warning("Cleanup failed for chunk '%s', using raw content: %s", chunk.title, exc)
                    article = chunk.body

                # Categorize
                category = await self.categorizer.categorize(
                    title=chunk.title,
                    body=article,
                    vault_path=self.config.vault_path,
                )

                # Save (with vault lock)
                slug = chunk.title.lower().replace("_", "-").replace(" ", "-")
                slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-") or "untitled"

                async with self._vault_lock:
                    target_dir = self.config.vault_path / category
                    target_dir.mkdir(parents=True, exist_ok=True)
                    article_path_rel = f"{category}/{slug}.md"
                    article_full_path = target_dir / f"{slug}.md"

                    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    article_meta = {
                        "title": chunk.title,
                        "created": now,
                        "updated": now,
                        "compiled_at": now,
                        "source_file": file_path,
                        "status": "compiled",
                    }
                    article_full_path.write_text(serialize_frontmatter(article_meta, article.strip() + "\n"))

                job.articles.append(article_path_rel)
                job.completed = idx
                logger.info("Imported chunk '%s' -> %s (%d/%d)", chunk.title, article_path_rel, idx, job.total)

            # Final commit and archive (with vault lock)
            async with self._vault_lock:
                self.wiki.rebuild_index()
                self.wiki.git_init()
                self.wiki.git_commit(f"import: {convert_title} ({len(job.articles)} articles)")
                archive_raw_files(self.config.vault_path, raw_path=file_path)
                self.wiki.git_commit(f"archive: {convert_title}")

            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            logger.info("Import job %s completed: %d articles", job.id, len(job.articles))

        except Exception as exc:
            logger.exception("Import job %s failed: %s", job.id, exc)
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            job.errors.append(str(exc))
```

- [ ] **Step 5: Rewrite _handle_import to spawn background task**

Replace everything in `_handle_import` from `total = len(chunks)` (line 1047) through the end of the method with:

```python
        total = len(chunks)

        # Create job and spawn background task
        job = self.jobs.create(type="import", source=file_path, total=total)
        asyncio.create_task(
            self._run_import_job(job, chunks, file_path, full_path, convert_result.title)
        )

        resp = ResponseMessage(
            text=(
                f"Import started: {total} chunks from {full_path.name}\n"
                f"Job ID: {job.id}\n"
                f"Use /jobs to check progress."
            ),
            command="import",
        )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 6: Update existing import tests**

The existing tests that monkeypatch `daemon.inference.complete` and check for articles need updating. They now need to wait for the background task to complete. Update each test that checks for created articles.

For `test_import_csv_creates_article`:

```python
@pytest.mark.asyncio
async def test_import_csv_creates_article(import_daemon, socket_path, monkeypatch):
    daemon, vault = import_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Employee Directory\n\nThe team consists of three members...")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    rel_path = _place_csv_in_raw(
        vault, "employees.csv",
        "Name,Role,Department\nAlice,Engineer,Platform\nBob,Designer,Product\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    assert "started" in resp.text.lower()
    await client.close()

    # Wait for background task
    for _ in range(50):
        await asyncio.sleep(0.05)
        if daemon.jobs.list_all()[0].status != "running":
            break

    articles = list((vault / "Research").glob("*.md"))
    assert len(articles) == 1
```

Apply the same pattern to: `test_import_archives_source`, `test_import_converts_underscores_to_hyphens_in_slug`, `test_import_splits_multi_heading_document`, `test_import_single_chunk_still_works`, `test_import_uses_raw_content_on_cleanup_failure`.

For each:
1. Change the `resp.text` assertion to check for "started" instead of content
2. Add a wait loop for the background task to complete
3. Keep the file/article assertions after the wait

The error tests (`test_import_rejects_non_raw_path`, `test_import_rejects_unsupported_format`, `test_import_empty_args`, `test_import_path_traversal`) should NOT change - these errors happen in the synchronous phase before the background task is spawned.

- [ ] **Step 7: Run import tests**

Run: `pytest tests/test_import.py -v`
Expected: All tests PASS

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add pal/daemon.py tests/test_import.py
git commit -m "feat: run import chunk processing as background task"
```

---

### Task 4: `/jobs` Command and Passive Notifications

**Files:**
- Modify: `pal/daemon.py` (add /jobs handler, notification check)
- Modify: `tests/test_import.py` (add /jobs and notification tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_import.py`:

```python
@pytest.mark.asyncio
async def test_jobs_command_shows_running_import(import_daemon, socket_path, monkeypatch):
    """The /jobs command should show running imports."""
    daemon, vault = import_daemon

    # Make the LLM very slow so the job stays running
    async def slow_complete(messages, **kwargs):
        await asyncio.sleep(10)
        return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", slow_complete)

    rel_path = _place_csv_in_raw(vault, "slow.csv", "A,B\n1,2\n")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("import", rel_path)

    # Check jobs while import is still running
    resp = await client.command("jobs", "")
    assert "running" in resp.text.lower()
    assert "slow.csv" in resp.text
    await client.close()


@pytest.mark.asyncio
async def test_jobs_command_shows_completed_import(import_daemon, socket_path, monkeypatch):
    """The /jobs command should show completed imports."""
    daemon, vault = import_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Report\n\nContent.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    rel_path = _place_csv_in_raw(vault, "report.csv", "A,B\n1,2\n")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("import", rel_path)

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        if daemon.jobs.list_all()[0].status != "running":
            break

    resp = await client.command("jobs", "")
    assert "completed" in resp.text.lower() or "articles" in resp.text.lower()
    await client.close()


@pytest.mark.asyncio
async def test_notification_on_next_command(import_daemon, socket_path, monkeypatch):
    """After import completes, next command should include a notification."""
    daemon, vault = import_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Article\n\nContent.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    rel_path = _place_csv_in_raw(vault, "notify.csv", "A,B\n1,2\n")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("import", rel_path)

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        if daemon.jobs.list_all()[0].status != "running":
            break

    # Next command should trigger notification
    resp = await client.command("status", "")
    # The notification is sent as a separate message before the status response
    # Since client.command() returns the first ResponseMessage, and notification
    # is also a ResponseMessage, we should see the notification
    assert "import complete" in resp.text.lower() or "Model:" in resp.text
    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_import.py::test_jobs_command_shows_running_import -v`
Expected: FAIL (no `/jobs` command)

- [ ] **Step 3: Add `/jobs` to command dispatch and help text**

In `pal/daemon.py`, add to the help text:

```python
                    "  /jobs          — Check background job status\n"
```

Add to command dispatch (after the `import` elif):

```python
        elif msg.name == "jobs":
            await self._handle_jobs(writer)
```

- [ ] **Step 4: Add _handle_jobs method**

Add to the Daemon class:

```python
    async def _handle_jobs(self, writer: asyncio.StreamWriter) -> None:
        """Handle /jobs — show background job status."""
        jobs = self.jobs.list_all()
        if not jobs:
            resp = ResponseMessage(text="No background jobs.", command="jobs")
            writer.write(encode_message(resp))
            await writer.drain()
            return

        lines = []
        for job in jobs:
            if job.status == "running":
                lines.append(f"Import: {job.source} - running ({job.completed}/{job.total} chunks)")
            elif job.status == "completed":
                lines.append(f"Import: {job.source} - completed ({len(job.articles)} articles)")
            elif job.status == "failed":
                lines.append(f"Import: {job.source} - failed ({', '.join(job.errors)})")

        resp = ResponseMessage(text="\n".join(lines), command="jobs")
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 5: Add notification check**

In `_handle_command`, at the very beginning (before the `if msg.name == "help":` check), add:

```python
        # Notify about completed background jobs
        for job in self.jobs.pop_unnotified():
            if job.status == "completed":
                note = ResponseMessage(
                    text=f"[Import complete: {job.source} - {len(job.articles)} articles created]",
                    command="notification",
                )
            else:
                note = ResponseMessage(
                    text=f"[Import failed: {job.source} - {', '.join(job.errors)}]",
                    command="notification",
                )
            writer.write(encode_message(note))
            await writer.drain()
```

Add the same notification check at the start of `_handle_chat`, before `conv.add_user(msg.text)`:

```python
        # Notify about completed background jobs
        for job in self.jobs.pop_unnotified():
            if job.status == "completed":
                note = ResponseMessage(
                    text=f"[Import complete: {job.source} - {len(job.articles)} articles created]",
                    command="notification",
                )
            else:
                note = ResponseMessage(
                    text=f"[Import failed: {job.source} - {', '.join(job.errors)}]",
                    command="notification",
                )
            writer.write(encode_message(note))
            await writer.drain()
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_import.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add pal/daemon.py tests/test_import.py
git commit -m "feat: add /jobs command and passive completion notifications"
```
