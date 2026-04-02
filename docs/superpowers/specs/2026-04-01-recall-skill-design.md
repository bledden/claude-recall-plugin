# Recall Assistant Skill — Design Specification

**Date:** 2026-04-01
**Author:** Blake Ledden
**Status:** Approved
**Extends:** Recall Plugin v2.1.0 → v2.2.0

## Overview

A SKILL.md file that teaches Claude to proactively use the recall plugin — detecting context loss, suggesting highlights for connected sessions, translating natural language into recall commands, and prompting inbox checks. The skill is opt-in: it ships with the plugin but does nothing until explicitly enabled.

## Goals

1. **Context-loss detection** — Claude recognizes when it has lost context and suggests `/recall` commands
2. **Proactive highlighting** — Claude suggests (or auto-runs) `/recall highlight` when it produces transferable findings
3. **Connection suggestions** — Claude translates natural language about other sessions into `/recall connect`
4. **Inbox awareness** — Claude suggests checking `/recall inbox` when working on topics that overlap with connected sessions
5. **Fully opt-in** — skill does nothing until `skill_enabled` is true; opt-in features stay opt-in
6. **User-configurable signals** — detection signal categories can be toggled individually

## Non-Goals

- Auto-enabling any opt-in feature (auto_highlight, decay, inject delivery)
- Running connect/disconnect/inbox without user confirmation
- Replacing the existing `recall.md` command — the skill supplements it

---

## Activation

The skill lives at `skills/recall-assistant/SKILL.md` inside the plugin.

Users enable it explicitly:
```
/recall config skill_enabled true
```

When `skill_enabled` is false (default), the skill file is loaded by Claude but every behavior section is gated behind a config check. Claude reads the skill but takes no proactive action.

---

## Configuration

All stored in `sessions.metadata` via existing `set_session_config`.

| Key | Default | Options | Controls |
|---|---|---|---|
| `skill_enabled` | `false` | `true`/`false` | Master switch for all skill behaviors |
| `detection_signals` | `explicit,behavioral,temporal` | Comma-separated subset | Which context-loss signal categories are active |
| `auto_run_highlight` | `false` | `true`/`false` | Whether Claude runs `/recall highlight` without asking |

---

## Behavior 1: Context-Loss Detection

**Gate:** `skill_enabled = true`

### Signal Categories

**Explicit signals** (`detection_signals` includes `explicit`):
- User says: "didn't we already...", "what was that thing about...", "earlier you said...", "I think we talked about...", "we discussed this before", "remind me what..."
- Claude's response: suggest a specific `/recall search <term>` or `/recall last10`

**Behavioral signals** (`detection_signals` includes `behavioral`):
- Claude is about to ask a question that was likely already answered
- Claude is repeating advice without realizing
- Claude contradicts something from earlier
- Claude's response: pause and suggest `/recall search` before continuing

**Temporal signals** (`detection_signals` includes `temporal`):
- PostCompact nudge just fired
- Session has 50+ exchanges
- Session spans multiple days
- Claude's response: suggest `/recall` to recover earlier context

### Response Pattern

Claude does NOT silently run commands. It explains what it's doing:
> "I may have lost context on this — let me check the recall index."
> Then suggests: `/recall search <relevant-term>` or `/recall last10`

---

## Behavior 2: Proactive Highlighting

**Gate:** `skill_enabled = true` AND session has active connections (checked via `/recall` context)

### Default (auto_run_highlight = false)

Claude recognizes transferable findings and suggests:
> "This warp shuffle fix could be useful to your other sessions. Want me to flag it? I'd run: `/recall highlight "warp shuffle eliminates divergence in reduction kernel"`"

User confirms or declines. Claude does not run the command without confirmation.

### Opt-in (auto_run_highlight = true)

Claude runs `/recall highlight` without asking, then briefly notes:
> "Flagged for connected sessions: warp shuffle eliminates divergence in reduction kernel"

### What Qualifies as Highlight-Worthy

- Solves a bug or identifies a root cause
- Recommends a technique that transfers across contexts
- Discovers a non-obvious configuration or flag
- Produces an architectural insight

### What Does NOT Qualify

- Routine answers, clarifications, exploratory discussion
- Code formatting, simple Q&A
- Incremental progress on a specific task

---

## Behavior 3: Connection Suggestions

**Gate:** `skill_enabled = true`

When the user mentions another session or parallel work:
> "Sounds like that other session is working on related kernel optimization. Want me to connect? I'd run: `/recall connect <id> "kernel optimization"`"

When the user doesn't provide a session ID:
> "Want me to connect to the most recent active session in this project? `/recall connect --latest "kernel optimization"`"

**Never auto-runs** connect or disconnect. Always suggests and waits for confirmation.

### Natural Language Triggers

- "keep an eye on session X"
- "watch what the other session finds"
- "my other session is working on..."
- "share context with session X"
- "link these sessions"

---

## Behavior 4: Inbox Awareness

**Gate:** `skill_enabled = true` AND session has active connections

Claude suggests checking inbox when:
- User starts working on a topic that overlaps with tags from connected sessions
- Beginning of a work block after idle time
- User asks about a topic that a connected session has highlighted

Response pattern:
> "You have active connections — worth checking `/recall inbox` for any relevant findings before we dig into this."

**Never auto-runs** inbox. Always suggests.

---

## File Structure

```
skills/
  recall-assistant/
    SKILL.md
```

Single file. No additional scripts, hooks, or tables needed. The skill is pure behavioral instruction.

### Modified Files

- `.claude-plugin/plugin.json` — bump to 2.2.0
- `commands/recall.md` — add `config skill_enabled` to the config section
- `scripts/manage_connections.py` — ensure `config skill_enabled` and `config detection_signals` are handled

---

## SKILL.md Structure

```markdown
---
name: recall-assistant
description: Proactive context recovery, highlight sharing, and session linking for the recall plugin. Enable with /recall config skill_enabled true.
---

# Recall Assistant

[Config gate section — check skill_enabled before any behavior]

## Context-Loss Detection
[Explicit/behavioral/temporal signal rules with config gate per category]

## Proactive Highlighting  
[Highlight suggestion rules with auto_run_highlight gate]

## Connection Suggestions
[Natural language translation rules]

## Inbox Awareness
[Topic overlap detection and suggestion rules]

## Available Commands Reference
[Quick reference of all /recall commands so Claude knows the full surface]
```

---

## Testing Strategy

The skill is a markdown instruction file — it can't be unit tested. Testing is behavioral:

1. **Manual eval: context loss** — start a long session, verify Claude suggests `/recall` when context seems lost
2. **Manual eval: highlighting** — produce a finding, verify Claude suggests highlighting it
3. **Manual eval: connections** — mention another session, verify Claude suggests connecting
4. **Manual eval: inbox** — have a connected session with highlights, verify Claude suggests checking inbox
5. **Manual eval: opt-in gate** — verify skill does nothing when `skill_enabled` is false
6. **Manual eval: signal config** — disable `behavioral` signals, verify only explicit/temporal fire
7. **Manual eval: auto_run** — enable `auto_run_highlight`, verify Claude runs without asking

---

## Version

Plugin bumps from 2.1.0 to 2.2.0 with this feature.
