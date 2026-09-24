"""Tests for the standalone ASS style/burn stage (no network or LLM)."""
from pathlib import Path
import subprocess
import tempfile
import pytest

from flows.maijev.burn import make_ass, burn

FONT_DIR = Path.home() / "Library/Fonts"


def test_make_ass():
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "sample.srt"
        ass = Path(tmp) / "sample.ass"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,500\n山川宇衣测试\n", encoding="utf-8")
        make_ass(srt, ass)
        text = ass.read_text(encoding="utf-8")
        assert "Style: 山川宇衣,LXGW WenKai GB,78," in text
        assert "PlayResX: 1920" in text
        assert "Dialogue: 0,0:00:00.00,0:00:01.50,山川宇衣," in text
        assert "山川宇衣测试" in text


def test_burn_smoke():
    if not (FONT_DIR / "LXGWWenKaiGB-Medium.ttf").exists() or "Filter ass" not in subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", "filter=ass"], capture_output=True, text=True
    ).stdout:
        pytest.skip("requires FFmpeg with libass and LXGW WenKai GB")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        srt = root / "sample.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n山川宇衣测试\n", encoding="utf-8")
        video = root / "input.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=black:s=1920x1080:r=1:d=1", "-c:v", "libx264", str(video)],
                       check=True)
        output = burn(video, srt, root / "output.mp4", FONT_DIR)
        assert output.is_file() and output.stat().st_size > 0
