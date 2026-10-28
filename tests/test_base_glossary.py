from flows.maijev import translate_llm as tr
from flows.maijev.segment_llm import MergedLine


def test_base_survives_manual_glossary_override(monkeypatch, tmp_path):
    base, auto, manual = [tmp_path / p for p in ['base.md', 'auto.md', 'manual.md']]
    base.write_text('過去曲\n愛MUST BE')
    auto.write_text('【本片主题】深夜电台\n'
                    '[节目] オールナイトニッポン0 / オールナイトニッポンゼロ -> オールナイトニッポン0')
    manual.write_text('オールナイトニッポン0 -> ANN0\n人名修正')
    monkeypatch.setenv('TRANSLATE_BASE_GLOSSARY_PATH', str(base))
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(manual))
    system = tr._load_system(auto)
    assert system.startswith(tr.PROMPT_PATH.read_text().strip())
    # one answer per entity: the manual row took over the pre-pass entity
    assert '[节目] オールナイトニッポン0 / オールナイトニッポンゼロ -> ANN0' in system
    assert '-> オールナイトニッポン0' not in system
    assert (system.index('深夜电台') < system.index('ANN0')
            < system.index('過去曲') < system.index('人名修正'))
    assert '愛MUST BE' in system
    assert tr._clean_zh('嗯，没关系啊。') == '没关系'


def test_manual_kana_left_in_zh_is_replaced_in_every_batch(monkeypatch, tmp_path):
    auto, manual = tmp_path / 'auto.md', tmp_path / 'manual.md'
    auto.write_text('[人名] 佐藤美咲 / みさき / みーちゃん -> 佐藤美咲')
    manual.write_text('オールナイトニッポン0 -> ANN0')
    monkeypatch.delenv('TRANSLATE_BASE_GLOSSARY_PATH', raising=False)
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(manual))
    monkeypatch.setattr(tr, 'BATCH_SIZE', 1)
    replies = iter(['ANN0开始了', 'オールナイトニッポン0又来了 みーちゃん'])
    monkeypatch.setattr(tr, 'generate_json', lambda body, **kw: {
        'lines': [{'id': int(body.split('\t')[0]), 'zh': next(replies)}]})
    monkeypatch.setattr(tr, 'resolve_model', lambda _: 'same-model')
    monkeypatch.setattr(tr, 'MAX_WORKERS', 1)
    lines = [MergedLine(start=i, end=i + 1, text=t, utterance_ids=[i])
             for i, t in enumerate(['オールナイトニッポン0！', 'オールナイトニッポン0です'])]
    # the manual key is enforced; the pre-pass guess みーちゃん only advises
    assert tr.translate_lines(lines, None, auto) == ['ANN0开始了', 'ANN0又来了 みーちゃん']


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
