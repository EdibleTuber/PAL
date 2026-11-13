# Verified: pal_discord_interactions p3+p4 (task 2/3)

(Saved by the synthesis session from the egpu reviewer's final message; the
reviewer's `write` tool returned "Permission denied: edit" in this task.)

## F1 [A][Low] Unguarded `client.send()` in batch-fallback button callbacks — line 750

The `send_choice` callback defined at lines 749–752 calls
`client.send(BatchFallbackApprovalMessage(...))` with no try/except wrapper.
It is invoked from `BatchFallbackView._on_retry` / `_on_main` / `_on_skip`
(lines 463–479), also unguarded. If `client.send()` raises, the
`BatchFallbackApprovalMessage` never reaches the backend and the
`interaction.response.edit_message` that follows never runs; discord.py's
interaction handler catches the callback exception and answers the user with
an error response. The batch-fallback flow then waits for a decision it never
receives.

Synthesis-session verification (read 455–484, 740–784): confirmed. Two
nuances the subagent did not reach:
- `client.send` raising means the processor's backend connection is broken,
  in which case the user's whole session is already dead and a re-click
  re-attempts the send, so the marginal impact of the missing guard is small.
- The same author wrapped the analogous best-effort skip send in the same
  function (`discord_interactions.py:766–773`), and the standard proposal path
  wraps its send in `discord_adapter.py` — the button path is the unguarded
  outlier.

Leads: p4:65, p4:47, p4:48, p4:49, p4:50, p4:51, p4:52 (all mislabeled
kind=http; see Notes).

## Ruled out

**p3 leads (all 6):** all are dict operations or simple assignments on the
asyncio event loop with no `await` between read and write; asyncio makes
non-suspending sequences atomic.
- p3:587 — `dict.pop()` after awaited sleep; single writer, no concurrent write race
- p3:631–633 — three sequential writes with no await between them
- p3:795, p3:797 — `thread_id` assignment and `None` assignment, no await between

**p4 leads — local object construction (not external effects):** lines
creating discord.py objects (Embed, View, Button, Modal, TextInput) are purely
local Python objects; the HTTP happens inside discord.py's machinery.
- p4:15, p4:421, p4:550 — import/class definition, not an effect
- p4:64, p4:123, p4:174, p4:239, p4:292, p4:329, p4:361, p4:394, p4:484 — `discord.Embed(...)`
- p4:97, p4:148, p4:213, p4:266, p4:303, p4:341, p4:374, p4:405 — `discord.ui.View(timeout=None)`
- p4:98, p4:104, p4:110, p4:149, p4:155, p4:161, p4:214, p4:220, p4:226, p4:267,
  p4:273, p4:279, p4:304, p4:310, p4:316, p4:342, p4:348, p4:375, p4:381, p4:406,
  p4:412 — `discord.ui.Button(...)`
- p4:437 — `super().__init__(timeout=timeout)`
- p4:442, p4:446, p4:450 — `discord.ui.Button(...)`
- p4:459–461 — `self.add_item(...)` local list manipulation
- p4:499, p4:524 — `discord.ui.Modal(...)`
- p4:503, p4:510, p4:529 — `modal.add_item(discord.ui.TextInput(...))`

**p4 leads — Discord API calls via discord.py, already wrapped in try/except:**
- p4:606, p4:607–619 — `channel.send()` in `_post_proposal` + fallbacks
- p4:610 — `client.send()` in `_post_proposal` error handler, wrapped
- p4:616 — `channel.send()` fallback in error handler, wrapped
- p4:761 — `channel.send()` in `_handle_batch_fallback_proposal`, wrapped
- p4:767, p4:768 — `client.send()` skip fallback, wrapped
- p4:775 — `channel.send()` fallback, wrapped
- p4:790 — `create_thread()` wrapped
- p4:803 — `current_thread.send()` wrapped
- p4:808 — `channel.send()` fallback wrapped

**p4 leads — not external effects:**
- p4:60 — `await asyncio.sleep(timeout_seconds)` is event-loop scheduling, not HTTP

## Notes

- **Quote/schema mismatch on all 71 p4 leads:** every p4 lead in this file is
  labeled `kind: "http"` targeting "Discord API", but none of the cited lines
  make direct HTTP calls — most are local discord.py object construction; the
  real Discord API calls go through discord.py methods (wrapped, see Ruled
  out). Line 60 (`await asyncio.sleep`) is also labeled "http" but is local
  scheduling. Inventory quality issue: the p4 pass over-labeled this file.
- The p3 `awaits_between_read_and_write` field was accurate for all 6 leads
  (none have an await between a read and a write).
