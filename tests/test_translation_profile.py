import pytest

from flows.maijev import translate_llm as tr
from flows.maijev.segment_llm import MergedLine


def test_ui_keeps_responses_and_original_speaker_separator(monkeypatch):
    monkeypatch.setenv('TRANSLATE_PROFILE', 'yamakawa-ui')
    assert tr._clean_zh('嗯，没关系啊。 -“诶？”') == '嗯 没关系啊 -「诶？」'
    monkeypatch.setenv('TRANSLATE_PROFILE', 'generic')
    assert tr._clean_zh('嗯，没关系啊。') == '没关系'


def test_all_glossary_layers_survive_gui_correction(monkeypatch, tmp_path):
    base, auto, user = [tmp_path / p for p in ['base.md', 'auto.md', 'user.md']]
    base.write_text('昔の曲 -> 昔の曲\n愛MUST BE -> 愛MUST BE')
    auto.write_text('画面名 -> 画面名')
    user.write_text('ひな -> ひな')
    monkeypatch.setenv('TRANSLATE_BASE_GLOSSARY_PATH', str(base))
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(user))
    monkeypatch.setenv('TRANSLATE_PROFILE', 'yamakawa-ui')
    system = tr._load_system(auto)
    for text in ['昔の曲', '愛MUST BE', '画面名', 'ひな', '先准确，再像她']:
        assert text in system
    assert system.index('昔の曲') < system.index('画面名') < system.index('ひな ->')
    assert '语气词省略（最高优先级）' not in system


def test_invalid_profile_fails_before_model_call(monkeypatch):
    monkeypatch.setenv('TRANSLATE_PROFILE', 'typo')
    with pytest.raises(ValueError, match='unknown translation profile'):
        tr._load_system()


def test_profile_and_updated_base_invalidate_only_translation_cache(monkeypatch, tmp_path):
    monkeypatch.delenv('TRANSLATE_GLOSSARY_PATH', raising=False)
    glossary = tmp_path / 'base.md'
    glossary.write_text('愛MUST BE')
    monkeypatch.setenv('TRANSLATE_BASE_GLOSSARY_PATH', str(glossary))
    calls = []
    def fake_generate(body, **kwargs):
        calls.append(kwargs['system'])
        return {'lines': [{'id': 0, 'zh': '嗯 好开心啊'}]}
    monkeypatch.setattr(tr, 'generate_json', fake_generate)
    monkeypatch.setattr(tr, 'resolve_model', lambda _: 'same-model')
    lines = [MergedLine(start=0, end=1, text='うん、うれしい', utterance_ids=[0])]
    cache = tmp_path / 'cache'
    monkeypatch.setenv('TRANSLATE_PROFILE', 'generic')
    assert tr.translate_lines(lines, cache) == ['好开心']
    tr.translate_lines(lines, cache)
    assert len(calls) == 1
    monkeypatch.setenv('TRANSLATE_PROFILE', 'yamakawa-ui')
    assert tr.translate_lines(lines, cache) == ['嗯 好开心啊']
    glossary.write_text('愛MUST BE\n元凶')
    assert tr.translate_lines(lines, cache) == ['嗯 好开心啊']
    assert len(calls) == 3
    assert len(list(cache.glob('*.json'))) == 3
