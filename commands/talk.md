---
description: "rig/talk — a conversational mode that takes what you say, works out what you meant, and bridges it to whichever rig flow can do it. Multi-turn, short spoken replies, and confirmation before anything consequential."
argument-hint: "[what you want to say (optional)] [--autonomous]"
---

# rig/talk — conversational mode

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal). This command is the way into a conversation; the engine itself lives in the skill and is not repeated here. talk handles only the front half — turning natural language into a structured rig invocation — and keeping the conversation going.

Then converse in the `talk-assistant` voice, following the `talk-loop` instruction. What was said:

```
$ARGUMENTS
```

With no argument, open with a short "what do you need?".

## What it does

Handle what was said per `facets/instructions/talk-loop`: answer chat and questions briefly and directly; for a request that means a rig action, normalise it, enumerate the available `/rig:*` commands to classify it, confirm in one line, delegate to that command through the engine, and report back in short spoken sentences. **Anything consequential — a write, a push, a merge, a capture — is confirmed first**; low-risk things like reading state or `--plan` happen immediately. "that's enough", "exit", or "stop" ends it.

## Flags

- `--autonomous` — skip confirmation for low-risk actions so it flows. Confirmation for writes, pushes, merges, and captures is not lifted.

## Examples

```
/rig:talk just review what I changed, lightly
/rig:talk evaluate this document with the installed recipe ./notes/input.md
/rig:talk                                   # no argument -> opens with "what do you need?"
```

## Not in v1

Voice I/O (TTS and STT, **swappable so the user can choose**) and a hands-free loop are a later layer. v1 is text conversation only; see the spec.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```

**One exception**: talk's own conversational turns — short chat that has not been delegated to a flow — carry no header, so the spoken register survives.
