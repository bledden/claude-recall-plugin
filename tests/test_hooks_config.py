#!/usr/bin/env python3
"""hooks.json must register the events recall actually relies on (v2.3).

- 'Stop' indexes every completed turn (the final turn of a session used to be
  lost because capture only ran on the *next* prompt).
- 'SessionStart' with matcher 'compact' carries the post-compaction nudge as
  additionalContext; 'PreCompact' cannot inject context and is gone.
- 'SessionStart' (any) exports the legacy env fallback; 'SessionEnd' finalizes.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
HOOKS_JSON = ROOT / 'hooks' / 'hooks.json'


class TestHooksConfig(unittest.TestCase):
    def setUp(self):
        self.hooks = json.loads(HOOKS_JSON.read_text())['hooks']

    def test_registers_stop_for_per_turn_capture(self):
        self.assertIn('Stop', self.hooks)
        cmds = [h['command'] for e in self.hooks['Stop'] for h in e['hooks']]
        self.assertTrue(any('hooks/stop.py' in c for c in cmds))

    def test_compaction_nudge_rides_on_sessionstart_compact(self):
        self.assertNotIn('PreCompact', self.hooks)   # cannot inject context
        self.assertNotIn('PostCompact', self.hooks)  # not a real event
        matchers = {e.get('matcher') for e in self.hooks['SessionStart']}
        self.assertIn('compact', matchers)
        compact_cmds = [h['command'] for e in self.hooks['SessionStart']
                        if e.get('matcher') == 'compact' for h in e['hooks']]
        self.assertTrue(any('post_compact.py' in c for c in compact_cmds))

    def test_keeps_sessionstart_promptsubmit_sessionend(self):
        self.assertIn('SessionStart', self.hooks)
        self.assertIn('UserPromptSubmit', self.hooks)
        self.assertIn('SessionEnd', self.hooks)

    def test_every_referenced_script_exists(self):
        for event, entries in self.hooks.items():
            for entry in entries:
                for h in entry['hooks']:
                    script = h['command'].split('${CLAUDE_PLUGIN_ROOT}/')[-1]
                    self.assertTrue((ROOT / script).exists(), f"{event}: {script} missing")

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
