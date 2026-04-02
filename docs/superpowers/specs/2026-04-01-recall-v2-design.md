# Recall Plugin v2.0.0 — Design Specification

**Date:** 2026-04-01
**Author:** Blake Ledden
**Status:** Approved

## Overview

Recall Plugin v2.0.0 evolves the plugin from a single-session JSON-based context recovery tool into a multi-session, cross-project, SQLite-backed recall system. It survives `/clear`, handles concurrent sessions, leverages new Claude Code hook events, and introduces hybrid auto/manual tagging for cross-project context discovery.

**Target users:** Power users running long, multi-day, multi-session workflows — especially those running parallel Claude sessions on related work (e.g., multiple kernel optimization projects).

## Goals

1. **Survive `/clear`** — session data persists when the user clears context
2. **Cross-session search** — search across all sessions in a project or globally
3. **Cross-project search** — find context across entirely separate projects
4. **PostCompact nudge** — automatically orient Claude after context compaction
5. **Hybrid tagging** — auto-extracted + manual tags for organizing and discovering context
6. **Concurrent session safety** — multiple Claude sessions can read/write simultaneously
7. **Zero new dependencies** — SQLite via Python stdlib, nothing to install
8. **Full backward compatibility** — all v1.0.1 commands work identically

## Non-Goals

- Semantic/embedding-based search (architecture supports it later, not built now)
- Auto-pruning (user-controlled only)
- MCP server architecture (SQLite can be wrapped later if needed)
- LLM-powered summarization at index time

---

## Storage Layer

### Database Location

```
~/.claude/context-recall/recall.db
```

Single SQLite database, WAL journal mode enabled at creation for concurrent access.

### Schema

```sql
CREATE TABLE sessions (
    session_id      TEXT PRIMARY KEY,
    project_path    TEXT NOT NULL,
    project_hash    TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    exchange_count  INTEGER DEFAULT 0,
    transcript_path TEXT,
    byte_offset     INTEGER DEFAULT 0,
    metadata        TEXT
);

CREATE TABLE exchanges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    idx             INTEGER NOT NULL,
    timestamp       TEXT NOT NULL,
    preview         TEXT NOT NULL,
    user_text       TEXT,
    assistant_text  TEXT,
    UNIQUE(session_id, idx)
);

CREATE TABLE tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag             TEXT NOT NULL,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    exchange_idx    INTEGER,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(tag, session_id, exchange_idx)
);

CREATE VIRTUAL TABLE exchanges_fts USING fts5(
    user_text,
    assistant_text,
    preview,
    content=exchanges,
    content_rowid=id
);
```

### Design Decisions

- `project_path` stored as full path for human-readable cross-project results
- `project_hash` aligns with Claude Code's internal project hashing
- `metadata` JSON blob provides extensibility without schema migrations
- FTS5 virtual table indexes text without data duplication
- WAL mode allows concurrent readers + single writer without blocking
- `byte_offset` on sessions table preserves incremental parsing optimization from v1.0.1

---

## Hook Architecture

### hooks.json

```json
{
  "description": "Recall hooks - indexes conversations, nudges after compaction, finalizes sessions",
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/prompt_submit.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/post_compact.py",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/session_end.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### UserPromptSubmit (prompt_submit.py)

Same logic as v1.0.1 with two changes:

1. **Write target:** SQLite via `db.py` instead of `index.json`
2. **Auto-tagging:** After inserting new exchanges, run lightweight term extraction

Flow:
1. Read JSON from stdin (session_id, transcript_path, user_prompt)
2. Look up session in DB — create row if new session_id
3. Check transcript size vs. stored byte_offset
4. Parse new transcript lines from offset (same incremental approach)
5. Build exchanges, INSERT into DB
6. Update session's byte_offset and exchange_count
7. Run auto-tagger on new exchanges
8. If user_prompt contains "/recall", log event to recall-events.log
9. Output `{}` (or `{"systemMessage": "..."}` if needed)

### PostCompact (post_compact.py)

Fires after Claude Code compacts the conversation context.

Flow:
1. Read JSON from stdin (session_id)
2. Query DB: current session exchange count, last 5 previews
3. Query DB: total exchange count for current project
4. Query DB: top 5 auto-tags for current session
5. Format nudge message (under 500 chars)
6. Output `{"systemMessage": "<nudge>"}`

Nudge format:
```
[Context Compacted] This session has {N} exchanges indexed.
{M} total exchanges across this project's history.
Recent topics: {tag1}, {tag2}, {tag3}
Last 5 exchanges:
  #{idx} - "{preview}"
  ...
Use /recall to recover full conversation context.
```

### SessionEnd (session_end.py)

Fires when the session terminates.

Flow:
1. Read JSON from stdin (session_id)
2. Set `ended_at` timestamp on the session row
3. Ensure any buffered state is flushed
4. Output `{}`

---

## Auto-Tagging

### Algorithm

1. Concatenate `user_text` + `assistant_text` from new exchanges
2. Tokenize: split on whitespace and punctuation, lowercase
3. Filter: remove tokens < 3 characters
4. Remove stopwords (hardcoded ~150 common English words)
5. Count term frequency across the session's full lifetime
6. Terms appearing 3+ times that pass the "technical term" heuristic get tagged
7. Cap at 10 auto-tags per session

### Technical Term Heuristic

A token is considered "technical" if any of:
- Contains `-`, `_`, or digits (e.g., `warp-divergence`, `fp16`, `sm_90`)
- Is not in a secondary "generic programming terms" filter list (e.g., `function`, `variable`, `error`, `file`)
- Appears in both user and assistant text (indicates a shared concept, not filler)

### Incremental Behavior

- On first prompt of a session: bootstrap counts from all existing exchanges
- On subsequent prompts: only process new exchanges, merge into running counts
- Tags are additive — once a tag meets the threshold, it persists
- Re-evaluation happens each prompt but is O(vocabulary_size), which is negligible

### Examples

Kernel optimization session auto-tags:
`warp-divergence`, `shared-memory`, `occupancy`, `coalescing`, `threadblock`, `blackwell`, `reduction`, `softmax`, `matmul`

Filtered out:
`function`, `code`, `error`, `looks`, `think`, `should`, `problem`

---

## Command Interface

### Existing Commands (backward compatible)

```
/recall                             Interactive menu (index + options)
/recall last5                       Last 5 exchanges, current session
/recall last10                      Last 10 exchanges, current session
/recall around 2pm                  Exchanges around 2pm
/recall around "jan 5 2pm"          Exchanges around specific date+time
/recall search <keyword>            Search current session
```

All behave identically to v1.0.1. Default scope is current session.

### New: Cross-Session & Cross-Project Search

```
/recall search <keyword> --all              Search all sessions, current project
/recall search <keyword> --global           Search all sessions, all projects
/recall search <keyword> --project <name>   Search sessions in specific project
                                            (substring match on project path,
                                             e.g., "triton" matches
                                             "/Users/.../triton-metal")
```

Cross-project results grouped by project and session:

```
### Results for "warp divergence" (3 matches across 2 projects)

**triton-metal** - Session Jan 15 (exchange #34) [2:30 pm]
  User: "the warp divergence in the reduction kernel..."
  Assistant: "The issue is that threads in the same warp..."

**cuda-kernels** - Session Jan 22 (exchange #12) [11:15 am]
  User: "seeing warp divergence on the Blackwell..."
  ...
```

### New: Session Management

```
/recall sessions                    List all sessions, current project
                                    (date range, exchange count, top tags)
/recall sessions --all              List sessions across all projects
/recall session <id> last10         Browse specific past session
```

### New: Tagging

```
/recall tag <name>                  Tag current session (manual)
/recall tag <name> #<exchange>      Tag specific exchange
/recall tags                        Show all tags, current project
/recall search --tag <name>         Find exchanges/sessions with tag
```

### New: Maintenance

```
/recall stats                       Disk usage, session/exchange counts per project
/recall prune --session <id>        Delete specific session
/recall prune --before 2026-01-01   Delete sessions before date
/recall export --session <id> --json  Dump session to JSON
```

If `/recall stats` shows storage growing large, the output includes a suggestion to use `/recall prune`.

---

## Migration from v1.0.1

Automatic, non-destructive, runs on first `UserPromptSubmit` of the new version.

### Detection

`prompt_submit.py` checks:
1. Does `recall.db` exist? If not → create with schema
2. Does `index.json` exist? If yes → migrate

### Migration Steps

1. Read `index.json` fully
2. Insert a `sessions` row from the index metadata
3. Insert all exchanges into `exchanges` table
4. Populate FTS5 index
5. Rename `index.json` → `index.json.migrated` (backup, not deleted)
6. Log migration to stderr for observability

### Rollback

If a user needs to go back to v1.0.1:
- `index.json.migrated` can be renamed back to `index.json`
- `recall.db` is ignored by v1.0.1

---

## `/clear` Survival

### Problem (v1.0.1)

`index.json` is keyed by `session_id`. When `/clear` creates a new session, the hook sees a mismatched session_id and overwrites the index. All prior context is lost.

### Solution (v2.0.0)

Each session is a row in the `sessions` table. When `/clear` fires:
1. New session gets a new `session_id`
2. Hook creates a new row in `sessions`
3. Old session's data is untouched
4. `/recall search <keyword> --all` searches both old and new session

No special logic needed — this is the natural behavior of the relational model.

---

## Concurrent Session Safety

Multiple Claude sessions (e.g., three kernel optimization sessions) writing to the same `recall.db`:

- **WAL mode** allows any number of concurrent readers
- **WAL mode** allows one writer at a time; other writers queue briefly (SQLite handles this internally with a default 5-second busy timeout)
- Each session writes only to its own `session_id` rows — no data contention
- Cross-session reads see other sessions' exchanges in real-time (WAL provides snapshot isolation for readers)

No file locking code needed — SQLite manages this.

---

## File Structure

```
claude-recall-plugin/
├── .claude-plugin/
│   └── plugin.json                    # v2.0.0
├── commands/
│   └── recall.md                      # Extended with new subcommands
├── hooks/
│   ├── hooks.json                     # UserPromptSubmit, PostCompact, SessionEnd
│   ├── prompt_submit.py               # Incremental indexing + auto-tagging
│   ├── post_compact.py                # Nudge after compaction
│   └── session_end.py                 # Finalize session
├── scripts/
│   ├── db.py                          # SQLite connection, schema, migration, queries
│   ├── auto_tagger.py                 # Term extraction, threshold logic
│   ├── utils.py                       # Formatting, parsing (I/O removed)
│   ├── show_index.py                  # Paginated display (queries DB)
│   ├── fetch_exchanges.py             # Fetch exchanges (queries DB, --all/--global)
│   ├── manage_sessions.py             # List, prune, export, stats
│   └── manage_tags.py                 # Tag, untag, list tags
├── tests/
│   ├── test_db.py                     # Schema, migration, WAL, FTS5, concurrent access
│   ├── test_auto_tagger.py            # Extraction, thresholds, incremental updates
│   ├── test_prompt_submit.py          # Hook behavior (adapted from v1.0.1 tests)
│   ├── test_post_compact.py           # Nudge generation
│   ├── test_session_end.py            # Session finalization
│   ├── test_fetch_exchanges.py        # DB queries, cross-session, cross-project
│   ├── test_show_index.py             # Paginated display from DB
│   ├── test_manage_sessions.py        # Session listing, pruning, export
│   ├── test_manage_tags.py            # Tag CRUD, search by tag
│   ├── test_utils.py                  # Formatting/parsing (slimmed)
│   └── integration_test.py            # Full lifecycle: create, search, cross-session, migrate
├── README.md
├── LICENSE
└── .gitignore
```

### Module Responsibilities

**db.py** — Single point of contact for SQLite:
- `get_connection()` — WAL-mode connection, auto-create schema
- `migrate_from_json()` — v1 → v2 migration
- `insert_session()`, `end_session()`, `list_sessions()`, `prune_sessions()`
- `insert_exchanges()`, `get_exchanges()`, `search_exchanges()`, `search_exchanges_global()`
- `insert_tag()`, `get_tags()`, `search_by_tag()`
- `get_stats()` — disk usage, counts

**auto_tagger.py** — Tagging logic:
- `extract_terms()` — tokenize, filter stopwords, count frequency
- `select_tags()` — apply threshold + technical term heuristic
- `update_session_tags()` — merge new tags into DB

**utils.py** — Slimmed from v1.0.1:
- Retains: `extract_text_content()`, `make_preview()`, `truncate_text()`, `parse_time_query()`, `parse_date_time_query()`, `format_timestamp()`, `format_date()`, `format_short_date()`, `find_exchanges_by_time()`, `search_in_text()`
- Removed: `load_index()`, `save_index()` (replaced by db.py)

---

## Constants

```python
# Unchanged from v1.0.1
PREVIEW_LENGTH = 80
MAX_CHARS_PER_MESSAGE = 1000
MAX_TOTAL_CHARS = 8000
PAGE_SIZE = 20
AROUND_TIME_WINDOW = 5

# New
MAX_AUTO_TAGS_PER_SESSION = 10
AUTO_TAG_MIN_FREQUENCY = 3
NUDGE_MAX_CHARS = 500
NUDGE_PREVIEW_COUNT = 5
DB_BUSY_TIMEOUT_MS = 5000
```

---

## Testing Strategy

- All existing v1.0.1 tests adapted to use DB instead of JSON — same assertions, different storage backend
- New tests use in-memory SQLite (`:memory:`) for speed, with select tests using real files for WAL/concurrent behavior
- Integration tests cover: create session → add exchanges → `/clear` → new session → cross-session search → migration from v1.0.1 JSON
- Concurrent access tests: two threads writing to same DB simultaneously
- Target: maintain 90+ tests, aiming for ~120+ with new coverage
