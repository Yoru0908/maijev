"""Local web UI for maijev — a thin shell over the CLI pipeline.

    uv sync --extra gui
    uv run python -m flows.maijev.gui [--host 127.0.0.1] [--port 8792] [--runs runs]

Design: every job is one ``python -m flows.maijev.pipeline`` subprocess and
one work_dir. The work_dir *is* the state — progress, results and the
glossary are all derived from the files the pipeline already writes, so
the pipeline itself needs no changes. Logs go to ``work_dir/gui.log`` and
are tailed over SSE. User glossary edits land in ``glossary_user.md`` and
reach the pipeline through ``TRANSLATE_GLOSSARY_PATH`` on re-run; the
translate cache key includes the system prompt, so only translation redoes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from flows.maijev.llm import DEFAULT_MODEL  # noqa: E402

STATIC = Path(__file__).parent / "static"
RUNS = ROOT / "runs"
PROCS: dict[str, subprocess.Popen] = {}
_COST: dict[str, tuple[float, float | None]] = {}

SAFE_NAME = re.compile(r"[^\w.\-]+")
MEDIA_EXT = {".mp4", ".mkv", ".mov", ".ts", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".webm"}
FILES = [
    "out_zh.srt", "out_llm_ja.srt", "out.srt", "glossary.md",
    "glossary_user.md", "timings.json", "source_meta.json", "job.json", "gui.log",
]

app = FastAPI(title="maijev")


class JobRequest(BaseModel):
    input: str
    name: str | None = None
    mode: Literal["asr", "ja", "zh"] = "zh"
    prepass: bool = True
    ocr_json: str | None = None
    extract_frames: bool = False
    model: str | None = None
    thinking_level: str | None = None


class GlossaryBody(BaseModel):
    text: str


# --- helpers ---------------------------------------------------------------

def _work_dir(job_id: str) -> Path:
    if SAFE_NAME.search(job_id) or job_id in {".", ".."}:
        raise HTTPException(400, "bad job id")
    wd = RUNS / job_id
    if not wd.is_dir():
        raise HTTPException(404, "job not found")
    return wd


def _running(job_id: str) -> bool:
    proc = PROCS.get(job_id)
    return proc is not None and proc.poll() is None


def _count(d: Path, pattern: str) -> int:
    return len(list(d.glob(pattern))) if d.is_dir() else 0


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _asr_cost(wd: Path) -> float | None:
    p = wd / "asr.json"
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    cached = _COST.get(wd.name)
    if cached is None or cached[0] != mtime:
        meta = (_read_json(p) or {}).get("_mai_meta", {})
        _COST[wd.name] = (mtime, meta.get("cost_usd"))
    return _COST[wd.name][1]


def _snapshot(wd: Path) -> dict:
    proc = PROCS.get(wd.name)
    running = proc is not None and proc.poll() is None
    manifest = _read_json(wd / "chunks" / "manifest.json") or []
    timings = _read_json(wd / "timings.json")
    job = _read_json(wd / "job.json") or {}
    # On re-runs every output file already exists; only a timings.json
    # written after this launch means the current run actually finished.
    finished = (
        timings is not None and not running
        and (wd / "timings.json").stat().st_mtime >= job.get("started", 0)
    )
    # Outputs are overwritten in place, which leaves the directory mtime
    # untouched — fold the files in so re-runs invalidate client caches.
    mtime = max(p.stat().st_mtime for p in [wd, *(wd / n for n in FILES)] if p.exists())
    return {
        "id": wd.name,
        "running": running,
        "finished": finished,
        "exit_code": None if running or proc is None else proc.returncode,
        "job": job,
        "mtime": mtime,
        "audio": (wd / "audio.wav").exists(),
        "chunks_done": _count(wd / "chunks", "*.wav.json"),
        "chunks_total": len(manifest),
        "asr": (wd / "asr.json").exists(),
        "merge_batches": _count(wd / "merge_cache", "*.json"),
        "ja": (wd / "out_llm_ja.srt").exists(),
        "glossary": (wd / "glossary.md").exists(),
        "glossary_user": (wd / "glossary_user.md").exists(),
        "zh_batches": _count(wd / "zh_cache", "*.json"),
        "zh": (wd / "out_zh.srt").exists(),
        "timings": timings,
        "cost_usd": _asr_cost(wd) if timings else None,
        "files": [n for n in FILES if (wd / n).exists()],
    }


def _launch(wd: Path, req: JobRequest) -> None:
    if any(p.poll() is None for p in PROCS.values()):
        raise HTTPException(409, "another job is running")
    cmd = [sys.executable, "-m", "flows.maijev.pipeline", req.input, str(wd)]
    if req.mode == "zh":
        cmd.append("--translate")
        if req.prepass:
            cmd.append("--prepass")
    elif req.mode == "ja":
        cmd.append("--llm-segment")
    if req.ocr_json:
        cmd += ["--ocr-json", req.ocr_json]
    if req.extract_frames:
        cmd.append("--extract-frames")
    if req.model:
        cmd += ["--model", req.model]

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if req.thinking_level:
        env["LLM_THINKING_LEVEL"] = req.thinking_level
    user_glossary = wd / "glossary_user.md"
    if user_glossary.exists() and user_glossary.read_text(encoding="utf-8").strip():
        env["TRANSLATE_GLOSSARY_PATH"] = str(user_glossary)

    wd.mkdir(parents=True, exist_ok=True)
    (wd / "job.json").write_text(
        json.dumps({**req.model_dump(), "started": time.time()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log = (wd / "gui.log").open("a", encoding="utf-8")
    log.write(f"\n$ {shlex.join(cmd)}\n")
    log.flush()
    PROCS[wd.name] = subprocess.Popen(
        cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# --- routes ----------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/env")
def env_status():
    def has(*keys: str) -> bool:
        return any(os.environ.get(k) for k in keys)

    if has("TYPESAFE_API_KEY"):
        jev = "typesafe"
    elif has("CLOUDFLARE_ACCOUNT_ID") and has("CLOUDFLARE_API_TOKEN"):
        jev = "cloudflare"
    else:
        jev = None
    return {
        "asr": has("OPENROUTER_API_KEY"),
        "llm": has("GEMINI_AGENT_PLATFORM_API_KEY", "AGENT_PLATFORM_API_KEY",
                   "GEMINI_API_KEY", "OPENROUTER_API_KEY"),
        "jev": jev,
        "default_model": os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL,
        "runs": str(RUNS),
    }


@app.get("/api/browse")
def browse(path: str = ""):
    p = Path(path).expanduser() if path else Path.home()
    if not p.is_dir():
        raise HTTPException(404, "not a directory")
    dirs, files = [], []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if c.name.startswith("."):
                continue
            if c.is_dir():
                dirs.append(c.name)
            elif c.suffix.lower() in MEDIA_EXT:
                files.append(c.name)
    except PermissionError:
        raise HTTPException(403, "permission denied")
    return {
        "path": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "dirs": dirs[:300],
        "files": files[:300],
    }


@app.get("/api/jobs")
def list_jobs():
    if not RUNS.is_dir():
        return []
    dirs = [
        d for d in RUNS.iterdir()
        if d.is_dir() and ((d / "job.json").exists() or (d / "audio.wav").exists())
    ]
    return sorted((_snapshot(d) for d in dirs), key=lambda s: s["mtime"], reverse=True)


@app.post("/api/jobs")
def create_job(req: JobRequest):
    base = req.name or Path(req.input.rstrip("/")).stem or "job"
    name = SAFE_NAME.sub("_", base).strip("_.")[:60] or "job"
    if not req.name and (RUNS / name).exists():
        name = f"{name}-{time.strftime('%H%M%S')}"
    wd = RUNS / name
    _launch(wd, req)
    return _snapshot(wd)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _snapshot(_work_dir(job_id))


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    proc = PROCS.get(job_id)
    if proc is None or proc.poll() is not None:
        raise HTTPException(409, "job is not running")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return _snapshot(_work_dir(job_id))


@app.post("/api/jobs/{job_id}/rerun")
def rerun_job(job_id: str):
    wd = _work_dir(job_id)
    job = _read_json(wd / "job.json")
    if not job:
        raise HTTPException(409, "job.json missing — this run was not started from the GUI")
    req = JobRequest(**{k: v for k, v in job.items() if k in JobRequest.model_fields})
    _launch(wd, req)
    return _snapshot(wd)


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    wd = _work_dir(job_id)
    log = wd / "gui.log"

    async def gen():
        # State first so the client has a panel to append the log replay to.
        snap = _snapshot(wd)
        last = json.dumps(snap, sort_keys=True)
        yield _sse({"type": "state", **snap})
        pos = 0
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-300:]:
                yield _sse({"type": "log", "line": line})
            pos = log.stat().st_size
        while True:
            snap = _snapshot(wd)
            if log.exists():
                with log.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                pos += len(chunk)
                for line in chunk.decode("utf-8", "replace").splitlines():
                    yield _sse({"type": "log", "line": line})
            key = json.dumps(snap, sort_keys=True)
            if key != last:
                yield _sse({"type": "state", **snap})
                last = key
            if not snap["running"]:
                yield _sse({"type": "end"})
                return
            await asyncio.sleep(0.7)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/files/{name}")
def get_file(job_id: str, name: str, download: bool = False):
    if name not in FILES:
        raise HTTPException(404, "unknown file")
    p = _work_dir(job_id) / name
    if not p.exists():
        raise HTTPException(404, "file not written yet")
    return FileResponse(
        p,
        media_type="text/plain; charset=utf-8",
        filename=f"{job_id}_{name}" if download else None,
    )


@app.get("/api/jobs/{job_id}/glossary")
def get_glossary(job_id: str):
    wd = _work_dir(job_id)

    def read(name: str) -> str:
        p = wd / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    return {"auto": read("glossary.md"), "user": read("glossary_user.md")}


@app.put("/api/jobs/{job_id}/glossary")
def put_glossary(job_id: str, body: GlossaryBody):
    wd = _work_dir(job_id)
    text = body.text.strip()
    (wd / "glossary_user.md").write_text(text + "\n" if text else "", encoding="utf-8")
    return {"ok": True, "lines": len([l for l in text.splitlines() if l.strip()])}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    global RUNS
    ap = argparse.ArgumentParser(description="maijev local web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--runs", type=Path, default=RUNS, help="directory holding job work_dirs")
    args = ap.parse_args()
    RUNS = args.runs.resolve()
    RUNS.mkdir(parents=True, exist_ok=True)

    import uvicorn

    print(f"maijev GUI → http://{args.host}:{args.port}   runs: {RUNS}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
