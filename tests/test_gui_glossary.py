import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from flows.maijev.gui import server


@pytest.fixture
def gui(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(server, 'GLOSSARIES', tmp_path / 'glossaries')
    monkeypatch.setattr(server, 'PROCS', {})
    monkeypatch.delenv('TRANSLATE_GLOSSARY_PATH', raising=False)
    popen = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(server.subprocess, 'Popen', popen)
    server.RUNS.mkdir()
    return popen


def launched_glossary(popen):
    return popen.call_args.kwargs['env'].get('TRANSLATE_GLOSSARY_PATH')


def test_shared_glossary_round_trip_and_name_check(gui):
    assert server.list_glossaries() == []
    assert server.put_shared_glossary('46', server.GlossaryBody(text='  ねこがみ -> 猫神\n\n'))['lines'] == 1
    assert server.list_glossaries() == ['46']
    assert server.get_shared_glossary('46')['text'] == 'ねこがみ -> 猫神\n'
    for bad in ['../x', '.hidden', 'a b']:
        with pytest.raises(HTTPException):
            server.put_shared_glossary(bad, server.GlossaryBody(text='x'))


def test_delete_shared_glossary(gui):
    server.put_shared_glossary('46', server.GlossaryBody(text='ねこがみ -> 猫神'))
    server.delete_shared_glossary('46')
    assert server.list_glossaries() == []
    for name in ['46', '../x']:
        with pytest.raises(HTTPException):
            server.delete_shared_glossary(name)


def test_new_job_uses_shared_glossary_from_first_run(gui):
    server.put_shared_glossary('46', server.GlossaryBody(text='ねこがみ -> 猫神'))
    wd = server.RUNS / 'fresh'
    server._launch(wd, server.JobRequest(input='input.mp4', glossary='46'))
    assert launched_glossary(gui) == str(server.GLOSSARIES / '46.md')
    assert not (wd / 'glossary_effective.md').exists()
    assert json.loads((wd / 'job.json').read_text())['glossary'] == '46'


def test_layers_merge_env_then_shared_then_job(gui, monkeypatch, tmp_path):
    env_file = tmp_path / 'env.md'
    env_file.write_text('A -> env\nB -> env\nC -> env')
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(env_file))
    server.put_shared_glossary('46', server.GlossaryBody(text='B -> shared\nD -> shared'))
    wd = server.RUNS / 'job'
    wd.mkdir()
    (wd / 'glossary_user.md').write_text('C -> job\nB -> job')
    server._launch(wd, server.JobRequest(input='input.mp4', glossary='46'))
    merged = wd / 'glossary_effective.md'
    assert launched_glossary(gui) == str(merged)
    assert merged.read_text() == 'A -> env\nB -> job\nC -> job\nD -> shared\n'


def test_env_glossary_survives_job_edits(gui, monkeypatch, tmp_path):
    env_file = tmp_path / 'env.md'
    env_file.write_text('46 -> 46')
    monkeypatch.setenv('TRANSLATE_GLOSSARY_PATH', str(env_file))
    wd = server.RUNS / 'job'
    wd.mkdir()
    server._launch(wd, server.JobRequest(input='input.mp4'))
    assert launched_glossary(gui) == str(env_file)
    server.PROCS.clear()
    server.put_glossary('job', server.GlossaryBody(text='〇〇さん -> 全名'))
    server.rerun_job('job')
    assert (wd / 'glossary_effective.md').read_text() == '46 -> 46\n〇〇さん -> 全名\n'


def test_rerun_keeps_shared_glossary_and_old_jobs_still_load(gui):
    server.put_shared_glossary('46', server.GlossaryBody(text='ねこがみ -> 猫神'))
    wd = server.RUNS / 'job'
    server._launch(wd, server.JobRequest(input='input.mp4', glossary='46'))
    server.PROCS.clear()
    server.rerun_job('job')
    assert launched_glossary(gui) == str(server.GLOSSARIES / '46.md')
    server.PROCS.clear()
    (wd / 'job.json').write_text(json.dumps({'input': 'input.mp4', 'mode': 'zh'}))
    server.rerun_job('job')
    assert launched_glossary(gui) is None


def test_missing_shared_glossary_fails_before_creating_job(gui):
    with pytest.raises(HTTPException, match='共享词库不存在'):
        server._launch(server.RUNS / 'job', server.JobRequest(input='input.mp4', glossary='gone'))
    assert not (server.RUNS / 'job').exists()
    gui.assert_not_called()


def test_rerun_ignores_retired_translation_profile(gui):
    wd = server.RUNS / 'old'
    wd.mkdir()
    (wd / 'job.json').write_text(json.dumps(
        {'input': 'input.mp4', 'mode': 'zh', 'translation_profile': 'yamakawa-ui'}))
    server.rerun_job('old')
    assert '--translation-profile' not in gui.call_args.args[0]
