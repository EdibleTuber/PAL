#!/usr/bin/env python3
"""Derive per-key p6 summaries from kept occurrence records.

The model's own summary objects ({"summary_key": ...}) are sometimes
malformed: in the p6a_code run every one was emitted as
`{"key": "summary_key": ...}` and rejected as not_json. This script
reconstructs the summaries deterministically from the kept occurrence
records, in the summary schema of prompts/p6_config_surface.md.

Usage: derive_summaries.py <out-dir> <p6-name> [more p6 names...]
Writes <name>.summaries.jsonl next to <name>.jsonl.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def derive(path: Path) -> list:
    occ = defaultdict(list)
    for line in path.open():
        d = json.loads(line)
        if "role" not in d:  # model-emitted summary object; skip
            continue
        occ[d["key"]].append(d)
    out = []
    for key in sorted(occ):
        rows = occ[key]
        values = sorted({r["value"] for r in rows
                         if r.get("value") not in (None, "unknown")})
        out.append({
            "summary_key": key,
            "read_by_code": any(r["role"] == "read_by_code" for r in rows),
            "defined_in": sorted({r["file"] for r in rows if r["role"] == "defined"}),
            "referenced_in": sorted({r["file"] for r in rows if r["role"] == "referenced"}),
            "defaults_differ": values if len(values) > 1 else "no",
            "occurrences": len(rows),
        })
    return out


def main() -> None:
    out_dir = Path(sys.argv[1])
    for name in sys.argv[2:]:
        src = out_dir / f"{name}.jsonl"
        rows = derive(src)
        dst = out_dir / f"{name}.summaries.jsonl"
        with dst.open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"{name}: {len(rows)} keys -> {dst.name}")


if __name__ == "__main__":
    main()
