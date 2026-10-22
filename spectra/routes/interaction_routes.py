"""
Interaction Routes Blueprint — AJAX endpoints for likes, comments, subscriptions,
playlists, and watch history.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request, session

from ..activity_tracking import record_viewer_activity
from ..admin_notifications import dispatch_admin_notification
from ..badges import evaluate_user_badges
from ..extensions import mongo
from ..notifications import create_notification

interaction_bp = Blueprint("interaction", __name__)

FEEDBACK_CATEGORIES = {
    "missed_violation",
    "false_positive",
    "player_bug",
    "parental_control",
    "feature_request",
    "other",
}
FEEDBACK_ISSUE_TAGS = {
    "missed_violation": {
        "visual_object_missed",
        "profane_dialogue_missed",
        "burned_in_text_missed",
        "loud_screamer_burst",
        "flashing_strobing_light",
    },
    "false_positive": {
        "innocent_object_blurred",
        "normal_dialogue_bleeped",
        "safe_scene_skipped",
    },
}
FEEDBACK_AGE_LABELS = {
    "preschool": "Preschool",
    "kids": "Kids",
    "tweens": "Tweens",
    "teens": "Teens",
    "unrestricted": "Parent",
}


def _login_required(f):
    @wraps(f)
    def deco(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"ok": False, "error": "Not authenticated."}), 401
        return f(*args, **kwargs)
    return deco


@interaction_bp.route("/feedback", methods=["POST"])
def submit_feedback():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "A feedback form is required."}), 400

    category = str(data.get("category") or "").strip()
    description = str(data.get("description") or "").strip()
    if category not in FEEDBACK_CATEGORIES:
        return jsonify({"ok": False, "error": "Choose a feedback category."}), 400
    if not 10 <= len(description) <= 1000:
        return jsonify({"ok": False, "error": "Description must be between 10 and 1000 characters."}), 400

    raw_tags = data.get("issue_tags") or []
    if not isinstance(raw_tags, list):
        return jsonify({"ok": False, "error": "Issue tags must be a list."}), 400
    allowed_tags = FEEDBACK_ISSUE_TAGS.get(category, set())
    issue_tags = list(dict.fromkeys(
        tag for tag in raw_tags if isinstance(tag, str) and tag in allowed_tags
    ))

    raw_context = str(data.get("video_context") or "").strip()
    if len(raw_context) > 2048:
        return jsonify({"ok": False, "error": "Video context is too long."}), 400
    parsed_context = urlparse(raw_context if "://" in raw_context else f"https://spectra.local/{raw_context.lstrip('/')}" )
    context_query = parse_qs(parsed_context.query)
    candidate_id = str(
        data.get("video_id")
        or (raw_context if ObjectId.is_valid(raw_context) else "")
        or context_query.get("v", [""])[0]
    ).strip()
    if not candidate_id:
        path_parts = [part for part in parsed_context.path.split("/") if part]
        candidate_id = path_parts[-1] if len(path_parts) > 1 and path_parts[-2] == "watch" else ""
    video_id = candidate_id if ObjectId.is_valid(candidate_id) else None

    raw_timestamp = data.get("timestamp_marker")
    timestamp_marker = None
    if raw_timestamp not in (None, ""):
        try:
            timestamp_marker = float(raw_timestamp)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Timestamp must be a valid number."}), 400
        if not math.isfinite(timestamp_marker) or timestamp_marker < 0:
            return jsonify({"ok": False, "error": "Timestamp must be zero or greater."}), 400

    raw_rating = data.get("rating")
    rating = None
    if raw_rating not in (None, ""):
        try:
            rating = int(raw_rating)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Choose a rating from 1 to 5."}), 400
        if rating not in range(1, 6):
            return jsonify({"ok": False, "error": "Choose a rating from 1 to 5."}), 400

    include_diagnostics = data.get("include_diagnostics") is True
    diagnostics = {}
    if include_diagnostics:
        resolution = str(data.get("resolution") or "")[:32]
        latency = data.get("hls_latency")
        hls_latency = None
        if latency not in (None, ""):
            try:
                parsed_latency = float(latency)
                if math.isfinite(parsed_latency) and 0 <= parsed_latency <= 600:
                    hls_latency = parsed_latency
            except (TypeError, ValueError):
                pass
        diagnostics = {
            "user_agent": request.headers.get("User-Agent", "")[:500],
            "resolution": resolution or None,
            "hls_latency": hls_latency,
        }

    feedback_id = str(uuid4())
    feedback = {
        "feedback_id": feedback_id,
        "user_id": str(session.get("persona_id") or session.get("account_id") or "guest"),
        "active_age_silo": (
            FEEDBACK_AGE_LABELS.get(session.get("age_silo"), "Guest")
            if include_diagnostics else "Not provided"
        ),
        "category": category,
        "video_id": video_id,
        "video_context": raw_context or None,
        "timestamp_marker": timestamp_marker,
        "issue_tags": issue_tags,
        "rating": rating,
        "description": description,
        "diagnostics": diagnostics,
        "status": "OPEN",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mongo.db.feedbacks.insert_one(feedback)
    if video_id or category in {"missed_violation", "parental_control"}:
        try:
            video_title = "a video"
            if video_id:
                video = mongo.db.videos.find_one(
                    {"_id": ObjectId(video_id)}, {"title": 1}
                )
                if video and video.get("title"):
                    video_title = f"'{video['title']}'"
            dispatch_admin_notification(
                event_key=f"user-report:{feedback_id}",
                severity="warning",
                category="report",
                title="User Report: Safety concern submitted",
                message=(
                    f"A user reported {video_title}: {description[:220]}"
                ),
                target_url=f"/admin/reports/{feedback_id}",
                video_id=video_id,
                report_id=feedback_id,
            )
        except Exception:
            current_app.logger.exception(
                "Could not dispatch admin alert for report %s", feedback_id
            )
    return jsonify({"ok": True, "feedback_id": feedback_id}), 201


# ── Like / Dislike ────────────────────────────────────────────────────────────

@interaction_bp.route("/like/<video_id>", methods=["POST"])
@_login_required
def like_video(video_id: str):
    account_id = ObjectId(session["account_id"])
    vid = ObjectId(video_id)
    account = mongo.db.accounts.find_one({"_id": account_id})
    liked = str(vid) in [str(x) for x in (account or {}).get("liked_videos", [])]

    if liked:
        mongo.db.accounts.update_one({"_id": account_id}, {"$pull": {"liked_videos": vid}})
        mongo.db.videos.update_one({"_id": vid}, {"$inc": {"likes": -1}})
        new_state = False
    else:
        mongo.db.accounts.update_one({"_id": account_id}, {"$addToSet": {"liked_videos": vid}})
        mongo.db.videos.update_one({"_id": vid}, {"$inc": {"likes": 1}})
        # Remove from dislikes if present
        mongo.db.accounts.update_one({"_id": account_id}, {"$pull": {"disliked_videos": vid}})
        new_state = True

    video = mongo.db.videos.find_one({"_id": vid}, {"likes": 1})
    if video:
        record_viewer_activity(
            session.get("persona_id"),
            session.get("age_silo"),
            "like",
            vid,
            "added" if new_state else "removed",
        )
    return jsonify({"ok": True, "liked": new_state, "count": video.get("likes", 0)})


@interaction_bp.route("/dislike/<video_id>", methods=["POST"])
@_login_required
def dislike_video(video_id: str):
    account_id = ObjectId(session["account_id"])
    vid = ObjectId(video_id)
    account = mongo.db.accounts.find_one({"_id": account_id})
    disliked = str(vid) in [str(x) for x in (account or {}).get("disliked_videos", [])]
    was_liked = str(vid) in [str(x) for x in (account or {}).get("liked_videos", [])]

    if disliked:
        mongo.db.accounts.update_one({"_id": account_id}, {"$pull": {"disliked_videos": vid}})
        mongo.db.videos.update_one({"_id": vid}, {"$inc": {"dislikes": -1}})
        new_state = False
    else:
        mongo.db.accounts.update_one({"_id": account_id}, {"$addToSet": {"disliked_videos": vid}})
        mongo.db.videos.update_one({"_id": vid}, {"$inc": {"dislikes": 1}})
        mongo.db.accounts.update_one({"_id": account_id}, {"$pull": {"liked_videos": vid}})
        new_state = True

    video = mongo.db.videos.find_one({"_id": vid}, {"dislikes": 1})
    if video and not disliked and was_liked:
        record_viewer_activity(
            session.get("persona_id"), session.get("age_silo"), "like", vid, "removed"
        )
    return jsonify({"ok": True, "disliked": new_state, "count": video.get("dislikes", 0)})


# ── Subscribe ─────────────────────────────────────────────────────────────────

@interaction_bp.route("/subscribe/<channel_id>", methods=["POST"])
@_login_required
def subscribe(channel_id: str):
    persona_id = session["persona_id"]
    cid = ObjectId(channel_id)
    channel = mongo.db.channels.find_one({"_id": cid})
    if not channel:
        return jsonify({"ok": False, "error": "Channel not found."}), 404

    is_subbed = persona_id in [str(x) for x in channel.get("subscribers", [])]

    if is_subbed:
        mongo.db.channels.update_one(
            {"_id": cid}, {"$pull": {"subscribers": ObjectId(persona_id)}}
        )
        mongo.db.channels.update_one({"_id": cid}, {"$inc": {"subscriber_count": -1}})
        subbed = False
    else:
        mongo.db.channels.update_one(
            {"_id": cid}, {"$addToSet": {"subscribers": ObjectId(persona_id)}}
        )
        mongo.db.channels.update_one({"_id": cid}, {"$inc": {"subscriber_count": 1}})
        subbed = True

    channel = mongo.db.channels.find_one(
        {"_id": cid}, {"subscriber_count": 1, "created_by": 1}
    ) or {}
    if ObjectId.is_valid(str(channel.get("created_by") or "")):
        try:
            evaluate_user_badges(channel["created_by"])
        except Exception:
            current_app.logger.exception(
                "Could not evaluate creator badges after channel subscription %s",
                channel_id,
            )
    return jsonify({"ok": True, "subscribed": subbed, "count": channel.get("subscriber_count", 0)})


@interaction_bp.route("/subscribe-creator/<creator_id>", methods=["POST"])
@_login_required
def subscribe_creator(creator_id: str):
    if not ObjectId.is_valid(creator_id):
        return jsonify({"ok": False, "error": "Invalid creator."}), 400

    viewer_id = session.get("persona_id")
    if not ObjectId.is_valid(viewer_id or ""):
        return jsonify({"ok": False, "error": "Invalid viewer profile."}), 403
    if creator_id == viewer_id:
        return jsonify({"ok": False, "error": "You cannot subscribe to your own profile."}), 400

    creator_oid = ObjectId(creator_id)
    viewer_oid = ObjectId(viewer_id)
    creator = mongo.db.personas.find_one({"_id": creator_oid}, {"_id": 1})
    viewer = mongo.db.personas.find_one(
        {"_id": viewer_oid}, {"subscribed_creators": 1}
    )
    if not creator:
        return jsonify({"ok": False, "error": "Creator not found."}), 404
    if not viewer:
        return jsonify({"ok": False, "error": "Viewer profile not found."}), 404

    subscribed_ids = {
        str(subscribed_id) for subscribed_id in viewer.get("subscribed_creators", [])
    }
    if creator_id in subscribed_ids:
        mongo.db.personas.update_one(
            {"_id": viewer_oid},
            {"$pull": {"subscribed_creators": creator_oid}},
        )
        subscribed = False
    else:
        mongo.db.personas.update_one(
            {"_id": viewer_oid},
            {"$addToSet": {"subscribed_creators": creator_oid}},
        )
        subscribed = True

    try:
        evaluate_user_badges(creator_oid)
    except Exception:
        current_app.logger.exception(
            "Could not evaluate creator badges after subscription change %s",
            creator_id,
        )

    return jsonify({"ok": True, "subscribed": subscribed})


# ── Comments ──────────────────────────────────────────────────────────────────

@interaction_bp.route("/comment", methods=["POST"])
@_login_required
def post_comment():
    data = request.get_json(force=True)
    video_id = (data.get("video_id") or "").strip()
    text = (data.get("text") or "").strip()
    parent_id = data.get("parent_id")  # None for top-level

    if not video_id or not text:
        return jsonify({"ok": False, "error": "Missing fields."}), 400
    if len(text) > 2000:
        return jsonify({"ok": False, "error": "Comment too long."}), 400

    comment = {
        "video_id": video_id,
        "persona_id": session["persona_id"],
        "persona_name": session.get("persona_name", "User"),
        "text": text,
        "parent_id": parent_id,
        "likes": 0,
        "created_at": datetime.utcnow(),
    }
    result = mongo.db.comments.insert_one(comment)
    mongo.db.videos.update_one({"_id": ObjectId(video_id)}, {"$inc": {"comment_count": 1}})

    return jsonify(
        {
            "ok": True,
            "comment": {
                "_id": str(result.inserted_id),
                "text": text,
                "persona_name": comment["persona_name"],
                "created_at_rel": "just now",
                "likes": 0,
            },
        }
    )


@interaction_bp.route("/comment/like/<comment_id>", methods=["POST"])
@_login_required
def like_comment(comment_id: str):
    mongo.db.comments.update_one(
        {"_id": ObjectId(comment_id)}, {"$inc": {"likes": 1}}
    )
    comment = mongo.db.comments.find_one({"_id": ObjectId(comment_id)}, {"likes": 1})
    return jsonify({"ok": True, "likes": comment.get("likes", 0)})


# ── Playlists ─────────────────────────────────────────────────────────────────

@interaction_bp.route("/playlist/create", methods=["POST"])
@_login_required
def create_playlist():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Playlist name required."}), 400
    result = mongo.db.playlists.insert_one(
        {
            "persona_id": session["persona_id"],
            "name": name,
            "video_ids": [],
            "created_at": datetime.utcnow(),
        }
    )
    return jsonify({"ok": True, "playlist_id": str(result.inserted_id)})


@interaction_bp.route("/playlist/add", methods=["POST"])
@_login_required
def add_to_playlist():
    data = request.get_json(force=True)
    playlist_id = data.get("playlist_id", "")
    video_id = data.get("video_id", "")
    if not playlist_id or not video_id:
        return jsonify({"ok": False, "error": "Missing fields."}), 400
    result = mongo.db.playlists.update_one(
        {"_id": ObjectId(playlist_id), "persona_id": session["persona_id"]},
        {"$addToSet": {"video_ids": video_id}},
    )
    if result.modified_count:
        record_viewer_activity(
            session.get("persona_id"), session.get("age_silo"), "save", video_id, "added"
        )
    return jsonify({"ok": True})


@interaction_bp.route("/watch-later/add", methods=["POST"])
@_login_required
def add_watch_later():
    data = request.get_json(force=True)
    video_id = data.get("video_id", "")
    if not video_id:
        return jsonify({"ok": False}), 400
    result = mongo.db.watch_later.update_one(
        {"persona_id": session["persona_id"]},
        {"$addToSet": {"video_ids": video_id}},
        upsert=True,
    )
    if result.modified_count or result.upserted_id:
        record_viewer_activity(
            session.get("persona_id"), session.get("age_silo"), "save", video_id, "added"
        )
    return jsonify({"ok": True})


# ── Watch History ─────────────────────────────────────────────────────────────

@interaction_bp.route("/history/record", methods=["POST"])
@_login_required
def record_history():
    data = request.get_json(silent=True)
    video_id = data.get("video_id", "") if isinstance(data, dict) else ""
    if not ObjectId.is_valid(video_id):
        return jsonify({"ok": False, "error": "Invalid video."}), 400

    persona_id = session.get("persona_id")
    age_silo = session.get("age_silo")
    if not persona_id or age_silo not in {"preschool", "kids", "tweens", "teens", "unrestricted"}:
        return jsonify({"ok": False, "error": "Invalid viewer profile."}), 403

    video = mongo.db.videos.find_one(
        {
            "_id": ObjectId(video_id),
            "status": "published",
            "$or": [
                {"age_silos": {"$in": [age_silo]}},
                {"age_silos": {"$exists": False}, "age_silo": age_silo},
            ],
        },
        {"_id": 1, "duration_seconds": 1},
    )
    if not video:
        return jsonify({"ok": False, "error": "Video is not available to this profile."}), 403

    updates = {"watched_at": datetime.utcnow()}
    update_operators = {"$set": updates}
    position_seconds = data.get("position_seconds")
    duration_seconds = data.get("duration_seconds")
    if position_seconds is not None:
        if (
            isinstance(position_seconds, bool)
            or not isinstance(position_seconds, (int, float))
            or not math.isfinite(position_seconds)
            or not 0 <= position_seconds <= 86400
        ):
            return jsonify({"ok": False, "error": "Invalid playback position."}), 400
        updates["position_seconds"] = float(position_seconds)

    if duration_seconds is not None:
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds)
            or not 0 < duration_seconds <= 86400
        ):
            return jsonify({"ok": False, "error": "Invalid video duration."}), 400
        update_operators["$max"] = {"duration_seconds": float(duration_seconds)}
    elif video.get("duration_seconds"):
        update_operators["$max"] = {
            "duration_seconds": float(video["duration_seconds"])
        }

    if (
        position_seconds is not None
        and duration_seconds
        and position_seconds >= duration_seconds * 0.9
    ):
        updates["completed"] = True
        updates["completed_at"] = datetime.utcnow()

    play_started = data.get("play_started") is True
    if play_started:
        update_operators["$inc"] = {"play_count": 1}

    mongo.db.watch_history.update_one(
        {"persona_id": persona_id, "video_id": video_id},
        update_operators,
        upsert=True,
    )
    if play_started:
        record_viewer_activity(
            persona_id,
            age_silo,
            "play_started",
            ObjectId(video_id),
            "started",
        )
    return jsonify({"ok": True})


@interaction_bp.route("/watch-time/<video_id>", methods=["POST"])
@_login_required
def record_watch_time(video_id: str):
    if not ObjectId.is_valid(video_id):
        return jsonify({"ok": False, "error": "Invalid video."}), 400

    data = request.get_json(force=True)
    duration_seconds = data.get("seconds")
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 30:
        return jsonify({"ok": False, "error": "Watch time must be between 1 and 30 seconds."}), 400

    age_silo = session.get("age_silo")
    silos = ["preschool", "kids", "tweens", "teens", "unrestricted"]
    if age_silo not in silos:
        return jsonify({"ok": False, "error": "Invalid profile age group."}), 403

    video_id_object = ObjectId(video_id)
    video = mongo.db.videos.find_one(
        {"_id": video_id_object, "status": "published"},
        {"age_silo": 1, "age_silos": 1},
    )
    available_silos = video.get("age_silos") if video else None
    available_to_profile = (
        age_silo in available_silos
        if isinstance(available_silos, list)
        else video and video.get("age_silo") == age_silo
    )
    if not video or not available_to_profile:
        return jsonify({"ok": False, "error": "Video is not available to this profile."}), 403

    record_viewer_activity(
        session.get("persona_id"),
        age_silo,
        "watch_time",
        video_id_object,
        "watched",
        duration_seconds=duration_seconds,
    )
    account_id = session.get("account_id")
    account = (
        mongo.db.accounts.find_one(
            {"_id": ObjectId(account_id)},
            {"settings.watch_limit_minutes": 1},
        )
        if ObjectId.is_valid(str(account_id or ""))
        else None
    )
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    watch_total = list(mongo.db.viewer_activity.aggregate([
        {"$match": {
            "persona_id": session.get("persona_id"),
            "event_type": "watch_time",
            "occurred_at": {"$gte": day_start, "$lt": now},
        }},
        {"$group": {"_id": None, "seconds": {"$sum": "$duration_seconds"}}},
    ]))
    settings = (account or {}).get("settings") or {}
    watched_seconds = watch_total[0].get("seconds", 0) if watch_total else 0
    limit_minutes = settings.get("watch_limit_minutes", 60)
    day_key = now.strftime("%Y-%m-%d")
    if (
        isinstance(limit_minutes, (int, float))
        and limit_minutes > 0
        and watched_seconds >= limit_minutes * 60
    ):
        create_notification(
            str(session.get("persona_id") or ""),
            "You reached today's video watch limit.",
            f"watch-limit:{session.get('persona_id')}:{day_key}",
            "watch_limit",
            "/feed",
        )
    return jsonify({
        "ok": True,
        "watched_seconds": watched_seconds,
        "limit_minutes": limit_minutes,
        "day_key": day_key,
        "limit_reached": (
            isinstance(limit_minutes, (int, float))
            and limit_minutes > 0
            and watched_seconds >= limit_minutes * 60
        ),
    })
