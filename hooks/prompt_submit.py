#!/usr/bin/env python3
"""SQLite-backed prompt submit hook for Claude Context Recall plugin v2.

Runs on every UserPromptSubmit event.  Reads JSON from stdin, incrementally
indexes new exchanges into SQLite, runs auto-tagging, and handles v1->v2
migration from the legacy JSON index.

The incremental indexer (``index_transcript``) lives here and is shared with
the Stop and SessionEnd hooks, so a turn is captured as soon as it completes
and the final turn of a session is never lost.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from utils import (extract_text_content, extract_tool_calls, make_preview,
                   truncate_text, redact_secrets, compute_project_hash,
                   MAX_CHARS_PER_MESSAGE, MAX_ASSISTANT_CHARS, MAX_TOOL_CHARS)
from db import (get_connection, insert_session, get_session, insert_exchanges,
                update_session_offset, get_exchanges, insert_tag, DB_PATH,
                get_last_exchange, update_exchange_text,
                get_connections, get_highlights, update_connection_check,
                get_exchange_count, get_session_config)
from auto_tagger import compute_auto_tags
from highlight import auto_detect_highlights


# ---------------------------------------------------------------------------
# Proactive recall suggestion — the deterministic counterpart to the skill's
# "explicit context-loss" detection. Running it from the hook means it fires
# every time the pattern appears, instead of depending on the model noticing.
# Gated on the per-session skill_enabled config (opt-in, default off).
# ---------------------------------------------------------------------------

_CONTEXT_LOSS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"did\s*n['’]?t we (?:already )?(?:discuss|talk about|cover|go over)",
    r"what was that (?:thing )?(?:about|called)",
    r"earlier you (?:said|mentioned)",
    r"we (?:discussed|talked about|covered) (?:this|that|it)(?: before| earlier| already)?",
    r"remind me (?:what|how|about|again)",
    r"you mentioned (?:something )?(?:about )?",
    r"as we (?:discussed|talked about)",
    r"weren['’]?t we (?:working on|talking about)",
)]


def _maybe_suggest_recall(conn, session_id, user_prompt):
    """Return a recall suggestion when the prompt shows an explicit context-loss
    signal AND the recall-assistant skill is enabled for this session, else None.
    """
    if not user_prompt:
        return None
    if not get_session_config(conn, session_id, 'skill_enabled'):
        return None
    if any(p.search(user_prompt) for p in _CONTEXT_LOSS_PATTERNS):
        return ("[Recall] That sounds like earlier context — you can run "
                "`/recall search <topic>` or `/recall last10` to recover it.")
    return None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_FILE = Path.home() / '.claude' / 'recall-events.log'

# Per-invocation catch-up caps. Kept small so one hook run fits well within the
# 10s timeout: a killed hook commits nothing, so an oversized read on a huge,
# actively-growing transcript would wedge the byte offset forever (it re-reads
# the same chunk and dies every prompt). Small reads bank progress each prompt
# and converge.
MAX_BYTES_PER_READ = 2 * 1024 * 1024      # 2 MB
MAX_MESSAGES_PER_READ = 1000
LEGACY_INDEX_FILE = Path.home() / '.claude' / 'context-recall' / 'index.json'

# Module-level flag: skip the filesystem stat on every prompt after first check
_migration_checked = False


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def parse_transcript_from_offset(
    transcript_path: str,
    byte_offset: int = 0,
) -> Tuple[List[Dict], int]:
    """Parse transcript JSONL starting from a byte offset.

    Opens the file, seeks to *byte_offset*, and reads every subsequent JSONL
    line that contains a user or assistant message (text or tool calls).

    Three rules keep the durable offset honest:
      * the size/message caps stop the read BEFORE a line that would exceed
        them, but at least one line is always consumed, so a single oversized
        record (a multi-MB tool result) can never block the offset forever;
      * a final line with no trailing newline that does not parse is the
        writer mid-record — it is NOT consumed, so the next run re-reads it
        whole instead of resuming inside the JSON;
      * every message records its byte span (``_start``/``_end``) so the
        indexer can rewind to a held-back prompt exactly.

    Returns:
        Tuple of (messages, new_byte_offset) where each message is a dict
        with keys: role, text, tools, timestamp, _start, _end.
    """
    messages: List[Dict] = []
    new_offset = byte_offset

    if not transcript_path or not os.path.exists(transcript_path):
        return messages, new_offset

    try:
        with open(transcript_path, 'rb') as f:
            if byte_offset > 0:
                f.seek(byte_offset)

            consumed = 0
            for line_bytes in f:
                if consumed > 0 and (consumed + len(line_bytes) > MAX_BYTES_PER_READ
                                     or len(messages) >= MAX_MESSAGES_PER_READ):
                    break

                line_start = byte_offset + consumed
                terminated = line_bytes.endswith(b'\n')
                entry = None
                try:
                    line = line_bytes.decode('utf-8').strip()
                    if line:
                        entry = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    if not terminated:
                        break          # partial record still being written
                    entry = None       # a corrupt, complete line: skip it

                consumed += len(line_bytes)
                new_offset = byte_offset + consumed

                if not isinstance(entry, dict):
                    continue
                role = entry.get('type', '') or entry.get('role', '')
                if role not in ('user', 'assistant'):
                    role = (entry.get('message') or {}).get('role', '')
                if role not in ('user', 'assistant'):
                    continue

                message_obj = entry.get('message') or {}
                text = extract_text_content(message_obj)
                tools = extract_tool_calls(message_obj) if role == 'assistant' else []
                # Keep tool-only assistant messages too: the commands Claude
                # ran are exactly what users come back for.
                if text or tools:
                    messages.append({
                        'role': role,
                        'text': text,
                        'tools': tools,
                        'timestamp': entry.get('timestamp', ''),
                        '_start': line_start,
                        '_end': new_offset,
                    })

    except Exception as e:
        print(f"[context-recall] Transcript parse error: {e}", file=sys.stderr)

    return messages, new_offset


# ---------------------------------------------------------------------------
# Exchange building
# ---------------------------------------------------------------------------

def build_new_exchanges(
    messages: List[Dict],
    start_idx: int = 1,
    allow_pending: bool = False,
) -> List[Dict]:
    """Pair each user message with ALL the assistant text that follows it.

    In an agentic turn Claude's reply arrives as several assistant text blocks
    separated by tool calls ("Let me look…", <tool>, "Found it…", <tool>, the
    actual answer). Pairing a user message with only the *next* assistant
    message kept the preamble and dropped the answer — 71% of assistant text on
    a real transcript. Every assistant message up to the next user message is
    merged (blank-line separated) into ``assistant_text``; its tool calls
    become ``tool_text`` (one line each). Secrets are redacted before storage.

    A user message with no assistant reply is skipped, unless
    ``allow_pending`` is set and it is the LAST message: then it is stored
    with an empty reply so later assistant blocks can be appended to it (the
    indexer uses this when a read stopped at the size cap right after the
    prompt). Assistant messages before any user message are skipped here; the
    indexer appends those to the previous exchange instead.

    Each exchange dict has: idx, preview, timestamp, user_text,
    assistant_text, tool_text (None when no tools ran).
    """
    exchanges: List[Dict] = []
    exchange_idx = start_idx
    i = 0
    n = len(messages)

    while i < n:
        if messages[i]['role'] != 'user':
            i += 1
            continue
        user_msg = messages[i]
        j = i + 1
        parts: List[str] = []
        tool_lines: List[str] = []
        while j < n and messages[j]['role'] == 'assistant':
            if messages[j]['text']:
                parts.append(messages[j]['text'])
            tool_lines.extend(messages[j].get('tools') or [])
            j += 1
        pending = allow_pending and j >= n and not parts and not tool_lines
        if parts or tool_lines or pending:
            # Redact credential-looking strings BEFORE anything is stored.
            user_text = redact_secrets(user_msg['text'])
            assistant_text = redact_secrets('\n\n'.join(parts))
            tool_text = redact_secrets('\n'.join(tool_lines))
            exchanges.append({
                'idx': exchange_idx,
                'preview': make_preview(user_text),
                'timestamp': user_msg.get('timestamp', ''),
                'user_text': truncate_text(user_text, MAX_CHARS_PER_MESSAGE),
                'assistant_text': truncate_text(assistant_text, MAX_ASSISTANT_CHARS),
                'tool_text': truncate_text(tool_text, MAX_TOOL_CHARS) if tool_text else None,
            })
            exchange_idx += 1
            i = j
        else:
            i += 1

    return exchanges


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

def migrate_from_json(conn, legacy_path: Path = None) -> None:
    """Migrate a v1 JSON index into the SQLite database.

    Reads the legacy ``index.json``, inserts its session and exchanges
    into the DB, then renames the file to ``index.json.migrated``.

    No-op if the legacy file does not exist.  After the first check the
    module-level ``_migration_checked`` flag is set so subsequent calls
    skip the Path.exists() stat entirely.

    Args:
        conn: SQLite connection.
        legacy_path: Override path for the legacy file.
    """
    global _migration_checked
    if _migration_checked:
        return

    if legacy_path is None:
        legacy_path = LEGACY_INDEX_FILE

    _migration_checked = True

    if not legacy_path.exists():
        return

    try:
        with open(legacy_path, 'r', encoding='utf-8') as f:
            legacy_data = json.load(f)

        session_id = legacy_data.get('session_id', 'migrated-unknown')
        started_at = legacy_data.get('session_start', datetime.now(timezone.utc).isoformat())
        transcript_path = legacy_data.get('transcript_path', '')

        # Insert session (IGNORE if already migrated in a previous run)
        insert_session(
            conn,
            session_id=session_id,
            project_path='',
            project_hash='',
            started_at=started_at,
            transcript_path=transcript_path,
        )

        # Insert exchanges
        exchanges = legacy_data.get('exchanges', [])
        if exchanges:
            insert_exchanges(conn, session_id, exchanges)
            update_session_offset(
                conn,
                session_id,
                byte_offset=legacy_data.get('_byte_offset', 0),
                exchange_count=len(exchanges),
            )

        # Rename legacy file so we never migrate twice
        migrated_path = legacy_path.with_suffix('.json.migrated')
        legacy_path.rename(migrated_path)

        print(f"[context-recall] Migrated {len(exchanges)} exchanges from v1 index", file=sys.stderr)

    except Exception as exc:
        print(f"[context-recall] Migration error (non-blocking): {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_recall_event(session_id: str, exchange_count: int) -> None:
    """Append a recall-triggered log line to LOG_FILE.

    Format:
        {timestamp} | session={id} | exchanges={count} | CONTEXT_RECALL_TRIGGERED
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entry = f"{timestamp} | session={session_id} | exchanges={exchange_count} | CONTEXT_RECALL_TRIGGERED\n"

    print(f"[context-recall] Context recall triggered at exchange #{exchange_count}", file=sys.stderr)

    # RECALL_LOG_FILE override keeps tests/tooling out of the real event log.
    log_file = Path(os.environ.get('RECALL_LOG_FILE') or LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception as e:
        print(f"[context-recall] Failed to write log: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------

def _store_auto_tags(conn, session_id: str, exchanges: List[Dict],
                     commit: bool = True) -> None:
    """Compute and persist auto-tags for a session's exchanges.

    Args:
        conn: SQLite connection.
        session_id: Session to tag.
        exchanges: Exchange dicts to derive tags from.
        commit: If True (default), commit after each tag insertion.
    """
    tags = compute_auto_tags(exchanges)
    for tag in tags:
        insert_tag(conn, tag, session_id, exchange_idx=None, source='auto',
                   commit=commit)


# ---------------------------------------------------------------------------
# Connection checks
# ---------------------------------------------------------------------------

def _check_connections(conn, session_id: str) -> Optional[str]:
    """Check connections for new highlights. Returns system message or None.

    Iterates all connections for the session.  Connections in 'explicit' mode
    are skipped (the user checks manually via /recall inbox).  For 'decay'
    mode connections the counter is incremented each call; when it reaches the
    interval, highlights are fetched from the target session and a formatted
    message is assembled.  The counter then resets and the interval grows by 3
    (capped at 30) to create exponential back-off.

    Only connections with delivery_mode == 'inject' produce a returned message;
    'silent' connections still update their counter but return nothing.

    Args:
        conn: SQLite connection.
        session_id: The watcher session ID.

    Returns:
        A formatted multi-line string if there are new highlights to surface,
        or None if nothing should be injected.
    """
    connections = get_connections(conn, session_id)
    if not connections:
        return None

    messages = []
    for connection in connections:
        if connection['check_mode'] != 'decay':
            continue  # User checks manually via /recall inbox

        # Decay mode: increment counter
        counter = (connection['check_counter'] or 0) + 1
        interval = connection['check_interval'] or 7

        if counter >= interval:
            # Time to check
            last_checked = connection['last_checked_at']
            target_highlights = get_highlights(conn, connection['target_session'], since=last_checked)

            connection_messages = []
            if target_highlights:
                for h in target_highlights:
                    connection_messages.append(f'  - "{h["summary"]}" [{h["tags"]}]')

                if connection_messages and connection['delivery_mode'] == 'inject':
                    topic = connection['topic']
                    target_id = connection['target_session'][:8]
                    connection_messages.insert(
                        0,
                        f'[Cross-session] New from session {target_id}... ({topic}):',
                    )
                    connection_messages.append(
                        f'Use /recall search <keyword> --session {connection["target_session"]}'
                        ' for full context.'
                    )
                    messages.extend(connection_messages)

            # Reset counter, grow interval (decay back-off)
            now = datetime.now(timezone.utc).isoformat()
            new_interval = min(30, interval + 3)
            update_connection_check(conn, connection['id'], 0, new_interval, now,
                                    commit=False)
        else:
            # Just increment counter, preserve last_checked_at
            update_connection_check(
                conn,
                connection['id'],
                counter,
                interval,
                connection['last_checked_at'],
                commit=False,
            )

    if not messages:
        return None

    return '\n'.join(messages)


# ---------------------------------------------------------------------------
# Core hook logic
# ---------------------------------------------------------------------------

# Auto-tags are computed over a rolling window of the session's most recent
# exchanges (not just the exchanges added by this hook run), so a term that
# recurs across several prompts still reaches AUTO_TAG_MIN_FREQUENCY.
AUTO_TAG_WINDOW = 50


def _hook_context(message: str, event: str = 'UserPromptSubmit') -> Dict:
    """Wrap *message* as context Claude will actually read.

    ``systemMessage`` is a warning shown to the USER only. Anything the model
    must act on has to travel via ``hookSpecificOutput.additionalContext``.
    """
    return {"hookSpecificOutput": {"hookEventName": event,
                                   "additionalContext": message}}


def _append_continuation(conn, session_id: str, leading: List[Dict]) -> bool:
    """Append assistant messages that arrived AFTER their exchange was stored.

    Happens whenever a turn spans hook runs: a read stopped at the size cap
    mid-turn, the transcript lagged at Stop time, or another Stop hook blocked
    and Claude continued without a new prompt. The blocks are merged into the
    session's last exchange (text and tool calls), and the FTS entry is
    refreshed. Returns True if anything was appended.
    """
    texts = [m['text'] for m in leading if m.get('text')]
    tools = [t for m in leading for t in (m.get('tools') or [])]
    if not texts and not tools:
        return False
    last = get_last_exchange(conn, session_id)
    if last is None:
        return False
    assistant = last.get('assistant_text') or ''
    if texts and '[...truncated...]' not in assistant:
        add = redact_secrets('\n\n'.join(texts))
        assistant = truncate_text((assistant + '\n\n' + add).strip() if assistant else add,
                                  MAX_ASSISTANT_CHARS)
    tool = last.get('tool_text') or ''
    if tools and '[...truncated...]' not in tool:
        add = redact_secrets('\n'.join(tools))
        tool = truncate_text((tool + '\n' + add).strip() if tool else add, MAX_TOOL_CHARS)
    update_exchange_text(conn, last['id'], assistant, tool or None, commit=False)
    return True


def index_transcript(conn, session_id: str, transcript_path: str,
                     project_path: str = '', project_hash: str = '',
                     now: Optional[str] = None) -> List[Dict]:
    """Incrementally index a session's transcript into the DB (no commit).

    Shared by the UserPromptSubmit, Stop and SessionEnd hooks. Ensures the
    session row exists, reads the transcript from the saved byte offset, and:

      1. appends any LEADING assistant messages (blocks of a turn whose prompt
         was stored on an earlier run) to the session's last exchange;
      2. pairs the rest into exchanges and inserts them;
      3. never consumes a TRAILING user message that has no reply yet (it
         would otherwise be orphaned when the reply lands) — unless the read
         stopped at the size cap right after it, in which case the prompt is
         stored with an empty reply so progress is guaranteed and the reply
         appends later.

    Every run therefore makes forward progress and nothing is orphaned, no
    matter how a turn is split across reads. Refreshes auto-tags over a
    rolling window and runs auto-highlight detection on new exchanges. The
    caller commits. Returns the newly inserted exchange dicts.
    """
    now = now or datetime.now(timezone.utc).isoformat()
    insert_session(conn, session_id=session_id, project_path=project_path,
                   project_hash=project_hash, started_at=now,
                   transcript_path=transcript_path, commit=False)

    session = get_session(conn, session_id)
    byte_offset = session['byte_offset'] if session else 0
    existing_count = (session['exchange_count'] or 0) if session else 0

    current_size = 0
    if transcript_path and os.path.exists(transcript_path):
        current_size = os.path.getsize(transcript_path)

    new_exchanges_list: List[Dict] = []
    if current_size > byte_offset:
        new_messages, new_offset = parse_transcript_from_offset(transcript_path, byte_offset)
        reached_eof = new_offset >= current_size

        # 1) continuation of the previously stored exchange
        k = 0
        while k < len(new_messages) and new_messages[k]['role'] == 'assistant':
            k += 1
        leading, body = new_messages[:k], new_messages[k:]
        if leading and existing_count > 0:
            _append_continuation(conn, session_id, leading)

        # 3) trailing prompt with no reply yet
        allow_pending = False
        if body and body[-1]['role'] == 'user':
            resume = body[-1].get('_start')
            if resume is not None and resume == byte_offset and not reached_eof:
                allow_pending = True      # cap hit right after the prompt: store it pending
            else:
                body = body[:-1]
                if resume is not None:
                    new_offset = resume   # re-read the prompt next run

        # 2) the completed turns
        if body:
            new_exchanges_list = build_new_exchanges(body, existing_count + 1,
                                                     allow_pending=allow_pending)
            if new_exchanges_list:
                insert_exchanges(conn, session_id, new_exchanges_list, commit=False)
        update_session_offset(conn, session_id, new_offset,
                              existing_count + len(new_exchanges_list), commit=False)

    if new_exchanges_list:
        window = get_exchanges(conn, session_id, last_n=AUTO_TAG_WINDOW)
        _store_auto_tags(conn, session_id, window, commit=False)
        auto_detect_highlights(conn, session_id, new_exchanges_list, commit=False)

    return new_exchanges_list


def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Core hook logic, separated from stdin/stdout for testability.

    1. Extract session metadata from *input_data*.
    2. Open (or create) the SQLite DB.
    3. Run one-time v1 migration if a legacy index.json exists.
    4. Index any new transcript data (``index_transcript``).
    5. Poll cross-session connections.
    6. Commit once.
    7. Return a user-facing ``systemMessage`` for ``/recall`` observability
       and/or ``additionalContext`` for everything Claude must act on (the
       proactive suggestion and delivered highlights are combined, never one
       dropped in favour of the other).
    """
    session_id = input_data.get('session_id', 'unknown')
    transcript_path = input_data.get('transcript_path', '')
    # Current Claude Code sends 'prompt' and 'cwd'; older payloads used
    # 'user_prompt'/'project_path'. Accept the new fields, fall back to legacy.
    user_prompt = input_data.get('prompt') or input_data.get('user_prompt', '')
    project_path = input_data.get('cwd') or input_data.get('project_path', '')
    project_hash = input_data.get('project_hash') or compute_project_hash(project_path)

    conn = get_connection(db_path)

    try:
        migrate_from_json(conn)

        index_transcript(conn, session_id, transcript_path,
                         project_path=project_path, project_hash=project_hash)

        # Check connections for incoming highlights (updates written with commit=False)
        connection_msg = _check_connections(conn, session_id)

        conn.commit()

        pieces: List[str] = []
        suggestion = _maybe_suggest_recall(conn, session_id, user_prompt)
        if suggestion:
            pieces.append(suggestion)
        if connection_msg:
            pieces.append(connection_msg)

        out: Dict = {}
        if user_prompt.strip().lower().startswith('/recall'):
            updated = get_session(conn, session_id)
            exchange_count = updated['exchange_count'] or 0 if updated else 0
            log_recall_event(session_id, exchange_count)
            out["systemMessage"] = f"[Observability] Context recall logged at exchange #{exchange_count}"
        if pieces:
            out.update(_hook_context('\n\n'.join(pieces)))
        return out

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Read stdin JSON, run the hook, print result to stdout."""
    try:
        raw = sys.stdin.read(1_000_000)  # 1 MB max
        input_data = json.loads(raw)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        print(f"[context-recall] Hook error: {e}", file=sys.stderr)
        error_output = {
            "systemMessage": "[context-recall] Hook encountered an error. Check logs for details."
        }
        print(json.dumps(error_output), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
