"""SRT timecode formatting and validation."""

import re


TIMECODE_LINE_REGEX = re.compile(
    r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}$"
)


def format_timecode(seconds: float) -> str:
    """Format a duration in seconds as an SRT timecode `HH:MM:SS,mmm`."""
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


_SINGLE_TC = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_timecode_ms(tc: str) -> int:
    """Parse a single SRT timecode component into total milliseconds."""
    m = _SINGLE_TC.match(tc.strip())
    if not m:
        raise ValueError(f"Cannot parse timecode: {tc!r}")
    h, mi, s, ms = (int(g) for g in m.groups())
    return h * 3_600_000 + mi * 60_000 + s * 1000 + ms


def format_timecode_ms(ms: int) -> str:
    """Format total milliseconds as an SRT timecode `HH:MM:SS,mmm`."""
    ms = max(0, ms)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
