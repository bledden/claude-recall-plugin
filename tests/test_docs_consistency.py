#!/usr/bin/env python3
"""Docs-consistency tests for the recall plugin (DOCS audit cluster).

Pure documentation tests: they read README.md, commands/recall.md, and
skills/recall-assistant/SKILL.md and assert cross-file consistency. They touch
NO database and import NO scripts — safe to run in isolation.

Work items proven here:
- WI-22: 'export --session <id> --json' is removed everywhere; export examples
         do NOT carry a --json flag (the subparser has none — it always emits JSON).
- WI-11: README's command reference includes 'sessions --project <name>' and the
         lastN range; the three docs share one canonical command surface.
- WI-23: commands/recall.md argument-hint reflects the real surface
         (highlight/connect/disconnect/inbox/config/tag/prune/export).
- WI-13: docs note that 'tags --project' expects a project HASH (distinct from
         'sessions --project' which takes a name/path).
- WI-10: docs document that --project does an unanchored SUBSTRING path match.
- WI-26: README has a differentiators section vs native Claude Code
         (cross-project FTS, tagging, highlight/connection sharing).
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / 'README.md'
_RECALL_MD = _ROOT / 'commands' / 'recall.md'
_SKILL_MD = _ROOT / 'skills' / 'recall-assistant' / 'SKILL.md'


def _read(p: Path) -> str:
    return p.read_text(encoding='utf-8')


class TestWI22ExportNoJsonFlag(unittest.TestCase):
    """The '--json' flag must not appear on any export example in any doc."""

    def test_no_export_json_flag_anywhere(self):
        for p in (_README, _RECALL_MD, _SKILL_MD):
            text = _read(p)
            # The export subparser only has --session; '--json' must not ride along.
            self.assertNotIn('export --session <id> --json', text,
                             f"{p.name} still advertises 'export --session <id> --json'")
            self.assertNotIn('--json', text,
                             f"{p.name} still references a --json flag (export always emits JSON)")

    def test_export_example_present_without_flag(self):
        for p in (_README, _RECALL_MD, _SKILL_MD):
            text = _read(p)
            self.assertIn('export --session', text,
                          f"{p.name} should still document 'export --session'")


class TestWI11CanonicalCommandSurface(unittest.TestCase):
    """README's reference includes sessions --project and lastN; docs agree."""

    def test_readme_has_sessions_project(self):
        self.assertIn('/recall sessions --project <name>', _read(_README),
                      "README must list 'sessions --project <name>'")

    def test_recallmd_and_skill_have_sessions_project(self):
        self.assertIn('sessions --project <name>', _read(_RECALL_MD))
        self.assertIn('/recall sessions --project <name>', _read(_SKILL_MD))

    def test_readme_documents_lastN_range(self):
        text = _read(_README)
        self.assertTrue(
            re.search(r'lastN', text),
            "README must document the lastN range (any positive N), not just last5")

    def test_recallmd_documents_lastN_range(self):
        self.assertIn('lastN', _read(_RECALL_MD))

    def test_skill_documents_lastN_range(self):
        self.assertIn('lastN', _read(_SKILL_MD))


class TestWI23ArgumentHint(unittest.TestCase):
    """commands/recall.md argument-hint reflects the real command surface."""

    def setUp(self):
        text = _read(_RECALL_MD)
        m = re.search(r'^argument-hint:\s*"(.*)"\s*$', text, re.MULTILINE)
        self.assertIsNotNone(m, "recall.md must have an argument-hint frontmatter line")
        self.hint = m.group(1)

    def test_hint_mentions_advanced_commands(self):
        for cmd in ('highlight', 'connect', 'disconnect', 'inbox', 'config',
                    'tag', 'prune', 'export'):
            self.assertIn(cmd, self.hint,
                          f"argument-hint must mention '{cmd}'")

    def test_hint_points_to_full_list(self):
        self.assertIn('see full list', self.hint.lower(),
                      "argument-hint should point to the full list below")


class TestWI13TagsProjectIsHash(unittest.TestCase):
    """Docs note 'tags --project' takes a project HASH, distinct from sessions."""

    def test_recallmd_notes_hash(self):
        text = _read(_RECALL_MD)
        self.assertIn('HASH', text)
        self.assertRegex(text, r'tags --project.*[Hh][Aa][Ss][Hh]|[Hh][Aa][Ss][Hh].*tags --project')

    def test_readme_notes_hash(self):
        text = _read(_README)
        self.assertIn('tags --project', text)
        self.assertIn('HASH', text)

    def test_skill_notes_hash(self):
        text = _read(_SKILL_MD)
        self.assertIn('tags --project', text)
        self.assertIn('HASH', text)


class TestWI10ProjectSubstringMatch(unittest.TestCase):
    """Docs document that --project does an unanchored SUBSTRING path match."""

    def test_each_doc_mentions_substring(self):
        for p in (_README, _RECALL_MD, _SKILL_MD):
            self.assertIn('substring', _read(p).lower(),
                          f"{p.name} must document the substring path match for --project")


class TestWI26Differentiators(unittest.TestCase):
    """README positions recall against native Claude Code features."""

    def setUp(self):
        self.text = _read(_README)

    def test_mentions_native_features(self):
        for native in ('/recap', '/resume', 'memory'):
            self.assertIn(native, self.text,
                          f"README differentiators must reference native '{native}'")

    def test_mentions_differentiators(self):
        low = self.text.lower()
        self.assertIn('fts', low, "README must call out cross-project FTS")
        self.assertIn('tagging', low, "README must call out tagging")
        # highlight / connection sharing
        self.assertIn('highlight', low)
        self.assertTrue('connection' in low or 'connect' in low,
                        "README must call out connection sharing")


if __name__ == '__main__':
    unittest.main(verbosity=2)
