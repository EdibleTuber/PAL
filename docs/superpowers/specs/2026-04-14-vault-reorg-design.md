# Vault Reorganization Tools

**Date:** 2026-04-14
**Status:** Draft

## Overview

PAL can promote raw summaries into wiki articles but has no way to rename, move, or consolidate articles after they land. This produces two observable problems: (1) article filenames derived from long summary titles are unwieldy (200-char slugs appear in the wild even with the length cap in place; see the prior-commit slug-clipping fix), and (2) cross-cutting knowledge naturally accumulates in overlapping articles that want to be merged but can't be without manual filesystem surgery.

This spec adds consent-gated reorganization tools. A model can propose a batch of move and merge operations; the user approves via the same CLI/Discord button flow used for research and compile; the `Reorganizer` validates and executes the batch, rewriting markdown link references to preserve vault integrity.

Move covers rename-in-place and relocate-to-new-directory. Merge reuses the existing compile-merge pipeline to fold one article's content into another, then redirects links and deletes the folded-in source. Delete-as-its-own-operation is deferred to a follow-up spec; the only place v1 deletes a file is the archive step inside merge.

## Goals

- A model can propose a reorg (moves, merges, or mix) and a user can approve, decline, or edit-as-decline via CLI or Discord — consistent with the research and compile flows.
- Every moved or merged source has its incoming markdown link references rewritten automatically, keeping the vault's internal cross-references intact.
- Pre-validation ensures a batch with any invalid operation fails cleanly without touching the filesystem.
- The approval prompt shows each operation with its type tag (`[move]` or `[merge]`) and a reference-rewrite count preview, so the blast radius is visible before approval.
- Merge operations reuse the existing compile pipeline's LLM-driven content-combining logic. No new inference design.

## Non-Goals

- Delete as a standalone operation. Deferred to a follow-up spec.
- Split (one article into several). Out of scope.
- Smart link rewriting via the LLM (handling unusual markdown syntax, rewriting link text as well as target). Regex-based rewriting of `](path)` syntax covers the project's existing link conventions; future work can add LLM-based rewriting if the regex proves insufficient.
- Undo / rollback of a partial batch failure. Pre-validation catches common cases; individual op failures are reported per-op, and git commit history provides manual revert.
- Real edit-modal UX for reorg proposals. For v1, clicking `[e]dit` maps to decline and the model reproposes from the user's next message, mirroring the v1 compile-edit behavior.

## Architecture

Two new tools in `pal/tools.py`. A new `Reorganizer` class in `pal/reorg.py`. A new `ReorgProposalMessage` type in `pal/protocol.py`. `Proposal.kind` gains a `"reorg"` variant carrying the operations list. The daemon constructs `Reorganizer` once at startup and injects it into the per-connection `ToolExecutor`, mirroring the `Compiler` pattern from the prior compile spec.

```
propose_reorg(operations, rationale)       emits ReorgProposalMessage
                                            blocks on proposal.event
                                            returns {status, proposal_id}

reorg(proposal_id)                          consumes proposal (single-use)
                                            validates operations
                                            for each op:
                                              move:  rewrite links + rename + commit
                                              merge: compile-merge + redirect + delete + commit
                                            rebuild _index.md at end
                                            returns per-op report
```

**Reorganizer responsibilities:**

```python
class Reorganizer:
    def __init__(self, vault_path, wiki, compiler):
        # wiki: WikiManager for git + index rebuild
        # compiler: Compiler for merge-path reuse

    def count_references(paths: list[str]) -> int:
        # Dry-run scan used at proposal time to compute references_preview.

    def validate_operations(ops: list[dict]) -> list[str]:
        # Return list of error strings; empty means valid.
        # Checks: paths legal (inside vault), types recognized,
        # for move: src exists, dst doesn't, no collisions within batch.
        # for merge: src exists, dst exists, both have article frontmatter.

    async def execute_operations(ops: list[dict]) -> list[dict]:
        # Run each op sequentially. Per-op result dict:
        # {op, src, dst, status, references_rewritten, reason?}
```

Link-rewrite regex: scans all `.md` files under `vault_path` (excluding `raw/archived/`) for `](SRC_PATH)` patterns. Rewrites match to `](DST_PATH)`. Only touches inside markdown link syntax — literal prose mentions of a filename are unaffected.

## The Tools

### propose_reorg (consent-gated, blocking)

```
Parameters:
  operations (list[dict], required, non-empty)
    Each op: {"type": "move" | "merge", "src": str, "dst": str}
  rationale (str, required) — one-line reason shown in the approval prompt

Returns (JSON string, one of):
  {proposal_id, status: "approved"}
  {proposal_id, status: "declined"}
  {proposal_id, status: "timed_out"}

Side effects:
  Runs Reorganizer.count_references(all src paths) for the preview.
  Creates pending Proposal in ApprovalRegistry with kind="reorg".
  Emits ReorgProposalMessage.
  Awaits proposal.event with timeout bounded by proposal.expires_at.
```

### reorg (executes approved proposal)

```
Parameters:
  proposal_id (str, required)

Returns (JSON string):
  {
    total, ok, failed, references_rewritten,
    per_op: [{op, src, dst, status, references_rewritten, reason?}]
  }

Side effects:
  Consumes the proposal (single-use).
  Calls Reorganizer.validate_operations; if any error, returns error report
  and does NOT touch the filesystem (proposal stays consumed so the user
  proposes a corrected batch).
  Calls Reorganizer.execute_operations.
  Partial failures are reported per-op; the batch does not abort.
  Rebuilds _index.md and git-commits it as the final step.
```

## Operations

### Move

```
{"type": "move", "src": "AI-Agents/old.md", "dst": "AI-Agents/new.md"}
```

**Semantics:**
1. Pre-validate (batch-level): src exists as a file inside vault, dst doesn't exist, dst path is inside vault and doesn't escape, no duplicate src or dst within the batch, src != dst.
2. Scan vault for references to src (regex on `](src-path)` and relative variants).
3. For each matching file, rewrite `](src)` → `](dst)`.
4. `os.rename(src, dst)`.
5. `wiki.git_commit(f"reorg: move {src} -> {dst}")`.
6. Return per-op result with `references_rewritten` count.

### Merge

```
{"type": "merge", "src": "AI-Security/duplicate.md", "dst": "AI-Security/canonical.md"}
```

**Semantics:**
1. Pre-validate: both src and dst exist, both parse as valid articles (have frontmatter), paths legal.
2. Read src's body and title.
3. Call the compile pipeline's merge path (extracted from `Compiler._compile_one`'s existing-match branch) with src's body as "new content" and dst as the existing article. This runs an LLM call to produce a merged body that preserves dst's structure and incorporates src's material.
4. If the LLM returns `insufficient` (refusal to synthesize), the op fails with status `insufficient` — src and dst are both untouched, no link rewrites performed.
5. Otherwise, dst's body is rewritten with the merged content. dst's timeline gets a new entry: `merged from {src}` with current date.
6. Scan vault for references to src (same regex as move) and rewrite them to point at dst.
7. `os.remove(src)` (or rename to `raw/archived/` if you want to keep it — discussed below).
8. `wiki.git_commit(f"reorg: merge {src} into {dst}")`.
9. Return per-op result.

**Merge archival decision:** matching the existing compile pattern, which archives raw+summary pairs on successful compile, merge should move src to `raw/archived/` rather than hard-deleting. This makes a merge recoverable without git-specific knowledge. Final v1 behavior: move src to `raw/archived/` with `.archived.md` suffix.

### Heterogeneous batches

A single reorg batch may contain any mix of moves and merges. Ordering within the batch is execution order (first op runs first). This matters when multiple ops touch the same paths: e.g., merging A into B, then later moving B to B-new. The pre-validator must simulate execution state to catch errors like "op 3 references a path that op 1 moved" — simplest implementation: walk operations in order, tracking which paths have been produced/consumed, and validate each op against the simulated state.

## Protocol Message

```python
@dataclass
class ReorgProposalMessage:
    proposal_id: str
    operations: list[dict]       # [{"type": "move", "src": ..., "dst": ...}, ...]
    rationale: str
    references_preview: int
    type: str = "reorg_proposal"
```

Added to `_MESSAGE_TYPES` and the `Message` union. The existing `ResearchApprovalResponseMessage` carries approve/decline/edit responses for reorg proposals — no new response type needed.

`ApprovalRegistry.Proposal` gains an optional `operations: list[dict] | None = None` field. `create_proposal` accepts `kind="reorg"` with `operations` instead of `summary_paths` or `topic/depth`. Validation: reorg proposals require `operations` non-empty and every op carries recognized `type` with `src` and `dst` strings.

## CLI rendering

`pal/cli.py` gains a `format_reorg_proposal(msg)` helper and a new dispatch branch:

```
────────── PAL proposes reorg ──────────
  Operations (4):
    [move]  AI-Agents/github---codeaashu-...-f3c17ca2.md
            -> AI-Agents/claude-code.md
    [move]  AI-APIs/github---taylorwilsdon-...-b6489f1d.md
            -> AI-APIs/llm-context-limits.md
    [merge] AI-Security/mcp-notes.md
            -> AI-Security/mcp-threat-modeling.md
    [move]  Security/frida-notes.md
            -> Mobile-Security/frida-notes.md
  Rationale: Shorten filenames, consolidate MCP notes, relocate Frida.
  Would rewrite 12 link references.
  [a]pprove  [d]ecline  [e]dit
>
```

The `[move]` and `[merge]` prefixes make destructive ops (merge deletes src) visually distinct. Approve/decline/edit input handling mirrors the compile proposal branch. `[e]dit` sends `decision="decline"` (v1 behavior).

## Discord rendering

`pal/discord_interactions.py` gains `build_reorg_proposal_embed(msg: ReorgProposalMessage) -> (Embed, View)`:

- Embed title: "PAL proposes reorg"
- Embed color: orange (distinct from research blurple and compile green)
- Embed field "Operations (N)": multi-line, each entry prefixed with `[move]` or `[merge]`, then `src` on one line, `-> dst` on the next, truncated at 10 entries with `+M more` indicator
- Embed field "Rationale": the rationale string
- Embed field "Link rewrites": `{references_preview}`
- Three buttons with custom_ids `reorg:approve:<proposal_id>`, `reorg:decline:<proposal_id>`, `reorg:edit:<proposal_id>`

`_handle_button_interaction` gains a `reorg` kind branch that mirrors the `research` and `compile` paths — authorization check, approve/decline sent via `ResearchApprovalResponseMessage`, edit triggers a modal.

For v1, the reorg edit flow is decline-disguised-as-modal: the modal shows the operation list for visibility but its submit handler always sends `decision="decline"`. Alternatively — and likely simpler — the edit button directly sends `decision="decline"` without opening a modal, and PAL interprets the user's next chat message as the refined instruction. The simpler path matches how compile-edit works in v1.

Thread progress on approval: `_post_progress_to_thread` already handles arbitrary `ToolProgressMessage` events. Per-op progress during reorg execution flows through as it does for research and compile.

## Changes to Existing Code

### New file: `pal/reorg.py`

`Reorganizer` class with `count_references`, `validate_operations`, `execute_operations` methods. Imports `WikiManager` and `Compiler` (for the merge path). The merge-path compile logic is extracted from `Compiler._compile_one` into a new public method `Compiler.merge_into_existing(new_content, new_title, existing_article_path) -> dict` (described in the compiler section below) so `Reorganizer` can call it without duplicating the LLM-synthesis logic.

### `pal/compiler.py`

Extract the merge-compile logic (the `existing_match` branch inside `_compile_one`) into a new public method `Compiler.merge_into_existing(new_content: str, new_title: str, existing_article_path: str) -> dict`. The method runs the existing LLM synthesis prompt, writes the updated article body, appends a timeline entry, and returns a result dict matching the existing `_compile_one` shape (`status`, `title`, `article_path_rel`, `reason` on failure). `_compile_one` is refactored to delegate to this method on the `existing_match` path so behavior is unchanged for the compile tool. `Reorganizer` calls this same method for merge operations.

### `pal/approval_registry.py`

`Proposal` dataclass gains `operations: list[dict] | None = None` field. `create_proposal` accepts `kind="reorg"` with `operations` kwarg (required for reorg, unused for other kinds). Validation: reorg proposals require non-empty operations, each with `type in {"move", "merge"}` and string `src`/`dst`.

`ProposalKind` Literal extends to `Literal["research", "compile", "reorg"]`.

### `pal/protocol.py`

Add `ReorgProposalMessage` dataclass. Register in `_MESSAGE_TYPES`. Extend `Message` union.

### `pal/tools.py`

- `ToolExecutor.__init__` gains `reorganizer: Reorganizer | None = None`.
- Two new entries in `TOOL_DEFINITIONS`: `propose_reorg`, `reorg`.
- New handlers `_propose_reorg` and `_reorg` following the patterns from `_propose_compile_batch` and `_compile_batch` respectively.

### `pal/daemon.py`

Construct `Reorganizer` in `Daemon.__init__` with `self.compiler` passed as a dependency. Pass `reorganizer=self.reorganizer` into `ToolExecutor` construction inside `_handle_connection`.

### `pal/cli.py`

Add `format_reorg_proposal`. Add dispatch branch for `ReorgProposalMessage` in the chat loop mirroring the compile branch. `[e]dit` sends `decision="decline"` in v1.

### `pal/discord_interactions.py`

Add `build_reorg_proposal_embed`. Extend button-custom-id parser to accept `"reorg"` kind. Add `reorg` branch in `_handle_button_interaction` (approve/decline sends `ResearchApprovalResponseMessage`, edit sends `decision="decline"`).

### `pal/discord_adapter.py`

`DiscordStreamProcessor._handle_*_proposal` gains a handler for `ReorgProposalMessage` that posts via `build_reorg_proposal_embed` and stores a `ProposalContext` entry. No other adapter changes — the thread-progress flow already handles arbitrary progress messages.

## Data Flow

Happy path — user asks PAL to rename a badly-named article:

1. User: "That `github---codeaashu...` article in AI-Agents has a grotesque filename. Can you rename it?"
2. Model reads the filename, decides the target name, calls `propose_reorg([{type: "move", src: "AI-Agents/github---codeaashu...md", dst: "AI-Agents/claude-code.md"}], rationale="Shorten overlong filename")`. Tool blocks.
3. Tool runs `count_references(["AI-Agents/github---codeaashu...md"])` to compute the preview: 2 references found in two other articles.
4. Tool emits `ReorgProposalMessage` with `references_preview=2`. CLI/Discord renders the approval prompt.
5. User approves.
6. `propose_reorg` returns `{status: "approved", proposal_id: ...}`.
7. Model calls `reorg(proposal_id)`.
8. `Reorganizer.validate_operations` passes. `execute_operations` runs:
   - Scans vault for references to the src path → finds 2.
   - Rewrites both references to the dst path.
   - `os.rename(src, dst)`.
   - `wiki.git_commit(...)`.
9. Rebuild index, final commit.
10. Tool returns per-op report: `{total: 1, ok: 1, failed: 0, references_rewritten: 2, per_op: [{op: "move", ..., status: "ok", references_rewritten: 2}]}`.
11. Model reports the result in prose.

## Security

- **Path traversal:** `validate_operations` rejects any src or dst that resolves outside `vault_path`. Same guard the compile tool uses.
- **System paths:** src and dst cannot be under leading-underscore directories (`_config/`, `_index.md`, etc.). Reorg never touches system state.
- **Consent gate:** the reorg tool requires an approved proposal. Injection attacks attempting to call `reorg(proposal_id="fake")` fail the registry lookup. Consumed proposals can't be re-executed.
- **Per-op errors can't cascade:** if op 3 of 5 fails (e.g., merge returns `insufficient` from the LLM), ops 4 and 5 continue — each is independent. A failing op leaves no partial filesystem state because link rewrites happen *before* the rename/delete.
- **Git as audit trail:** every operation commits under a descriptive message. `git log` shows exactly what moved and when. `git revert <sha>` undoes any single op.

## Error Handling

- **Missing src:** validation fails, entire batch rejected before any filesystem touch.
- **Dst collision:** validation fails, batch rejected.
- **Duplicate src or dst within batch:** validation fails, batch rejected.
- **Operation references a path produced/consumed by an earlier op in the batch:** validation simulates execution state; if an op depends on a prior op's success and any prior op would fail validation, the dependent op also fails. Simplifies to: every op must be valid against the state *after* all prior ops succeed.
- **Merge LLM returns insufficient:** the merge op fails with status `insufficient`, src/dst both untouched, no link rewrites. Batch continues with next op.
- **Filesystem error mid-execution (disk full, permissions):** the specific op's status reports the error. Git commits from prior successful ops remain. No rollback.
- **Pre-validation error after proposal was approved:** returns an error report. Proposal is already consumed (strict single-use). User must propose again with corrected operations.

## Testing

### Unit tests (`tests/test_reorg.py`)

- `Reorganizer.count_references` with a synthetic vault containing known link references.
- `validate_operations`: missing src, dst collision, path traversal, leading-underscore, duplicate src/dst, empty operations list, simulated-state ordering.
- `execute_operations` move path: rewrites link references, renames file, git-committed.
- `execute_operations` merge path: calls `Compiler.merge_into_existing`, handles insufficient response, archives src on success, rewrites link references.
- Per-op error isolation: a failing op doesn't abort the batch; subsequent ops still run.

### Integration tests (`tests/test_reorg_integration.py` — or append to existing chat-tool integration file)

- `propose_reorg` happy path: emits `ReorgProposalMessage`, awaits event, returns approved status.
- `propose_reorg` decline: returns declined.
- `reorg` execution with mocked `Reorganizer`: verifies proposal consume-before-execute invariant.
- Injection-regression: `reorg(proposal_id="fake")` returns error, no `Reorganizer` call.

### Protocol tests (`tests/test_protocol.py`)

- `ReorgProposalMessage` roundtrip: encode, decode, assert all fields survive.

### Prompt regression (if we choose to update the system prompt in this spec)

Not strictly required. A future prompt-tuning pass can teach the model when to propose a reorg vs. when to leave things alone. For v1, `TOOL_DEFINITIONS` descriptions are sufficient — the model picks up reorg as a new capability through its schema.

### Manual smoke test (last plan task)

- Propose a rename of one of the known-bad long filenames. Approve via CLI. Verify the file renamed, the two known references rewrote, git log shows two commits.
- Propose a merge of two redundant articles. Approve via Discord. Verify dst article contains merged content, src archived, references rewritten, thread shows per-op progress.
- Propose a mixed batch (2 moves + 1 merge). Approve. Verify all three completed correctly.
- Trigger a merge where the LLM returns insufficient content (craft a summary likely to produce this). Verify the op reports `insufficient`, files untouched, batch continues.
- Propose an invalid batch (dst collision). Verify pre-validation rejects, nothing moves.

## Future Extensions

- **Delete as a standalone operation.** Same propose/approve pattern, simpler than merge (no content synthesis). Follow-up spec.
- **Split one article into several.** Inverse of merge. Requires LLM reasoning about which content belongs where. Genuinely hard UX.
- **LLM-driven link rewriting.** If regex proves insufficient for some markdown edge case (nested parentheses, HTML-embedded links), a follow-up can swap `Reorganizer._rewrite_links_in_file` for an LLM-driven edit per referring file.
- **Inline edit modal for reorg proposals.** V1 treats `[e]dit` as decline. A richer design would let the user modify the operation list directly in the Discord modal or CLI prompt.
- **Dry-run mode.** A `reorg_dry_run(proposal_id)` variant that validates and simulates without touching files, useful for inspecting a complex batch before committing.
