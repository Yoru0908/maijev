"""Isolated Homeserver subtitle queue. Originals and QQ notifications stay in watchers.

Watchers call enqueue(payload); cron runs `python -m flows.maijev.auto_publish run`.
All writable paths are below /vol1/maijev; no automatic retry after an ambiguous upload.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from flows.maijev.burn import burn

ROOT = Path("/vol1/maijev")
JOBS = ROOT / "jobs"
RUNS = ROOT / "runs"
FONT_DIR = ROOT / "fonts"
ALLOWED = {"nhk", "phone"}
KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
BV = re.compile(r"BV[0-9A-Za-z]{10}")
load_dotenv(ROOT / "app" / ".env")


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".new")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def enqueue(data: dict) -> bool:
    source, source_id = data["source"], data["source_id"]
    if source not in ALLOWED or not KEY.fullmatch(source_id):
        raise ValueError("invalid source or source_id")
    video = Path(data["video"]).resolve(strict=True)
    if not video.is_file() or video.suffix.lower() != ".mp4" or video.stat().st_size < 100_000:
        raise ValueError("invalid input video")
    if source == "nhk" and not video.is_relative_to(Path("/vol1/nhk_downloads")):
        raise ValueError("NHK input outside authoritative download directory")
    if source == "phone" and not (video.is_relative_to(Path("/vol1/msg-pusher")) or
                                   video.is_relative_to(Path("/home/srzwyuu/napcat-v13-config/msg-phone-videos"))):
        raise ValueError("phone input outside expected directory")
    if not BV.fullmatch(data["original_bv"]):
        raise ValueError("original BV not confirmed; refusing auto-publish")
    dest = JOBS / f"{source}-{source_id}.json"
    if dest.exists():
        return False
    if len(list(JOBS.glob("*.json"))) >= 100:
        raise RuntimeError("subtitle job limit reached")
    save(dest, {"source": source, "source_id": source_id, "video": str(video),
                "original_bv": data["original_bv"], "title": str(data["title"])[:70],
                "desc": str(data.get("desc", ""))[:500],
                "tags": list(data.get("tags", []))[:10],
                "tid": int(data["tid"]), "copyright": int(data["copyright"]),
                "source_url": str(data.get("source_url", ""))[:500],
                "extra_fields": data.get("extra_fields"), "status": "pending"})
    return True


def disk_ok() -> bool:
    usage = shutil.disk_usage(ROOT)
    return (usage.used / usage.total < .85 and usage.free >= 8 * 1024**3)


def uploaded_bv(output: str, original_bv: str) -> str | None:
    candidates = set(BV.findall(output)) - {original_bv}
    return next(iter(candidates)) if len(candidates) == 1 else None


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def process(path: Path) -> None:
    job = json.loads(path.read_text(encoding="utf-8"))
    if job["status"] != "pending":
        return
    video = Path(job["video"])
    if not video.is_file() or not disk_ok():
        raise RuntimeError("input missing or /vol1 disk guard tripped")
    wd = RUNS / path.stem
    wd.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TMPDIR"] = str(ROOT / "tmp")
    env["TMP"] = env["TMPDIR"]
    env["TEMP"] = env["TMPDIR"]
    env["UV_CACHE_DIR"] = str(ROOT / "cache")
    srt = wd / "out_zh.srt"
    rendered = wd / "zh.mp4"
    if not srt.exists() or not srt.stat().st_size:
        subprocess.run([sys.executable, "-m", "flows.maijev.pipeline", str(video), str(wd),
                        "--translate", "--prepass"], cwd=ROOT / "app", env=env,
                       check=True, timeout=10800)
    if not srt.exists() or not srt.stat().st_size:
        raise RuntimeError("missing translated SRT")
    # Burn to a separate path; retain SRT and ASS for inspection/recovery.
    if not rendered.exists() or not rendered.stat().st_size:
        if not disk_ok():
            raise RuntimeError("disk guard tripped before rendering")
        burn(video, srt, rendered, FONT_DIR)
    if abs(duration(video) - duration(rendered)) > 1.5:
        raise RuntimeError("rendered duration differs from original")
    if not disk_ok():
        raise RuntimeError("disk guard tripped before upload")
    title = ("【中字】 " + job["title"])[:80]
    desc = (job["desc"] + "\n中文字幕由自动流程生成。\n原档：https://www.bilibili.com/video/"
            + job["original_bv"])[:2000]
    cmd = ["/home/srzwyuu/venv/bin/python3", "-m", "biliup", "upload",
           "--title", title, "--desc", desc, "--tag", ",".join(job["tags"]),
           "--tid", str(job["tid"]), "--copyright", str(job["copyright"])]
    if job["copyright"] == 2 and job["source_url"]:
        cmd.extend(["--source", job["source_url"]])
    if job.get("extra_fields"):
        cmd.extend(["--extra-fields", json.dumps(job["extra_fields"], ensure_ascii=False)])
    cmd.append(str(rendered))
    # A crash/timeout/network error while submitting is ambiguous. Never blindly retry.
    job["status"] = "submitting"
    save(path, job)
    try:
        result = subprocess.run(cmd, cwd="/vol1/sakuradio", env=env,
                                capture_output=True, text=True, timeout=7200)
        subtitle_bv = uploaded_bv((result.stdout or "") + (result.stderr or ""),
                                  job["original_bv"])
        if result.returncode != 0 or not subtitle_bv:
            raise RuntimeError(f"upload uncertain (exit={result.returncode}); manual review required: "
                               + (result.stderr or "")[-300:])
        job["subtitle_bv"] = subtitle_bv
        job["status"] = "uploaded"
        save(path, job)
        print(f"{path.stem}: {subtitle_bv}")
    except Exception:
        job["status"] = "review_upload"
        save(path, job)
        raise


def run() -> None:
    # Single cron worker plus persistent per-job state; never compete with another uploader.
    import fcntl
    ROOT.joinpath("tmp").mkdir(parents=True, exist_ok=True)
    JOBS.mkdir(parents=True, exist_ok=True)
    with (ROOT / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        for path in sorted(JOBS.glob("*.json")):
            if not disk_ok():
                print("disk guard tripped; stopping", file=sys.stderr)
                break
            try:
                process(path)
            except Exception as exc:
                print(f"{path.name}: {exc}", file=sys.stderr)
                job = json.loads(path.read_text())
                if job["status"] == "pending":
                    job["status"] = "failed"
                    job["error"] = str(exc)[-300:]
                    save(path, job)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("enqueue", "run"))
    args = parser.parse_args()
    if args.action == "enqueue":
        print("enqueued" if enqueue(json.load(sys.stdin)) else "already enqueued")
    else:
        run()


if __name__ == "__main__":
    main()
