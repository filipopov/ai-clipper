from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Prefer ffmpeg-full (has libass) over base ffmpeg
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
_FFPROBE_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
FFMPEG = _FFMPEG_FULL if os.path.exists(_FFMPEG_FULL) else (shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = _FFPROBE_FULL if os.path.exists(_FFPROBE_FULL) else (shutil.which("ffprobe") or "ffprobe")


def cut_clip(source: str, start: float, end: float, output: str) -> str:
    """Extract a time segment from source video."""
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(start), "-to", str(end), "-i", source,
         "-c", "copy", "-avoid_negative_ts", "make_zero", output],
        capture_output=True, check=True,
    )
    return output


def reformat_vertical(
    input_path: str,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    face_x: float = 0.5,
    crop_width_ratio: float = 0.5625,  # 9/16
) -> str:
    """
    Crop and scale to 9:16 vertical format.
    face_x: horizontal face center (0-1). crop_width_ratio: how wide to crop relative to source height.
    For single speaker: 9/16 = 0.5625. For multi-speaker: wider (0.7-1.0).
    """
    probe = subprocess.run(
        [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", input_path],
        capture_output=True, text=True, check=True,
    )
    src_w, src_h = [int(x) for x in probe.stdout.strip().split(",")]
    src_ratio = src_w / src_h
    target_ratio = 9 / 16

    if src_ratio > target_ratio:
        crop_w = min(src_w, int(src_h * crop_width_ratio))
        max_offset = src_w - crop_w
        offset_x = int(face_x * src_w - crop_w / 2)
        offset_x = max(0, min(offset_x, max_offset))
        vf = f"crop={crop_w}:{src_h}:{offset_x}:0,scale={width}:{height}:flags=lanczos,setsar=1"
    elif src_ratio < target_ratio:
        vf = f"crop=iw:iw*16/9,scale={width}:{height}:flags=lanczos,setsar=1"
    else:
        vf = f"scale={width}:{height}:flags=lanczos,setsar=1"

    subprocess.run(
        [FFMPEG, "-y", "-i", input_path, "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


def burn_captions(input_path: str, ass_path: str, output_path: str) -> str:
    """Burn ASS subtitles into video."""
    # Need to escape path for ass filter
    escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    subprocess.run(
        [FFMPEG, "-y", "-i", input_path,
         "-vf", f"ass={escaped}",
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-c:a", "copy",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


def generate_thumbnail(video_path: str, output_path: str, time_offset: float = 0) -> str:
    """Extract a single frame as JPEG thumbnail."""
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(time_offset), "-i", video_path,
         "-vframes", "1", "-q:v", "3", output_path],
        capture_output=True, check=True,
    )
    return output_path
