# Verified: pal_compiler p4+p5 (task 2/2)

Synthesis note: persisted by the synthesis session after re-adjudication
(2026-11-09). The subagent's one "confirmed" finding (unguarded
`trigger_reindex`) is REJECTED as a false positive: `trigger_reindex` is
internally best-effort in the deployed agent_core (see Ruled out). Final
result: 0 findings, all 58 leads ruled out.

## Ruled out

- p4:297, p4:507, p4:596, p4:722 — `trigger_reindex` (agent_core/retrieval.py:74–105) catches all exceptions and non-202 statuses, logs a warning, returns None; docstring: "A None return is intentional best-effort: a downed inference server must never break the write path." The 4 call sites need no guard. Verified against the deployed venv (agent_core 1.13.0) — the runtime.
- p4:109,321 — Local filesystem query (Path.exists), not an external effect.
- p4:114,326 — Local filesystem query (Path.resolve), not an external effect.
- p4:115,327 — Local filesystem query (Path.resolve), not an external effect.
- p4:121,333,408,530,579,627 — Local file read (Path.read_text) within the vault, not an external effect.
- p4:150,390 — categorizer.categorize makes an inference call internally but is guarded: categorizer.py:117–140 wraps the call, `except Exception` → warning + default category (synthesis spot-checked).
- p4:157,397 — wiki.list_articles reads local .md files via Path.rglob, not an external effect.
- p4:158,398 — find_existing_article makes an inference call internally but is guarded: article.py:253–258 `except Exception` → warning + None (synthesis spot-checked).
- p4:167 — Local object construction (prompt_builder.build), no I/O.
- p4:180,286,496,581 — archive_raw_files writes/moves files within the vault (pal/archive.py:17–40, rename into raw/archived/), local file operations (synthesis spot-checked).
- p4:185,287,497,586 — wiki.git_commit is guarded: wiki.py:179 bails without .git, wiki.py:196–197 no-op when clean.
- p4:216 — inference.complete is guarded by try/except at compiler.py:215–220.
- p4:220,671 — Returns a dict, not an external effect.
- p4:272,482 — Path.mkdir creates a directory within the vault, local filesystem operation.
- p4:276,486,568,706 — Path.write_text writes to the vault, local file operation.
- p4:280,490,571,710 — wiki.rebuild_index writes _index.md within the vault, local file operation.
- p4:281,491,572,711 — wiki.git_init is guarded: wiki.py:149 bails without .git.
- p4:282,492,573,712 — wiki.git_commit is guarded (see p4:185,287 above).

## Notes

- p4 tool over-labels `kind` (local Path operations labeled "fs_query", vault-internal writes labeled "file_write", guarded git ops labeled "subprocess"). The substance of the quote determines the ruling, not the kind.
- p5 verdicts seen: consistent=2 contradicted=0 cannot_tell=0. Both p5 claims verified true: EmptySourceError at :144 (raised when both source fields empty; claim at :46), timeline preservation at :553 (`list(existing_article.timeline)`; claim at :528).
- Subagent mis-ruling corrected in synthesis: it claimed the 4 `trigger_reindex` sites were unguarded HTTP calls that crash after the article is written. The helper in agent_core makes them safe (retrieval.py:88–99). Second known cross-file-helper misruling pattern (first: "drain blocks the loop").
