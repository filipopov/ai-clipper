from __future__ import annotations

import os
import subprocess
import json
import tempfile
from pathlib import Path

from openai import OpenAI
from video_processor import FFMPEG, FFPROBE


def extract_audio(video_path: str, output_path: str) -> str:
    """Extract audio from video as mono 16kHz OGG/Opus (much smaller than WAV, same Whisper quality)."""
    subprocess.run(
        [FFMPEG, "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1",
         "-c:a", "libopus", "-b:a", "32k", output_path],
        capture_output=True, check=True,
    )
    return output_path


def get_video_info(video_path: str) -> dict:
    """Get video metadata via ffprobe."""
    result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    duration = float(info["format"].get("duration", 0))
    video_stream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    width = int(video_stream["width"]) if video_stream else 0
    height = int(video_stream["height"]) if video_stream else 0
    return {"duration": duration, "width": width, "height": height}


def transcribe(audio_path: str) -> dict:
    """Transcribe audio using OpenAI Whisper API. Returns segments + words with timestamps."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Whisper API has 25MB limit — chunk if needed (Opus files are small, but check anyway)
    file_size = Path(audio_path).stat().st_size
    if file_size > 24 * 1024 * 1024:
        return _transcribe_chunked(client, audio_path)

    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    return {
        "text": result.text,
        "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in (result.segments or [])],
        "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (result.words or [])],
    }


def _transcribe_chunked(client: OpenAI, audio_path: str, chunk_minutes: int = 10) -> dict:
    """Split audio into chunks and transcribe each, then merge."""
    info_result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration", "-of", "json", audio_path],
        capture_output=True, text=True, check=True,
    )
    total_duration = float(json.loads(info_result.stdout)["format"]["duration"])
    chunk_seconds = chunk_minutes * 60
    overlap = 30  # seconds

    all_segments = []
    all_words = []
    all_text = []

    with tempfile.TemporaryDirectory() as tmpdir:
        start = 0.0
        chunk_idx = 0
        while start < total_duration:
            chunk_path = os.path.join(tmpdir, f"chunk_{chunk_idx}.ogg")
            subprocess.run(
                [FFMPEG, "-y", "-i", audio_path, "-ss", str(start), "-t", str(chunk_seconds + overlap),
                 "-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", chunk_path],
                capture_output=True, check=True,
            )

            with open(chunk_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["word", "segment"],
                )

            # Offset timestamps by chunk start
            for s in (result.segments or []):
                all_segments.append({"start": s.start + start, "end": s.end + start, "text": s.text})
            for w in (result.words or []):
                all_words.append({"word": w.word, "start": w.start + start, "end": w.end + start})
            all_text.append(result.text)

            start += chunk_seconds
            chunk_idx += 1

    return {"text": " ".join(all_text), "segments": all_segments, "words": all_words}
