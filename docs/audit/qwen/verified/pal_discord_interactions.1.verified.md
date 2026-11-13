# Verified: pal_discord_interactions p1+p2 (task 1/3)

## Ruled out

p1:97, p1:148, p1:213, p1:266, p1:303, p1:341, p1:374, p1:405 — `timeout=None` is intentional design for indefinite proposal interaction windows; no expiry needed since proposals are resolved by button click or bot restart.
p1:437 — `timeout=300` (5 min) is a bounded, sensible default; not unbounded or absurd.
p1:586 — `asyncio.sleep(timeout_seconds)` is bounded to 15 min (default param) and is the core of a scheduled cleanup task, not a blocking stall.
p2-A:590 — `except Exception: pass` in fire-and-forget `_expire` cleanup; the task is already abandoned and the exception is a failed edit of a message that may already be gone.
p2-A:607 — `except Exception as exc:` logs via `logger.exception`; appropriate error handling.
p2-A:613 — `except Exception:` logs via `logger.exception` as a second-failure path; appropriate.
p2-A:617 — `except Exception: pass` is the third fallback in `_post_proposal` after the proposal already failed and the decline was sent; best-effort channel notification, already logged upstream.
p2-A:762 — `except Exception as exc:` logs via `logger.exception`; appropriate.
p2-A:770 — `except Exception:` logs via `logger.exception`; appropriate.
p2-A:778 — `except Exception: pass` is the third fallback in `_handle_batch_fallback_proposal` after the skip message already failed; same pattern as p2-A:617.
p2-A:796 — `except discord.HTTPException:` sets `self.current_thread = None` to trigger the channel fallback path; intended behavior per docstring.
p2-A:805 — `except discord.HTTPException: pass` falls through to channel send; intended fallback.
p2-A:809 — `except discord.HTTPException: pass` is the final fallback for progress messages; best-effort only.
p2-B:606, p2-B:610, p2-B:616, p2-B:761, p2-B:767, p2-B:775, p2-B:790, p2-B:803, p2-B:808 — All are `await` calls (not `create_task`); not fire-and-forget.
p2-C:592 — `asyncio.create_task(_expire())` is fire-and-forget, but the exception handler at line 590 (`except Exception: pass`) is appropriate for a best-effort cleanup task that edits a message which may no longer exist.

## Notes

none
