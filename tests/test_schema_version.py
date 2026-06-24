#!/usr/bin/env python3
"""WI-25: db.py tracks a schema version so future migrations have a managed path.

The only prior migration was the one-shot v1 index.json -> SQLite import; there
was no schema_version, so v2 -> v3 (vector/tier tables) had no framework.
"""

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

import db
from db import get_connection


class TestSchemaVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 'sv.db'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _user_version(self, path):
        conn = get_connection(path)
        v = conn.execute('PRAGMA user_version').fetchone()[0]
        conn.close()
        return v

    def test_schema_version_constant_is_set(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 1)

    def test_fresh_db_is_stamped_current(self):
        """A brand-new DB ends up at the current SCHEMA_VERSION."""
        self.assertEqual(self._user_version(self.db_path), db.SCHEMA_VERSION)

    def test_pre_versioning_db_is_upgraded(self):
        """An existing DB at user_version 0 is stamped to current on reconnect."""
        conn = get_connection(self.db_path)
        conn.execute('PRAGMA user_version = 0')  # simulate a pre-versioning store
        conn.commit()
        conn.close()
        self.assertEqual(self._user_version(self.db_path), db.SCHEMA_VERSION)

    def test_reconnect_is_idempotent(self):
        self._user_version(self.db_path)
        self.assertEqual(self._user_version(self.db_path), db.SCHEMA_VERSION)


if __name__ == '__main__':
    unittest.main()
