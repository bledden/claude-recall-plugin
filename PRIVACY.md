# Privacy Policy — Claude Recall Plugin

**Last updated:** September 5, 2026

## What Data Is Stored

The recall plugin stores conversation data locally on your machine to enable context recovery across sessions. Specifically:

| Data | Location | Purpose |
|---|---|---|
| Exchange text | `~/.claude/context-recall/recall.db` | User prompts (up to 1,000 chars) and assistant responses (all text blocks of the turn merged, up to 4,000 chars) for search and recall |
| Tool calls (v2.4+) | Same DB (exchanges.tool_text) | One line per tool call Claude made in the turn: the shell command, the file path edited/read, the URL fetched, or the tool name (300 chars per call, 2,000 per exchange). Tool **output** is never stored |
| Session metadata | Same DB | Session IDs, project paths, timestamps, byte offsets for incremental indexing |
| Auto-tags | Same DB | Technical terms extracted from exchange text for search and discovery |
| Manual tags | Same DB | User-applied tags for organizing sessions and exchanges |
| Highlights | Same DB | Summaries of findings flagged for cross-session sharing |
| Connections | Same DB | Opt-in links between sessions for highlight sharing |
| Session config | Same DB (sessions.metadata) | User preferences (skill_enabled, check_mode, etc.) |
| Usage counter (v2.2.3+) | Same DB (invocations) | Timestamp, session ID, project hash, command name, and command arguments for each recall invocation — arguments may include search terms you typed. Powers `/recall usage`; never leaves your machine |
| Recall events | `~/.claude/recall-events.log` | Timestamps and session IDs when `/recall` is invoked (for observability) |

## Where Data Is Stored

All data is stored **locally on your machine** in the `~/.claude/` directory. The plugin:

- Does **not** transmit data to any external server
- Does **not** make any network requests
- Does **not** share data with Anthropic or any third party
- Does **not** include any telemetry, analytics, or tracking

The database directory is created with restricted permissions (0o700 — owner-only access).

## What Data Is NOT Stored

- Full conversation transcripts (only truncated exchange text: up to 1,000 chars per user prompt and 4,000 chars per assistant reply; tool output and thinking are never stored, tool calls only in the compact form above)
- Credentials that match the redaction patterns below (see Secrets Redaction; this is pattern-based, not a guarantee)
- System information beyond project directory paths
- Any data from other applications

## Secrets Redaction

Before anything is written, captured text (prompts, replies, tool-call lines) passes through `redact_secrets()`, which replaces matches with `[REDACTED:<kind>]`:

- Well-known token formats: AWS access keys, OpenAI and Anthropic keys, GitHub tokens, Hugging Face tokens, Slack tokens, Google API keys, JWTs, `Bearer …` tokens, PEM private-key blocks
- Assignments whose name looks like a secret (`API_KEY=…`, `"password": "…"`, `AWS_SECRET_ACCESS_KEY=…`)
- Plain-language phrasings such as "my password is …" or "the api key is …"

This is pattern matching, not detection of every secret. Anything that does not match one of these shapes is stored as typed. If you paste something sensitive that slips through, `prune --session <id>` removes that session; the redaction list lives in `scripts/utils.py` and pull requests adding patterns are welcome.

## Data Retention

Data persists in the SQLite database until you explicitly delete it. The plugin does not auto-prune or expire data. You control retention entirely:

- `/recall prune --session <id>` — delete a specific session and all its data
- `/recall prune --before <date>` — delete all sessions before a date
- `rm -rf ~/.claude/context-recall/` — delete all recall data permanently
- `rm ~/.claude/recall-events.log` — delete the event log

Versions prior to 2.0 stored snapshots as JSON files (`*_index.json`, `current.json`, `recall-config.json`) in the same `~/.claude/context-recall/` directory. Current versions no longer write these, but old files may remain; the `rm -rf` above removes them along with everything else.

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
