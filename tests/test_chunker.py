"""Tests for silence detection (no network or LLM)."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _has_locale(name: str) -> bool:
    if shutil.which("locale") is None:  # Windows
        return False
    out = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout
    return name in out.split()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires FFmpeg")
def test_detect_silences_survives_non_utf8_locale():
    """Japanese metadata in ffmpeg's stderr must not crash on a Big5/cp950 locale."""
    if not _has_locale("zh_TW.Big5"):
        pytest.skip("requires the zh_TW.Big5 locale")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=f=440:d=3",
                        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                        "-filter_complex", "[0][1]concat=n=2:v=0:a=1[a]", "-map", "[a]",
                        "-t", "6", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        "-metadata", "title=【26 10 19配信】#385 まだひな", str(wav)],
                       check=True)
        env = {**os.environ, "LC_ALL": "zh_TW.Big5", "PYTHONUTF8": "0"}
        code = ("import sys; from pathlib import Path; "
                "from flows.maijev.chunker import detect_silences; "
                "print(detect_silences(Path(sys.argv[1])))")
        out = subprocess.run([sys.executable, "-c", code, str(wav)], cwd=ROOT, env=env,
                             capture_output=True, encoding="utf-8", errors="replace")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[4.5]"
