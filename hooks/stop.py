#!/usr/bin/env python3
"""Stop hook for Claude Context Recall plugin.

Runs when Claude finishes responding and indexes the turn that just completed,
so it is recallable immediately (the UserPromptSubmit hook only sees a turn on
the *next* prompt, which meant the final turn of every session was lost).

The transcript file can lag the in-memory turn at Stop time. That is safe: the
indexer stores whatever is on disk and appends any assistant blocks that land
later to the same exchange (see ``index_transcript``), so nothing is orphaned
and no guess about "is the turn flushed yet" is needed.
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
    """Stop hook logic, separated from stdin/stdout for testability. Returns {}."""
    session_id = input_data.get('session_id')
    transcript_path = input_data.get('transcript_path', '')
    if not session_id or not transcript_path:
        return {}

    project_path = input_data.get('cwd') or input_data.get('project_path', '')
    project_hash = compute_project_hash(project_path)

    conn = get_connection(db_path)
    try:
        index_transcript(conn, session_id, transcript_path,
                         project_path=project_path, project_hash=project_hash)
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
        print(f"[context-recall] Stop hook error (non-blocking): {e}", file=sys.stderr)
        print(json.dumps({}), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
