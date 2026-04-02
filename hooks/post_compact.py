#!/usr/bin/env python3
"""PostCompact nudge hook for Claude Context Recall plugin v2.

Runs after a context compaction event.  Injects a systemMessage summarising
session history so the model can recover relevant context after compaction.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from db import get_connection, get_session, get_exchanges, DB_PATH

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUDGE_PREVIEW_COUNT = 5
NUDGE_MAX_CHARS = 500


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def build_nudge_message(
    session_exchange_count: int,
    project_exchange_count: int,
    recent_previews: List[str],
    tags: List[str],
) -> str:
    """Build the context-recovery nudge message injected after compaction.

    Args:
        session_exchange_count: Number of exchanges indexed for this session.
        project_exchange_count: Total exchanges across all project sessions.
        recent_previews: Short preview strings for the most recent exchanges.
        tags: Auto-tags associated with this session.

    Returns:
        Formatted nudge string.
    """
    lines = [
        f"[Context Compacted] This session has {session_exchange_count} exchanges indexed.",
        f"{project_exchange_count} total exchanges across this project's history.",
    ]

    if tags:
        lines.append(f"Recent topics: {', '.join(tags)}")

    if recent_previews:
        lines.append("Last exchanges:")
        for preview in recent_previews:
            lines.append(f'  - "{preview}"')

    lines.append("Use /recall to recover full conversation context.")

    result = '\n'.join(lines)
    if len(result) > NUDGE_MAX_CHARS:
        result = result[:NUDGE_MAX_CHARS - 20] + '\n[...truncated...]'
    return result


# ---------------------------------------------------------------------------
# Core hook logic
# ---------------------------------------------------------------------------

def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """PostCompact hook logic, separated from stdin/stdout for testability.

    Queries the DB for the current session's stats and builds a nudge message
    that is returned as a systemMessage to help Claude recover context.

    Args:
        input_data: Dict parsed from the hook's stdin JSON.
        db_path: Override path for the database (used in tests).

    Returns:
        {"systemMessage": <nudge>} on success, {} if session unknown.
    """
    session_id = input_data.get('session_id')
    if not session_id:
        return {}

    conn = get_connection(db_path or DB_PATH)
    try:
        session = get_session(conn, session_id)
        if session is None:
            return {}

        # Session exchange count
        session_exchange_count = session.get('exchange_count', 0) or 0

        # Project-wide total (sum exchange_count for all sessions with same project_hash)
        project_hash = session.get('project_hash', '')
        if project_hash:
            row = conn.execute(
                "SELECT COALESCE(SUM(exchange_count), 0) AS total "
                "FROM sessions WHERE project_hash = ?",
                (project_hash,),
            ).fetchone()
            project_exchange_count = row['total'] if row else session_exchange_count
        else:
            project_exchange_count = session_exchange_count

        # Last N exchange previews
        recent_exchanges = get_exchanges(conn, session_id, last_n=NUDGE_PREVIEW_COUNT)
        recent_previews = [ex['preview'] for ex in recent_exchanges if ex.get('preview')]

        # Top 5 auto-tags for this session
        rows = conn.execute(
            "SELECT tag FROM tags WHERE session_id = ? "
            "AND source = 'auto' "
            "GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 5",
            (session_id,),
        ).fetchall()
        tags = [r['tag'] for r in rows]

        nudge = build_nudge_message(
            session_exchange_count=session_exchange_count,
            project_exchange_count=project_exchange_count,
            recent_previews=recent_previews,
            tags=tags,
        )

        return {"systemMessage": nudge}

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
        print(f"[context-recall] PostCompact error: {e}", file=sys.stderr)
        error_output = {
            "systemMessage": "[context-recall] PostCompact hook encountered an error. Check logs for details."
        }
        print(json.dumps(error_output), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
