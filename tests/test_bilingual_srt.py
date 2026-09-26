"""Bilingual export preserves cue alignment and existing Chinese outputs."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.maijev.segment_llm import MergedLine
from flows.maijev.translate_llm import render_srt


def test_bilingual_keeps_same_timestamps_and_language_order():
    lines = [MergedLine(1.25, 3.5, '啓治さんです。', [0]),
             MergedLine(5.2, 7.8, 'ありがとうございます。', [1])]
    zh = ['是啓治桑', '谢谢']
    mono = render_srt(lines, zh).strip().split('\n\n')
    bi = render_srt(lines, zh, bilingual=True).strip().split('\n\n')
    assert len(bi) == len(mono) == 2
    for i, (one, both) in enumerate(zip(mono, bi)):
        assert both.splitlines()[:2] == one.splitlines()[:2]
        assert both.splitlines()[2:] == [lines[i].text, zh[i]]
    assert '{\\' not in '\n'.join(bi)


def test_bilingual_newlines_do_not_split_cues_and_fallback_is_preserved():
    lines = [MergedLine(0, 2, 'はい\nどうぞ', [0]),
             MergedLine(3, 4, 'また明日', [1])]
    text = render_srt(lines, ['好\n\n请进', ''], bilingual=True)
    blocks = text.strip().split('\n\n')
    assert len(blocks) == 2
    assert blocks[0].splitlines()[2:] == ['はい どうぞ', '好  请进']
    assert blocks[1].splitlines()[2:] == ['また明日', 'また明日']


def test_misaligned_translations_fail_instead_of_truncating():
    with pytest.raises(ValueError, match='equal lengths'):
        render_srt([MergedLine(0, 1, 'はい', [0])], [], bilingual=True)
