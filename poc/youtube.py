from __future__ import annotations

import glob
import json
import os
import subprocess
from pathlib import Path

from video_processor import FFMPEG, FFPROBE


def download(url: str, output_dir: str, max_duration: int = 7200) -> dict:
    """
    Download video from URL. Tries yt-dlp first, then Playwright screen recording.
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

    # Try yt-dlp download
    strategies = [
        [],
        ["--extractor-args", "youtube:player_client=web"],
        ["--extractor-args", "youtube:player_client=default,web"],
    ]

    base_cmd = [
        "yt-dlp",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv+ba/b",
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-playlist", "--no-overwrites",
    ]

    for extra in strategies:
        r = subprocess.run(base_cmd + extra + [url], capture_output=True, text=True)
        if r.returncode == 0:
            break

    files = list(Path(output_dir).glob("source.*"))
    if files:
        return {"path": str(files[0]), "title": title, "duration": duration, "width": width, "height": height}

    # Fallback: Playwright screen recording
    return _record_with_playwright(url, output_dir, title, duration, max_duration)


def _record_with_playwright(url: str, output_dir: str, title: str, known_duration: float, max_duration: int) -> dict:
    """Record the video by playing it in a headless browser."""
    from playwright.sync_api import sync_playwright

    rec_dir = os.path.join(output_dir, "_recording")
    os.makedirs(rec_dir, exist_ok=True)
    output_mp4 = os.path.join(output_dir, "source.mp4")

    # Limit recording time
    record_seconds = min(known_duration or 300, max_duration, 600)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            record_video_dir=rec_dir,
            record_video_size={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.goto(url, timeout=30000)

        # Dismiss cookie banners
        for sel in ['button:has-text("Accept")', 'button:has-text("Agree")', '[aria-label="Accept"]']:
            try:
                page.click(sel, timeout=2000)
            except:
                pass

        # Enter fullscreen and play
        page.wait_for_timeout(2000)
        page.evaluate("""() => {
            const v = document.querySelector('video');
            if (v) {
                v.muted = false;
                v.currentTime = 0;
                v.play();
                try { v.requestFullscreen(); } catch(e) {}
            }
        }""")

        # Hide UI overlays (YouTube specific)
        page.evaluate("""() => {
            const style = document.createElement('style');
            style.textContent = `
                .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-top,
                .ytp-gradient-bottom, .ytp-pause-overlay, .ytp-watermark,
                .ytp-ce-element, .iv-branding, .branding-img-container,
                .ytp-cards-button-icon, .annotation { display: none !important; }
            `;
            document.head.appendChild(style);
        }""")

        # Wait for the video to finish (or hit time limit)
        wait_ms = int(record_seconds * 1000) + 5000
        page.wait_for_timeout(min(wait_ms, 600000))

        page.close()
        ctx.close()
        browser.close()

    # Find recorded webm and convert to mp4
    recordings = glob.glob(os.path.join(rec_dir, "*.webm"))
    if not recordings:
        raise RuntimeError("Playwright recording produced no file")

    webm_path = recordings[0]
    subprocess.run(
        [FFMPEG, "-y", "-i", webm_path,
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart",
         output_mp4],
        capture_output=True, check=True,
    )

    # Get actual duration from the output
    probe = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", output_mp4],
        capture_output=True, text=True,
    )
    actual_duration = float(probe.stdout.strip()) if probe.returncode == 0 else known_duration

    # Cleanup recording
    try:
        os.remove(webm_path)
        os.rmdir(rec_dir)
    except:
        pass

    return {
        "path": output_mp4,
        "title": title,
        "duration": actual_duration,
        "width": 1920,
        "height": 1080,
    }
