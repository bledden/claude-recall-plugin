#!/usr/bin/env python3
"""v2.3/v2.4: the Stop hook captures each completed turn; SessionEnd drains the rest.

Before v2.3 capture ran only on the *next* UserPromptSubmit, so the final turn
of every session was never indexed. The transcript file may lag the in-memory
turn at Stop time: whatever is on disk is stored now and later assistant blocks
are appended to the same exchange, so nothing is orphaned and no
``last_assistant_message`` guesswork is needed.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from stop import run_hook as stop_hook
from session_end import run_hook as end_hook
from prompt_submit import run_hook as prompt_hook
from db import get_connection, get_exchanges, get_session, search_exchanges_fts


def _entry(role, text, ts):
    return {'type': role, 'timestamp': ts,
            'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}}


def _append(path, *entries):
    with open(path, 'a', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'test.db'
        self.tp = str(Path(self.tmp) / 't.jsonl')
        self.sid = 'sess-stop'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _exchanges(self):
        conn = get_connection(self.db_path)
        try:
            return get_exchanges(conn, self.sid)
        finally:
            conn.close()

    def _stop(self, **extra):
        payload = {'session_id': self.sid, 'transcript_path': self.tp, 'cwd': '/tmp/p', **extra}
        return stop_hook(payload, db_path=self.db_path)


class TestStopHook(_Base):
    def test_indexes_the_completed_turn(self):
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'The answer is 42.', 't2'))
        self.assertEqual(self._stop(last_assistant_message='The answer is 42.'), {})
        ex = self._exchanges()
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]['assistant_text'], 'The answer is 42.')

    def test_lagging_transcript_is_completed_by_appending(self):
        """Only 'part one' is on disk at Stop time; the rest lands later."""
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'part one', 't2'))
        self._stop()
        self.assertEqual(self._exchanges()[0]['assistant_text'], 'part one')
        _append(self.tp, _entry('assistant', 'and the final answer is 42', 't3'))
        self._stop()
        ex = self._exchanges()
        self.assertEqual(len(ex), 1)
        self.assertIn('part one', ex[0]['assistant_text'])
        self.assertIn('final answer is 42', ex[0]['assistant_text'])
        conn = get_connection(self.db_path)
        try:   # the FTS entry followed the update
            self.assertEqual(len(search_exchanges_fts(conn, 'final', session_id=self.sid)), 1)
        finally:
            conn.close()

    def test_prompt_without_reply_is_left_for_later(self):
        _append(self.tp, _entry('user', 'Q1', 't1'))
        self._stop()
        self.assertEqual(self._exchanges(), [])
        _append(self.tp, _entry('assistant', 'A1', 't2'))
        self._stop()
        self.assertEqual([(e['user_text'], e['assistant_text']) for e in self._exchanges()], [('Q1', 'A1')])

    def test_blocked_stop_continuation_is_appended(self):
        """Another Stop hook blocks; Claude continues with assistant-only records."""
        _append(self.tp, _entry('user', 'Fix the bug', 't1'), _entry('assistant', 'First attempt complete.', 't2'))
        self._stop(stop_hook_active=False)
        _append(self.tp, _entry('assistant', 'Validation caught an error; here is the corrected answer.', 't3'))
        self._stop(stop_hook_active=True)
        ex = self._exchanges()
        self.assertEqual(len(ex), 1)
        self.assertIn('corrected answer', ex[0]['assistant_text'])

    def test_missing_transcript_is_a_noop(self):
        self.assertEqual(stop_hook({'session_id': self.sid}, db_path=self.db_path), {})


class TestSessionEndIndexesFinalTurn(_Base):
    def test_final_turn_is_captured_at_session_end(self):
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'A1', 't2'))
        prompt_hook({'session_id': self.sid, 'transcript_path': self.tp,
                     'prompt': 'Q2', 'cwd': '/tmp/p'}, db_path=self.db_path)
        _append(self.tp, _entry('user', 'Q2', 't3'), _entry('assistant', 'A2 final', 't4'))
        self.assertEqual(end_hook({'session_id': self.sid, 'transcript_path': self.tp},
                                  db_path=self.db_path), {})
        ex = self._exchanges()
        self.assertEqual([e['user_text'] for e in ex], ['Q1', 'Q2'])
        self.assertEqual(ex[1]['assistant_text'], 'A2 final')
        conn = get_connection(self.db_path)
        self.assertIsNotNone(get_session(conn, self.sid)['ended_at']); conn.close()

    def test_uses_stored_transcript_path_when_payload_lacks_it(self):
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'A1', 't2'))
        prompt_hook({'session_id': self.sid, 'transcript_path': self.tp,
                     'prompt': 'x', 'cwd': '/tmp/p'}, db_path=self.db_path)   # registers the path
        _append(self.tp, _entry('user', 'Q2', 't3'), _entry('assistant', 'A2', 't4'))
        end_hook({'session_id': self.sid}, db_path=self.db_path)
        self.assertEqual(len(self._exchanges()), 2)

    def test_drains_a_backlog_larger_than_one_pass(self):
        prompt_hook({'session_id': self.sid, 'transcript_path': self.tp,
                     'prompt': 'x', 'cwd': '/tmp/p'}, db_path=self.db_path)
        _append(self.tp, *[e for i in range(600) for e in (_entry('user', f'Q{i}', f't{2*i}'),
                                                          _entry('assistant', f'A{i}', f't{2*i+1}'))])
        end_hook({'session_id': self.sid, 'transcript_path': self.tp}, db_path=self.db_path)
        self.assertEqual(len(self._exchanges()), 600)


if __name__ == '__main__':
    unittest.main()
