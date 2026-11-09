#!/usr/bin/env python3
"""Recover records the model emitted as field-per-line JSON fragments.

For some passes (mostly p5_claims on larger files, plus two p4 passes) the
model spread each record object over multiple lines:

    {
    "line": 0106,
    "quote": "...",
    ...
    }

verify() is line-based, so every fragment was rejected as not_json and the
pass ended up with zero kept rows. The fragments are still in
<name>.rejects.jsonl in stream order, so this script joins them, parses the
object sequence (tolerating prose, array brackets, and the model's
zero-padded "line" numbers), and re-runs each object through the same
verification gate as the live pass (run_inventory.check_object). Recovered
rows are appended to <name>.jsonl; the rejects file is left untouched as
raw evidence.

Usage: recover_fragments.py <out-dir> [pass-name ...]
With no pass names, every pass that has not_json rejects is processed.
Rows already present in <name>.jsonl are not appended again, so re-runs
are safe.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_inventory import REPO, check_object, code_files, numbered

ZERO_PAD = re.compile(r'(":\s*)0+(\d)')


def source_for(name: str) -> Path | None:
    """Map a per-file pass name (e.g. p5_claims__pal_tools_compile) to its file."""
    for f in code_files():
        esc = f.relative_to(REPO).with_suffix("").as_posix().replace("/", "_")
        if name.endswith("__" + esc):
            return f
    return None


def parse_objects(text: str) -> list:
    """Parse a stream of JSON objects, skipping anything between them."""
    dec = json.JSONDecoder()
    text = ZERO_PAD.sub(r"\1\2", text)
    objs, i = [], 0
    while True:
        j = text.find("{", i)
        if j == -1:
            break
        try:
            obj, k = dec.raw_decode(text, j)
            if isinstance(obj, dict):
                objs.append(obj)
            i = k
        except json.JSONDecodeError:
            i = j + 1
    return objs


def main() -> None:
    out_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        names = sys.argv[2:]
    else:
        names = sorted(p.name[:-len(".rejects.jsonl")]
                       for p in out_dir.glob("*.rejects.jsonl")
                       if any(json.loads(l).get("reason") == "not_json"
                              for l in open(p)))
    for name in names:
        rj = out_dir / f"{name}.rejects.jsonl"
        if not rj.is_file():
            print(f"{name}: no rejects file, skipped")
            continue
        frags = [json.loads(l)["raw"] for l in open(rj)
                 if json.loads(l).get("reason") == "not_json"]
        if not frags:
            print(f"{name}: no not_json fragments, skipped")
            continue
        f = source_for(name)
        sources = {str(f): numbered(f)} if f else {}
        objs = parse_objects("\n".join(frags))
        kept, failed = [], 0
        for obj in objs:
            kind, obj = check_object(obj, sources, str(f) if f else None)
            if kind == "keep":
                kept.append(obj)
            elif kind == "reject":
                failed += 1
        # Dedup against rows already present so re-runs are safe.
        dst = out_dir / f"{name}.jsonl"
        existing = set(dst.read_text().splitlines()) if dst.is_file() else set()
        new = [o for o in kept if json.dumps(o) not in existing]
        if new:
            with dst.open("a") as out:
                for o in new:
                    out.write(json.dumps(o) + "\n")
        print(f"{name}: {len(frags)} fragments -> {len(objs)} objects, "
              f"{len(new)} appended ({len(kept) - len(new)} duplicates), "
              f"{failed} failed verification")


if __name__ == "__main__":
    main()
