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

    def test_hook_commands_have_python_fallback(self):
        """Hooks must not hard-code bare `python3` — on Linux boxes where
        python3 isn't on PATH (or it's `python`), every hook silently fails.
        Each command must probe for python3 and fall back to python.
        """
        for event, entries in self.hooks.items():
            for entry in entries:
                for h in entry.get('hooks', []):
                    cmd = h['command']
                    self.assertIn('command -v python3', cmd,
                                  f"{event} hook has no python3 probe: {cmd}")
                    self.assertIn('PY=python', cmd,
                                  f"{event} hook has no python fallback: {cmd}")


if __name__ == '__main__':
    unittest.main()
