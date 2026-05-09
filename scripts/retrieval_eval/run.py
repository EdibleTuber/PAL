"""Retrieval-quality experiment runner (5a from the research-assistant assessment).

Reads queries from queries.txt, calls search_vault via agent_core's RetrievalClient,
writes results to results.md with checkboxes for human labeling.

Run on the inference server itself (`/mnt/secondary/PAL` on agenthost).
The manager is localhost-only as of 2026-05-09 — not reachable from the dev
machine. Override the default with PAL_INFERENCE_URL if needed.
"""

import asyncio
import os
from pathlib import Path

from agent_core.retrieval import RetrievalClient

HERE = Path(__file__).resolve().parent
QUERIES_FILE = HERE / "queries.txt"
RESULTS_FILE = HERE / "results.md"

BASE_URL = os.environ.get("PAL_INFERENCE_URL", "http://localhost:11434")
COLLECTION_ID = os.environ.get("PAL_COLLECTION_ID", "vault")
TOP_K = 5
SNIPPET_LEN = 240


def _truncate(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _format_query_block(query: str, results: list[dict]) -> str:
    lines = [f"## Query: {query}", ""]
    if not results:
        lines.append("_(no results)_")
        lines.append("")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        rid = r.get("id", "?")
        name = r.get("name", "")
        score = r.get("score", 0.0)
        summary = _truncate(r.get("summary", ""), SNIPPET_LEN)
        title_str = f"`{rid}`" + (f" — *{name}*" if name and name != rid else "")
        lines.append(f"- [ ] **#{i}** {title_str} (score={score:.3f})")
        if summary:
            lines.append(f"  > {summary}")
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    queries = [
        line.strip()
        for line in QUERIES_FILE.read_text().splitlines()
        if line.strip()
    ]
    if not queries:
        raise SystemExit(f"No queries found in {QUERIES_FILE}")

    print(f"Running {len(queries)} queries against {BASE_URL}/collections/{COLLECTION_ID}/search ...")
    client = RetrievalClient(base_url=BASE_URL, collection_id=COLLECTION_ID)

    blocks = [
        "# Retrieval Evaluation Results",
        "",
        "Tick the checkbox next to each result you'd consider relevant to the query.",
        "Binary judgment: relevant or not. After labeling, run `python scripts/retrieval_eval/score.py`.",
        "",
        f"Top-K: {TOP_K}, Collection: `{COLLECTION_ID}`, Inference: `{BASE_URL}`",
        "",
        "---",
        "",
    ]

    for i, query in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {query[:70]}")
        try:
            results = await client.search(query, limit=TOP_K)
        except Exception as exc:
            blocks.append(f"## Query: {query}\n\n_(error: {exc})_\n")
            continue
        blocks.append(_format_query_block(query, results))

    RESULTS_FILE.write_text("\n".join(blocks))
    print(f"\nWrote {RESULTS_FILE}")
    print("Open it, tick the relevant results, then run score.py.")


if __name__ == "__main__":
    asyncio.run(main())
