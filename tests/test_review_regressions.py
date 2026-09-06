"""Regression cases from the independent 2026-09-05 review of v2.3.1 (external reviewer).

Each test encodes an expected behaviour that was broken at 9c8a0ba: the retrieval
display cap, capture wedging on turns larger than the read budget, premature
Stop finalisation, partially written JSONL records, one-pass SessionEnd,
suggestion output discarding due highlights, the privacy claim, and the global
search tiebreaker. Fixed in v2.4.0. Synthetic transcripts and temporary stores only.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / 'hooks'), str(REPO / 'scripts')]
import prompt_submit as capture
import stop
import session_end
import db
from manage_connections import connect, inbox
from fetch_exchanges import format_exchanges


def entry(role, text):
    return {'type': role, 'timestamp': '2026-09-05T20:00:00Z',
            'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}}


def line(role, text):
    return json.dumps(entry(role, text)) + '\n'


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, '_migration_checked', True)
    monkeypatch.setenv('RECALL_DB', str(tmp_path / 'db.sqlite'))
    monkeypatch.setenv('RECALL_LOG_FILE', str(tmp_path / 'events.log'))
    transcript = tmp_path / 'transcript.jsonl'
    payload = {'session_id': 'review', 'transcript_path': str(transcript),
               'cwd': str(tmp_path), 'prompt': 'next prompt'}
    return tmp_path / 'db.sqlite', transcript, payload


def rows(path):
    conn = db.get_connection(path)
    try:
        return db.get_exchanges(conn, 'review')
    finally:
        conn.close()


def test_turn_larger_than_read_budget_eventually_makes_progress(case):
    path, transcript, payload = case
    # Every line is below 2 MB; the full turn exceeds 2 MB due to tool output.
    tool_line = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'synthetic', 'content': 'x' * 800000}]}}) + '\n'
    transcript.write_text(line('user', 'Long task') + line('assistant', 'Starting work')
                          + tool_line * 3 + line('assistant', 'Finished work')
                          + line('user', 'Next task') + line('assistant', 'Next answer'))
    offsets = []
    for _ in range(5):
        capture.run_hook(payload, db_path=path)
        conn = db.get_connection(path)
        offsets.append(db.get_session(conn, 'review')['byte_offset'])
        conn.close()
    assert len(rows(path)) == 2, f'capture is stuck; offsets={offsets}'


def test_single_oversized_tool_line_does_not_block_all_future_turns(case):
    path, transcript, payload = case
    huge_line = json.dumps({'type': 'progress', 'data': 'x' * (capture.MAX_BYTES_PER_READ + 1)}) + '\n'
    transcript.write_text(huge_line + line('user', 'Q') + line('assistant', 'A'))
    for _ in range(4):
        capture.run_hook(payload, db_path=path)
    assert len(rows(path)) == 1


def test_stop_does_not_treat_repeated_ending_as_proof_of_flush(case):
    path, transcript, payload = case
    transcript.write_text(line('user', 'Run checks') + line('assistant', 'Next I will verify it.'))
    # Use a final answer whose whole short text appears earlier verbatim.
    final_answer = 'I will verify it.'
    stop.run_hook({**payload, 'last_assistant_message': final_answer}, db_path=path)
    with transcript.open('a') as f:
        f.write(line('assistant', 'The important result is 42.'))
        f.write(line('assistant', final_answer))
    stop.run_hook({**payload, 'last_assistant_message': final_answer}, db_path=path)
    assert 'The important result is 42.' in rows(path)[0]['assistant_text']


def test_partial_json_prompt_is_retried_after_append(case):
    path, transcript, payload = case
    user_line = line('user', 'Question after an append boundary')
    transcript.write_text(user_line[:30])
    capture.run_hook(payload, db_path=path)
    with transcript.open('a') as f:
        f.write(user_line[30:] + line('assistant', 'Answer after flush'))
    stop.run_hook({**payload, 'last_assistant_message': 'Answer after flush'}, db_path=path)
    assert len(rows(path)) == 1


def test_context_loss_suggestion_does_not_discard_due_highlights(case):
    path, transcript, payload = case
    capture.run_hook(payload, db_path=path)
    conn = db.get_connection(path)
    db.insert_session(conn, 'peer', '/tmp/peer', 'peer', '2026-09-05T00:00:00Z')
    connect(conn, 'review', 'peer', 'topic', check_mode='decay', delivery_mode='inject')
    db.insert_highlight(conn, 'peer', 'Distinctive peer finding', 'topic', source='explicit')
    db.set_session_config(conn, 'review', 'skill_enabled', True)
    conn.execute("UPDATE connections SET check_counter=6, check_interval=7")
    conn.commit()
    conn.close()
    response = capture.run_hook({**payload, 'prompt': 'remind me what we discussed'}, db_path=path)
    context = response.get('hookSpecificOutput', {}).get('additionalContext', '')
    conn = db.get_connection(path)
    remaining = inbox(conn, 'review')
    conn.close()
    assert 'Distinctive peer finding' in context or 'Distinctive peer finding' in remaining


def test_session_end_drains_backlog_instead_of_one_capped_pass(case):
    path, transcript, payload = case
    capture.run_hook(payload, db_path=path)  # register session before transcript appears
    transcript.write_text(''.join(line('user', f'Q{i}') + line('assistant', f'A{i}') for i in range(600)))
    session_end.run_hook(payload, db_path=path)
    assert len(rows(path)) == 600


def test_privacy_claim_credentials_are_not_stored(case):
    path, transcript, payload = case
    # This is deliberately fake, not a real credential.
    marker = 'fake-password-for-review-only'
    transcript.write_text(line('user', f'My password is {marker}') + line('assistant', 'Received'))
    capture.run_hook(payload, db_path=path)
    assert marker not in rows(path)[0]['user_text']


def test_global_search_breaks_timestamp_ties_by_newest_index(case):
    path, transcript, payload = case
    transcript.write_text(''.join(line('user', f'kernel Q{i}') + line('assistant', f'A{i}') for i in range(12)))
    capture.run_hook(payload, db_path=path)
    conn = db.get_connection(path)
    actual = [row['idx'] for row in db.search_exchanges_global(conn, 'kernel', limit=3)]
    conn.close()
    assert actual == [12, 11, 10]


def test_recall_displays_answer_that_was_stored_beyond_old_cap(case):
    path, transcript, payload = case
    marker = 'ACTUAL_RESULT_42'
    transcript.write_text(line('user', 'What is the result?')
                          + line('assistant', 'Intermediate notes. ' * 80)
                          + line('assistant', marker))
    capture.run_hook(payload, db_path=path)
    stored = rows(path)
    assert marker in stored[0]['assistant_text']
    assert marker in format_exchanges(stored)


def test_capture_keeps_continuation_after_another_stop_hook_blocks(case):
    path, transcript, payload = case
    transcript.write_text(line('user', 'Fix the bug') + line('assistant', 'First attempt complete.'))
    stop.run_hook({**payload, 'last_assistant_message': 'First attempt complete.',
                   'stop_hook_active': False}, db_path=path)
    # Another Stop hook blocks, so Claude continues without another user prompt.
    with transcript.open('a') as f:
        f.write(line('assistant', 'Validation caught an error; here is the corrected answer.'))
    stop.run_hook({**payload,
                   'last_assistant_message': 'Validation caught an error; here is the corrected answer.',
                   'stop_hook_active': True}, db_path=path)
    stored = rows(path)
    assert len(stored) == 1
    assert 'corrected answer' in stored[0]['assistant_text']
