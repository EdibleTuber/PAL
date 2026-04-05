"""HTTP client for the inference server's collection search endpoints.

Thin wrapper over:
  POST /collections/{collection_id}/search
  GET  /collections/{collection_id}/docs/{doc_id}

The inference server handles embedding generation and vector search.
PAL's retrieval layer is used when the wiki outgrows index-file navigation
or for fuzzy/semantic queries.
"""
import httpx


class RetrievalClient:
    def __init__(self, base_url: str, collection_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection_id = collection_id
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """Search the collection for documents matching the query.

        Returns a list of result dicts with keys: id, name, collection,
        summary, tags, score. Results are sorted by score (descending).
        """
        payload: dict = {"query": query, "limit": limit}
        if tags:
            payload["tags"] = tags
        resp = await self._client.post(
            f"{self.base_url}/collections/{self.collection_id}/search",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])

    async def get_document(self, doc_id: str) -> dict:
        """Fetch the full content of a document by its ID.

        Returns a dict with keys: id, name, collection, summary, content, metadata.
        Raises FileNotFoundError if the document doesn't exist.
        """
        resp = await self._client.get(
            f"{self.base_url}/collections/{self.collection_id}/docs/{doc_id}"
        )
        if resp.status_code == 404:
            raise FileNotFoundError(f"Document not found: {doc_id}")
        resp.raise_for_status()
        return resp.json()
