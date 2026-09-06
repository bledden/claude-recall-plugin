"""Test isolation: never let the suite touch the user's real recall data.

Two leaks have bitten in the past: the event log was hardcoded to
`~/.claude/recall-events.log` (fixed in 2.2.3 via RECALL_LOG_FILE), and
`record_invocation()` opened the DEFAULT database from script `main()` calls
(fixed in 2.3.1 via RECALL_DB). This autouse fixture redirects both, and unsets
CLAUDE_CODE_SESSION_ID so nothing is attributed to a live session.
"""

import os
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_recall_store():
    """Point BOTH the event log and the default DB at temp files.

    Tests pass an explicit db_path for their own data, but every script
    ``main()`` calls ``record_invocation()``, which opens ``get_connection()``
    with NO path -> the user's real ``recall.db`` unless RECALL_DB is set. Before
    this fixture, each suite run appended fixture invocations (``last0``,
    ``last-3``, ``search xyzzy...``) to the real store and could even migrate its
    schema. Also drop the session id so nothing is attributed to a live session.
    """
    tmpdir = tempfile.mkdtemp(prefix="recall-test-")
    saved = {k: os.environ.get(k) for k in ("RECALL_LOG_FILE", "RECALL_DB", "CLAUDE_CODE_SESSION_ID")}
    os.environ["RECALL_LOG_FILE"] = os.path.join(tmpdir, "recall-events.log")
    os.environ["RECALL_DB"] = os.path.join(tmpdir, "recall.db")
    os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
