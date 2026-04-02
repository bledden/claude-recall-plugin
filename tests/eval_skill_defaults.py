#!/usr/bin/env python3
"""Eval tests for skill defaults, config behaviour, and SKILL.md content.

Covers:
1. Config defaults — get_session_config returns None for all skill keys when unset
2. Config setting — each key stores and retrieves correctly
3. Bool parsing — truthy/falsy string values parse correctly for bool keys
4. Detection signals string — "explicit,behavioral" round-trips as a string
5. Skill gate logic — the skill_enabled gating pattern works as expected
6. SKILL.md frontmatter — name and description present
7. SKILL.md content coverage — all four behaviour sections present
8. SKILL.md config gate — gating instruction about skill_enabled present
9. SKILL.md command reference — all major /recall commands listed
"""

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import get_connection, get_session_config, set_session_config, insert_session

# Absolute path to SKILL.md — resolve relative to this file's location so the
# tests work regardless of cwd.
_SKILL_MD = Path(__file__).resolve().parent.parent / 'skills' / 'recall-assistant' / 'SKILL.md'

# All skill config keys that the plugin recognises
_SKILL_KEYS = [
    'skill_enabled',
    'detection_signals',
    'auto_run_highlight',
    'auto_highlight',
    'check_mode',
    'delivery_mode',
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_bool(raw: str) -> bool:
    """Replicate the bool-parsing logic used by the skill command handler."""
    return raw.strip().lower() in ('true', 'yes', '1')


# ---------------------------------------------------------------------------
# 1. Config defaults
# ---------------------------------------------------------------------------

class TestConfigDefaults(unittest.TestCase):
    """When a session has no config set, get_session_config returns None for all skill keys."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-defaults', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_skill_keys_default_to_none(self):
        """Every recognised skill key returns None before any config is set."""
        for key in _SKILL_KEYS:
            with self.subTest(key=key):
                val = get_session_config(self.conn, 'sess-defaults', key)
                self.assertIsNone(val, f"Expected None for '{key}', got {val!r}")

    def test_unknown_key_returns_none(self):
        """An arbitrary unknown key also returns None."""
        val = get_session_config(self.conn, 'sess-defaults', 'no_such_config_key')
        self.assertIsNone(val)

    def test_nonexistent_session_returns_none(self):
        """get_session_config on a session that does not exist returns None."""
        val = get_session_config(self.conn, 'ghost-session', 'skill_enabled')
        self.assertIsNone(val)


# ---------------------------------------------------------------------------
# 2. Config setting
# ---------------------------------------------------------------------------

class TestConfigSetting(unittest.TestCase):
    """Setting each skill key stores and retrieves the correct value."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-cfg', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_skill_enabled_true(self):
        set_session_config(self.conn, 'sess-cfg', 'skill_enabled', True)
        self.assertIs(get_session_config(self.conn, 'sess-cfg', 'skill_enabled'), True)

    def test_set_skill_enabled_false(self):
        set_session_config(self.conn, 'sess-cfg', 'skill_enabled', False)
        self.assertIs(get_session_config(self.conn, 'sess-cfg', 'skill_enabled'), False)

    def test_set_auto_run_highlight(self):
        set_session_config(self.conn, 'sess-cfg', 'auto_run_highlight', True)
        self.assertIs(get_session_config(self.conn, 'sess-cfg', 'auto_run_highlight'), True)

    def test_set_auto_highlight(self):
        set_session_config(self.conn, 'sess-cfg', 'auto_highlight', False)
        self.assertIs(get_session_config(self.conn, 'sess-cfg', 'auto_highlight'), False)

    def test_set_check_mode(self):
        set_session_config(self.conn, 'sess-cfg', 'check_mode', 'decay')
        self.assertEqual(get_session_config(self.conn, 'sess-cfg', 'check_mode'), 'decay')

    def test_set_delivery_mode(self):
        set_session_config(self.conn, 'sess-cfg', 'delivery_mode', 'inject')
        self.assertEqual(get_session_config(self.conn, 'sess-cfg', 'delivery_mode'), 'inject')

    def test_multiple_keys_coexist(self):
        """Setting several keys independently does not clobber sibling keys."""
        for key in _SKILL_KEYS:
            set_session_config(self.conn, 'sess-cfg', key, f'value_of_{key}')
        for key in _SKILL_KEYS:
            with self.subTest(key=key):
                val = get_session_config(self.conn, 'sess-cfg', key)
                self.assertEqual(val, f'value_of_{key}')

    def test_overwrite_existing_value(self):
        """set_session_config overwrites the same key without disturbing others."""
        set_session_config(self.conn, 'sess-cfg', 'skill_enabled', True)
        set_session_config(self.conn, 'sess-cfg', 'check_mode', 'explicit')
        set_session_config(self.conn, 'sess-cfg', 'skill_enabled', False)
        self.assertIs(get_session_config(self.conn, 'sess-cfg', 'skill_enabled'), False)
        # Sibling key must be unaffected
        self.assertEqual(get_session_config(self.conn, 'sess-cfg', 'check_mode'), 'explicit')


# ---------------------------------------------------------------------------
# 3. Bool parsing
# ---------------------------------------------------------------------------

class TestBoolParsing(unittest.TestCase):
    """String representations of booleans parse correctly."""

    TRUTHY_STRINGS = ('true', 'True', 'TRUE', 'yes', 'Yes', 'YES', '1')
    FALSY_STRINGS = ('false', 'False', 'FALSE', 'no', 'No', 'NO', '0')

    def test_truthy_strings_parse_as_true(self):
        for raw in self.TRUTHY_STRINGS:
            with self.subTest(raw=raw):
                self.assertTrue(_parse_bool(raw), f"'{raw}' should parse as True")

    def test_falsy_strings_parse_as_false(self):
        for raw in self.FALSY_STRINGS:
            with self.subTest(raw=raw):
                self.assertFalse(_parse_bool(raw), f"'{raw}' should parse as False")

    def test_bool_roundtrip_via_db(self):
        """Parsed booleans round-trip through the database correctly."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, 'test.db')
        try:
            conn = get_connection(db_path)
            insert_session(conn, 'sess-bool', '/p', 'h', '2025-01-01T00:00:00Z')

            for raw in self.TRUTHY_STRINGS:
                parsed = _parse_bool(raw)
                set_session_config(conn, 'sess-bool', 'skill_enabled', parsed)
                result = get_session_config(conn, 'sess-bool', 'skill_enabled')
                self.assertIs(result, True, f"Roundtrip failed for truthy '{raw}'")

            for raw in self.FALSY_STRINGS:
                parsed = _parse_bool(raw)
                set_session_config(conn, 'sess-bool', 'skill_enabled', parsed)
                result = get_session_config(conn, 'sess-bool', 'skill_enabled')
                self.assertIs(result, False, f"Roundtrip failed for falsy '{raw}'")

            conn.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. Detection signals string
# ---------------------------------------------------------------------------

class TestDetectionSignalsString(unittest.TestCase):
    """'detection_signals' is stored and retrieved as a plain string."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-sig', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_explicit_behavioral_roundtrip(self):
        value = 'explicit,behavioral'
        set_session_config(self.conn, 'sess-sig', 'detection_signals', value)
        result = get_session_config(self.conn, 'sess-sig', 'detection_signals')
        self.assertEqual(result, value)
        self.assertIsInstance(result, str)

    def test_all_three_signals_roundtrip(self):
        value = 'explicit,behavioral,temporal'
        set_session_config(self.conn, 'sess-sig', 'detection_signals', value)
        result = get_session_config(self.conn, 'sess-sig', 'detection_signals')
        self.assertEqual(result, value)

    def test_single_signal_roundtrip(self):
        value = 'temporal'
        set_session_config(self.conn, 'sess-sig', 'detection_signals', value)
        result = get_session_config(self.conn, 'sess-sig', 'detection_signals')
        self.assertEqual(result, value)

    def test_signals_are_not_parsed_as_list(self):
        """The stored value is a string, not a Python list."""
        value = 'explicit,behavioral'
        set_session_config(self.conn, 'sess-sig', 'detection_signals', value)
        result = get_session_config(self.conn, 'sess-sig', 'detection_signals')
        self.assertNotIsInstance(result, list)


# ---------------------------------------------------------------------------
# 5. Skill gate logic
# ---------------------------------------------------------------------------

class TestSkillGateLogic(unittest.TestCase):
    """Simulate the skill_enabled gate: `if not get_session_config(...)`."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-gate', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _skill_is_disabled(self, session_id: str) -> bool:
        """Replicate: if not get_session_config(..., 'skill_enabled')."""
        return not get_session_config(self.conn, session_id, 'skill_enabled')

    def test_gate_blocks_when_not_set(self):
        """Gate evaluates to True (skill disabled) when skill_enabled is unset."""
        self.assertTrue(self._skill_is_disabled('sess-gate'))

    def test_gate_blocks_when_set_to_false(self):
        """Gate evaluates to True (skill disabled) when skill_enabled is False."""
        set_session_config(self.conn, 'sess-gate', 'skill_enabled', False)
        self.assertTrue(self._skill_is_disabled('sess-gate'))

    def test_gate_passes_when_set_to_true(self):
        """Gate evaluates to False (skill enabled) when skill_enabled is True."""
        set_session_config(self.conn, 'sess-gate', 'skill_enabled', True)
        self.assertFalse(self._skill_is_disabled('sess-gate'))

    def test_gate_blocks_after_disabling_again(self):
        """Gate re-blocks after skill_enabled is set back to False."""
        set_session_config(self.conn, 'sess-gate', 'skill_enabled', True)
        self.assertFalse(self._skill_is_disabled('sess-gate'))
        set_session_config(self.conn, 'sess-gate', 'skill_enabled', False)
        self.assertTrue(self._skill_is_disabled('sess-gate'))

    def test_gate_blocks_on_nonexistent_session(self):
        """Gate evaluates to True for a session that doesn't exist (None is falsy)."""
        self.assertTrue(not get_session_config(self.conn, 'ghost-sess', 'skill_enabled'))


# ---------------------------------------------------------------------------
# Helpers for SKILL.md tests
# ---------------------------------------------------------------------------

def _read_skill_md() -> str:
    """Return the full text of SKILL.md, or raise FileNotFoundError."""
    return _SKILL_MD.read_text(encoding='utf-8')


def _parse_frontmatter(text: str) -> dict:
    """Extract key: value pairs from a YAML-style frontmatter block (--- ... ---)."""
    match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).splitlines():
        if ':' in line:
            key, _, val = line.partition(':')
            fm[key.strip()] = val.strip()
    return fm


# ---------------------------------------------------------------------------
# 6. SKILL.md exists and has correct frontmatter
# ---------------------------------------------------------------------------

class TestSkillMdFrontmatter(unittest.TestCase):
    """SKILL.md exists and has the required frontmatter fields."""

    def test_skill_md_exists(self):
        self.assertTrue(_SKILL_MD.exists(), f"SKILL.md not found at {_SKILL_MD}")

    def test_frontmatter_name_is_recall_assistant(self):
        text = _read_skill_md()
        fm = _parse_frontmatter(text)
        self.assertIn('name', fm, "Frontmatter is missing 'name' key")
        self.assertEqual(fm['name'], 'recall-assistant',
                         f"Expected name='recall-assistant', got {fm['name']!r}")

    def test_frontmatter_has_description(self):
        text = _read_skill_md()
        fm = _parse_frontmatter(text)
        self.assertIn('description', fm, "Frontmatter is missing 'description' key")
        self.assertTrue(fm['description'], "Frontmatter 'description' must not be empty")

    def test_frontmatter_description_mentions_recall(self):
        text = _read_skill_md()
        fm = _parse_frontmatter(text)
        self.assertIn('recall', fm.get('description', '').lower(),
                      "Frontmatter description should mention 'recall'")


# ---------------------------------------------------------------------------
# 7. SKILL.md content coverage — all four behaviour sections
# ---------------------------------------------------------------------------

class TestSkillMdContentCoverage(unittest.TestCase):
    """SKILL.md contains sections for each of the four documented behaviours."""

    def setUp(self):
        self.text = _read_skill_md()

    def test_contains_context_loss_detection_section(self):
        self.assertIn('Context-Loss Detection', self.text,
                      "SKILL.md must contain a 'Context-Loss Detection' section")

    def test_contains_proactive_highlighting_section(self):
        self.assertIn('Proactive Highlighting', self.text,
                      "SKILL.md must contain a 'Proactive Highlighting' section")

    def test_contains_connection_suggestions_section(self):
        self.assertIn('Connection Suggestions', self.text,
                      "SKILL.md must contain a 'Connection Suggestions' section")

    def test_contains_inbox_awareness_section(self):
        self.assertIn('Inbox Awareness', self.text,
                      "SKILL.md must contain an 'Inbox Awareness' section")


# ---------------------------------------------------------------------------
# 8. SKILL.md config gate — skill_enabled gating instruction present
# ---------------------------------------------------------------------------

class TestSkillMdConfigGate(unittest.TestCase):
    """SKILL.md instructs Claude to check skill_enabled before taking action."""

    def setUp(self):
        self.text = _read_skill_md()

    def test_skill_enabled_key_mentioned(self):
        self.assertIn('skill_enabled', self.text,
                      "SKILL.md must reference the 'skill_enabled' config key")

    def test_gating_instruction_present(self):
        """File must contain the gate language — checking enabled before acting."""
        # Accept either the exact phrase or a broader equivalent
        gate_patterns = [
            'gated',
            'check whether the user has enabled',
            'If not enabled, do nothing',
            'before taking any proactive action',
        ]
        found = any(p.lower() in self.text.lower() for p in gate_patterns)
        self.assertTrue(found,
                        "SKILL.md must contain gating language instructing Claude to "
                        "check skill_enabled before taking proactive action")

    def test_skill_enabled_true_shown_as_enablement_command(self):
        """/recall config skill_enabled true must appear as the enable command."""
        self.assertIn('/recall config skill_enabled true', self.text,
                      "SKILL.md must show '/recall config skill_enabled true' as the enable command")


# ---------------------------------------------------------------------------
# 9. SKILL.md command reference — all major /recall commands listed
# ---------------------------------------------------------------------------

class TestSkillMdCommandReference(unittest.TestCase):
    """SKILL.md lists all major /recall commands."""

    def setUp(self):
        self.text = _read_skill_md()

    def _assert_command(self, fragment: str):
        self.assertIn(fragment, self.text,
                      f"SKILL.md must list the command containing '{fragment}'")

    # Core recall
    def test_lists_recall_last5(self):
        self._assert_command('/recall last5')

    def test_lists_recall_last10(self):
        self._assert_command('/recall last10')

    def test_lists_recall_search(self):
        self._assert_command('/recall search')

    def test_lists_recall_around(self):
        self._assert_command('/recall around')

    # Session management
    def test_lists_recall_sessions(self):
        self._assert_command('/recall sessions')

    def test_lists_recall_stats(self):
        self._assert_command('/recall stats')

    # Tagging
    def test_lists_recall_tag(self):
        self._assert_command('/recall tag')

    def test_lists_recall_tags(self):
        self._assert_command('/recall tags')

    # Cross-session sharing
    def test_lists_recall_highlight(self):
        self._assert_command('/recall highlight')

    def test_lists_recall_connect(self):
        self._assert_command('/recall connect')

    def test_lists_recall_disconnect(self):
        self._assert_command('/recall disconnect')

    def test_lists_recall_inbox(self):
        self._assert_command('/recall inbox')

    # Configuration
    def test_lists_recall_config_detection_signals(self):
        self._assert_command('/recall config detection_signals')

    def test_lists_recall_config_auto_run_highlight(self):
        self._assert_command('/recall config auto_run_highlight')

    def test_lists_recall_config_check_mode(self):
        self._assert_command('/recall config check_mode')

    def test_lists_recall_config_delivery_mode(self):
        self._assert_command('/recall config delivery_mode')


if __name__ == '__main__':
    unittest.main(verbosity=2)
