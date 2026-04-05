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

# "ultrafast" on Railway, "fast" locally — same quality, smaller files with "fast"
PRESET = os.getenv("FFMPEG_PRESET", "fast")


def cut_clip(source: str, start: float, end: float, output: str) -> str:
    """Extract a time segment from source video."""
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(start), "-to", str(end), "-i", source,
         "-c", "copy", "-avoid_negative_ts", "make_zero", output],
        capture_output=True, check=True,
    )
    return output


def _build_vertical_vf(
    input_path: str,
    width: int = 1080,
    height: int = 1920,
    face_x: float = 0.5,
    crop_width_ratio: float = 0.5625,
) -> str:
    """Build the crop+scale filter string for vertical reformat."""
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
        return f"crop={crop_w}:{src_h}:{offset_x}:0,scale={width}:{height}:flags=lanczos,setsar=1"
    elif src_ratio < target_ratio:
        return f"crop=iw:iw*16/9,scale={width}:{height}:flags=lanczos,setsar=1"
    else:
        return f"scale={width}:{height}:flags=lanczos,setsar=1"


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
    vf = _build_vertical_vf(input_path, width, height, face_x, crop_width_ratio)
    subprocess.run(
        [FFMPEG, "-y", "-threads", "0", "-i", input_path, "-vf", vf,
         "-c:v", "libx264", "-preset", PRESET, "-crf", "23",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


def burn_captions(input_path: str, ass_path: str, output_path: str) -> str:
    """Burn ASS subtitles into video."""
    escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    subprocess.run(
        [FFMPEG, "-y", "-threads", "0", "-i", input_path,
         "-vf", f"ass={escaped}",
         "-c:v", "libx264", "-preset", PRESET, "-crf", "23",
         "-c:a", "copy",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         output_path],
        capture_output=True, check=True,
    )
    return output_path


def reformat_and_burn(
    input_path: str,
    ass_path: str,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    face_x: float = 0.5,
    crop_width_ratio: float = 0.5625,
) -> str:
    """Crop to vertical + burn captions in a single encode pass."""
    vf = _build_vertical_vf(input_path, width, height, face_x, crop_width_ratio)
    escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf_combined = f"{vf},ass={escaped}"
    subprocess.run(
        [FFMPEG, "-y", "-threads", "0", "-i", input_path, "-vf", vf_combined,
         "-c:v", "libx264", "-preset", PRESET, "-crf", "23",
         "-c:a", "aac", "-b:a", "128k",
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
