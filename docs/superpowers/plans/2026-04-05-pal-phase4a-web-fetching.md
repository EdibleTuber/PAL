# PAL Phase 4a: Strict /note + Web Fetching Infrastructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/note` refuse to hallucinate, and add the infrastructure to search the web (SearxNG) and fetch URLs into a quarantined `raw/web/` directory with strict size/type/domain controls. No model sees web content in this phase — that's Phase 4b.

**Architecture:** Three new modules: `websearch.py` (SearxNG HTTP client), `fetcher.py` (URL fetch + content extraction + validation), `allowlist.py` (reads `_config/allowlist.md`, validates URLs). New slash commands `/search-web`, `/fetch`. `/note` gets strict-mode framing that returns `UNKNOWN: <reason>` when the model lacks confident knowledge. All fetches require user confirmation via new protocol message types.

**Tech Stack:** Python 3.12, httpx (existing), trafilatura (new — content extraction), existing PAL modules (daemon, protocol, config, wiki)

---

## File Structure

```
pal/
├── websearch.py        # SearxNG client — POST query, return result list
├── fetcher.py          # URL fetcher — HEAD check + GET + trafilatura extraction
├── allowlist.py        # Read _config/allowlist.md, validate URLs against it
├── daemon.py           # Modified — add /search-web, /fetch, strict /note
├── config.py           # Modified — add SearxNG URL, fetch limits
├── protocol.py         # Modified — add ConfirmMessage, ConfirmResponseMessage
├── client.py           # Modified — handle confirm/confirm_response flow
├── cli.py              # Modified — render confirmation prompts
tests/
├── test_websearch.py
├── test_fetcher.py
├── test_allowlist.py
├── test_web_commands.py     # Integration: /search-web and /fetch
├── test_strict_note.py      # /note returns UNKNOWN when model says so
├── conftest.py              # Modified — add SearxNG + fetcher mock endpoints
```

---

### Task 1: Add Web Config Settings

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_config.py`:
```python
def test_default_config_has_web_settings():
    cfg = Config()
    assert cfg.searxng_url == "http://192.168.1.14:8080"
    assert cfg.fetch_max_bytes == 2_000_000
    assert cfg.fetch_timeout == 30


def test_load_config_web_settings_from_env(monkeypatch):
    monkeypatch.setenv("PAL_SEARXNG_URL", "http://localhost:9999")
    monkeypatch.setenv("PAL_FETCH_MAX_BYTES", "500000")
    monkeypatch.setenv("PAL_FETCH_TIMEOUT", "10")
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH", "PAL_COLLECTION_ID", "PAL_USERNAME"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.searxng_url == "http://localhost:9999"
    assert cfg.fetch_max_bytes == 500_000
    assert cfg.fetch_timeout == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_default_config_has_web_settings -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'searxng_url'`

- [ ] **Step 3: Add fields and env loading**

In `pal/config.py`, add to the `Config` dataclass (after `username`):
```python
    searxng_url: str = "http://192.168.1.14:8080"
    fetch_max_bytes: int = 2_000_000
    fetch_timeout: int = 30
```

And in `load_config()`, add before `return Config(**kwargs)`:
```python
    if url := os.environ.get("PAL_SEARXNG_URL"):
        kwargs["searxng_url"] = url
    if mb := os.environ.get("PAL_FETCH_MAX_BYTES"):
        kwargs["fetch_max_bytes"] = int(mb)
    if ft := os.environ.get("PAL_FETCH_TIMEOUT"):
        kwargs["fetch_timeout"] = int(ft)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat: add SearxNG URL and fetch limit config settings"
```

---

### Task 2: AllowlistManager

**Files:**
- Create: `pal/allowlist.py`
- Create: `tests/test_allowlist.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_allowlist.py`:
```python
"""Tests for AllowlistManager — domain allowlist validation."""
from pathlib import Path

import pytest

from pal.allowlist import AllowlistManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def allowlist(vault) -> AllowlistManager:
    return AllowlistManager(vault)


def test_empty_allowlist_denies_all(allowlist):
    assert allowlist.is_allowed("https://example.com/page") is False


def test_seed_creates_file_with_starter_domains(allowlist, vault):
    allowlist.seed()
    path = vault / "_config" / "allowlist.md"
    assert path.exists()
    content = path.read_text()
    assert "wikipedia.org" in content
    assert "arxiv.org" in content


def test_after_seed_allows_listed_domains(allowlist):
    allowlist.seed()
    assert allowlist.is_allowed("https://en.wikipedia.org/wiki/Python") is True
    assert allowlist.is_allowed("https://arxiv.org/abs/1706.03762") is True


def test_denies_unlisted_domains(allowlist):
    allowlist.seed()
    assert allowlist.is_allowed("https://evil.example.com/") is False


def test_wildcard_subdomain_match(allowlist, vault):
    (vault / "_config").mkdir()
    (vault / "_config" / "allowlist.md").write_text(
        "# Allowlist\n\n- *.readthedocs.io\n"
    )
    assert allowlist.is_allowed("https://flask.readthedocs.io/en/stable/") is True
    assert allowlist.is_allowed("https://readthedocs.io/") is True
    assert allowlist.is_allowed("https://readthedocs.example.com/") is False


def test_exact_domain_match_no_subdomain(allowlist, vault):
    (vault / "_config").mkdir()
    (vault / "_config" / "allowlist.md").write_text(
        "# Allowlist\n\n- github.com\n"
    )
    assert allowlist.is_allowed("https://github.com/user/repo") is True
    assert allowlist.is_allowed("https://raw.github.com/x") is False


def test_list_returns_all_patterns(allowlist):
    allowlist.seed()
    patterns = allowlist.list()
    assert "wikipedia.org" in patterns
    assert "arxiv.org" in patterns
    assert len(patterns) > 5


def test_rejects_non_http_schemes(allowlist):
    allowlist.seed()
    assert allowlist.is_allowed("ftp://wikipedia.org/file") is False
    assert allowlist.is_allowed("file:///etc/passwd") is False
    assert allowlist.is_allowed("javascript:alert(1)") is False


def test_rejects_malformed_urls(allowlist):
    allowlist.seed()
    assert allowlist.is_allowed("not a url") is False
    assert allowlist.is_allowed("") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_allowlist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.allowlist'`

- [ ] **Step 3: Implement allowlist.py**

`pal/allowlist.py`:
```python
"""AllowlistManager — domain allowlist for web fetching.

The allowlist lives at _config/allowlist.md in the vault. It's a markdown
bullet list of domain patterns (one per line). Supports:
    exact.domain        — matches exactly that host
    *.subdomain.tld     — matches any subdomain + the bare domain

Only http:// and https:// URLs are ever allowed.
"""
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


STARTER_ALLOWLIST = """# Web Allowlist

Domains PAL is allowed to fetch. Edit this file to add or remove entries.
Patterns: `example.com` matches the exact host. `*.example.com` matches any subdomain AND the bare domain.

## Reference
- wikipedia.org
- wiktionary.org
- plato.stanford.edu

## Academic
- arxiv.org
- semanticscholar.org
- pubmed.ncbi.nlm.nih.gov

## Technical
- *.readthedocs.io
- docs.python.org
- developer.mozilla.org

## Code
- github.com
- stackoverflow.com
- stackexchange.com

## Standards
- rfc-editor.org
- w3.org
- ietf.org
"""


class AllowlistManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @property
    def allowlist_path(self) -> Path:
        return self.vault_path / "_config" / "allowlist.md"

    def seed(self) -> None:
        """Write the starter allowlist if no allowlist file exists yet."""
        if self.allowlist_path.exists():
            return
        self.allowlist_path.parent.mkdir(parents=True, exist_ok=True)
        self.allowlist_path.write_text(STARTER_ALLOWLIST)
        logger.info("Seeded allowlist at %s", self.allowlist_path)

    def list(self) -> list[str]:
        """Parse the allowlist file, return all domain patterns."""
        if not self.allowlist_path.exists():
            return []
        patterns = []
        for line in self.allowlist_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                patterns.append(stripped[2:].strip())
        return patterns

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL is http/https AND its host matches a pattern."""
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        for pattern in self.list():
            if pattern.startswith("*."):
                bare = pattern[2:]
                if host == bare or host.endswith("." + bare):
                    return True
            else:
                if host == pattern:
                    return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_allowlist.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/allowlist.py tests/test_allowlist.py
git commit -m "feat: AllowlistManager — domain allowlist for web fetching"
```

---

### Task 3: WebSearchClient — SearxNG

**Files:**
- Create: `pal/websearch.py`
- Create: `tests/test_websearch.py`
- Modify: `tests/conftest.py` (add SearxNG mock)

- [ ] **Step 1: Add SearxNG mock to conftest.py**

In `tests/conftest.py`, find the `mock_app = Starlette(routes=[...])` definition. Add a new mock function before it:
```python
async def mock_searxng_search(request: Request):
    """Mock SearxNG /search endpoint."""
    query = request.query_params.get("q", "")
    return JSONResponse({
        "query": query,
        "results": [
            {
                "url": f"https://wikipedia.org/wiki/{query.replace(' ', '_')}",
                "title": f"{query} - Wikipedia",
                "content": f"Wikipedia snippet about {query}.",
            },
            {
                "url": f"https://arxiv.org/abs/2301.00001",
                "title": f"Research on {query}",
                "content": f"Abstract mentioning {query}.",
            },
            {
                "url": "https://evil.example.com/junk",
                "title": "Not allowlisted",
                "content": "Should be filtered by allowlist.",
            },
        ],
    })
```

Then add the route to `mock_app`:
```python
mock_app = Starlette(routes=[
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
    Route("/collections/{collection_id}/search", mock_collection_search, methods=["POST"]),
    Route("/collections/{collection_id}/docs/{doc_id:path}", mock_collection_get_doc, methods=["GET"]),
    Route("/search", mock_searxng_search, methods=["GET"]),
])
```

- [ ] **Step 2: Write the failing tests**

`tests/test_websearch.py`:
```python
"""Tests for WebSearchClient — SearxNG HTTP client."""
import pytest

from pal.websearch import WebSearchClient, SearchResult


@pytest.mark.asyncio
async def test_search_returns_results(mock_inference_server):
    client = WebSearchClient(base_url=mock_inference_server)
    results = await client.search("quantum computing")
    assert len(results) == 3
    assert isinstance(results[0], SearchResult)
    assert "wikipedia.org" in results[0].url
    assert "quantum computing" in results[0].title.lower() or "quantum" in results[0].title.lower()


@pytest.mark.asyncio
async def test_search_includes_snippets(mock_inference_server):
    client = WebSearchClient(base_url=mock_inference_server)
    results = await client.search("python")
    assert all(r.snippet for r in results)


@pytest.mark.asyncio
async def test_search_result_has_all_fields(mock_inference_server):
    client = WebSearchClient(base_url=mock_inference_server)
    results = await client.search("test")
    r = results[0]
    assert r.url
    assert r.title
    assert r.snippet
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_websearch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.websearch'`

- [ ] **Step 4: Implement websearch.py**

`pal/websearch.py`:
```python
"""WebSearchClient — thin HTTP client for SearxNG.

SearxNG is a self-hosted meta-search engine. This client hits its
JSON search endpoint and returns results as structured SearchResult objects.
"""
from dataclasses import dataclass

import httpx


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


class WebSearchClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def search(self, query: str) -> list[SearchResult]:
        """Query SearxNG, return raw results (no allowlist filtering applied)."""
        resp = await self._client.get(
            f"{self.base_url}/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("results", []):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
            ))
        return results
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_websearch.py -v`
Expected: all 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add pal/websearch.py tests/test_websearch.py tests/conftest.py
git commit -m "feat: WebSearchClient — SearxNG JSON query client"
```

---

### Task 4: URL Fetcher + Content Extraction

**Files:**
- Create: `pal/fetcher.py`
- Create: `tests/test_fetcher.py`
- Modify: `tests/conftest.py` (add mock fetch endpoint)
- Modify: `pyproject.toml` (add trafilatura)

- [ ] **Step 1: Add trafilatura to dependencies**

In `pyproject.toml`, change:
```toml
dependencies = [
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
]
```
To:
```toml
dependencies = [
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "trafilatura>=1.12.0",
]
```

Run: `pip install -e ".[dev]"`

- [ ] **Step 2: Add mock content endpoints to conftest.py**

In `tests/conftest.py`, add these mock functions before `mock_app`:
```python
async def mock_page_html(request: Request):
    """Return a basic HTML page for fetcher tests."""
    return Response(
        "<html><head><title>Test Page</title></head>"
        "<body><article><h1>Test Article</h1>"
        "<p>This is the main content. Extract me.</p>"
        "<nav>Nav junk</nav><footer>Footer junk</footer>"
        "</article></body></html>",
        media_type="text/html",
    )


async def mock_page_too_large(request: Request):
    """Return a response with a too-large Content-Length."""
    return Response(
        "tiny body",
        media_type="text/html",
        headers={"Content-Length": "999999999"},
    )


async def mock_page_binary(request: Request):
    """Return a binary content-type."""
    return Response(
        b"\x00\x01\x02\x03",
        media_type="application/octet-stream",
    )


async def mock_page_404(request: Request):
    return Response("not found", status_code=404)
```

Import `Response` at the top of conftest:
```python
from starlette.responses import StreamingResponse, JSONResponse, Response
```

Add routes to `mock_app`:
```python
    Route("/page.html", mock_page_html, methods=["GET"]),
    Route("/too-large", mock_page_too_large, methods=["GET"]),
    Route("/binary", mock_page_binary, methods=["GET"]),
    Route("/missing", mock_page_404, methods=["GET"]),
```

- [ ] **Step 3: Write the failing tests**

`tests/test_fetcher.py`:
```python
"""Tests for URLFetcher — fetch + extract + validate."""
import pytest

from pal.fetcher import URLFetcher, FetchResult, FetchError


@pytest.mark.asyncio
async def test_fetch_extracts_main_content(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    result = await fetcher.fetch(f"{mock_inference_server}/page.html")
    assert isinstance(result, FetchResult)
    assert "main content" in result.text.lower() or "extract me" in result.text.lower()
    assert "nav junk" not in result.text.lower()
    assert result.url == f"{mock_inference_server}/page.html"
    assert result.title == "Test Page"


@pytest.mark.asyncio
async def test_fetch_rejects_too_large(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    with pytest.raises(FetchError, match="too large"):
        await fetcher.fetch(f"{mock_inference_server}/too-large")


@pytest.mark.asyncio
async def test_fetch_rejects_binary(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    with pytest.raises(FetchError, match="content type"):
        await fetcher.fetch(f"{mock_inference_server}/binary")


@pytest.mark.asyncio
async def test_fetch_404_raises(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    with pytest.raises(FetchError, match="404"):
        await fetcher.fetch(f"{mock_inference_server}/missing")


@pytest.mark.asyncio
async def test_fetch_result_has_hash(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    result = await fetcher.fetch(f"{mock_inference_server}/page.html")
    assert result.content_hash
    assert len(result.content_hash) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_fetch_respects_max_bytes_during_download(mock_inference_server):
    """If response streams more than max_bytes, fetch should abort."""
    # Use a 1-byte limit against a normal page — should fail
    fetcher = URLFetcher(max_bytes=1, timeout=10)
    with pytest.raises(FetchError, match="too large"):
        await fetcher.fetch(f"{mock_inference_server}/page.html")
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.fetcher'`

- [ ] **Step 5: Implement fetcher.py**

`pal/fetcher.py`:
```python
"""URLFetcher — fetch URLs, extract main content, enforce limits.

Performs:
  1. Streamed download with byte cap (rejects oversized responses mid-stream)
  2. Content-Type validation (only text/html, text/plain, application/xhtml+xml)
  3. Content-Length header check where available
  4. trafilatura extraction (strips nav/footer/ads, keeps article body)
  5. SHA-256 hashing for provenance
"""
from dataclasses import dataclass
import hashlib

import httpx
import trafilatura


ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/xhtml+xml",
)


class FetchError(Exception):
    """Raised when a URL can't be fetched for safety/correctness reasons."""


@dataclass
class FetchResult:
    url: str
    title: str
    text: str
    content_hash: str
    byte_size: int


class URLFetcher:
    def __init__(self, max_bytes: int, timeout: int) -> None:
        self.max_bytes = max_bytes
        self.timeout = timeout

    async def fetch(self, url: str) -> FetchResult:
        """Fetch a URL and return extracted main content."""
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} for {url}")

                ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ct and not any(ct.startswith(t) for t in ALLOWED_CONTENT_TYPES):
                    raise FetchError(f"rejected content type: {ct}")

                cl = resp.headers.get("content-length")
                if cl and int(cl) > self.max_bytes:
                    raise FetchError(f"response too large (Content-Length: {cl})")

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise FetchError(f"response too large (exceeded {self.max_bytes} bytes)")
                    chunks.append(chunk)
                raw = b"".join(chunks)

        try:
            html = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise FetchError(f"decode error: {exc}")

        text = trafilatura.extract(html) or ""
        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata and metadata.title else ""

        content_hash = hashlib.sha256(raw).hexdigest()

        return FetchResult(
            url=url,
            title=title,
            text=text,
            content_hash=content_hash,
            byte_size=len(raw),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: all 6 tests PASS

- [ ] **Step 7: Commit**

```bash
git add pal/fetcher.py tests/test_fetcher.py tests/conftest.py pyproject.toml
git commit -m "feat: URLFetcher — fetch, extract, and validate URL content"
```

---

### Task 5: Strict /note — UNKNOWN Response

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_strict_note.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_strict_note.py`:
```python
"""Tests for strict /note mode — model must refuse to guess."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def strict_daemon(socket_path, mock_inference_server, tmp_path, monkeypatch):
    """Daemon with a mock inference that returns UNKNOWN for specific prompts."""
    # This test uses the default echo mock; we craft a topic that will
    # trigger the UNKNOWN check via the response content.
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_note_refuses_when_model_returns_unknown(strict_daemon, socket_path, monkeypatch):
    """If the model returns 'UNKNOWN: ...', /note does not save anything."""
    daemon, vault = strict_daemon

    # Patch the inference client to return UNKNOWN
    async def fake_complete(messages):
        return "UNKNOWN: No reliable information on this topic."
    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("note", "obscure topic")
    assert "UNKNOWN" in resp.text or "unknown" in resp.text.lower()
    # Vault should not contain the article
    assert not (vault / "obscure-topic.md").exists()

    await client.close()


@pytest.mark.asyncio
async def test_note_saves_when_model_responds_normally(strict_daemon, socket_path, monkeypatch):
    """If the model returns actual content, /note saves normally."""
    daemon, vault = strict_daemon

    async def fake_complete(messages):
        return "# Known Topic\n\nThis is confident content about a known topic."
    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("note", "known topic")
    assert "Created article:" in resp.text
    assert (vault / "known-topic.md").exists()

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_strict_note.py -v`
Expected: FAIL — `test_note_refuses_when_model_returns_unknown` fails because current `/note` saves whatever the model returns

- [ ] **Step 3: Update `_handle_note` in daemon.py**

In `pal/daemon.py`, find the `_handle_note` method. Replace the existing `prompt` string:
```python
        prompt = (
            f"Write a concise wiki article about: {topic}\n\n"
            "Format: Start with a markdown heading, then clear explanatory paragraphs. "
            "Be informative and concise."
        )
```

With this stricter version:
```python
        prompt = (
            f"Write a concise wiki article about: {topic}\n\n"
            "RULES:\n"
            "- If you do not have confident, factual knowledge of this topic, "
            "respond with exactly: UNKNOWN: <one-sentence reason>\n"
            "- Do NOT guess, speculate, or fabricate facts.\n"
            "- Do NOT use placeholder text like [insert details here].\n"
            "- Only write the article if you can ground every claim in what you actually know.\n\n"
            "Format: Start with a markdown heading, then clear explanatory paragraphs. "
            "Be informative and concise."
        )
```

Then, after the existing `body = await self.inference.complete(messages)` call and BEFORE `slug = topic.lower()...`, add:
```python
        if body.strip().startswith("UNKNOWN:"):
            resp = ResponseMessage(
                text=(
                    f"{body.strip()}\n\n"
                    "No article saved. Try `/search-web <topic>` to find sources, "
                    "then `/fetch` and `/compile` to build from them."
                ),
                command="note",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_strict_note.py -v`
Expected: both tests PASS

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass. NOTE: Existing `/note` tests in test_wiki_commands.py use the echo-mock inference server — its response won't start with UNKNOWN, so those tests should still work.

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_strict_note.py
git commit -m "feat: strict /note — model returns UNKNOWN when uncertain, nothing saved"
```

---

### Task 6: Wire /search-web into Daemon

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_web_commands.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_web_commands.py`:
```python
"""Integration tests for /search-web and /fetch commands."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def web_daemon(socket_path, mock_inference_server, tmp_path):
    """Daemon using mock_inference_server as SearxNG endpoint too."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,  # same server serves both in tests
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_search_web_returns_allowed_results(web_daemon, socket_path):
    """/search-web returns only results from allowlisted domains."""
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("search-web", "python")
    # Mock returns wikipedia, arxiv, and evil.example.com
    # Only the first two should appear (allowlist filters evil.example.com)
    assert "wikipedia.org" in resp.text
    assert "arxiv.org" in resp.text
    assert "evil.example.com" not in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_search_web_empty_query(web_daemon, socket_path):
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("search-web", "")

    await client.close()


@pytest.mark.asyncio
async def test_search_web_seeds_allowlist_on_first_use(web_daemon, socket_path):
    daemon, vault = web_daemon
    assert not (vault / "_config" / "allowlist.md").exists()

    client = PalClient(socket_path)
    await client.connect()
    await client.command("search-web", "test")
    await client.close()

    assert (vault / "_config" / "allowlist.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_commands.py -v`
Expected: FAIL — daemon doesn't handle /search-web yet

- [ ] **Step 3: Wire allowlist + websearch into daemon**

In `pal/daemon.py`:

1. Add imports:
```python
from pal.allowlist import AllowlistManager
from pal.websearch import WebSearchClient
```

2. In `Daemon.__init__`, after the existing prompt_builder setup, add:
```python
        self.allowlist = AllowlistManager(config.vault_path)
        self.allowlist.seed()
        self.websearch = WebSearchClient(
            base_url=config.searxng_url,
            timeout=config.fetch_timeout,
        )
```

3. In `_handle_command`, add a new elif before the final `else:`:
```python
        elif msg.name == "search-web":
            await self._handle_search_web(msg.args, writer)
```

4. Add this new method to the `Daemon` class:
```python
    async def _handle_search_web(self, query: str, writer: asyncio.StreamWriter) -> None:
        """Handle /search-web <query> — SearxNG query, return allowlisted results."""
        query = query.strip()
        if not query:
            error = ErrorMessage(error="Usage: /search-web <query>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            results = await self.websearch.search(query)
        except Exception as exc:
            logger.exception("Web search failed: %s", exc)
            error = ErrorMessage(error=f"Web search failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Filter through allowlist
        allowed = [r for r in results if self.allowlist.is_allowed(r.url)]

        if not allowed:
            resp = ResponseMessage(
                text=(
                    "No allowlisted results. "
                    "Edit `_config/allowlist.md` in the vault to add domains."
                ),
                command="search-web",
            )
        else:
            lines = [f"Found {len(allowed)} allowed result(s) (of {len(results)} total):\n"]
            for i, r in enumerate(allowed, 1):
                lines.append(f"{i}. **{r.title}**")
                lines.append(f"   {r.url}")
                if r.snippet:
                    lines.append(f"   {r.snippet}")
            lines.append("\nUse `/fetch <url>` to save a page to the vault.")
            resp = ResponseMessage(text="\n".join(lines), command="search-web")

        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_commands.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_web_commands.py
git commit -m "feat: /search-web — SearxNG query filtered through allowlist"
```

---

### Task 7: Wire /fetch into Daemon

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_web_commands.py` (append tests)

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_web_commands.py`:
```python
@pytest.mark.asyncio
async def test_fetch_saves_to_raw_web(web_daemon, socket_path):
    """/fetch <url> pulls content, validates against allowlist, saves to raw/web/."""
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    # We need an allowlisted URL — temporarily add the mock server to allowlist
    # by writing to _config/allowlist.md manually
    (vault / "_config").mkdir(parents=True, exist_ok=True)
    import re
    host = re.sub(r"^https?://", "", daemon.websearch.base_url).split(":")[0]
    (vault / "_config" / "allowlist.md").write_text(
        f"# Allowlist\n\n- {host}\n"
    )

    resp = await client.command("fetch", f"{daemon.websearch.base_url}/page.html")
    assert "Saved" in resp.text or "saved" in resp.text.lower()

    # File should exist in raw/web/
    raw_web = vault / "raw" / "web"
    assert raw_web.exists()
    files = list(raw_web.glob("*.md"))
    assert len(files) >= 1
    content = files[0].read_text()
    assert "main content" in content.lower() or "extract me" in content.lower()

    await client.close()


@pytest.mark.asyncio
async def test_fetch_rejects_non_allowlisted_url(web_daemon, socket_path):
    """/fetch refuses to fetch URLs not on the allowlist."""
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not on allowlist"):
        await client.command("fetch", "https://evil.example.com/page")

    await client.close()


@pytest.mark.asyncio
async def test_fetch_empty_url(web_daemon, socket_path):
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("fetch", "")

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_commands.py::test_fetch_saves_to_raw_web -v`
Expected: FAIL — daemon doesn't handle /fetch yet

- [ ] **Step 3: Wire fetcher into daemon**

In `pal/daemon.py`:

1. Add import:
```python
from pal.fetcher import URLFetcher, FetchError
```

2. In `Daemon.__init__`, after `self.websearch = ...`, add:
```python
        self.fetcher = URLFetcher(
            max_bytes=config.fetch_max_bytes,
            timeout=config.fetch_timeout,
        )
```

3. In `_handle_command`, add a new elif before the final `else:`:
```python
        elif msg.name == "fetch":
            await self._handle_fetch(msg.args, writer)
```

4. Add this new method to the `Daemon` class:
```python
    async def _handle_fetch(self, url: str, writer: asyncio.StreamWriter) -> None:
        """Handle /fetch <url> — download URL content into raw/web/ (quarantine)."""
        url = url.strip()
        if not url:
            error = ErrorMessage(error="Usage: /fetch <url>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not self.allowlist.is_allowed(url):
            error = ErrorMessage(
                error=(
                    f"URL not on allowlist: {url}\n"
                    "Add its domain to _config/allowlist.md in the vault, then retry."
                )
            )
            writer.write(encode_message(error))
            await writer.drain()
            return

        try:
            result = await self.fetcher.fetch(url)
        except FetchError as exc:
            error = ErrorMessage(error=f"Fetch failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return
        except Exception as exc:
            logger.exception("Fetch failed: %s", exc)
            error = ErrorMessage(error=f"Fetch failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Build a slug from the URL path + hash suffix for uniqueness
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path_part = (parsed.path or "/").strip("/").replace("/", "-") or parsed.hostname or "page"
        path_part = "".join(c for c in path_part if c.isalnum() or c in "-_")[:40]
        slug = f"{path_part}-{result.content_hash[:8]}"
        filename = f"{slug}.md"

        # Write to raw/web/ with frontmatter containing provenance
        raw_dir = self.config.vault_path / "raw" / "web"
        raw_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        from pal.frontmatter import serialize_frontmatter
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {
            "source_url": url,
            "title": result.title or slug,
            "fetched_at": fetched_at,
            "content_hash": result.content_hash,
            "byte_size": result.byte_size,
            "status": "raw",
        }
        content = serialize_frontmatter(meta, result.text + "\n")
        (raw_dir / filename).write_text(content)
        logger.info("Fetched %s to %s", url, filename)

        resp = ResponseMessage(
            text=(
                f"Saved to raw/web/{filename}\n"
                f"Title: {result.title or '(no title)'}\n"
                f"Size: {result.byte_size} bytes\n\n"
                "Review it in Obsidian before running /summarize (Phase 4b)."
            ),
            command="fetch",
        )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_commands.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_web_commands.py
git commit -m "feat: /fetch — download URL to raw/web/ with allowlist validation"
```

---

### Task 8: Final Verification + CLI Help

**Files:**
- Modify: `pal/cli.py`

- [ ] **Step 1: Update CLI help text**

In `pal/cli.py`, find:
```python
    console.print("[dim]Commands: /note /read /search /get /profile /wisdom /lint /status /quit[/dim]\n")
```

Replace with:
```python
    console.print("[dim]Commands: /note /read /search /get /search-web /fetch /profile /wisdom /lint /status /quit[/dim]\n")
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add pal/cli.py
git commit -m "docs: update CLI help with /search-web and /fetch commands"
```
