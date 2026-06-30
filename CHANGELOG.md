# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.2] - 2026-06-28

### Fixed
- **"`/recall` returns another session's data" under concurrency** (multi-session Linux): session identity now resolves from the native, per-session `CLAUDE_CODE_SESSION_ID` that Claude Code injects into every command subprocess, instead of an appended `$CLAUDE_ENV_FILE` var that could leak between concurrent sessions. A stale/leaked `RECALL_SESSION_ID` can no longer win. Project scope (`--all`, `sessions`, `tags`) self-resolves from the working directory; `connect-latest` self-resolves its project hash. `recall.md` no longer relies on `$SESSION_ID`/`$SESSION_HASH` plumbing.
- **Hooks silently failed on Linux without `python3` on PATH**: every hook hard-coded `python3`. Each hook command now probes for `python3` and falls back to `python`, so indexing/recall work on environments that only ship `python`.

### Added
- **Reliable proactive recall:** the `UserPromptSubmit` hook now deterministically detects explicit context-loss phrases ("didn't we discuss…", "remind me what…", etc.) and injects a `[Recall]` suggestion — gated on `skill_enabled` (opt-in, default off). Previously this depended on the model noticing, so it fired only sometimes; the recall-assistant skill retains the behavioral/temporal signals.

## [2.2.1] - 2026-06-26

### Fixed
- Hooks now read current Claude Code input fields `prompt`/`cwd` (were `user_prompt`/`project_path`/`project_hash`) and derive the project hash from `cwd` — restores cross-project search, `--all`, connect-latest, and project-scoped commands that had silently broken against current Claude Code (verified against a live hook payload)
- Compaction nudge registered under `PreCompact` (was the non-existent `PostCompact`, so it never fired)
- New `SessionStart` hook exports `RECALL_SESSION_ID`/`RECALL_PROJECT_HASH` to `$CLAUDE_ENV_FILE` (using the documented `export KEY=value` format); `/recall` commands now resolve the current session, which previously relied on never-defined `$SESSION_ID`/`$SESSION_HASH`
- `fetch_exchanges`: `last0`/`last<=0` rejected (was dumping all exchanges); search scope flags are mutually exclusive
- `show_index`: `--search` uses FTS5; `--around` compares in local time; negative pages guarded
- `manage_sessions`: `prune --before` validates the date before a destructive delete; export of a missing session errors instead of emitting empty JSON
- `manage_tags`: `add` distinguishes inserted vs already-present; `--project` documented as a hash
- `highlight`: argparse migration — `--help` no longer performs a real insert; bad `--exchange` errors cleanly
- `manage_connections`: single mode vocabulary (explicit/decay, silent/inject); `inbox` is a read-only view (`--mark-read` to advance, decay-only); `disconnect`/`config` report when nothing changed
- Enhanced-tier consent text is honest — no fabricated model download / SHA256 — and ONNX is no longer falsely auto-detected (v3 branch)

### Changed
- DB schema versioning via `PRAGMA user_version` for managed future migrations
- README / command / skill command surfaces reconciled; phantom `export --json` flag removed
- Stress/concurrency/scale suites wired into CI via `pytest.ini` (full suite: 386 passing)

## [2.2.0] - 2026-04-02

### Added
- Recall assistant skill: opt-in SKILL.md for proactive context recovery, highlight suggestions, and natural language session linking
- New config keys: `skill_enabled`, `detection_signals`, `auto_run_highlight`
- PRIVACY.md documenting all data storage and handling practices
- CHANGELOG.md (this file)

### Changed
- Performance: batch commits (10-15 fsyncs reduced to 1 per prompt)
- Performance: schema init guard skips DDL when tables exist (~2ms saved per prompt)
- Performance: PRAGMA synchronous=NORMAL for WAL mode (30-50% fsync reduction)
- Performance: incremental auto-tagging processes only new exchanges (O(1) vs O(N))
- Performance: `lastN` queries use SQL LIMIT instead of loading all exchanges
- Security: error messages no longer leak raw exceptions to the model
- Security: transcript reads capped at 10MB / 5000 messages per invocation
- Security: LIKE wildcards escaped in project path search
- Security: stdin reads bounded to 1MB in all hooks
- Security: DB directory created with 0o700 permissions
- Quality: full type annotations across all modules
- Quality: narrow exception handling (ValueError/TypeError instead of bare Exception)
- Quality: empty FTS query and empty keyword guards added

### Fixed
- check_mode validation now rejects invalid values (only 'explicit' and 'decay' accepted)
- Removed unused imports and duplicate inline imports

## [2.1.0] - 2026-04-01

### Added
- Cross-session context sharing via highlights and connections
- Explicit highlight creation: `/recall highlight "summary"`
- Opt-in auto-detect highlights: conservative heuristic (2+ solution signals, 25+ words)
- `/recall connect`, `/recall connect --latest`, `/recall disconnect`
- `/recall inbox` for viewing unchecked highlights from connections
- Configurable check frequency with decay (7th prompt, grows by 3, caps at 30th)
- Configurable delivery: silent queue or system message injection
- `/recall config` command for all settings
- Stress tests: scale (22), concurrent (6), /clear cycles (22), sharing (22)

### Fixed
- FTS5 multi-word search uses AND logic instead of exact phrase matching
- `prune_session` FK violation on sessions with highlights or connections
- Auto-detect summary collisions (exchange index included in summary)
- FTS5 insert atomicity (single transaction for content + index)
- Connection leaks in fetch_exchanges.py and show_index.py
- recall.md tag routing (positional arg changed to --project flag)

## [2.0.0] - 2026-04-01

### Added
- SQLite storage (`recall.db`) replacing JSON index files
- FTS5 full-text search with sub-millisecond query times
- WAL mode for concurrent session safety
- `/clear` survival — context persists across clear commands
- Cross-session search (`--all`) and cross-project search (`--global`, `--project`)
- PostCompact hook with automatic context recovery nudge
- SessionEnd hook for session finalization
- Hybrid auto/manual tagging with TF-based keyword extraction
- Session management: `sessions`, `session <id>`, `stats`, `prune`, `export`
- Automatic migration from v1.0.1 on first run (non-destructive)
- 166 unit/integration tests

### Removed
- `hooks/save_context_snapshot.py` (replaced by `hooks/prompt_submit.py`)
- `scripts/extract_context.py` (deprecated)
- `load_index()` and `save_index()` from utils.py (replaced by db.py)

## [1.0.1] - 2026-01-30

### Added
- Claude Cowork support (zip upload via Plugins sidebar)
- Pre-built marketplace for easier Claude Code installation

### Fixed
- Python 3.15 deprecation warning in date parsing
- Test imports for refactored module structure

### Changed
- Updated installation docs for Claude Code 2.1.x marketplace requirement

## [1.0.0] - 2026-01-15

### Added
- Initial release
- Interactive `/recall` command with menu
- Quick commands: `last5`, `last10`, `search`, `around`
- Full-content search across user prompts and assistant responses
- Multi-day session support with date grouping
- Incremental indexing with byte offset tracking
- Observability logging to `~/.claude/recall-events.log`
- 91 unit tests
