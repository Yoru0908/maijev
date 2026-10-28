"""Audio correction of the Japanese lines — no network, no ffmpeg."""

import json

import pytest

from flows.maijev import correct, glossary
from flows.maijev.segment_llm import MergedLine


def _lines(*spec):
    return [MergedLine(a, b, text, []) for a, b, text in spec]


def test_segments_cut_on_line_boundaries():
    lines = _lines((0, 4, 'a'), (5, 9, 'b'), (10, 14, 'c'), (30, 31, 'd'))
    assert correct.segments(lines, 10) == [range(0, 2), range(2, 3), range(3, 4)]
    assert correct.segments(lines, 100) == [range(0, 4)]
    assert correct.segments([], 10) == []


def test_context_is_japanese_only_and_drops_review_reasons():
    table = glossary.merge([
        ('【本片主题】深夜电台\n[人名] 佐藤美咲 / みーちゃん -> 美咲', False),
        ('ミーグリ -> 咪咕力\n只在节目里这样叫', True),
    ])
    ctx = correct.context(table, ['今井さん'])
    assert '[人名] 佐藤美咲 / みーちゃん' in ctx and 'ミーグリ' in ctx
    assert '深夜电台' in ctx and '今井さん' in ctx
    assert '美咲\n' not in ctx and '咪咕力' not in ctx and '只在节目' not in ctx


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Fake clip + model; returns the list of model calls."""
    calls, replies = [], []
    monkeypatch.setattr(correct, '_clip', lambda wav, a, b, out: f'{a:.1f}-{b:.1f}'.encode())
    monkeypatch.setattr(correct, 'resolve_model', lambda _: 'gemini-3.8-flash')
    monkeypatch.setenv('CORRECT_SEGMENT_SECONDS', '10')

    def fake_generate(body, **kw):
        calls.append({'body': body, **kw})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(correct, 'generate_json', fake_generate)
    return calls, replies, tmp_path


def test_edits_apply_by_id_and_are_cached(fake):
    calls, replies, tmp = fake
    lines = _lines((1, 3, '今井さんは大富豪'), (4, 6, 'そうそう'))
    replies.append({'edits': [
        {'id': 0, 'ja': '今の私は大富豪', 'reason': '音频'},
        {'id': 1, 'ja': 'そうそう'},                          # unchanged
        {'id': 7, 'ja': '越界'}, {'id': 'x', 'ja': '坏 id'}, {'id': 1, 'ja': ''},
        {'id': 1, 'ja': 'そうそう、それでね、昨日はずっと家にいて本を読んでたんだ'},  # a new sentence
    ]})
    out, edits, failed = correct.correct_lines(lines, tmp / 'a.wav', glossary.Table(), [], tmp / 'c')
    assert [l.text for l in out] == ['今の私は大富豪', 'そうそう']
    assert [(e['id'], e['applied']) for e in edits] == [(0, True), (1, False)]
    assert edits[0]['time'] == '00:01.0-00:03.0' and failed == []
    part = calls[0]['parts'][0]['inlineData']
    assert part['mimeType'] == 'audio/ogg' and calls[0]['model'] == 'gemini-3.8-flash'
    assert '0\t00:00.5-00:02.5\t今井さんは大富豪' in calls[0]['body']  # relative to the 0.5 s clip start

    again = correct.correct_lines(lines, tmp / 'a.wav', glossary.Table(), [], tmp / 'c')
    assert again[0] == out and len(calls) == 1


def test_failed_segment_keeps_its_text_and_is_reported(fake):
    calls, replies, tmp = fake
    lines = _lines((0, 4, 'あ'), (20, 24, 'い'))
    replies += [RuntimeError('429'), {'edits': [{'id': 1, 'ja': 'う'}]}]
    correct.MAX_WORKERS, workers = 1, correct.MAX_WORKERS  # keep reply order
    try:
        out, edits, failed = correct.correct_lines(lines, tmp / 'a.wav', glossary.Table(), [], tmp / 'c')
    finally:
        correct.MAX_WORKERS = workers
    assert [l.text for l in out] == ['あ', 'う']
    assert failed == ['00:00.0-00:04.0']
    assert not [p for p in (tmp / 'c').glob('correct_*.json') if 'あ' in p.read_text()]
    assert len(json.loads(next((tmp / 'c').glob('correct_*.json')).read_text())['edits']) == 1
