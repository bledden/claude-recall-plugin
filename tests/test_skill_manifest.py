#!/usr/bin/env python3
"""v2.4: /recall:recall is a skill Claude can invoke on its own (funes-style model-initiated recall)."""
import re, unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / 'skills' / 'recall' / 'SKILL.md'


class TestRecallSkill(unittest.TestCase):
    def setUp(self):
        text = SKILL.read_text(encoding='utf-8')
        self.fm = re.match(r'^---\n(.*?)\n---\n', text, re.S).group(1)
        self.body = text

    def test_skill_replaces_legacy_command(self):
        self.assertTrue(SKILL.exists())
        self.assertFalse((ROOT / 'commands' / 'recall.md').exists(), 'legacy command must be gone (skill takes precedence anyway)')

    def test_model_invocable(self):
        self.assertNotIn('disable-model-invocation: true', self.fm)
        self.assertNotIn('user-invocable: false', self.fm)
        self.assertIsNotNone(re.search(r'^name: recall$', self.fm, re.M))
        self.assertIn('when_to_use:', self.fm)
        desc = re.search(r'^description: (.*)$', self.fm, re.M).group(1)
        when = re.search(r'^when_to_use: (.*)$', self.fm, re.M).group(1)
        self.assertLess(len(desc) + len(when), 1536, 'listing is truncated at 1,536 chars')
        for phrase in ('earlier', 'previous session', 'command'):
            self.assertIn(phrase, desc.lower())

    def test_allows_python_fallback(self):
        self.assertIn('Bash(python:*)', self.fm)

    def test_has_self_invocation_instructions_and_menu(self):
        self.assertIn('If you invoked this skill yourself', self.body)
        self.assertIn('show_index.py', self.body)
        self.assertIn('$ARGUMENTS', self.body)


if __name__ == '__main__':
    unittest.main()
