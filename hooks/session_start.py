#!/usr/bin/env python3
"""SessionStart hook for Claude Context Recall plugin.

Exports the current session id and project hash as environment variables so the
/recall command's script invocations can resolve "the current session" without
relying on undefined shell variables.

Claude Code makes variables written to ``$CLAUDE_ENV_FILE`` (one ``KEY=value``
per line) available to subsequent hook/command shell invocations in the session.
We write:

    RECALL_SESSION_ID=<session_id>
    RECALL_PROJECT_HASH=<hash(cwd)>

The recall scripts already read ``RECALL_SESSION_ID`` (with a ``--session``
override); ``RECALL_PROJECT_HASH`` is consumed by the project-scoped commands.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from utils import compute_project_hash


def run_hook(input_data: Dict, env_file: Optional[Path] = None) -> Dict:
    """SessionStart hook logic, separated from stdin/stdout for testability.

    Args:
        input_data: Dict parsed from the hook's stdin JSON.
        env_file: Override for $CLAUDE_ENV_FILE (used in tests).

    Returns:
        {} always (no systemMessage needed for session-start events).
    """
    session_id = input_data.get('session_id')
    if not session_id:
        return {}

    cwd = input_data.get('cwd') or input_data.get('project_path', '')
    project_hash = compute_project_hash(cwd)

    env_path = env_file or os.environ.get('CLAUDE_ENV_FILE')
    if env_path:
        # Use `export KEY=value` — the documented $CLAUDE_ENV_FILE format — so the
        # vars are exported to the python3 subprocesses the /recall commands spawn.
        with open(env_path, 'a', encoding='utf-8') as f:
            f.write(f"export RECALL_SESSION_ID={session_id}\n")
            f.write(f"export RECALL_PROJECT_HASH={project_hash}\n")

    return {}


def main():
    """Read stdin JSON, run the hook, print result to stdout."""
    try:
        raw = sys.stdin.read(1_000_000)  # 1 MB max
        input_data = json.loads(raw)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        # SessionStart errors are non-blocking; log to stderr and continue.
        print(
            f"[context-recall] SessionStart hook error (non-blocking): {e}",
            file=sys.stderr,
        )
        print(json.dumps({}), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
