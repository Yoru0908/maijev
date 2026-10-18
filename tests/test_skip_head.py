"""Omitted interjections: the model decides, the program moves the timeline."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.maijev import translate_llm as tr  # noqa: E402
from flows.maijev.segment_llm import MergedLine, trim_heads  # noqa: E402
from flows.maijev.translate_llm import render_srt  # noqa: E402

TEXT = 'なんか、あのー、私いつもさ'
# Real MAI timings from a phone recording: punctuation is its own word.
WORDS = [{'text': c, 'start': t, 'end': t + 0.08} for c, t in zip(
    TEXT, [6.16, 6.26, 6.36, 6.68, 7.56, 7.76, 7.88, 8.16, 8.84, 9.20, 9.32, 9.44, 9.60])]
LINE = MergedLine(6.16, 16.96, TEXT, [0])


def test_resolve_keeps_only_verified_heads():
    assert tr._resolve(LINE, {'zh': '我平时', 'skip_head': 'なんか、あのー、'}) == ('我平时', 'なんか、あのー、')
    assert tr._resolve(LINE, {'zh': '我平时', 'skip_head': 'なんか あのー'}) == ('我平时', '')
    assert tr._resolve(LINE, '我平时') == ('我平时', '')


def test_only_interjections_up_to_punctuation_are_trusted():
    cases = {
        'なんか、あのー、': 'なんか、あのー、',
        'そのさ、なんかその、なんだろう、こう': 'そのさ、なんかその、なんだろう、',
        'そのなんかこれまでの質問、あの、': '',
        'でもさ、本当にその、なんかね、': '',
        '気がする。うん。から、': '',
        'なんかこういう': '',
        'はいつも': '',
        'なんか、ん？あ、': 'なんか、ん？あ、',
    }
    for skip, head in cases.items():
        assert tr._filler_head(skip) == head, skip


def test_whole_line_omission_is_filler_lines_only():
    hai = MergedLine(0, 1, 'はい、はい。', [0])
    assert tr._resolve(hai, {'zh': '', 'skip_head': 'はい、はい。'}) == (None, 'はい、はい。')
    assert tr._resolve(hai, {'zh': ''}) == ('はい、はい。', '')
    bare = MergedLine(0, 1, 'はい', [0])
    assert tr._resolve(bare, {'zh': '', 'skip_head': 'はい'}) == (None, 'はい')
    assert tr._resolve(LINE, {'zh': '', 'skip_head': TEXT}) == (TEXT, '')


def test_trim_moves_start_to_first_kept_word():
    [moved] = trim_heads([LINE], ['なんか、あのー、'], WORDS)
    assert round(moved.start, 2) == 8.74 and moved.end == LINE.end and moved.text == TEXT


def test_trim_keeps_timing_on_mismatch_or_flash():
    assert trim_heads([LINE], ['なんか あのー'], WORDS) == [LINE]  # not the line's prefix
    other = [dict(w, text='x') for w in WORDS]
    assert trim_heads([LINE], ['なんか、あのー、'], other) == [LINE]  # words disagree with text
    assert trim_heads([LINE], [''], WORDS) == [LINE]
    short = MergedLine(6.16, 9.0, TEXT, [0])
    assert trim_heads([short], ['なんか、あのー、'], WORDS) == [short]


def test_omitted_line_has_no_zh_cue_and_bilingual_keeps_japanese():
    lines = [MergedLine(0, 1, 'はい。', [0]), MergedLine(2, 3, 'また明日', [1])]
    zh = [None, '明天见']
    assert render_srt(lines, zh).strip().split('\n\n') == ['1\n00:00:02,000 --> 00:00:03,000\n明天见']
    bi = render_srt(lines, zh, bilingual=True).strip().split('\n\n')
    assert bi[0].splitlines()[2:] == ['はい。'] and bi[1].splitlines()[2:] == ['また明日', '明天见']


def test_heads_survive_the_translation_cache(monkeypatch, tmp_path):
    calls = []

    def fake(body, **_):
        calls.append(body)
        return {'lines': [{'id': 0, 'zh': '我平时', 'skip_head': 'なんか、あのー、'}]}

    monkeypatch.setattr(tr, 'generate_json', fake)
    monkeypatch.setattr(tr, 'resolve_model', lambda _: 'gemini-3.8-flash')
    monkeypatch.delenv('TRANSLATE_BASE_GLOSSARY_PATH', raising=False)
    monkeypatch.delenv('TRANSLATE_GLOSSARY_PATH', raising=False)
    for _ in range(2):
        assert tr.translate_lines_with_heads([LINE], tmp_path) == (['我平时'], ['なんか、あのー、'])
    assert len(calls) == 1
    [cache] = tmp_path.glob('*.json')
    assert json.loads(cache.read_text())['0']['skip_head'] == 'なんか、あのー、'
