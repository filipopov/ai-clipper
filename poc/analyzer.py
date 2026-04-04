from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import anthropic

MODEL = os.getenv("CLIPENGINE_AI_MODEL", "claude-sonnet-4-6")
TEMPERATURE = float(os.getenv("CLIPENGINE_AI_TEMPERATURE", "0.3"))
DEFAULT_MAX_CLIPS = int(os.getenv("CLIPENGINE_AI_MAX_SEGMENTS", "10"))


def smart_max_clips(requested: int, video_duration: float, clip_length: str = "short") -> int:
    """Cap requested clips to something reasonable for the video length."""
    _, max_s, _ = CLIP_LENGTHS.get(clip_length, CLIP_LENGTHS["short"])
    # Can't have more clips than the video can fit (with some overlap tolerance)
    max_possible = max(1, int(video_duration / (max_s * 0.6)))
    return min(requested, max_possible)

client = anthropic.Anthropic()

# clip_length → (min_seconds, max_seconds, description for prompt)
CLIP_LENGTHS = {
    "short": (15, 35, "15-35 seconds — punchy, single-moment clips"),
    "medium": (30, 60, "30-60 seconds — one complete idea or story beat"),
}


def build_system_prompt(clip_length: str) -> str:
    _, _, desc = CLIP_LENGTHS.get(clip_length, CLIP_LENGTHS["short"])
    return (
        "You are a viral content analyst for a short-form video clipping tool. "
        f"Find segments that are {desc}. "
        "Each segment MUST be a self-contained moment that works on its own. "
        "Return ONLY a raw JSON array. No markdown, no code fences, no explanation."
    )


def build_user_prompt(
    transcript: str,
    duration: float,
    user_intent: Optional[str] = None,
    clip_length: str = "short",
    max_clips: int = 10,
) -> str:
    min_s, max_s, _ = CLIP_LENGTHS.get(clip_length, CLIP_LENGTHS["short"])

    intent_line = ""
    if user_intent and user_intent.strip():
        intent_line = f'\nUser\'s focus: "{user_intent.strip()}"\nPrioritize segments matching this description.\n'

    return (
        f"Video duration: {duration:.0f}s. Find up to {max_clips} viral segments "
        f"(score 50+, sorted by virality_score desc).\n"
        f"IMPORTANT: Each segment MUST be between {min_s} and {max_s} seconds long. "
        f"Do NOT return segments longer than {max_s}s or shorter than {min_s}s.\n"
        f"{intent_line}\n"
        f"JSON format per segment:\n"
        f'{{"start_time":seconds,"end_time":seconds,"virality_score":0-100,'
        f'"suggested_title":"punchy title under 60 chars",'
        f'"hook_text":"the opening line that grabs attention",'
        f'"topic":"what this segment is about",'
        f'"suggested_hashtags":["#tag1","#tag2","#tag3"]}}\n\n'
        f"Transcript:\n{transcript}"
    )


def extract_segments(text: str) -> Optional[list]:
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    try:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except json.JSONDecodeError:
        pass

    try:
        last = text.rfind("},")
        if last > 0:
            candidate = text[: last + 1] + "]"
            m = re.search(r"\[.*\]", candidate, re.DOTALL)
            if m:
                return json.loads(m.group())
    except json.JSONDecodeError:
        pass

    return None


def analyze_streaming(
    transcript: str,
    duration: float,
    user_intent: Optional[str] = None,
    clip_length: str = "short",
    max_clips: int = 10,
):
    """
    Generator that yields (event_type, data) tuples.
    event_type: "status", "segment", "done", "error"
    """
    max_clips = smart_max_clips(max_clips, duration, clip_length)
    min_s, max_s, _ = CLIP_LENGTHS.get(clip_length, CLIP_LENGTHS["short"])
    yield ("status", f"Analyzing for up to {max_clips} {clip_length} clips ({min_s}-{max_s}s)...")

    t0 = time.monotonic()
    full_text = ""
    emitted = 0

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            temperature=TEMPERATURE,
            system=build_system_prompt(clip_length),
            messages=[{"role": "user", "content": build_user_prompt(transcript, duration, user_intent, clip_length, max_clips)}],
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                segments = extract_segments(full_text)
                if segments and len(segments) > emitted:
                    for i in range(emitted, len(segments)):
                        yield ("segment", segments[i])
                    emitted = len(segments)

        elapsed = time.monotonic() - t0
        segments = extract_segments(full_text) or []

        # Post-process: enforce duration limits, clamp if needed
        for seg in segments:
            seg_dur = seg.get("end_time", 0) - seg.get("start_time", 0)
            if seg_dur > max_s:
                seg["end_time"] = seg["start_time"] + max_s

        for i in range(emitted, len(segments)):
            yield ("segment", segments[i])

        usage = stream.get_final_message().usage
        yield ("done", {
            "modelUsed": MODEL,
            "inputTokens": usage.input_tokens,
            "outputTokens": usage.output_tokens,
            "segmentCount": len(segments),
            "elapsed": round(elapsed, 2),
        })

    except Exception as e:
        yield ("error", str(e))
