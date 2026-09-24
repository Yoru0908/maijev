import json
from pathlib import Path

import pytest
from flows.maijev import auto_publish as ap


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
