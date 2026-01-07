# Claude Recall Plugin

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that helps you recover conversation context when Claude loses track, with interactive browsing and observability logging.

## Installation

### From GitHub

```bash
claude plugins install https://github.com/bledden/claude-recall-plugin
```

### From Local Directory

```bash
claude plugins install /path/to/claude-recall-plugin
```

## Quick Start

When Claude seems to have lost context, simply run:

```
/recall
```

This will:
1. **Show you a timestamped index** of all exchanges in your session
2. **Present a menu** asking what you'd like to recall
3. **Fetch and display** the exchanges you select
4. **Summarize** where you left off

## Usage

### Interactive Mode (Default)

Running `/recall` shows your conversation index with timestamps:

```
Session started: Jan 5, 2026 at 9:00 AM (19 exchanges)

Showing page 1 of 1 (most recent first):

#19 [11:24 pm] "like 10:15pm-ish?"
#18 [11:24 pm] "# Context Recall The user wants to recover..."
#17 [8:42 pm] "skip"
#16 [8:36 pm] "<ide_opened_file>The user opened the file..."
...

Navigation:
- Show older: page 2
- Jump to time: e.g., "around 2pm"
- Search: e.g., "search authentication"
```

Then you'll be asked what to recall:

| Option | Description |
|--------|-------------|
| **Recent (last 5)** | Quick recall of most recent exchanges |
| **Search by keyword** | Find exchanges containing specific text |
| **Jump to time** | Find exchanges around a specific time (e.g., "2pm") |

### Quick Commands (skip the menu)

```
/recall last5           # Last 5 exchanges
/recall last10          # Last 10 exchanges
/recall around 2pm      # Exchanges around 2pm
/recall around 14:30    # Exchanges around 2:30pm
/recall search auth     # Search for "auth" in exchanges
/recall search "PAI"    # Search for specific term
```

## How It Works

1. **Hook runs on every prompt** - Builds a timestamped index of all exchanges
2. **Index stored locally** - At `~/.claude/context-recall/index.json`
3. **On `/recall`** - Shows index, lets you choose, fetches full content
4. **Observability** - Every recall is logged for analysis

## Observability

### Log File

Each `/recall` is logged to `~/.claude/recall-events.log`:

```
2026-01-05T16:45:00+00:00 | session=abc123 | exchanges=72 | CONTEXT_RECALL_TRIGGERED
```

### Analyzing Patterns

```bash
# View recent recall events
tail -20 ~/.claude/recall-events.log

# Count recalls per day
cut -d'T' -f1 ~/.claude/recall-events.log | uniq -c

# Find sessions with frequent recalls
grep -oP 'session=\K[^ ]+' ~/.claude/recall-events.log | sort | uniq -c | sort -rn
```

## Data Storage

Located at `~/.claude/context-recall/`:

| File | Purpose |
|------|---------|
| `index.json` | Current session's timestamped index |
| `current.json` | Last 5 exchanges (quick access) |
| `{session_id}_index.json` | Per-session backup (persists after session ends) |

### Session Behavior

- **Current session**: `index.json` updates on every prompt
- **Past sessions**: Saved as `{session_id}_index.json` and preserved
- **No size limit**: Index grows with conversation (~160KB per 1,000 exchanges)

### Accessing Past Sessions

```bash
# List all session indexes
ls ~/.claude/context-recall/*_index.json

# To recall from a past session, copy its index:
cp ~/.claude/context-recall/{session_id}_index.json ~/.claude/context-recall/index.json
```

## Plugin Structure

```
claude-recall-plugin/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── commands/
│   └── recall.md                # The /recall command
├── hooks/
│   ├── hooks.json               # Hook configuration
│   └── save_context_snapshot.py # Builds index + logs events
├── scripts/
│   ├── show_index.py            # Paginated index display
│   ├── fetch_exchanges.py       # Fetch exchanges by query
│   └── extract_context.py       # Legacy quick recall
└── tests/
    └── *.py                     # Unit and integration tests
```

## Running Tests

```bash
cd claude-recall-plugin
python3 -m unittest discover -v tests/
```

## Contributing

1. Fork the repository
2. Make your changes
3. Run tests: `python3 -m unittest discover -v tests/`
4. Submit a pull request

## License

MIT License - see LICENSE file for details.
