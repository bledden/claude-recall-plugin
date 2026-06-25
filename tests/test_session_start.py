#!/usr/bin/env python3
"""WI-1: SessionStart hook exports RECALL_SESSION_ID + RECALL_PROJECT_HASH.

These are written to $CLAUDE_ENV_FILE so the /recall command's script
invocations resolve the current session/project (the old recall.md used
undefined $SESSION_ID / $SESSION_HASH that nothing ever set).
"""

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from session_start import run_hook
from utils import compute_project_hash


class TestSessionStartEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Path(self.tmp) / 'env'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_session_id_and_project_hash(self):
        run_hook({'session_id': 'sess-123', 'cwd': '/tmp/projZ'}, env_file=self.env)
        content = self.env.read_text()
        # Must use `export KEY=value` (the documented $CLAUDE_ENV_FILE format) so
        # the vars actually propagate to the python3 subprocesses the /recall
        # commands spawn — bare `KEY=value` would not export to children.
        self.assertIn('export RECALL_SESSION_ID=sess-123', content)
        self.assertIn(f'export RECALL_PROJECT_HASH={compute_project_hash("/tmp/projZ")}', content)

    def test_no_session_id_writes_nothing(self):
        run_hook({'cwd': '/tmp/x'}, env_file=self.env)
        self.assertFalse(self.env.exists() and self.env.read_text().strip())


if __name__ == '__main__':
    unittest.main()
