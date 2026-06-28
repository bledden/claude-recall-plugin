#!/usr/bin/env python3
"""Deterministic proactive recall suggestion (reliability fix for the skill).

The recall-assistant skill's "explicit context-loss" detection relied on the
model noticing phrases like "didn't we discuss..." — which it did only
sometimes. Move that one signal into the UserPromptSubmit hook so it fires
deterministically, gated on skill_enabled (still opt-in, default off).
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import run_hook
from db import get_connection, set_session_config


class TestProactiveRecallSuggestion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _enable_skill(self, session_id):
        conn = get_connection(self.db_path)
        set_session_config(conn, session_id, 'skill_enabled', True)
        conn.commit()
        conn.close()

    def _run(self, session_id, prompt):
        return run_hook(
            {'session_id': session_id, 'transcript_path': '', 'prompt': prompt,
             'cwd': '/tmp/projP'},
            db_path=self.db_path,
        )

    def test_suggests_recall_on_context_loss_when_enabled(self):
        self._run('s1', 'hello')          # create the session row
        self._enable_skill('s1')
        result = self._run('s1', "wait, didn't we already discuss the cache fix?")
        self.assertIn('systemMessage', result)
        self.assertIn('recall', result['systemMessage'].lower())

    def test_no_suggestion_when_skill_disabled(self):
        self._run('s2', 'hello')          # skill_enabled defaults to false
        result = self._run('s2', "didn't we already discuss this earlier?")
        self.assertEqual(result, {})

    def test_no_suggestion_on_ordinary_prompt_when_enabled(self):
        self._run('s3', 'hello')
        self._enable_skill('s3')
        result = self._run('s3', 'please add a unit test for the parser')
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
