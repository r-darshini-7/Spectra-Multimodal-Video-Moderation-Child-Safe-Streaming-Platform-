"""
Spectra Platform — Flask Application Factory
"""
import os
from datetime import datetime, timedelta
from bson import ObjectId
from flask import Flask, abort, request, session
from dotenv import dotenv_values, load_dotenv

from .extensions import mongo
from .routes.auth_routes import auth_bp
from .routes.user_routes import user_bp
from .routes.interaction_routes import interaction_bp
from .routes.admin_routes import admin_bp, admin_ops_bp


def create_app() -> Flask:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    env_file_values = dotenv_values(env_path)

    def configured_value(name: str) -> str | None:
        environment_value = os.getenv(name)
        if environment_value and environment_value.strip():
            return environment_value
        return env_file_values.get(name)

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ── Jinja2 custom filters ─────────────────────────────────────────────────
    import re as _re
    from markupsafe import Markup as _Markup

    @app.template_filter("replace_timestamps")
    def replace_timestamps(text: str) -> str:
        """Convert HH:MM or MM:SS timestamps in comment text into seek links."""
        pattern = _re.compile(r'\b((?:\d{1,2}:)?\d{1,2}:\d{2})\b')

        def _to_link(m: _re.Match) -> str:
            ts = m.group(1)
            parts = ts.split(":")
            if len(parts) == 3:
                sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                sec = int(parts[0]) * 60 + int(parts[1])
            safe = _re.sub(r'[<>&"\']', '', ts)
            return f'<a href="javascript:void(0)" class="timestamp-link" data-sec="{sec}">{safe}</a>'

        return _Markup(pattern.sub(_to_link, text or ""))

    # ── Core Configuration ────────────────────────────────────────────────────
    secret_key = os.getenv("FLASK_SECRET_KEY", "")
    production = os.getenv("FLASK_ENV", "").lower() == "production"
    if production and len(secret_key) < 32:
        raise RuntimeError("FLASK_SECRET_KEY must contain at least 32 characters in production.")
    if not secret_key:
        secret_key = os.urandom(32).hex()
        app.logger.warning("FLASK_SECRET_KEY is unset; using an ephemeral development key.")
    app.config["SECRET_KEY"] = secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SMTP_HOST=configured_value("SMTP_HOST"),
        SMTP_PORT=int(configured_value("SMTP_PORT") or "587"),
        SMTP_USERNAME=configured_value("SMTP_USERNAME"),
        SMTP_PASSWORD=configured_value("SMTP_PASSWORD"),
        SMTP_FROM=configured_value("SMTP_FROM"),
    )
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    _mongo_uri = (
        os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
        + os.getenv("MONGODB_DB", "spectra_db")
    )
    # flask-pymongo ≥ 2.3 uses MONGO_URI; older builds used MONGODB_URI
    app.config["MONGO_URI"] = _mongo_uri
    app.config["MONGODB_URI"] = _mongo_uri   # fallback alias
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024 * 1024)
    )

    # ── Resolve media folders relative to the package's static directory ─────
    _static = app.static_folder  # always absolute
    app.config["UPLOAD_FOLDER"] = os.getenv(
        "UPLOAD_FOLDER", os.path.join(_static, "uploads")
    )
    app.config["HLS_OUTPUT_FOLDER"] = os.getenv(
        "HLS_OUTPUT_FOLDER", os.path.join(_static, "hls")
    )
    app.config["DOWNLOAD_FOLDER"] = os.getenv(
        "DOWNLOAD_FOLDER", os.path.join(_static, "downloads")
    )

    # ── Ensure runtime directories exist ─────────────────────────────────────
    for directory in [
        app.config["UPLOAD_FOLDER"],
        app.config["HLS_OUTPUT_FOLDER"],
        app.config["DOWNLOAD_FOLDER"],
        os.path.join(_static, "thumbnails"),
        os.path.join(_static, "vtt"),
    ]:
        os.makedirs(directory, exist_ok=True)

    # ── Extensions ───────────────────────────────────────────────────────────
    mongo.init_app(app)

    @app.before_request
    def guard_private_media_access():
        account_id = session.get("account_id")
        if session.get("logged_in") and account_id and ObjectId.is_valid(str(account_id)):
            account = mongo.db.accounts.find_one(
                {"_id": ObjectId(str(account_id))}, {"status": 1}
            )
            if account and account.get("status") == "suspended":
                session.clear()
                return redirect("/auth/login")
        if request.endpoint == "static":
            filename = (request.view_args or {}).get("filename", "")
            private_media_dirs = {"downloads", "hls", "thumbnails", "uploads", "vtt"}
            media_dir = filename.replace("\\", "/").split("/", 1)[0].lower()
            if media_dir in private_media_dirs:
                admin_id = session.get("admin_user_id")
                is_active_superuser = (
                    session.get("admin_authenticated")
                    and session.get("role") == "admin"
                    and admin_id
                    and ObjectId.is_valid(str(admin_id))
                )
                if is_active_superuser:
                    is_active_superuser = mongo.db.users.find_one(
                        {
                            "_id": ObjectId(str(admin_id)),
                            "role": "admin",
                            "is_superuser": True,
                            "status": {"$ne": "suspended"},
                        },
                        {"_id": 1},
                    ) is not None
                if not is_active_superuser:
                    abort(404)

    @app.context_processor
    def inject_current_avatar():
        avatar_data = ""
        theme_preference = "system"
        persona_id = session.get("persona_id")
        if persona_id:
            try:
                persona = mongo.db.personas.find_one(
                    {"_id": ObjectId(persona_id)}, {"avatar_data": 1}
                )
                avatar_data = (persona or {}).get("avatar_data", "")
                account_id = session.get("account_id")
                if ObjectId.is_valid(str(account_id or "")):
                    account = mongo.db.accounts.find_one(
                        {"_id": ObjectId(account_id)}, {"settings.theme": 1}
                    )
                    theme_preference = ((account or {}).get("settings") or {}).get(
                        "theme", "system"
                    )
            except Exception:
                avatar_data = ""
        return {
            "current_avatar": avatar_data,
            "current_theme": theme_preference,
        }

    # ── Blueprints ───────────────────────────────────────────────────────────
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(user_bp, url_prefix="")
    app.register_blueprint(interaction_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/studio")
    app.register_blueprint(admin_ops_bp, url_prefix="/admin")

    # ── Error handlers ────────────────────────────────────────────────────────
    from flask import render_template as _rt

    @app.errorhandler(404)
    def not_found(e):
        return _rt("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return _rt("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(e):
        return _rt("errors/500.html"), 500

    # ── Database Indexes (idempotent) ─────────────────────────────────────────
    with app.app_context():
        _init_db_indexes()
        _seed_chroma_policies()
        _cleanup_stale_media(app)

    return app


def _init_db_indexes():
    """Create MongoDB indexes for text search and uniqueness."""
    from .extensions import mongo as m

    db = m.db
    try:
        db.videos.create_index([("title", "text"), ("description", "text")])
        db.videos.create_index([("age_silo", 1), ("status", 1)])
        db.accounts.create_index([("email", 1)], unique=True)
        db.personas.create_index([("account_id", 1)])
        db.personas.create_index([("pin_hash", 1)])
        db.comments.create_index([("video_id", 1)])
        db.audit_logs.create_index([("created_at", -1)])
        db.viewer_activity.create_index([("persona_id", 1), ("occurred_at", -1)])
        db.channels.create_index([("name", 1)])
        db.notifications.create_index([("persona_id", 1), ("read", 1)])
        db.notifications.create_index(
            "notification_key", unique=True, sparse=True
        )
        db.watch_history.create_index([("persona_id", 1), ("watched_at", -1)])
        db.watch_later.create_index([("persona_id", 1)], unique=True)
        db.users.create_index(
            "username",
            unique=True,
            partialFilterExpression={"username": {"$type": "string"}},
        )
        db.users.create_index(
            "email",
            unique=True,
            partialFilterExpression={"email": {"$type": "string"}},
        )
        db.policy_rules.create_index("rule_id", unique=True, sparse=True)
        db.admin_login_attempts.create_index([("key", 1), ("created_at", 1)])
        db.admin_login_attempts.create_index("expires_at", expireAfterSeconds=0)
        db.pin_reset_requests.create_index("expires_at", expireAfterSeconds=0)
        db.password_recovery_requests.create_index("expires_at", expireAfterSeconds=0)
        db.password_recovery_requests.create_index(
            "token_hash", unique=True, sparse=True
        )

        # Seed default system_config if absent
        if db.system_config.count_documents({}) == 0:
            db.system_config.insert_one(
                {
                    "yolo_confidence": 0.45,
                    "bert_toxicity_threshold": 0.60,
                    "rag_distance_threshold": 0.75,
                }
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[DB INIT] Index/config seed skipped: {exc}")


def _seed_chroma_policies():
    """Seed ChromaDB with age-tiered platform safety policies if empty."""
    from .ai.rag_policy_agent import POLICY_DOCUMENTS, get_policy_agent

    agent = get_policy_agent()
    agent.seed_policies_if_empty()
    for policy in POLICY_DOCUMENTS:
        rule_id = policy["id"]
        text = policy["text"]
        existing = mongo.db.policy_rules.find_one({"rule_id": rule_id})
        if existing is None:
            mongo.db.policy_rules.update_one(
                {"rule_id": rule_id},
                {"$setOnInsert": {
                    "rule_id": rule_id,
                    "title": rule_id.replace("-", " ").title(),
                    "description": text,
                    "category": "guideline",
                    "age_silo": policy["silo"],
                    "threshold": 0.75,
                    "active": True,
                    "created_at": datetime.utcnow(),
                }},
                upsert=True,
            )
            existing = mongo.db.policy_rules.find_one({"rule_id": rule_id}) or {}
        agent.upsert_admin_policy(
            rule_id,
            text,
            policy["silo"],
            existing.get("category", "guideline"),
            active=existing.get("active", True),
        )

    for rule in mongo.db.policy_rules.find({"rule_id": {"$nin": [policy["id"] for policy in POLICY_DOCUMENTS]}}):
        agent.upsert_admin_policy(
            rule.get("rule_id", str(rule["_id"])),
            rule.get("description", ""),
            rule.get("age_silo", "all"),
            rule.get("category", "guideline"),
            active=rule.get("active", True),
        )


def _cleanup_stale_media(app: Flask) -> None:
    """Clean only stale files not referenced by video records."""
    from .storage_cleanup import cleanup_temp_storage

    referenced_uploads: set[str] = set()
    referenced_downloads: set[str] = set()
    retained_hls_ids: set[str] = set()
    for video in mongo.db.videos.find(
        {},
        {
            "_id": 1,
            "status": 1,
            "hls_master": 1,
            "original_filename": 1,
            "source_filename": 1,
            "remediated_filename": 1,
            "sanitized_filename": 1,
            "download_filename": 1,
        },
    ):
        download_filename = video.get("download_filename")
        if (
            video.get("status") == "published"
            and download_filename
            and os.path.basename(download_filename) == download_filename
        ):
            referenced_downloads.add(download_filename)
        if video.get("status") not in {"published", "quarantined", "rejected", "deleted"}:
            for field in (
                "original_filename",
                "source_filename",
                "remediated_filename",
                "sanitized_filename",
            ):
                filename = video.get(field)
                if filename and os.path.basename(filename) == filename:
                    referenced_uploads.add(filename)
        if video.get("status") in {"published", "transcoding"} or video.get("hls_master"):
            retained_hls_ids.add(str(video["_id"]))

    removed = cleanup_temp_storage(
        app.config["UPLOAD_FOLDER"],
        app.config["HLS_OUTPUT_FOLDER"],
        referenced_uploads=referenced_uploads,
        retained_hls_ids=retained_hls_ids,
        download_dir=app.config["DOWNLOAD_FOLDER"],
        referenced_downloads=referenced_downloads,
    )
    app.logger.info("Stale media cleanup complete: %s", removed)
