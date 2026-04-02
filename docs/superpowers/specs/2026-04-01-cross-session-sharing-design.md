# Cross-Session Context Sharing — Design Specification

**Date:** 2026-04-01
**Author:** Blake Ledden
**Status:** Approved
**Extends:** Recall Plugin v2.0.0

## Overview

Adds cross-session context sharing to the recall plugin. Sessions can opt in to watch other sessions for highlights — valuable findings flagged by Claude or detected automatically. Highlights are lightweight tag-pointers, not full context, making checks token-efficient. Full context is pulled on demand via existing `/recall search`.

**Target users:** Power users running parallel Claude sessions on related work (e.g., multiple kernel optimization projects).

## Goals

1. **Explicit highlight creation** — Claude proactively flags findings worth sharing
2. **Opt-in auto-detection** — heuristic-based highlight creation for users who want automation
3. **Lightweight connections** — sessions watch other sessions via opt-in links
4. **Configurable check frequency** — explicit-only (default), decay polling (opt-in: 7th prompt → 30th)
5. **Configurable delivery** — silent queue (default) or system message injection
6. **Natural language support** — Claude translates "watch session X" into `/recall connect`
7. **Token efficiency** — checks return tag names + one-liners, full context pulled on demand

## Non-Goals

- Real-time push notifications between sessions
- Automatic connection detection (user must opt in)
- Semantic/embedding-based relevance matching

---

## Database Schema

Two new tables in the existing `recall.db`.

```sql
CREATE TABLE IF NOT EXISTS highlights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    summary         TEXT NOT NULL,
    exchange_idx    INTEGER,
    tags            TEXT NOT NULL,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(session_id, summary)
);

CREATE TABLE IF NOT EXISTS connections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher_session TEXT NOT NULL REFERENCES sessions(session_id),
    target_session  TEXT NOT NULL REFERENCES sessions(session_id),
    topic           TEXT NOT NULL,
    check_mode      TEXT NOT NULL DEFAULT 'explicit',
    check_counter   INTEGER DEFAULT 0,
    check_interval  INTEGER DEFAULT 7,
    last_checked_at TEXT,
    delivery_mode   TEXT NOT NULL DEFAULT 'silent',
    created_at      TEXT NOT NULL,
    UNIQUE(watcher_session, target_session)
);

CREATE INDEX IF NOT EXISTS idx_highlights_session ON highlights(session_id);
CREATE INDEX IF NOT EXISTS idx_highlights_created ON highlights(created_at);
CREATE INDEX IF NOT EXISTS idx_connections_watcher ON connections(watcher_session);
CREATE INDEX IF NOT EXISTS idx_connections_target ON connections(target_session);
```

### Design Decisions

- `highlights.tags` is comma-separated text — simple, tags are for display/matching only
- `connections.check_counter` increments every prompt, resets on check
- Decay formula: `check_interval = min(30, check_interval + 3)` — starts at 7, grows toward 30
- `delivery_mode` controls injection vs silent queue
- `auto_highlight` config stored in `sessions.metadata` JSON blob (avoids new table)

---

## Highlight Creation

### Path A: Explicit (Default)

Claude runs `/recall highlight "one-line summary"`.

Script behavior:
1. Get current session's auto-tags from DB
2. Insert into `highlights` with `source='explicit'`, tags from session auto-tags
3. Link to most recent exchange via `exchange_idx`
4. Return confirmation message

### Path B: Auto-Detection (Opt-In)

Enabled via `/recall config auto_highlight true`. Stored in `sessions.metadata`.

When enabled, the `prompt_submit` hook runs a heuristic after inserting exchanges:
- Scan `assistant_text` for solution signals: "the fix is", "this works because", "the solution", "try using", "the issue was"
- If 2+ signals found in a single exchange, auto-create highlight with `source='auto'`
- Summary = first 100 chars of assistant text
- Tags = session's current auto-tags

Both paths write to the same `highlights` table. Downstream is unified.

### Behavioral Instruction (recall.md)

```
When you produce a finding, solution, or technique that would be valuable
to other sessions working on related problems, proactively run:
/recall highlight "one-line summary of the finding"

Examples of highlight-worthy findings:
- A bug fix or workaround that others might hit
- A performance technique that transfers across kernels
- An architectural insight about the codebase
- A configuration or flag that solved a problem
```

---

## Connection Management

### Commands

```
/recall connect <session-id> "topic description"
/recall connect --latest "topic description"
/recall disconnect <session-id>
```

### Connect behavior

1. Validate target session exists in DB
2. For `--latest`: `SELECT * FROM sessions WHERE project_hash = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1`
3. Insert into `connections` with default `check_mode='explicit'`, `delivery_mode='silent'`
4. Return: `*Connected to session {id[:8]}... — watching for highlights about "{topic}"*`

### Natural Language Support (recall.md)

```
When the user mentions another session or asks you to watch/monitor/track
another session's work, translate that into a /recall connect command.
For example: "keep an eye on session abc123, they're doing kernel work"
→ /recall connect abc123 "kernel work"
```

### Disconnect

Deletes the connection row.

---

## Configuration

```
/recall config check_mode decay           # enable decay polling (default: explicit)
/recall config delivery_mode inject       # inject as system messages (default: silent)
/recall config auto_highlight true        # enable heuristic detection (default: false)
```

- `check_mode` and `delivery_mode` are per-connection (stored on `connections` row)
- `auto_highlight` is per-session (stored in `sessions.metadata` JSON)

When setting `check_mode` or `delivery_mode`, applies to all connections for the current session. A future enhancement could allow per-connection overrides.

---

## Check & Delivery

### Check Logic (in prompt_submit hook)

After exchange indexing and auto-tagging:

1. Query `connections WHERE watcher_session = current_session_id`
2. For each connection:
   - If `check_mode = 'explicit'`: skip
   - If `check_mode = 'decay'`: increment `check_counter`
     - If `check_counter >= check_interval`: run check, reset counter, decay interval
     - Decay: `check_interval = min(30, check_interval + 3)`
3. Check query: `SELECT * FROM highlights WHERE session_id = target_session AND created_at > last_checked_at`
4. Update `last_checked_at` to now

### Delivery

If `delivery_mode = 'silent'`:
- No output. Highlights queue for `/recall inbox`.

If `delivery_mode = 'inject'`:
- Build system message (pointers only):
```
[Cross-session] New from session {id[:8]}... ({topic}):
  - "warp shuffle eliminates divergence" [reduction, warp-divergence]
  - "shared memory tiling for 16x16 blocks" [shared-memory, tiling]
Use /recall search <tag> --session <id> for full context.
```
- Return via `{"systemMessage": msg}`

### /recall inbox

Shows all unchecked highlights from connected sessions:

```
**Inbox** (3 new highlights)

From session abc123... (CUDA reduction kernels):
  - "warp shuffle eliminates divergence" [reduction] — 2:30 pm
  - "shared memory tiling approach" [shared-memory] — 3:15 pm

From session def456... (Blackwell dispatch):
  - "threadgroup size 512 optimal for Blackwell" [blackwell] — 4:00 pm

Use /recall search <keyword> --session <id> to pull full context.
```

Updates `last_checked_at` on all connections after display.

---

## Command Interface

### New Commands

```
/recall highlight "summary"                 Create a highlight for connected sessions
/recall connect <session-id> "topic"        Watch another session
/recall connect --latest "topic"            Watch most recent active session (same project)
/recall disconnect <session-id>             Stop watching a session
/recall inbox                               Show unchecked highlights from connections
/recall config <key> <value>                Configure check_mode, delivery_mode, auto_highlight
```

### Updated recall.md Behavioral Instructions

Two new sections added to recall.md:
1. Proactive highlighting instruction (when to run `/recall highlight`)
2. Natural language connect translation (when user mentions other sessions)

---

## File Structure

### New Files

```
scripts/highlight.py            # Highlight creation (explicit + auto-detection)
scripts/manage_connections.py   # Connect, disconnect, inbox, config
tests/test_highlight.py
tests/test_manage_connections.py
```

### Modified Files

```
scripts/db.py                   # Add highlights + connections tables, CRUD functions
hooks/prompt_submit.py          # Add connection check logic after indexing
commands/recall.md              # Add new commands + behavioral instructions
.claude-plugin/plugin.json      # Bump to 2.1.0
```

### Unchanged Files

```
scripts/auto_tagger.py
scripts/manage_tags.py
scripts/manage_sessions.py
scripts/fetch_exchanges.py
scripts/show_index.py
scripts/utils.py
hooks/post_compact.py
hooks/session_end.py
hooks/hooks.json
```

---

## db.py Functions to Add

```python
# Highlights
insert_highlight(conn, session_id, summary, tags, source, exchange_idx=None)
get_highlights(conn, session_id, since=None, limit=20)
get_highlights_for_connections(conn, watcher_session)  # unchecked highlights across all connections

# Connections
insert_connection(conn, watcher_session, target_session, topic, check_mode, delivery_mode)
get_connections(conn, watcher_session)
update_connection_check(conn, connection_id, check_counter, check_interval, last_checked_at)
delete_connection(conn, watcher_session, target_session)

# Session config
get_session_config(conn, session_id, key)       # reads from sessions.metadata JSON
set_session_config(conn, session_id, key, value) # writes to sessions.metadata JSON
```

---

## Constants

```python
# Decay parameters
DECAY_INITIAL_INTERVAL = 7
DECAY_MAX_INTERVAL = 30
DECAY_INCREMENT = 3

# Auto-detection
SOLUTION_SIGNALS = [
    "the fix is", "this works because", "the solution",
    "try using", "the issue was", "the problem was",
    "resolved by", "fixed by", "the answer is",
]
SOLUTION_SIGNAL_THRESHOLD = 2

# Highlight limits
HIGHLIGHT_SUMMARY_MAX_CHARS = 100
```

---

## Testing Strategy

- Highlight CRUD: insert, get, get-since, dedup
- Connection CRUD: insert, get, delete, update check state
- Decay logic: counter increment, interval growth, cap at 30
- Auto-detection heuristic: triggers on solution signals, skips non-solution exchanges
- Check & delivery: silent mode queues, inject mode returns systemMessage
- Inbox: shows unchecked, updates last_checked_at
- Integration: full lifecycle — connect, highlight in target, check fires, inbox shows results
- Target: ~30 new tests
