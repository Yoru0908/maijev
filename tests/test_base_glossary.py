from flows.maijev import translate_llm as tr
from flows.maijev.segment_llm import MergedLine


def test_base_survives_manual_glossary_override(monkeypatch, tmp_path):
    base, auto, manual = [tmp_path / p for p in ['base.md', 'auto.md', 'manual.md']]
    base.write_text('過去曲\n愛MUST BE')
    auto.write_text('画面名')
    manual.write_text('人名修正')
    monkeypatch.setenv('TRANSLATE_BASE_GLOSSARY_PATH', str(base))
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(manual))
    system = tr._load_system(auto)
    assert system.startswith(tr.PROMPT_PATH.read_text().strip())
    assert system.index('過去曲') < system.index('画面名') < system.index('人名修正')
    assert '愛MUST BE' in system
    assert tr._clean_zh('嗯，没关系啊。') == '没关系'


def test_base_update_invalidates_translation_cache(monkeypatch, tmp_path):
    monkeypatch.delenv('TRANSLATE_GLOSSARY_PATH', raising=False)
    base = tmp_path / 'base.md'
    base.write_text('愛MUST BE')
    monkeypatch.setenv('TRANSLATE_BASE_GLOSSARY_PATH', str(base))
    calls = []
    def fake_generate(body, **kwargs):
        calls.append(kwargs['system'])
        return {'lines': [{'id': 0, 'zh': '好开心'}]}
    monkeypatch.setattr(tr, 'generate_json', fake_generate)
    monkeypatch.setattr(tr, 'resolve_model', lambda _: 'same-model')
    lines = [MergedLine(start=0, end=1, text='うれしい', utterance_ids=[0])]
    cache = tmp_path / 'cache'
    tr.translate_lines(lines, cache)
    tr.translate_lines(lines, cache)
    assert len(calls) == 1
    base.write_text('愛MUST BE\n元凶')
    tr.translate_lines(lines, cache)
    assert len(calls) == 2
    assert len(list(cache.glob('*.json'))) == 2
