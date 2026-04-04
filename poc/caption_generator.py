from __future__ import annotations

STYLES = {
    "bold": {
        "font": "Arial Bold", "size": 58,
        "primary": "&H00FFFFFF", "highlight": "&H0000FFFF",
        "outline": "&H00000000", "outline_width": 3.5, "shadow": 1.5,
        "animation": "none",
    },
    "clean": {
        "font": "Helvetica", "size": 52,
        "primary": "&H00FFFFFF", "highlight": "&H00FF9544",
        "outline": "&H00000000", "outline_width": 2.0, "shadow": 0,
        "animation": "none",
    },
    "neon": {
        "font": "Arial Bold", "size": 62,
        "primary": "&H00FFFFFF", "highlight": "&H00FF00FF",
        "outline": "&H00990099", "outline_width": 4.0, "shadow": 2.0,
        "animation": "none",
    },
    "minimal": {
        "font": "Helvetica Neue", "size": 46,
        "primary": "&H00DDDDDD", "highlight": "&H00FFFFFF",
        "outline": "&H00000000", "outline_width": 1.5, "shadow": 0.5,
        "animation": "none",
    },
    "pop": {
        "font": "Arial Bold", "size": 62,
        "primary": "&H00FFFFFF", "highlight": "&H0000FFFF",
        "outline": "&H00000000", "outline_width": 4.0, "shadow": 1.5,
        "animation": "pop",  # scale up on highlight
    },
    "bounce": {
        "font": "Arial Bold", "size": 58,
        "primary": "&H00FFFFFF", "highlight": "&H0064FF64",
        "outline": "&H00000000", "outline_width": 3.5, "shadow": 1.5,
        "animation": "bounce",  # move up on highlight
    },
    "glow": {
        "font": "Arial Bold", "size": 60,
        "primary": "&H00FFFFFF", "highlight": "&H0000CCFF",
        "outline": "&H00005599", "outline_width": 5.0, "shadow": 3.0,
        "animation": "none",
    },
}


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def generate_ass(
    words: list,
    clip_start: float,
    clip_end: float,
    style: str = "bold",
    video_width: int = 1080,
    video_height: int = 1920,
) -> str:
    cfg = STYLES.get(style, STYLES["bold"])

    clip_words = []
    for w in words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        clip_words.append({
            "word": w["word"].strip(),
            "start": max(0.0, w["start"] - clip_start),
            "end": min(clip_end - clip_start, w["end"] - clip_start),
        })

    header = (
        "[Script Info]\n"
        "Title: Cliphook Captions\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{cfg['font']},{cfg['size']},{cfg['primary']},&H000000FF,"
        f"{cfg['outline']},&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{cfg['outline_width']},{cfg['shadow']},2,40,40,180,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    if not clip_words:
        return header

    max_per_line = 4
    max_lines = 2
    chunk_size = max_per_line * max_lines
    chunks = [clip_words[i:i + chunk_size] for i in range(0, len(clip_words), chunk_size)]

    events = []
    hl = cfg["highlight"]
    pr = cfg["primary"]
    anim = cfg["animation"]

    for chunk in chunks:
        lines = [chunk[j:j + max_per_line] for j in range(0, len(chunk), max_per_line)]

        for wi, cw in enumerate(chunk):
            t_start = cw["start"]
            t_end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else cw["end"]
            if t_end <= t_start:
                t_end = t_start + 0.05

            text_lines = []
            for line in lines:
                parts = []
                for w in line:
                    if w is cw:
                        word_text = _apply_animation(w["word"], anim, hl, pr)
                    else:
                        parts.append(w["word"])
                        continue
                    parts.append(word_text)
                text_lines.append(" ".join(parts))

            text = "\\N".join(text_lines)
            events.append(f"Dialogue: 0,{_ass_time(t_start)},{_ass_time(t_end)},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def _apply_animation(word: str, anim: str, hl: str, pr: str) -> str:
    """Apply animation override tags to the highlighted word."""
    if anim == "pop":
        # Scale up to 120% then back
        return (
            "{\\c" + hl + "\\fscx120\\fscy120\\t(0,80,\\fscx100\\fscy100)}" +
            word +
            "{\\c" + pr + "}"
        )
    elif anim == "bounce":
        # Move up 15px then back
        return (
            "{\\c" + hl + "\\move(0,-15,0,0,0,100)}" +
            word +
            "{\\c" + pr + "}"
        )
    else:
        return "{\\c" + hl + "}" + word + "{\\c" + pr + "}"
