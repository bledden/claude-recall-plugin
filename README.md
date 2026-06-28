# Claude Recall Plugin v2.2.2

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that persists conversation context across sessions, `/clear` commands, and compaction events — with cross-session search, tagging, cross-session highlight sharing, and observability.

> **Marketplace Status:** Submitted via the official submission form at [claude.ai/settings/plugins/submit](https://claude.ai/settings/plugins/submit).
>
> **Pre-built Marketplace:** [claude-recall-marketplace](https://github.com/bledden/claude-recall-marketplace) (for easy installation until approved)

---

## Why Recall (vs. native Claude Code)

Claude Code ships with `/recap`, `/resume`, and a `memory/` directory. Recall is positioned around what those do **not** cover:

- **Cross-project full-text search (FTS5).** Native `/recap` and `/resume` operate within the current session/project. Recall indexes every exchange into SQLite FTS5 and searches across **all** sessions in a project (`--all`) or across **every** project (`--global`) — including past, closed sessions.
- **Tagging.** Apply manual tags to sessions or individual exchanges, plus automatic keyword extraction, then query them across projects (`/recall tag`, `/recall tags`, `/recall search --tag`). The native `memory/` directory is freeform notes, not a queryable tag index.
- **Highlight & connection sharing between parallel sessions.** Link two live sessions and share findings as lightweight highlights delivered to a connected session's inbox (`/recall connect`, `/recall highlight`, `/recall inbox`). Native Claude Code has no mechanism to push a finding from one session to another.

If you only need to re-anchor within the current session, native `/recap` / `/resume` may be enough. Recall is for cross-session, cross-project retrieval, tagging, and sharing.

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
  "version": "2.2.2",
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
/recall last10                      Last N exchanges (lastN — any positive N)
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
/recall sessions --project <name>   List sessions in a specific project by name
/recall session <id> last10         Browse a specific past session
```

> **`--project` matching:** For `search --project <name>` and `sessions --project <name>`, `<name>` is matched as an **unanchored substring** of the stored project path (`LIKE '%name%'`, case-sensitive) — any session whose project path contains the substring matches.
>
> **`tags --project` is different:** `/recall tags --project <hash>` expects a project **HASH** (exact match), *not* a name/path. This is distinct from `sessions --project <name>`, which takes a name/path.

### Tagging

```
/recall tag <name>                  Tag the current session
/recall tag <name> #<exchange>      Tag a specific exchange by number
/recall tags                        Show all tags
/recall tags --project <hash>       Show tags for a specific project HASH (exact match)
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
/recall config skill_enabled true        Enable the recall-assistant skill (default: false)
/recall config detection_signals explicit,behavioral,temporal   Configure context-loss detection signals
/recall config auto_run_highlight true   Auto-flag findings without asking (default: false)
```

### Maintenance

```
/recall stats                               Storage statistics
/recall prune --session <id>                Delete a specific session
/recall prune --before 2026-01-01           Delete all sessions before a date
/recall export --session <id>               Export a session to JSON (always JSON)
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

### 3. Compaction Nudge

When Claude Code compacts the conversation (via the `PreCompact` hook), the plugin automatically injects a brief context-recovery hint reminding Claude to re-anchor on what was happening. No manual `/recall` needed around compaction.

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

### 7. Recall Assistant Skill (Opt-In)

An optional skill that teaches Claude to proactively use the recall plugin. Enable with `/recall config skill_enabled true`.

When enabled, Claude will:
- **Detect context loss** — explicit phrases ("didn't we already...", "remind me what...") are caught *deterministically by the `UserPromptSubmit` hook*, which injects a `[Recall]` suggestion (so it no longer depends on the model noticing); behavioral signals (contradicting itself) and temporal signals (post-compaction) stay model-driven
- **Suggest highlighting** — when Claude produces a transferable finding, it suggests `/recall highlight` (or auto-runs it if `auto_run_highlight` is enabled)
- **Translate natural language** — "keep an eye on session abc123" becomes `/recall connect abc123 "..."`
- **Suggest inbox checks** — when working on topics that overlap with connected sessions

The skill is fully opt-in and respects all existing opt-in gates. It never auto-runs connect, disconnect, or inbox commands.

### 8. Concurrent Session Safety

Multiple Claude sessions in the same project write to the same database without conflicts (SQLite WAL mode allows concurrent reads and serializes writes). Each session also resolves *its own* identity from the native, per-session `CLAUDE_CODE_SESSION_ID` that Claude Code injects into every command — so `/recall` always returns the current session's history, never a concurrent session's, even with several sessions open at once.

### 9. SQLite Storage

All context is stored in a single SQLite database (`recall.db`) with FTS5 for full-text search. No JSON files, no external dependencies beyond Python's built-in `sqlite3` module.

### 10. Timestamped Conversation Index

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

### 11. Full-Content Search

Search looks in both user prompts and assistant responses, not just preview text. Multi-word queries use AND logic — both terms must appear anywhere in the exchange. Force exact phrase matching by quoting: `search "the fix is"`.

```
/recall search dimension
/recall search "auth flow"
```

Results show up to 10 most recent matches, grouped by date.

### 12. Observability Logging

Every `/recall` invocation is logged:

```
~/.claude/recall-events.log
```

Log format:
```
2026-01-05T16:45:00+00:00 | session=abc123 | exchanges=72 | CONTEXT_RECALL_TRIGGERED
```

### 13. Pagination

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

- **SessionStart** — Exports the session's env vars (a legacy fallback for resolving the current session/project; the native `CLAUDE_CODE_SESSION_ID` is preferred).
- **UserPromptSubmit** — Incrementally indexes each exchange into SQLite on every prompt. Handles `/clear` survival by committing before the clear executes. Also runs connection checks (decay mode), auto-highlight detection, and the deterministic proactive-recall suggestion (all if enabled).
- **PreCompact** — On compaction, injects a context-recovery nudge so Claude re-anchors on the session state.
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
│   └── plugin.json                  # Plugin metadata (v2.2.2)
├── commands/
│   └── recall.md                    # The /recall command definition
├── skills/
│   └── recall-assistant/
│       └── SKILL.md                 # Proactive recall assistant skill
├── hooks/
│   ├── hooks.json                   # Hook config (SessionStart, UserPromptSubmit, PreCompact, SessionEnd)
│   ├── session_start.py             # Exports session env vars (legacy fallback)
│   ├── prompt_submit.py             # Incremental indexer + auto-tagging + proactive recall
│   ├── post_compact.py              # Context recovery nudge (PreCompact)
│   └── session_end.py               # Session finalization
├── scripts/
│   ├── db.py                        # SQLite layer (FTS5, WAL, all CRUD)
│   ├── utils.py                     # Shared formatting and parsing utilities
│   ├── auto_tagger.py               # TF-based keyword extraction
│   ├── highlight.py                 # Highlight creation (explicit + auto-detect)
│   ├── manage_connections.py        # Connect, disconnect, inbox, config
│   ├── manage_tags.py               # Tag CRUD, search by tag
│   ├── manage_sessions.py           # Session list, prune, export, stats
│   ├── fetch_exchanges.py           # Fetch exchanges by query
│   └── show_index.py                # Paginated index display
├── tests/                          # 396 tests: unit + integration + skill evals
│                                    #   + stress (scale/concurrent/clear/sharing)
│                                    #   run with `python3 -m pytest -q` (see pytest.ini)
├── pytest.ini                      # Collects test_*.py AND stress_test_*.py
├── docs/
│   └── superpowers/
│       ├── specs/                   # Design specifications
│       └── plans/                   # Implementation plans
├── README.md
├── LICENSE
└── .gitignore
```

---

## Running Tests

```bash
cd claude-recall-plugin

# Full suite — unit, integration, and stress (396 tests)
# pytest.ini collects both test_*.py and stress_test_*.py
python3 -m pytest -q
```

---

## Contributing

1. Fork the repository
2. Make your changes
3. Run tests: `python3 -m pytest tests/ -v`
4. Submit a pull request

---

## Privacy and Data Handling

All data is stored **locally on your machine** in `~/.claude/context-recall/`. The plugin makes no network requests, includes no telemetry, and shares no data with any external service.

For full details on what data is stored, how to delete it, and your control options, see [PRIVACY.md](PRIVACY.md).

---

## Security

### Reporting Vulnerabilities

To report a security vulnerability, please open an issue at [github.com/bledden/claude-recall-plugin/issues](https://github.com/bledden/claude-recall-plugin/issues) or contact the author directly via GitHub.

### Security Practices

- All SQL queries use parameterized statements
- No dynamic code execution of any kind
- No external network requests or downloads
- Error messages do not leak file paths or internal state
- Transcript reads are bounded (10MB / 5000 messages per invocation)
- Database directory created with restricted permissions (0o700)
- Hook stdin reads bounded to 1MB

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

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## License

MIT License - see LICENSE file for details.
