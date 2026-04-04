from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "cliphook.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                source_type TEXT,          -- 'video', 'srt', 'youtube'
                source_name TEXT,
                youtube_url TEXT,
                user_intent TEXT,
                duration_seconds REAL,
                caption_style TEXT DEFAULT 'bold',
                segment_count INTEGER DEFAULT 0,
                clip_count INTEGER DEFAULT 0,
                ai_model TEXT,
                ai_elapsed REAL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                whisper_audio_seconds REAL DEFAULT 0,
                whisper_cost_usd REAL DEFAULT 0,
                claude_cost_usd REAL DEFAULT 0,
                total_cost_usd REAL DEFAULT 0,
                error TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            );

            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                clip_index INTEGER,
                start_time REAL,
                end_time REAL,
                virality_score INTEGER,
                suggested_title TEXT,
                hook_text TEXT,
                topic TEXT,
                hashtags TEXT,           -- JSON array
                video_url TEXT,
                thumb_url TEXT,
                download_url TEXT,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_clips_job ON clips(job_id);
        """)


def create_job(
    job_id: str,
    source_type: str,
    source_name: str,
    user_intent: str = "",
    youtube_url: str = "",
    caption_style: str = "bold",
) -> dict:
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id, source_type, source_name, user_intent, youtube_url, caption_style, created_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, source_type, source_name, user_intent, youtube_url, caption_style, time.time()),
        )
    return get_job(job_id)


def update_job(job_id: str, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)


def get_job(job_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 50) -> list:
    with _conn() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_clip(job_id: str, clip_id: str, clip_index: int, data: dict):
    with _conn() as c:
        c.execute(
            """INSERT INTO clips (id, job_id, clip_index, start_time, end_time, virality_score,
               suggested_title, hook_text, topic, hashtags, video_url, thumb_url, download_url, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                clip_id, job_id, clip_index,
                data.get("start_time"), data.get("end_time"), data.get("virality_score"),
                data.get("suggested_title"), data.get("hook_text"), data.get("topic"),
                json.dumps(data.get("suggested_hashtags", [])),
                data.get("video_url"), data.get("thumb_url"), data.get("download_url"),
                time.time(),
            ),
        )


def get_clips(job_id: str) -> list:
    with _conn() as c:
        rows = c.execute("SELECT * FROM clips WHERE job_id=? ORDER BY clip_index", (job_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["suggested_hashtags"] = json.loads(d.pop("hashtags", "[]"))
        result.append(d)
    return result


def get_usage_stats() -> dict:
    with _conn() as c:
        row = c.execute("""
            SELECT
                COUNT(*) as total_jobs,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(whisper_audio_seconds), 0) as total_whisper_seconds,
                COALESCE(SUM(whisper_cost_usd), 0) as total_whisper_cost,
                COALESCE(SUM(claude_cost_usd), 0) as total_claude_cost,
                COALESCE(SUM(total_cost_usd), 0) as total_cost
            FROM jobs WHERE status = 'completed'
        """).fetchone()
    return dict(row)


def delete_job(job_id: str):
    with _conn() as c:
        c.execute("DELETE FROM clips WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


# Auto-init on import
init_db()
