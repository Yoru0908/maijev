"""Video source resolution: yt-dlp download + platform talent metadata.

Accepts a local file path or a source string (platform video ID or full URL)
for Bilibili / TVer / Abema / YouTube. TVer and Abema additionally expose cast
(talents) metadata, which the pipeline injects into the pre-pass as
authoritative person anchors — they bypass JEV classification entirely.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from loguru import logger


@dataclass(frozen=True)
class VideoSource:
    """A resolved remote video source."""

    video_id: str
    platform: str  # bilibili | youtube | tver | abema

    @property
    def url(self) -> str:
        if self.platform == "bilibili":
            return f"https://www.bilibili.com/video/{self.video_id}"
        if self.platform == "tver":
            return f"https://tver.jp/episodes/{self.video_id}"
        if self.platform == "abema":
            return f"https://abema.tv/video/episode/{self.video_id}"
        if self.platform == "youtube":
            return f"https://www.youtube.com/watch?v={self.video_id[2:]}"
        raise ValueError(f"unsupported platform: {self.platform}")


@dataclass
class SourceMeta:
    """Metadata collected while resolving a remote source."""

    video_id: str
    platform: str
    url: str
    title: str | None = None
    talents: list[str] = field(default_factory=list)


def parse_source_id(source_str: str) -> str:
    """Extract a platform video ID from a source string (URL or bare ID)."""
    bv_match = re.search(r"(BV[a-zA-Z0-9]+)", source_str)
    if bv_match:
        return bv_match.group(1)
    if source_str.startswith("v="):
        return source_str
    if "youtube.com" in source_str or "youtu.be" in source_str:
        parsed = urlparse(source_str)
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return f"v={qs['v'][0]}"
        parts = parsed.path.strip("/").split("/")
        if parts and parts[-1]:
            return f"v={parts[-1]}"
        raise ValueError(f"invalid YouTube URL: {source_str}")
    if (
        "bilibili.com" in source_str
        or "tver.jp" in source_str
        or "abema.tv" in source_str
    ):
        parts = urlparse(source_str).path.strip("/").split("/")
        if parts and parts[-1]:
            return parts[-1]
    if source_str.startswith(("https://", "http://")):
        raise ValueError(f"unsupported video source URL: {source_str}")
    return source_str


def classify_platform(video_id: str) -> str:
    """Infer the platform from a bare video ID."""
    if video_id.startswith("BV"):
        return "bilibili"
    if video_id.startswith("v="):
        return "youtube"
    # TVer ids start with 'ep'/'sh' and are purely alphanumeric; Abema ids
    # contain '_'/'-' or start with digits — Abema is the fallback.
    if video_id.startswith(("ep", "sh")) and video_id.isalnum():
        return "tver"
    return "abema"


def resolve_source(source_str: str) -> VideoSource:
    video_id = parse_source_id(source_str)
    return VideoSource(video_id, classify_platform(video_id))


def download_video(source: VideoSource, dest_dir: Path) -> Path:
    """Download the source video with yt-dlp; returns the video file path."""
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "source.%(ext)s")
    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "format": "bestvideo+bestaudio/best",
        "quiet": True,
        "no_warnings": True,
    }
    logger.info(f"Downloading {source.platform}:{source.video_id} via yt-dlp")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source.url, download=True)
        title = info.get("title") if isinstance(info, dict) else None
    for ext in ("mp4", "mkv", "webm", "m4a", "mp3"):
        candidate = dest_dir / f"source.{ext}"
        if candidate.exists():
            logger.success(f"Downloaded: {title or candidate.name}")
            return candidate
    raise RuntimeError(f"yt-dlp finished but no media file in {dest_dir}")


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": _UA, **headers},
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _tver_talents(episode_id: str) -> list[str]:
    data = _get_json(
        f"https://contents-api.tver.jp/contents/api/v1/episodes/"
        f"{episode_id}/talents",
        {
            "Origin": "https://tver.jp",
            "Referer": "https://tver.jp/",
            "x-tver-platform-type": "web",
        },
    )
    raw = data.get("talents")
    if not isinstance(raw, list):
        return []
    return [
        t["name"]
        for t in raw
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]


def _abema_device_token() -> str:
    """Anonymous ABEMA device token, reusing yt-dlp's app-key routine."""
    from yt_dlp.extractor.abematv import AbemaTVBaseIE

    device_id = str(uuid.uuid4())
    request = Request(
        "https://api.abema.io/v1/users",
        data=json.dumps(
            {
                "deviceId": device_id,
                "applicationKeySecret": AbemaTVBaseIE._generate_aks(device_id),
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://abema.tv",
            "Referer": "https://abema.tv/",
            "User-Agent": _UA,
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("ABEMA token response did not contain a token")
    return token


def _abema_talents(episode_id: str) -> list[str]:
    data = _get_json(
        f"https://api.abema.io/v1/video/programs/{episode_id}",
        {
            "Authorization": f"bearer {_abema_device_token()}",
            "Origin": "https://abema.tv",
            "Referer": "https://abema.tv/",
        },
    )
    raw = data.get("credit", {}).get("casts")
    if not isinstance(raw, list):
        return []
    # Role headers arrive as "■role" strings interleaved with names.
    return [
        c.strip()
        for c in raw
        if isinstance(c, str) and c.strip() and not c.startswith("■")
    ]


def fetch_talents(source: VideoSource) -> list[str]:
    """Best-effort cast list for TVer/Abema; empty on failure or other platforms."""
    try:
        if source.platform == "tver":
            names = _tver_talents(source.video_id)
        elif source.platform == "abema":
            names = _abema_talents(source.video_id)
        else:
            return []
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"talent fetch failed for {source.video_id}: {exc}")
        return []
    logger.info(f"{len(names)} cast names from {source.platform} metadata")
    return names
