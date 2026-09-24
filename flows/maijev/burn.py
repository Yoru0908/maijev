"""Convert maijev SRT to styled ASS and burn it into a video.

Example (Homeserver: keep all inputs/work/output under /vol1):
  TMPDIR=/vol1/tmp uv run python -m flows.maijev.burn input.mp4 runs/job/out_zh.srt \
    runs/job/zh.mp4 --fontsdir /vol1/maijev/fonts
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

STYLE = Path(__file__).parent / "styles" / "yamakawa_ui.tpl"


def make_ass(srt: Path, ass: Path, template: Path = STYLE) -> None:
    """Use FFmpeg for timestamp/text conversion, then apply the named template style."""
    if not srt.is_file() or srt.stat().st_size == 0:
        raise ValueError(f"missing or empty SRT: {srt}")
    tpl = template.read_text(encoding="utf-8-sig")
    script = tpl.split("[Script Info]", 1)[1].split("[V4+ Styles]", 1)[0].strip()
    styles = tpl.split("[V4+ Styles]", 1)[1].strip()
    style_names = [line.split(":", 1)[1].split(",", 1)[0].strip()
                   for line in styles.splitlines() if line.startswith("Style:")]
    if style_names != ["山川宇衣"]:
        raise ValueError("expected exactly the 山川宇衣 style")
    ass.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-sub_charenc", "UTF-8", "-i", str(srt), "-f", "ass", str(ass)],
                   check=True)
    converted = ass.read_text(encoding="utf-8-sig")
    if "[Events]" not in converted:
        raise ValueError("FFmpeg produced no ASS events")
    events = converted.split("[Events]", 1)[1]
    lines = []
    for line in events.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if len(fields) != 10:
                raise ValueError("invalid ASS dialogue")
            fields[3] = "山川宇衣"
            line = ",".join(fields)
        lines.append(line)
    if not any(line.startswith("Dialogue:") for line in lines):
        raise ValueError("SRT produced no dialogue")
    ass.write_text("[Script Info]\n" + script + "\n\n[V4+ Styles]\n" + styles
                   + "\n\n[Events]\n" + "\n".join(lines) + "\n", encoding="utf-8")


def burn(video: Path, srt: Path, output: Path, fontsdir: Path,
         template: Path = STYLE) -> Path:
    if not video.is_file() or not fontsdir.is_dir():
        raise ValueError("video or fonts directory missing")
    if not (fontsdir / "LXGWWenKaiGB-Medium.ttf").is_file():
        raise ValueError("LXGW WenKai GB font missing; refusing font fallback")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ass = output.with_suffix(".ass")
    partial = output.with_name(output.stem + ".partial.mp4")
    make_ass(srt, ass, template)
    # Work in output directory so FFmpeg's filter paths have no platform-specific
    # escaping issues (job names must be safe; fontsdir is an absolute path).
    if any(c in str(fontsdir) for c in "':,[]\\"):
        raise ValueError("fontsdir has characters requiring FFmpeg filter escaping")
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(video.resolve()),
                        "-vf", f"ass={ass.name}:fontsdir={fontsdir.resolve()}",
                        "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
                        "-preset", "medium", "-crf", "20", "-c:a", "copy",
                        "-movflags", "+faststart", str(partial.name)],
                       cwd=output.parent, check=True)
        if not partial.is_file() or partial.stat().st_size == 0:
            raise ValueError("empty rendered video")
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(partial)],
                                check=True, capture_output=True, text=True)
        if float(result.stdout.strip()) <= 0:
            raise ValueError("invalid rendered video duration")
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Burn maijev SRT with 山川宇衣 ASS style")
    parser.add_argument("video", type=Path)
    parser.add_argument("srt", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fontsdir", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=STYLE)
    args = parser.parse_args()
    burn(args.video, args.srt, args.output, args.fontsdir, args.template)


if __name__ == "__main__":
    main()
