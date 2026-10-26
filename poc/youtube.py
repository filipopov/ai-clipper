from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from video_processor import FFMPEG, FFPROBE


def download(url: str, output_dir: str, max_duration: int = 7200) -> dict:
    """
    Download video from URL with yt-dlp.
    """
    out_template = str(Path(output_dir) / "source.%(ext)s")

    # Get info via yt-dlp (works even when download is blocked)
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True, text=True,
    )

    title = "Untitled"
    duration = 0
    width = 0
    height = 0

    if result.returncode == 0:
        info = json.loads(result.stdout)
        title = info.get("title", "Untitled")
        duration = info.get("duration") or 0
        width = info.get("width", 0)
        height = info.get("height", 0)
        if duration > max_duration:
            raise ValueError(f"Video too long: {duration}s (max {max_duration}s)")

    # Try yt-dlp download with multiple strategies
    base_cmd = [
        "yt-dlp",
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-playlist", "--no-overwrites",
    ]

    strategies = [
        # 1. Best combined format with cookies from browser
        ["-f", "b[height<=1080]/b", "--cookies-from-browser", "chrome"],
        # 2. Separate video+audio with cookies
        ["-f", "bv*[height<=1080]+ba/b[height<=1080]", "--cookies-from-browser", "chrome"],
        # 3. Best combined without cookies
        ["-f", "b[height<=1080]/b"],
        # 4. Separate streams, try different player clients
        ["-f", "bv*[height<=1080]+ba/b[height<=1080]", "--extractor-args", "youtube:player_client=tv"],
        ["-f", "bv*[height<=1080]+ba/b[height<=1080]", "--extractor-args", "youtube:player_client=web,default"],
        # 5. Any format at all
        ["-f", "best"],
    ]

    last_err = ""
    for extra in strategies:
        r = subprocess.run(base_cmd + extra + [url], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            break
        last_err = r.stderr[-500:] if r.stderr else ""

    files = [f for f in Path(output_dir).glob("source.*") if f.suffix != ".part"]
    if files:
        # Verify the file has both video and audio streams
        probe = subprocess.run(
            [FFPROBE, "-v", "quiet", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(files[0])],
            capture_output=True, text=True,
        )
        streams = probe.stdout.strip().split("\n") if probe.returncode == 0 else []
        has_video = "video" in streams
        has_audio = "audio" in streams

        if has_video and has_audio:
            return {"path": str(files[0]), "title": title, "duration": duration, "width": width, "height": height}

        if has_video and not has_audio:
            # Video-only file — try to get audio separately and mux
            audio_out = str(Path(output_dir) / "audio_only.m4a")
            ar = subprocess.run(
                ["yt-dlp", "-f", "ba", "-o", audio_out, "--no-playlist", url],
                capture_output=True, text=True, timeout=60,
            )
            if ar.returncode == 0 and Path(audio_out).exists():
                muxed = str(Path(output_dir) / "source_muxed.mp4")
                subprocess.run(
                    [FFMPEG, "-y", "-i", str(files[0]), "-i", audio_out,
                     "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", muxed],
                    capture_output=True, check=True,
                )
                os.replace(muxed, str(files[0]))
                try:
                    os.remove(audio_out)
                except OSError:
                    pass
                return {"path": str(files[0]), "title": title, "duration": duration, "width": width, "height": height}

        # File exists but may be incomplete — still return it
        return {"path": str(files[0]), "title": title, "duration": duration, "width": width, "height": height}

    raise RuntimeError(f"Download failed: {last_err}")
