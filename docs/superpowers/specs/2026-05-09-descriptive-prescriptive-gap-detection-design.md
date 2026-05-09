---
title: PAL Descriptive vs Prescriptive Gap Detection
date: 2026-05-09
type: design
status: draft
---

# PAL Descriptive vs Prescriptive Gap Detection

## Purpose

When the user asks PAL an action-oriented question ("how do I get Frida hooking JNI calls?"), the vault may have descriptive coverage (what JNI is, what Frida does) without prescriptive content (specific command sequences, code examples that work). The user currently identifies this gap manually and prompts PAL for more research. This design teaches PAL to identify the gap on its own response and offer the next step.

The change is a small behavioral one: a wisdom rule that PAL self-applies after answering. It does not require code changes to PAL or `agent_core`, does not require new tools, and does not change retrieval or compile pipelines. It is an interlocutor-axis improvement (per `2026-05-09-pal-research-assistant-assessment.md` section 3) that fits the existing wisdom-injection mechanism.

## Background

PAL's research workflow today:

1. User asks question in chat.
2. PAL searches the vault, summarizes existing coverage.
3. User reads the response and decides whether it answers the question or whether more research is needed.
4. If descriptive-only when prescriptive was wanted, user prompts another research round.

Step 3 is the manual gap detection. The user reported (2026-05-09 brainstorm) that PAL's summaries are good but the descriptive/prescriptive judgment is fully manual. They wanted PAL to flag the mismatch and offer to fix it.

## The change

Add one wisdom rule at `_wisdom/pal/descriptive-prescriptive-gap-detection.md`. Wisdom rules are injected into every PAL system prompt per the existing wisdom layer (`project_pal_overview` memory). PAL applies the rule each turn.

The rule has three parts:

1. **Trigger.** PAL evaluates whether the user's question is action-oriented. Use explicit phrasings only. Do not infer action intent from noun-phrase questions.
   - Action signals: "how do I", "how to", "how can I", "walk me through", "step by step", "what's the command for", "what's the best way to", "show me how", "tell me how to"
   - Negative cases (do not fire): "what is X", "what does X mean", "tell me about X", historical or conceptual queries, and noun-phrase questions like "Frida JNI hooking?" or "GDB remote attach?" (even though the user might have intended action, the rule treats these as ambiguous and stays silent)

2. **Self-evaluation.** After producing the answer, PAL classifies its own response:
   - **Prescriptive** if it contains: command-line snippets, code blocks with executable content, numbered steps tied to specific actions, named tool invocations with arguments, copy-pasteable examples
   - **Descriptive** if it contains: definitions, conceptual explanations, general principles, historical/architectural overviews, paraphrased summaries without concrete steps
   - Mixed responses count as prescriptive when the prescriptive content directly addresses the user's action question; descriptive otherwise

3. **Flag.** If trigger matched and self-evaluation came back descriptive, append a compact end-of-response note:

   > (Note: this is descriptive coverage. The vault doesn't have prescriptive examples for `<specific need>`. Want me to research that?)

   Where `<specific need>` is the action the user actually asked about, named specifically. Not generic ("more details") but specific ("the actual jadx-gui CLI flags for batch decompilation").

The rule fires at most once per response. PAL does not flag on follow-up turns when the user has already chosen to proceed with descriptive content (e.g., user says "that's fine, keep going" after the first flag).

## Why a wisdom rule, not a system-prompt change

Wisdom rules are the existing mechanism for behavioral principles. They inject into every system prompt automatically. Editing or removing a wisdom rule is a vault file edit, no redeploy needed. This makes the rule cheap to iterate on if the trigger or phrasing turns out to be miscalibrated.

If the rule underfires (PAL doesn't flag when it should) or overfires (PAL flags on every response), the iteration cost is one file edit. If after a few weeks the rule is stable but needs more specificity than wisdom rules comfortably support, the rule can be promoted to a dedicated section in PAL's main system prompt. Start at the lighter weight; promote only if needed.

## The wisdom rule body

The actual content of `_wisdom/pal/descriptive-prescriptive-gap-detection.md`:

```markdown
---
title: Descriptive Prescriptive Gap Detection
created: '2026-05-09T00:00:00+00:00'
---
When the user asks an action-oriented question, evaluate your own answer
before sending. Action signals are explicit phrasings only: "how do I", "how
to", "how can I", "walk me through", "step by step", "what's the command for",
"best way to", "show me how", "tell me how to". Do not infer action intent
from noun-phrase questions like "Frida JNI hooking?" or "GDB remote attach?";
treat those as ambiguous and stay silent.

If the trigger fires and your answer is mostly descriptive (definitions,
concepts, what-is) rather than prescriptive (specific commands, executable
steps, copy-pasteable examples), append a compact end-of-response note:

> (Note: this is descriptive coverage. The vault doesn't have prescriptive
> examples for [name the specific action the user asked about]. Research that?)

Name the specific gap, not a generic one. Fire at most once per response. Do
not fire on follow-up turns where the user has already chosen to proceed with
the descriptive material.
```

This is one wisdom rule, self-contained. Follows the existing wisdom-rule shape (per the audit's section on the learn/promote loop: "Each is 2-6 lines, behaviorally specific, non-overlapping"). It runs slightly longer than the typical wisdom rule because the trigger list is concrete; if that proves cumbersome we can promote the rule to a system-prompt section per the iteration plan.

## Rollback and iteration

**Rollback:** delete `_wisdom/pal/descriptive-prescriptive-gap-detection.md`. The rule stops injecting on the next session. No code change needed.

**Iteration triggers:**

- *Underfires:* user notices an action question got a descriptive answer and PAL didn't flag. Edit the rule to broaden the trigger signals or to be more aggressive about classifying mixed responses as descriptive.
- *Overfires:* PAL flags on every response, becomes noise. Edit the rule to tighten the trigger (require explicit action phrases, drop the imperative-verb fallback) or to require the response to be unambiguously descriptive (high bar for prescriptive content).
- *Wrong calls:* PAL flags when the answer was actually prescriptive enough, or misses when it was descriptive. Edit the rule to clarify the classification heuristics.

If after two iteration cycles the rule is still miscalibrating, escalate to a system-prompt section as approach C from the brainstorm.

## Validation

No automated test. Validation is qualitative and based on user observation:

1. **Smoke check:** ask PAL an action question with known-descriptive vault coverage (e.g., "How do I run Frida against a packed iOS app?" if vault has Frida overviews but not iOS-specific scripts). Expected: PAL answers, then flags the gap.
2. **Negative check:** ask PAL a what-is question (e.g., "What is Frida?"). Expected: PAL answers without flagging.
3. **Mixed check:** ask an action question with prescriptive vault coverage. Expected: PAL answers with the prescriptive content and does not flag (no gap).

If smoke check 1 fires correctly and check 2 stays silent, the rule is calibrated. If check 3 over-flags, the classification heuristic is too aggressive about calling responses descriptive.

Track results informally in `_learning/pal/` if a calibration learning emerges, then promote to `_wisdom/` if it's stable.

## What this does not do

- Does not change retrieval. The rule fires after retrieval has happened and the response is composed.
- Does not auto-trigger more research. PAL's flag is an offer, not an action. The user explicitly approves the next research round (preserving the existing consent gate).
- Does not classify articles in the vault. The classification is response-level, not content-level. If article-level descriptive/prescriptive tagging is later wanted, that's approach B from the brainstorm and a separate workstream.
- Does not address the tone tic ("It's not just X, it's Y"). That is a separate prompt-audit concern, queued in `project_phase2_inference_investigation`.

## Cross-references

- Holistic assessment: `docs/superpowers/specs/2026-05-09-pal-research-assistant-assessment.md` (section 3 active-interlocutor axis)
- Memory: `project_pal_overview.md` (wisdom-injection mechanism), `project_phase2_inference_investigation.md` (prompt audit follow-up that includes the tone tic)
- Audit reference: `docs/pal-vault-audit-2026-05-09.md` (learn/promote loop section, on wisdom-rule shape)
