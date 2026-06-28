---
name: recall-assistant
description: Proactive context recovery, highlight sharing, and session linking for the recall plugin. Detects context loss, suggests /recall commands, flags findings for connected sessions, and translates natural language into recall operations. Enable with /recall config skill_enabled true.
---

# Recall Assistant

You have access to the **recall plugin** — a cross-session, cross-project conversation recall system backed by SQLite. This skill guides you on when and how to use it proactively.

**IMPORTANT: All behaviors below are gated.** Before taking any proactive action described here, check whether the user has enabled this skill by verifying `skill_enabled` is true. If not enabled, do nothing — the user interacts with the plugin only via explicit `/recall` commands.

---

## Context-Loss Detection

When `skill_enabled` is true, watch for signals that you have lost earlier conversation context. The user may configure which signal categories are active via `detection_signals` (default: all three).

### Explicit Signals

> **Hook-backed (reliable):** when `skill_enabled` is true, the `UserPromptSubmit`
> hook already detects these explicit phrases *deterministically* and injects a
> `[Recall]` suggestion — so this signal no longer depends on you noticing. When
> you see that injected suggestion, act on it (run the appropriate recall). The
> behavioral and temporal signals below remain model-driven.

Watch for the user saying things like:
- "didn't we already discuss..."
- "what was that thing about..."
- "earlier you said..."
- "I think we talked about..."
- "we discussed this before"
- "remind me what..."
- "you mentioned something about..."

When you detect these: suggest a specific recall command based on what the user is looking for.

Example:
> I may have lost context on the warp divergence discussion. Let me check — I'll run `/recall search "warp divergence"` to recover what we covered.

### Behavioral Signals

Watch for your own behavior indicating context loss:
- You are about to ask a question that the user likely already answered earlier in the session
- You are repeating advice or explanations you gave before without realizing
- You are about to contradict something you said earlier

When you detect these: pause, acknowledge the potential gap, and suggest a recall command before continuing.

Example:
> Before I answer — I have a feeling we may have covered this earlier and I may have lost that context. Let me check `/recall last10` to make sure I'm not repeating myself.

### Temporal Signals

Watch for environmental cues:
- A PostCompact nudge just appeared in your context (the system message starting with "[Context Compacted]")
- The conversation has been going for a long time (50+ exchanges)
- The conversation spans multiple days

When you detect these: proactively suggest recovering context before proceeding with complex work.

Example:
> This session has been going a while and context may have been compacted. Want me to run `/recall search` on the current topic to make sure I haven't lost anything important?

---

## Proactive Highlighting

When `skill_enabled` is true and you know the session has active connections to other sessions:

### When to Suggest Highlighting

You should consider highlighting a finding when you:
- Solve a bug or identify a root cause
- Recommend a performance technique that would transfer to other contexts
- Discover a non-obvious configuration, flag, or workaround
- Produce an architectural insight about a codebase
- Find a solution that other sessions working on related problems would benefit from

Do NOT suggest highlighting for:
- Routine answers, clarifications, or exploratory discussion
- Code formatting or simple Q&A
- Incremental progress on a task-specific problem
- Information that is only relevant to the current session's specific context

### Default Behavior (auto_run_highlight = false)

Suggest the highlight and wait for confirmation:

> This warp shuffle fix could be useful to your other sessions working on kernel optimization. Want me to flag it? I'd run:
> `/recall highlight "warp shuffle eliminates divergence in reduction kernel"`

### Auto-Run Behavior (auto_run_highlight = true)

Run the highlight command directly, then briefly note what you did:

> Flagged for connected sessions: "warp shuffle eliminates divergence in reduction kernel"

---

## Connection Suggestions

When `skill_enabled` is true, translate natural language about other sessions into recall commands.

### Triggers

When the user says things like:
- "keep an eye on session X"
- "watch what the other session finds"
- "my other session is working on..."
- "share context with session X"
- "link these sessions"
- "that session is doing kernel work, stay connected"

### Response

Suggest the appropriate connect command:

If the user provides a session ID:
> I'll connect to that session. Running: `/recall connect abc123 "kernel optimization"`

If the user doesn't provide a session ID:
> Want me to connect to the most recent active session in this project?
> `/recall connect --latest "kernel optimization"`

**Never auto-run** connect or disconnect. Always suggest and wait for the user to confirm.

---

## Inbox Awareness

When `skill_enabled` is true and the session has active connections:

### When to Suggest Checking Inbox

- The user starts working on a topic that likely overlaps with connected sessions' work
- The user begins a new work block after being idle
- The user asks about a topic that a connected session may have highlighted

### Response

> You have active connections — it might be worth checking `/recall inbox` for any relevant findings from your other sessions before we dive in.

**Never auto-run** inbox. Always suggest.

---

## Available Commands Reference

When suggesting recall commands, use these:

### Core Recall
- `/recall` — interactive menu
- `/recall last5` / `/recall last10` (`lastN` — any positive N) — recent exchanges
- `/recall search <keyword>` — search current session
- `/recall search <keyword> --all` — search all sessions in project
- `/recall search <keyword> --global` — search across all projects
- `/recall search <keyword> --project <name>` — search specific project (unanchored substring path match)
- `/recall around <time>` — exchanges around a time

### Session Management
- `/recall sessions` — list all sessions
- `/recall sessions --all` — list sessions across all projects
- `/recall sessions --project <name>` — list sessions in specific project (unanchored substring path match)
- `/recall session <id> last10` — browse a past session
- `/recall stats` — storage statistics

### Tagging
- `/recall tag <name>` — tag current session
- `/recall tags` — list all tags
- `/recall tags --project <hash>` — list tags for a project HASH (exact match; distinct from `sessions --project <name>`, which takes a name/path)
- `/recall search --tag <name>` — find by tag

### Maintenance
- `/recall prune --session <id>` — delete a specific session
- `/recall prune --before <date>` — delete sessions before a date
- `/recall export --session <id>` — export a session (always emits JSON)

### Cross-Session Sharing
- `/recall highlight "summary"` — flag a finding
- `/recall connect <session-id> "topic"` — watch a session
- `/recall connect --latest "topic"` — watch most recent active session
- `/recall disconnect <session-id>` — stop watching
- `/recall inbox` — view new highlights from connections

### Configuration
- `/recall config skill_enabled true` — enable this skill
- `/recall config detection_signals explicit,behavioral,temporal` — configure which signals are active
- `/recall config auto_run_highlight true` — auto-flag findings without asking
- `/recall config check_mode decay` — enable decay-based polling
- `/recall config delivery_mode inject` — auto-inject highlights as system messages
- `/recall config auto_highlight true` — enable heuristic highlight detection
