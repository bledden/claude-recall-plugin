#!/usr/bin/env python3
"""Concurrency-safe session/project resolution (hardening for the
"recall returns another session" bug on multi-session Linux boxes).

Session id comes from the NATIVE per-session env var CLAUDE_CODE_SESSION_ID
(Claude Code injects it into every command subprocess), so concurrent sessions
never collide — unlike the old reliance on an appended $CLAUDE_ENV_FILE.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from utils import resolve_session_id, resolve_project_hash, compute_project_hash


class TestResolveSessionId(unittest.TestCase):
    def test_explicit_arg_wins(self):
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'native'}, clear=True):
            self.assertEqual(resolve_session_id('explicit'), 'explicit')

    def test_native_session_id_preferred_over_legacy_recall_var(self):
        """The B-fix: the native per-session id beats a possibly-stale/shared
        RECALL_SESSION_ID, so a concurrent session's leaked value can't win."""
        with mock.patch.dict(os.environ,
                             {'CLAUDE_CODE_SESSION_ID': 'mine',
                              'RECALL_SESSION_ID': 'someone-elses'}, clear=True):
            self.assertEqual(resolve_session_id(''), 'mine')

    def test_falls_back_to_recall_var_when_no_native(self):
        with mock.patch.dict(os.environ, {'RECALL_SESSION_ID': 'legacy'}, clear=True):
            self.assertEqual(resolve_session_id(''), 'legacy')

    def test_empty_when_nothing_available(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_session_id(''), '')


class TestResolveProjectHash(unittest.TestCase):
    def test_explicit_arg_wins(self):
        with mock.patch.dict(os.environ, {'RECALL_PROJECT_HASH': 'envhash'}, clear=True):
            self.assertEqual(resolve_project_hash('explicit'), 'explicit')

    def test_env_var_over_cwd(self):
        with mock.patch.dict(os.environ, {'RECALL_PROJECT_HASH': 'envhash'}, clear=True):
            self.assertEqual(resolve_project_hash(''), 'envhash')

    def test_derives_from_cwd_when_nothing_else(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_project_hash(''), compute_project_hash(os.getcwd()))


if __name__ == '__main__':
    unittest.main()
