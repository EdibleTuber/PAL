# Verified: pal_tools_vault p2+p3 (task 1/2)

## Ruled out

p2-A:41 -- Exception caught, WARN logged, None returned; docstring matches; trigger_reindex already internally guarded (established context) -- no silent swallow.

p2-A:305 -- Catches only three specific exceptions (FileNotFoundError, FileExistsError, ValueError) from move_single; returns error JSON with str(exc) -- proper structured error, not swallowing.

p2-A:387 -- Catches Exception from git_rm; returns error JSON with reason -- proper structured error; git_rm already deleted the file, but the handler correctly reports failure to the caller.

p2-A:392 -- Catches Exception from git_commit after git_rm succeeded; returns "deleted_uncommitted" status with warning -- proper structured error; the file is deleted (git rm staging), commit failed, handler communicates this accurately.

p2-A:543 -- Catches Exception from git_commit; restores original content via resolved.write_text(original_text) at line 545 before returning error JSON -- proper rollback pattern; file state is preserved on commit failure.

p3:135 -- wiki.write_article is a synchronous call; established context confirms wiki.git_commit bails without .git and is no-op when clean -- no concurrency risk.

p3:136 -- Same wiki.guarded pattern; git_commit is safe per established context (pal/wiki.py:176-200).

p3:243 -- Same as p3:135; wiki.write_article in CreateFile.run -- no concurrency risk.

p3:244 -- Same as p3:136; wiki.git_commit in CreateFile.run -- guarded.

p3:304 -- reorganizer.move_single is a synchronous call; no await between read and write -- no lost-update risk.

p3:310 -- wiki.git_commit in MoveFile.run -- guarded per established context.

p3:386 -- wiki.git_rm in DeleteFile.run -- synchronous; no concurrency issue.

p3:391 -- wiki.git_commit in DeleteFile.run -- guarded per established context.

p3:539 -- resolved.write_text is the file content write; followed by guarded wiki.git_commit at line 542; if commit fails, line 545 restores original -- proper error-handling pattern, not a lost-update.

p3:542 -- wiki.git_commit (guarded per established context); no await between read and write.

p3:545 -- resolved.write_text(original_text) is the rollback; only executes inside the except block after git_commit failed -- safe recovery, not a concurrency issue.

## Notes

- All 5 p2-A leads were mislabeled as "swallowing" exceptions. None swallow: each handler either logs (p2-A:41) or returns a structured error JSON to the LLM caller (p2-A:305, 387, 392, 543). Tool handlers that return structured error strings/dicts to the LLM are proper handling -- the model can retry with corrected input.
- All 11 p3 leads flagged wiki operations as state writes with "locks_held: none". Per established context, wiki.git_commit is guarded (bails without .git, no-op when clean) and wiki.write_article/git_rm are synchronous operations with no await between read and write. No lost-update or interleaving risk exists.
- The established context claims about trigger_reindex (agent_core/retrieval.py:74-105) being internally best-effort and wiki.git_commit being guarded were relied upon; the .venv was not accessible for independent verification of these cross-file claims.
- Synthesis note (2026-11-09): report written by the subagent itself (first task where the write tool was NOT denied). Synthesis spot-checked p2-A:41, 305, 387, 392 and the ReplaceInFile write/commit/rollback sequence (vault.py:539–545) against source — all rulings confirmed as written.
