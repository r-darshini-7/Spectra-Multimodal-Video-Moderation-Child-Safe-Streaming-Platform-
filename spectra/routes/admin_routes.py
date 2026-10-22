"""
Admin Routes Blueprint — Creator Studio, AI pipeline, analytics, settings.
"""
from __future__ import annotations

import os
import threading
import secrets
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps

from bson import ObjectId
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

admin_bp = Blueprint("admin", __name__)
admin_ops_bp = Blueprint("admin_ops", __name__)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}
PUBLISH_SCORE_OVERRIDE_THRESHOLD = 95.0


def _publish_gate_status(ai_result: dict) -> tuple[bool, bool]:
    """Return publish eligibility and whether incomplete checks meet the score override."""
    policy_status = (ai_result.get("policy") or {}).get("compliance_status")
    if ai_result.get("verdict") == "safe" and policy_status == "passed":
        return True, False
    score = ai_result.get("overall_safe_pct")
    if (
        policy_status == "insufficient_evidence"
        and isinstance(score, (int, float))
        and not isinstance(score, bool)
        and PUBLISH_SCORE_OVERRIDE_THRESHOLD <= score <= 100
    ):
        return True, True
    return False, False


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
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = day_start - timedelta(days=1)
    published_filter = {"status": "published"}
    total = mongo.db.videos.count_documents(published_filter)
    today = mongo.db.videos.count_documents({
        **published_filter,
        "published_at": {"$gte": day_start},
    })
    yesterday = mongo.db.videos.count_documents(
        {
            **published_filter,
            "published_at": {"$gte": yesterday_start, "$lt": day_start},
        }
    )
    remediated = mongo.db.videos.count_documents({
        **published_filter,
        "moderation_decision": "approve_remediation",
    })
    clean = mongo.db.videos.count_documents({"status": "published", "moderation_decision": {"$nin": ["approve_remediation", "restore_original"]}})
    pending = None

    videos = list(
        mongo.db.videos.find({"status": "published"}).sort("published_at", -1).limit(5)
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
    return jsonify({
        "kpis": {
            "ingested": total,
            "ingested_today": today,
            "ingested_change_pct": round((today - yesterday) * 100 / yesterday, 1) if yesterday else None,
            "remediated": remediated,
            "clean_bypasses": clean,
            "pending_reviews": pending,
        },
        "recent_videos": rows,
        "pipeline": {
            "inference_pending": None,
            "audio_pending": None,
            "publish_pending": None,
        },
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


@admin_ops_bp.route("/api/moderation")
@_ops_admin_required
def api_moderation():
    return jsonify({"items": []})


@admin_ops_bp.route("/api/moderation/<video_id>/action", methods=["POST"])
@_ops_admin_required
def api_moderation_action(video_id):
    _require_csrf()
    if not ObjectId.is_valid(video_id):
        return jsonify({"ok": False, "error": "Invalid video id."}), 400
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    allowed = {"approve_remediation", "restore_original", "quarantine"}
    if action not in allowed:
        return jsonify({"ok": False, "error": "Unsupported moderation action."}), 400
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Video not found."}), 404
    if video.get("status") not in {"flagged", "analyzed"}:
        return jsonify({"ok": False, "error": "Only flagged or analyzed videos can be reviewed."}), 409
    now = datetime.utcnow()
    if action == "quarantine":
        claim = mongo.db.videos.update_one({"_id": video["_id"], "status": video["status"]}, {"$set": {
            "status": "quarantined", "moderation_decision": action,
            "moderated_at": now, "moderated_by": session["admin_user_id"],
        }})
        if not claim.modified_count:
            return jsonify({"ok": False, "error": "The video has already been updated by another moderator."}), 409
        from ..storage_cleanup import delete_uploaded_files
        delete_uploaded_files(
            current_app.config["UPLOAD_FOLDER"],
            (
                video.get("original_filename"),
                video.get("source_filename"),
                video.get("remediated_filename"),
                video.get("sanitized_filename"),
            ),
        )
        status = "quarantined"
    else:
        original_name = (
            video.get("source_filename") or video.get("original_filename")
        )
        if not original_name:
            return jsonify({"ok": False, "error": "The original media file is unavailable."}), 409
        source_path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(original_name))
        if not os.path.isfile(source_path):
            return jsonify({"ok": False, "error": "The original media file is unavailable."}), 409
        publish_source = source_path
        remediated_name = None
        if action == "approve_remediation":
            existing_remediated = video.get("remediated_filename") or video.get("sanitized_filename")
            if not existing_remediated and video.get("original_filename") != original_name:
                existing_remediated = video.get("original_filename")
            if existing_remediated:
                existing_path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(existing_remediated))
                if os.path.isfile(existing_path):
                    publish_source = existing_path
                    remediated_name = os.path.basename(existing_remediated)
            if remediated_name is None:
                violations = (video.get("ai_result") or {}).get("violations_summary") or []
                visual_result = (video.get("ai_result") or {}).get("visual") or {}
                visual_violations = visual_result.get("violations") or []
                violations = list(violations) + [
                    {
                        **item,
                        "type": "visual",
                        "end_sec": (item.get("timestamp_sec") or 0) + 2,
                    }
                    for item in visual_violations
                ]
                visual_segments = [
                    {"start_sec": float(item.get("timestamp_sec", 0)), "end_sec": float(item.get("end_sec", float(item.get("timestamp_sec", 0)) + 2))}
                    for item in violations if item.get("type") == "visual"
                ]
                audio_segments = [
                    {"start_sec": float(item.get("timestamp_sec", 0)), "end_sec": float(item.get("end_sec", float(item.get("timestamp_sec", 0)) + 3))}
                    for item in violations if item.get("type") == "audio"
                ]
                if not visual_segments and not audio_segments:
                    return jsonify({"ok": False, "error": "No supported visual or audio violation segments are available to sanitize."}), 409
                from ..ai.media_processor import apply_blur_segments, apply_mute_segments
                remediated_name = f"admin-remediated-{video_id}.mp4"
                final_path = os.path.join(current_app.config["UPLOAD_FOLDER"], remediated_name)
                working_path = source_path
                intermediate_paths = []
                if visual_segments:
                    working_path = os.path.join(current_app.config["UPLOAD_FOLDER"], f"admin-blur-{video_id}.mp4")
                    intermediate_paths.append(working_path)
                    apply_blur_segments(source_path, working_path, visual_segments)
                if audio_segments:
                    audio_output = final_path if not visual_segments else os.path.join(current_app.config["UPLOAD_FOLDER"], f"admin-bleep-{video_id}.mp4")
                    if visual_segments:
                        intermediate_paths.append(audio_output)
                    apply_mute_segments(working_path, audio_output, audio_segments)
                    working_path = audio_output
                if working_path != final_path:
                    import shutil
                    shutil.copy2(working_path, final_path)
                for intermediate in intermediate_paths:
                    if os.path.isfile(intermediate):
                        os.remove(intermediate)
                publish_source = final_path

        claim = mongo.db.videos.update_one(
            {"_id": video["_id"], "status": video["status"]},
            {"$set": {
                "status": "transcoding", "moderation_decision": action,
                "moderated_at": now, "moderated_by": session["admin_user_id"],
                **({"remediated_filename": remediated_name} if remediated_name else {}),
            }},
        )
        if not claim.modified_count:
            return jsonify({"ok": False, "error": "The video has already been updated by another moderator."}), 409

        app = current_app._get_current_object()

        def prepare_playback():
            with app.app_context():
                try:
                    from ..ai.media_processor import transcode_to_hls
                    master_path = transcode_to_hls(
                        publish_source, app.config["HLS_OUTPUT_FOLDER"], video_id
                    )
                    if not os.path.isfile(master_path):
                        raise RuntimeError("HLS playlist was not created.")
                    result = mongo.db.videos.update_one({"_id": video["_id"], "status": "transcoding"}, {"$set": {
                        "status": "published", "hls_master": f"hls/{video_id}/master.m3u8",
                        "published_at": datetime.utcnow(),
                    }})
                    if result.modified_count:
                        from ..storage_cleanup import delete_uploaded_files
                        delete_uploaded_files(
                            app.config["UPLOAD_FOLDER"],
                            (
                                video.get("original_filename"),
                                video.get("source_filename"),
                                video.get("remediated_filename"),
                                video.get("sanitized_filename"),
                                os.path.basename(publish_source),
                            ),
                        )
                except Exception:
                    app.logger.exception("Admin moderation publication failed for video %s", video_id)
                    mongo.db.videos.update_one({"_id": video["_id"], "status": "transcoding"}, {"$set": {
                        "status": "analyzed", "publish_error": "Media publication failed. Retry the moderation decision.",
                    }, "$unset": {"hls_master": "", "published_at": ""}})

        threading.Thread(target=prepare_playback, daemon=True).start()
        status = "transcoding"
    mongo.db.moderation_queue.update_many(
        {"video_id": {"$in": [video_id, ObjectId(video_id)]}},
        {"$set": {"status": "resolved", "resolved_at": now}},
    )
    _ops_audit("moderation_" + action, f"{action.replace('_', ' ').title()} for {video.get('title', video_id)}", video_id)
    return jsonify({"ok": True, "status": status})


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
        {
            "_id": 1,
            "original_filename": 1,
            "thumbnail": 1,
            "vtt_file": 1,
            "download_filename": 1,
        },
    ))
    video_ids = [str(video["_id"]) for video in videos]
    from ..storage_cleanup import delete_download_files

    for video in videos:
        delete_download_files(
            current_app.config["DOWNLOAD_FOLDER"],
            [video.get("download_filename")],
        )
        for relative, folder in [
            (video.get("original_filename"), current_app.config["UPLOAD_FOLDER"]),
            (video.get("thumbnail"), os.path.join(current_app.static_folder, "thumbnails")),
            (video.get("vtt_file"), os.path.join(current_app.static_folder, "vtt")),
        ]:
            if relative:
                path = os.path.join(folder, os.path.basename(relative))
                if os.path.isfile(path):
                    os.remove(path)
        hls_dir = os.path.abspath(os.path.join(
            current_app.config["HLS_OUTPUT_FOLDER"], str(video["_id"])
        ))
        hls_root = os.path.abspath(current_app.config["HLS_OUTPUT_FOLDER"])
        if os.path.commonpath([hls_root, hls_dir]) == hls_root and os.path.isdir(hls_dir):
            import shutil
            shutil.rmtree(hls_dir)
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
    if "video_file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    f = request.files["video_file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename."}), 400

    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Unsupported format: {ext}"}), 400

    title = (request.form.get("title") or "Untitled").strip()
    description = (request.form.get("description") or "").strip()
    age_silo = (request.form.get("age_silo") or "").strip().lower()
    category = (request.form.get("category") or "").strip()
    if not isinstance(age_silo, str) or age_silo not in AGE_GROUPS:
        return jsonify({"ok": False, "error": "Select a valid target age group."}), 400
    if not isinstance(category, str) or not category_is_allowed(age_silo, category):
        return jsonify(
            {
                "ok": False,
                "error": f"Select a category permitted for the {age_silo} age group.",
            }
        ), 400

    video_id = str(ObjectId())
    filename = f"{video_id}{ext}"
    upload_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    f.save(upload_path)

    duration_str = "0:00"
    try:
        secs = max(0, int(float(request.form.get("duration_seconds", "0"))))
        m, s = divmod(int(secs), 60)
        duration_str = f"{m}:{s:02d}"
    except Exception:
        pass

    # Insert pending document
    mongo.db.videos.insert_one(
        {
            "_id": ObjectId(video_id),
            "title": title,
            "description": description,
            "age_silo": age_silo,
            "category": category,
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
        }
    )

    return jsonify({"ok": True, "video_id": video_id, "upload_path": upload_path})


# ── AI Analysis Pipeline ──────────────────────────────────────────────────────

@admin_bp.route("/add-video/analyze/<video_id>", methods=["POST"])
@creator_required
def analyze_video(video_id: str):
    """Run the full multimodal AI pipeline on an uploaded video."""
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

    age_silo = metadata_updates.get("age_silo", video.get("age_silo", ""))
    category = metadata_updates.get("category", video.get("category", ""))
    if isinstance(age_silo, str):
        age_silo = age_silo.strip().lower()
    if isinstance(category, str):
        category = category.strip()
    if not isinstance(age_silo, str) or age_silo not in AGE_GROUPS:
        return jsonify({"ok": False, "error": "Select a valid target age group."}), 400
    if not isinstance(category, str) or not category_is_allowed(age_silo, category):
        return jsonify(
            {
                "ok": False,
                "error": f"Select a category permitted for the {age_silo} age group.",
            }
        ), 400

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

    upload_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], video["original_filename"]
    )
    age_silo = video.get("age_silo", "kids")

    # Retrieve tunable thresholds from system_config
    config = mongo.db.system_config.find_one({}) or {}
    yolo_conf = float(config.get("yolo_confidence", 0.45))
    bert_thresh = float(config.get("bert_toxicity_threshold", 0.60))
    rag_dist = float(config.get("rag_distance_threshold", 0.75))

    try:
        from ..ai.visual_analyzer import analyze_video_frames
        from ..ai.audio_analyzer import transcribe_audio, analyze_toxicity, export_vtt
        from ..ai.rag_policy_agent import get_policy_agent

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
            audio_result = analyze_toxicity(segments, threshold=bert_thresh)
            cached_analysis = {
                "visual": visual_result,
                "audio": audio_result,
                "transcript": transcript_result,
            }

        segments = transcript_result.get("segments", [])
        # 4. Export VTT captions
        vtt_filename = f"{video_id}.vtt"
        vtt_output = os.path.join(current_app.static_folder, "vtt", vtt_filename)
        if segments:
            export_vtt(segments, vtt_output)
            mongo.db.videos.update_one(
                {"_id": ObjectId(video_id)}, {"$set": {"vtt_file": vtt_filename}}
            )

        # 5. RAG policy evaluation
        agent = get_policy_agent()
        policy_result = agent.evaluate(
            visual_result, audio_result, transcript_result,
            age_silo=age_silo, distance_threshold=rag_dist,
            category=video.get("category", ""),
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

        ai_result = {
            "visual": visual_result,
            "audio": audio_result,
            "transcript": transcript_result,
            "policy": policy_result,
            "metadata_match": metadata_match,
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
        ai_result["publish_eligible"] = _publish_gate_status(ai_result)[0]

        mongo.db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {
                "$set": {"ai_result": ai_result, "status": "analyzed"},
                "$unset": {"analysis_cache": ""},
            },
        )

        _log_audit(
            action="ai_analysis",
            video_id=video_id,
            detail=f"Verdict: {policy_result['verdict']} | Safe: {policy_result['overall_safe_pct']}%",
        )

        # Serialise for JSON response (remove non-serialisable datetime)
        ai_result.pop("analyzed_at", None)

        return jsonify(
            {"ok": True, "result": ai_result, "reused_analysis": reused_analysis}
        )

    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


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

    original_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], video["original_filename"]
    )
    download_filename = video.get("download_filename")
    if (
        not os.path.isfile(original_path)
        and download_filename
        and os.path.basename(download_filename) == download_filename
        and os.path.splitext(download_filename)[1].lower()
        in {".mp4", ".mov", ".avi"}
    ):
        original_path = os.path.join(
            current_app.config["DOWNLOAD_FOLDER"], download_filename
        )
    if not os.path.isfile(original_path):
        return jsonify({"ok": False, "error": "The source video is no longer available for remediation."}), 409
    remediated_name = f"rem_{video['original_filename']}"
    remediated_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], remediated_name
    )

    # Separate by type
    visual_segs = [
        {"start_sec": v["timestamp_sec"], "end_sec": v["timestamp_sec"] + 2}
        for v in violations if v["type"] == "visual"
    ]
    audio_segs = [
        {"start_sec": v["timestamp_sec"], "end_sec": v.get("end_sec", v["timestamp_sec"] + 3)}
        for v in violations if v["type"] == "audio"
    ]

    tmp_path = original_path
    if visual_segs:
        blur_tmp = original_path + ".blur.mp4"
        apply_blur_segments(tmp_path, blur_tmp, visual_segs)
        tmp_path = blur_tmp

    if audio_segs:
        mute_tmp = original_path + ".mute.mp4"
        apply_mute_segments(tmp_path, mute_tmp, audio_segs)
        if tmp_path != original_path:
            os.remove(tmp_path)
        tmp_path = mute_tmp

    import shutil
    shutil.copy2(tmp_path, remediated_path)
    if tmp_path != original_path:
        os.remove(tmp_path)

    mongo.db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {
            "$set": {
                "source_filename": video.get("source_filename") or video["original_filename"],
                "original_filename": remediated_name,
                "remediated_filename": remediated_name,
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
    """Publish policy-passed videos or high-scoring incomplete reviews."""
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Video not found."}), 404
    if video.get("status") == "published":
        return jsonify({"ok": True, "message": "Video is already live."})
    if video.get("status") == "transcoding":
        return jsonify({"ok": True, "message": "Video preparation is already in progress."})

    ai_result = video.get("ai_result") or {}
    compliance_status = (ai_result.get("policy") or {}).get("compliance_status")
    can_publish, score_override = _publish_gate_status(ai_result)
    if not can_publish:
        if compliance_status == "violation":
            message = "Publishing is blocked because the video violates the selected age-tier policy."
        elif compliance_status == "insufficient_evidence":
            message = (
                "Automatic publishing requires a content score of at least "
                f"{PUBLISH_SCORE_OVERRIDE_THRESHOLD:.0f}% when age-tier checks are incomplete."
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
    claim_updates = {"status": "transcoding"}
    claim = mongo.db.videos.update_one(
        {"_id": ObjectId(video_id), "status": "analyzed"},
        {"$set": claim_updates, "$unset": {"publish_error": "", "age_review": ""}},
    )
    if not claim.modified_count:
        return jsonify(
            {"ok": False, "error": "Run a successful AI analysis before publishing."}
        ), 409

    def _prepare_playback():
        from ..storage_cleanup import (
            delete_download_files,
            delete_uploaded_files,
            preserve_downloadable_file,
        )

        download_filename = None
        try:
            with app.app_context():
                from ..ai.media_processor import transcode_to_hls

                upload_path = os.path.join(
                    app.config["UPLOAD_FOLDER"], video["original_filename"]
                )
                master_path = transcode_to_hls(
                    upload_path, app.config["HLS_OUTPUT_FOLDER"], video_id
                )
                if not os.path.isfile(master_path):
                    raise RuntimeError("HLS playlist was not created.")
                updates = {
                    "hls_master": f"hls/{video_id}/master.m3u8",
                    "status": "published",
                    "published_at": datetime.utcnow(),
                }
                download_filename = preserve_downloadable_file(
                    app.config["UPLOAD_FOLDER"],
                    app.config["DOWNLOAD_FOLDER"],
                    video_id,
                    video.get("original_filename"),
                )
                updates["download_filename"] = download_filename

                try:
                    import cv2
                    cap = cv2.VideoCapture(upload_path)
                    ret, frame = cap.read()
                    cap.release()
                    if ret:
                        thumb_name = f"{video_id}.jpg"
                        thumb_path = os.path.join(
                            app.static_folder, "thumbnails", thumb_name
                        )
                        if cv2.imwrite(thumb_path, frame):
                            updates["thumbnail"] = thumb_name
                except Exception:
                    app.logger.exception("Thumbnail extraction failed for %s", video_id)

                audit_detail = (
                    "Published using the 95% content-score exception; age-tier checks were incomplete."
                    if score_override
                    else "Video published after passing AI safety gate."
                )
                try:
                    _log_audit(
                        "video_published",
                        video_id,
                        audit_detail,
                        persona_id=publisher_persona_id,
                    )
                except Exception:
                    app.logger.exception("Publish audit logging failed for %s", video_id)
                result = mongo.db.videos.update_one(
                    {"_id": ObjectId(video_id)}, {"$set": updates}
                )
                if result.modified_count:
                    delete_download_files(
                        app.config["DOWNLOAD_FOLDER"],
                        [video.get("download_filename")],
                    )
                    delete_uploaded_files(
                        app.config["UPLOAD_FOLDER"],
                        (
                            video.get("original_filename"),
                            video.get("source_filename"),
                            video.get("remediated_filename"),
                            video.get("sanitized_filename"),
                        ),
                    )
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
                    delete_download_files(
                        app.config["DOWNLOAD_FOLDER"],
                        [download_filename],
                    )
        except Exception:
            with app.app_context():
                app.logger.exception("HLS preparation failed for %s", video_id)
                if download_filename:
                    delete_download_files(
                        app.config["DOWNLOAD_FOLDER"],
                        [download_filename],
                    )
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
    return jsonify({"ok": True, "message": "Preparing video for publication. It will be visible when processing finishes."})


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
    """Hard-delete a video document and its media files."""
    video = mongo.db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        return jsonify({"ok": False, "error": "Not found."}), 404
    # Remove files
    from ..storage_cleanup import delete_download_files
    delete_download_files(
        current_app.config["DOWNLOAD_FOLDER"],
        [video.get("download_filename")],
    )
    for fname in [video.get("original_filename"), video.get("thumbnail")]:
        if fname:
            for folder in [current_app.config["UPLOAD_FOLDER"],
                           os.path.join(current_app.static_folder, "thumbnails")]:
                fpath = os.path.join(folder, fname)
                if os.path.exists(fpath):
                    os.remove(fpath)
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
