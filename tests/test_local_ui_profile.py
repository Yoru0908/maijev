import json
from unittest.mock import Mock

import pytest

from flows.maijev import translate_llm as tr
from flows.maijev.segment_llm import MergedLine


@pytest.fixture
def local_ui(monkeypatch, tmp_path):
    base, prompt, terms, optimized = [tmp_path / p for p in ['base.md', 'ui.txt', 'ui.json', 'optimized.md']]
    base.write_text('過去曲\n愛MUST BE')
    prompt.write_text('先准确，再像她')
    terms.write_text(json.dumps({'entries': [{'ja': 'ういたん', 'zh': '宇衣碳'}]}))
    optimized.write_text('行云流水\n上下文理解\n翻译→反思→调整\n\n静态词库\n過去曲')
    for key, value in [('TRANSLATE_BASE_GLOSSARY_PATH', base),
                       ('LOCAL_TRANSLATE_UI_PROMPT_PATH', prompt),
                       ('LOCAL_TRANSLATE_OPTIMIZED_PROMPT_PATH', optimized),
                       ('LOCAL_TRANSLATE_UI_GLOSSARY_PATH', terms)]:
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('TRANSLATE_GLOSSARY_PATH', raising=False)
    return base


def test_ui_requires_local_prompt_and_complete_glossary(monkeypatch):
    monkeypatch.delenv('LOCAL_TRANSLATE_UI_PROMPT_PATH', raising=False)
    assert not tr.local_ui_available()
    with pytest.raises(ValueError, match='完整基础词库'):
        tr._load_system(profile='yamakawa-ui')


def test_ui_keeps_all_glossary_layers_and_generic_stays_default(local_ui, monkeypatch, tmp_path):
    auto, manual = tmp_path / 'auto.md', tmp_path / 'manual.md'
    auto.write_text('画面人名')
    manual.write_text('最终人工修正')
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(manual))
    monkeypatch.setenv('TRANSLATE_PROFILE', 'yamakawa-ui')  # Never selects a profile implicitly.
    assert tr._load_system().startswith(tr.PROMPT_PATH.read_text().strip())
    system = tr._load_system(auto, profile='yamakawa-ui')
    assert '先准确，再像她' in system and '愛MUST BE' in system
    for rule in ['行云流水', '上下文理解', '翻译→反思→调整', tr.PROMPT_PATH.read_text().strip()]:
        assert rule in system
    assert system.index('行云流水') < system.index('先准确，再像她')
    assert system.index('過去曲') < system.index('画面人名') < system.index('ういたん -> 宇衣碳') < system.index('最终人工修正')
    assert tr._clean_zh('嗯，没关系啊。 -“诶？”', profile='yamakawa-ui') == '嗯 没关系啊 -「诶？」'
    assert tr._clean_zh('嗯，没关系啊。') == '没关系'


def test_profile_cache_is_separate_and_ui_glossary_updates_invalidate_it(local_ui, monkeypatch, tmp_path):
    calls = []
    def generate(body, **kwargs):
        calls.append(kwargs['system'])
        return {'lines': [{'id': 0, 'zh': '嗯 好开心啊'}]}
    monkeypatch.setattr(tr, 'generate_json', generate)
    monkeypatch.setattr(tr, 'resolve_model', lambda _: 'same-model')
    lines = [MergedLine(start=0, end=1, text='うん、うれしい', utterance_ids=[0])]
    cache = tmp_path / 'cache'
    assert tr.translate_lines(lines, cache) == ['好开心']
    assert tr.translate_lines(lines, cache, profile='yamakawa-ui') == ['嗯 好开心啊']
    assert tr.translate_lines(lines, cache, profile='yamakawa-ui') == ['嗯 好开心啊']
    assert tr.translate_lines(lines, cache) == ['好开心']
    local_ui.write_text('過去曲\n愛MUST BE\n元凶')
    tr.translate_lines(lines, cache, profile='yamakawa-ui')
    assert len(calls) == 3
    assert len(list(cache.glob('*.json'))) == 3


def test_gui_rerun_keeps_explicit_ui_profile_and_manual_glossary(local_ui, monkeypatch, tmp_path):
    from flows.maijev.gui import server
    monkeypatch.setattr(server, 'RUNS', tmp_path)
    monkeypatch.setattr(server, 'PROCS', {})
    popen = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(server.subprocess, 'Popen', popen)
    wd = tmp_path / 'ui-task'
    wd.mkdir()
    (wd / 'glossary_user.md').write_text('最终人工词表')
    server._launch(wd, server.JobRequest(input='input.mp4', translation_profile='yamakawa-ui'))
    args, kwargs = popen.call_args
    assert args[0][args[0].index('--translation-profile') + 1] == 'yamakawa-ui'
    assert kwargs['env']['TRANSLATE_BASE_GLOSSARY_PATH'] == str(local_ui)
    assert kwargs['env']['TRANSLATE_GLOSSARY_PATH'] == str(wd / 'glossary_user.md')
    server.PROCS.clear()
    server.rerun_job(wd.name)
    args, _ = popen.call_args
    assert args[0][args[0].index('--translation-profile') + 1] == 'yamakawa-ui'
