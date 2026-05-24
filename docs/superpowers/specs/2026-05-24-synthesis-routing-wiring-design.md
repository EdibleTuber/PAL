# Synthesis routing wiring (panel-blessed slice of held split)

**Date:** 2026-05-24
**Status:** Ready to implement
**Parent spec:** `2026-05-16-inference-routing-split-design.md` (held, full split)
**Related memory:** `project_review_inference_fetches`, `feedback_inference_remote`
**Author:** Drafted with Claude

## Scope

This spec ships the synthesis-side half of the held inference-routing-split design. The chat-side half (running the chat loop on a smaller model) remains held pending benchmark data. The synthesis-side wiring was pre-blessed by the 2026-05-16 panel review as *"uncontroversial and could ship independently."*

Concretely:

1. Wire 3 synthesis services (`Researcher`, `Compiler`, `Consolidator`) and 1 slash command (`/learn`) through the batch client so they route to whatever the batch slot is configured to run.
2. Fix the `batch_model` config default in agent_core which currently defaults to a smaller model than `model` (backwards from the user's "small chat, big synthesis" intent).

Out of scope:
- Chat-loop model swap (held)
- Benchmark harness (separate spec when chat-side resumes)
- `/note` (deleted in slash-prune)
- `/think on` flip behavior (held with chat-side)
- New `agent.effective_batch` attribute (panel told us to inline at slash sites; constructor sites use a local var)

## Why now

User has both slots loaded on the inference server (small + big concurrently). Today:

- Categorizer, LearningScanner, `/compile` already route through batch -- small bet.
- Researcher, Compiler (tool path), Consolidator, `/learn` still hit `self.inference` regardless -- the user pays the chat-model latency on heavy synthesis work even when batch is idle.
- `agent_core.config.batch_model` defaults to `gemma-4-E4B-it-Q4_K_M` (smaller than the `model` default of `Qwen3.5-35B-A3B-Q4_K_M`). So opting into batch without setting `PAL_BATCH_MODEL` explicitly silently runs the smaller model in the batch slot -- exact opposite of the user's intent.

The user already overrides batch_model via env in their active config. The config-default fix is to nip the silent-misroute risk in the bud for any future operator (or any future PAL deploy that forgets the env override).

## Design

### Phase 1: agent_core v1.3.3

`agent_core/config.py:43`:

```python
batch_model: str = "Qwen3.5-35B-A3B-Q4_K_M"   # was: "gemma-4-E4B-it-Q4_K_M"
```

Rationale: matching `model` means out-of-the-box both slots resolve to the same model. No behavior change for anyone not setting `PAL_BATCH_MODEL`. The user opts into split routing by setting the env var explicitly. Zero-change-for-existing-deployments default.

Tag as v1.3.3, push, update PAL pin in lockstep.

### Phase 2: PAL pin bump

`pal/pyproject.toml:11`: `agent_core@v1.3.2` → `agent_core@v1.3.3`.

### Phase 3: PAL synthesis wiring

`pal/agent.py` already computes `effective_batch` as a local var in `setup()` at line 217:

```python
effective_batch = (
    self.batch_inference if self.batch_inference is not None else self.inference
)
```

Reuse this local. Change three constructor sites:

```python
# Researcher (line 239)
self.researcher = Researcher(
    websearch=self.websearch,
    fetcher=self.fetcher,
    inference=effective_batch,             # was: self.inference
    vault_path=config.vault_path,
    ...
)

# Compiler (line 256)
self.compiler = Compiler(
    vault_path=config.vault_path,
    wiki=self.wiki,
    inference=effective_batch,             # was: self.inference
    ...
)

# Consolidator (line 275)
self.consolidator = Consolidator(
    vault_path=config.vault_path,
    wiki=self.wiki,
    inference=effective_batch,             # was: self.inference
    ...
)
```

`pal/commands/domain.py:364` (`/learn`):

```python
# Was:
completion = await ctx.agent.inference.complete(api_messages, reasoning="off")

# After:
inference = ctx.agent.batch_inference or ctx.agent.inference
completion = await inference.complete(api_messages, reasoning="off")
```

Inline pattern matches `/compile`'s existing form. No `effective_batch` attribute on the agent (per panel: "drop `agent.effective_batch` attribute").

### Phase 4: Tests (panel-trimmed)

Three tests:

1. **Parameterized constructor test** (`tests/test_agent_setup.py` or similar): when `batch_enabled=True`, assert that `agent.researcher.inference is agent.batch_inference`, same for compiler + consolidator. When `batch_enabled=False`, assert `agent.researcher.inference is agent.inference`. Parameterize over the three services.

2. **`/learn` parameterized** (`tests/test_learn_command.py` if it exists, else new file): mock `batch_inference` and `inference`; assert `complete()` called on batch when batch_enabled, on inference when not. Two parameter sets.

3. **Chat-loop negative**: run a tool-free chat round through `handle_chat`; assert `batch_inference.complete` was never awaited. Scoped to no-tool chat per the panel's note that broader scoping would flake whenever a chat fires a tool that routes to a now-batch site.

### Phase 5: Verify + git

Two commits, same order as today:

1. agent_core: stage config + pyproject, commit, tag v1.3.3, push --follow-tags.
2. PAL: stage pin + agent.py + domain.py + new tests, commit, push.

## Behavioral consequences

After this lands, with `PAL_BATCH_ENABLED=1` + `PAL_BATCH_INFERENCE_URL=<batch-url>` + `PAL_BATCH_MODEL=<big>`:

| Call site | Before | After |
| --- | --- | --- |
| Chat loop | self.inference | self.inference |
| Categorizer | effective_batch | effective_batch (unchanged) |
| LearningScanner | effective_batch | effective_batch (unchanged) |
| `/compile` cmd | inline batch fallback | inline batch fallback (unchanged) |
| Researcher | self.inference | effective_batch |
| Compiler (tool) | self.inference | effective_batch |
| Consolidator | self.inference | effective_batch |
| `/learn` | self.inference | inline batch fallback |

Without `PAL_BATCH_ENABLED=1`, every site resolves to `self.inference` via the fallback. Zero behavior change for the no-batch deploy path.

## Risks

- **Latency shift on /learn and synthesis tools**: if user has a slow batch model, these calls take longer than before. Mitigation: it's opt-in via batch_enabled.
- **Batch slot serialization**: 3 concurrent batch jobs serialize per the held spec. With Researcher and Compiler both routed, a multi-topic research + concurrent compile could queue. Not addressed in this slice; would need separate concurrency work.
- **/learn synthesizes from full conversation with PAL_BASE_PROMPT injected**: the synthesis prompt at domain.py:342 uses the heavy chat system prompt. That's Pass 2 audit territory; this spec just swaps the client, not the prompt.

## Acceptance

- 619 PAL tests + 625 agent_core tests still pass.
- 3 new tests pass.
- Manual smoke: with batch_enabled=True and different batch_model, running `/learn` should hit the batch slot (visible in inference server logs).
- agent_core v1.3.3 tag pushed; PAL pin bumped.

## Non-acceptance (out of scope)

- Chat-side model swap behavior is unchanged.
- Benchmark harness not built.
- Synthesis-tool internal prompts (Pass 2 audit) not touched.
- /think show/hide on chat-side small model still degraded.
