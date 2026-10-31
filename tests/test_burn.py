"""Tests for the standalone ASS style/burn stage (no network or LLM)."""
from pathlib import Path
import subprocess
import tempfile
import pytest

from flows.maijev.burn import PORTRAIT, STYLE, _lines, make_ass, burn, style_for

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


def _ass(text: str, size=None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "sample.srt"
        ass = Path(tmp) / "sample.ass"
        srt.write_text(f"1\n00:00:00,000 --> 00:00:01,500\n{text}\n", encoding="utf-8")
        make_ass(srt, ass, size=size)
        return ass.read_text(encoding="utf-8")


def test_canvas_follows_video_aspect():
    line = "山川宇衣测试"
    assert _ass(line, (1920, 1080)) == _ass(line)  # landscape: template unchanged
    assert "PlayResY: 1080" in _ass(line, (1280, 720))
    portrait = _ass(line, (1152, 2048))
    assert "PlayResX: 1920" in portrait and "PlayResY: 3413" in portrait


def test_style_follows_orientation():
    assert style_for((1152, 2048)) == PORTRAIT
    assert style_for((1920, 1080)) == STYLE
    assert style_for((1080, 1080)) == STYLE
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "sample.srt"
        ass = Path(tmp) / "sample.ass"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,500\n山川宇衣测试\n", encoding="utf-8")
        make_ass(srt, ass, PORTRAIT, (1152, 2048))
        text = ass.read_text(encoding="utf-8")
        assert "PlayResY: 3413" in text and "Style: 山川宇衣,LXGW WenKai GB,104," in text


def test_long_lines_wrap_evenly():
    short = "心里想着要是能变得可爱点就好了"
    assert _lines(short, 30.5) == [short]
    long = "心里想着要是能变得可爱点就好了但是好像还是有点难呢真的不知道该怎么办"
    half = len(long) // 2
    assert _lines(long, 30.5) == [long[:half], long[half:]]
    spaced = "万圣节要做些什么呢我想了很多 虽然纠结了挺多事情但我想尝试点新鲜的东西"
    assert _lines(spaced, 30.5) == spaced.split(" ")
    three = _lines("一" * 70, 30.5)
    assert len(three) == 3 and max(map(len, three)) - min(map(len, three)) <= 1
    assert _lines("ANN0の" + "一" * 40, 30.5)[0].startswith("ANN0の")
    # a space a few characters off the middle beats cutting a word
    assert _lines("对 所以呢 嗯 如果写出来了你要看哦 就是这种感觉呢", 23.2) == [
        "对 所以呢 嗯", "如果写出来了你要看哦 就是这种感觉呢"]
    text = _ass(long).split("[Events]")[1]
    assert long[:half] + r"\N" + long[half:] in text


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
