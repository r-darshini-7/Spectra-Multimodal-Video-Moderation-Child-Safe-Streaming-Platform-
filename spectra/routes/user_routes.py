"""
User / Viewer Routes Blueprint — Feed, watch page, search, channel.
"""
from __future__ import annotations

import calendar
import os
import re
import time
from datetime import datetime
from functools import wraps

from bson import ObjectId
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)

from ..activity_tracking import record_viewer_activity
from ..category_rules import CATEGORY_OPTIONS
from ..extensions import mongo
from ..notifications import create_notification

user_bp = Blueprint("user", __name__)

ANALYTICS_AGE_SILOS = {"preschool", "kids", "tweens", "teens", "unrestricted"}
ANALYTICS_AGE_GROUP_LABELS = {
    "preschool": "Preschool",
    "kids": "Kids",
    "tweens": "Tweens",
    "teens": "Teens",
    "unrestricted": "Unrestricted",
}


def _attach_creator_profiles(videos: list[dict]) -> None:
    """Add the uploading persona's name and avatar to video-card data."""
    creator_ids = {
        ObjectId(str(video["uploaded_by"]))
        for video in videos
        if ObjectId.is_valid(str(video.get("uploaded_by") or ""))
    }
    creators = {
        str(creator["_id"]): creator
        for creator in mongo.db.personas.find(
            {"_id": {"$in": list(creator_ids)}},
            {"name": 1, "avatar_data": 1},
        )
    } if creator_ids else {}

    for video in videos:
        creator = creators.get(str(video.get("uploaded_by") or ""), {})
        video["creator_name"] = (
            creator.get("name") or video.get("channel_name") or "Creator"
        )
        video["creator_avatar"] = creator.get("avatar_data") or ""


# ── Auth Guard ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login_page"))
        return f(*args, **kwargs)
    return decorated


# ── Silo Filter Helper ────────────────────────────────────────────────────────

SILO_ORDER = ["preschool", "kids", "tweens", "teens", "unrestricted"]


def _allowed_silos(user_silo: str):
    """Return only the active profile's age silo."""
    return [user_silo] if user_silo in SILO_ORDER else []


def _published_video_search_query(query: str, age_silo: str) -> dict:
    """Build the shared, literal substring search for visible published videos."""
    escaped_query = re.escape(query[:100])
    return {
        "status": "published",
        "age_silo": {"$in": _allowed_silos(age_silo)},
        "$or": [
            {"title": {"$regex": escaped_query, "$options": "i"}},
            {"description": {"$regex": escaped_query, "$options": "i"}},
        ],
    }


# ── Root redirect ─────────────────────────────────────────────────────────────

@user_bp.route("/")
def index():
    session.clear()
    return render_template("welcome.html")


@user_bp.route("/help")
@login_required
def help_page():
    return render_template("help.html")


@user_bp.route("/terms")
def terms_page():
    return render_template("terms.html")


@user_bp.route("/feedback")
def feedback_page():
    return render_template("feedback.html")


# ── Creator Upload ────────────────────────────────────────────────────────────

@user_bp.route("/upload")
@login_required
def upload_video_page():
    age_silo = session.get("age_silo")
    if age_silo not in {"teens", "unrestricted"}:
        abort(403)
    return render_template(
        "admin/add_video.html",
        category_options=CATEGORY_OPTIONS,
        default_age_silo=age_silo,
    )


@user_bp.route("/uploaded-videos")
@login_required
def uploaded_videos():
    persona_id = session.get("persona_id")
    if not persona_id:
        abort(403)
    videos = list(
        mongo.db.videos.find({"uploaded_by": persona_id, "status": "published"})
        .sort("created_at", -1)
    )
    for index, video in enumerate(videos, start=1):
        video["_id"] = str(video["_id"])
        video["serial_number"] = index
    return render_template("user/uploaded_videos.html", videos=videos)


@user_bp.route("/uploaded-videos/<video_id>/analytics")
@login_required
def uploaded_video_analytics(video_id: str):
    if not ObjectId.is_valid(video_id):
        abort(404)
    persona_id = session.get("persona_id")
    if not persona_id:
        abort(403)
    video = mongo.db.videos.find_one(
        {"_id": ObjectId(video_id), "uploaded_by": persona_id, "status": "published"}
    )
    if not video:
        abort(404)
    video["_id"] = str(video["_id"])
    return render_template("user/uploaded_video_analytics.html", video=video)


@user_bp.route("/uploaded-videos/<video_id>/delete", methods=["POST"])
@login_required
def delete_uploaded_video(video_id: str):
    if not ObjectId.is_valid(video_id):
        abort(404)
    persona_id = session.get("persona_id")
    if not persona_id:
        abort(403)
    video = mongo.db.videos.find_one(
        {"_id": ObjectId(video_id), "uploaded_by": persona_id}
    )
    if not video:
        abort(404)

    from ..storage_cleanup import delete_download_files
    delete_download_files(
        current_app.config["DOWNLOAD_FOLDER"],
        [video.get("download_filename")],
    )
    for filename, folder in (
        (video.get("original_filename"), current_app.config["UPLOAD_FOLDER"]),
        (video.get("thumbnail"), os.path.join(current_app.static_folder, "thumbnails")),
        (video.get("vtt_file"), os.path.join(current_app.static_folder, "vtt")),
    ):
        if filename and os.path.basename(filename) == filename:
            filepath = os.path.join(folder, filename)
            if os.path.isfile(filepath):
                for attempt in range(5):
                    try:
                        os.remove(filepath)
                        break
                    except PermissionError:
                        if attempt == 4:
                            return redirect(
                                url_for(
                                    "user.uploaded_videos",
                                    delete_error="file_busy",
                                )
                            )
                        time.sleep(0.25)

    import shutil
    shutil.rmtree(
        os.path.join(current_app.config["HLS_OUTPUT_FOLDER"], video_id),
        ignore_errors=True,
    )
    mongo.db.videos.delete_one({"_id": ObjectId(video_id)})
    return redirect(url_for("user.uploaded_videos"))


# ── Feed ──────────────────────────────────────────────────────────────────────

@user_bp.route("/feed")
@login_required
def feed():
    age_silo = session.get("age_silo", "unrestricted")
    allowed = _allowed_silos(age_silo)
    query = {"status": "published", "age_silo": {"$in": allowed}}

    # Subscriptions sub-feed
    subs_only = request.args.get("subs", "0") == "1"
    if subs_only:
        persona_id = session.get("persona_id")
        viewer = (
            mongo.db.personas.find_one(
                {"_id": ObjectId(persona_id)}, {"subscribed_creators": 1}
            )
            if ObjectId.is_valid(persona_id or "")
            else {}
        ) or {}
        subscribed_channels = list(
            mongo.db.channels.find(
                {"subscribers": ObjectId(persona_id)}, {"_id": 1}
            ) if ObjectId.is_valid(persona_id or "") else []
        )
        ch_ids = [str(c["_id"]) for c in subscribed_channels]
        creator_ids = [
            str(creator_id) for creator_id in viewer.get("subscribed_creators", [])
        ]
        query["$or"] = [
            {"channel_id": {"$in": ch_ids}},
            {"uploaded_by": {"$in": creator_ids}},
        ]

    search_q = request.args.get("q", "").strip()
    if search_q:
        query["$text"] = {"$search": search_q}

    category_options = CATEGORY_OPTIONS.get(age_silo, CATEGORY_OPTIONS["unrestricted"])
    selected_category = request.args.get("category", "").strip()
    if selected_category not in category_options:
        selected_category = ""
    if selected_category:
        query["category"] = selected_category

    videos = list(
        mongo.db.videos.find(query)
        .sort("created_at", -1)
        .limit(48)
    )
    _attach_creator_profiles(videos)
    for v in videos:
        v["_id"] = str(v["_id"])
        v["created_at_rel"] = _relative_time(v.get("created_at"))

    return render_template(
        "user/feed.html",
        videos=videos,
        age_silo=age_silo,
        category_options=category_options,
        selected_category=selected_category,
        search_q=search_q,
        subs_only=subs_only,
    )


@user_bp.route("/analytics")
@login_required
def analytics():
    can_view_household = _can_view_household_analytics()
    if session.get("age_silo") not in ANALYTICS_AGE_SILOS and not can_view_household:
        abort(403)
    return render_template(
        "user/analytics.html",
        can_view_household=can_view_household,
        age_group_labels=ANALYTICS_AGE_GROUP_LABELS,
    )


def _can_view_household_analytics() -> bool:
    """Only the primary unrestricted household profile can view all child groups."""
    try:
        persona_id = ObjectId(session.get("persona_id", ""))
        account_id = ObjectId(session.get("account_id", ""))
    except Exception:
        return False

    return mongo.db.personas.find_one(
        {
            "_id": persona_id,
            "account_id": account_id,
            "is_primary": True,
            "age_silo": "unrestricted",
        },
        {"_id": 1},
    ) is not None


@user_bp.route("/analytics/data")
@login_required
def analytics_data():
    can_view_household = _can_view_household_analytics()
    if session.get("age_silo") not in ANALYTICS_AGE_SILOS and not can_view_household:
        abort(403)

    months_value = request.args.get("months", "1")
    if months_value not in {"1", "3", "6", "12"}:
        return jsonify({"ok": False, "error": "Select a valid analytics period."}), 400

    selected_age_group = request.args.get("age_group", "")
    if can_view_household:
        if selected_age_group not in {*ANALYTICS_AGE_SILOS, "all"}:
            return jsonify({"ok": False, "error": "Select a valid age group."}), 400
        if selected_age_group == "all":
            personas = mongo.db.personas.find(
                {
                    "account_id": ObjectId(session["account_id"]),
                    "age_silo": {"$in": list(ANALYTICS_AGE_SILOS)},
                },
                {"_id": 1},
            )
        else:
            personas = mongo.db.personas.find(
                {
                    "account_id": ObjectId(session["account_id"]),
                    "age_silo": selected_age_group,
                },
                {"_id": 1},
            )
        persona_ids = [str(persona["_id"]) for persona in personas]
    else:
        selected_age_group = session["age_silo"]
        persona_ids = [session["persona_id"]]

    now = datetime.utcnow()
    months = int(months_value)
    month_index = now.month - months
    target_year = now.year + (month_index - 1) // 12
    target_month = (month_index - 1) % 12 + 1
    range_start = now.replace(
        year=target_year,
        month=target_month,
        day=min(now.day, calendar.monthrange(target_year, target_month)[1]),
    )

    activity_pipeline = [
        {
            "$match": {
                "persona_id": {"$in": persona_ids},
                "occurred_at": {"$gte": range_start, "$lte": now},
                "event_type": {"$in": ["like", "save", "download"]},
            }
        },
        {"$sort": {"occurred_at": 1}},
        {
            "$group": {
                "_id": {
                    "event_type": "$event_type",
                    "video_id": "$video_id",
                },
                "action": {"$last": "$action"},
                "category": {"$last": "$category"},
            }
        },
    ]

    activity_counts = {"like": 0, "save": 0, "download": 0}
    topic_interests = {}
    for activity in mongo.db.viewer_activity.aggregate(activity_pipeline):
        event_type = activity["_id"]["event_type"]
        action = activity.get("action")
        if action == "removed" or event_type not in activity_counts:
            continue
        activity_counts[event_type] += 1
        category = activity.get("category") or "Other"
        topic_interests[category] = topic_interests.get(category, 0) + 1

    watch_event_filter = {
        "persona_id": {"$in": persona_ids},
        "occurred_at": {"$gte": range_start, "$lte": now},
        "event_type": "watch_time",
    }
    watch_analytics = list(mongo.db.viewer_activity.aggregate([
        {"$match": watch_event_filter},
        {"$facet": {
            "monthly": [
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {
                                "format": "%Y-%m",
                                "date": "$occurred_at",
                            }
                        },
                        "seconds": {"$sum": "$duration_seconds"},
                    }
                },
                {"$sort": {"_id": 1}},
            ],
            "weekdays": [
                {
                    "$group": {
                        "_id": {"$dayOfWeek": "$occurred_at"},
                        "seconds": {"$sum": "$duration_seconds"},
                    }
                },
            ],
            "dayparts": [
                {
                    "$group": {
                        "_id": {"$hour": "$occurred_at"},
                        "seconds": {"$sum": "$duration_seconds"},
                    }
                },
            ],
            "videos": [
                {
                    "$group": {
                        "_id": "$video_id",
                        "seconds": {"$sum": "$duration_seconds"},
                    }
                },
                {"$sort": {"seconds": -1}},
            ],
        }},
    ]))
    watch_analytics = watch_analytics[0] if watch_analytics else {}
    monthly_watch_time = watch_analytics.get("monthly", [])
    weekly_watch_time = watch_analytics.get("weekdays", [])
    daypart_watch_time = watch_analytics.get("dayparts", [])
    video_watch_totals = watch_analytics.get("videos", [])
    play_counts = list(mongo.db.viewer_activity.aggregate([
        {
            "$match": {
                "persona_id": {"$in": persona_ids},
                "occurred_at": {"$gte": range_start, "$lte": now},
                "event_type": "play_started",
            }
        },
        {
            "$group": {
                "_id": "$video_id",
                "plays": {"$sum": 1},
            }
        },
        {"$sort": {"plays": -1}},
    ]))
    history_records = list(mongo.db.watch_history.find(
        {
            "persona_id": {"$in": persona_ids},
            "watched_at": {"$gte": range_start, "$lte": now},
        },
        {
            "video_id": 1,
            "position_seconds": 1,
            "duration_seconds": 1,
            "completed": 1,
        },
    ))
    saved_lists = mongo.db.watch_later.find(
        {"persona_id": {"$in": persona_ids}},
        {"video_ids": 1},
    )
    saved_video_ids = {
        str(video_id)
        for saved_list in saved_lists
        for video_id in saved_list.get("video_ids", [])
        if ObjectId.is_valid(str(video_id))
    }
    history_video_ids = {
        record.get("video_id")
        for record in history_records
        if ObjectId.is_valid(str(record.get("video_id") or ""))
    }
    video_id_strings = {
        str(item["_id"])
        for item in video_watch_totals
        if ObjectId.is_valid(str(item.get("_id") or ""))
    } | {
        str(item["_id"])
        for item in play_counts
        if ObjectId.is_valid(str(item.get("_id") or ""))
    } | {
        str(video_id)
        for video_id in history_video_ids
    }
    video_object_ids = [ObjectId(video_id) for video_id in video_id_strings]
    video_details = {
        str(video["_id"]): video
        for video in mongo.db.videos.find(
            {"_id": {"$in": video_object_ids}},
            {
                "title": 1,
                "category": 1,
                "uploaded_by": 1,
                "channel_name": 1,
                "thumbnail": 1,
                "duration_seconds": 1,
                "duration_str": 1,
            },
        )
    } if video_object_ids else {}

    total_watch_seconds = sum(
        month["seconds"] for month in monthly_watch_time
    )
    range_days = max(1, (now.date() - range_start.date()).days + 1)
    weekday_seconds = [0] * 7
    for day in weekly_watch_time:
        # MongoDB numbers Sunday as 1; the dashboard begins on Monday.
        weekday_seconds[(int(day["_id"]) + 5) % 7] = day["seconds"]

    daypart_seconds = {
        "Morning": 0,
        "Afternoon": 0,
        "Evening": 0,
        "Night": 0,
    }
    for hour_data in daypart_watch_time:
        hour = int(hour_data["_id"])
        if 6 <= hour < 12:
            daypart = "Morning"
        elif 12 <= hour < 17:
            daypart = "Afternoon"
        elif 17 <= hour < 21:
            daypart = "Evening"
        else:
            daypart = "Night"
        daypart_seconds[daypart] += hour_data["seconds"]
    daypart_total = sum(daypart_seconds.values())
    time_of_day = [
        {
            "period": period,
            "seconds": seconds,
            "percentage": round(seconds / daypart_total * 100)
            if daypart_total else 0,
        }
        for period, seconds in daypart_seconds.items()
    ]

    watched_video_ids = {
        str(record.get("video_id"))
        for record in history_records
        if ObjectId.is_valid(str(record.get("video_id") or ""))
    }
    completed_video_ids = set()
    for record in history_records:
        video_id = str(record.get("video_id") or "")
        detail = video_details.get(video_id, {})
        duration = record.get("duration_seconds") or detail.get("duration_seconds")
        if not duration and detail.get("duration_str"):
            try:
                minutes, seconds = detail["duration_str"].split(":")
                duration = int(minutes) * 60 + int(seconds)
            except (TypeError, ValueError):
                duration = 0
        position = record.get("position_seconds") or 0
        if record.get("completed") or (
            duration and position >= duration * 0.9
        ):
            completed_video_ids.add(video_id)

    watched_saved_count = len(saved_video_ids & watched_video_ids)
    top_category_seconds: dict[str, int] = {}
    creator_seconds: dict[str, int] = {}
    for item in video_watch_totals:
        video_id = str(item.get("_id") or "")
        detail = video_details.get(video_id, {})
        category = detail.get("category") or "Other"
        top_category_seconds[category] = (
            top_category_seconds.get(category, 0) + item.get("seconds", 0)
        )
        creator_id = str(detail.get("uploaded_by") or "")
        creator_key = creator_id if ObjectId.is_valid(creator_id) else (
            detail.get("channel_name") or "Other creators"
        )
        creator_seconds[creator_key] = (
            creator_seconds.get(creator_key, 0) + item.get("seconds", 0)
        )

    creator_ids = [
        ObjectId(creator_id)
        for creator_id in creator_seconds
        if ObjectId.is_valid(creator_id)
    ]
    creator_profiles = {
        str(creator["_id"]): creator
        for creator in mongo.db.personas.find(
            {"_id": {"$in": creator_ids}},
            {"name": 1, "avatar_data": 1},
        )
    } if creator_ids else {}
    top_creators = []
    for creator_id, seconds in sorted(
        creator_seconds.items(), key=lambda item: (-item[1], item[0].casefold())
    )[:5]:
        profile = creator_profiles.get(creator_id, {})
        top_creators.append({
            "name": profile.get("name") or creator_id,
            "avatar": profile.get("avatar_data") or "",
            "minutes": max(1, round(seconds / 60)) if seconds else 0,
        })

    most_replayed = None
    if play_counts:
        favorite = play_counts[0]
        favorite_video = video_details.get(str(favorite.get("_id") or ""), {})
        if favorite_video:
            most_replayed = {
                "title": favorite_video.get("title") or "Untitled video",
                "thumbnail": favorite_video.get("thumbnail") or "",
                "video_id": str(favorite.get("_id")),
                "plays": favorite.get("plays", 0),
            }
    elif video_watch_totals:
        favorite = video_watch_totals[0]
        favorite_video = video_details.get(str(favorite.get("_id") or ""), {})
        if favorite_video:
            most_replayed = {
                "title": favorite_video.get("title") or "Untitled video",
                "thumbnail": favorite_video.get("thumbnail") or "",
                "video_id": str(favorite.get("_id")),
                "plays": 1,
            }

    badges = []
    if len(top_category_seconds) >= 3:
        badges.append({
            "title": "Curious Learner",
            "detail": f"You explored {len(top_category_seconds)} different topics.",
            "icon": "fa-compass",
            "color": "purple",
        })
    weekend_seconds = weekday_seconds[5] + weekday_seconds[6]
    if weekend_seconds > 0:
        badges.append({
            "title": "Weekend Explorer",
            "detail": "You made time for videos over the weekend.",
            "icon": "fa-sun",
            "color": "orange",
        })
    if not badges and top_category_seconds:
        favorite_topic = max(
            top_category_seconds.items(), key=lambda item: item[1]
        )[0]
        if "stor" in favorite_topic.casefold():
            badges.append({
                "title": "Storytime Lover",
                "detail": f"You spent the most time with {favorite_topic}.",
                "icon": "fa-book-open",
                "color": "blue",
            })

    watch_time_by_month = [
        {
            "month": month["_id"],
            "hours": round(month["seconds"] / 3600, 2),
        }
        for month in monthly_watch_time
    ]

    return jsonify(
        {
            "ok": True,
            "months": months,
            "age_group": selected_age_group,
            "watch_time_hours": round(
                sum(month["seconds"] for month in monthly_watch_time) / 3600, 2
            ),
            "watch_time_by_month": watch_time_by_month,
            "summary": {
                "total_seconds": total_watch_seconds,
                "daily_average_seconds": round(total_watch_seconds / range_days),
                "range_days": range_days,
                "top_topic": max(
                    top_category_seconds,
                    key=top_category_seconds.get,
                    default=None,
                ),
                "most_replayed": most_replayed,
            },
            "watch_by_day": [
                {"day": day, "seconds": seconds}
                for day, seconds in zip(
                    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    weekday_seconds,
                )
            ],
            "time_of_day": time_of_day,
            "top_creators": top_creators,
            "completion": {
                "started": len(watched_video_ids),
                "completed": len(completed_video_ids),
                "rate": round(
                    len(completed_video_ids) / len(watched_video_ids) * 100
                ) if watched_video_ids else 0,
            },
            "saved_vs_watched": {
                "saved": len(saved_video_ids),
                "watched": watched_saved_count,
            },
            "badges": badges[:2],
            "counts": {
                "liked": activity_counts["like"],
                "saved": activity_counts["save"],
                "downloaded": activity_counts["download"],
            },
            "topic_interests": [
                {"category": category, "count": count}
                for category, count in sorted(
                    topic_interests.items(),
                    key=lambda item: (-item[1], item[0].casefold()),
                )
            ],
        }
    )


# ── Search Results ────────────────────────────────────────────────────────────

@user_bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    category = request.args.get("cat", "").strip()
    age_silo = session.get("age_silo", "unrestricted")
    allowed = _allowed_silos(age_silo)

    query: dict = {"status": "published", "age_silo": {"$in": allowed}}
    if q:
        query = _published_video_search_query(q, age_silo)
    if category:
        query["category"] = category

    videos = list(
        mongo.db.videos.find(query).sort("created_at", -1).limit(60)
    )
    _attach_creator_profiles(videos)
    for v in videos:
        v["_id"] = str(v["_id"])
        v["created_at_rel"] = _relative_time(v.get("created_at"))

    categories = mongo.db.videos.distinct("category")

    return render_template(
        "user/search.html",
        videos=videos,
        search_q=q,
        selected_cat=category,
        categories=sorted(categories),
        age_silo=age_silo,
    )


# ── Watch Page ────────────────────────────────────────────────────────────────

@user_bp.route("/watch")
@login_required
def watch():
    video_id = request.args.get("v")
    if not video_id:
        return redirect(url_for("user.feed"))

    try:
        oid = ObjectId(video_id)
    except Exception:
        abort(404)

    video = mongo.db.videos.find_one({"_id": oid, "status": "published"})
    if not video:
        abort(404)

    age_silo = session.get("age_silo", "unrestricted")
    allowed = _allowed_silos(age_silo)
    if video.get("age_silo") not in allowed:
        abort(403)

    # Increment view count atomically
    mongo.db.videos.update_one({"_id": oid}, {"$inc": {"views": 1}})

    video["_id"] = str(video["_id"])
    video_id_str = video["_id"]
    creator_id = str(video.get("uploaded_by") or "")
    creator = None
    creator_is_subscribed = False
    creator_is_self = False
    if ObjectId.is_valid(creator_id):
        creator = mongo.db.personas.find_one(
            {"_id": ObjectId(creator_id)}, {"name": 1, "avatar_data": 1}
        )
        if creator:
            creator["_id"] = creator_id
            viewer_id = session.get("persona_id", "")
            viewer = (
                mongo.db.personas.find_one(
                    {"_id": ObjectId(viewer_id)}, {"subscribed_creators": 1}
                )
                if ObjectId.is_valid(viewer_id)
                else {}
            ) or {}
            creator_is_self = viewer_id == creator_id
            creator_is_subscribed = creator_id in {
                str(subscribed_id)
                for subscribed_id in viewer.get("subscribed_creators", [])
            }
    watch_progress = mongo.db.watch_history.find_one(
        {"persona_id": session.get("persona_id"), "video_id": video_id_str},
        {"position_seconds": 1},
    ) or {}
    resume_time = max(0.0, float(watch_progress.get("position_seconds", 0) or 0))

    # Comments
    comments = list(
        mongo.db.comments.find({"video_id": video_id_str, "parent_id": None})
        .sort("created_at", -1)
        .limit(50)
    )
    for c in comments:
        c["_id"] = str(c["_id"])
        c["replies"] = _get_replies(str(c["_id"]))
        c["created_at_rel"] = _relative_time(c.get("created_at"))

    # Recommendations (same silo, exclude current)
    recs = list(
        mongo.db.videos.find(
            {"status": "published", "age_silo": {"$in": allowed}, "_id": {"$ne": oid}}
        )
        .sort("views", -1)
        .limit(12)
    )
    _attach_creator_profiles(recs)
    for r in recs:
        r["_id"] = str(r["_id"])
        r["created_at_rel"] = _relative_time(r.get("created_at"))

    # Channel info
    channel = None
    if video.get("channel_id"):
        channel = mongo.db.channels.find_one({"_id": ObjectId(video["channel_id"])})
        if channel:
            channel["_id"] = str(channel["_id"])

    # Playlists for save modal
    persona_id = session.get("persona_id")
    playlists = list(
        mongo.db.playlists.find({"persona_id": persona_id})
    )
    for p in playlists:
        p["_id"] = str(p["_id"])

    # VTT caption path
    vtt_path = None
    if video.get("vtt_file"):
        vtt_path = url_for(
            "user.video_media",
            video_id=video_id_str,
            asset=f"vtt/{video['vtt_file']}",
        )

    return render_template(
        "user/watch.html",
        video=video,
        creator=creator,
        creator_is_subscribed=creator_is_subscribed,
        creator_is_self=creator_is_self,
        comments=comments,
        recs=recs,
        channel=channel,
        playlists=playlists,
        vtt_path=vtt_path,
        age_silo=age_silo,
        resume_time=resume_time,
    )


@user_bp.route("/media/<video_id>/<path:asset>")
@login_required
def video_media(video_id: str, asset: str):
    try:
        oid = ObjectId(video_id)
    except Exception:
        abort(404)

    video = mongo.db.videos.find_one(
        {"_id": oid},
        {"uploaded_by": 1, "status": 1, "age_silo": 1, "original_filename": 1, "thumbnail": 1, "vtt_file": 1, "hls_master": 1},
    )
    if not video:
        abort(404)

    persona_id = session.get("persona_id")
    is_owner = bool(persona_id) and str(video.get("uploaded_by")) == str(persona_id)
    if not is_owner and (
        video.get("status") != "published" or video.get("age_silo") != session.get("age_silo")
    ):
        abort(404)

    allowed_assets = {
        f"uploads/{video.get('original_filename', '')}",
        f"thumbnails/{video.get('thumbnail', '')}",
        f"vtt/{video.get('vtt_file', '')}",
    }
    hls_prefix = f"hls/{video_id}/"
    is_hls_asset = bool(video.get("hls_master")) and asset.startswith(hls_prefix)
    if asset not in allowed_assets or asset.endswith("/"):
        if not is_hls_asset:
            abort(404)

    return send_from_directory(current_app.static_folder, asset, conditional=True)


def _get_replies(comment_id: str):
    replies = list(
        mongo.db.comments.find({"parent_id": comment_id}).sort("created_at", 1).limit(20)
    )
    for r in replies:
        r["_id"] = str(r["_id"])
        r["created_at_rel"] = _relative_time(r.get("created_at"))
    return replies


# ── Search Autocomplete ───────────────────────────────────────────────────────

@user_bp.route("/api/search-suggest")
@login_required
def search_suggest():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    age_silo = session.get("age_silo", "unrestricted")
    results = list(
        mongo.db.videos.find(
            _published_video_search_query(q, age_silo),
            {
                "title": 1,
                "description": 1,
                "category": 1,
                "thumbnail": 1,
                "uploaded_by": 1,
            },
        ).sort("created_at", -1).limit(8)
    )
    suggestions = [
        {
            "id": str(video["_id"]),
            "title": video.get("title") or "Untitled video",
            "category": video.get("category") or "",
            "thumbnail": video.get("thumbnail") or "",
        }
        for video in results
    ]
    _attach_creator_profiles(results)
    for suggestion, video in zip(suggestions, results):
        suggestion["creator_name"] = video["creator_name"]
        suggestion["creator_avatar"] = video["creator_avatar"]
    return jsonify(suggestions)


# ── Video Download ────────────────────────────────────────────────────────────

@user_bp.route("/download/<video_id>")
@login_required
def download_video(video_id: str):
    try:
        oid = ObjectId(video_id)
    except Exception:
        abort(404)
    video = mongo.db.videos.find_one({"_id": oid, "status": "published"})
    if not video:
        abort(404)
    age_silo = session.get("age_silo", "unrestricted")
    if video.get("age_silo") not in _allowed_silos(age_silo):
        abort(403)
    import os
    from flask import current_app
    download_filename = video.get("download_filename")
    if (
        download_filename
        and os.path.basename(download_filename) == download_filename
        and os.path.splitext(download_filename)[1].lower() in {".mp4", ".mov", ".avi"}
    ):
        file_path = os.path.join(
            current_app.config["DOWNLOAD_FOLDER"], download_filename
        )
    else:
        file_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"],
            os.path.basename(video.get("original_filename", "")),
        )
    if not os.path.isfile(file_path) or os.path.islink(file_path):
        abort(404)
    persona_id = str(session.get("persona_id") or "")
    age_silo = session.get("age_silo")
    download_event_id = str(ObjectId())
    app = current_app._get_current_object()
    try:
        create_notification(
            persona_id,
            f"Download started: {video.get('title') or 'Video'}",
            f"download-start:{download_event_id}",
            "download_started",
            f"/downloads",
        )
        record_viewer_activity(
            persona_id,
            age_silo,
            "download",
            oid,
            "started",
        )
    except Exception:
        app.logger.exception("Could not record download start for video %s", video_id)

    response = send_file(
        file_path,
        as_attachment=True,
        download_name=f"{video['title']}{os.path.splitext(file_path)[1] or '.mp4'}",
    )

    def record_download_complete() -> None:
        with app.app_context():
            try:
                create_notification(
                    persona_id,
                    f"Download completed: {video.get('title') or 'Video'}",
                    f"download-complete:{download_event_id}",
                    "download_completed",
                    "/downloads",
                )
                record_viewer_activity(
                    persona_id,
                    age_silo,
                    "download",
                    oid,
                    "downloaded",
                )
            except Exception:
                app.logger.exception(
                    "Could not record download completion for video %s", video_id
                )

    response.call_on_close(record_download_complete)
    return response


# ── Notifications ────────────────────────────────────────────────────────────

@user_bp.route("/notifications")
@login_required
def notifications():
    persona_id = session.get("persona_id")
    notifs = list(
        mongo.db.notifications.find({"persona_id": persona_id})
        .sort("created_at", -1)
        .limit(20)
    )
    for n in notifs:
        n["_id"] = str(n["_id"])
    return render_template("user/notifications.html", notifications=notifs)


# ── History, Liked, Saved ─────────────────────────────────────────────────────

@user_bp.route("/history")
@login_required
def history():
    persona_id = session.get("persona_id")
    records = list(
        mongo.db.watch_history.find({"persona_id": persona_id})
        .sort("watched_at", -1)
        .limit(50)
    )
    video_ids = [ObjectId(r["video_id"]) for r in records if ObjectId.is_valid(r.get("video_id", ""))]
    age_silo = session.get("age_silo")
    videos = {
        str(v["_id"]): v
        for v in mongo.db.videos.find(
            {
                "_id": {"$in": video_ids},
                "status": "published",
                "age_silo": age_silo,
            }
        )
    }
    _attach_creator_profiles(list(videos.values()))
    for v in videos.values():
        v["_id"] = str(v["_id"])
    return render_template(
        "user/history.html",
        records=records,
        videos=videos,
    )


@user_bp.route("/liked")
@login_required
def liked():
    persona_id = session.get("persona_id")
    account = mongo.db.accounts.find_one({"_id": ObjectId(session.get("account_id"))})
    liked_ids = [ObjectId(vid) for vid in (account or {}).get("liked_videos", []) if ObjectId.is_valid(vid)]
    videos = list(
        mongo.db.videos.find(
            {
                "_id": {"$in": liked_ids},
                "status": "published",
                "age_silo": session.get("age_silo"),
            }
        )
    )
    _attach_creator_profiles(videos)
    for v in videos:
        v["_id"] = str(v["_id"])
    return render_template("user/liked.html", videos=videos)


@user_bp.route("/playlists")
@login_required
def playlists():
    persona_id = session.get("persona_id")
    pls = list(mongo.db.playlists.find({"persona_id": persona_id}))
    for p in pls:
        p["_id"] = str(p["_id"])
    return render_template("user/playlists.html", playlists=pls)


@user_bp.route("/playlist/<playlist_id>")
@login_required
def playlist_detail(playlist_id: str):
    try:
        oid = ObjectId(playlist_id)
    except Exception:
        abort(404)
    persona_id = session.get("persona_id")
    pl = mongo.db.playlists.find_one({"_id": oid, "persona_id": persona_id})
    if not pl:
        abort(404)
    pl["_id"] = str(pl["_id"])

    age_silo = session.get("age_silo", "unrestricted")
    allowed = _allowed_silos(age_silo)
    vid_ids = [ObjectId(v) for v in pl.get("video_ids", []) if ObjectId.is_valid(v)]
    videos = list(
        mongo.db.videos.find(
            {"_id": {"$in": vid_ids}, "status": "published", "age_silo": {"$in": allowed}}
        )
    )
    _attach_creator_profiles(videos)
    for v in videos:
        v["_id"] = str(v["_id"])
        v["created_at_rel"] = _relative_time(v.get("created_at"))

    return render_template("user/playlist_detail.html", playlist=pl, videos=videos)


# ── Utility ───────────────────────────────────────────────────────────────────

def _relative_time(dt: datetime | None) -> str:
    if not dt:
        return ""
    delta = datetime.utcnow() - dt
    if delta.days >= 365:
        return f"{delta.days // 365}y ago"
    if delta.days >= 30:
        return f"{delta.days // 30}mo ago"
    if delta.days >= 1:
        return f"{delta.days}d ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h ago"
    minutes = delta.seconds // 60
    return f"{minutes}m ago"


# ── Watch Later ───────────────────────────────────────────────────────────────

@user_bp.route("/watch-later")
@login_required
def watch_later():
    persona_id = session.get("persona_id")
    wl = mongo.db.watch_later.find_one({"persona_id": persona_id}) or {}
    video_ids = [ObjectId(v) for v in wl.get("video_ids", []) if ObjectId.is_valid(v)]
    videos = list(
        mongo.db.videos.find(
            {
                "_id": {"$in": video_ids},
                "status": "published",
                "age_silo": session.get("age_silo"),
            }
        )
    )
    _attach_creator_profiles(videos)
    for v in videos:
        v["_id"] = str(v["_id"])
        v["created_at_rel"] = _relative_time(v.get("created_at"))
    return render_template("user/watch_later.html", videos=videos)


@user_bp.route("/downloads")
@login_required
def downloads():
    persona_id = session.get("persona_id")
    if not persona_id:
        abort(403)

    records = mongo.db.viewer_activity.find(
        {
            "persona_id": persona_id,
            "event_type": "download",
            "action": "downloaded",
        }
    ).sort("occurred_at", -1).limit(200)
    video_ids = []
    seen_ids = set()
    for record in records:
        video_id = record.get("video_id", "")
        if ObjectId.is_valid(video_id) and video_id not in seen_ids:
            video_ids.append(ObjectId(video_id))
            seen_ids.add(video_id)

    allowed = _allowed_silos(session.get("age_silo"))
    videos_by_id = {
        str(video["_id"]): video
        for video in mongo.db.videos.find(
            {
                "_id": {"$in": video_ids},
                "status": "published",
                "age_silo": {"$in": allowed},
            }
        )
    }
    videos = [videos_by_id[str(video_id)] for video_id in video_ids if str(video_id) in videos_by_id]
    _attach_creator_profiles(videos)
    for video in videos:
        video["_id"] = str(video["_id"])
        video["created_at_rel"] = _relative_time(video.get("created_at"))

    return render_template(
        "user/liked.html",
        videos=videos,
        page_title="Downloaded Videos",
        page_icon="download",
        empty_message="Videos you download will appear here.",
    )


# ── Channel Page ─────────────────────────────────────────────────────────────

@user_bp.route("/creator/<persona_id>")
@login_required
def creator_profile(persona_id: str):
    if not ObjectId.is_valid(persona_id):
        abort(404)

    creator = mongo.db.personas.find_one(
        {"_id": ObjectId(persona_id)}, {"name": 1, "avatar_data": 1}
    )
    if not creator:
        abort(404)
    creator["_id"] = str(creator["_id"])

    viewer_id = session.get("persona_id", "")
    viewer = (
        mongo.db.personas.find_one(
            {"_id": ObjectId(viewer_id)}, {"subscribed_creators": 1}
        )
        if ObjectId.is_valid(viewer_id)
        else {}
    ) or {}
    is_own_profile = viewer_id == creator["_id"]
    is_subscribed = creator["_id"] in {
        str(subscribed_id)
        for subscribed_id in viewer.get("subscribed_creators", [])
    }

    age_silo = session.get("age_silo", "unrestricted")
    allowed = _allowed_silos(age_silo)
    videos = list(
        mongo.db.videos.find(
            {
                "uploaded_by": creator["_id"],
                "status": "published",
                "age_silo": {"$in": allowed},
            }
        )
        .sort("created_at", -1)
    )
    for video in videos:
        video["_id"] = str(video["_id"])
        video["created_at_rel"] = _relative_time(video.get("created_at"))

    return render_template(
        "user/creator_profile.html",
        creator=creator,
        videos=videos,
        age_silo=age_silo,
        is_own_profile=is_own_profile,
        is_subscribed=is_subscribed,
    )


@user_bp.route("/channel/<channel_id>")
@login_required
def channel(channel_id: str):
    try:
        cid = ObjectId(channel_id)
    except Exception:
        abort(404)

    ch = mongo.db.channels.find_one({"_id": cid})
    if not ch:
        abort(404)
    ch["_id"] = str(ch["_id"])

    age_silo = session.get("age_silo", "unrestricted")
    allowed = _allowed_silos(age_silo)
    videos = list(
        mongo.db.videos.find(
            {"channel_id": channel_id, "status": "published", "age_silo": {"$in": allowed}}
        ).sort("created_at", -1).limit(24)
    )
    for v in videos:
        v["_id"] = str(v["_id"])
        v["created_at_rel"] = _relative_time(v.get("created_at"))

    # Is current persona subscribed?
    persona_id = session.get("persona_id")
    is_subscribed = persona_id in [str(s) for s in ch.get("subscribers", [])]

    return render_template(
        "user/channel.html",
        channel=ch,
        videos=videos,
        is_subscribed=is_subscribed,
    )


# ── Notification Count (AJAX badge) ──────────────────────────────────────────

@user_bp.route("/api/notification-count")
@login_required
def notification_count():
    persona_id = session.get("persona_id")
    account_id = session.get("account_id")
    account = (
        mongo.db.accounts.find_one(
            {"_id": ObjectId(account_id)},
            {"settings.notifications_enabled": 1},
        )
        if ObjectId.is_valid(str(account_id or ""))
        else None
    )
    if not ((account or {}).get("settings") or {}).get("notifications_enabled", True):
        return jsonify({"count": 0})
    count = mongo.db.notifications.count_documents(
        {"persona_id": persona_id, "read": {"$ne": True}}
    )
    return jsonify({"count": count})


# ── Mark Notifications Read ───────────────────────────────────────────────────

@user_bp.route("/api/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    """Mark all notifications as read for the current persona."""
    persona_id = session.get("persona_id")
    mongo.db.notifications.update_many(
        {"persona_id": persona_id, "read": {"$ne": True}},
        {"$set": {"read": True}},
    )
    return jsonify({"ok": True})


@user_bp.route("/history/clear", methods=["POST"])
@login_required
def clear_history():
    persona_id = session.get("persona_id")
    if not persona_id:
        return jsonify({"ok": False, "error": "Viewer profile not found."}), 403
    mongo.db.watch_history.delete_many({"persona_id": persona_id})
    return jsonify({"ok": True, "message": "Watch history deleted."})


# ── Session check (idle / timeout guard) ─────────────────────────────────────

@user_bp.route("/api/session-check")
def session_check():
    """Returns login state — used by the client-side idle timeout guard."""
    return jsonify({"logged_in": bool(session.get("logged_in"))})
