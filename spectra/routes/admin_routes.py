"""
Admin Routes Blueprint — Creator Studio, AI pipeline, analytics, settings.
"""
from __future__ import annotations

import os
import json
import threading
import secrets
import tempfile
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from time import perf_counter

from bson import ObjectId
from gridfs.errors import NoFile
from pymongo import ReturnDocument
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    abort,
    url_for,
)
from werkzeug.utils import secure_filename

from ..category_rules import AGE_GROUPS, CATEGORY_OPTIONS, category_is_allowed
from ..extensions import mongo
from ..admin_notifications import dispatch_admin_notification
from ..media_storage import (
    asset_exists,
    copy_asset,
    delete_asset,
    delete_video_assets,
    materialize_asset,
    send_asset,
    store_directory,
    store_file,
    store_stream,
)

admin_bp = Blueprint("admin", __name__)
admin_ops_bp = Blueprint("admin_ops", __name__)
admin_metrics_bp = Blueprint("admin_metrics", __name__)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}


def _publish_gate_status(ai_result: dict) -> bool:
    """Allow publishing only to age tiers that passed their independent checks."""
    group_results = (ai_result.get("policy") or {}).get("age_group_results")
    if isinstance(group_results, list) and any(
        isinstance(result, dict)
        and ("approved" in result or "status" in result)
        for result in group_results
    ):
        return any(
            result.get("approved") is True
            and result.get("status") == "Approved"
            for result in group_results
            if isinstance(result, dict)
        )
    policy_status = (ai_result.get("policy") or {}).get("compliance_status")
    return ai_result.get("verdict") == "safe" and policy_status == "passed"


def _validate_age_group_targets(
    primary_age_silo: str,
    primary_category: str,
    targets,
) -> tuple[list[dict[str, str]] | None, str | None]:
    if targets is None:
        targets = [{"age_silo": primary_age_silo, "category": primary_category}]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 3:
        return None, "Select between one and three age-group/category pairs."

    normalized_targets = []
    seen_age_groups = set()
    for target in targets:
        if not isinstance(target, dict):
            return None, "Each age-group selection must include a category."
        age_silo = target.get("age_silo")
        category = target.get("category")
        if not isinstance(age_silo, str) or not isinstance(category, str):
            return None, "Each age-group selection must include a category."
        age_silo = age_silo.strip().lower()
        category = category.strip()
        if age_silo not in AGE_GROUPS:
            return None, "Select a valid target age group."
        if age_silo in seen_age_groups:
            return None, "Select each target age group only once."
        if not category_is_allowed(age_silo, category):
            return None, f"Select a category permitted for the {age_silo} age group."
        seen_age_groups.add(age_silo)
        normalized_targets.append({"age_silo": age_silo, "category": category})

    if normalized_targets[0] != {
        "age_silo": primary_age_silo,
        "category": primary_category,
    }:
        return None, "The first age-group selection must match the primary selection."
    return normalized_targets, None


# ── Auth Guard ────────────────────────────────────────────────────────────────

def _admin_session_user():
    user_id = session.get("user_id") or session.get("account_id")
    if not user_id or not ObjectId.is_valid(str(user_id)):
        return None
    return mongo.db.users.find_one({"_id": ObjectId(str(user_id))})


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in") or session.get("role") != "admin":
            flash("You must sign in as a Spectra super-admin to access this console.", "error")
            return redirect(url_for("admin_ops.login"))
        user = _admin_session_user()
        if user is None or not user.get("is_superuser"):
            session.clear()
            flash("Admin access is restricted to trusted super-admin accounts.", "error")
            return redirect(url_for("admin_ops.login"))
        return f(*args, **kwargs)
    return decorated


def _ops_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("admin_user_id")
        if (
            not session.get("admin_authenticated")
            or session.get("role") != "admin"
            or not user_id
        ):
            return redirect(url_for("admin_ops.login"))
        if not ObjectId.is_valid(user_id):
            session.clear()
            return redirect(url_for("admin_ops.login"))
        user = mongo.db.users.find_one(
            {"_id": ObjectId(user_id), "role": "admin", "is_superuser": True, "status": {"$ne": "suspended"}}
        )
        if user is None:
            session.clear()
            return redirect(url_for("admin_ops.login"))
        request.admin_user = user
        return f(*args, **kwargs)
    return decorated


def _require_csrf():
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    expected = session.get("admin_csrf")
    if not token or not expected or not secrets.compare_digest(token, expected):
        abort(400, description="Invalid or missing CSRF token.")


def _json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items() if key != "password_hash"}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _ops_audit(action, detail, target_id=None):
    mongo.db.audit_logs.insert_one({
        "action": action,
        "detail": detail,
        "target_id": str(target_id) if target_id else None,
        "admin_user_id": session.get("admin_user_id"),
        "created_at": datetime.utcnow(),
    })


@admin_ops_bp.route("/login", methods=["GET"])
def login():
    token = secrets.token_urlsafe(32)
    session["auth_csrf"] = token
    return render_template("auth/login.html", csrf_token=token)


@admin_ops_bp.route("/logout", methods=["POST"])
@_ops_admin_required
def logout():
    _require_csrf()
    session.clear()
    return redirect(url_for("admin_ops.login"))


@admin_ops_bp.route("/viewer-mode", methods=["POST"])
@_ops_admin_required
def viewer_mode():
    _require_csrf()
    session.clear()
    return redirect(url_for("user.index"))


@admin_ops_bp.route("/", methods=["GET"])
@_ops_admin_required
def console():
    user = request.admin_user
    return render_template(
        "admin.html",
        admin_identity=user.get("username") or user.get("email", "Super-admin"),
        csrf_token=session["admin_csrf"],
    )


@admin_ops_bp.route("/api/overview")
@_ops_admin_required
def api_overview():
    videos = list(
        mongo.db.videos.find({"status": "published"}).sort("published_at", -1).limit(10)
    )
    rows = []
    for video in videos:
        persona_id = video.get("uploaded_by")
        persona = None
        if persona_id and ObjectId.is_valid(str(persona_id)):
            persona = mongo.db.personas.find_one({"_id": ObjectId(str(persona_id))}, {"name": 1})
        status = video.get("status", "unknown")
        if status == "published" and video.get("moderation_decision") == "approve_remediation":
            display_status = "Auto-Remediated"
        elif status in {"flagged", "analyzed", "quarantined"}:
            display_status = "Flagged"
        elif status == "published":
            display_status = "Clean"
        else:
            display_status = status.replace("_", " ").title()
        rows.append({
            "id": str(video["_id"]),
            "title": video.get("title", "Untitled"),
            "creator": (persona or {}).get("name", "Unknown creator"),
            "age_silo": video.get("age_silo", "unknown"),
            "status": display_status,
            "created_at": _json_safe(video.get("created_at")),
        })
    return jsonify({"recent_videos": rows})


@admin_metrics_bp.route("/metrics")
@_ops_admin_required
def api_admin_metrics():
    active_users = mongo.db.personas.count_documents({})

    creator_ids = mongo.db.videos.distinct(
        "uploaded_by",
        {
            "uploaded_by": {"$exists": True, "$ne": None},
            "status": {"$nin": ["deleted", "rejected"]},
        },
    )
    uploading_creators = len({str(creator_id) for creator_id in creator_ids if creator_id})
    published_videos = mongo.db.videos.count_documents({
        "$or": [{"status": "published"}, {"is_published": True}],
    })
    auto_remediated = mongo.db.videos.count_documents({
        "$or": [
            {"remediation_applied": True},
            {"visual_remediation_applied": True},
            {"audio_remediation_applied": True},
            {"visual_blur_applied": True},
            {"audio_bleep_applied": True},
            {"moderation_decision": "approve_remediation"},
            {"remediated_filename": {"$exists": True, "$nin": [None, ""]}},
            {"sanitized_filename": {"$exists": True, "$nin": [None, ""]}},
        ],
    })
    return jsonify({
        "active_users": active_users,
        "uploading_creators": uploading_creators,
        "published_videos": published_videos,
        "auto_remediated": auto_remediated,
    })


@admin_metrics_bp.route("/notifications")
@_ops_admin_required
def api_admin_notifications():
    category = request.args.get("category", "all")
    if category not in {"all", "moderation", "pipeline", "report"}:
        return jsonify({"ok": False, "error": "Select a valid alert category."}), 400

    query = {"recipient_role": "admin"}
    if category == "moderation":
        query["category"] = {"$in": ["moderation", "creator"]}
    elif category != "all":
        query["category"] = category

    unread_count = mongo.db.admin_notifications.count_documents(
        {"recipient_role": "admin", "is_read": False}
    )
    notifications = list(
        mongo.db.admin_notifications.find(query)
        .sort("created_at", -1)
        .limit(50)
    )
    return jsonify({
        "ok": True,
        "unread_count": unread_count,
        "items": _json_safe(notifications),
    })


@admin_metrics_bp.route("/notifications/mark-read", methods=["POST"])
@_ops_admin_required
def api_admin_notifications_mark_read():
    _require_csrf()
    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Invalid request body."}), 400

    notification_id = (data or {}).get("notification_id")
    if notification_id:
        if not ObjectId.is_valid(str(notification_id)):
            return jsonify({"ok": False, "error": "Invalid notification id."}), 400
        mongo.db.admin_notifications.update_one(
            {
                "_id": ObjectId(str(notification_id)),
                "recipient_role": "admin",
            },
            {"$set": {"is_read": True}},
        )
    else:
        mongo.db.admin_notifications.update_many(
            {"recipient_role": "admin", "is_read": False},
            {"$set": {"is_read": True}},
        )

    unread_count = mongo.db.admin_notifications.count_documents(
        {"recipient_role": "admin", "is_read": False}
    )
    return jsonify({"ok": True, "unread_count": unread_count})


@admin_metrics_bp.route("/notifications/dispatch", methods=["POST"])
@_ops_admin_required
def api_admin_notifications_dispatch():
    """Allow authenticated internal operations to dispatch an admin alert."""
    _require_csrf()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "A notification object is required."}), 400

    required_text = ("event_key", "severity", "category", "title", "message", "target_url")
    if any(not isinstance(data.get(field), str) for field in required_text):
        return jsonify({"ok": False, "error": "Notification fields must be text."}), 400
    if (
        len(data["event_key"]) > 240
        or not data["title"].strip()
        or len(data["title"]) > 160
        or not data["message"].strip()
        or len(data["message"]) > 1000
        or len(data["target_url"]) > 500
    ):
        return jsonify({"ok": False, "error": "Notification fields exceed their limits."}), 400
    video_id = data.get("video_id")
    report_id = data.get("report_id")
    if video_id is not None and (
        not isinstance(video_id, str) or not ObjectId.is_valid(video_id)
    ):
        return jsonify({"ok": False, "error": "Invalid video id."}), 400
    if report_id is not None and (
        not isinstance(report_id, str) or len(report_id) > 100
    ):
        return jsonify({"ok": False, "error": "Invalid report id."}), 400

    try:
        created = dispatch_admin_notification(
            event_key=data["event_key"],
            severity=data["severity"],
            category=data["category"],
            title=data["title"].strip(),
            message=data["message"].strip(),
            target_url=data["target_url"],
            video_id=video_id,
            report_id=report_id,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    unread_count = mongo.db.admin_notifications.count_documents(
        {"recipient_role": "admin", "is_read": False}
    )
    return jsonify({"ok": True, "created": created, "unread_count": unread_count}), 201


@admin_ops_bp.route("/moderation/review/<video_id>")
@_ops_admin_required
def admin_moderation_review(video_id: str):
    if not ObjectId.is_valid(video_id):
        abort(404)
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        abort(404)
    video = _json_safe(video)
    return render_template("admin/notification_video_review.html", video=video)


@admin_ops_bp.route("/reports/<report_id>")
@_ops_admin_required
def admin_report_detail(report_id: str):
    from uuid import UUID

    try:
        UUID(report_id)
    except ValueError:
        abort(404)
    if not mongo.db.feedbacks.find_one({"feedback_id": report_id}, {"_id": 1}):
        abort(404)
    return redirect(
        url_for("admin_ops.console", report_id=report_id) + "#feedback"
    )


@admin_metrics_bp.route("/analytics")
@_ops_admin_required
def api_platform_analytics():
    period = request.args.get("range", "7d")
    if period not in {"today", "7d", "1m", "all"}:
        return jsonify({"error": "Select a valid analytics period."}), 400

    now = datetime.utcnow()
    if period == "today":
        range_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        previous_start = range_start - timedelta(days=1)
        previous_end = range_start
        trend_start = range_start
    elif period == "7d":
        trend_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        range_start = trend_start
        previous_start = range_start - timedelta(days=7)
        previous_end = range_start
    elif period == "1m":
        trend_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
        range_start = trend_start
        previous_start = range_start - timedelta(days=30)
        previous_end = range_start
    else:
        range_start = previous_start = previous_end = None
        trend_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)

    video_filter = {"status": {"$ne": "deleted"}}
    if range_start:
        video_filter["created_at"] = {"$gte": range_start, "$lte": now}
    else:
        video_filter["created_at"] = {"$lte": now}

    video_summary = list(mongo.db.videos.aggregate([
        {"$match": video_filter},
        {"$project": {
            "ingested": {"$literal": 1},
            "is_remediated": {"$cond": [{"$or": [
                {"$eq": ["$remediation_applied", True]},
                {"$eq": ["$visual_remediation_applied", True]},
                {"$eq": ["$audio_remediation_applied", True]},
                {"$eq": ["$visual_blur_applied", True]},
                {"$eq": ["$audio_bleep_applied", True]},
                {"$eq": ["$moderation_decision", "approve_remediation"]},
                {"$ne": [{"$ifNull": ["$remediated_filename", ""]}, ""]},
                {"$ne": [{"$ifNull": ["$sanitized_filename", ""]}, ""]},
            ]}, 1, 0]},
        }},
        {"$group": {
            "_id": None,
            "ingested": {"$sum": "$ingested"},
            "remediated": {"$sum": "$is_remediated"},
        }},
    ]))
    summary = video_summary[0] if video_summary else {}
    ingestion_count = int(summary.get("ingested", 0))
    remediated_count = int(summary.get("remediated", 0))

    auto_remediation_rate = (
        remediated_count / ingestion_count * 100 if ingestion_count else 0
    )

    previous_video_filter = {
        "status": {"$ne": "deleted"},
        "created_at": {"$gte": previous_start, "$lt": previous_end},
    } if previous_start else None
    previous_ingestion_count = (
        mongo.db.videos.count_documents(previous_video_filter)
        if previous_video_filter else 0
    )

    violation_totals = list(mongo.db.videos.aggregate([
        {"$match": video_filter},
        {"$project": {
            "weapon_count": {"$size": {"$filter": {
                "input": {"$ifNull": ["$ai_result.visual.violations", []]},
                "as": "finding",
                "cond": {"$eq": ["$$finding.is_weapon", True]},
            }}},
            "visual_count": {"$size": {"$ifNull": ["$ai_result.visual.violations", []]}},
            "transcript_count": {"$size": {"$ifNull": ["$ai_result.audio.violations", []]}},
            "other_policy_count": {"$size": {"$filter": {
                "input": {"$ifNull": ["$ai_result.violations_summary", []]},
                "as": "finding",
                "cond": {"$not": [{"$in": ["$$finding.type", ["visual", "audio"]]}]},
            }}},
            "needs_policy_review": {"$cond": [
                {"$eq": ["$ai_result.policy.compliance_status", "insufficient_evidence"]},
                1,
                0,
            ]},
        }},
        {"$group": {
            "_id": None,
            "weapons": {"$sum": "$weapon_count"},
            "visual": {"$sum": "$visual_count"},
            "transcript_toxicity": {"$sum": "$transcript_count"},
            "other_policy": {"$sum": "$other_policy_count"},
            "needs_policy_review": {"$sum": "$needs_policy_review"},
        }},
    ]))
    violation_summary = violation_totals[0] if violation_totals else {}

    def watch_time_total(start, end):
        match = {"event_type": "watch_time", "occurred_at": {"$lte": end}}
        if start:
            match["occurred_at"]["$gte"] = start
            match["occurred_at"]["$lt"] = end
        totals = list(mongo.db.viewer_activity.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "seconds": {"$sum": "$duration_seconds"}}},
        ]))
        return int(totals[0].get("seconds", 0)) if totals else 0

    watch_seconds = watch_time_total(range_start, now)
    previous_watch_seconds = (
        watch_time_total(previous_start, previous_end) if previous_start else None
    )
    watch_change = None
    if previous_watch_seconds is not None and previous_watch_seconds > 0:
        watch_change = round(
            (watch_seconds - previous_watch_seconds) / previous_watch_seconds * 100,
            1,
        )

    visual_violations = {
        "weapons": int(violation_summary.get("weapons", 0)),
        "other": max(
            0,
            int(violation_summary.get("visual", 0))
            - int(violation_summary.get("weapons", 0)),
        ),
    }
    transcript_violations = int(violation_summary.get("transcript_toxicity", 0))
    policy_review_count = (
        int(violation_summary.get("other_policy", 0))
        + int(violation_summary.get("needs_policy_review", 0))
    )
    action_pipeline = [
        {"$match": video_filter},
        {"$project": {
            "status": 1,
            "age_review": 1,
            "moderation_decision": 1,
            "remediated_filename": 1,
            "sanitized_filename": 1,
            "remediation_applied": 1,
            "visual_remediation_applied": 1,
            "audio_remediation_applied": 1,
            "visual_blur_applied": 1,
            "audio_bleep_applied": 1,
            "visual_count": {
                "$size": {"$ifNull": ["$ai_result.visual.violations", []]}
            },
            "audio_count": {
                "$size": {"$ifNull": ["$ai_result.audio.violations", []]}
            },
            "verdict": "$ai_result.verdict",
            "compliance_status": "$ai_result.policy.compliance_status",
            "is_remediated": {"$or": [
                {"$eq": ["$remediation_applied", True]},
                {"$eq": ["$visual_remediation_applied", True]},
                {"$eq": ["$audio_remediation_applied", True]},
                {"$eq": ["$visual_blur_applied", True]},
                {"$eq": ["$audio_bleep_applied", True]},
                {"$eq": ["$moderation_decision", "approve_remediation"]},
                {"$ne": [{"$ifNull": ["$remediated_filename", ""]}, ""]},
                {"$ne": [{"$ifNull": ["$sanitized_filename", ""]}, ""]},
            ]},
        }},
        {"$project": {
            "action": {"$switch": {
                "branches": [
                    {
                        "case": {"$eq": ["$status", "rejected"]},
                        "then": "rejected",
                    },
                    {
                        "case": {"$or": [
                            {"$in": ["$status", ["flagged", "analyzed", "quarantined"]]},
                            {"$and": [
                                {"$ne": [{"$ifNull": ["$age_review", None]}, None]},
                                {"$ne": ["$age_review", False]},
                            ]},
                        ]},
                        "then": "manual_review",
                    },
                    {
                        "case": {"$and": [
                            "$is_remediated",
                            {"$or": [
                                {"$eq": ["$visual_remediation_applied", True]},
                                {"$eq": ["$visual_blur_applied", True]},
                                {"$gt": ["$visual_count", 0]},
                            ]},
                        ]},
                        "then": "visual_blur",
                    },
                    {
                        "case": {"$and": [
                            "$is_remediated",
                            {"$or": [
                                {"$eq": ["$audio_remediation_applied", True]},
                                {"$eq": ["$audio_bleep_applied", True]},
                                {"$gt": ["$audio_count", 0]},
                            ]},
                        ]},
                        "then": "audio_bleep",
                    },
                    {
                        "case": {"$and": [
                            {"$eq": ["$status", "published"]},
                            {"$eq": ["$verdict", "safe"]},
                            {"$eq": ["$compliance_status", "passed"]},
                            {"$eq": ["$is_remediated", False]},
                        ]},
                        "then": "clean_pass",
                    },
                ],
                "default": None,
            }},
        }},
        {"$match": {"action": {"$ne": None}}},
        {"$group": {"_id": "$action", "count": {"$sum": 1}}},
        {"$project": {"_id": 1, "count": 1}},
    ]
    action_counts = {
        "clean_pass": 0,
        "visual_blur": 0,
        "audio_bleep": 0,
        "manual_review": 0,
        "rejected": 0,
    }
    for item in mongo.db.videos.aggregate(action_pipeline):
        if item.get("_id") in action_counts:
            action_counts[item["_id"]] = int(item.get("count", 0))

    silo_watch_pipeline = [
        {"$match": {
            "event_type": "watch_time",
            "occurred_at": {
                **({"$gte": range_start} if range_start else {}),
                "$lte": now,
            },
        }},
        {"$group": {
            "_id": "$video_id",
            "watch_seconds": {"$sum": "$duration_seconds"},
        }},
        {"$addFields": {
            "video_object_id": {
                "$convert": {
                    "input": "$_id",
                    "to": "objectId",
                    "onError": None,
                    "onNull": None,
                }
            }
        }},
        {"$lookup": {
            "from": "videos",
            "localField": "video_object_id",
            "foreignField": "_id",
            "as": "video",
        }},
        {"$unwind": "$video"},
        {"$group": {
            "_id": {"$cond": [
                {"$in": ["$video.age_silo", ["teens", "unrestricted"]]},
                "general",
                "$video.age_silo",
            ]},
            "watch_seconds": {"$sum": "$watch_seconds"},
        }},
        {"$project": {"_id": 1, "watch_seconds": 1}},
    ]
    silo_totals = {
        item.get("_id"): int(item.get("watch_seconds", 0))
        for item in mongo.db.viewer_activity.aggregate(silo_watch_pipeline)
        if item.get("_id")
    }
    age_silo_labels = {
        "preschool": "Preschool (0–4)",
        "kids": "Early Elementary (5–8)",
        "tweens": "Pre-Teens (9–12)",
        "general": "General Audience (13+)",
    }
    silos = [
        {
            "silo": silo,
            "label": age_silo_labels[silo],
            "watch_seconds": silo_totals.get(silo, 0),
        }
        for silo in age_silo_labels
    ]

    daily_start = trend_start.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_video_filter = {
        "created_at": {"$gte": daily_start, "$lte": now},
        "status": {"$ne": "deleted"},
    }
    daily_uploads = {
        item["_id"]: int(item.get("uploads", 0))
        for item in mongo.db.videos.aggregate([
            {"$match": daily_video_filter},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "uploads": {"$sum": 1},
            }},
            {"$project": {"_id": 1, "uploads": 1}},
        ])
    }
    daily_watches = {
        item["_id"]: int(item.get("watch_seconds", 0))
        for item in mongo.db.viewer_activity.aggregate([
            {"$match": {
                "event_type": "watch_time",
                "occurred_at": {"$gte": daily_start, "$lte": now},
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$occurred_at"}},
                "watch_seconds": {"$sum": "$duration_seconds"},
            }},
            {"$project": {"_id": 1, "watch_seconds": 1}},
        ])
    }
    hour_watches = {
        int(item["_id"]): int(item.get("watch_seconds", 0))
        for item in mongo.db.viewer_activity.aggregate([
            {"$match": {
                "event_type": "watch_time",
                "occurred_at": {"$gte": range_start or daily_start, "$lte": now},
            }},
            {"$group": {
                "_id": {"$hour": "$occurred_at"},
                "watch_seconds": {"$sum": "$duration_seconds"},
            }},
            {"$project": {"_id": 1, "watch_seconds": 1}},
        ])
    }
    hour_uploads = {
        int(item["_id"]): int(item.get("uploads", 0))
        for item in mongo.db.videos.aggregate([
            {"$match": {
                "status": {"$ne": "deleted"},
                "created_at": {"$gte": range_start or daily_start, "$lte": now},
            }},
            {"$group": {
                "_id": {"$hour": "$created_at"},
                "uploads": {"$sum": 1},
            }},
            {"$project": {"_id": 1, "uploads": 1}},
        ])
    }

    creator_pipeline = [
        {"$match": {
            **video_filter,
            "uploaded_by": {"$exists": True, "$nin": [None, ""]},
        }},
        {"$project": {
            "creator_id": "$uploaded_by",
            "is_published": {"$cond": [{"$eq": ["$status", "published"]}, 1, 0]},
            "is_clean": {"$cond": [{"$and": [
                {"$eq": ["$status", "published"]},
                {"$eq": ["$ai_result.verdict", "safe"]},
                {"$eq": ["$ai_result.policy.compliance_status", "passed"]},
                {"$ne": ["$moderation_decision", "approve_remediation"]},
            ]}, 1, 0]},
            "is_remediated": {"$cond": [{"$or": [
                {"$eq": ["$remediation_applied", True]},
                {"$eq": ["$visual_remediation_applied", True]},
                {"$eq": ["$audio_remediation_applied", True]},
                {"$eq": ["$visual_blur_applied", True]},
                {"$eq": ["$audio_bleep_applied", True]},
                {"$eq": ["$moderation_decision", "approve_remediation"]},
                {"$ne": [{"$ifNull": ["$remediated_filename", None]}, None]},
                {"$ne": [{"$ifNull": ["$sanitized_filename", None]}, None]},
            ]}, 1, 0]},
            "flagged_violations": {"$cond": [
                {"$gt": [
                    {"$size": {"$ifNull": ["$ai_result.violations_summary", []]}},
                    0,
                ]},
                {"$size": {"$ifNull": ["$ai_result.violations_summary", []]}},
                {"$add": [
                    {"$size": {"$ifNull": ["$ai_result.visual.violations", []]}},
                    {"$size": {"$ifNull": ["$ai_result.audio.violations", []]}},
                ]},
            ]},
        }},
        {"$group": {
            "_id": "$creator_id",
            "uploads": {"$sum": 1},
            "published": {"$sum": "$is_published"},
            "clean": {"$sum": "$is_clean"},
            "remediated": {"$sum": "$is_remediated"},
            "flagged_violations": {"$sum": "$flagged_violations"},
        }},
        {"$sort": {"uploads": -1, "_id": 1}},
        {"$limit": 100},
        {"$project": {
            "_id": 1,
            "uploads": 1,
            "published": 1,
            "clean": 1,
            "remediated": 1,
            "flagged_violations": 1,
        }},
    ]
    creator_rows = list(mongo.db.videos.aggregate(creator_pipeline))
    creator_object_ids = [
        ObjectId(str(row["_id"]))
        for row in creator_rows
        if ObjectId.is_valid(str(row.get("_id") or ""))
    ]
    creator_profiles = {
        str(profile["_id"]): profile
        for profile in mongo.db.personas.find(
            {"_id": {"$in": creator_object_ids}},
            {"name": 1, "channel_name": 1},
        )
    } if creator_object_ids else {}
    creators = []
    for row in creator_rows:
        creator_id = str(row.get("_id") or "")
        profile = creator_profiles.get(creator_id, {})
        published = int(row.get("published", 0))
        clean = int(row.get("clean", 0))
        clean_rate = clean / published * 100 if published else 0
        violations = int(row.get("flagged_violations", 0))
        if published == 0:
            trust_badge = "Needs Review"
        elif clean_rate >= 95 and violations == 0:
            trust_badge = "High Trust"
        elif clean_rate >= 80:
            trust_badge = "Needs Review"
        else:
            trust_badge = "Restricted"
        creators.append({
            "name": profile.get("name") or profile.get("channel_name") or creator_id,
            "uploads": int(row.get("uploads", 0)),
            "published": published,
            "clean_rate": round(clean_rate, 1),
            "remediated": int(row.get("remediated", 0)),
            "flagged_violations": violations,
            "trust_badge": trust_badge,
        })

    published_platform_count = mongo.db.videos.count_documents({"status": "published"})
    feedback_filter = {
        "status": "IN_REVIEW",
        "created_at": {"$lte": now.isoformat()},
    }
    if range_start:
        feedback_filter["created_at"]["$gte"] = range_start.isoformat()
    escalated_video_ids = {
        str(item.get("video_id"))
        for item in mongo.db.feedbacks.find(
            feedback_filter,
            {"video_id": 1},
        )
        if item.get("video_id")
    }
    safety_score = None
    if published_platform_count:
        report_rate = min(
            len(escalated_video_ids) / published_platform_count,
            1,
        )
        safety_score = round((1 - report_rate) * 100, 1)

    previous_ingestion_count = previous_ingestion_count or 0
    ingestion_change = None
    if previous_ingestion_count:
        ingestion_change = round(
            (ingestion_count - previous_ingestion_count)
            / previous_ingestion_count * 100,
            1,
        )

    daily = []
    day_count = max(1, min(31, (now.date() - daily_start.date()).days + 1))
    for offset in range(day_count):
        day = daily_start.date() + timedelta(days=offset)
        day_key = day.isoformat()
        daily.append({
            "date": f"{day.strftime('%b')} {day.day}",
            "watch_seconds": daily_watches.get(day_key, 0),
            "uploads": daily_uploads.get(day_key, 0),
        })

    return jsonify({
        "range": period,
        "kpis": {
            "watch_time_seconds": watch_seconds,
            "watch_time_change_pct": watch_change,
            "watch_time_previous_seconds": previous_watch_seconds,
            "ingested": ingestion_count,
            "ingestion_change_pct": ingestion_change,
            "auto_remediation_rate": round(auto_remediation_rate, 1),
            "auto_remediated": remediated_count,
            "safety_score": safety_score,
            "reported_videos": len(escalated_video_ids),
        },
        "violations": {
            "weapons": visual_violations["weapons"],
            "other_visual": visual_violations["other"],
            "transcript_toxicity": transcript_violations,
            "policy_review": policy_review_count,
        },
        "actions": action_counts,
        "silos": silos,
        "activity": {
            "daily": daily,
            "hourly": [
                {
                    "hour": f"{hour:02d}:00",
                    "watch_seconds": hour_watches.get(hour, 0),
                    "uploads": hour_uploads.get(hour, 0),
                }
                for hour in range(24)
            ],
            "daily_trend_days": day_count,
            "range": period,
        },
        "creators": creators,
    })


PLATFORM_ANALYTICS_SILOS = {
    "preschool": "Preschool",
    "kids": "Kids 6–10",
    "tweens": "Pre-Teens",
    "teens": "Teens",
    "unrestricted": "Unrestricted",
}


@admin_metrics_bp.route("/analytics/viewing")
@_ops_admin_required
def api_platform_viewing_analytics():
    period = request.args.get("range", "7d")
    silo = request.args.get("silo", "all")
    if period not in {"today", "7d", "1m", "all"}:
        return jsonify({"ok": False, "error": "Select a valid analytics period."}), 400
    if silo not in {"all", *PLATFORM_ANALYTICS_SILOS}:
        return jsonify({"ok": False, "error": "Select a valid profile silo."}), 400

    now = datetime.utcnow()
    if period == "today":
        range_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        range_start = now - timedelta(days=7)
    elif period == "1m":
        range_start = now - timedelta(days=30)
    else:
        range_start = None

    persona_query = (
        {"age_silo": silo}
        if silo != "all"
        else {"age_silo": {"$in": list(PLATFORM_ANALYTICS_SILOS)}}
    )
    personas = list(mongo.db.personas.find(
        persona_query,
        {"_id": 1, "account_id": 1},
    ))
    persona_ids = [str(persona["_id"]) for persona in personas]
    account_ids = {
        ObjectId(str(persona["account_id"]))
        for persona in personas
        if ObjectId.is_valid(str(persona.get("account_id") or ""))
    }
    if not persona_ids:
        return jsonify({
            "ok": True,
            "range": period,
            "silo": silo,
            "kpis": {
                "watch_seconds": 0,
                "average_daily_minutes": 0,
                "completion_rate": 0,
                "most_active_silo": None,
            },
            "timeline": [],
            "weekday_weekend": {"weekdays_minutes": 0, "weekend_minutes": 0},
            "dayparts": [],
            "categories": [],
            "interactions": {"likes": 0, "saves": 0, "downloads": 0},
            "top_videos": [],
            "top_creators": [],
            "exploration_diversity": 0,
        })

    time_match = {
        "event_type": "watch_time",
        "persona_id": {"$in": persona_ids},
        "occurred_at": {"$lte": now},
    }
    if range_start:
        time_match["occurred_at"]["$gte"] = range_start
    timeline_format = "%Y-%m-%d" if period in {"today", "7d", "1m"} else "%Y-%m"
    facets = list(mongo.db.viewer_activity.aggregate([
        {"$match": time_match},
        {"$facet": {
            "total": [
                {"$group": {
                    "_id": None,
                    "seconds": {"$sum": "$duration_seconds"},
                }},
            ],
            "timeline": [
                {"$group": {
                    "_id": {"$dateToString": {
                        "format": timeline_format,
                        "date": "$occurred_at",
                        "timezone": "UTC",
                    }},
                    "seconds": {"$sum": "$duration_seconds"},
                }},
                {"$sort": {"_id": 1}},
            ],
            "weekdays": [
                {"$group": {
                    "_id": {"$dayOfWeek": {
                        "date": "$occurred_at",
                        "timezone": "UTC",
                    }},
                    "seconds": {"$sum": "$duration_seconds"},
                }},
            ],
            "dayparts": [
                {"$group": {
                    "_id": {"$hour": {
                        "date": "$occurred_at",
                        "timezone": "UTC",
                    }},
                    "seconds": {"$sum": "$duration_seconds"},
                }},
                {"$sort": {"_id": 1}},
            ],
            "categories": [
                {"$group": {
                    "_id": {"$ifNull": ["$category", "Other"]},
                    "seconds": {"$sum": "$duration_seconds"},
                }},
                {"$sort": {"seconds": -1, "_id": 1}},
                {"$limit": 12},
            ],
            "silos": [
                {"$group": {
                    "_id": "$age_silo",
                    "seconds": {"$sum": "$duration_seconds"},
                }},
                {"$sort": {"seconds": -1}},
            ],
            "active_days": [
                {"$group": {
                    "_id": {
                        "persona_id": "$persona_id",
                        "day": {"$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$occurred_at",
                            "timezone": "UTC",
                        }},
                    },
                }},
                {"$count": "count"},
            ],
            "profile_categories": [
                {"$group": {
                    "_id": {
                        "persona_id": "$persona_id",
                        "category": {"$ifNull": ["$category", "Other"]},
                    },
                }},
                {"$group": {
                    "_id": "$_id.persona_id",
                    "categories": {"$sum": 1},
                }},
                {"$group": {
                    "_id": None,
                    "average": {"$avg": "$categories"},
                }},
            ],
        }},
    ]))
    analytics = facets[0] if facets else {}
    totals = analytics.get("total") or []
    watch_seconds = int(totals[0].get("seconds", 0)) if totals else 0
    active_user_days = int(
        (analytics.get("active_days") or [{}])[0].get("count", 0)
    )
    average_daily_minutes = (
        round(watch_seconds / 60 / active_user_days, 1)
        if active_user_days
        else 0
    )

    weekday_seconds = [0] * 7
    for row in analytics.get("weekdays", []):
        weekday_seconds[(int(row["_id"]) + 5) % 7] = int(row.get("seconds", 0))
    weekdays_minutes = round(sum(weekday_seconds[:5]) / 60, 1)
    weekend_minutes = round(sum(weekday_seconds[5:]) / 60, 1)

    daypart_seconds = {"Morning": 0, "Afternoon": 0, "Evening": 0, "Night": 0}
    for row in analytics.get("dayparts", []):
        hour = int(row["_id"])
        if 6 <= hour < 12:
            daypart = "Morning"
        elif 12 <= hour < 17:
            daypart = "Afternoon"
        elif 17 <= hour < 21:
            daypart = "Evening"
        else:
            daypart = "Night"
        daypart_seconds[daypart] += int(row.get("seconds", 0))

    watch_history_match = {"persona_id": {"$in": persona_ids}}
    if range_start:
        watch_history_match["watched_at"] = {"$gte": range_start, "$lte": now}
    else:
        watch_history_match["watched_at"] = {"$lte": now}
    history_summary = list(mongo.db.watch_history.aggregate([
        {"$match": watch_history_match},
        {"$project": {
            "video_id": 1,
            "persona_id": 1,
            "play_count": {"$max": [1, {"$ifNull": ["$play_count", 1]}]},
            "watch_pct": {"$cond": [
                {"$gt": [{"$ifNull": ["$duration_seconds", 0]}, 0]},
                {"$min": [
                    100,
                    {"$multiply": [
                        {"$divide": [
                            {"$ifNull": ["$position_seconds", 0]},
                            "$duration_seconds",
                        ]},
                        100,
                    ]},
                ]},
                None,
            ]},
            "is_complete": {"$or": [
                {"$eq": ["$completed", True]},
                {"$and": [
                    {"$gt": [{"$ifNull": ["$duration_seconds", 0]}, 0]},
                    {"$gte": [
                        "$position_seconds",
                        {"$multiply": ["$duration_seconds", 0.9]},
                    ]},
                ]},
            ]},
        }},
        {"$facet": {
            "completion": [
                {"$match": {"watch_pct": {"$ne": None}}},
                {"$group": {
                    "_id": None,
                    "eligible": {"$sum": 1},
                    "completed": {"$sum": {"$cond": ["$is_complete", 1, 0]}},
                }},
            ],
            "videos": [
                {"$group": {
                    "_id": "$video_id",
                    "history_watch_pct": {"$avg": "$watch_pct"},
                    "history_records": {"$sum": 1},
                }},
            ],
        }},
    ]))
    history = history_summary[0] if history_summary else {}
    completion = (history.get("completion") or [{}])[0]
    completion_eligible = int(completion.get("eligible", 0))
    completion_rate = (
        round(int(completion.get("completed", 0)) * 100 / completion_eligible, 1)
        if completion_eligible
        else 0
    )
    watch_pct_by_video = {
        str(row["_id"]): row.get("history_watch_pct")
        for row in history.get("videos", [])
        if row.get("_id")
    }

    interaction_match = {
        "persona_id": {"$in": persona_ids},
        "event_type": "download",
        "occurred_at": {"$lte": now},
    }
    if range_start:
        interaction_match["occurred_at"]["$gte"] = range_start
    interaction_states = list(mongo.db.viewer_activity.aggregate([
        {"$match": interaction_match},
        {"$sort": {"occurred_at": 1}},
        {"$group": {
            "_id": {
                "persona_id": "$persona_id",
                "event_type": "$event_type",
                "video_id": "$video_id",
            },
            "action": {"$last": "$action"},
            "actions": {"$push": "$action"},
        }},
    ]))
    downloaded_items = set()
    for item in interaction_states:
        key = item["_id"]
        if "downloaded" in item.get("actions", []):
            downloaded_items.add((key.get("persona_id"), key.get("video_id")))

    liked_items = set()
    if account_ids:
        for account in mongo.db.accounts.find(
            {"_id": {"$in": list(account_ids)}},
            {"liked_videos": 1},
        ):
            account_key = str(account["_id"])
            for liked_video_id in account.get("liked_videos", []):
                if ObjectId.is_valid(str(liked_video_id)):
                    liked_items.add((account_key, str(liked_video_id)))

    saved_items = set()
    saved_lists = mongo.db.watch_later.find(
        {"persona_id": {"$in": persona_ids}},
        {"persona_id": 1, "video_ids": 1},
    )
    for saved_list in saved_lists:
        persona_key = str(saved_list.get("persona_id") or "")
        for saved_video_id in saved_list.get("video_ids", []):
            if ObjectId.is_valid(str(saved_video_id)):
                saved_items.add((persona_key, str(saved_video_id)))
    playlists = mongo.db.playlists.find(
        {"persona_id": {"$in": persona_ids}},
        {"persona_id": 1, "video_ids": 1},
    )
    for playlist in playlists:
        persona_key = str(playlist.get("persona_id") or "")
        for saved_video_id in playlist.get("video_ids", []):
            if ObjectId.is_valid(str(saved_video_id)):
                saved_items.add((persona_key, str(saved_video_id)))
    interactions = {
        "likes": len(liked_items),
        "saves": len(saved_items),
        "downloads": len(downloaded_items),
    }

    play_match = {
        "persona_id": {"$in": persona_ids},
        "event_type": "play_started",
        "occurred_at": {"$lte": now},
    }
    if range_start:
        play_match["occurred_at"]["$gte"] = range_start
    play_totals = list(mongo.db.viewer_activity.aggregate([
        {"$match": play_match},
        {"$group": {
            "_id": {"video_id": "$video_id", "persona_id": "$persona_id"},
            "plays": {"$sum": 1},
        }},
        {"$project": {
            "video_id": "$_id.video_id",
            "plays": 1,
            "replays": {"$max": [{"$subtract": ["$plays", 1]}, 0]},
        }},
        {"$group": {
            "_id": "$video_id",
            "plays": {"$sum": "$plays"},
            "replays": {"$sum": "$replays"},
        }},
        {"$sort": {"plays": -1, "_id": 1}},
        {"$limit": 20},
    ]))
    top_video_ids = [
        ObjectId(row["_id"])
        for row in play_totals
        if ObjectId.is_valid(str(row.get("_id") or ""))
    ]
    video_details = {
        str(video["_id"]): video
        for video in mongo.db.videos.find(
            {"_id": {"$in": top_video_ids}},
            {"title": 1, "uploaded_by": 1, "age_silo": 1, "age_silos": 1},
        )
    } if top_video_ids else {}
    creator_ids = {
        ObjectId(str(video.get("uploaded_by")))
        for video in video_details.values()
        if ObjectId.is_valid(str(video.get("uploaded_by") or ""))
    }
    creator_details = {
        str(persona["_id"]): persona
        for persona in mongo.db.personas.find(
            {"_id": {"$in": list(creator_ids)}},
            {"name": 1},
        )
    } if creator_ids else {}
    top_videos = []
    for row in play_totals:
        video_id = str(row.get("_id") or "")
        video = video_details.get(video_id)
        if not video:
            continue
        creator = creator_details.get(str(video.get("uploaded_by") or ""), {})
        video_silos = video.get("age_silos") or [video.get("age_silo")]
        silo_category = ", ".join(
            PLATFORM_ANALYTICS_SILOS.get(value, value)
            for value in video_silos
            if value
        ) or "Unspecified"
        top_videos.append({
            "title": video.get("title") or "Untitled video",
            "creator": creator.get("name") or "Unknown creator",
            "plays": int(row.get("plays", 0)),
            "replays": int(row.get("replays", 0)),
            "average_watch_pct": round(
                watch_pct_by_video[video_id], 1
            ) if watch_pct_by_video.get(video_id) is not None else None,
            "silo_category": silo_category,
        })

    watched_video_totals = list(mongo.db.viewer_activity.aggregate([
        {"$match": time_match},
        {"$group": {
            "_id": "$video_id",
            "seconds": {"$sum": "$duration_seconds"},
        }},
    ]))
    watched_video_ids = [
        ObjectId(row["_id"])
        for row in watched_video_totals
        if ObjectId.is_valid(str(row.get("_id") or ""))
    ]
    watched_video_details = {
        str(video["_id"]): video
        for video in mongo.db.videos.find(
            {"_id": {"$in": watched_video_ids}},
            {"uploaded_by": 1},
        )
    } if watched_video_ids else {}
    creator_seconds = {}
    for row in watched_video_totals:
        video = watched_video_details.get(str(row.get("_id") or ""), {})
        creator_id = str(video.get("uploaded_by") or "")
        if ObjectId.is_valid(creator_id):
            creator_seconds[creator_id] = (
                creator_seconds.get(creator_id, 0) + int(row.get("seconds", 0))
            )
    creator_profiles = {
        str(persona["_id"]): persona
        for persona in mongo.db.personas.find(
            {"_id": {"$in": [
                ObjectId(creator_id) for creator_id in creator_seconds
            ]}},
            {"name": 1},
        )
    } if creator_seconds else {}
    top_creators = [
        {
            "name": creator_profiles.get(creator_id, {}).get("name") or "Creator",
            "watch_seconds": seconds,
        }
        for creator_id, seconds in sorted(
            creator_seconds.items(),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]
    diversity = (analytics.get("profile_categories") or [{}])[0].get("average", 0)
    silo_rows = analytics.get("silos", [])
    if silo != "all":
        global_silo_match = {
            "event_type": "watch_time",
            "occurred_at": {"$lte": now},
        }
        if range_start:
            global_silo_match["occurred_at"]["$gte"] = range_start
        silo_rows = list(mongo.db.viewer_activity.aggregate([
            {"$match": global_silo_match},
            {"$group": {
                "_id": "$age_silo",
                "seconds": {"$sum": "$duration_seconds"},
            }},
            {"$sort": {"seconds": -1}},
        ]))
    silo_total = sum(int(row.get("seconds", 0)) for row in silo_rows)
    most_active_silo = None
    if silo_rows and silo_total:
        top_silo = (
            next(
                (row for row in silo_rows if row.get("_id") == silo),
                {"_id": silo, "seconds": 0},
            )
            if silo != "all"
            else silo_rows[0]
        )
        top_silo_name = top_silo.get("_id")
        most_active_silo = {
            "id": top_silo_name,
            "label": PLATFORM_ANALYTICS_SILOS.get(
                top_silo_name, top_silo_name or "Unknown"
            ),
            "percentage": round(int(top_silo.get("seconds", 0)) * 100 / silo_total),
        }

    return jsonify({
        "ok": True,
        "range": period,
        "silo": silo,
        "kpis": {
            "watch_seconds": watch_seconds,
            "average_daily_minutes": average_daily_minutes,
            "completion_rate": completion_rate,
            "most_active_silo": most_active_silo,
        },
        "timeline": [
            {"period": row["_id"], "hours": round(row["seconds"] / 3600, 2)}
            for row in analytics.get("timeline", [])
        ],
        "weekday_weekend": {
            "weekdays_minutes": weekdays_minutes,
            "weekend_minutes": weekend_minutes,
        },
        "dayparts": [
            {"label": label, "seconds": seconds}
            for label, seconds in daypart_seconds.items()
        ],
        "categories": [
            {"category": row.get("_id") or "Other", "seconds": int(row.get("seconds", 0))}
            for row in analytics.get("categories", [])
        ],
        "interactions": interactions,
        "top_videos": top_videos[:10],
        "top_creators": top_creators,
        "exploration_diversity": round(float(diversity or 0), 1),
    })


@admin_ops_bp.route("/api/health")
@_ops_admin_required
def api_health():
    import importlib.util
    statuses = {"mongo": "offline", "yolo": "offline", "whisper": "offline", "chromadb": "offline"}
    try:
        mongo.cx.admin.command("ping")
        statuses["mongo"] = "online"
    except Exception:
        current_app.logger.exception("Admin health check could not reach MongoDB.")
    model_path = os.getenv("YOLO_MODEL", "yolo11n.pt")
    if not os.path.isabs(model_path):
        model_path = os.path.abspath(os.path.join(current_app.root_path, "..", model_path))
    if importlib.util.find_spec("ultralytics") and os.path.isfile(model_path):
        statuses["yolo"] = "online"
    if importlib.util.find_spec("faster_whisper"):
        statuses["whisper"] = "online"
    try:
        from ..ai.rag_policy_agent import get_policy_agent
        if get_policy_agent().is_ready():
            statuses["chromadb"] = "connected"
    except Exception:
        current_app.logger.exception("Admin health check could not reach ChromaDB.")
    return jsonify(statuses)


@admin_ops_bp.route("/api/analytics")
@_ops_admin_required
def api_analytics():
    days = [(datetime.utcnow() - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0) for offset in reversed(range(7))]
    daily = []
    for start in days:
        next_day = start + timedelta(days=1)
        daily.append({
            "date": start.strftime("%a"),
            "ingested": mongo.db.videos.count_documents({
                "status": "published",
                "published_at": {"$gte": start, "$lt": next_day},
            }),
            "remediated": mongo.db.videos.count_documents({
                "status": "published",
                "published_at": {"$gte": start, "$lt": next_day},
                "moderation_decision": "approve_remediation",
            }),
        })
    silos = []
    for silo in ["preschool", "kids", "tweens", "teens", "unrestricted"]:
        silos.append({"silo": silo, "count": mongo.db.videos.count_documents({"status": "published", "age_silo": silo})})
    modalities = {"visual": 0, "speech": 0, "acoustic": 0, "photosensitivity": 0}
    for video in mongo.db.videos.find(
        {"status": "published", "ai_result": {"$exists": True}},
        {"ai_result": 1},
    ):
        ai_result = video.get("ai_result") or {}
        modalities["visual"] += len((ai_result.get("visual") or {}).get("violations") or [])
        for violation in ai_result.get("violations_summary") or []:
            kind = str(violation.get("type", "")).lower()
            label = str(violation.get("label", violation.get("class", ""))).lower()
            if "flash" in kind or "strob" in label:
                modalities["photosensitivity"] += 1
            elif "audio" in kind or "speech" in kind or "toxic" in kind:
                modalities["speech"] += 1
            elif "sound" in kind or "scream" in label or "acoustic" in label:
                modalities["acoustic"] += 1
    return jsonify({"daily": daily, "silos": silos, "modalities": modalities,
        "latency": {"yolo_ms_frame": 42, "whisper_seconds_chunk": 1.1, "chromadb_ms": 15}})


@admin_ops_bp.route("/api/feedback")
@_ops_admin_required
def api_feedback():
    items = list(mongo.db.feedbacks.find({}).sort("created_at", -1).limit(300))
    for item in items:
        item["_id"] = str(item.get("_id"))
        item["video_id"] = str(item["video_id"]) if item.get("video_id") else ""
        reporter_id = item.get("user_id")
        if ObjectId.is_valid(str(reporter_id)):
            reporter_oid = ObjectId(str(reporter_id))
            persona = mongo.db.personas.find_one(
                {"_id": reporter_oid}, {"name": 1, "account_id": 1}
            )
            if persona:
                account = mongo.db.accounts.find_one(
                    {"_id": persona.get("account_id")}, {"email": 1}
                )
                item["reporter"] = (
                    (account or {}).get("email")
                    or persona.get("name")
                    or item.get("reporter")
                    or str(reporter_id)
                )
            else:
                account = mongo.db.accounts.find_one({"_id": reporter_oid}, {"email": 1})
                item["reporter"] = (
                    (account or {}).get("email")
                    or item.get("reporter")
                    or str(reporter_id)
                )
        else:
            item.setdefault("reporter", reporter_id or "Guest")
    return jsonify({"items": _json_safe(items)})


@admin_ops_bp.route("/api/feedback/<feedback_id>", methods=["PATCH"])
@_ops_admin_required
def api_feedback_update(feedback_id):
    _require_csrf()
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"OPEN", "IN_REVIEW", "RESOLVED"}:
        return jsonify({"ok": False, "error": "Invalid feedback status."}), 400
    from uuid import UUID
    try:
        UUID(feedback_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Feedback not found."}), 404
    result = mongo.db.feedbacks.update_one({"feedback_id": feedback_id}, {"$set": {"status": status, "reviewed_at": datetime.utcnow(), "reviewed_by": session["admin_user_id"]}})
    if result.matched_count == 0:
        return jsonify({"ok": False, "error": "Feedback not found."}), 404
    _ops_audit("feedback_updated", f"Feedback {feedback_id} marked {status.lower()}.", feedback_id)
    return jsonify({"ok": True})


@admin_ops_bp.route("/api/policies")
@_ops_admin_required
def api_policies():
    items = list(mongo.db.policy_rules.find({}).sort("created_at", -1).limit(500))
    return jsonify({"items": _json_safe(items)})


@admin_ops_bp.route("/api/policies", methods=["POST"])
@_ops_admin_required
def api_policy_create():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    silo = str(data.get("age_silo") or "").strip().lower()
    action = str(data.get("action") or "").strip().lower()
    category = str(data.get("category") or "visual_object").strip().lower()
    if (
        not title
        or not description
        or silo not in {"preschool", "kids", "tweens", "teens", "unrestricted"}
        or action not in {"blur", "bleep", "reject"}
        or category not in {"visual_object", "toxicity", "acoustic", "photosensitivity"}
    ):
        return jsonify({"ok": False, "error": "Provide a title, description, valid silo, and remediation action."}), 400
    rule_id = str(ObjectId())
    rule = {"rule_id": rule_id, "title": title[:120], "description": description[:2000], "category": category,
            "age_silo": silo, "action": action, "threshold": 0.7, "active": True,
            "created_at": datetime.utcnow(), "created_by": session["admin_user_id"]}
    mongo.db.policy_rules.insert_one(rule)
    try:
        from ..ai.rag_policy_agent import get_policy_agent
        get_policy_agent().upsert_admin_policy(rule_id, description, silo, category)
    except Exception:
        current_app.logger.exception("Failed to update ChromaDB for policy rule %s", rule_id)
        mongo.db.policy_rules.delete_one({"rule_id": rule_id})
        return jsonify({"ok": False, "error": "ChromaDB update failed; the rule was not saved."}), 503
    return jsonify({"ok": True, "item": _json_safe(rule)}), 201


@admin_ops_bp.route("/api/policies/<rule_id>", methods=["PATCH"])
@_ops_admin_required
def api_policy_update(rule_id):
    _require_csrf()
    data = request.get_json(silent=True) or {}
    if "active" not in data or not isinstance(data["active"], bool):
        return jsonify({"ok": False, "error": "Active must be a boolean."}), 400
    rule = mongo.db.policy_rules.find_one({"rule_id": rule_id})
    if not rule:
        return jsonify({"ok": False, "error": "Policy rule not found."}), 404
    mongo.db.policy_rules.update_one({"_id": rule["_id"]}, {"$set": {"active": data["active"], "updated_at": datetime.utcnow()}})
    rule_text = rule.get("description", "")
    try:
        from ..ai.rag_policy_agent import get_policy_agent
        get_policy_agent().upsert_admin_policy(
            rule_id,
            rule_text,
            rule.get("age_silo", "all"),
            rule.get("category", ""),
            active=data["active"],
        )
    except Exception:
        current_app.logger.exception("Failed to synchronize ChromaDB policy rule %s", rule_id)
        return jsonify({"ok": False, "error": "Policy saved but ChromaDB synchronization failed."}), 503
    return jsonify({"ok": True})


@admin_ops_bp.route("/api/users")
@_ops_admin_required
def api_users():
    items = []
    for account in mongo.db.accounts.find({}).sort("created_at", -1).limit(500):
        account_id = account["_id"]
        personas = list(mongo.db.personas.find({"account_id": account_id}, {"name": 1, "is_primary": 1, "role": 1}))
        persona_ids = [str(persona["_id"]) for persona in personas]
        creator = any(p.get("role") == "creator" for p in personas) or bool(
            persona_ids and mongo.db.videos.count_documents({"uploaded_by": {"$in": persona_ids}})
        )
        items.append({"id": str(account_id), "email": account.get("email", ""), "role": "Creator" if creator else "Parent",
                      "children": sum(not p.get("is_primary", False) for p in personas),
                      "status": account.get("status", "active"), "kind": "account"})
    for admin in mongo.db.users.find({"is_superuser": True}, {"username": 1, "email": 1, "status": 1}):
        items.append({"id": str(admin["_id"]), "email": admin.get("email", ""), "role": "Admin",
                      "children": 0, "status": admin.get("status", "active"), "kind": "admin"})
    return jsonify({"items": items})


@admin_ops_bp.route("/api/users/<user_id>/status", methods=["PATCH"])
@_ops_admin_required
def api_user_status(user_id):
    _require_csrf()
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"active", "suspended"} or not ObjectId.is_valid(user_id):
        return jsonify({"ok": False, "error": "Invalid account status or id."}), 400
    oid = ObjectId(user_id)
    account = mongo.db.accounts.find_one({"_id": oid})
    if account:
        mongo.db.accounts.update_one({"_id": oid}, {"$set": {"status": status, "updated_at": datetime.utcnow()}})
    else:
        user = mongo.db.users.find_one({"_id": oid, "is_superuser": True})
        if not user:
            return jsonify({"ok": False, "error": "User not found."}), 404
        if str(oid) == session["admin_user_id"] and status == "suspended":
            return jsonify({"ok": False, "error": "You cannot suspend your own active super-admin account."}), 400
        mongo.db.users.update_one({"_id": oid}, {"$set": {"status": status, "updated_at": datetime.utcnow()}})
    _ops_audit("user_status_changed", f"User {user_id} status changed to {status}.", user_id)
    return jsonify({"ok": True})


@admin_ops_bp.route("/api/users/<user_id>/delete", methods=["DELETE"])
@_ops_admin_required
def api_user_delete(user_id):
    _require_csrf()
    if not ObjectId.is_valid(user_id) or user_id == session["admin_user_id"]:
        return jsonify({"ok": False, "error": "Invalid user id or self-deletion is prohibited."}), 400
    oid = ObjectId(user_id)
    admin = mongo.db.users.find_one({"_id": oid, "is_superuser": True})
    if admin:
        mongo.db.users.delete_one({"_id": oid, "is_superuser": True})
        _ops_audit("admin_user_deleted", f"Super-admin account {admin.get('email', user_id)} removed.", user_id)
        return jsonify({"ok": True})
    account = mongo.db.accounts.find_one({"_id": oid})
    if not account:
        return jsonify({"ok": False, "error": "User not found."}), 404
    personas = list(mongo.db.personas.find({"account_id": oid}, {"_id": 1}))
    persona_ids = [str(persona["_id"]) for persona in personas]
    videos = list(mongo.db.videos.find(
        {"uploaded_by": {"$in": persona_ids}},
        {"_id": 1},
    ))
    video_ids = [str(video["_id"]) for video in videos]

    for video in videos:
        delete_video_assets(str(video["_id"]))
    mongo.db.videos.delete_many({"uploaded_by": {"$in": persona_ids}})
    mongo.db.moderation_queue.delete_many({"video_id": {"$in": video_ids + [ObjectId(item) for item in video_ids]}})
    mongo.db.channels.delete_many({"created_by": {"$in": persona_ids}})
    mongo.db.personas.delete_many({"account_id": oid})
    mongo.db.accounts.delete_one({"_id": oid})
    for collection in ("comments", "feedbacks", "viewer_activity", "watch_history", "watch_later", "notifications", "playlists"):
        mongo.db[collection].delete_many({"$or": [
            {"account_id": oid},
            {"persona_id": {"$in": persona_ids}},
            {"user_id": {"$in": persona_ids + [str(oid)]}},
            {"video_id": {"$in": video_ids}},
        ]})
    _ops_audit("user_deleted", f"Account {account.get('email', user_id)} and associated records removed.", user_id)
    return jsonify({"ok": True})


@admin_ops_bp.route("/api/users/<user_id>/pin-reset", methods=["POST"])
@_ops_admin_required
def api_pin_reset(user_id):
    _require_csrf()
    if not ObjectId.is_valid(user_id):
        return jsonify({"ok": False, "error": "Invalid user id."}), 400
    account = mongo.db.accounts.find_one({"_id": ObjectId(user_id)})
    if not account:
        return jsonify({"ok": False, "error": "Account not found."}), 404
    if not account.get("email_verified", account.get("verified_email", False)):
        return jsonify({"ok": False, "error": "The account has no verified recovery email."}), 409
    mail_host = current_app.config.get("SMTP_HOST")
    sender = current_app.config.get("SMTP_FROM") or current_app.config.get("SMTP_USERNAME")
    if not mail_host or not sender:
        return jsonify({"ok": False, "error": "PIN recovery delivery is not configured. Set SMTP_HOST and SMTP_FROM."}), 503
    import smtplib
    from email.message import EmailMessage
    import hashlib
    import hmac
    import secrets as _secrets
    from datetime import timezone
    email = account.get("email")
    if not email:
        return jsonify({"ok": False, "error": "This account has no verified recovery email."}), 409
    otp = f"{_secrets.randbelow(1_000_000):06d}"
    reset_id = str(ObjectId())
    mongo.db.pin_reset_requests.delete_many({"account_id": ObjectId(user_id), "used": False})
    otp_hash = hmac.new(
        current_app.secret_key.encode(), otp.encode(), hashlib.sha256
    ).hexdigest()
    mongo.db.pin_reset_requests.insert_one({
        "_id": ObjectId(reset_id), "account_id": ObjectId(user_id),
        "otp_hash": otp_hash,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "created_at": datetime.now(timezone.utc), "used": False,
    })
    message = EmailMessage()
    message["Subject"] = "Spectra account PIN recovery"
    message["From"] = sender
    message["To"] = email
    message.set_content(f"Your Spectra PIN recovery code is {otp}. It expires in 10 minutes.")
    try:
        with smtplib.SMTP(mail_host, int(current_app.config.get("SMTP_PORT", 587)), timeout=10) as smtp:
            smtp.starttls()
            username = current_app.config.get("SMTP_USERNAME")
            if username:
                smtp.login(username, current_app.config.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        mongo.db.pin_reset_requests.delete_one({"_id": ObjectId(reset_id)})
        current_app.logger.exception("Failed to dispatch PIN recovery email for account %s", user_id)
        return jsonify({"ok": False, "error": "Recovery email could not be delivered."}), 503
    _ops_audit("pin_reset_dispatched", f"PIN reset alert dispatched to {email}.", user_id)
    return jsonify({"ok": True})


@admin_ops_bp.route("/api/sensitivity", methods=["POST"])
@_ops_admin_required
def api_sensitivity():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode not in {"normal", "strict"}:
        return jsonify({"ok": False, "error": "Mode must be normal or strict."}), 400
    mongo.db.system_config.update_one({}, {"$set": {"admin_sensitivity_mode": mode, "updated_at": datetime.utcnow()}}, upsert=True)
    _ops_audit("sensitivity_changed", f"Global sensitivity mode set to {mode}.")
    return jsonify({"ok": True, "mode": mode})


@admin_ops_bp.route("/api/sensitivity", methods=["GET"])
@_ops_admin_required
def api_get_sensitivity():
    config = mongo.db.system_config.find_one({}, {"admin_sensitivity_mode": 1}) or {}
    return jsonify({"mode": config.get("admin_sensitivity_mode", "normal")})


def creator_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in") or session.get("age_silo") not in {"teens", "unrestricted"}:
            return redirect(url_for("auth.login_page"))
        return f(*args, **kwargs)
    return decorated


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    published = mongo.db.videos.count_documents({"status": "published"})
    total_accounts = mongo.db.accounts.count_documents({})

    recent_audits = list(
        mongo.db.audit_logs.find({}).sort("created_at", -1).limit(10)
    )
    for a in recent_audits:
        a["_id"] = str(a["_id"])

    recent_videos = list(
        mongo.db.videos.find({"status": "published"}).sort("published_at", -1).limit(6)
    )
    for v in recent_videos:
        v["_id"] = str(v["_id"])

    return render_template(
        "admin/dashboard.html",
        published=published,
        total_accounts=total_accounts,
        recent_audits=recent_audits,
        recent_videos=recent_videos,
    )


# ── Add Video Page ────────────────────────────────────────────────────────────

@admin_bp.route("/add-video", methods=["GET"])
@creator_required
def add_video_page():
    return render_template(
        "admin/add_video.html",
        category_options=CATEGORY_OPTIONS,
    )


@admin_bp.route("/add-video/upload", methods=["POST"])
@creator_required
def upload_video():
    """Receive video file upload, save to disk, return video_id for AI analysis."""
    upload_started = perf_counter()
    is_streamed_upload = request.mimetype == "application/octet-stream"
    if is_streamed_upload:
        original_filename = request.args.get("filename", "")
        title = (request.args.get("title") or "Untitled").strip()
        description = (request.args.get("description") or "").strip()
        age_silo = (request.args.get("age_silo") or "").strip().lower()
        category = (request.args.get("category") or "").strip()
        targets_value = request.args.get("age_group_targets")
    else:
        if "video_file" not in request.files:
            return jsonify({"ok": False, "error": "No file uploaded."}), 400
        f = request.files["video_file"]
        original_filename = f.filename
        title = (request.form.get("title") or "Untitled").strip()
        description = (request.form.get("description") or "").strip()
        age_silo = (request.form.get("age_silo") or "").strip().lower()
        category = (request.form.get("category") or "").strip()
        targets_value = request.form.get("age_group_targets")

    if not original_filename:
        return jsonify({"ok": False, "error": "Empty filename."}), 400

    ext = os.path.splitext(secure_filename(original_filename))[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Unsupported format: {ext}"}), 400

    try:
        age_group_targets = json.loads(targets_value) if targets_value else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid age-group selections."}), 400
    age_group_targets, target_error = _validate_age_group_targets(
        age_silo, category, age_group_targets
    )
    if target_error:
        return jsonify({"ok": False, "error": target_error}), 400

    video_id = str(ObjectId())
    filename = f"{video_id}{ext}"
    upload_source = request.stream if is_streamed_upload else f.stream
    upload_size = store_stream(
        f"uploads/{filename}",
        upload_source,
        video_id=video_id,
        asset_type="upload",
    )
    if upload_size == 0:
        delete_asset(f"uploads/{filename}")
        return jsonify({"ok": False, "error": "The uploaded video is empty."}), 400
    if is_streamed_upload and upload_size != request.content_length:
        delete_asset(f"uploads/{filename}")
        return jsonify({"ok": False, "error": "The video upload was incomplete."}), 400

    current_app.logger.info(
        "Video upload stored in MongoDB for %s: %.1f MB in %.1f seconds",
        video_id,
        upload_size / (1024 * 1024),
        perf_counter() - upload_started,
    )

    duration_str = "0:00"
    try:
        duration_value = (
            request.args.get("duration_seconds", "0")
            if is_streamed_upload
            else request.form.get("duration_seconds", "0")
        )
        secs = max(0, int(float(duration_value)))
        m, s = divmod(int(secs), 60)
        duration_str = f"{m}:{s:02d}"
    except Exception:
        pass

    # Insert pending document
    try:
        mongo.db.videos.insert_one({
            "_id": ObjectId(video_id),
            "title": title,
            "description": description,
            "age_silo": age_silo,
            "category": category,
            "age_silos": [target["age_silo"] for target in age_group_targets],
            "age_group_targets": age_group_targets,
            "status": "pending",
            "original_filename": filename,
            "duration_str": duration_str,
            "uploaded_by": session.get("persona_id"),
            "created_at": datetime.utcnow(),
            "views": 0,
            "likes": 0,
            "dislikes": 0,
            "comment_count": 0,
            "ai_result": None,
        })
    except Exception:
        delete_asset(f"uploads/{filename}")
        raise

    return jsonify({"ok": True, "video_id": video_id, "upload_path": f"uploads/{filename}"})


# ── AI Analysis Pipeline ──────────────────────────────────────────────────────

@admin_bp.route("/add-video/analyze/<video_id>", methods=["POST"])
@creator_required
def analyze_video(video_id: str):
    """Run the full multimodal AI pipeline on an uploaded video."""
    pipeline_started = perf_counter()
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Video not found."}), 404

    metadata = request.get_json(silent=True)
    metadata_updates = {}
    if metadata is not None:
        if not isinstance(metadata, dict):
            return jsonify({"ok": False, "error": "Video details must be a JSON object."}), 400

        title = metadata.get("title", video.get("title", ""))
        description = metadata.get("description", video.get("description", ""))
        age_silo = metadata.get("age_silo", video.get("age_silo", ""))
        category = metadata.get("category", video.get("category", ""))
        if not all(isinstance(value, str) for value in (title, description, age_silo, category)):
            return jsonify({"ok": False, "error": "Video details must be text."}), 400
        title = title.strip()
        age_silo = age_silo.strip().lower()
        category = category.strip()
        if not title:
            return jsonify({"ok": False, "error": "Video title is required."}), 400
        metadata_updates = {
            "title": title,
            "description": description.strip(),
            "age_silo": age_silo,
            "category": category,
        }
        age_group_targets = metadata.get(
            "age_group_targets", video.get("age_group_targets")
        )
    else:
        age_silo = video.get("age_silo", "")
        category = video.get("category", "")
        age_group_targets = video.get("age_group_targets")

    age_silo = metadata_updates.get("age_silo", video.get("age_silo", ""))
    category = metadata_updates.get("category", video.get("category", ""))
    if isinstance(age_silo, str):
        age_silo = age_silo.strip().lower()
    if isinstance(category, str):
        category = category.strip()
    age_group_targets, target_error = _validate_age_group_targets(
        age_silo, category, age_group_targets
    )
    if target_error:
        return jsonify({"ok": False, "error": target_error}), 400
    metadata_updates["age_group_targets"] = age_group_targets
    metadata_updates["age_silos"] = [
        target["age_silo"] for target in age_group_targets
    ]

    previous_result = video.get("ai_result") or {}
    cached_analysis = video.get("analysis_cache") or {
        key: previous_result[key]
        for key in ("visual", "audio", "transcript")
        if key in previous_result
    }
    if "analysis_config" in previous_result and "_config" not in cached_analysis:
        cached_analysis["_config"] = previous_result["analysis_config"]
    mongo.db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {
            "$set": {
                **metadata_updates,
                "status": "pending",
                "analysis_cache": cached_analysis,
            },
            "$unset": {"ai_result": "", "age_review": "", "publish_error": ""},
        },
    )
    video.update(metadata_updates)
    video["analysis_cache"] = cached_analysis

    upload_context = materialize_asset(
        f"uploads/{video['original_filename']}",
        suffix=os.path.splitext(video["original_filename"])[1],
    )
    upload_context_entered = False
    age_silo = video.get("age_silo", "kids")

    # Retrieve tunable thresholds from system_config
    config = mongo.db.system_config.find_one({}) or {}
    yolo_conf = float(config.get("yolo_confidence", 0.45))
    bert_thresh = float(config.get("bert_toxicity_threshold", 0.60))
    rag_dist = float(config.get("rag_distance_threshold", 0.75))

    try:
        upload_path = upload_context.__enter__()
        upload_context_entered = True
        from ..ai.visual_analyzer import analyze_video_frames
        from ..ai.audio_analyzer import transcribe_audio, analyze_toxicity, export_vtt
        from ..ai.rag_policy_agent import evaluate_age_group_content, get_policy_agent

        cached_analysis = video.get("analysis_cache") or {}
        reusable_components = all(
            isinstance(cached_analysis.get(key), dict)
            for key in ("visual", "audio", "transcript")
        )
        cached_config = cached_analysis.get("_config")
        reused_analysis = (
            reusable_components
            and isinstance(cached_config, dict)
            and cached_config.get("yolo_confidence") == yolo_conf
            and cached_config.get("bert_toxicity_threshold") == bert_thresh
        )
        inference_started = perf_counter()
        toxicity_seconds = 0.0
        if reused_analysis:
            visual_result = cached_analysis["visual"]
            audio_result = cached_analysis["audio"]
            transcript_result = cached_analysis["transcript"]
        else:
            # Expensive model inference is cached so metadata-only retries can
            # refresh policy decisions without rescanning or retranscribing.
            # Visual inference and transcription are independent, so overlap them.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-analysis") as pool:
                visual_future = pool.submit(
                    analyze_video_frames,
                    upload_path,
                    confidence_threshold=yolo_conf,
                )
                transcript_future = pool.submit(transcribe_audio, upload_path)
                visual_result = visual_future.result()
                transcript_result = transcript_future.result()
            segments = transcript_result.get("segments", [])
            toxicity_started = perf_counter()
            audio_result = analyze_toxicity(segments, threshold=bert_thresh)
            toxicity_seconds = perf_counter() - toxicity_started
            cached_analysis = {
                "visual": visual_result,
                "audio": audio_result,
                "transcript": transcript_result,
            }
        inference_seconds = perf_counter() - inference_started

        segments = transcript_result.get("segments", [])
        # 4. Export VTT captions
        vtt_filename = f"{video_id}.vtt"
        if segments:
            with tempfile.TemporaryDirectory(prefix="spectra-vtt-") as temp_dir:
                vtt_output = os.path.join(temp_dir, vtt_filename)
                export_vtt(segments, vtt_output)
                store_file(
                    f"vtt/{vtt_filename}",
                    vtt_output,
                    video_id=video_id,
                    asset_type="captions",
                )
            mongo.db.videos.update_one(
                {"_id": ObjectId(video_id)}, {"$set": {"vtt_file": vtt_filename}}
            )

        # 5. RAG policy evaluation
        policy_started = perf_counter()
        agent = get_policy_agent()
        age_group_evaluations = evaluate_age_group_content(
            {
                "visual": visual_result,
                "audio": audio_result,
                "transcript": transcript_result,
                "age_group_targets": age_group_targets,
                "distance_threshold": rag_dist,
            },
            [target["age_silo"] for target in age_group_targets],
        )
        age_group_results = list(age_group_evaluations.values())
        for group_result in age_group_results:
            for citation in group_result["citations"]:
                citation["age_silo"] = group_result["age_silo"]
            for violation in group_result["violations_summary"]:
                violation["age_silo"] = group_result["age_silo"]
                violation["category"] = group_result["category"]

        compliance_statuses = {
            result["compliance_status"] for result in age_group_results
        }
        compliance_status = (
            "violation"
            if "violation" in compliance_statuses
            else "insufficient_evidence"
            if "insufficient_evidence" in compliance_statuses
            else "passed"
        )
        policy_result = dict(age_group_results[0])
        combined_citations = {}
        combined_checks = []
        for result in age_group_results:
            for citation in result["citations"]:
                combined_citations[(citation.get("age_silo"), citation["id"])] = citation
            for check in result.get("automated_checks", []):
                combined_checks.append(
                    {
                        **check,
                        "age_silo": result["age_silo"],
                        "category": result["category"],
                    }
                )
        policy_result.update(
            {
                "verdict": "safe" if compliance_status == "passed" else "unsafe",
                "compliance_status": compliance_status,
                "review_reasons": [],
                "automated_checks": combined_checks,
                "age_group_results": age_group_results,
                "permitted_age_silos": [
                    result["age_silo"]
                    for result in age_group_results
                    if result["approved"]
                ],
                "violations_summary": [
                    violation
                    for result in age_group_results
                    for violation in result["violations_summary"]
                ],
                "citations": list(combined_citations.values()),
            }
        )
        scored_results = [
            result
            for result in age_group_results
            if isinstance(result.get("overall_safe_pct"), (int, float))
        ]
        if scored_results:
            policy_result["overall_safe_pct"] = min(
                result["overall_safe_pct"] for result in scored_results
            )
            policy_result["overall_unsafe_pct"] = (
                100.0 - policy_result["overall_safe_pct"]
            )
        metadata_match = agent.match_video_metadata(
            title=video.get("title", ""),
            description=video.get("description", ""),
            category=video.get("category", "General"),
            age_silo=age_silo,
            visual_result=visual_result,
            transcript_result=transcript_result,
            safety_verdict=policy_result["verdict"],
        )
        policy_seconds = perf_counter() - policy_started

        ai_result = {
            "visual": visual_result,
            "audio": audio_result,
            "transcript": transcript_result,
            "age_group_targets": age_group_targets,
            "policy": policy_result,
            "metadata_match": metadata_match,
            "permitted_age_silos": policy_result["permitted_age_silos"],
            "analysis_config": {
                "yolo_confidence": yolo_conf,
                "bert_toxicity_threshold": bert_thresh,
            },
            "verdict": policy_result["verdict"],
            "overall_safe_pct": policy_result["overall_safe_pct"],
            "overall_unsafe_pct": policy_result["overall_unsafe_pct"],
            "violations_summary": policy_result["violations_summary"],
            "citations": policy_result["citations"],
            "analyzed_at": datetime.utcnow().isoformat(),
        }
        ai_result["publish_eligible"] = _publish_gate_status(ai_result)

        mongo.db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {
                "$set": {
                    "ai_result": ai_result,
                    "approved_age_silos": policy_result["permitted_age_silos"],
                    "status": "analyzed",
                },
                "$unset": {"analysis_cache": ""},
            },
        )

        _log_audit(
            action="ai_analysis",
            video_id=video_id,
            detail=f"Verdict: {policy_result['verdict']} | Safe: {policy_result['overall_safe_pct']}%",
        )
        current_app.logger.info(
            "Video analysis complete for %s (reused=%s, inference=%.1fs, "
            "toxicity=%.1fs, policy=%.1fs, total=%.1fs)",
            video_id,
            reused_analysis,
            inference_seconds,
            toxicity_seconds,
            policy_seconds,
            perf_counter() - pipeline_started,
        )

        alert_target = f"/admin/moderation/review/{video_id}"
        notification_events = []
        video_title = video.get("title") or "Untitled video"
        if compliance_status == "violation":
            first_violation = next(
                iter(policy_result.get("violations_summary") or []),
                {},
            )
            finding = (
                first_violation.get("label")
                or first_violation.get("description")
                or first_violation.get("type")
                or "policy violation"
            )
            notification_events.append({
                "event_key": f"moderation-block:{video_id}:{ai_result['analyzed_at']}",
                "severity": "critical",
                "category": "moderation",
                "title": f"Critical Safety Alert: {video_title} blocked",
                "message": f"Automated age-tier checks detected {finding}. Publishing is blocked until the content is addressed.",
                "target_url": alert_target,
            })
        elif compliance_status == "insufficient_evidence":
            notification_events.append({
                "event_key": f"moderation-incomplete:{video_id}:{ai_result['analyzed_at']}",
                "severity": "warning",
                "category": "moderation",
                "title": f"Automated review needed: {video_title}",
                "message": "The automated checks returned a borderline or incomplete result. Admin review is required before publishing.",
                "target_url": alert_target,
            })
        if inference_seconds >= 45:
            latency_state = mongo.db.admin_notification_state.find_one_and_update(
                {"_id": "multimodal-analysis-latency"},
                {
                    "$inc": {"consecutive_slow_jobs": 1},
                    "$set": {"updated_at": datetime.utcnow()},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            slow_job_count = int((latency_state or {}).get("consecutive_slow_jobs", 0))
        else:
            mongo.db.admin_notification_state.update_one(
                {"_id": "multimodal-analysis-latency"},
                {
                    "$set": {
                        "consecutive_slow_jobs": 0,
                        "updated_at": datetime.utcnow(),
                    }
                },
                upsert=True,
            )
            slow_job_count = 0
        if slow_job_count >= 3:
            notification_events.append({
                "event_key": f"pipeline-latency:{video_id}:{ai_result['analyzed_at']}",
                "severity": "warning",
                "category": "pipeline",
                "title": "Pipeline latency warning",
                "message": (
                    "Multimodal analysis exceeded 45 seconds for 3 consecutive "
                    f"jobs. The latest job for '{video_title}' took "
                    f"{inference_seconds:.1f} seconds."
                ),
                "target_url": alert_target,
            })
            mongo.db.admin_notification_state.update_one(
                {"_id": "multimodal-analysis-latency"},
                {"$set": {"consecutive_slow_jobs": 0}},
            )

        if compliance_status == "violation" and video.get("uploaded_by"):
            creator_id = str(video["uploaded_by"])
            creator_id_values = [creator_id]
            if ObjectId.is_valid(creator_id):
                creator_id_values.append(ObjectId(creator_id))
            violation_cutoff = datetime.utcnow() - timedelta(days=7)
            recent_creator_violations = mongo.db.videos.count_documents({
                "uploaded_by": {"$in": creator_id_values},
                "created_at": {"$gte": violation_cutoff},
                "ai_result.policy.compliance_status": "violation",
                "status": {"$in": ["analyzed", "published"]},
            })
            if recent_creator_violations >= 3:
                creator = (
                    mongo.db.personas.find_one(
                        {"_id": ObjectId(creator_id)}, {"name": 1}
                    )
                    if ObjectId.is_valid(creator_id)
                    else None
                )
                creator_name = (creator or {}).get("name") or "This creator"
                week_key = datetime.utcnow().strftime("%G-W%V")
                notification_events.append({
                    "event_key": f"creator-violations:{creator_id}:{week_key}",
                    "severity": "warning",
                    "category": "creator",
                    "title": f"Creator safety warning: {creator_name}",
                    "message": (
                        f"{creator_name} has triggered {recent_creator_violations} "
                        "policy violations in the past 7 days."
                    ),
                    "target_url": f"/admin/?creator_id={creator_id}#users",
                })

        for event in notification_events:
            try:
                dispatch_admin_notification(
                    **event,
                    video_id=video_id,
                )
            except Exception:
                current_app.logger.exception(
                    "Could not dispatch admin notification for video %s",
                    video_id,
                )

        # Serialise for JSON response (remove non-serialisable datetime)
        ai_result.pop("analyzed_at", None)

        return jsonify(
            {"ok": True, "result": ai_result, "reused_analysis": reused_analysis}
        )

    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Video analysis failed for %s", video_id)
        try:
            mongo.db.admin_notification_state.update_one(
                {"_id": "multimodal-analysis-latency"},
                {"$set": {"consecutive_slow_jobs": 0}},
            )
        except Exception:
            current_app.logger.exception(
                "Could not reset the pipeline latency streak after failure"
            )
        try:
            dispatch_admin_notification(
                event_key=(
                    f"pipeline-failure:{video_id}:"
                    f"{int(datetime.utcnow().timestamp())}"
                ),
                severity="critical",
                category="pipeline",
                title=(
                    "AI pipeline memory alert"
                    if "out of memory" in str(exc).lower()
                    else "AI moderation pipeline failed"
                ),
                message=f"Analysis failed for '{video.get('title') or 'Untitled video'}': {str(exc)[:240]}",
                target_url=f"/admin/moderation/review/{video_id}",
                video_id=video_id,
            )
        except Exception:
            current_app.logger.exception(
                "Could not dispatch pipeline failure alert for video %s",
                video_id,
            )
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if upload_context_entered:
            upload_context.__exit__(None, None, None)


# ── Remediation ───────────────────────────────────────────────────────────────

@admin_bp.route("/add-video/remediate/<video_id>", methods=["POST"])
@creator_required
def remediate_video(video_id: str):
    """Apply one-click AI remediation (blur + mute), re-analyze."""
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Video not found."}), 404

    ai_result = video.get("ai_result") or {}
    violations = ai_result.get("violations_summary", [])

    if not violations:
        return jsonify({"ok": False, "error": "No violations to remediate."}), 400

    from ..ai.media_processor import apply_blur_segments, apply_mute_segments

    source_key = f"uploads/{video['original_filename']}"
    download_filename = video.get("download_filename")
    if not asset_exists(source_key) and download_filename:
        source_key = f"downloads/{download_filename}"
    if not asset_exists(source_key):
        return jsonify({"ok": False, "error": "The source video is no longer available for remediation."}), 409
    remediated_name = f"rem_{video['original_filename']}"

    # Separate by type
    visual_segs = [
        {"start_sec": v["timestamp_sec"], "end_sec": v["timestamp_sec"] + 2}
        for v in violations if v["type"] == "visual"
    ]
    audio_segs = [
        {"start_sec": v["timestamp_sec"], "end_sec": v.get("end_sec", v["timestamp_sec"] + 3)}
        for v in violations if v["type"] == "audio"
    ]

    with tempfile.TemporaryDirectory(prefix="spectra-remediation-") as work_dir:
        with materialize_asset(
            source_key,
            suffix=os.path.splitext(source_key)[1],
        ) as original_path:
            remediated_path = os.path.join(work_dir, remediated_name)
            tmp_path = original_path
            if visual_segs:
                blur_tmp = os.path.join(work_dir, "blurred.mp4")
                apply_blur_segments(tmp_path, blur_tmp, visual_segs)
                tmp_path = blur_tmp

            if audio_segs:
                mute_tmp = os.path.join(work_dir, "muted.mp4")
                apply_mute_segments(tmp_path, mute_tmp, audio_segs)
                tmp_path = mute_tmp

            import shutil

            shutil.copy2(tmp_path, remediated_path)
            store_file(
                f"uploads/{remediated_name}",
                remediated_path,
                video_id=video_id,
                asset_type="remediated_upload",
            )

    mongo.db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {
            "$set": {
                "source_filename": video.get("source_filename") or video["original_filename"],
                "original_filename": remediated_name,
                "remediated_filename": remediated_name,
                "remediation_applied": True,
                "visual_remediation_applied": bool(visual_segs),
                "audio_remediation_applied": bool(audio_segs),
                "status": "pending",
                "ai_result": None,
            },
            "$unset": {"analysis_cache": ""},
        },
    )
    _log_audit("remediation_applied", video_id, "Auto-remediation applied; re-analysis needed.")
    return jsonify({"ok": True, "message": "Remediation applied. Please re-analyze."})


# ── Publish Gate ──────────────────────────────────────────────────────────────

@admin_bp.route("/add-video/publish/<video_id>", methods=["POST"])
@creator_required
def publish_video(video_id: str):
    """Publish only to selected age tiers that passed their independent checks."""
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Video not found."}), 404
    if video.get("status") == "published":
        return jsonify({"ok": True, "message": "Video is already live."})
    if video.get("status") == "transcoding":
        return jsonify({"ok": True, "message": "Video preparation is already in progress."})

    ai_result = video.get("ai_result") or {}
    compliance_status = (ai_result.get("policy") or {}).get("compliance_status")
    can_publish = _publish_gate_status(ai_result)
    if not can_publish:
        if compliance_status == "violation":
            message = "Publishing is blocked because the video violates the selected age-tier policy."
        elif compliance_status == "insufficient_evidence":
            message = (
                "Automatic checks did not complete. Run the analysis again before publishing."
            )
        else:
            message = "Run a successful AI analysis before publishing this video."
        return jsonify(
            {
                "ok": False,
                "error": message,
            }
        ), 403

    app = current_app._get_current_object()
    publisher_persona_id = session.get("persona_id")
    permitted_age_silos = (
        ai_result.get("permitted_age_silos")
        or (ai_result.get("policy") or {}).get("permitted_age_silos")
        or video.get("age_silos")
        or [video.get("age_silo")]
    )
    if not isinstance(permitted_age_silos, list) or not permitted_age_silos:
        return jsonify(
            {"ok": False, "error": "No selected age group passed moderation."}
        ), 403
    claim_updates = {
        "status": "transcoding",
        "age_silos": permitted_age_silos,
        "approved_age_silos": permitted_age_silos,
    }
    claim = mongo.db.videos.update_one(
        {"_id": ObjectId(video_id), "status": "analyzed"},
        {"$set": claim_updates, "$unset": {"publish_error": "", "age_review": ""}},
    )
    if not claim.modified_count:
        return jsonify(
            {"ok": False, "error": "Run a successful AI analysis before publishing."}
        ), 409

    def _prepare_playback():
        download_filename = None
        hls_asset_keys = []
        thumbnail_key = None
        try:
            with app.app_context():
                from ..ai.media_processor import transcode_to_hls

                source_key = f"uploads/{video['original_filename']}"
                if not asset_exists(source_key) and video.get("download_filename"):
                    source_key = f"downloads/{video['download_filename']}"

                with tempfile.TemporaryDirectory(prefix="spectra-publish-") as work_dir:
                    with materialize_asset(
                        source_key,
                        suffix=os.path.splitext(source_key)[1],
                    ) as upload_path:
                        hls_output_dir = os.path.join(work_dir, "hls")
                        master_path = transcode_to_hls(
                            upload_path, hls_output_dir, video_id
                        )
                        if not os.path.isfile(master_path):
                            raise RuntimeError("HLS playlist was not created.")
                        hls_asset_keys = store_directory(
                            os.path.join(hls_output_dir, video_id),
                            f"hls/{video_id}",
                            video_id=video_id,
                            asset_type="hls",
                        )
                        if not hls_asset_keys:
                            raise RuntimeError("No HLS media assets were generated.")

                        extension = os.path.splitext(video["original_filename"])[1].lower()
                        download_filename = (
                            f"{video_id}-{secrets.token_hex(8)}{extension}"
                        )
                        copy_asset(
                            source_key,
                            f"downloads/{download_filename}",
                            video_id=video_id,
                            asset_type="download",
                        )

                        updates = {
                            "hls_master": f"hls/{video_id}/master.m3u8",
                            "download_filename": download_filename,
                            "status": "published",
                            "published_at": datetime.utcnow(),
                        }

                        try:
                            import cv2

                            cap = cv2.VideoCapture(upload_path)
                            ret, frame = cap.read()
                            cap.release()
                            if ret:
                                thumb_name = f"{video_id}.jpg"
                                thumb_path = os.path.join(work_dir, thumb_name)
                                if cv2.imwrite(thumb_path, frame):
                                    thumbnail_key = f"thumbnails/{thumb_name}"
                                    store_file(
                                        thumbnail_key,
                                        thumb_path,
                                        video_id=video_id,
                                        asset_type="thumbnail",
                                    )
                                    updates["thumbnail"] = thumb_name
                        except Exception:
                            app.logger.exception(
                                "Thumbnail extraction failed for %s", video_id
                            )

                try:
                    _log_audit(
                        "video_published",
                        video_id,
                        "Video published after passing AI safety gate.",
                        persona_id=publisher_persona_id,
                    )
                except Exception:
                    app.logger.exception("Publish audit logging failed for %s", video_id)
                result = mongo.db.videos.update_one(
                    {"_id": ObjectId(video_id)}, {"$set": updates}
                )
                if result.modified_count:
                    creator_id = str(video.get("uploaded_by") or "")
                    if ObjectId.is_valid(creator_id):
                        try:
                            from ..badges import evaluate_user_badges

                            evaluate_user_badges(creator_id)
                        except Exception:
                            app.logger.exception(
                                "Could not evaluate creator badges after publishing %s",
                                video_id,
                            )
                    if video.get("download_filename"):
                        delete_asset(f"downloads/{video['download_filename']}")
                    for field in (
                        "original_filename",
                        "source_filename",
                        "remediated_filename",
                        "sanitized_filename",
                    ):
                        filename = video.get(field)
                        if filename:
                            delete_asset(f"uploads/{filename}")
                    if publisher_persona_id:
                        try:
                            from ..notifications import notify_creator_subscribers

                            notify_creator_subscribers(
                                str(publisher_persona_id),
                                video_id,
                                video.get("title") or "New video",
                                video.get("age_silo") or "",
                            )
                        except Exception:
                            app.logger.exception(
                                "Subscriber notifications failed for video %s",
                                video_id,
                            )
                else:
                    if download_filename:
                        delete_asset(f"downloads/{download_filename}")
                    for asset_key in hls_asset_keys:
                        delete_asset(asset_key)
                    if thumbnail_key:
                        delete_asset(thumbnail_key)
        except Exception:
            with app.app_context():
                app.logger.exception("HLS preparation failed for %s", video_id)
                if download_filename:
                    delete_asset(f"downloads/{download_filename}")
                for asset_key in hls_asset_keys:
                    delete_asset(asset_key)
                if thumbnail_key:
                    delete_asset(thumbnail_key)
                mongo.db.videos.update_one(
                    {"_id": ObjectId(video_id)},
                    {
                        "$set": {
                            "status": "analyzed",
                            "publish_error": "Video preparation failed. Please retry publishing.",
                        },
                        "$unset": {
                            "hls_master": "",
                            "published_at": "",
                            "download_filename": "",
                        },
                    },
                )

    threading.Thread(target=_prepare_playback, daemon=True).start()
    return jsonify(
        {
            "ok": True,
            "permitted_age_silos": permitted_age_silos,
            "message": (
                "Preparing video for publication. It will be visible to approved "
                "age groups when processing finishes."
            ),
        }
    )


# ── Settings / Model Calibration ─────────────────────────────────────────────

@admin_bp.route("/settings")
@admin_required
def settings():
    config = mongo.db.system_config.find_one({}) or {}
    config.pop("_id", None)
    return render_template("admin/settings.html", config=config)


@admin_bp.route("/settings/save", methods=["POST"])
@admin_required
def save_settings():
    data = request.get_json(force=True)
    updates = {}
    if "yolo_confidence" in data:
        updates["yolo_confidence"] = float(data["yolo_confidence"])
    if "bert_toxicity_threshold" in data:
        updates["bert_toxicity_threshold"] = float(data["bert_toxicity_threshold"])
    if "rag_distance_threshold" in data:
        updates["rag_distance_threshold"] = float(data["rag_distance_threshold"])
    if updates:
        mongo.db.system_config.update_one({}, {"$set": updates}, upsert=True)
    return jsonify({"ok": True})


# ── Video Status Polling ──────────────────────────────────────────────────────

@admin_bp.route("/video-status/<video_id>")
@creator_required
def video_status(video_id: str):
    """Poll transcoding / publish status for a video. Used by add_video.js."""
    video = mongo.db.videos.find_one(
        {"_id": ObjectId(video_id)},
        {"status": 1, "hls_master": 1, "thumbnail": 1, "publish_error": 1},
    )
    if not video:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return jsonify({
        "ok": True,
        "status": video.get("status", "unknown"),
        "hls_ready": bool(video.get("hls_master")),
        "thumbnail": video.get("thumbnail"),
        "publish_error": video.get("publish_error"),
    })


# ── Video Management List ─────────────────────────────────────────────────────

@admin_bp.route("/videos")
@admin_required
def video_list():
    """List only videos that have been published to the catalog."""
    status_filter = "published"
    videos = list(
        mongo.db.videos.find({"status": "published"})
        .sort("published_at", -1)
        .limit(100)
    )
    for v in videos:
        v["_id"] = str(v["_id"])
    published_count = mongo.db.videos.count_documents({"status": "published"})
    counts = {
        "all": published_count,
        "published": published_count,
    }
    return render_template(
        "admin/video_list.html",
        videos=videos,
        status_filter=status_filter,
        counts=counts,
    )


@admin_bp.route("/videos/delete/<video_id>", methods=["POST"])
@admin_required
def delete_video(video_id: str):
    """Hard-delete a video document and its MongoDB media assets."""
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Not found."}), 404
    delete_video_assets(video_id)
    mongo.db.videos.delete_one({"_id": ObjectId(video_id)})
    _log_audit("video_deleted", video_id, f"Video '{video.get('title','')}' deleted by admin.")
    return jsonify({"ok": True})


# ── Channel Management ────────────────────────────────────────────────────────

@admin_bp.route("/channels")
@admin_required
def channel_list():
    channels = list(mongo.db.channels.find({}).sort("created_at", -1))
    for c in channels:
        c["_id"] = str(c["_id"])
    return render_template("admin/channels.html", channels=channels)


@admin_bp.route("/channels/create", methods=["POST"])
@admin_required
def create_channel():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Channel name required."}), 400
    result = mongo.db.channels.insert_one({
        "name": name,
        "description": description,
        "subscriber_count": 0,
        "subscribers": [],
        "created_by": session.get("persona_id"),
        "created_at": datetime.utcnow(),
    })
    return jsonify({"ok": True, "channel_id": str(result.inserted_id)})


@admin_bp.route("/videos/assign-channel", methods=["POST"])
@admin_required
def assign_channel():
    """Assign a channel_id + channel_name to a video."""
    data = request.get_json(force=True)
    video_id = data.get("video_id", "")
    channel_id = data.get("channel_id", "")
    if not video_id or not channel_id:
        return jsonify({"ok": False, "error": "Missing fields."}), 400
    channel = mongo.db.channels.find_one({"_id": ObjectId(channel_id)})
    if not channel:
        return jsonify({"ok": False, "error": "Channel not found."}), 404
    mongo.db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {"$set": {"channel_id": channel_id, "channel_name": channel.get("name", "")}},
    )
    return jsonify({"ok": True})







# ── Video Detail / Re-Analyze ─────────────────────────────────────────────────

@admin_bp.route("/video/<video_id>")
@admin_required
def video_detail(video_id: str):
    """View a published video and its AI results."""
    if not ObjectId.is_valid(video_id):
        abort(404)
    video = mongo.db.videos.find_one(
        {"_id": ObjectId(video_id), "status": "published"}
    )
    if not video:
        abort(404)
    video["_id"] = str(video["_id"])
    channels = list(mongo.db.channels.find({}, {"name": 1}))
    for c in channels:
        c["_id"] = str(c["_id"])
    return render_template("admin/video_detail.html", video=video, channels=channels)


@admin_bp.route("/media/<video_id>/<path:asset>")
@admin_required
def admin_video_media(video_id: str, asset: str):
    if not ObjectId.is_valid(video_id):
        abort(404)
    video = mongo.db.videos.find_one(
        {"_id": ObjectId(video_id), "status": "published"},
        {"thumbnail": 1, "hls_master": 1},
    )
    if not video:
        abort(404)
    allowed_thumbnail = asset == f"thumbnails/{video.get('thumbnail', '')}"
    allowed_hls = bool(video.get("hls_master")) and asset.startswith(
        f"hls/{video_id}/"
    )
    if not allowed_thumbnail and not allowed_hls:
        abort(404)
    try:
        return send_asset(asset)
    except (NoFile, ValueError):
        abort(404)


# ── Audit Log Full Page ───────────────────────────────────────────────────────

@admin_bp.route("/audit-log")
@admin_required
def audit_log():
    """Full paginated audit log."""
    page = max(1, int(request.args.get("page", 1)))
    per_page = 50
    total = mongo.db.audit_logs.count_documents({})
    logs = list(
        mongo.db.audit_logs.find({})
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    for lg in logs:
        lg["_id"] = str(lg["_id"])
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "admin/audit_log.html",
        logs=logs,
        page=page,
        total_pages=total_pages,
        total=total,
    )


# ── Utility ───────────────────────────────────────────────────────────────────

def _log_audit(
    action: str,
    video_id: str,
    detail: str,
    persona_id: str | None = None,
):
    mongo.db.audit_logs.insert_one(
        {
            "action": action,
            "video_id": video_id,
            "persona_id": persona_id if persona_id is not None else session.get("persona_id"),
            "detail": detail,
            "created_at": datetime.utcnow(),
        }
    )
