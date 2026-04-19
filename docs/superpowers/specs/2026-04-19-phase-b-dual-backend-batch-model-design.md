---
title: Phase B - Dual-Backend Inference with CPU-Resident Batch Model
date: 2026-04-19
status: draft
---

# Phase B: Dual-Backend Inference with CPU-Resident Batch Model

## Context

PAL's single inference backend is Gemma 4 26B-A4B on a Tesla P40. Chat responses share the GPU with background jobs (learning extraction, categorization, LLM-based chapter detection), which has two failure modes observed in practice:

1. Chat snappiness degrades when background work and chat land on the same model at the same time.
2. A long-running import or research session can OOM or saturate the P40, killing in-flight chat traffic (observed concretely on 2026-04-17 when llama-server was SIGKILLed mid-generation while handling a tool-loop-heavy import session).

The inference server (`agenthost`, 192.168.1.14) has an **AMD Cezanne Radeon Vega iGPU** alongside the P40. It shares system RAM with the host (UMA), runs a Vulkan backend in llama.cpp, and on real benchmarks delivers ~2x prompt-processing throughput over CPU-only at roughly the same generation speed (memory-bandwidth-bound). For short-output batch workloads, this is meaningful headroom at zero cost to the P40.

Phase B introduces a second inference backend running on the iGPU, pinned to a small capable model (Gemma 3 4B IT as the starting choice), and routes selected background workloads to it. Chat continues to own the P40 undisturbed.

Current relevant state:

- Inference server runs `llama-server.service` (CUDA, P40, port 8081) and `llama-embeddings.service` (CPU, nomic-embed-text, port 8082) behind `llama-manager.service` (port 11434, public-facing).
- The manager proxies OpenAI-compatible requests, handles model swaps on the main backend by editing `/etc/llama/llama-server.env` and restarting the service.
- PAL is at `/mnt/secondary/PAL` on the server and is the only client of significance.

## Non-goals

- Moving chat or latency-sensitive calls to the batch backend.
- Moving `compile_one`, `consolidate`, or `summarize_raw_file` in the initial rollout. These produce user-visible wiki articles and stay on Gemma 4 until the batch model proves quality; they can be moved in follow-up work with measurement behind each move.
- Replacing or modifying the embeddings service. Phase B is orthogonal to embeddings.
- Automatic transparent fallback from batch to main. Fallback is explicit, user-surfaced for user-facing callers, silent-skip for background callers.
- General-purpose ROCm support. Phase B standardizes on the Vulkan backend for the iGPU because it is the supported path for Cezanne Vega.

## Architecture

### Server-side: dual-slot manager

Today the manager has one logical backend slot. Phase B introduces a second slot:

| Slot | Device | Binding | Port | Default model | Swaps |
|------|--------|---------|------|---------------|-------|
| `main` | P40 (CUDA) | 127.0.0.1 | 8081 | Gemma 4 26B-A4B Q4_K_M | Auto on request of unloaded model (existing behavior) |
| `batch` | Vega iGPU (Vulkan) | 127.0.0.1 | 8083 | Gemma 3 4B IT Q4_K_M | Manual via admin endpoint |

Each slot runs its own llama-server process as its own systemd unit (`llama-server.service` existing, `llama-server-batch.service` new). Both call the same binary at `/opt/llama/bin/llama-server`, which must be built with both CUDA and Vulkan backends enabled. Device selection is per-process via command-line flags in the systemd unit (`--device CUDA0` for main, `--device Vulkan0` for batch, or equivalent llama.cpp flags at build time of this spec).

The manager (`llama-manager.service`) gains:

- Awareness of both slots: configuration in `manager.env` adds `BATCH_SERVER_HOST` / `BATCH_SERVER_PORT` alongside the existing main-backend settings.
- A model-to-slot routing table built from "what model is currently loaded on each slot." When a request arrives with a `model` field, the manager routes to the slot holding that model. If the model is not loaded anywhere, default behavior is to swap on `main` (preserves today's chat flow). Batch swaps are only initiated by the admin swap endpoint.
- A parameter on the swap endpoint to target a specific slot: `POST /swap {model, target}` where `target` is `main` (default) or `batch`. Requests without a `target` parameter keep existing behavior.
- Per-slot health reporting: `/status` gains a `slots` section showing each slot's loaded model, health, and last-swap time.

Queue handling stays per-backend (each llama-server has its own internal FIFO). The manager's existing `QUEUE_LIMIT=50` applies to main; batch gets its own limit (likely higher since batch work is less latency-sensitive, but configurable).

### Client-side: two InferenceClient instances in PAL

PAL's `Daemon` gains a second client alongside the existing one:

- `self.inference: InferenceClient`: default model is chat model (Gemma 4). Used by all existing callers except those explicitly moved to batch.
- `self.batch_inference: InferenceClient`: default model is the batch model (Gemma 3 4B). Used by the three initial offload callers.

Both clients point at the same manager URL; the manager handles routing. Callers grep-distinguishable: every offloaded call site uses `self.batch_inference` rather than a magic `model=` argument.

Config additions in `pal/config.py`:

- `batch_inference_url: str = "http://192.168.1.14:11434"`: default is the same manager URL; exposed separately in case a future deployment splits them.
- `batch_model: str = "gemma-3-4b-it-q4_k_m"`: default model name for the batch client.
- Env overrides: `PAL_BATCH_INFERENCE_URL`, `PAL_BATCH_MODEL`.

### Failure behavior

`batch_inference.complete(...)` raises a new exception `BatchUnavailableError` when the batch backend is unreachable, returns 503 repeatedly past retry, or the manager reports the batch slot as unhealthy. Callers catch it and handle differently by context:

**Background caller (learning scanner):** logs a warning and skips the extraction for this turn. No user-visible signal. The scanner runs on every turn, so a persistent outage does not spam the user.

**User-facing callers (categorizer, `detect_from_llm_toc`):** emits a proposal via the existing `approval_registry` pattern. UX surface:

```
Batch model unavailable.
The <categorize|detect-chapters> step can:
  [r] Retry on batch
  [m] Run on main instead (one-off)
  [s] Skip this step
```

For categorizer, "Skip" uses the configured default category (`Unfiled` or the system's existing fallback). For `detect_from_llm_toc`, "Skip" returns None and the tier falls through to single-file. "Run on main instead" reissues the same request using `self.inference` for this one call only.

A new proposal kind `BatchFallbackProposal` carries:

- `caller`: which offloaded caller is affected (`categorizer` / `llm_toc`).
- `context`: a short human-readable description of the operation ("categorizing compile for raw/summaries/X.md", "detecting chapters for Agentic_Design_Patterns.pdf").
- `original_request`: the messages + reasoning flag, so retry-on-main sends the same request to the other backend.

The proposal emits over the active connection (Discord or CLI) the same way consolidate and research proposals do today.

### /model command changes

Today `/model` shows the main backend's loaded model and allows swapping it. Phase B extends the UX without breaking existing muscle memory:

- `/model` (no args): shows both slots: `main: gemma-4-26b-a4b-it-q4_k_m (healthy)` and `batch: gemma-3-4b-it-q4_k_m (healthy)` plus available models per slot.
- `/model <name>`: swap main. Unchanged default behavior.
- `/model --target batch <name>`: swap batch. Admin operation, same under-the-hood mechanism as main swap.
- `/model --target main <name>`: explicit target, same as bare `/model <name>`.

Available models for each slot are queried from the manager's `/models?target=<slot>` (or equivalent) endpoint, which lists GGUF files matching each slot's compatibility profile.

### Initial offload set

Phase B ships with three callers moved to `batch_inference`:

1. **`Categorizer.categorize`** in `pal/categorizer.py`. Called during `compile_one` and `/import`. Short structured-output task (single category label from a whitelist). Gemma 3 4B handles reliably. Fallback-on-main is explicit and one-shot.

2. **Learning scanner extraction** in `pal/learning_scanner.py`. Fires after every LLM turn as a best-effort background scan. Latency-insensitive, silent failure is acceptable. Moving it to batch stops it from briefly holding the main model after every turn.

3. **`detect_from_llm_toc`** in `pal/pdf_structure.py`. Fires only when TOC and typography tiers both fail (rare). Structural extraction from a compact sample. Gemma 3 4B is more than adequate.

Callers NOT moved in Phase B (stay on main):

- Chat (`_handle_chat`).
- `compile_one` editorial / merge calls.
- `consolidate`.
- `summarize_raw_file` (feeds research synthesis; quality matters).
- Research subtasks.
- `find_existing_article` (semantic topic matching).

These can be moved individually in follow-up work once Phase B is running and we have evidence about the batch model's quality on each workload.

## Data shapes

### Manager `/status` response (extended)

```json
{
  "slots": {
    "main": {
      "host": "127.0.0.1",
      "port": 8081,
      "loaded_model": "gemma-4-26b-a4b-it-q4_k_m",
      "healthy": true,
      "last_swap_utc": "2026-04-17T05:37:10+00:00"
    },
    "batch": {
      "host": "127.0.0.1",
      "port": 8083,
      "loaded_model": "gemma-3-4b-it-q4_k_m",
      "healthy": true,
      "last_swap_utc": "2026-04-19T14:00:00+00:00"
    }
  },
  "queue_depth_main": 0,
  "queue_depth_batch": 0
}
```

### Manager `/swap` request (extended)

```json
{"model": "qwen3-4b-instruct", "target": "batch"}
```

`target` is optional; defaults to `main` when omitted.

### BatchFallbackProposal (PAL protocol)

```python
@dataclass
class BatchFallbackProposal(Message):
    proposal_id: str
    caller: Literal["categorizer", "llm_toc"]
    context: str  # "categorizing compile for raw/summaries/X.md"
    original_request: dict  # {messages: [...], reasoning: "off"}
```

Approval states: `retry` (retry on batch), `main` (run on main), `skip` (use caller's default fallback behavior).

## Build, deploy, and config

### llama.cpp binary

Rebuild `/opt/llama/bin/llama-server` with both CUDA and Vulkan backends enabled. The inference server's `~/Projects/inference_server` build scripts need updating accordingly. Validate that both `--device CUDA0` and `--device Vulkan0` work against the same binary before cutting over.

### New systemd unit

`systemd/llama-server-batch.service`:

- Parallel to existing `llama-server.service`.
- Loads `/etc/llama/llama-server-batch.env` (new file).
- Binds to `127.0.0.1:8083`.
- Uses `--device Vulkan0` (or the equivalent flag), explicitly not `-ngl` because Vulkan device selection handles layer allocation.
- MemoryMax set to bound the iGPU's shared-RAM footprint (prevents the iGPU backend from competing with chat for host RAM under pressure).

### Batch env file

`/etc/llama/llama-server-batch.env`:

```
HOST=127.0.0.1
PORT=8083
MODEL_PATH=/opt/llama/models/gemma-3-4b-it-q4_k_m.gguf
CTX_SIZE=16384
DEVICE=Vulkan0
```

### Manager config additions

`/etc/llama/manager.env`:

```
BATCH_SERVER_HOST=127.0.0.1
BATCH_SERVER_PORT=8083
BATCH_MODEL_DEFAULT=gemma-3-4b-it-q4_k_m
BATCH_QUEUE_LIMIT=20
```

### Model acquisition

Gemma 3 4B IT GGUF (Q4_K_M) lands at `/opt/llama/models/gemma-3-4b-it-q4_k_m.gguf`. Pull from Hugging Face (unsloth or official Google GGUF release), verify with sha256, record version in this spec's follow-up notes.

### Rollout order

1. Build llama.cpp with CUDA+Vulkan.
2. Validate Vulkan device works end-to-end on the host (model load + one-off inference via curl).
3. Drop Gemma 3 4B GGUF in place.
4. Install `llama-server-batch.service`, start it, confirm `/v1/chat/completions` on port 8083 responds.
5. Update manager to route by model name with the new slot configuration.
6. Roll PAL changes (two clients, three offloaded callers, proposal kind) behind an env flag (`PAL_BATCH_ENABLED=true`).
7. Flip the flag, observe under real usage.

## Error handling

- **Build fails (Vulkan not supported in toolchain):** roll back to CUDA-only binary; Phase B is blocked. Investigate Vulkan headers and driver before retrying.
- **Batch service fails to start (OOM, model not found):** systemd restarts per unit config; if repeated restarts fail, `BatchUnavailableError` surfaces to PAL as designed. Investigate via `journalctl -u llama-server-batch`.
- **Model swap on batch fails mid-flight:** manager enters error state for batch slot (same shape as existing main-swap error handling), `BatchUnavailableError` returns to PAL. Admin re-runs `/model --target batch` after fixing.
- **Manager reachable, batch slot unhealthy:** manager returns 503 with a body indicating batch slot status. PAL's inference client converts to `BatchUnavailableError`.
- **PAL flag off (`PAL_BATCH_ENABLED=false`):** `self.batch_inference` is None, offloaded callers transparently use `self.inference` instead. Effectively turns Phase B off without code removal. Useful for rollback and for environments without an iGPU.

## Testing strategy

### Unit tests

- `BatchFallbackProposal` shape and serialization.
- Categorizer failure path: when `batch_inference.complete` raises `BatchUnavailableError`, emits a proposal, and on each proposal state (retry/main/skip) dispatches correctly.
- Learning scanner failure path: when `batch_inference.complete` raises, logs a warning and returns no candidate (no proposal emitted).
- `detect_from_llm_toc` failure path: when `batch_inference` raises, returns None (same fallback as before).
- PAL's two-client construction from config: `batch_inference` present when `PAL_BATCH_ENABLED=true` and `batch_inference_url` reachable, None otherwise.
- Manager routing: mocked multi-slot manager, requests with known main/batch model names route correctly; unknown model names trigger main swap by default.

### Integration tests

- End-to-end against a running local dual-slot manager (mock or Docker-hosted): submit requests to both model names, verify each lands on the expected slot.
- Compile flow with batch down: simulate BatchUnavailable, confirm proposal UX surfaces and each response state produces the expected behavior.

### Manual validation

- Install the batch service on `agenthost`, confirm iGPU-resident inference on Gemma 3 4B. Measure tok/s for a realistic prompt to validate Vulkan performance.
- Run a real compile against a raw summary with `PAL_BATCH_ENABLED=true`; confirm categorizer fires on batch without affecting chat latency in a parallel session.
- Simulate batch outage (`systemctl stop llama-server-batch`), run the same compile, confirm the proposal appears and each response option behaves.

## Follow-up work (out of scope for this spec)

- **Move `compile_one`, `consolidate`, and `summarize_raw_file` to batch** after Phase B is running. Measure quality regression on the specific kinds of content PAL actually processes (web fetches, book chapters) before committing. Separate spec each.
- **Editorial cleanup pass on imported chapters** as a new caller on batch. Was the original motivation for Phase B; was queued behind Phase B infrastructure. Separate spec.
- **`ask_file` delegated sub-call tool** using batch as the default target. Separate spec.
- **Dynamic batch-model selection per caller** (different tasks want different small models). Overengineering for now; revisit only if we hit a concrete case.
- **Per-slot CTX_SIZE autotune.** For now, the batch CTX is hand-configured.

## Success criteria

- `/model` shows two healthy slots and distinct loaded models.
- Submitting the same request to PAL with `PAL_BATCH_ENABLED=true` vs `false` produces equivalent-quality output for the three offloaded callers; the only observable difference is that chat latency stays steady under import or learning-scan load with the flag on.
- Stopping the batch service mid-compile produces a visible proposal to the user and (on "Run on main") completes the compile successfully via fallback.
- Stopping the batch service with a learning-scan in flight logs a warning and does not disturb the user.
- `nvidia-smi` shows Gemma 4 on the P40 unchanged under concurrent batch traffic; host RAM delta attributable to the batch model stays under 3 GB (Gemma 3 4B Q4 + KV cache for CTX_SIZE=16384).
