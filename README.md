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
Session started: Jan 5, 2026 at 9:00 AM (Jan 5 - Jan 7)
Total exchanges: 117

Showing page 1 of 6 (most recent first):

**Jan 7:**
#117 [5:13 pm] "root@dendritic-distillation:~/dendritic# ls..."
#116 [2:49 pm] "Yes, give me the command to kick that off"

**Jan 6:**
#115 [1:33 pm] "It looks like the experiment is complete..."
...

Navigation:
- Show older: page 2
- Jump to time: e.g., "around 2pm" or "around jan 5 2pm"
- Search: e.g., "search authentication"
```

Then you'll be asked what to recall:

| Option | Description |
|--------|-------------|
| **Recent (last 5)** | Quick recall of most recent exchanges |
| **Search by keyword** | Find exchanges in full content (user + assistant) |
| **Jump to time** | Find exchanges around a specific time |

### Quick Commands (skip the menu)

```
/recall last5              # Last 5 exchanges
/recall last10             # Last 10 exchanges
/recall around 2pm         # Exchanges around 2pm (any day)
/recall around "jan 5 2pm" # Exchanges around 2pm on Jan 5
/recall search auth        # Search full content for "auth"
/recall search "PAI dim"   # Search for phrase
```

## Features

### Full-Content Search
Search looks in both user prompts AND assistant responses, not just previews. Find that thing Claude mentioned even if you don't remember exactly what you asked.

### Multi-Day Session Support
For sessions spanning multiple days, the index groups exchanges by date and time searches can specify a date:
- `around 2pm` - finds closest 2pm across all days
- `around "jan 5 2pm"` - finds 2pm specifically on Jan 5

### Incremental Updates
The hook only processes new messages since the last update, making it efficient even for very long sessions.

## How It Works

1. **Hook runs on every prompt** - Incrementally updates the index
2. **Index stored locally** - At `~/.claude/context-recall/index.json`
3. **Full content cached** - Enables searching without re-parsing transcript
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
| `index.json` | Current session's timestamped index with full content |

### Session Behavior

- **Current session**: `index.json` updates incrementally on every prompt
- **New session**: Previous index is overwritten
- **No size limit**: Index grows with conversation

## Plugin Structure

```
claude-recall-plugin/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── commands/
│   └── recall.md                # The /recall command
├── hooks/
│   ├── hooks.json               # Hook configuration
│   └── save_context_snapshot.py # Builds index incrementally
├── scripts/
│   ├── utils.py                 # Shared utilities
│   ├── show_index.py            # Paginated index display
│   ├── fetch_exchanges.py       # Fetch exchanges by query
│   └── extract_context.py       # Legacy quick recall
└── tests/
    └── *.py                     # Unit tests
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
