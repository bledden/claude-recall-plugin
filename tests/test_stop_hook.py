#!/usr/bin/env python3
"""v2.3: the Stop hook captures each completed turn; SessionEnd captures the last.

Before v2.3 capture ran only on the *next* UserPromptSubmit, so the final turn
of every session was never indexed (6/6 ended sessions on a real store were
missing it). The transcript file may lag the in-memory turn at Stop time, so
the hook consumes the trailing turn only once the payload's
``last_assistant_message`` has reached the file.
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
from db import get_connection, get_exchanges, get_session


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

    def _stop(self, last=''):
        payload = {'session_id': self.sid, 'transcript_path': self.tp, 'cwd': '/tmp/p'}
        if last is not None:
            payload['last_assistant_message'] = last
        return stop_hook(payload, db_path=self.db_path)


class TestStopHook(_Base):
    def test_indexes_the_completed_turn(self):
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'The answer is 42.', 't2'))
        self.assertEqual(self._stop(last='The answer is 42.'), {})
        ex = self._exchanges()
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]['assistant_text'], 'The answer is 42.')

    def test_holds_back_while_transcript_lags(self):
        """Only 'part one' is on disk; the final text is not -> nothing stored yet."""
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'part one', 't2'))
        self._stop(last='and the final answer is 42')
        self.assertEqual(self._exchanges(), [])
        conn = get_connection(self.db_path)
        self.assertEqual(get_session(conn, self.sid)['byte_offset'], 0); conn.close()
        # The file catches up; the same Stop payload now completes the turn.
        _append(self.tp, _entry('assistant', 'and the final answer is 42', 't3'))
        self._stop(last='and the final answer is 42')
        ex = self._exchanges()
        self.assertEqual(len(ex), 1)
        self.assertIn('part one', ex[0]['assistant_text'])
        self.assertIn('final answer is 42', ex[0]['assistant_text'])

    def test_without_last_assistant_message_defers_to_next_prompt(self):
        _append(self.tp, _entry('user', 'Q1', 't1'), _entry('assistant', 'A1', 't2'))
        self._stop(last=None)                     # older Claude Code: field absent
        self.assertEqual(self._exchanges(), [])   # cannot verify -> hold back
        prompt_hook({'session_id': self.sid, 'transcript_path': self.tp,
                     'prompt': 'next', 'cwd': '/tmp/p'}, db_path=self.db_path)
        self.assertEqual(len(self._exchanges()), 1)

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


if __name__ == '__main__':
    unittest.main()
