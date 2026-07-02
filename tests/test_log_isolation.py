#!/usr/bin/env python3
"""log_recall_event must honor a RECALL_LOG_FILE override so tests (and any
other tooling) never pollute the user's real ~/.claude/recall-events.log —
the bug that filled the production log with s1/s3 test rows.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import log_recall_event


class TestLogIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = Path(self.tmp) / 'recall-events.log'
        self._saved = os.environ.get('RECALL_LOG_FILE')
        os.environ['RECALL_LOG_FILE'] = str(self.log)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('RECALL_LOG_FILE', None)
        else:
            os.environ['RECALL_LOG_FILE'] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_to_override_not_the_real_log(self):
        # Snapshot the real log so we can prove this call did not touch it.
        real = Path.home() / '.claude' / 'recall-events.log'
        before = real.read_text(errors='replace') if real.exists() else ''

        log_recall_event('iso-test-sess', 3)

        self.assertTrue(self.log.exists(), "override log file not written")
        content = self.log.read_text()
        self.assertIn('session=iso-test-sess', content)
        self.assertIn('CONTEXT_RECALL_TRIGGERED', content)
        # The real default log is unchanged by this write.
        after = real.read_text(errors='replace') if real.exists() else ''
        self.assertEqual(before, after, "real event log was modified")


if __name__ == '__main__':
    unittest.main()
