from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import costs
import database as db
from auth import router as auth_router, get_current_user
from analyzer import analyze_streaming, smart_max_clips, DEFAULT_MAX_CLIPS
from caption_generator import generate_ass, STYLES
from face_detector import detect_faces_in_segment
from srt_parser import parse_srt, to_transcript, total_duration
from transcriber import extract_audio, get_video_info, transcribe
from thumbnail import generate_candidates as gen_thumb_candidates, generate_with_text as gen_thumb_text
from video_processor import burn_captions, cut_clip, generate_thumbnail, reformat_vertical
from youtube import download as yt_download

app = FastAPI(title="Cliphook")
app.include_router(auth_router)

BASE_DIR = Path(__file__).parent
WORK_DIR = Path(os.environ.get("WORK_DIR", BASE_DIR / "workdir"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def sse(event: str, data) -> str:
    if isinstance(data, (dict, list)):
        data = json.dumps(data)
    return f"event: {event}\ndata: {data}\n\n"


def _render_single_clip(
    video_path: str, job_id: str, job_dir: Path,
    seg: dict, clip_index: int, words: list,
    caption_style: str,
) -> dict:
    """Render one clip: face detect → cut → reformat → captions → burn → thumb."""
    clip_id = f"clip_{clip_index:02d}"
    clip_dir = job_dir / "clips" / clip_id
    clip_dir.mkdir(parents=True, exist_ok=True)

    start = seg["start_time"]
    end = seg["end_time"]

    # Per-clip face detection
    face_info = detect_faces_in_segment(video_path, start, end, sample_count=4)

    raw_path = str(clip_dir / "raw.mp4")
    cut_clip(video_path, start, end, raw_path)

    vertical_path = str(clip_dir / "vertical.mp4")
    reformat_vertical(
        raw_path, vertical_path,
        face_x=face_info["face_x"],
        crop_width_ratio=face_info["crop_width_ratio"],
    )

    ass_content = generate_ass(words, start, end, style=caption_style)
    ass_path = str(clip_dir / "captions.ass")
    with open(ass_path, "w") as f:
        f.write(ass_content)

    final_path = str(clip_dir / "final.mp4")
    burn_captions(vertical_path, ass_path, final_path)

    thumb_path = str(clip_dir / "thumb.jpg")
    generate_thumbnail(vertical_path, thumb_path, (end - start) / 2)

    return {
        **seg,
        "clip_id": clip_id,
        "clip_index": clip_index,
        "multi_speaker": face_info["multi_speaker"],
        "face_count": face_info["face_count"],
        "video_url": f"/workdir/{job_id}/clips/{clip_id}/final.mp4",
        "thumb_url": f"/workdir/{job_id}/clips/{clip_id}/thumb.jpg",
        "download_url": f"/workdir/{job_id}/clips/{clip_id}/final.mp4",
    }


# ── Main analysis endpoint (file upload) ──────────────────────────────

@app.post("/api/analyze")
async def analyze_endpoint(
    request: Request,
    file: UploadFile = File(...),
    user_intent: str = Form(default=""),
    caption_style: str = Form(default="bold"),
    clip_length: str = Form(default="short"),
    max_clips: int = Form(default=DEFAULT_MAX_CLIPS),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)
    db.increment_user_analyses(user["id"])

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_srt = ext == ".srt"

    if not is_video and not is_srt:
        return StreamingResponse(iter([sse("error", f"Unsupported file type: {ext}")]), media_type="text/event-stream")

    job_id = str(uuid.uuid4())[:8]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    source_path = str(job_dir / f"source{ext}")
    with open(source_path, "wb") as f:
        f.write(await file.read())

    source_type = "video" if is_video else "srt"
    db.create_job(job_id, source_type, filename, user_intent, caption_style=caption_style)

    if is_srt:
        return StreamingResponse(
            _process_srt(source_path, job_id, job_dir, user_intent, clip_length, max_clips),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _process_video(source_path, job_id, job_dir, user_intent, caption_style, clip_length, max_clips),
        media_type="text/event-stream",
    )


# ── YouTube URL endpoint ──────────────────────────────────────────────

@app.post("/api/analyze-url")
async def analyze_url_endpoint(
    request: Request,
    url: str = Form(...),
    user_intent: str = Form(default=""),
    caption_style: str = Form(default="bold"),
    clip_length: str = Form(default="short"),
    max_clips: int = Form(default=DEFAULT_MAX_CLIPS),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)
    db.increment_user_analyses(user["id"])

    job_id = str(uuid.uuid4())[:8]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    db.create_job(job_id, "youtube", url, user_intent, youtube_url=url, caption_style=caption_style)

    def generate():
        yield sse("pipeline", {"stage": "probe", "message": "Downloading video from YouTube..."})
        try:
            info = yt_download(url, str(job_dir))
        except Exception as e:
            db.update_job(job_id, status="failed", error=str(e))
            yield sse("error", f"YouTube download failed: {e}")
            return
        yield sse("status", f"Downloaded: {info['title']} ({info['duration']:.0f}s)")
        yield from _process_video(info["path"], job_id, job_dir, user_intent, caption_style, clip_length, max_clips)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Re-render endpoint (clip editor) ─────────────────────────────────

@app.post("/api/jobs/{job_id}/clips/{clip_id}/rerender")
async def rerender_clip(
    job_id: str,
    clip_id: str,
    start_time: float = Form(...),
    end_time: float = Form(...),
    caption_style: str = Form(default="bold"),
):
    job = db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    job_dir = WORK_DIR / job_id
    source_files = list(job_dir.glob("source.*"))
    if not source_files:
        return JSONResponse({"error": "Source video not found"}, status_code=404)

    video_path = str(source_files[0])

    # Load word timestamps from transcript
    import pickle
    words_path = job_dir / "words.json"
    words = []
    if words_path.exists():
        with open(words_path) as f:
            words = json.load(f)

    seg = {
        "start_time": start_time,
        "end_time": end_time,
        "suggested_title": "Re-rendered clip",
        "hook_text": "",
        "topic": "",
        "virality_score": 0,
        "suggested_hashtags": [],
    }

    # Find clip index from clip_id
    idx = int(clip_id.replace("clip_", "")) if clip_id.startswith("clip_") else 0

    try:
        clip_info = _render_single_clip(video_path, job_id, job_dir, seg, idx, words, caption_style)
        return JSONResponse(clip_info)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Dashboard API ─────────────────────────────────────────────────────

@app.get("/api/jobs")
async def list_jobs(request: Request):
    if not get_current_user(request):
        return JSONResponse({"error": "auth_required"}, status_code=401)
    return JSONResponse(db.list_jobs())


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    job["clips"] = db.get_clips(job_id)
    return JSONResponse(job)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    db.delete_job(job_id)
    import shutil
    job_dir = WORK_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    return JSONResponse({"ok": True})


@app.get("/api/styles")
async def list_styles():
    return JSONResponse(list(STYLES.keys()))


@app.get("/api/usage")
async def usage_stats():
    return JSONResponse(db.get_usage_stats())


@app.post("/api/jobs/{job_id}/clips/{clip_id}/thumbnails")
async def generate_thumbnails(job_id: str, clip_id: str):
    """Generate thumbnail candidates for a clip."""
    clip_dir = WORK_DIR / job_id / "clips" / clip_id
    final = clip_dir / "final.mp4"
    if not final.exists():
        return JSONResponse({"error": "Clip not found"}, status_code=404)
    thumb_dir = str(clip_dir / "thumbs")
    candidates = gen_thumb_candidates(str(final), thumb_dir)
    for c in candidates:
        c["url"] = f"/workdir/{job_id}/clips/{clip_id}/thumbs/{Path(c['path']).name}"
    return JSONResponse(candidates)


@app.post("/api/jobs/{job_id}/clips/{clip_id}/thumbnail-text")
async def generate_thumbnail_with_text(
    job_id: str, clip_id: str,
    text: str = Form(...),
    time_offset: float = Form(default=0),
):
    """Generate a thumbnail with text overlay."""
    clip_dir = WORK_DIR / job_id / "clips" / clip_id
    final = clip_dir / "final.mp4"
    if not final.exists():
        return JSONResponse({"error": "Clip not found"}, status_code=404)
    out = str(clip_dir / "thumb_custom.jpg")
    gen_thumb_text(str(final), out, text, time_offset)
    return JSONResponse({"url": f"/workdir/{job_id}/clips/{clip_id}/thumb_custom.jpg"})


# ── Processing pipelines ──────────────────────────────────────────────

def _process_srt(srt_path: str, job_id: str, job_dir: Path, user_intent: str,
                 clip_length: str = "short", max_clips: int = 10):
    yield sse("status", "Parsing SRT file...")
    db.update_job(job_id, status="analyzing")

    with open(srt_path) as f:
        entries = parse_srt(f.read())
    if not entries:
        db.update_job(job_id, status="failed", error="Could not parse SRT")
        yield sse("error", "Could not parse SRT file")
        return

    transcript = to_transcript(entries)
    duration = total_duration(entries)
    db.update_job(job_id, duration_seconds=duration)

    effective_max = smart_max_clips(max_clips, duration, clip_length)
    yield sse("status", f"Parsed {len(entries)} entries ({duration:.0f}s) — targeting {effective_max} clips")

    seg_count = 0
    for event_type, data in analyze_streaming(transcript, duration, user_intent, clip_length, effective_max):
        if event_type == "segment":
            seg_count += 1
            db.add_clip(job_id, f"{job_id}_s{seg_count}", seg_count, data)
            # SRT mode: emit segments immediately (no rendering)
            yield sse("segment", data)
        elif event_type == "done":
            c_cost = costs.claude_cost(data.get("modelUsed", ""), data.get("inputTokens", 0), data.get("outputTokens", 0))
            data["claude_cost"] = costs.format_cost(c_cost)
            data["total_cost"] = costs.format_cost(c_cost)
            db.update_job(job_id, status="completed", segment_count=seg_count,
                          ai_model=data.get("modelUsed"), ai_elapsed=data.get("elapsed"),
                          input_tokens=data.get("inputTokens"), output_tokens=data.get("outputTokens"),
                          claude_cost_usd=c_cost, total_cost_usd=c_cost)
            yield sse(event_type, data)
        elif event_type == "status":
            yield sse(event_type, data)
        elif event_type == "error":
            yield sse(event_type, data)


def _process_video(video_path: str, job_id: str, job_dir: Path, user_intent: str,
                   caption_style: str = "bold", clip_length: str = "short", max_clips: int = 10):
    # Step 1: Probe
    yield sse("pipeline", {"stage": "probe", "message": "Reading video metadata..."})
    db.update_job(job_id, status="probing")
    try:
        info = get_video_info(video_path)
    except Exception as e:
        db.update_job(job_id, status="failed", error=str(e))
        yield sse("error", f"Failed to read video: {e}")
        return
    db.update_job(job_id, duration_seconds=info["duration"])

    effective_max = smart_max_clips(max_clips, info["duration"], clip_length)
    yield sse("status", f"Video: {info['width']}x{info['height']}, {info['duration']:.0f}s — targeting {effective_max} clips")

    # Step 2: Extract audio
    yield sse("pipeline", {"stage": "audio", "message": "Extracting audio..."})
    db.update_job(job_id, status="extracting_audio")
    audio_path = str(job_dir / "audio.wav")
    try:
        extract_audio(video_path, audio_path)
    except Exception as e:
        db.update_job(job_id, status="failed", error=str(e))
        yield sse("error", f"Audio extraction failed: {e}")
        return

    # Step 3: Transcribe
    yield sse("pipeline", {"stage": "transcribe", "message": "Transcribing audio (Whisper)..."})
    db.update_job(job_id, status="transcribing")
    try:
        transcript_data = transcribe(audio_path)
    except Exception as e:
        db.update_job(job_id, status="failed", error=str(e))
        yield sse("error", f"Transcription failed: {e}")
        return

    words = transcript_data.get("words", [])
    w_cost = costs.whisper_cost(info["duration"])
    db.update_job(job_id, whisper_audio_seconds=info["duration"], whisper_cost_usd=w_cost)
    yield sse("status", f"Transcribed: {len(words)} words (Whisper: {costs.format_cost(w_cost)})")

    # Save words for re-render endpoint
    with open(str(job_dir / "words.json"), "w") as f:
        json.dump(words, f)

    transcript_text = "\n".join(
        f"[{s['start']:.0f}-{s['end']:.0f}] {s['text']}" for s in transcript_data.get("segments", [])
    )

    # Step 4: AI analysis — collect segments silently (no UI emission yet)
    yield sse("pipeline", {"stage": "analyze", "message": "Finding viral segments (Claude)..."})
    db.update_job(job_id, status="analyzing")
    segments = []
    analysis_meta = {}

    for event_type, data in analyze_streaming(transcript_text, info["duration"], user_intent, clip_length, effective_max):
        if event_type == "segment":
            segments.append(data)
        elif event_type == "done":
            analysis_meta = data
        elif event_type == "error":
            db.update_job(job_id, status="failed", error=data)
            yield sse("error", data)
            return
        elif event_type == "status":
            yield sse("status", data)

    if not segments:
        db.update_job(job_id, status="completed", segment_count=0)
        yield sse("done", {**analysis_meta, "clips": []})
        return

    # Step 5: Render clips — emit each only when fully rendered
    yield sse("pipeline", {"stage": "render", "message": f"Rendering {len(segments)} clips with face tracking..."})
    db.update_job(job_id, status="rendering")
    (job_dir / "clips").mkdir(exist_ok=True)
    rendered_clips = []

    for i, seg in enumerate(segments):
        yield sse("status", f"Rendering clip {i + 1}/{len(segments)}: {seg.get('suggested_title', '')}")
        try:
            clip_info = _render_single_clip(video_path, job_id, job_dir, seg, i, words, caption_style)
            clip_info["job_id"] = job_id
            rendered_clips.append(clip_info)
            db.add_clip(job_id, f"{job_id}_{clip_info['clip_id']}", i, clip_info)
            yield sse("clip_ready", clip_info)
        except Exception as e:
            yield sse("status", f"Failed to render clip {i + 1}: {e}")

    # Costs
    c_cost = costs.claude_cost(
        analysis_meta.get("modelUsed", ""), analysis_meta.get("inputTokens", 0), analysis_meta.get("outputTokens", 0)
    )
    total = c_cost + w_cost
    analysis_meta["claude_cost"] = costs.format_cost(c_cost)
    analysis_meta["whisper_cost"] = costs.format_cost(w_cost)
    analysis_meta["total_cost"] = costs.format_cost(total)

    db.update_job(
        job_id, status="completed", segment_count=len(segments), clip_count=len(rendered_clips),
        ai_model=analysis_meta.get("modelUsed"), ai_elapsed=analysis_meta.get("elapsed"),
        input_tokens=analysis_meta.get("inputTokens"), output_tokens=analysis_meta.get("outputTokens"),
        claude_cost_usd=c_cost, total_cost_usd=total,
    )
    yield sse("done", {**analysis_meta, "clips": rendered_clips, "jobId": job_id})


# ── Static file serving ───────────────────────────────────────────────

app.mount("/workdir", StaticFiles(directory=str(WORK_DIR)), name="workdir")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
