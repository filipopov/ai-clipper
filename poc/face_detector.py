from __future__ import annotations

import os
from typing import Optional, Tuple

import cv2
import numpy as np


_CASCADE = None


def _get_cascade():
    global _CASCADE
    if _CASCADE is None:
        path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        _CASCADE = cv2.CascadeClassifier(path)
    return _CASCADE


def detect_faces_in_segment(
    video_path: str,
    start_sec: float,
    end_sec: float,
    sample_count: int = 6,
) -> dict:
    """
    Detect faces within a specific time range of the video.
    Returns {
        "face_count": int,          # number of distinct speakers (0, 1, 2+)
        "face_x": float,            # horizontal center (0-1), 0.5 = center
        "face_y": float,            # vertical center (0-1)
        "multi_speaker": bool,      # True if 2+ faces detected consistently
        "crop_width_ratio": float,  # how wide to crop (1.0 = full width for multi-speaker)
    }
    """
    cascade = _get_cascade()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return _default()

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width == 0 or height == 0:
        cap.release()
        return _default()

    start_frame = int(start_sec * fps)
    end_frame = int(end_sec * fps)
    frame_range = end_frame - start_frame
    step = max(1, frame_range // sample_count)

    face_xs = []
    face_ys = []
    face_counts = []

    for frame_idx in range(start_frame, end_frame, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))

        if len(faces) > 0:
            face_counts.append(len(faces))
            # For face_x/face_y: use center of all faces (covers multi-speaker)
            all_cx = [(x + w / 2) / width for x, y, w, h in faces]
            all_cy = [(y + h / 2) / height for x, y, w, h in faces]
            face_xs.append(float(np.mean(all_cx)))
            face_ys.append(float(np.mean(all_cy)))

    cap.release()

    if not face_xs:
        return _default()

    median_count = int(np.median(face_counts))
    multi = median_count >= 2

    return {
        "face_count": median_count,
        "face_x": float(np.median(face_xs)),
        "face_y": float(np.median(face_ys)),
        "multi_speaker": multi,
        # For multi-speaker, use wider crop to include both
        "crop_width_ratio": min(1.0, 0.7 + 0.15 * median_count) if multi else 0.5625,  # 9/16
    }


def detect_face_position(video_path: str, sample_count: int = 10) -> Optional[Tuple[float, float]]:
    """Legacy: detect average face position across entire video."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / (cap.get(cv2.CAP_PROP_FPS) or 30)
    cap.release()
    result = detect_faces_in_segment(video_path, 0, dur, sample_count)
    if result["face_count"] == 0:
        return None
    return (result["face_x"], result["face_y"])
