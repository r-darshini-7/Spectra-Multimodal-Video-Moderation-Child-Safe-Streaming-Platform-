"""Helpers for persistent, deduplicated viewer notifications."""
from __future__ import annotations

from datetime import datetime

from bson import ObjectId

from .extensions import mongo


def create_notification(
    persona_id: str,
    message: str,
    notification_key: str,
    notification_type: str,
    link: str | None = None,
) -> bool:
    """Create a notification once using a stable event key."""
    if not persona_id or not notification_key:
        return False

    notification = {
        "persona_id": persona_id,
        "message": message,
        "type": notification_type,
        "notification_key": notification_key,
        "read": False,
        "created_at": datetime.utcnow(),
    }
    if link:
        notification["link"] = link
    result = mongo.db.notifications.update_one(
        {"notification_key": notification_key},
        {"$setOnInsert": notification},
        upsert=True,
    )
    return result.upserted_id is not None


def notify_creator_subscribers(
    creator_id: str,
    video_id: str,
    video_title: str,
    age_silo: str,
) -> int:
    """Notify same-age-silo profiles that follow the creator about a new upload."""
    if not ObjectId.is_valid(creator_id) or not ObjectId.is_valid(video_id):
        return 0

    creator = mongo.db.personas.find_one(
        {"_id": ObjectId(creator_id)}, {"name": 1}
    ) or {}
    creator_name = creator.get("name") or "A creator you follow"
    subscribers = mongo.db.personas.find(
        {
            "subscribed_creators": ObjectId(creator_id),
            "age_silo": age_silo,
            "_id": {"$ne": ObjectId(creator_id)},
        },
        {"_id": 1},
    )

    notified = 0
    for subscriber in subscribers:
        notified += create_notification(
            str(subscriber["_id"]),
            f"{creator_name} uploaded a new video: {video_title}",
            f"creator-upload:{subscriber['_id']}:{video_id}",
            "creator_upload",
            f"/watch?v={video_id}",
        )
    return notified
