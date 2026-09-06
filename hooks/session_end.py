#!/usr/bin/env python3
"""SessionEnd hook for Claude Context Recall plugin v2.

Runs when a Claude session ends.  Indexes whatever the transcript still holds
(so the session's final turn is captured), then marks the session as ended by
recording the current UTC timestamp in the ended_at column.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent))            # hooks/  (prompt_submit)
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from db import get_connection, get_session, end_session, DB_PATH
from prompt_submit import index_transcript

# Stay inside the hook's 10s timeout with margin.
DRAIN_BUDGET_SECONDS = 7.0


# ---------------------------------------------------------------------------
# Core hook logic
# ---------------------------------------------------------------------------

def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """SessionEnd hook logic, separated from stdin/stdout for testability.

    Records the session's ended_at timestamp in the DB.

    Args:
        input_data: Dict parsed from the hook's stdin JSON.
        db_path: Override path for the database (used in tests).

    Returns:
        {} always (no systemMessage needed for session-end events).
    """
    session_id = input_data.get('session_id')
    if not session_id:
        return {}

    conn = get_connection(db_path or DB_PATH)
    try:
        session = get_session(conn, session_id)
        if session is None:
            return {}

        # Final catch-up: drain the whole backlog in bounded, committed passes
        # (each pass reads at most 2 MB / 1000 messages) until no progress or
        # the time budget is spent — one capped pass could leave the last
        # turns unread on a long session.
        transcript_path = input_data.get('transcript_path') or session.get('transcript_path') or ''
        if transcript_path:
            deadline = time.monotonic() + DRAIN_BUDGET_SECONDS
            while True:
                before = (get_session(conn, session_id) or {}).get('byte_offset', 0)
                index_transcript(conn, session_id, transcript_path,
                                 project_path=session.get('project_path') or input_data.get('cwd', ''),
                                 project_hash=session.get('project_hash') or '')
                conn.commit()
                after = (get_session(conn, session_id) or {}).get('byte_offset', 0)
                if after <= before or time.monotonic() > deadline:
                    break

        ended_at = datetime.now(timezone.utc).isoformat()
        end_session(conn, session_id, ended_at)

        return {}

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
        # Session-end errors are non-blocking; log to stderr and continue
        print(
            f"[context-recall] SessionEnd hook error (non-blocking): {e}",
            file=sys.stderr,
        )
        print(json.dumps({}), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
