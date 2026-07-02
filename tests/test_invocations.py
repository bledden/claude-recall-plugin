#!/usr/bin/env python3
"""WI: a durable invocation counter — every recall command records itself so
usage stats come from a direct write, not (expensive, unreliable) transcript
forensics. Schema v3 adds an `invocations` table.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

import db
from db import get_connection, log_invocation


class TestInvocations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'inv.db'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_version_is_at_least_3(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 3)

    def test_fresh_db_has_invocations_table(self):
        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invocations'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_log_invocation_inserts_a_row(self):
        conn = get_connection(self.db_path)
        log_invocation(conn, 'search', args='runpod', session_id='s1', project_hash='ph1')
        r = conn.execute(
            "SELECT command, args, session_id, project_hash FROM invocations"
        ).fetchone()
        conn.close()
        self.assertEqual(r['command'], 'search')
        self.assertEqual(r['args'], 'runpod')
        self.assertEqual(r['session_id'], 's1')
        self.assertEqual(r['project_hash'], 'ph1')

    def test_existing_v2_db_gets_invocations_via_migration(self):
        """A pre-v3 store (has tables, no invocations, user_version 2) gains the
        table and is stamped current on the next connection."""
        conn = get_connection(self.db_path)
        conn.execute("DROP TABLE IF EXISTS invocations")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()

        conn2 = get_connection(self.db_path)
        row = conn2.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invocations'"
        ).fetchone()
        ver = conn2.execute("PRAGMA user_version").fetchone()[0]
        conn2.close()
        self.assertIsNotNone(row, "migration did not recreate invocations")
        self.assertEqual(ver, db.SCHEMA_VERSION)

    def test_record_invocation_honors_recall_db_env(self):
        """record_invocation() logs to the RECALL_DB-overridden store, never raising."""
        import os
        dbp = Path(self.tmp) / 'env.db'
        prev = os.environ.get('RECALL_DB')
        os.environ['RECALL_DB'] = str(dbp)
        try:
            db.record_invocation('last5', 'x')
        finally:
            if prev is None:
                os.environ.pop('RECALL_DB', None)
            else:
                os.environ['RECALL_DB'] = prev
        conn = get_connection(dbp)
        r = conn.execute("SELECT command, args FROM invocations").fetchone()
        conn.close()
        self.assertEqual(r['command'], 'last5')
        self.assertEqual(r['args'], 'x')


if __name__ == '__main__':
    unittest.main()
