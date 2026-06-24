#!/usr/bin/env python3
"""WI-4 + WI-1: hooks.json must register CURRENT Claude Code events.

- 'PreCompact' is the real compaction event ('PostCompact' is not, so the
  nudge never fired in production).
- A 'SessionStart' hook is needed to export RECALL_SESSION_ID/HASH (WI-1).
"""

import json
import unittest
from pathlib import Path

HOOKS_JSON = Path(__file__).parent.parent / 'hooks' / 'hooks.json'


class TestHooksConfig(unittest.TestCase):
    def setUp(self):
        self.hooks = json.loads(HOOKS_JSON.read_text())['hooks']

    def test_uses_precompact_not_postcompact(self):
        self.assertIn('PreCompact', self.hooks)
        self.assertNotIn('PostCompact', self.hooks)

    def test_registers_sessionstart(self):
        self.assertIn('SessionStart', self.hooks)

    def test_keeps_userpromptsubmit_and_sessionend(self):
        self.assertIn('UserPromptSubmit', self.hooks)
        self.assertIn('SessionEnd', self.hooks)


if __name__ == '__main__':
    unittest.main()
