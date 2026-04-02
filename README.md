# Claude Recall Plugin v2.1.0

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that persists conversation context across sessions, `/clear` commands, and compaction events — with cross-session search, tagging, cross-session highlight sharing, and observability.

> **Marketplace Status:** This plugin has been submitted to the official Claude Code plugins repository and is awaiting approval.
>
> **PR:** [anthropics/claude-code#16680](https://github.com/anthropics/claude-code/pull/16680)
>
> **Pre-built Marketplace:** [claude-recall-marketplace](https://github.com/bledden/claude-recall-marketplace) (for easy installation until approved)

---

## Requirements

- **Claude Code** 2.0.x or 2.1.x (see breaking change note below for 2.1.x), or **Claude Cowork** (macOS desktop app)
- **Python 3.6+** (for hook and script execution)

---

## UPDATE: Claude Code 2.1.x Breaking Change

**As of Claude Code 2.1.x, local plugins no longer persist across sessions.** This is an undocumented breaking change from 2.0.x behavior. See [issue #17089](https://github.com/anthropics/claude-code/issues/17089).

Custom plugins now **require a marketplace structure** to work reliably with the VSCode extension. The `--plugin-dir` flag only works with the CLI, not VSCode.

---

## Installation

### Claude Cowork (macOS Desktop App)

**From GitHub:**
1. Open the Claude Desktop app
2. Navigate to the **Cowork** tab
3. Click **"Plugins"** in the left sidebar
4. Click **"Add from GitHub"**
5. Enter: `https://github.com/bledden/claude-recall-plugin`

**From zip file:**
1. **[Download claude-recall-plugin.zip](https://github.com/bledden/claude-recall-plugin/releases/latest/download/claude-recall-plugin.zip)**
2. In the Cowork **Plugins** sidebar, click **"Upload plugin"**
3. Select the downloaded `claude-recall-plugin.zip` file

The plugin will appear in your Cowork plugins list. Invoke with `/recall` during a Cowork session.

### Claude Code: Option 1 - Pre-Built Marketplace (Recommended for VSCode)

This is the only reliable method for the VSCode extension until the plugin is approved in the official marketplace.

```bash
claude plugin marketplace add https://github.com/bledden/claude-recall-marketplace
claude plugin install recall@recall-local
```

The plugin will now persist across sessions in both CLI and VSCode.

<details>
<summary><strong>Alternative: Build Your Own Marketplace</strong></summary>

If you prefer to create your own local marketplace:

**Step 1: Clone and set up marketplace structure**

```bash
# Clone this repo
git clone https://github.com/bledden/claude-recall-plugin.git

# Create a marketplace wrapper
mkdir -p claude-recall-marketplace/.claude-plugin
mkdir -p claude-recall-marketplace/plugins
cp -R claude-recall-plugin claude-recall-marketplace/plugins/recall
```

**Step 2: Create the marketplace manifest**

Create `claude-recall-marketplace/.claude-plugin/marketplace.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "recall-local",
  "version": "1.0.0",
  "description": "Local marketplace for the recall plugin",
  "owner": {
    "name": "your-name",
    "email": "your-email@example.com"
  },
  "plugins": [
    {
      "name": "recall",
      "description": "Recover conversation context when Claude loses track",
      "source": "./plugins/recall",
      "category": "productivity"
    }
  ]
}
```

**Step 3: Register and install**

```bash
claude plugin marketplace add /path/to/claude-recall-marketplace
claude plugin install recall@recall-local
```

</details>

### Claude Code: Option 2 - Shell Alias (CLI Only)

This method works for the terminal but **does not work with the VSCode extension**.

```bash
git clone https://github.com/bledden/claude-recall-plugin.git
```

**For Zsh (default on macOS):**
```bash
echo "alias claude='claude --plugin-dir /path/to/claude-recall-plugin'" >> ~/.zshrc
source ~/.zshrc
```

**For Bash:**
```bash
echo "alias claude='claude --plugin-dir /path/to/claude-recall-plugin'" >> ~/.bashrc
source ~/.bashrc
```

### Claude Code: Option 3 - Plugin Install Command (Not Recommended)

```bash
claude plugins install https://github.com/bledden/claude-recall-plugin
```

> **Warning:** This method does not reliably persist in Claude Code 2.1.x. The plugin may disappear after restarting. Use Option 1 instead.

---

## Migration from v1.0.1

Migration is automatic. On the first prompt after upgrading to v2.0.0, the hook migrates your existing `index.json` into SQLite and renames the file to `index.json.migrated`. No manual steps required.

**Rollback:** If you need to go back to v1.0.1, rename `index.json.migrated` to `index.json`. v1.0.1 ignores `recall.db`.

---

## Quick Start

When Claude seems to have lost context, run:

```
/recall:recall
```

This will:
1. Show you a timestamped index of all exchanges in your session
2. Present a menu asking what you'd like to recall
3. Fetch and display the exchanges you select
4. Summarize where you left off

---

## Full Command Reference

### Core Commands

```
/recall                             Interactive menu (index + options)
/recall last5                       Last 5 exchanges, current session
/recall around 2pm                  Exchanges around a time
/recall search <keyword>            Search current session
```

### Cross-Session and Cross-Project Search

```
/recall search <keyword> --all              Search all sessions, current project
/recall search <keyword> --global           Search across ALL projects
/recall search <keyword> --project <name>   Search a specific project by name
```

### Session Management

```
/recall sessions                    List all sessions (current project)
/recall sessions --all              List sessions across all projects
/recall session <id> last10         Browse a specific past session
```

### Tagging

```
/recall tag <name>                  Tag the current session
/recall tag <name> #<exchange>      Tag a specific exchange by number
/recall tags                        Show all tags
/recall search --tag <name>         Find sessions and exchanges by tag
```

### Cross-Session Sharing

```
/recall highlight "summary"              Flag a finding for connected sessions
/recall connect <session-id> "topic"     Watch another session for highlights
/recall connect --latest "topic"         Watch most recent active session (same project)
/recall disconnect <session-id>          Stop watching a session
/recall inbox                            View new highlights from connected sessions
```

### Configuration

```
/recall config check_mode decay          Enable decay-based polling (default: explicit)
/recall config delivery_mode inject      Auto-inject highlights as system messages (default: silent)
/recall config auto_highlight true       Enable heuristic highlight detection (default: false)
```

### Maintenance

```
/recall stats                               Storage statistics
/recall prune --session <id>                Delete a specific session
/recall prune --before 2026-01-01           Delete all sessions before a date
/recall export --session <id> --json        Export a session to JSON
```

### Time Format Support

The plugin understands various time formats:

| Format | Example |
|--------|---------|
| 12-hour | `2pm`, `2:30pm`, `2:30 pm` |
| 24-hour | `14:30`, `14:00` |
| With date (month day) | `jan 5 2pm`, `dec 25 10am` |
| With date (numeric) | `1/5 2pm`, `12/25 10:30am` |
| Relative | `yesterday 2pm`, `today 10am` |

---

## Features

### 1. /clear Survival

Context is persisted to SQLite before `/clear` executes. After clearing, your full exchange history is still searchable and retrievable. Clearing the context window no longer means losing the record of what happened.

### 2. Cross-Session Search

Search across all sessions in a project with `--all`, or across every project you've worked in with `--global`. Results include session ID, project, timestamp, and a content preview.

```
/recall search "auth flow" --all
/recall search "triton kernel" --global
```

### 3. PostCompact Nudge

After Claude Code compacts the conversation, the plugin automatically injects a brief context-recovery hint: a prompt reminding Claude to re-anchor on what was happening. No manual `/recall` needed after compaction.

### 4. Auto-Tagging

Technical terms are extracted automatically from each exchange — function names, file paths, identifiers, command names. These feed into FTS5 search so you can find exchanges without remembering the exact wording.

### 5. Manual Tagging

Apply your own tags to sessions or individual exchanges for cross-project discovery:

```
/recall tag auth-refactor
/recall tag metal-backend #42
```

Tags are queryable across all sessions and projects.

### 6. Cross-Session Context Sharing

Share findings between parallel sessions working on related problems. Highlights are lightweight tag-pointers, not full context — checks are token-efficient. Full context is pulled on demand via `/recall search`.

**Two highlight creation paths:**

- **Explicit** (default): Claude proactively runs `/recall highlight "summary"` when it produces a finding worth sharing — a bug fix, performance technique, architectural insight, or config that solved a problem.
- **Auto-detect** (opt-in): Enable with `/recall config auto_highlight true`. The hook scans assistant responses for solution signals (e.g., "the fix is", "the solution", "resolved by"). If 2+ signals appear in one exchange and the response is 25+ words, a highlight is created automatically with `source='auto'`.

**Check frequency:**

By default, connections are `check_mode=explicit` — highlights only appear when you run `/recall inbox`. Enable decay polling with `/recall config check_mode decay`: starts checking every 7th prompt, grows by 3 each time, caps at every 30th prompt.

**Delivery modes:**

- `silent` (default): highlights queue silently, view with `/recall inbox`
- `inject`: highlights are injected as system messages automatically

**Natural language support:** Claude translates "watch session abc123 for kernel work" into `/recall connect abc123 "kernel work"` automatically.

```
/recall connect abc123 "CUDA reduction kernels"
/recall connect --latest "Blackwell dispatch work"
/recall inbox
```

### 7. Concurrent Session Safety

Multiple Claude sessions in the same project write to the same database without conflicts. SQLite WAL mode allows concurrent reads and serializes writes safely.

### 8. SQLite Storage

All context is stored in a single SQLite database (`recall.db`) with FTS5 for full-text search. No JSON files, no external dependencies beyond Python's built-in `sqlite3` module.

### 9. Timestamped Conversation Index

Every exchange is indexed with its timestamp:

```
Session started: Jan 5, 2026 at 9:00 AM (Jan 5 - Jan 7)
Total exchanges: 117

Showing page 1 of 6 (most recent first):

Jan 7:
#117 [5:13 pm] "root@dendritic-distillation:~/dendritic# ls..."
#116 [2:49 pm] "Yes, give me the command to kick that off"

Jan 6:
#115 [1:33 pm] "It looks like the experiment is complete..."
```

### 10. Full-Content Search

Search looks in both user prompts and assistant responses, not just preview text. Multi-word queries use AND logic — both terms must appear anywhere in the exchange. Force exact phrase matching by quoting: `search "the fix is"`.

```
/recall search dimension
/recall search "auth flow"
```

Results show up to 10 most recent matches, grouped by date.

### 11. Observability Logging

Every `/recall` invocation is logged:

```
~/.claude/recall-events.log
```

Log format:
```
2026-01-05T16:45:00+00:00 | session=abc123 | exchanges=72 | CONTEXT_RECALL_TRIGGERED
```

### 12. Pagination

Long sessions are paginated (20 exchanges per page):

```
Showing page 1 of 6 (most recent first)

Navigation:
- Show newer: page 1
- Show older: page 2
```

---

## Usage Examples

### Claude lost context mid-task

```
/recall last5
```

### Find a specific discussion from earlier

```
/recall search "API endpoint"
```

### Find something across all sessions in this project

```
/recall search "gradient checkpointing" --all
```

### Find a concept you worked on in a different project

```
/recall search "WAL mode" --global
```

### Return to work from yesterday afternoon

```
/recall around "yesterday 3pm"
```

### Tag a session for later reference

```
/recall tag metal-backend
```

### Browse a past session

```
/recall sessions
/recall session abc123 last10
```

### Clean up old sessions

```
/recall prune --before 2026-01-01
```

### Share a finding with a parallel session

```
/recall highlight "threadgroup size 512 optimal for Blackwell dispatch"
```

### Watch another session for highlights

```
/recall connect abc123 "CUDA reduction kernel work"
# or connect to the most recent active session in this project:
/recall connect --latest "Metal backend optimizations"
```

### Check for new highlights from connected sessions

```
/recall inbox
```

---

## How It Works

### Hooks

Three hooks are registered:

- **UserPromptSubmit** — Incrementally indexes each exchange into SQLite on every prompt. Handles `/clear` survival by committing before the clear executes. Also runs connection checks (decay mode) and auto-highlight detection (if enabled).
- **PostCompact** — After compaction, injects a context-recovery nudge so Claude re-anchors on the session state.
- **SessionEnd** — Finalizes the session record in the database.

### Storage

SQLite with FTS5 for full-text search and WAL mode for concurrent session safety. All data lives in a single file — no external services, no dependencies beyond Python's standard library.

### Tagging

A hybrid approach: auto-tags are extracted from exchange content at index time; manual tags are applied via `/recall tag`. Both are searchable via FTS5.

### Cross-Session Sharing

Highlights are created via two paths: explicit (Claude runs `/recall highlight`) or auto-detection (heuristic scan of assistant responses, opt-in). Connections are opt-in links between sessions stored in the `connections` table. The `UserPromptSubmit` hook checks connections on each prompt when `check_mode=decay` is set. Delivery is either silent (queue for `/recall inbox`) or injected as a system message.

---

## Data Storage

```
~/.claude/context-recall/recall.db     Single SQLite database (WAL mode, FTS5)
~/.claude/recall-events.log            Recall event log (unchanged from v1)
```

The database contains five tables:
- `sessions` — one row per session, with project, timestamps, and metadata (including per-session config like `auto_highlight`)
- `exchanges` — one row per exchange, with full user/assistant text
- `tags` — session and exchange-level tags
- `highlights` — findings flagged for sharing, linked to a session and exchange; `source` field distinguishes explicit vs auto-detected
- `connections` — opt-in links between sessions; stores `check_mode`, `check_interval`, `delivery_mode`, and `last_checked_at`

FTS5 virtual tables index exchange content for fast keyword search across any scope.

---

## Analyzing Recall Patterns

```bash
# View recent recall events
tail -20 ~/.claude/recall-events.log

# Count recalls per day
cut -dT -f1 ~/.claude/recall-events.log | uniq -c

# Find sessions with frequent recalls
grep -oP 'session=\K[^ ]+' ~/.claude/recall-events.log | sort | uniq -c | sort -rn

# Count total recalls
wc -l ~/.claude/recall-events.log
```

---

## Plugin Structure

```
claude-recall-plugin/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── commands/
│   └── recall.md                # The /recall command definition
├── hooks/
│   ├── hooks.json               # Hook configuration (UserPromptSubmit, PostCompact, SessionEnd)
│   └── save_context_snapshot.py # Incremental indexer + migration
├── scripts/
│   ├── utils.py                 # Shared utilities
│   ├── db.py                    # SQLite layer (FTS5, WAL, migrations, highlights, connections)
│   ├── show_index.py            # Paginated index display
│   ├── fetch_exchanges.py       # Fetch exchanges by query
│   ├── search.py                # Cross-session/project search
│   ├── sessions.py              # Session list, browse, prune, export
│   ├── tags.py                  # Tag management
│   ├── highlight.py             # Highlight creation (explicit + auto-detection)
│   └── manage_connections.py    # Connect, disconnect, inbox, config
└── tests/
    ├── test_utils.py
    ├── test_fetch_exchanges.py
    ├── test_show_index.py
    ├── test_save_context_snapshot.py
    ├── test_db.py
    ├── test_search.py
    ├── test_tags.py
    ├── test_highlight.py
    └── test_manage_connections.py
```

---

## Running Tests

```bash
cd claude-recall-plugin
python3 -m unittest discover -v tests/
```

---

## Contributing

1. Fork the repository
2. Make your changes
3. Run tests: `python3 -m unittest discover -v tests/`
4. Submit a pull request

---

## Known Limitations

- **Claude Cowork requires zip upload** — Cowork does not yet support marketplace installation; upload the plugin zip file manually via the Plugins sidebar
- **VSCode extension requires marketplace** — Due to a [breaking change in 2.1.x](https://github.com/anthropics/claude-code/issues/17089), the VSCode extension requires the marketplace installation method
- **No semantic/embedding search** — Search is keyword-based via SQLite FTS5; embedding/vector search is not supported yet
- **Cross-session sharing is polling-based** — No real-time push; highlights appear on the next check interval or via `/recall inbox`

---

## Uninstalling

**If installed via marketplace:**
```bash
claude plugin uninstall recall@recall-local
claude plugin marketplace remove recall-local
```

**If using shell alias:**
Remove the alias line from your `~/.zshrc` or `~/.bashrc`, then run `source ~/.zshrc` or `source ~/.bashrc`.

**Removing stored data:**
```bash
rm -rf ~/.claude/context-recall/
```

---

## Changelog

### 2.1.0 (April 2026)
- Cross-session context sharing via highlights and connections
- Explicit highlight creation: Claude proactively runs `/recall highlight "summary"` for shareable findings
- Opt-in auto-detect highlights: conservative heuristic (2+ solution signals, 25+ words) creates highlights automatically
- `/recall connect <session-id> "topic"` and `/recall connect --latest "topic"` to watch sessions
- `/recall disconnect` to stop watching a session
- `/recall inbox` to view unchecked highlights from connected sessions
- Configurable check frequency: `explicit` (default, manual inbox check only) or `decay` (polling: starts every 7th prompt, grows by 3 each check, caps at every 30th)
- Configurable delivery: `silent` (default, queue for inbox) or `inject` (system message auto-injection)
- `/recall config` command for `check_mode`, `delivery_mode`, and `auto_highlight` settings
- FTS5 multi-word search now uses AND logic — both terms must appear anywhere in the exchange (previously matched as exact phrase)
- Users can still force exact phrase matching with explicit quotes: `search "the fix is"`
- Bug fixes: `prune_session` FK violation on sessions with highlights; auto-detect summary deduplication collision

### 2.0.0 (April 2026)
- SQLite storage (`recall.db`) replaces JSON index files — FTS5 for search, WAL for concurrent access
- `/clear` survival — context persists across clear commands
- Cross-session search (`--all`) and cross-project search (`--global`, `--project`)
- PostCompact hook — automatic context recovery nudge after compaction
- Hybrid auto/manual tagging — automatic technical term extraction plus user-applied tags
- Session management commands: `sessions`, `session <id>`, `stats`, `prune`, `export`
- Concurrent session safety via WAL mode
- Automatic migration from v1.0.1 on first run

### 1.0.1 (January 2026)
- Added Claude Cowork support (zip upload via Plugins sidebar)
- Updated installation docs for Claude Code 2.1.x marketplace requirement
- Added pre-built marketplace for easier Claude Code installation
- Fixed Python 3.15 deprecation warning in date parsing
- Fixed all test imports for refactored module structure

### 1.0.0 (January 2026)
- Initial release
- Interactive `/recall` command with menu
- Quick commands: `last5`, `last10`, `search`, `around`
- Full-content search across user prompts and assistant responses
- Multi-day session support with date grouping
- Incremental indexing with byte offset tracking
- Observability logging to `~/.claude/recall-events.log`
- 91 unit tests

---

## License

MIT License - see LICENSE file for details.
