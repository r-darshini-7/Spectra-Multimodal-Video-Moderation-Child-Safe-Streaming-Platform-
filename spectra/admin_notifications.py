"""Persistence helpers for deduplicated super-admin alerts."""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from .extensions import mongo


def dispatch_admin_notification(
    *,
    event_key: str,
    severity: str,
    category: str,
    title: str,
    message: str,
    target_url: str,
    video_id: str | ObjectId | None = None,
    report_id: str | None = None,
) -> bool:
    """Insert a single internal admin alert for a stable event key."""
    if not event_key:
        raise ValueError("An event key is required for an admin alert.")
    if severity not in {"critical", "warning", "info"}:
        raise ValueError("Invalid admin alert severity.")
    if category not in {"moderation", "pipeline", "report", "creator"}:
        raise ValueError("Invalid admin alert category.")
    if not target_url.startswith("/") or target_url.startswith("//"):
        raise ValueError("Admin alert destinations must be internal paths.")

    notification = {
        "recipient_role": "admin",
        "severity": severity,
        "category": category,
        "title": title,
        "message": message,
        "target_url": target_url,
        "is_read": False,
        "event_key": event_key,
        "created_at": datetime.now(timezone.utc),
    }
    if video_id and ObjectId.is_valid(str(video_id)):
        notification["video_id"] = ObjectId(str(video_id))
    if report_id:
        notification["report_id"] = str(report_id)

    result = mongo.db.admin_notifications.update_one(
        {"event_key": event_key},
        {"$setOnInsert": notification},
        upsert=True,
    )
    return result.upserted_id is not None
