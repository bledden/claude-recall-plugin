#!/usr/bin/env python3
"""SessionEnd hook for Claude Context Recall plugin v2.

Runs when a Claude session ends.  Marks the session as ended in the SQLite
database by recording the current UTC timestamp in the ended_at column.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from db import get_connection, get_session, end_session, DB_PATH


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
