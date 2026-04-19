# Phase B PAL Integration: Manual Verification Runbook

Ordered steps to verify the PAL-side Phase B changes work against a running dual-slot manager. Assumes the server-side Phase B plan (separate, not yet written) has landed and the batch endpoint is healthy.

## Prerequisites

- Server-side Phase B deployed: manager with dual slots, `llama-server-batch.service` running on port 8083, Gemma 3 4B loaded on the Vega iGPU via Vulkan.
- `/mnt/secondary/PAL` on the server is on the latest PAL commit including PB-1 through PB-16.
- Server-side systemd units restarted after PAL pull.
- `pip install -e .` has been re-run in PAL's `.venv` on the server (dependencies unchanged for Phase B, but routine after a pull).

## 1. Flag off: confirm no regressions

On the server:

```bash
systemctl --user stop pal-daemon
# Ensure PAL_BATCH_ENABLED is unset or false in the service environment.
# Check with: systemctl --user show pal-daemon -p Environment
systemctl --user start pal-daemon
pal
```

In the CLI:

```
> /help
> /model
```

Expected:

- `/model` shows only the main slot (e.g., `main: gemma-4-26b-a4b-it-q4_k_m (healthy)`).
- All existing flows work unchanged: `/compile`, `/import` on a simple file, chat, `/research`.

## 2. Flag on: confirm batch construction and routing

Set `PAL_BATCH_ENABLED=true` in the daemon's systemd environment (edit the user unit's drop-in or `~/.pal/env` depending on how env is loaded). Restart:

```bash
systemctl --user restart pal-daemon
```

```
pal
> /model
```

Expected:

- `/model` shows both slots: `main: gemma-4-26b-a4b-it-q4_k_m (healthy)` and `batch: gemma-3-4b-it-q4_k_m (healthy)`.
- Both `/model --target main <name>` and `/model --target batch <name>` produce "Requested swap: <target> -> <name>" output.
- A chat turn completes normally and (because it invokes no offloaded caller by itself) produces no user-visible change from flag-off behavior.

## 3. Categorizer happy path on batch

Trigger a compile of an existing raw summary:

```
> /compile raw/summaries/<some-file>.md
```

Expected:

- Compile succeeds. Its category is chosen by the batch model (Gemma 3 4B).
- No user prompts appear.
- Chat latency in a parallel CLI session is unaffected: run a chat in another terminal during the compile, confirm responses stay snappy.

## 4. Categorizer batch outage with user fallback

On the server:

```bash
sudo systemctl stop llama-server-batch
```

Trigger another compile:

```
> /compile raw/summaries/<some-other-file>.md
```

Expected:

- PAL surfaces a `BatchFallbackProposal` prompt in the CLI with three options: `[r] Retry on batch`, `[m] Run on main instead (one-off)`, `[s] Skip this step`.
- Choose `r` → retry fails again (batch still down) → proposal re-emits.
- Choose `m` → compile succeeds using the main Gemma 4 for categorize.
- Choose `s` → compile succeeds with the default category (`Research`).

Restart the batch service:

```bash
sudo systemctl start llama-server-batch
```

## 5. Learning scanner silent skip

Stop the batch service again:

```bash
sudo systemctl stop llama-server-batch
```

Have a normal chat turn (send any message, get a response).

Expected:

- No user-visible prompt about batch.
- `journalctl --user -u pal-daemon -n 50` shows a `Learning scan skipped, batch unavailable` warning.
- Chat response lands normally; learning extraction simply did not run for that turn.

Restart:

```bash
sudo systemctl start llama-server-batch
```

## 6. Swap the batch model

With batch up:

```
> /model --target batch qwen3-4b-instruct
```

Expected:

- Manager swaps the batch slot. PAL prints "Requested swap: batch -> qwen3-4b-instruct".
- `/model` reflects the new loaded model on batch.
- Chat latency on main is unaffected during the batch swap.

Swap back:

```
> /model --target batch gemma-3-4b-it-q4_k_m
```

## 7. PDF import with LLM-TOC fallback proposal

Prepare a PDF that has no embedded TOC and no clear typography (a scanned-style or uniformly-styled document). Place at `raw/flat-book.pdf`.

Stop the batch service:

```bash
sudo systemctl stop llama-server-batch
```

In PAL:

```
> /import raw/flat-book.pdf
```

Expected:

- Tiers 1 and 2 both return None, tier 3 is invoked, raises `BatchUnavailableError`.
- A `BatchFallbackProposal` surfaces with `caller: llm_toc` and context naming the PDF.
- Choosing `m` (run on main) runs tier 3 on the main Gemma 4 and continues the import.
- Choosing `s` (skip) falls through to single-file output at `raw/sources/flat-book/full.md`.

Restart batch after the test.

## 8. Concurrent load test

With batch enabled and healthy:

- Terminal 1: a long chat session in the CLI.
- Terminal 2: trigger a compile of a non-trivial summary, or a PDF re-import. The compile's categorize call lands on the batch model; the import's LLM-TOC fallback (if it fires) also lands on batch.

Expected:

- Terminal 1 chat latency stays at normal Gemma 4 speeds (~40 tok/s).
- `nvidia-smi` shows the P40's utilization/memory unchanged while the categorize/LLM-TOC calls run on the iGPU.
- No user-visible BatchFallback prompt.

## 9. Rollback

If anything goes wrong at any step:

```bash
# Flip the flag off
sed -i 's/PAL_BATCH_ENABLED=true/PAL_BATCH_ENABLED=false/' \
  ~/.config/systemd/user/pal-daemon.service.d/override.conf
systemctl --user daemon-reload
systemctl --user restart pal-daemon
```

All offloaded callers silently fall back to the main inference. No code deployment required. PAL behaves as pre-Phase B.

## 10. Success criteria review

Before declaring Phase B done, confirm against the spec:

- [ ] `/model` shows two healthy slots and distinct loaded models.
- [ ] Compile + learning scan + any PDF tier-3 fallback all land on the batch backend under normal conditions (visible in `journalctl -u llama-server-batch` request counts).
- [ ] Stopping the batch service mid-compile produces the proposal and "Run on main" completes the compile.
- [ ] Stopping the batch service with a learning scan does not disturb the user.
- [ ] `nvidia-smi` shows P40 footprint unchanged under concurrent batch traffic.
- [ ] Host RAM delta attributable to the batch model stays under 3 GB (Gemma 3 4B Q4 + KV cache for batch CTX_SIZE).
