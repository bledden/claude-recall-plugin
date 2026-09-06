#!/usr/bin/env python3
"""v2.4: the commands and files Claude touched are captured and searchable."""
import json, os, shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
from utils import extract_tool_calls
from prompt_submit import parse_transcript_from_offset, build_new_exchanges, run_hook
from db import get_connection, get_exchanges, search_exchanges_fts, export_session_json


def _tool(name, **inp):
    return {'type': 'tool_use', 'id': 'x', 'name': name, 'input': inp}


class TestExtractToolCalls(unittest.TestCase):
    def test_compact_lines_per_tool(self):
        msg = {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'Let me look.'},
            _tool('Bash', command='ssh root@ssh.runpod.io -i ~/.ssh/id_ed25519', description='ssh'),
            _tool('Edit', file_path='/p/kernel.py', old_string='a', new_string='b'),
            _tool('Grep', pattern='BLOCK_S', path='/p'),
            _tool('WebFetch', url='https://example.com/x', prompt='q'),
            _tool('Skill', skill='recall:recall', args='last5'),
            _tool('Agent', description='explore repo', prompt='...'),
            _tool('SomethingNew', foo='bar'),
        ]}
        lines = extract_tool_calls(msg)
        self.assertEqual(lines[0], '$ ssh root@ssh.runpod.io -i ~/.ssh/id_ed25519')
        self.assertEqual(lines[1], 'Edit /p/kernel.py')
        self.assertEqual(lines[2], 'Grep BLOCK_S in /p')
        self.assertEqual(lines[3], 'WebFetch https://example.com/x')
        self.assertEqual(lines[4], 'Skill recall:recall last5')
        self.assertEqual(lines[5], 'Agent: explore repo')
        self.assertEqual(lines[6], 'SomethingNew')

    def test_long_command_is_capped_per_line(self):
        lines = extract_tool_calls({'content': [_tool('Bash', command='x' * 1000)]})
        self.assertLess(len(lines[0]), 320)

    def test_no_tools(self):
        self.assertEqual(extract_tool_calls({'content': 'plain'}), [])
        self.assertEqual(extract_tool_calls({'content': [{'type': 'text', 'text': 'hi'}]}), [])


class TestToolTextEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / 't.db'
        self.tp = str(Path(self.tmp) / 't.jsonl')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, *entries):
        with open(self.tp, 'a') as f:
            for e in entries:
                f.write(json.dumps(e) + '\n')

    def test_tool_only_assistant_message_is_kept_and_merged(self):
        self._write(
            {'type': 'user', 'timestamp': 't1', 'message': {'role': 'user', 'content': 'spin up the pod'}},
            {'type': 'assistant', 'timestamp': 't2', 'message': {'role': 'assistant', 'content': [
                _tool('Bash', command='runpodctl create pod --gpu A100')]}},   # no text at all
            {'type': 'assistant', 'timestamp': 't3', 'message': {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'Pod is up.'}]}},
        )
        msgs, _ = parse_transcript_from_offset(self.tp, 0)
        self.assertEqual([m['role'] for m in msgs], ['user', 'assistant', 'assistant'])
        ex = build_new_exchanges(msgs)
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]['assistant_text'], 'Pod is up.')
        self.assertEqual(ex[0]['tool_text'], '$ runpodctl create pod --gpu A100')

    def test_commands_are_searchable_and_exported(self):
        self._write(
            {'type': 'user', 'timestamp': 't1', 'message': {'role': 'user', 'content': 'connect to the box'}},
            {'type': 'assistant', 'timestamp': 't2', 'message': {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'Connecting.'},
                _tool('Bash', command='ssh root@ssh.runpod.io -p 22222')]}},
            {'type': 'user', 'timestamp': 't3', 'message': {'role': 'user', 'content': 'next'}},
        )
        run_hook({'session_id': 's', 'transcript_path': self.tp, 'prompt': 'x', 'cwd': '/p'}, db_path=self.db_path)
        conn = get_connection(self.db_path)
        try:
            rows = get_exchanges(conn, 's')
            self.assertEqual(rows[0]['tool_text'], '$ ssh root@ssh.runpod.io -p 22222')
            hits = search_exchanges_fts(conn, 'runpod', session_id='s')
            self.assertEqual(len(hits), 1)
            self.assertIn('runpod', hits[0]['snippet'].lower())
            self.assertIn('tool_text', export_session_json(conn, 's')['exchanges'][0])
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
