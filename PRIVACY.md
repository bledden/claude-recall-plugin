# Privacy Policy — Claude Recall Plugin

**Last updated:** April 2, 2026

## What Data Is Stored

The recall plugin stores conversation data locally on your machine to enable context recovery across sessions. Specifically:

| Data | Location | Purpose |
|---|---|---|
| Exchange text | `~/.claude/context-recall/recall.db` | User prompts and assistant responses (truncated to 1000 chars each) for search and recall |
| Session metadata | Same DB | Session IDs, project paths, timestamps, byte offsets for incremental indexing |
| Auto-tags | Same DB | Technical terms extracted from exchange text for search and discovery |
| Manual tags | Same DB | User-applied tags for organizing sessions and exchanges |
| Highlights | Same DB | Summaries of findings flagged for cross-session sharing |
| Connections | Same DB | Opt-in links between sessions for highlight sharing |
| Session config | Same DB (sessions.metadata) | User preferences (skill_enabled, check_mode, etc.) |
| Recall events | `~/.claude/recall-events.log` | Timestamps and session IDs when `/recall` is invoked (for observability) |

## Where Data Is Stored

All data is stored **locally on your machine** in the `~/.claude/` directory. The plugin:

- Does **not** transmit data to any external server
- Does **not** make any network requests
- Does **not** share data with Anthropic or any third party
- Does **not** include any telemetry, analytics, or tracking

The database directory is created with restricted permissions (0o700 — owner-only access).

## What Data Is NOT Stored

- Full conversation transcripts (only truncated exchange text — up to 1000 chars per message)
- Passwords, API keys, or credentials
- System information beyond project directory paths
- Any data from other applications

## Data Retention

Data persists in the SQLite database until you explicitly delete it. The plugin does not auto-prune or expire data. You control retention entirely:

- `/recall prune --session <id>` — delete a specific session and all its data
- `/recall prune --before <date>` — delete all sessions before a date
- `rm -rf ~/.claude/context-recall/` — delete all recall data permanently
- `rm ~/.claude/recall-events.log` — delete the event log

## User Control

You have full control over what the plugin stores:

- **Opt-in features**: Auto-highlight detection, decay polling, system message injection, and the recall assistant skill are all disabled by default. You enable them explicitly via `/recall config`.
- **Cross-session sharing**: Session connections are opt-in. No data is shared between sessions unless you explicitly run `/recall connect`.
- **Deletion**: All data can be deleted at any time via the prune commands or by removing the database file.

## Third-Party Dependencies

The plugin uses only Python standard library modules (`sqlite3`, `json`, `os`, `sys`, `re`, `datetime`, `pathlib`, `collections`). No third-party packages are installed, downloaded, or executed.

## Changes to This Policy

Changes to this privacy policy will be documented in the plugin's CHANGELOG.md and README.md.

## Contact

For questions about data handling: [https://github.com/bledden/claude-recall-plugin/issues](https://github.com/bledden/claude-recall-plugin/issues)
