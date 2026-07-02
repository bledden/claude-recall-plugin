#!/usr/bin/env python3
"""The capture hook must converge on huge/actively-growing transcripts instead
of wedging (a killed 10 MB read committed nothing, re-read the same chunk every
prompt) — and must not skip lines when a read hits its cap. Verifies both:
successive runs advance and eventually index every exchange with zero loss.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

import prompt_submit
from prompt_submit import run_hook
from db import get_connection, get_session


def _entry(role, text, i):
    return {
        'type': role,
        'message': {'role': role, 'content': [{'type': 'text', 'text': text}]},
        'timestamp': f'2026-01-01T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z',
    }


class TestIndexerConvergence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'conv.db'
        self.transcript = Path(self.tmp) / 't.jsonl'
        self._orig_cap = prompt_submit.MAX_MESSAGES_PER_READ
        prompt_submit.MAX_MESSAGES_PER_READ = 50  # small cap keeps the test fast

    def tearDown(self):
        prompt_submit.MAX_MESSAGES_PER_READ = self._orig_cap
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _count(self):
        conn = get_connection(self.db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM exchanges WHERE session_id='big'").fetchone()[0]
        off = (get_session(conn, 'big') or {}).get('byte_offset', 0) if get_session(conn, 'big') else 0
        conn.close()
        return n, off

    def test_converges_without_loss_on_large_backlog(self):
        n_pairs = 120  # 240 messages, far over the 50-message read cap
        with open(self.transcript, 'w') as f:
            for i in range(n_pairs):
                f.write(json.dumps(_entry('user', f'q{i}', 2 * i)) + '\n')
                f.write(json.dumps(_entry('assistant', f'a{i}', 2 * i + 1)) + '\n')

        inp = {'session_id': 'big', 'transcript_path': str(self.transcript),
               'prompt': 'hi', 'cwd': '/tmp/projP'}

        # First run: partial progress (not the whole backlog in one go).
        run_hook(inp, db_path=self.db_path)
        first, off1 = self._count()
        self.assertGreater(first, 0, "first run indexed nothing (wedged)")
        self.assertLess(first, n_pairs, "did the whole backlog in one read")

        # Successive runs converge to the full set with no lost exchanges.
        for _ in range(12):
            run_hook(inp, db_path=self.db_path)
        total, _ = self._count()
        self.assertEqual(total, n_pairs, "did not converge / lost exchanges")


if __name__ == '__main__':
    unittest.main()
