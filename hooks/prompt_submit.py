#!/usr/bin/env python3
"""SQLite-backed prompt submit hook for Claude Context Recall plugin v2.

Runs on every UserPromptSubmit event.  Reads JSON from stdin, incrementally
indexes new exchanges into SQLite, runs auto-tagging, and handles v1->v2
migration from the legacy JSON index.

Replaces the old save_context_snapshot.py (which is left in place until
Task 9 cleanup).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from utils import extract_text_content, make_preview, truncate_text, MAX_CHARS_PER_MESSAGE
from db import (get_connection, insert_session, get_session, insert_exchanges,
                update_session_offset, get_exchanges, insert_tag, DB_PATH,
                get_connections, get_highlights, update_connection_check)
from auto_tagger import compute_auto_tags
from highlight import auto_detect_highlights

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_FILE = Path.home() / '.claude' / 'recall-events.log'
LEGACY_INDEX_FILE = Path.home() / '.claude' / 'context-recall' / 'index.json'


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def parse_transcript_from_offset(
    transcript_path: str,
    byte_offset: int = 0,
) -> Tuple[List[Dict], int]:
    """Parse transcript JSONL starting from a byte offset.

    Opens the file, seeks to *byte_offset*, and reads every subsequent
    JSONL line that contains a user or assistant message.

    Args:
        transcript_path: Path to the JSONL transcript file.
        byte_offset: Position to seek to before reading.

    Returns:
        Tuple of (messages, new_byte_offset) where each message is a dict
        with keys: role, text, timestamp.
    """
    messages: List[Dict] = []
    new_offset = byte_offset

    if not transcript_path or not os.path.exists(transcript_path):
        return messages, new_offset

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            if byte_offset > 0:
                f.seek(byte_offset)

            for line in f:
                line_stripped = line.strip()
                if not line_stripped:
                    continue

                try:
                    entry = json.loads(line_stripped)
                    role = entry.get('type', '') or entry.get('role', '')
                    if role not in ('user', 'assistant'):
                        message_obj = entry.get('message', {})
                        role = message_obj.get('role', '')

                    if role in ('user', 'assistant'):
                        message_obj = entry.get('message', {})
                        text = extract_text_content(message_obj)
                        timestamp = entry.get('timestamp', '')

                        if text:
                            messages.append({
                                'role': role,
                                'text': text,
                                'timestamp': timestamp,
                            })
                except json.JSONDecodeError:
                    continue

            new_offset = f.tell()

    except Exception:
        pass

    return messages, new_offset


# ---------------------------------------------------------------------------
# Exchange building
# ---------------------------------------------------------------------------

def build_new_exchanges(
    messages: List[Dict],
    start_idx: int = 1,
) -> List[Dict]:
    """Pair consecutive user/assistant messages into exchanges.

    Each exchange dict has: idx, preview, timestamp, user_text, assistant_text.

    Args:
        messages: List of message dicts with role, text, timestamp.
        start_idx: Starting exchange index number.

    Returns:
        List of exchange dicts.
    """
    exchanges: List[Dict] = []
    i = 0
    exchange_idx = start_idx

    while i < len(messages):
        if messages[i]['role'] == 'user':
            user_msg = messages[i]
            if i + 1 < len(messages) and messages[i + 1]['role'] == 'assistant':
                assistant_msg = messages[i + 1]
                exchanges.append({
                    'idx': exchange_idx,
                    'preview': make_preview(user_msg['text']),
                    'timestamp': user_msg.get('timestamp', ''),
                    'user_text': truncate_text(user_msg['text'], MAX_CHARS_PER_MESSAGE),
                    'assistant_text': truncate_text(assistant_msg['text'], MAX_CHARS_PER_MESSAGE),
                })
                exchange_idx += 1
                i += 2
            else:
                i += 1
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

    No-op if the legacy file does not exist.

    Args:
        conn: SQLite connection.
        legacy_path: Override path for the legacy file.
    """
    if legacy_path is None:
        legacy_path = LEGACY_INDEX_FILE

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

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------

def _store_auto_tags(conn, session_id: str, exchanges: List[Dict]) -> None:
    """Compute and persist auto-tags for a session's exchanges.

    Args:
        conn: SQLite connection.
        session_id: Session to tag.
        exchanges: Exchange dicts to derive tags from.
    """
    tags = compute_auto_tags(exchanges)
    for tag in tags:
        insert_tag(conn, tag, session_id, exchange_idx=None, source='auto')


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
        if connection['check_mode'] == 'explicit':
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
            update_connection_check(conn, connection['id'], 0, new_interval, now)
        else:
            # Just increment counter, preserve last_checked_at
            update_connection_check(
                conn,
                connection['id'],
                counter,
                interval,
                connection['last_checked_at'],
            )

    if not messages:
        return None

    return '\n'.join(messages)


# ---------------------------------------------------------------------------
# Core hook logic
# ---------------------------------------------------------------------------

def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Core hook logic, separated from stdin/stdout for testability.

    1. Extract session metadata from *input_data*.
    2. Open (or create) the SQLite DB.
    3. Run one-time v1 migration if a legacy index.json exists.
    4. Ensure the session row exists.
    5. Incrementally parse any new transcript data and insert exchanges.
    6. Run auto-tagger over the full session.
    7. If the user typed ``/recall``, log the event and return a systemMessage.

    Args:
        input_data: Dict parsed from the hook's stdin JSON.
        db_path: Override path for the database (used in tests).

    Returns:
        Dict to be printed as JSON to stdout.  Empty dict ``{}`` for
        normal prompts; ``{"systemMessage": "..."}`` for /recall.
    """
    session_id = input_data.get('session_id', 'unknown')
    transcript_path = input_data.get('transcript_path', '')
    user_prompt = input_data.get('user_prompt', '')
    project_path = input_data.get('project_path', '')
    project_hash = input_data.get('project_hash', '')

    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection(db_path)

    try:
        # One-time v1 migration (no-op if legacy file absent)
        migrate_from_json(conn)

        # Ensure session exists
        insert_session(
            conn,
            session_id=session_id,
            project_path=project_path,
            project_hash=project_hash,
            started_at=now,
            transcript_path=transcript_path,
        )

        # Read current offset
        session = get_session(conn, session_id)
        byte_offset = session['byte_offset'] if session else 0

        # Check if transcript has grown
        current_size = 0
        if transcript_path and os.path.exists(transcript_path):
            current_size = os.path.getsize(transcript_path)

        new_exchanges_list: List[Dict] = []

        if current_size > byte_offset:
            new_messages, new_offset = parse_transcript_from_offset(transcript_path, byte_offset)

            if new_messages:
                # Determine start index from existing exchanges
                existing = get_exchanges(conn, session_id)
                start_idx = len(existing) + 1

                new_exchanges_list = build_new_exchanges(new_messages, start_idx)

                if new_exchanges_list:
                    insert_exchanges(conn, session_id, new_exchanges_list)

                total_count = len(existing) + len(new_exchanges_list)
                update_session_offset(conn, session_id, new_offset, total_count)
            else:
                # File grew but no complete messages yet — still advance offset
                update_session_offset(
                    conn,
                    session_id,
                    new_offset,
                    session['exchange_count'] if session else 0,
                )

        # Auto-tag the full session
        all_exchanges = get_exchanges(conn, session_id)
        _store_auto_tags(conn, session_id, all_exchanges)

        # Auto-detect highlights (if enabled)
        if new_exchanges_list:
            auto_detect_highlights(conn, session_id, new_exchanges_list)

        # Check connections for incoming highlights
        connection_msg = _check_connections(conn, session_id)

        # Handle /recall
        if user_prompt.strip().lower().startswith('/recall'):
            exchange_count = len(all_exchanges)
            log_recall_event(session_id, exchange_count)
            return {
                "systemMessage": f"[Observability] Context recall logged at exchange #{exchange_count}"
            }
        elif connection_msg:
            return {"systemMessage": connection_msg}

        return {}

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Read stdin JSON, run the hook, print result to stdout."""
    try:
        input_data = json.load(sys.stdin)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        error_output = {
            "systemMessage": f"[context-recall] Hook error (non-blocking): {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
