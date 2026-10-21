"""Remote source resolution and the Bilibili player-API download — no network."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.maijev.source as src  # noqa: E402

BV = "BV1iKnv6mEo4"
PAGES = {"code": 0, "data": [{"cid": 11, "page": 1, "part": "P1"},
                             {"cid": 22, "page": 2, "part": "P2"}]}
PLAY = {"code": 0, "data": {"dash": {
    "video": [
        {"id": 64, "codecid": 12, "bandwidth": 900, "height": 720, "baseUrl": "https://cdn/720-hevc"},
        {"id": 64, "codecid": 7, "bandwidth": 800, "height": 720, "baseUrl": "https://cdn/720-avc"},
        {"id": 32, "codecid": 7, "bandwidth": 500, "height": 480, "baseUrl": "https://cdn/480-avc"},
    ],
    "audio": [{"bandwidth": 65000, "baseUrl": "https://cdn/a64k"},
              {"bandwidth": 130000, "baseUrl": "https://cdn/a132k"}],
}}}


@pytest.fixture
def api(monkeypatch):
    """Fake player API, CDN and ffmpeg; records every call."""
    calls = {"json": [], "fetch": []}

    def fake_json(url, headers):
        calls["json"].append((url, headers))
        return PAGES if "/pagelist?" in url else PLAY

    def fake_fetch(url, dest):
        calls["fetch"].append(url)
        dest.write_bytes(b"m4s")

    def fake_ffmpeg(cmd, check):
        Path(cmd[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(src, "_get_json", fake_json)
    monkeypatch.setattr(src, "_fetch", fake_fetch)
    monkeypatch.setattr(src.subprocess, "run", fake_ffmpeg)
    return calls


def test_bilibili_part_is_parsed_from_url():
    assert src.resolve_source(BV) == src.VideoSource(BV, "bilibili", 1)
    s = src.resolve_source(f"https://www.bilibili.com/video/{BV}/?p=2&spm_id_from=333")
    assert (s.video_id, s.page) == (BV, 2)
    assert s.url == f"https://www.bilibili.com/video/{BV}?p=2"
    assert src.resolve_source(f"{BV}?p=0").page == 1


def test_bilibili_downloads_best_dash_streams_via_player_api(api, tmp_path):
    out = src.download_video(src.resolve_source(f"{BV}?p=2"), tmp_path)
    assert out == tmp_path / "source.mp4"
    assert out.read_bytes() == b"mp4"
    playurl, headers = api["json"][1]
    assert f"bvid={BV}" in playurl and "cid=22" in playurl
    assert headers["Referer"] == "https://www.bilibili.com/"
    assert api["fetch"] == ["https://cdn/720-avc", "https://cdn/a132k"]
    assert [p.name for p in tmp_path.iterdir()] == ["source.mp4"]


def test_bilibili_download_resumes_without_network(api, tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"done")
    assert src.download_video(src.resolve_source(BV), tmp_path).read_bytes() == b"done"
    assert api["json"] == []


def test_bilibili_rejects_missing_part(api, tmp_path):
    with pytest.raises(ValueError, match="no p=3"):
        src._download_bilibili(src.resolve_source(f"{BV}?p=3"), tmp_path)


@pytest.mark.parametrize("failure", [
    HTTPError("https://api.bilibili.com", 412, "Precondition Failed", {}, None),
    {"code": -404, "message": "啥都木有"},
])
def test_bilibili_falls_back_to_ytdlp(monkeypatch, tmp_path, failure):
    def fake_json(url, headers):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(src, "_get_json", fake_json)
    monkeypatch.setattr(src, "_download_ytdlp", lambda s, d: d / "via-ytdlp.mp4")
    assert src.download_video(src.resolve_source(BV), tmp_path) == tmp_path / "via-ytdlp.mp4"


def test_other_platforms_still_use_ytdlp(monkeypatch, tmp_path):
    monkeypatch.setattr(src, "_get_json", lambda *a: pytest.fail("player API called"))
    monkeypatch.setattr(src, "_download_ytdlp", lambda s, d: d / "via-ytdlp.mp4")
    assert src.download_video(src.resolve_source("v=dQw4w9WgXcQ"), tmp_path).name == "via-ytdlp.mp4"
