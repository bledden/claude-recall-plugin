"""Test isolation: never let the suite touch the user's real recall data.

The recall event log (`log_recall_event`) wrote to a hardcoded
`~/.claude/recall-events.log`, so running the tests polluted the production log
with test session rows (the s1/s3 pollution we found). This autouse fixture
points RECALL_LOG_FILE at a temp file for the whole test session; the DB is
already isolated because tests pass an explicit `db_path`.
"""

import os
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_recall_log():
    tmpdir = tempfile.mkdtemp(prefix="recall-test-log-")
    prev = os.environ.get("RECALL_LOG_FILE")
    os.environ["RECALL_LOG_FILE"] = os.path.join(tmpdir, "recall-events.log")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("RECALL_LOG_FILE", None)
        else:
            os.environ["RECALL_LOG_FILE"] = prev
