"""Minimal interaction-event logging for viewer analytics."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId

from .extensions import mongo

ANALYTICS_AGE_SILOS = {"preschool", "kids", "tweens", "teens", "unrestricted"}


def record_viewer_activity(
    persona_id: str | None,
    age_silo: str | None,
    event_type: str,
    video_id: str | ObjectId,
    action: str,
    duration_seconds: int | None = None,
) -> None:
    """Record profile interactions and playback duration for analytics."""
    if age_silo not in ANALYTICS_AGE_SILOS or not persona_id:
        return

    if event_type == "watch_time" and (
        duration_seconds is None or not 1 <= duration_seconds <= 30
    ):
        raise ValueError("Watch-time events must contain 1 to 30 seconds.")

    video_object_id = video_id if isinstance(video_id, ObjectId) else None
    if video_object_id is None:
        if not ObjectId.is_valid(video_id):
            return
        video_object_id = ObjectId(video_id)

    video = mongo.db.videos.find_one({"_id": video_object_id}, {"category": 1})
    if not video:
        return
    activity = {
        "persona_id": persona_id,
        "event_type": event_type,
        "action": action,
        "video_id": str(video_object_id),
        "category": video.get("category") or "Other",
        "occurred_at": datetime.utcnow(),
    }
    if event_type == "watch_time":
        activity["duration_seconds"] = duration_seconds
    mongo.db.viewer_activity.insert_one(activity)
