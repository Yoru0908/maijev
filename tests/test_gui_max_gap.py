import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from flows.maijev.gui import server


@pytest.fixture
def gui(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(server, 'PROCS', {})
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')  # any LLM backend passes check_model
    popen = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(server.subprocess, 'Popen', popen)
    server.RUNS.mkdir()
    return popen


def launched_cmd(popen):
    return popen.call_args.args[0]


def test_max_gap_reaches_pipeline_and_survives_rerun(gui):
    wd = server.RUNS / 'job'
    server._launch(wd, server.JobRequest(input='input.mp4', max_gap=0.5))
    cmd = launched_cmd(gui)
    assert cmd[cmd.index('--max-gap') + 1] == '0.5'
    assert json.loads((wd / 'job.json').read_text())['max_gap'] == 0.5
    server.PROCS.clear()
    server.rerun_job('job')
    assert '--max-gap' in launched_cmd(gui)


def test_max_gap_left_empty_keeps_default(gui):
    server._launch(server.RUNS / 'job', server.JobRequest(input='input.mp4'))
    assert '--max-gap' not in launched_cmd(gui)


def test_max_gap_not_passed_to_asr_only_runs(gui):
    server._launch(server.RUNS / 'job', server.JobRequest(input='input.mp4', mode='asr', max_gap=0.5))
    assert '--max-gap' not in launched_cmd(gui)


@pytest.mark.parametrize('value', [0, -1])
def test_max_gap_must_be_positive(value):
    with pytest.raises(ValidationError):
        server.JobRequest(input='input.mp4', max_gap=value)
