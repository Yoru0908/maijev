import json
from pathlib import Path

import pytest
from flows.maijev import auto_publish as ap


def test_uploaded_bv_ignores_original_and_rejects_ambiguous():
    assert ap.uploaded_bv("desc BV1234567890 uploaded BV0987654321", "BV1234567890") == "BV0987654321"
    assert ap.uploaded_bv("desc BV1234567890", "BV1234567890") is None
    assert ap.uploaded_bv("BV0987654321 BV1111111111", "BV1234567890") is None


def test_all_subtitle_uploads_are_original_content():
    for original_copyright in (1, 2):
        cmd = ap.upload_command({"title": "测试", "desc": "", "original_bv": "BV1234567890",
                                 "tags": ["山川宇衣"], "tid": 21,
                                 "copyright": original_copyright,
                                 "source_url": "https://www.nhk.or.jp/"}, Path("zh.mp4"))
        assert cmd[cmd.index("--title") + 1] == "【中字】 测试"
        assert cmd[cmd.index("--desc") + 1] == "https://github.com/Yoru0908/maijev"
        assert cmd[cmd.index("--copyright") + 1] == "1"
        assert "--source" not in cmd


def test_enqueue_idempotent_and_requires_real_bv(tmp_path, monkeypatch):
    root = tmp_path / "vol1"
    videos = root / "nhk_downloads"
    videos.mkdir(parents=True)
    video = videos / "ep.mp4"
    video.write_bytes(b"0" * 100001)
    monkeypatch.setattr(ap, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(ap, "ALLOWED", {"nhk"})
    # The production allowlist is /vol1; use a local test-only equivalent.
    original = Path.is_relative_to
    monkeypatch.setattr(Path, "is_relative_to", lambda self, other: original(self, videos) if str(other) == "/vol1/nhk_downloads" else original(self, other))
    payload = {"source": "nhk", "source_id": "EP123", "video": str(video),
               "original_bv": "BV1234567890", "title": "title", "tid": 21, "copyright": 2}
    assert ap.enqueue(payload) is True
    assert ap.enqueue(payload) is False
    stored = json.loads((ap.JOBS / "nhk-EP123.json").read_text())
    assert stored["status"] == "pending"
    with pytest.raises(ValueError):
        ap.enqueue({**payload, "source_id": "EP124", "original_bv": "success"})
