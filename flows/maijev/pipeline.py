"""End-to-end MAI-Transcribe-2 pipeline: video/audio → SRT.

    python -m flows.maijev.pipeline <input> <work_dir> [--srt out.srt]
        [--llm-segment] [--translate]

Stages (all resumable — chunk JSONs and wav slices are cached):
    1. extract   input → work_dir/audio.wav (16kHz mono PCM)
    2. chunk     silence-aligned ~5min slices → work_dir/chunks/
    3. transcribe per-chunk API calls → chunks/chunk_XX.wav.json
    4. merge     → work_dir/asr.json (ElevenLabs-shaped payload)
    5. srt       → work_dir/out.srt via services.elevenlabs.srt_builder
    6. llm       (optional) LLM merge → out_llm_ja.srt;
                 --translate adds zh translation → out_zh.srt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Allow running as `python -m flows.maijev.pipeline` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.elevenlabs.srt_builder import (  # noqa: E402
    SrtFormatOptions,
    _build_utterances,
    _extract_tokens,
    convert_payload_to_srt,
)

from .chunker import (  # noqa: E402
    extract_audio, make_chunks, probe_duration, write_manifest,
)
from .merge import merge_chunks  # noqa: E402
from .transcriber import transcribe_chunk  # noqa: E402
from .jev import JevClient, load_ocr_items, render_context  # noqa: E402
from .frames import prepare_reference_frames  # noqa: E402


def run(
    input_path: Path | str,
    work_dir: Path,
    srt_path: Path | None = None,
    phrases: list[str] | None = None,
    llm_segment: bool = False,
    translate: bool = False,
    ocr_json: Path | None = None,
    extract_frames: bool = False,
    prepass: bool = False,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    run_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    # 0. resolve input: a missing local path is treated as a remote source
    #    (Bilibili BV… / TVer ep… / Abema 90-…_s…_p… / YouTube v=… or URL)
    #    and downloaded via yt-dlp; TVer/Abema cast metadata is collected
    #    here for the pre-pass.
    input_path = Path(input_path)
    talents: list[str] = []
    if not input_path.exists():
        from .source import download_video, fetch_talents, resolve_source

        source = resolve_source(str(input_path))
        dl_t0 = time.perf_counter()
        input_path = download_video(source, work_dir / "download")
        timings["download_seconds"] = time.perf_counter() - dl_t0
        talents = fetch_talents(source)
        (work_dir / "source_meta.json").write_text(
            json.dumps(
                {
                    "video_id": source.video_id,
                    "platform": source.platform,
                    "url": source.url,
                    "talents": talents,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    wav_path = work_dir / "audio.wav"
    asr_path = work_dir / "asr.json"
    srt_path = srt_path or work_dir / "out.srt"
    result_path = srt_path

    # 1. extract
    asr_t0 = time.perf_counter()
    if not wav_path.exists():
        extract_audio(input_path, wav_path)
    total = probe_duration(wav_path)
    logger.info(f"Audio duration: {total:.1f}s")

    # 2. chunk
    chunks_dir = work_dir / "chunks"
    chunks = make_chunks(wav_path, chunks_dir, total)
    write_manifest(chunks, chunks_dir / "manifest.json")

    # 3. transcribe (cached per chunk)
    responses = []
    for chunk in chunks:
        t0 = time.time()
        resp = transcribe_chunk(chunk.path, chunk.cache_path, phrases)
        logger.info(
            f"chunk {chunk.index} [{chunk.start:.0f}-{chunk.end:.0f}s]: "
            f"{time.time() - t0:.0f}s, "
            f"{len(resp.get('words') or [])} words"
        )
        responses.append(resp)

    # 4. merge into ElevenLabs-shaped payload
    payload = merge_chunks(chunks, responses)
    asr_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta = payload["_mai_meta"]
    logger.info(
        f"Merged {meta['chunks']} chunks: {meta['audio_seconds']:.0f}s audio, "
        f"${meta['cost_usd']:.4f} → {asr_path}"
    )
    timings["asr_seconds"] = time.perf_counter() - asr_t0

    # Keep the deterministic baseline unchanged; the no-hard-punctuation
    # policy applies only to the LLM candidate segmentation below.
    srt_path.write_text(convert_payload_to_srt(payload), encoding="utf-8")
    logger.success(f"SRT written: {srt_path}")
    if extract_frames:
        frames, manifest_path = prepare_reference_frames(
            input_path,
            work_dir / "reference_frames",
        )
        logger.info(
            f"Reference frames: {len(frames)} frames → {manifest_path}"
        )
    ocr_items: list = []
    jev_results: list | None = None
    if ocr_json is not None:
        ocr_items = load_ocr_items(ocr_json)
        try:
            jev = JevClient.from_env()
        except RuntimeError:
            logger.info(
                "No Jev credentials (TYPESAFE_API_KEY or Cloudflare) — "
                "OCR items go to the pre-pass unfiltered"
            )
        else:
            jev_results = jev.classify(
                ocr_items,
                cache_dir=work_dir / "jev_cache",
            )
            ocr_context = render_context(ocr_items, jev_results)
            (work_dir / "ocr_classification.json").write_text(
                json.dumps(jev_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (work_dir / "ocr_context.txt").write_text(
                ocr_context,
                encoding="utf-8",
            )
            logger.info(
                f"Jev OCR context: {len(ocr_items)} observations, "
                f"{len(ocr_context.splitlines()) if ocr_context else 0} "
                "lines kept"
            )

    # 6. optional LLM stages: merge segmentation + zh translation
    if llm_segment or translate:
        from .prepass import run_prepass
        from .segment_llm import MergedLine, build_atoms, merge_utterances
        from .translate_llm import render_srt, translate_lines

        # Deterministic word-level atoms; the LLM only merges them.
        split_t0 = time.perf_counter()
        atoms = build_atoms(payload)
        timings["split_seconds"] = time.perf_counter() - split_t0
        logger.info(f"{len(atoms)} atoms for LLM stage")

        if llm_segment:
            merge_t0 = time.perf_counter()
            lines = merge_utterances(atoms, work_dir / "merge_cache")
            timings["merge_seconds"] = time.perf_counter() - merge_t0
        else:
            lines = [
                MergedLine(a["start"], a["end"], a["text"], [i])
                for i, a in enumerate(atoms)
            ]

        ja_srt = work_dir / "out_llm_ja.srt"
        ja_srt.write_text(
            render_srt(lines, [l.text for l in lines]), encoding="utf-8"
        )
        logger.success(f"LLM-segmented JA SRT: {ja_srt}")
        result_path = ja_srt

        if translate:
            # Shared text-only pre-pass: JEV-filtered OCR anchors + merged
            # lines -> glossary.md, injected into every translate batch.
            glossary_path = work_dir / "glossary.md"
            if ocr_json is not None or prepass or talents:
                # Cast names from source metadata are authoritative person
                # anchors — they bypass JEV and go straight to the pre-pass.
                cast_anchors = [
                    {"text": t, "kind": "person_name", "source": "cast"}
                    for t in talents
                ]
                prepass_t0 = time.perf_counter()
                glossary_path.write_text(
                    run_prepass(
                        ocr_items,
                        jev_results,
                        lines,
                        extra_anchors=cast_anchors,
                        cache_dir=work_dir / "prepass_cache",
                    )
                    + "\n",
                    encoding="utf-8",
                )
                timings["prepass_seconds"] = time.perf_counter() - prepass_t0

            translate_t0 = time.perf_counter()
            zh = translate_lines(
                lines,
                work_dir / "zh_cache",
                glossary_path=(
                    glossary_path if glossary_path.exists() else None
                ),
            )
            timings["translate_seconds"] = time.perf_counter() - translate_t0
            zh_srt = work_dir / "out_zh.srt"
            zh_srt.write_text(render_srt(lines, zh), encoding="utf-8")
            logger.success(f"ZH SRT: {zh_srt}")
            result_path = zh_srt

    timings["wall_seconds"] = time.perf_counter() - run_t0
    (work_dir / "timings.json").write_text(
        json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"Stage timings → {work_dir / 'timings.json'}: {timings}")
    return result_path


def main() -> None:
    parser = argparse.ArgumentParser(description="MAI-Transcribe-2 → SRT")
    parser.add_argument(
        "input",
        help="local media file, or a remote source: Bilibili BV… / TVer "
        "ep… / Abema 90-…_s…_p… / YouTube v=… or a full URL",
    )
    parser.add_argument("work_dir", type=Path, help="working/output directory")
    parser.add_argument("--srt", type=Path, default=None, help="SRT output path")
    parser.add_argument("--llm-segment", action="store_true",
                        help="LLM merge pass over utterances")
    parser.add_argument("--translate", action="store_true",
                        help="translate merged lines to zh (implies segment)")
    parser.add_argument(
        "--extract-frames",
        action="store_true",
        help="extract sparse reference frames for OCR/JEV context",
    )
    parser.add_argument(
        "--ocr-json",
        type=Path,
        default=None,
        help="OCR observations JSON; Jev classifies, then a shared pre-pass "
        "produces glossary.md for translation",
    )
    parser.add_argument(
        "--prepass",
        action="store_true",
        help="run the shared glossary pre-pass on subtitle text alone "
        "(no OCR input; requires --translate)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Gemini model for all LLM stages (same as GEMINI_MODEL; "
        "per-stage overrides: SEGMENT_MODEL / TRANSLATE_MODEL / PREPASS_MODEL)",
    )
    args = parser.parse_args()
    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    run(
        args.input,
        args.work_dir,
        args.srt,
        llm_segment=args.llm_segment or args.translate,
        translate=args.translate,
        ocr_json=args.ocr_json,
        extract_frames=args.extract_frames,
        prepass=args.prepass,
    )


if __name__ == "__main__":
    main()
