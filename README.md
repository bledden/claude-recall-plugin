# Claude Recall Plugin

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that helps you recover conversation context when Claude loses track, with interactive browsing and observability logging.

> **📋 Marketplace Status:** This plugin has been submitted to the official Claude Code plugins repository and is awaiting approval.
>
> **PR:** [anthropics/claude-code#16680](https://github.com/anthropics/claude-code/pull/16680)
>
> **Pre-built Marketplace:** [claude-recall-marketplace](https://github.com/bledden/claude-recall-marketplace) (for easy installation until approved)

---

## Requirements

- **Claude Code** 2.0.x or 2.1.x (see breaking change note below for 2.1.x)
- **Python 3.6+** (for hook and script execution)

---

## ⚠️ UPDATE: Claude Code 2.1.x Breaking Change

**As of Claude Code 2.1.x, local plugins no longer persist across sessions.** This is an undocumented breaking change from 2.0.x behavior. See [issue #17089](https://github.com/anthropics/claude-code/issues/17089).

Custom plugins now **require a marketplace structure** to work reliably with the VSCode extension. The `--plugin-dir` flag only works with the CLI, not VSCode.

---

## Installation

### Option 1: Use Pre-Built Marketplace (Recommended for VSCode)

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

### Option 2: Shell Alias (CLI Only)

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

### Option 3: Plugin Install Command (Not Recommended)

```bash
claude plugins install https://github.com/bledden/claude-recall-plugin
```

> **⚠️ Warning:** This method does not reliably persist in Claude Code 2.1.x. The plugin may disappear after restarting. Use Option 1 instead.

## Quick Start

When Claude seems to have lost context, simply run:

```
/recall:recall
```

This will:
1. **Show you a timestamped index** of all exchanges in your session
2. **Present a menu** asking what you'd like to recall
3. **Fetch and display** the exchanges you select
4. **Summarize** where you left off

---

## Full Command Reference

### Interactive Mode

```
/recall
```

Shows the conversation index and presents a menu with options:
- **Recent (last 5)** - Quick recall of most recent exchanges
- **Search by keyword** - Find exchanges containing specific text
- **Jump to time** - Find exchanges around a specific time

### Quick Commands

Skip the menu by providing arguments directly:

| Command | Description | Example |
|---------|-------------|---------|
| `/recall last<N>` | Fetch the last N exchanges | `/recall last5`, `/recall last10`, `/recall last20` |
| `/recall around <time>` | Fetch exchanges around a specific time | `/recall around 2pm`, `/recall around 14:30` |
| `/recall around "<date> <time>"` | Fetch exchanges around a specific date and time | `/recall around "jan 5 2pm"`, `/recall around "1/5 14:30"` |
| `/recall search <keyword>` | Search for keyword in full content | `/recall search authentication` |
| `/recall search "<phrase>"` | Search for exact phrase | `/recall search "PAI dimension"` |

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

### 1. Timestamped Conversation Index

Every exchange is indexed with its timestamp, allowing you to:
- Browse your conversation history chronologically
- See exactly when each exchange happened
- Navigate through paginated results (20 exchanges per page)

**Example output:**
```
Session started: Jan 5, 2026 at 9:00 AM (Jan 5 - Jan 7)
Total exchanges: 117

Showing page 1 of 6 (most recent first):

**Jan 7:**
#117 [5:13 pm] "root@dendritic-distillation:~/dendritic# ls..."
#116 [2:49 pm] "Yes, give me the command to kick that off"

**Jan 6:**
#115 [1:33 pm] "It looks like the experiment is complete..."
```

### 2. Full-Content Search

Search looks in **both user prompts AND assistant responses**, not just the preview text.

```
/recall search dimension
```

This finds exchanges where:
- You mentioned "dimension" in your prompt
- Claude mentioned "dimension" in the response
- The word appears anywhere in the full content

**Search results show up to 10 most recent matches**, grouped by date.

### 3. Multi-Day Session Support

For sessions spanning multiple days:

- **Index groups exchanges by date** with headers
- **Time searches can specify a date** for precision
- **Date range shown in header** (e.g., "Jan 5 - Jan 7")

```
# Find 2pm on any day (closest match)
/recall around 2pm

# Find 2pm specifically on Jan 5
/recall around "jan 5 2pm"
```

### 4. Flexible Time Navigation

Jump to any point in the conversation by time:

```
/recall around 2pm          # Finds ~5 exchanges around 2pm
/recall around "jan 5 2pm"  # Finds ~5 exchanges around 2pm on Jan 5
/recall around yesterday 3pm # Finds exchanges around 3pm yesterday
```

### 5. Observability Logging

Every `/recall` invocation is logged for analysis:

```
~/.claude/recall-events.log
```

Log format:
```
2026-01-05T16:45:00+00:00 | session=abc123 | exchanges=72 | CONTEXT_RECALL_TRIGGERED
```

Use this to:
- Track how often context is lost
- Identify problematic sessions
- Analyze patterns in context loss

### 6. Incremental Updates

The plugin efficiently updates the index:
- Only processes **new messages** since last update
- Uses byte offset to skip already-indexed content
- Stores full content in index for instant search
- No redundant transcript parsing

### 7. Pagination

Long sessions are paginated (20 exchanges per page):

```
Showing page 1 of 6 (most recent first)

Navigation:
- Show newer: page 1
- Show older: page 2
```

---

## Usage Examples

### Scenario: Claude lost context mid-task

```
/recall last5
```
Fetches the 5 most recent exchanges so Claude can pick up where you left off.

### Scenario: Find a specific discussion from earlier

```
/recall search "API endpoint"
```
Searches all exchanges for mentions of "API endpoint" in either your prompts or Claude's responses.

### Scenario: Return to work from yesterday afternoon

```
/recall around "yesterday 3pm"
```
Fetches exchanges from around 3pm yesterday.

### Scenario: Browse the full conversation

```
/recall
```
Then use the interactive menu to navigate pages, search, or jump to specific times.

---

## How It Works

1. **Hook runs on every prompt** - A `UserPromptSubmit` hook incrementally updates the index
2. **Index stored locally** - At `~/.claude/context-recall/index.json`
3. **Full content cached** - Enables instant full-text search without re-parsing
4. **Byte offset tracking** - Only new transcript data is processed
5. **Observability logging** - Every recall event is logged for analysis

---

## Analyzing Recall Patterns

```bash
# View recent recall events
tail -20 ~/.claude/recall-events.log

# Count recalls per day
cut -d'T' -f1 ~/.claude/recall-events.log | uniq -c

# Find sessions with frequent recalls
grep -oP 'session=\K[^ ]+' ~/.claude/recall-events.log | sort | uniq -c | sort -rn

# Count total recalls
wc -l ~/.claude/recall-events.log
```

---

## Data Storage

Located at `~/.claude/context-recall/`:

| File | Purpose |
|------|---------|
| `index.json` | Current session's timestamped index with full content |

### Index Structure

```json
{
  "session_id": "abc123",
  "session_start": "2026-01-05T09:00:00Z",
  "updated_at": "2026-01-07T17:30:00Z",
  "total_exchanges": 117,
  "transcript_path": "/path/to/transcript.jsonl",
  "_byte_offset": 524288,
  "exchanges": [
    {
      "idx": 1,
      "preview": "Hello, please get caught up...",
      "timestamp": "2026-01-05T09:00:00Z",
      "user_text": "Full user message content...",
      "assistant_text": "Full assistant response..."
    }
  ]
}
```

### Session Behavior

- **Current session**: `index.json` updates incrementally on every prompt
- **New session**: Previous index is overwritten when a new session starts
- **No size limit**: Index grows with conversation (~2KB per exchange)

---

## Plugin Structure

```
claude-recall-plugin/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── commands/
│   └── recall.md                # The /recall command definition
├── hooks/
│   ├── hooks.json               # Hook configuration
│   └── save_context_snapshot.py # Builds index incrementally
├── scripts/
│   ├── utils.py                 # Shared utilities
│   ├── show_index.py            # Paginated index display
│   ├── fetch_exchanges.py       # Fetch exchanges by query
│   └── extract_context.py       # Legacy quick recall
└── tests/
    ├── test_utils.py            # Utils module tests
    ├── test_fetch_exchanges.py  # Fetch tests
    ├── test_show_index.py       # Index display tests
    └── test_save_context_snapshot.py # Hook tests
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

- **Claude Cowork not supported** - This is a Claude Code plugin; it does not work with Claude Cowork (which uses Skills/Connectors instead of plugins)
- **Single session tracking** - Only the current session is indexed; previous session data is overwritten when a new session starts
- **VSCode extension requires marketplace** - Due to a [breaking change in 2.1.x](https://github.com/anthropics/claude-code/issues/17089), the VSCode extension requires the marketplace installation method

---

## Uninstalling

**If installed via marketplace:**
```bash
claude plugin uninstall recall@recall-local
claude plugin marketplace remove recall-local
```

**If using shell alias:**
Remove the alias line from your `~/.zshrc` or `~/.bashrc`, then run `source ~/.zshrc` or `source ~/.bashrc`.

---

## Changelog

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
