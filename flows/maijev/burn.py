"""Convert maijev SRT to styled ASS and burn it into a video.

Example (Homeserver: keep all inputs/work/output under /vol1):
  TMPDIR=/vol1/tmp uv run python -m flows.maijev.burn input.mp4 runs/job/out_zh.srt \
    runs/job/zh.mp4 --fontsdir /vol1/maijev/fonts
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
from pathlib import Path

STYLE = Path(__file__).parent / "styles" / "yamakawa_ui.tpl"
# Phone-call videos are 9:16; the landscape proportions read small there.
PORTRAIT = STYLE.with_name("yamakawa_ui_portrait.tpl")
# libass sizes a font by usWinAscent + usWinDescent. LXGW WenKai GB has 1032 + 285
# over 1000 units/em, so a CJK glyph is 0.76 x Fontsize wide; burn() refuses other fonts.
EM = 1000 / (1032 + 285)
BREAKS = " \u3000，。！？、；：…,.!?;:"


def _fields(styles: str) -> dict[str, str]:
    """The template's one style line, keyed by its Format header."""
    rows = {line.split(":", 1)[0]: line.split(":", 1)[1].split(",")
            for line in styles.splitlines() if line.startswith(("Format:", "Style:"))}
    return {k.strip(): v.strip() for k, v in zip(rows["Format"], rows["Style"])}


def _play_x(script: str) -> int:
    return int(re.search(r"^PlayResX:\s*(\d+)", script, re.M)[1])


def _fit(script: str, size: tuple[int, int] | None) -> str:
    """Give the canvas the video's aspect ratio, keeping the template's width.

    libass scales glyphs by PlayResY: a 1920x1080 canvas on a 1152x2048 phone
    video makes them 1.9x taller while the frame is only 1152 wide, so lines
    run off both edges. 16:9 sources keep the template's 1080 unchanged.
    """
    if size is None:
        return script
    w, h = size
    return re.sub(r"^PlayResY:.*$", f"PlayResY: {round(_play_x(script) * h / w)}",
                  script, flags=re.M)


def _cols(script: str, style: dict[str, str]) -> float:
    """Full-width glyphs that fit between the margins."""
    glyph = (float(style["Fontsize"]) * float(style["ScaleX"]) / 100 * EM
             + float(style["Spacing"]))
    return (_play_x(script) - int(style["MarginL"]) - int(style["MarginR"])) / glyph


def _width(text: str) -> float:
    return sum(0.6 if c.isascii() else 1 for c in text)


def _lines(text: str, cols: float) -> list[str]:
    """Fewest lines that fit, each cut at the space or punctuation nearest an even share."""
    n = math.ceil(_width(text) / cols)
    if n <= 1:
        return [text]
    at = round(len(text) / n)
    while at > 1 and _width(text[:at]) > cols:
        at -= 1
    reach = max(4, len(text) // (2 * n))  # up to a quarter-line off for a natural break
    near = sorted(range(max(1, at - reach), min(len(text), at + reach + 1)),
                  key=lambda i: abs(i - at))
    cut = next((i for i in near if text[i - 1] in BREAKS and _width(text[:i]) <= cols), at)
    return [text[:cut].rstrip(), *_lines(text[cut:].lstrip(), cols)]


def _wrap(text: str, cols: float) -> str:
    """libass on Debian 12 never breaks between CJK characters: add explicit \\N."""
    if "{" in text:
        return text
    return r"\N".join(line for seg in text.split(r"\N") for line in _lines(seg, cols))


def make_ass(srt: Path, ass: Path, template: Path = STYLE,
             size: tuple[int, int] | None = None) -> None:
    """Use FFmpeg for timestamp/text conversion, then apply the named template style.

    ``size`` is the video's (width, height); without it the template canvas is kept.
    """
    if not srt.is_file() or srt.stat().st_size == 0:
        raise ValueError(f"missing or empty SRT: {srt}")
    tpl = template.read_text(encoding="utf-8-sig")
    script = _fit(tpl.split("[Script Info]", 1)[1].split("[V4+ Styles]", 1)[0].strip(), size)
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
    cols = _cols(script, _fields(styles))
    lines = []
    for line in events.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if len(fields) != 10:
                raise ValueError("invalid ASS dialogue")
            fields[3] = "山川宇衣"
            fields[9] = _wrap(fields[9], cols)
            line = ",".join(fields)
        lines.append(line)
    if not any(line.startswith("Dialogue:") for line in lines):
        raise ValueError("SRT produced no dialogue")
    ass.write_text("[Script Info]\n" + script + "\n\n[V4+ Styles]\n" + styles
                   + "\n\n[Events]\n" + "\n".join(lines) + "\n", encoding="utf-8")


def video_size(video: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                        str(video)], check=True, capture_output=True, text=True)
    w, h = r.stdout.strip().split("x")[:2]
    return int(w), int(h)


def style_for(size: tuple[int, int]) -> Path:
    """Portrait videos get the portrait template; landscape and square the original."""
    w, h = size
    return PORTRAIT if h > w else STYLE


def burn(video: Path, srt: Path, output: Path, fontsdir: Path,
         template: Path | None = None) -> Path:
    """``template`` overrides the style chosen from the video's aspect ratio."""
    if not video.is_file() or not fontsdir.is_dir():
        raise ValueError("video or fonts directory missing")
    if not (fontsdir / "LXGWWenKaiGB-Medium.ttf").is_file():
        raise ValueError("LXGW WenKai GB font missing; refusing font fallback")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ass = output.with_suffix(".ass")
    partial = output.with_name(output.stem + ".partial.mp4")
    size = video_size(video)
    make_ass(srt, ass, template or style_for(size), size)
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
    parser.add_argument("--template", type=Path,
                        help="default: chosen by aspect ratio (portrait/landscape)")
    args = parser.parse_args()
    burn(args.video, args.srt, args.output, args.fontsdir, args.template)


if __name__ == "__main__":
    main()
