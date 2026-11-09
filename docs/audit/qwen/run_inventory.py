#!/usr/bin/env python3
"""Run the audit inventory passes against a local model and verify citations.

Adapted from the inference-server audit (its docs/audit/qwen/run_inventory.py).
Differences for PAL:
- per-file passes run over the whole `pal/` package; stub files with fewer
  than 10 lines (the empty __init__/__main__ files) are skipped
- per-file output names use the slash-escaped path relative to the repo,
  because pal/reorg.py and pal/tools/reorg.py share a stem
- requests are authenticated with the manager token (the manager has required
  it since the inference-server phase-6 shared-token work); the token file
  defaults to ~/.config/llama/manager-token
- the p6 config surface adds agent_core's config module (the actual PAL_* env
  reader, taken from the deployed venv), the repo systemd units including
  timers, and host files under /etc/pal and /etc/systemd/system so
  repo-vs-host drift is visible

Requests go one at a time with stream=true, so they never hit the manager's
120s non-streaming timeout (incident 2026-10-27, F4).

Every entry whose `quote` is not on its cited line is moved to *.rejects.jsonl.
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"

PER_FILE_PASSES = ["p1_timeouts_waits", "p2_exceptions_tasks", "p3_state_writes",
                   "p4_external_effects", "p5_claims"]


def code_files() -> list[Path]:
    return sorted(p for p in (REPO / "pal").rglob("*.py")
                  if len(p.read_text(errors="replace").splitlines()) >= 10)


def agent_core_config() -> Path | None:
    for venv in (REPO / ".venv", Path("/mnt/secondary/PAL/.venv")):
        hits = sorted(venv.glob("lib/python*/site-packages/agent_core/config.py"))
        if hits:
            return hits[0]
    return None


def config_files() -> list[Path]:
    files = [REPO / "pal/config.py"]
    ac = agent_core_config()
    if ac:
        files.append(ac)
    files += sorted((REPO / "systemd").glob("*.service"))
    files += sorted((REPO / "systemd").glob("*.timer"))
    files += sorted(Path("/etc/pal").glob("*.env"))
    files += sorted(Path("/etc/systemd/system").glob("pal-*.service"))
    files += sorted(Path("/etc/systemd/system").glob("searxng-*"))
    return [f for f in files if f.is_file()]


def numbered(path: Path) -> list[str]:
    return path.read_text(errors="replace").splitlines()


def render(path: Path, lines: list[str]) -> str:
    return f"===== FILE: {path} =====\n" + "\n".join(
        f"{i:04d}| {line}" for i, line in enumerate(lines, 1))


class Incomplete(Exception):
    pass


def ask(url: str, model: str, token: str | None, prompt: str, max_tokens: int, think: bool) -> str:
    body = json.dumps({
        "model": model, "stream": True, "temperature": 0.3, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "chat_template_kwargs": {"enable_thinking": think},
    }).encode()
    headers = {"content-type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=body, headers=headers)
    out, finish = [], None
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            choice = json.loads(line[5:])["choices"][0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta", {})
            if delta.get("content"):
                out.append(delta["content"])
                print(delta["content"], end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    text = "".join(out)
    # An empty or length-capped answer is a failed run, not an empty inventory.
    if finish != "stop" or not text.strip():
        raise Incomplete(f"finish_reason={finish!r}, content_chars={len(text)}")
    return text


def check_object(obj: dict, sources: dict[str, list[str]], default_file: str | None):
    """Verify one parsed record against the cited source line.

    Returns (kind, obj) with kind in {"keep", "summary", "reject"}.
    Used by verify() and by recover_fragments.py so the recovered rows go
    through exactly the same gate as the live ones.
    """
    if "line" not in obj or "quote" not in obj:
        return "summary", obj  # summary / none rows carry no citation
    fname = obj.get("file", default_file)
    lines = sources.get(fname)
    n = obj["line"]
    ok = (lines is not None and isinstance(n, int) and 1 <= n <= len(lines)
          and obj["quote"].strip() and obj["quote"].strip() in lines[n - 1])
    if not ok:
        return "reject", {"reason": "quote_mismatch", **obj}
    if default_file:
        obj.setdefault("file", fname)
    return "keep", obj


def verify(text: str, sources: dict[str, list[str]], default_file: str | None):
    good, bad = [], []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            # The model echoes the zero-padded line prefix ("line": 0025), which is invalid JSON.
            obj = json.loads(re.sub(r'(":\s*)0+(\d)', r"\1\2", raw))
        except json.JSONDecodeError:
            bad.append({"reason": "not_json", "raw": raw})
            continue
        kind, obj = check_object(obj, sources, default_file)
        (good if kind in ("keep", "summary") else bad).append(obj)
    return good, bad


def write(out_dir: Path, name: str, good, bad):
    for suffix, rows in ((".jsonl", good), (".rejects.jsonl", bad)):
        (out_dir / f"{name}{suffix}").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{name}: {len(good)} kept, {len(bad)} rejected", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="Qwen3.6-35B-A3B-UD-IQ4_NL_XL")
    ap.add_argument("--token-file", default="~/.config/llama/manager-token")
    ap.add_argument("--passes", nargs="*", default=PER_FILE_PASSES + ["p6_config_surface"])
    ap.add_argument("--files", nargs="*",
                    help="limit per-file passes to these basenames (note: pal/reorg.py and "
                         "pal/tools/reorg.py share a basename; the filter selects both)")
    ap.add_argument("--p6-files", nargs="*",
                    help="explicit file list for p6_config_surface (default: derived config "
                         "surface). Use with --p6-name to run p6 in subsets when the full "
                         "surface exceeds the output cap.")
    ap.add_argument("--p6-name", default="p6_config_surface",
                    help="output basename for the p6 run (must differ between subset runs)")
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--think", action="store_true", help="enable model reasoning (default off)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    token_path = Path(args.token_file).expanduser()
    token = token_path.read_text().strip() if token_path.is_file() else None
    common = (PROMPTS / "_common.md").read_text()
    failures = []

    def run(name, prompt, sources, default_file):
        for stale in out_dir.glob(f"{name}.*"):
            stale.unlink()
        try:
            text = ask(args.url, args.model, token, prompt, args.max_tokens, args.think)
        except Incomplete as exc:
            (out_dir / f"{name}.FAILED").write_text(str(exc) + "\n")
            failures.append(name)
            print(f"{name}: FAILED ({exc})", flush=True)
            return
        good, bad = verify(text, sources, default_file)
        write(out_dir, name, good, bad)

    for p in args.passes:
        task = (PROMPTS / f"{p}.md").read_text()
        if p == "p6_config_surface":
            files = ([Path(x) for x in args.p6_files] if args.p6_files
                     else config_files())
            missing = [str(f) for f in files if not f.is_file()]
            if missing:
                print(f"p6: missing files, aborting: {missing}", flush=True)
                sys.exit(2)
            sources = {str(f): numbered(f) for f in files}
            prompt = common + "\n" + task + "\n\n" + "\n\n".join(
                render(f, sources[str(f)]) for f in files)
            run(args.p6_name, prompt, sources, None)
            continue
        for f in code_files():
            if args.files and f.name not in args.files:
                continue
            lines = numbered(f)
            prompt = common + "\n" + task + "\n\n" + render(f, lines)
            name = f"{p}__{f.relative_to(REPO).with_suffix('').as_posix().replace('/', '_')}"
            run(name, prompt, {str(f): lines}, str(f))

    if failures:
        print(f"\n{len(failures)} FAILED: {' '.join(failures)}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
