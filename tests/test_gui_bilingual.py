import json

import pytest
from fastapi import HTTPException

from flows.maijev.gui import server

LLM_KEYS = ('GEMINI_AGENT_PLATFORM_API_KEY', 'AGENT_PLATFORM_API_KEY', 'GEMINI_API_KEY',
            'CORRECT_MODEL', 'TRANSLATE_MODEL', 'GEMINI_MODEL')


@pytest.fixture
def gui(monkeypatch, tmp_path):
    from unittest.mock import Mock
    for key in LLM_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(server, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(server, 'PROCS', {})
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    popen = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(server.subprocess, 'Popen', popen)
    server.RUNS.mkdir()
    return popen


def test_bilingual_is_off_by_default_and_reaches_pipeline_when_ticked(gui, monkeypatch):
    server._launch(server.RUNS / 'a', server.JobRequest(input='in.mp4'))
    assert '--bilingual' not in gui.call_args.args[0]
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    server.PROCS.clear()
    server._launch(server.RUNS / 'b', server.JobRequest(input='in.mp4', bilingual=True))
    assert '--bilingual' in gui.call_args.args[0]
    job = json.loads((server.RUNS / 'b' / 'job.json').read_text())
    assert job['bilingual'] is True and job['version'].startswith(server.describe()[:3])


def test_bilingual_needs_native_gemini_before_anything_runs(gui):
    with pytest.raises(HTTPException) as err:
        server._launch(server.RUNS / 'a', server.JobRequest(input='in.mp4', bilingual=True))
    assert err.value.status_code == 400 and 'Gemini 原生接口' in err.value.detail
    assert not gui.called


def test_snapshot_reports_correction_only_when_every_segment_succeeded(gui):
    wd = server.RUNS / 'a'
    wd.mkdir()
    edits = [{'applied': True}, {'applied': False}, {'applied': True}]
    (wd / 'ja_corrections.json').write_text(json.dumps({'failed': [], 'edits': edits}))
    snap = server._snapshot(wd)
    assert snap['corrected'] is True and snap['fixed'] == 2
    assert 'ja_corrections.json' in snap['files']
    (wd / 'ja_corrections.json').write_text(json.dumps({'failed': ['00:00.0-10:00.0'], 'edits': edits}))
    assert server._snapshot(wd)['corrected'] is False
