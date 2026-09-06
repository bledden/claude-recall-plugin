#!/usr/bin/env python3
"""Stop hook for Claude Context Recall plugin.

Runs when Claude finishes responding.  Indexes the turn that just completed so
it is recallable immediately (the UserPromptSubmit hook only sees a turn on the
*next* prompt, which meant the final turn of every session was never captured).

The transcript file can lag the in-memory turn at Stop time, so the indexer is
handed the payload's ``last_assistant_message`` and consumes the trailing turn
only once that text has reached the file; otherwise it holds the turn back and
the next prompt (or SessionEnd) picks it up.
"""

import json
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent))            # hooks/  (prompt_submit)
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from db import get_connection
from utils import compute_project_hash
from prompt_submit import index_transcript


def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Stop hook logic, separated from stdin/stdout for testability.

    Returns {} always — capture is silent.
    """
    session_id = input_data.get('session_id')
    transcript_path = input_data.get('transcript_path', '')
    if not session_id or not transcript_path:
        return {}

    project_path = input_data.get('cwd') or input_data.get('project_path', '')
    project_hash = compute_project_hash(project_path)
    last_assistant_message = input_data.get('last_assistant_message') or ''

    conn = get_connection(db_path)
    try:
        index_transcript(conn, session_id, transcript_path,
                         project_path=project_path, project_hash=project_hash,
                         last_assistant_message=last_assistant_message)
        conn.commit()
    finally:
        conn.close()
    return {}


def main():
    """Read stdin JSON, run the hook, print result to stdout."""
    try:
        raw = sys.stdin.read(1_000_000)  # 1 MB max
        input_data = json.loads(raw)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        # Capture errors are non-blocking; never interfere with the turn.
        print(f"[context-recall] Stop hook error (non-blocking): {e}", file=sys.stderr)
        print(json.dumps({}), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
