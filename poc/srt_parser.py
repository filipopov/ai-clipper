from __future__ import annotations

import re

SRT_TIMESTAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.]?(\d{0,3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.]?(\d{0,3})"
)


def _to_seconds(h, m, s, ms_str):
    ms = int(ms_str) if ms_str else 0
    if len(ms_str) == 1:
        ms *= 100
    elif len(ms_str) == 2:
        ms *= 10
    return int(h) * 3600 + int(m) * 60 + int(s) + ms / 1000.0


def parse_srt(content: str) -> list[dict]:
    entries = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        idx = 0
        try:
            int(lines[idx].strip())
            idx += 1
        except ValueError:
            pass
        if idx >= len(lines):
            continue
        m = SRT_TIMESTAMP.search(lines[idx])
        if not m:
            continue
        idx += 1
        start = _to_seconds(m[1], m[2], m[3], m[4])
        end = _to_seconds(m[5], m[6], m[7], m[8])
        text = " ".join(l.strip() for l in lines[idx:] if l.strip())
        if text:
            entries.append({"start": start, "end": end, "text": text})
    return entries


def to_transcript(entries: list[dict]) -> str:
    return "\n".join(f"[{e['start']:.0f}-{e['end']:.0f}] {e['text']}" for e in entries)


def total_duration(entries: list[dict]) -> float:
    return max((e["end"] for e in entries), default=0.0)
