# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.4.0] - 2026-09-05

Parity release, driven by an independent review of v2.3.1 (GPT 6 Astra, eight findings, all fixed; its ten reproducers are now `tests/test_review_regressions.py`) and by the gap to funes.

### Added
- **The commands Claude ran are recallable.** Tool calls in a turn are captured as one compact line each (`$ <shell command>`, `Edit <path>`, `Grep <pattern>`, `WebFetch <url>`, …) into a new `exchanges.tool_text` column, indexed by FTS and shown under "Tools run". Tool *output* is still never stored. On a real 20 MB transcript: 141 of 163 exchanges carry tool calls, 327 shell commands.
- **Ranked search with match snippets.** Results are BM25 relevance re-weighted by recency (30-day half-life; `--half-life 0` for pure relevance), best match first, and every hit carries the passage that matched (`Match: …«term»…`) instead of the first 200 characters.
- **Secrets redacted at index time.** Well-known credential formats (AWS, OpenAI/Anthropic, GitHub, Hugging Face, Slack, Google, JWTs, bearer tokens, PEM private keys), `KEY=value` assignments with secret-like names, and "my password is …" phrasings are replaced with `[REDACTED:<kind>]` before storage. Pattern-based, documented as not exhaustive (PRIVACY.md).
- **Claude reaches for recall on its own.** `/recall:recall` is now a skill (`skills/recall/SKILL.md`) with a trigger-phrase description, so Claude invokes it when the user refers to earlier work; a section tells it to search directly instead of showing the menu.
- **Continuation-append indexer.** Assistant blocks that land after their exchange was stored (a turn split across reads, a Stop-time transcript lag, a blocked-Stop continuation) are appended to that exchange, FTS kept in sync. Replaces the v2.3 hold-back rule and the `last_assistant_message` check.
- `SessionEnd` drains the whole backlog in committed passes within a 7s budget.

### Fixed
- Retrieval hid stored answers after 1,000 characters (display used the user-side cap). Display caps now match storage (4,000) and the aggregate budget is 20,000.
- Capture could wedge forever on a turn larger than the 2 MB read budget, and on any single record larger than the budget. Every read now makes progress: at least one record is always consumed, and a prompt cut off by the cap is stored pending its reply.
- A partially written JSONL record could be skipped permanently (offset advanced past it). Unterminated, unparseable final records are no longer consumed.
- A proactive `[Recall]` suggestion discarded a due cross-session highlight in the same prompt; the `/recall` observability notice did the same. Outputs are now combined.
- Global search lacked the `idx` tiebreaker; ranking now ties on timestamp then idx.
- PRIVACY.md claimed credentials were never stored; they now are redacted, and the policy says exactly what that means.
- Hook commands left the plugin path unquoted (broken under paths with spaces).

### Changed
- DB schema version 5 (`tool_text` column; FTS index rebuilt once).
- `commands/recall.md` removed in favour of the skill.
- 459 tests.

## [2.3.1] - 2026-09-05

### Fixed
- **The test suite wrote into the user's real `recall.db`.** Every script `main()` calls `record_invocation()`, which opens the default database; `conftest.py` isolated only the event log, so each `pytest` run appended fixture invocations (`last0`, `last-3`, `search authentication`, ...) to the live store and could migrate its schema ahead of the installed plugin. The autouse fixture now sets `RECALL_DB` (and unsets `CLAUDE_CODE_SESSION_ID`) for the whole run; a suite run adds 0 rows to the real store.

## [2.3.0] - 2026-09-05

Fidelity release. A fresh-eyes review found that the plugin's own "verbatim memory" claim was not true in practice; this release makes it true.

### Fixed
- **71% of assistant text was discarded.** Exchanges paired a user message with only the *next* assistant message; in agentic turns Claude's reply arrives as several text blocks separated by tool calls, and every block after the first was dropped (measured on a real transcript: 384 of 544 assistant messages; 43% of stored replies were sub-200-char preambles). All assistant text between two user prompts is now merged into one `assistant_text`. Replaying the same transcript through the new indexer retains 95% of Claude's reply text (was 15%); preamble-only replies drop from 70 to 10 of 161.
- **The final turn of every session was never indexed.** Capture ran only on the *next* prompt and `SessionEnd` only stamped `ended_at` (6 of 6 recently ended sessions were missing their last exchange). A new `Stop` hook indexes each turn as it completes, and `SessionEnd` runs a final catch-up.
- **Nothing the plugin injected ever reached Claude.** The compaction nudge, the proactive `[Recall]` suggestion and `delivery_mode inject` all returned `systemMessage`, which Claude Code shows to the *user* only. They now return `hookSpecificOutput.additionalContext`. The compaction nudge moves from `PreCompact` (which cannot inject context at all) to `SessionStart` with `matcher: "compact"`.
- **Search returned the oldest matches.** `search_exchanges_fts`/`search_exchanges_global` had no `ORDER BY`, so under a `LIMIT` FTS5 returned rowid order (a 68-hit term returned idx 1..13 of 158). Results are now newest-first from the index (shown in reading order for session/project searches).
- **`around <time>` compared UTC hours to a local target** (the 2.2.1 fix covered `show_index` only), so "around 2pm" matched 2pm UTC. `find_exchanges_by_time` and date grouping now use local time.
- Auto-tagging almost never fired in normal use: `min_frequency` was applied to the exchanges of a single prompt. Tags are now computed over a rolling window of the session's last 50 exchanges; bare numbers are no longer tag candidates.
- `commands/recall.md` allowed only `Bash(python3:*)` while the hooks fall back to `python`; `Bash(python:*)` is now allowed too.

### Added
- **Held-back incomplete turns.** The indexer never advances past a turn it cannot prove complete: a trailing user message with no reply is left for the next run, a read that stopped at the size cap does not commit its last group, and at `Stop` the trailing turn is consumed only once the payload's `last_assistant_message` has reached the transcript file (the file can lag the in-memory turn). This is what makes per-turn capture and block merging safe together.
- Porter stemming for FTS5 ("kernel" matches "kernels"), via schema v4 which rebuilds the FTS index once.

### Changed
- Assistant replies are stored up to 4,000 chars (was 1,000) so the merged answer survives; user prompts stay at 1,000. See PRIVACY.md.
- DB schema version 4.
- Test suite: 425 tests; the scale stress test no longer mutates its shared fixture (it failed under `pytest-randomly`).

## [2.2.3] - 2026-07-02

### Added
- **`/recall usage`** — self-served invocation stats (total, date range, by command, by month, by project) from a new durable invocation counter. Every `/recall` command now records itself at the point of use via a schema-v3 `invocations` table, so usage is captured accurately regardless of dispatch path (skill / slash-command / conversational) — no more reconstructing counts from giant transcripts.
- Troubleshooting section in the README (Linux `Unknown skill` = install/enablement; `python3`-on-PATH; indexer catch-up).

### Fixed
- **Capture hook wedged — and silently lost lines — on huge transcripts.** A read of up to 10 MB committed once at the very end, so a hook killed by the 10s timeout committed nothing and re-read the same chunk on every prompt (permanently stuck ~10–15 MB behind on marathon sessions); the loop also broke *after* consuming the over-cap line and advanced the offset via a buffered `f.tell()`, skipping the unread tail. Now uses 2 MB / 1000-message caps that fit the timeout and bank progress each prompt, breaks *before* consuming an over-cap line, and advances the offset precisely — converges with zero loss.
- **The test suite polluted the real event log.** `log_recall_event` hard-coded `~/.claude/recall-events.log`, so running the tests wrote fake session rows into it. Added a `RECALL_LOG_FILE` override + an autouse test fixture (and a `RECALL_DB` override) so the suite never touches real recall data.

### Changed
- DB schema version bumped to 3 (adds the `invocations` table via a managed migration).

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
