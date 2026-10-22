"""Helpers for persistent, deduplicated viewer notifications."""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from .extensions import mongo

NOTIFICATION_DEFAULTS = {
    "badge": ("Achievement unlocked!", "sparkles"),
    "subscriber_upload": ("New video from a creator you follow", "video"),
    "trending_recommendation": ("Trending in your age group", "trending"),
    "creator_upload": ("New video from a creator you follow", "video"),
    "download_started": ("Download started", "download"),
    "download_completed": ("Download ready", "download"),
    "watch_limit": ("Daily watch reminder", "clock"),
}


def create_notification(
    persona_id: str,
    message: str,
    notification_key: str,
    notification_type: str,
    link: str | None = None,
    *,
    title: str | None = None,
    icon_type: str | None = None,
) -> bool:
    """Create a compatible notification once using its stable event key."""
    if not ObjectId.is_valid(str(persona_id or "")) or not notification_key:
        return False

    default_title, default_icon = NOTIFICATION_DEFAULTS.get(
        notification_type, ("New activity", "bell")
    )
    target_url = link or "/feed"
    now = datetime.now(timezone.utc)
    notification = {
        "user_id": ObjectId(str(persona_id)),
        "persona_id": str(persona_id),
        "type": notification_type,
        "title": title or default_title,
        "message": message,
        "icon_type": icon_type or default_icon,
        "target_url": target_url,
        "is_read": False,
        "read": False,
        "notification_key": notification_key,
        "created_at": now,
    }
    notification["link"] = target_url
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
    """Notify followers whose active age group is allowed to view the upload."""
    if not ObjectId.is_valid(creator_id) or not ObjectId.is_valid(video_id):
        return 0

    video = mongo.db.videos.find_one(
        {"_id": ObjectId(video_id)},
        {"age_silo": 1, "age_silos": 1, "title": 1},
    ) or {}
    allowed_silos = video.get("age_silos")
    if not isinstance(allowed_silos, list) or not allowed_silos:
        allowed_silos = [video.get("age_silo") or age_silo]

    creator = mongo.db.personas.find_one(
        {"_id": ObjectId(creator_id)}, {"name": 1}
    ) or {}
    creator_name = creator.get("name") or "A creator you follow"
    subscribers = mongo.db.personas.find(
        {
            "subscribed_creators": {"$in": [ObjectId(creator_id), creator_id]},
            "age_silo": {"$in": allowed_silos},
            "_id": {"$ne": ObjectId(creator_id)},
        },
        {"_id": 1},
    )

    notified = 0
    silo_labels = {
        "preschool": "preschool",
        "kids": "kids",
        "tweens": "tweens",
        "teens": "teens",
        "unrestricted": "unrestricted",
    }
    audience = " and ".join(silo_labels.get(silo, silo) for silo in allowed_silos)
    safe_video_description = (
        f"a new {audience}-safe video"
        if len(allowed_silos) == 1
        else f"a new video safe for {audience}"
    )
    for subscriber in subscribers:
        notified += create_notification(
            str(subscriber["_id"]),
            (
                f"{creator_name} just uploaded {safe_video_description}: "
                f"{video_title or video.get('title') or 'New video'}"
            ),
            f"creator-upload:{subscriber['_id']}:{video_id}",
            "subscriber_upload",
            f"/watch?v={video_id}",
            title=f"{creator_name} uploaded a new video",
            icon_type="video",
        )
    return notified
