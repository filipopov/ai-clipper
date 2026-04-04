from __future__ import annotations

import os
import subprocess
from pathlib import Path
from video_processor import FFMPEG, FFPROBE


def generate_candidates(video_path: str, output_dir: str, count: int = 6) -> list:
    """Extract evenly spaced frames as thumbnail candidates."""
    probe = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, check=True,
    )
    duration = float(probe.stdout.strip())
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    candidates = []
    for i in range(count):
        t = duration * (i + 0.5) / count
        out = os.path.join(output_dir, f"thumb_{i:02d}.jpg")
        subprocess.run(
            [FFMPEG, "-y", "-ss", str(t), "-i", video_path,
             "-vframes", "1", "-q:v", "2",
             "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
             out],
            capture_output=True, check=True,
        )
        candidates.append({"path": out, "time": round(t, 2), "index": i})

    return candidates


def generate_with_text(
    video_path: str,
    output_path: str,
    text: str,
    time_offset: float = 0,
    font_size: int = 64,
) -> str:
    """Extract a frame and overlay text as a thumbnail."""
    escaped = text.replace("'", "'\\''").replace(":", "\\:").replace("\\", "\\\\")
    subprocess.run(
        [FFMPEG, "-y", "-ss", str(time_offset), "-i", video_path,
         "-vframes", "1", "-q:v", "2",
         "-vf", (
             f"scale=1080:1920:force_original_aspect_ratio=decrease,"
             f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
             f"drawtext=text='{escaped}':"
             f"fontsize={font_size}:fontcolor=white:borderw=3:bordercolor=black:"
             f"x=(w-text_w)/2:y=h*0.15:font=Arial"
         ),
         output_path],
        capture_output=True, check=True,
    )
    return output_path
