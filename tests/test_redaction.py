#!/usr/bin/env python3
"""v2.4: credential-looking strings are redacted at index time (PRIVACY.md promise)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
from utils import redact_secrets
from prompt_submit import build_new_exchanges


class TestRedactSecrets(unittest.TestCase):
    def test_known_token_formats(self):
        cases = {
            'aws AKIAIOSFODNN7EXAMPLE': 'aws-access-key',
            'key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789': 'anthropic-key',
            'key sk-proj-abcdefghijklmnopqrstuvwxyz0123456789': 'openai-key',
            'gh ghp_abcdefghijklmnopqrstuvwxyz0123456789': 'github-token',
            'hf hf_abcdefghijklmnopqrstuvwxyz012345': 'huggingface-token',
            'slack xoxb-123456789012-abcdefghij': 'slack-token',
            'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789': 'bearer-token',
            'jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U': 'jwt',
        }
        for text, kind in cases.items():
            out = redact_secrets(text)
            self.assertIn(f'[REDACTED:{kind}]', out, text)
            self.assertNotIn(text.split()[-1], out, text)   # the secret itself is gone

    def test_private_key_block(self):
        pem = '-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\nlots\n-----END RSA PRIVATE KEY-----'
        self.assertEqual(redact_secrets(f'here {pem} end'), 'here [REDACTED:private-key] end')

    def test_generic_assignment(self):
        out = redact_secrets('set API_KEY=abc123def456ghi789 and password: hunter2hunter2')
        self.assertIn('API_KEY=[REDACTED:credential]', out)
        self.assertIn('password: [REDACTED:credential]', out)

    def test_ordinary_text_untouched(self):
        for t in ('the token count was 4000', 'password reset flow', 'sk is short',
                  'use the secret sauce', 'tokens per second: 120', 'Bearer of bad news',
                  'the api key is invalid', 'the token is expired'):
            self.assertEqual(redact_secrets(t), t)

    def test_natural_language_secret(self):
        out = redact_secrets('My password is fake-password-for-review-only, ok?')
        self.assertNotIn('fake-password', out)
        self.assertIn('[REDACTED:credential]', out)

    def test_applied_when_building_exchanges(self):
        msgs = [{'role': 'user', 'text': 'my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123', 'timestamp': 't', 'tools': []},
                {'role': 'assistant', 'text': 'exporting AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY', 'timestamp': 't',
                 'tools': ['$ export TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789']}]
        ex = build_new_exchanges(msgs)[0]
        self.assertIn('[REDACTED:anthropic-key]', ex['user_text'])
        self.assertIn('[REDACTED:', ex['assistant_text']); self.assertNotIn('wJalrXUtnFEMIK7MDENG', ex['assistant_text'])
        self.assertIn('[REDACTED:', ex['tool_text']); self.assertNotIn('ghp_abc', ex['tool_text'])
        self.assertNotIn('sk-ant', ex['preview'])


if __name__ == '__main__':
    unittest.main()
