#!/usr/bin/env python3
"""WI-2: prompt_submit must read the CURRENT Claude Code hook fields.

Current UserPromptSubmit payload provides 'prompt' and 'cwd' (not 'user_prompt'
/ 'project_path' / 'project_hash'). project_hash must be DERIVED from cwd.
"""

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import run_hook
from db import get_connection, get_session
from utils import compute_project_hash


class TestRunHookInputFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_prompt_field_for_recall_detection(self):
        """'/recall' typed in the 'prompt' field triggers the observability message."""
        result = run_hook(
            {'session_id': 's1', 'transcript_path': '',
             'prompt': '/recall last5', 'cwd': '/tmp/projX'},
            db_path=self.db_path,
        )
        self.assertIn('systemMessage', result)
        self.assertIn('recall', result['systemMessage'].lower())

    def test_derives_project_hash_from_cwd(self):
        """No project_hash in payload -> derive a stable non-empty hash from cwd."""
        run_hook(
            {'session_id': 's2', 'transcript_path': '',
             'prompt': 'hello', 'cwd': '/tmp/projY'},
            db_path=self.db_path,
        )
        conn = get_connection(self.db_path)
        sess = get_session(conn, 's2')
        conn.close()
        self.assertEqual(sess['project_path'], '/tmp/projY')
        self.assertEqual(sess['project_hash'], compute_project_hash('/tmp/projY'))

    def test_legacy_fields_still_accepted(self):
        """Back-compat: old 'user_prompt'/'project_path' still work as a fallback."""
        result = run_hook(
            {'session_id': 's3', 'transcript_path': '',
             'user_prompt': '/recall stats', 'project_path': '/tmp/legacy'},
            db_path=self.db_path,
        )
        self.assertIn('systemMessage', result)


if __name__ == '__main__':
    unittest.main()
