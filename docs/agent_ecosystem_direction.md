# Agent Ecosystem Direction

Takeaway notes from the 2026-04-13 conversation that started from the "Mode Dispatcher" section of `index_problem_and_future_direction_talk.md`. The framing shifted during that discussion: this is not a PAL feature, it is a separate ecosystem project where PAL becomes one agent among several.

Move this doc into the new ecosystem repo when that project starts. Leaving it in PAL for now so it travels with the context that produced it.

## Reframe

"Mode dispatcher" was the wrong name. Modes imply one agent changing hats. What is actually wanted is **N independent agents, each with its own daemon, socket, commands, and wisdom pool**, sharing a small amount of infrastructure. A launcher selects which agent you talk to for a given session.

PAL remains PAL. RE Lab, Coding, General, and any future specialties are their own agents built on the same plumbing.

## Decisions already made

- **Vault access**: shared. Each agent biases toward its own subtree at retrieval time but is not walled off. Rationale: optimization, not isolation. Cross-domain questions should still work.
- **Wisdom**: per-agent, fully separate. No tagging scheme across a shared pool. Each agent learns in its own context.
- **Commands**: per-agent. `/import`, `/lint`, `/learn` stay with PAL. Other agents define their own command sets appropriate to their work.
- **Architecture**: N independent daemons, one per agent. Not one daemon with mode-switching. Each agent is its own Python package with its own socket.
- **Build order**: shared infrastructure gets extracted before the second agent ships, not after. Factoring after four agents have copy-pasted the daemon skeleton is expensive.

## What becomes shared infrastructure (`agent-core` or similar)

These exist in PAL today but are not PAL-specific:

- Inference client (already remote)
- Socket protocol, NDJSON plumbing, daemon skeleton (`pal/protocol.py`, `pal/client.py`, the `Daemon` class scaffold)
- Vault read + retrieval client (with query-time filtering for per-agent bias)
- Wisdom and learning machinery (same code, distinct storage per agent)
- General-purpose utilities: frontmatter parsing, chunker, fetcher, HTML-to-markdown converter

## What stays agent-specific

For PAL specifically:

- System prompt (Oracle / librarian framing)
- Categorizer prompts and category set
- `/import`, `/lint`, `/learn`, `/think`, `/scratch`
- The summary-to-article compile pipeline
- PAL-specific wisdom pool

Other agents define their own equivalents.

## Open questions (revisit when ecosystem project starts)

1. **Monorepo vs. separate repos.** One repo with agents as subpackages iterates faster but blurs the API boundary between shared and agent-specific code. Separate repos force a cleaner API at the cost of dependency management overhead. Recommendation: monorepo during extraction phase, split once the `agent-core` API stabilizes.
2. **Inter-agent communication.** If RE Lab agent discovers something general-purpose, does it write to PAL's vault directly, or message PAL's socket and say "please compile this"? Direct write is simpler; socket message keeps each agent as the sole writer of its own state and makes cross-agent interactions auditable. Leaning toward socket messages if the pattern comes up often, direct writes if it is rare.
3. **MVP second agent.** Building a framework in the abstract produces a framework that fits exactly one agent. The second consumer is what makes the extraction honest. RE Lab is the likely candidate given the separate `docs/re_lab_direction.md` notes, but choose before starting the extraction.
4. **Launcher UX.** CLI startup screen with numbered options was the sketch. For Discord or other adapters, probably a per-session command. Worth deciding whether the launcher is a separate process or a thin wrapper that exec()s the chosen agent's CLI.
5. **Proactive polling awareness.** The autonomous-plan-continuation work (PAL memory) assumes one daemon. Under the ecosystem, each agent polls its own queue. Dispatcher or scheduler layer above agents? Or each agent responsible for its own wake-up schedule?

## Relationship to current PAL work

- **Summarizer title cleanup**: still a PAL concern, not an ecosystem concern. Land it before the deterministic indexer fix so the regenerated index has clean titles.
- **Deterministic indexer rebuild** (from `index_problem_and_future_direction_talk.md` Problem 2): still a PAL concern. Wisdom-pool-per-agent does not change how PAL's own `_index.md` gets built.
- **File watcher for vector index** (Problem 1 from the same doc): lives in `inference-server`, which is already shared infrastructure. Solving it there benefits every future agent for free.

The ecosystem extraction is a parallel track to the current PAL roadmap, not a blocker for it. Keep shipping PAL improvements; the extraction starts when you have a concrete second agent to build.
