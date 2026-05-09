"""Score the labeled retrieval-eval results.

Reads results.md, parses checkbox state per result, and computes:
  - Top-1 hit rate: fraction of queries whose top result is relevant
  - Top-5 hit rate: fraction of queries with at least one relevant result in top 5
  - MRR: mean reciprocal rank (only over queries with at least one hit)
  - Per-query breakdown
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS_FILE = HERE / "results.md"

QUERY_RE = re.compile(r"^## Query:\s*(.+?)\s*$")
RESULT_RE = re.compile(r"^- \[(.)\] \*\*#(\d+)\*\*")


def parse(text: str) -> list[tuple[str, list[bool]]]:
    """Return list of (query, [is_relevant_per_position]) tuples."""
    out: list[tuple[str, list[bool]]] = []
    current_query: str | None = None
    current_hits: list[bool] = []
    for line in text.splitlines():
        m = QUERY_RE.match(line)
        if m:
            if current_query is not None:
                out.append((current_query, current_hits))
            current_query = m.group(1)
            current_hits = []
            continue
        m = RESULT_RE.match(line)
        if m:
            mark = m.group(1)
            current_hits.append(mark.lower() == "x")
    if current_query is not None:
        out.append((current_query, current_hits))
    return out


def main() -> None:
    text = RESULTS_FILE.read_text()
    parsed = parse(text)
    if not parsed:
        raise SystemExit(f"No queries parsed from {RESULTS_FILE}")

    n = len(parsed)
    top1 = 0
    top5 = 0
    rr_sum = 0.0
    rr_count = 0

    for query, hits in parsed:
        if hits and hits[0]:
            top1 += 1
        if any(hits):
            top5 += 1
            first_hit = hits.index(True) + 1
            rr_sum += 1.0 / first_hit
            rr_count += 1

    print(f"Queries: {n}")
    print(f"Top-1 hit rate:  {top1}/{n} = {top1 / n:.2%}")
    print(f"Top-5 hit rate:  {top5}/{n} = {top5 / n:.2%}")
    if rr_count:
        print(f"MRR (over hits): {rr_sum / rr_count:.3f}")
    else:
        print("MRR: undefined (no hits)")
    print()
    print("Per-query breakdown (* = relevant at position):")
    for query, hits in parsed:
        marks = "".join("*" if h else "." for h in hits) or "(none)"
        print(f"  [{marks:<5}] {query}")


if __name__ == "__main__":
    main()
