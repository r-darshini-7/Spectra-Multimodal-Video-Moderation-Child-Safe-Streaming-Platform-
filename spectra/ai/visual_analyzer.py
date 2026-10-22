"""
Visual Analysis Module — OpenCV frame extraction + YOLOv11 object detection.
"""
from __future__ import annotations

import os
from threading import Lock
from typing import Any, Dict, List

import cv2

# Lazy-loaded YOLO model
_yolo_model = None
_yolo_model_lock = Lock()


def _get_model(model_path: str = "yolo11n.pt"):
    global _yolo_model
    with _yolo_model_lock:
        if _yolo_model is None:
            from ultralytics import YOLO
            _yolo_model = YOLO(model_path)
    return _yolo_model


# COCO labels that identify a weapon directly; context-dependent objects are
# kept in detected_objects for review but do not lower the frame score.
RESTRICTED_CLASSES = {
    "knife", "gun", "pistol", "rifle", "sword",
}

WEAPON_CLASSES = RESTRICTED_CLASSES


def analyze_video_frames(
    video_path: str,
    confidence_threshold: float = 0.45,
    sample_interval_sec: float = 1.0,
    batch_size: int = 8,
) -> Dict[str, Any]:
    """
    Extract frames at `sample_interval_sec` and run YOLOv11 detection on each.

    Returns:
        {
          "violations": [{"timestamp_sec": float, "class": str, "confidence": float}, ...],
          "total_frames_sampled": int,
          "safe_frame_count": int,
          "unsafe_frame_count": int,
          "safe_pct": float,
          "unsafe_pct": float,
        }
    """
    model = _get_model(os.getenv("YOLO_MODEL", "yolo11n.pt"))
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    sample_interval_sec = max(1.0, float(sample_interval_sec))
    frame_interval = max(1, int(fps * sample_interval_sec))

    violations: List[Dict[str, Any]] = []
    frame_idx = 0
    sampled = 0
    unsafe_frames = set()
    detected_objects: Dict[str, Dict[str, Any]] = {}
    frame_batch = []
    batch_size = max(1, int(batch_size))

    def process_batch() -> None:
        nonlocal sampled
        if not frame_batch:
            return

        batch_results = None
        frame = result = box = None
        try:
            batch_results = model(
                [frame for _, _, frame in frame_batch],
                verbose=False,
                conf=confidence_threshold,
            )
            if len(batch_results) != len(frame_batch):
                raise RuntimeError(
                    "Object detector returned an unexpected number of results."
                )

            for (sampled_frame_idx, timestamp_sec, frame), result in zip(
                frame_batch, batch_results
            ):
                if result.boxes is not None:
                    for box in result.boxes:
                        cls_id = int(box.cls[0])
                        cls_name = model.names.get(cls_id, "unknown").lower()
                        conf = float(box.conf[0])
                        detection = detected_objects.setdefault(
                            cls_name,
                            {"class": cls_name, "count": 0, "max_confidence": 0.0},
                        )
                        detection["count"] += 1
                        detection["max_confidence"] = max(
                            detection["max_confidence"], conf
                        )
                        if cls_name in RESTRICTED_CLASSES and conf >= confidence_threshold:
                            frame_height, frame_width = frame.shape[:2]
                            x1, y1, x2, y2 = (
                                float(value) for value in box.xyxy[0].tolist()
                            )
                            violations.append(
                                {
                                    "timestamp_sec": round(timestamp_sec, 2),
                                    "class": cls_name,
                                    "confidence": round(conf, 3),
                                    "is_weapon": cls_name in WEAPON_CLASSES,
                                    "bbox": [
                                        round(x1 / frame_width, 6),
                                        round(y1 / frame_height, 6),
                                        round(x2 / frame_width, 6),
                                        round(y2 / frame_height, 6),
                                    ],
                                }
                            )
                            unsafe_frames.add(sampled_frame_idx)
                sampled += 1
        finally:
            frame_batch.clear()
            batch_results = frame = result = box = None

    try:
        while cap.grab():
            if frame_idx % frame_interval == 0:
                ret, frame = cap.retrieve()
                if not ret:
                    break
                timestamp_sec = frame_idx / fps
                frame_batch.append((frame_idx, timestamp_sec, frame))
                frame = None
                if len(frame_batch) >= batch_size:
                    process_batch()
            frame_idx += 1

        process_batch()
    finally:
        cap.release()

    total = sampled
    unsafe_count = len(unsafe_frames)
    safe_count = total - unsafe_count
    detected_objects_summary = sorted(
        detected_objects.values(),
        key=lambda detection: detection["count"],
        reverse=True,
    )[:30]

    return {
        "violations": violations,
        "detected_objects": detected_objects_summary,
        "total_frames_sampled": total,
        "safe_frame_count": safe_count,
        "unsafe_frame_count": unsafe_count,
        "safe_pct": round((safe_count / total) * 100, 2) if total else None,
        "unsafe_pct": round((unsafe_count / total) * 100, 2) if total else None,
    }
