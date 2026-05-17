# Dynamic tool loading (tiered, PAL-side)

**Date:** 2026-05-16
**Status:** Held -- panel review (2026-05-16) flagged this is downstream of the small-model chat decision (held in `2026-05-16-inference-routing-split-design.md`). Cheap-ship alternative: extend `disabled_builtins` to prune unused agent_core builtins. Revisit dynamic loading only if the benchmark commits to small chat AND pruning didn't close the context gap.
**Author:** Brainstormed with Claude
**Related:** `project_pal_path_determinism`, today's small-model smoke (gemma-4-E4B-it-Q4_K_M), `2026-05-16-inference-routing-split-design.md` (held for the same reason)

## Why held

Panel review (4 reviewers, 2026-05-16) converged on hold:

- **Order of operations problem.** This spec is downstream of "commit to the small model for chat." The routing spec is already Held pending a small-vs-big benchmark. If the benchmark says small isn't viable, every line of `tool_loader.py` and `meta.py` becomes dead code. Same trap as routing.
- **Real cost is ~5K tokens, not 5-13K.** Measured: 29 schemas, 20,099 chars JSON, ~5,024 tokens. Smaller motivation than the draft pitched.
- **Prompt/schema mismatch unaddressed.** PAL_BASE_PROMPT lists tools by name in prose. Filtering schemas without filtering prose creates contradictory signals: model reads "you have edit_file" then tries to call a schema that's not there. Risk #6 in this draft acknowledged it and punted; reviewers say it's load-bearing.
- **`request_tools` meta-tool is too much for a 4B model.** Free-form category string, 2-round-trip flow, recovery reasoning. Today's smoke already showed narration leakage on the small model; adding meta-reasoning makes it worse.
- **Keyword false negatives on file ops.** "Rewrite", "tidy up", "fix the typo", "clean it up" don't hit any keywords. File ops is the most common write intent; recovery is the unhappy path.

## Tier definitions in this draft are buggy

Codebase reviewer ran the actual registry. Errors to fix on revisit:
- `read_file` doesn't exist (PAL dropped it; use `cat` + `read_lines`).
- `list_vault` doesn't exist (use `ls`).
- `current_date` doesn't exist anywhere.
- `reorganize` -- actual name is `reorg`.
- `wait_for_research` -- actual name is `wait_for_reindex`.
- `fetch_url` already in `disabled_builtins` (no-op to tier it).
- Untiered: `compile_summary`, `propose_compile_batch`, `compile_batch`, `update_scratch`.
- HandlerContext has no mutable per-turn state field; the "pass active_tiers via context" line is hand-wave. Real mechanism would be `dict[channel_id, set]` on `PALAgent`.

## Cheap-ship alternative (separate spec/work)

Extend `disabled_builtins` to remove agent_core builtins that overlap PAL's own tools. Zero new code, zero design surface. Measure first to ground specific drops; ship as a one-line frozenset extension.

---

(Original draft preserved below for revisit.)

---

## Problem

PAL's `ToolExecutor.schemas()` returns all ~26 tool schemas (19 PAL tools + 7 agent_core builtins) on every chat-loop round. At ~200-500 tokens per schema that's 5-13K tokens of context, every round, just for tool definitions. On the big model (32K context, gemma-4-26B) this is bearable; on the small model (4B, with smaller working context and known sensitivity to schema noise) it's a meaningful slice of the budget and may degrade tool-call accuracy.

PAL_BASE_PROMPT also catalogs all tools in prose, but the tool schemas (JSON) are what the model actually sees in the function-calling structure. Those are the load to shrink.

## Constraint

**agent_core stays untouched.** The hook is PAL-side: intercept what gets passed to `inference.complete(tools=...)` and `inference.stream(tools=...)` at the 3 sites in `pal/agent.py:handle_chat` (lines 479, 504, 565). `ToolExecutor.schemas()` keeps returning all schemas; PAL narrows the subset that reaches the inference wire.

## Proposed design

### Tier definitions (`pal/tool_loader.py`, new file)

```python
TIER_CORE = {
    "search_vault", "read_file", "list_vault", "current_date",
}
TIER_FILE_OPS = {
    "edit_file", "replace_in_file", "create_file", "move_file", "delete_file",
}
TIER_PROPOSALS = {
    "propose_consolidate", "consolidate",
    "propose_promote", "promote",
    "propose_url_fix", "url_fix",
    "propose_reorg", "reorganize",
    "propose_promote_synthesis",
}
TIER_RESEARCH = {
    "propose_research", "research_topic", "wait_for_research", "fetch_url",
}
TIER_LEARNING = {
    "add_learning",
}
```

Core is always loaded. Other tiers are loaded on detection (see below).

### Keyword detection (`pal/tool_loader.py`)

```python
TIER_KEYWORDS = {
    "file_ops":  ["edit", "replace", "create", "update", "fix", "rename", "move", "delete"],
    "proposals": ["consolidate", "merge", "promote", "fix url", "reorganize", "promote to wisdom"],
    "research":  ["research", "look up", "search the web", "find online"],
    "learning":  ["learn this", "remember this", "lesson"],
}

def detect_tiers(user_message: str) -> set[str]:
    msg = user_message.lower()
    active = {"core"}
    for tier, keywords in TIER_KEYWORDS.items():
        if any(kw in msg for kw in keywords):
            active.add(tier)
    return active
```

### Meta-tool: `request_tools` (`pal/tools/meta.py`, new file)

```python
class RequestTools(Tool):
    name = "request_tools"
    description = (
        "If you need a tool that you don't see in your current schema list, "
        "call this with the category name. Tools become available on your "
        "next call. Categories: file_ops, proposals, research, learning."
    )
    # Mutates per-turn active_tiers; next round's filter_schemas includes
    # the requested tier.
```

Always loaded in Tier 1 (so always available). Single tool slot, free-form tier name.

### Filter at the call sites (`pal/agent.py`)

```python
def _dynamic_schemas(self, active_tiers: set[str]) -> list[dict]:
    """Return schemas for tools belonging to active tiers."""
    allowed = set()
    for tier in active_tiers:
        allowed.update(TIER_NAMES_BY_KEY.get(tier, set()))
    allowed.add("request_tools")  # always available
    return [s for s in self.tool_executor.schemas()
            if s["function"]["name"] in allowed]

# In handle_chat:
active_tiers = detect_tiers(msg.text)
# ... in each tool-loop round:
schemas = self._dynamic_schemas(active_tiers)
completion = await self.inference.complete(messages, tools=schemas, ...)
```

`active_tiers` is a turn-local set (lives in handle_chat's scope), passed to RequestTools execution via HandlerContext so it can mutate the set when the model requests more.

### Error-path tool guidance

If the model calls a tool not in the current loaded set, `ToolExecutor.dispatch()` errors. Wrap the error in PAL's tool-loop to translate:

```python
"Tool 'X' is not currently loaded. Call request_tools(category='file_ops')
to load it, then try again."
```

(Where `category` is inferred from a reverse lookup: which tier contains X?)

## Estimated context savings

Core-only chat: 5 schemas vs 26 = ~80% reduction in tool-schema tokens. For typical chats (retrieval-heavy), context-per-round drops by 4-10K tokens.

Worst case: user message triggers all tiers loaded. Filter returns nearly all schemas; ~0% savings. Acceptable; this is the same as today.

## Open design questions

1. **Tier boundaries.** 4 tiers (file_ops / proposals / research / learning) + core. Right granularity? Should file_ops split read-vs-write? Should learning be in core (cheap, common) or its own tier (rare)?
2. **Keyword list completeness.** "consolidate"/"promote"/"research" are clear. "fix the typos in X" needs file_ops on "fix"/"update" -- risk of false positives ("fix my understanding" loads file_ops unnecessarily, wastes ~1.5K tokens but does no harm).
3. **State lifetime.** Per-turn ephemeral (recommended) vs persistent per-channel. Ephemeral is simpler; persistent lets "I'm doing several consolidates" stay loaded but needs state cleanup.
4. **request_tools signature.** Free-form tier name (one tool, `request_tools(category="proposals")`) vs per-tier tools (4 tools, `request_proposal_tools()`, etc). Free-form keeps the core tier slim.
5. **Failure messaging.** Should the error from a missing tool tell the model exactly which `request_tools(category=...)` call to make? Big QoL win, costs nothing.
6. **What about slash commands?** `/research`, `/compile`, etc. flow through a separate code path (not via tool schemas to the chat model). Out of scope for this change; they're invoked directly. Worth confirming this is true.
7. **Token-cost validation.** Estimate of 5-13K tokens for tool schemas is back-of-envelope. Worth measuring the actual token count of `tool_executor.schemas()` serialized vs the filtered subset before committing.

## Non-goals

- Modifying `agent_core/tools/` (Tool base, ToolExecutor, builtin tools).
- Modifying the `Agent.tools` ClassVar mechanism.
- Removing tools from PAL (pruning) -- this is filtering, not pruning. Every tool still exists.
- Per-channel tier preferences (override which tiers are always loaded for this channel).
- Caching per-conversation: "this conversation has been about proposals, default to loading proposals tier."
- LLM-driven classification (using a model to pick the tier from message). Keyword detection is intentionally dumb and deterministic.

## Risks

1. **Keyword detection has false negatives.** User says "rewrite the GDB notes to be clearer" -- "rewrite" isn't in TIER_KEYWORDS["file_ops"]. Model doesn't see edit_file, must call `request_tools("file_ops")` first. One extra round-trip on miss. Acceptable.
2. **Keyword detection has false positives.** "Fix my understanding of GDB" loads file_ops tier unnecessarily. Wastes ~1.5K tokens that round. Acceptable.
3. **Multi-intent messages.** "Research X and then update my notes." Detection should pick up "research" AND "update" -- correctly fires both tiers. Verify with test cases.
4. **The model doesn't read tool descriptions.** A 4B model may not understand "call request_tools to load more tools" from the description alone -- it might just hallucinate a non-loaded tool. The error-path message in the dispatch wrapper mitigates by explicitly naming the request_tools call.
5. **Model loops on request_tools.** Model calls request_tools, gets new schemas, calls a tool, fails for an unrelated reason, calls request_tools again, etc. Cap turns / detect repeated request_tools calls and force-load all tiers as a fallback.
6. **Slash commands rendered in the system prompt.** PAL_BASE_PROMPT may still describe tools that aren't in the current schema set, confusing the model. Pre-check whether the prompt lists tools by name or only conceptually.

## What this spec doesn't include (yet)

- Concrete tier-to-tool-name mappings verified against actual PAL tool registry (sketched above; needs grep to confirm).
- Concrete test list.
- Migration / verification steps.
- A definitive ranking of the 7 open questions.

This is intentional. The panel review should shape these before the spec hardens.
