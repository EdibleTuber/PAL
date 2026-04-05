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
import re

import httpx
import trafilatura


_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)


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

        # Prefer the HTML <title> tag; fall back to trafilatura metadata (h1, og:title, etc.)
        m = _TITLE_RE.search(html)
        if m:
            title = m.group(1).strip()
        else:
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
