"""
Authentication Routes Blueprint — Registration, PIN login, session management.
"""
from __future__ import annotations

import hashlib
import secrets
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps

from bson import ObjectId
import phonenumbers
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from ..badges import evaluate_user_badges
from ..extensions import mongo
from ..media_storage import delete_video_assets

auth_bp = Blueprint("auth", __name__)

# ── Age silo mapping ──────────────────────────────────────────────────────────
AGE_GROUP_SILOS = {
    "preschool": "preschool",
    "kids": "kids",
    "tweens": "tweens",
    "teens": "teens",
    "unrestricted": "unrestricted",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def _normalize_international_phone(phone_number: str) -> str | None:
    try:
        parsed_number = phonenumbers.parse(phone_number, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed_number):
        return None
    return phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)


def _matching_personas(pin_hash: str, account_id=None):
    query = {"pin_hash": pin_hash}
    if account_id:
        query["account_id"] = account_id
    return list(mongo.db.personas.find(query).limit(2))


def _set_session(account_id: str, persona_id: str, role: str, age_silo: str, name: str):
    session.clear()
    session["account_id"] = account_id
    session["persona_id"] = persona_id
    session["role"] = "viewer"
    session["age_silo"] = age_silo
    session["persona_name"] = name
    session["logged_in"] = True


# ── Registration ──────────────────────────────────────────────────────────────

@auth_bp.route("/register", methods=["GET"])
def register_page():
    return render_template("auth/register.html")


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone_number = str(data.get("phone_number") or "").strip()
    age_group = (data.get("age_group") or "unrestricted").strip().lower()
    password = data.get("password") or ""
    members = data.get("members") or []  # [{"name", "age_group", "pin"}, ...]
    primary_pin = str(data.get("primary_pin") or "").strip()

    if not full_name or not email or not phone_number or not password:
        return jsonify({"ok": False, "error": "Name, email, phone number, and password are required."}), 400
    phone_number = _normalize_international_phone(phone_number)
    if not phone_number:
        return jsonify({
            "ok": False,
            "error": "Enter a valid international phone number including its country code.",
        }), 400

    if len(primary_pin) != 5 or not primary_pin.isascii() or not primary_pin.isdigit():
        return jsonify({"ok": False, "error": "Your PIN must be exactly 5 digits."}), 400

    if not isinstance(members, list):
        return jsonify({"ok": False, "error": "Household members must be a list."}), 400

    household_members = []
    used_pins = {primary_pin}
    for member in members:
        if not isinstance(member, dict):
            return jsonify({"ok": False, "error": "Invalid household member."}), 400
        member_name = (member.get("name") or "").strip()
        if not member_name:
            continue
        member_age_group = (member.get("age_group") or "kids").strip().lower()
        member_pin = str(member.get("pin") or "").strip()
        if len(member_pin) != 5 or not member_pin.isascii() or not member_pin.isdigit():
            return jsonify({"ok": False, "error": "Each household member needs a 5-digit PIN."}), 400
        if member_pin in used_pins:
            return jsonify({"ok": False, "error": "Each household member must have a unique PIN."}), 409
        used_pins.add(member_pin)
        household_members.append((member_name, member_age_group, member_pin))

    if mongo.db.accounts.find_one({"email": email}):
        return jsonify({"ok": False, "error": "Email already registered."}), 409

    account_id = str(ObjectId())
    mongo.db.accounts.insert_one(
        {
            "_id": ObjectId(account_id),
            "full_name": full_name,
            "email": email,
            "phone_number": phone_number,
            "password_hash": generate_password_hash(password),
            "age_group": age_group,
            "created_at": datetime.utcnow(),
        }
    )

    # Insert primary persona
    _create_persona(account_id, full_name, age_group, primary_pin, is_primary=True)

    # Insert household sub-personas
    for m_name, m_age, m_pin in household_members:
        _create_persona(account_id, m_name, m_age, m_pin)

    return jsonify({"ok": True, "redirect": url_for("auth.login_page")}), 201


def _create_persona(
    account_id: str,
    name: str,
    age_group: str,
    pin: str,
    is_primary: bool = False,
):
    age_silo = AGE_GROUP_SILOS.get(age_group, "unrestricted")
    mongo.db.personas.insert_one(
        {
            "account_id": ObjectId(account_id),
            "name": name,
            "age_group": age_group,
            "age_silo": age_silo,
            "pin_hash": _hash_pin(pin),
            "role": "viewer",
            "is_primary": is_primary,
            "created_at": datetime.utcnow(),
        }
    )


# ── Password Sign-In ──────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET"])
def login_page():
    # Auto-seed demo account on first visit so the app works out-of-the-box
    try:
        _ensure_demo_account()
    except Exception:
        pass
    token = secrets.token_urlsafe(32)
    session["auth_csrf"] = token
    return render_template("auth/login.html", csrf_token=token)


@auth_bp.route("/password-auth", methods=["POST"])
def password_auth():
    """Authenticate a super-admin or the primary household account."""
    data = request.get_json(force=True)
    identifier = str(data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    csrf_token = request.headers.get("X-CSRF-Token", "")
    expected_csrf = session.get("auth_csrf", "")
    if not csrf_token or not expected_csrf or not secrets.compare_digest(csrf_token, expected_csrf):
        abort(400, description="Invalid or missing CSRF token.")
    if not identifier or not password:
        return jsonify({"ok": False, "error": "Email or admin username and password are required."}), 400

    now = datetime.utcnow()
    login_key = hashlib.sha256(
        f"{identifier.casefold()}|{request.remote_addr or 'unknown'}".encode()
    ).hexdigest()
    cutoff = now - timedelta(minutes=15)
    if mongo.db.admin_login_attempts.count_documents(
        {"key": login_key, "created_at": {"$gte": cutoff}}
    ) >= 5:
        return jsonify({"ok": False, "error": "Too many sign-in attempts. Try again in 15 minutes."}), 429

    admin = mongo.db.users.find_one({
        "$or": [{"username": identifier}, {"email": identifier}],
        "role": "admin",
        "is_superuser": True,
        "status": {"$ne": "suspended"},
    })
    admin_password_hash = (admin or {}).get("password_hash", "")
    if admin and admin_password_hash and check_password_hash(admin_password_hash, password):
        mongo.db.admin_login_attempts.delete_many({"key": login_key})
        session.clear()
        session.permanent = True
        session["admin_authenticated"] = True
        session["admin_user_id"] = str(admin["_id"])
        session["role"] = "admin"
        session["admin_csrf"] = secrets.token_urlsafe(32)
        mongo.db.audit_logs.insert_one({
            "action": "admin_login",
            "detail": f"Super-admin {admin.get('username', admin.get('email'))} signed in.",
            "admin_user_id": str(admin["_id"]),
            "created_at": now,
        })
        return jsonify({"ok": True, "redirect": url_for("admin_ops.console"), "mode": "admin"})

    account = mongo.db.accounts.find_one({"email": identifier})
    if account and account.get("status") == "suspended":
        return jsonify({"ok": False, "error": "This account is suspended. Contact Spectra support."}), 403
    if not account or not password or not check_password_hash(account.get("password_hash", ""), password):
        return jsonify({"ok": False, "error": "Invalid email or password."}), 401

    persona = mongo.db.personas.find_one({"account_id": account["_id"], "is_primary": True})
    if not persona:
        return jsonify({"ok": False, "error": "Primary household profile not found."}), 500

    session.clear()
    session["pending_account_id"] = str(account["_id"])
    return jsonify({"ok": True, "redirect": url_for("auth.passkey_page")})


@auth_bp.route("/passkey", methods=["GET"])
def passkey_page():
    if not session.get("pending_account_id"):
        return redirect(url_for("auth.login_page"))
    return render_template("auth/passkey.html")


@auth_bp.route("/passkey-auth", methods=["POST"])
def passkey_auth():
    if not session.get("pending_account_id"):
        return jsonify({"ok": False, "error": "Sign in again to continue."}), 401

    data = request.get_json(force=True)
    passkey = str(data.get("passkey") or "").strip()
    if len(passkey) != 5 or not passkey.isdigit():
        return jsonify({"ok": False, "error": "Passkey must be exactly 5 digits."}), 400

    account_id = ObjectId(session["pending_account_id"])
    account = mongo.db.accounts.find_one({"_id": account_id}, {"status": 1})
    if not account or account.get("status") == "suspended":
        session.clear()
        return jsonify({"ok": False, "error": "This account is suspended or unavailable."}), 403
    matches = _matching_personas(_hash_pin(passkey), account_id)
    if len(matches) > 1:
        return jsonify({"ok": False, "error": "This passkey matches multiple profiles. Use a unique passkey."}), 409
    if not matches:
        return jsonify({"ok": False, "error": "Invalid passkey."}), 401

    persona = matches[0]
    age_silo = persona.get("age_silo", "unrestricted")
    _set_session(
        str(account_id), str(persona["_id"]), persona.get("role", "viewer"),
        age_silo, persona.get("name", "User"),
    )
    return jsonify({"ok": True, "redirect": url_for("user.feed", silo=age_silo)})


# ── Hot-Swap PIN Switch ───────────────────────────────────────────────────────

@auth_bp.route("/switch-pin", methods=["POST"])
def switch_pin():
    """In-app quick-switch: swap active persona by PIN without full logout."""
    data = request.get_json(force=True)
    pin = str(data.get("pin") or "").strip()

    if len(pin) != 5 or not pin.isdigit():
        return jsonify({"ok": False, "error": "Invalid PIN."}), 400

    pin_hash = _hash_pin(pin)
    account_id = session.get("account_id")
    matches = _matching_personas(pin_hash, ObjectId(account_id) if account_id else None)

    if len(matches) > 1:
        return jsonify({"ok": False, "error": "This PIN matches multiple profiles. Use a unique PIN."}), 409

    persona = matches[0] if matches else None

    if not persona:
        return jsonify({"ok": False, "error": "PIN not recognised."}), 401

    account_id = str(persona["account_id"])
    persona_id = str(persona["_id"])
    role = persona.get("role", "viewer")
    age_silo = persona.get("age_silo", "unrestricted")
    name = persona.get("name", "User")

    _set_session(account_id, persona_id, role, age_silo, name)

    landing = url_for("user.feed", silo=age_silo)

    return jsonify({"ok": True, "redirect": landing, "name": name, "silo": age_silo})


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"))



# ── Profile Settings ──────────────────────────────────────────────────────────

def _profile_account():
    account_id = session.get("account_id")
    if not session.get("logged_in") or not ObjectId.is_valid(str(account_id or "")):
        return None
    return mongo.db.accounts.find_one({"_id": ObjectId(account_id)})


def _settings_access_required(view):
    @wraps(view)
    def decorated(*args, **kwargs):
        if (
            session.get("logged_in")
            and session.get("age_silo") not in {"teens", "unrestricted"}
        ):
            if request.method == "GET":
                return redirect(url_for("user.feed"))
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "Settings are only available for Teen and Unrestricted "
                        "profiles."
                    ),
                }
            ), 403
        return view(*args, **kwargs)

    return decorated


def _password_matches(account, password):
    return bool(
        account
        and isinstance(password, str)
        and password
        and check_password_hash(account.get("password_hash", ""), password)
    )


def _remove_persona_owned_videos(persona_ids):
    videos = list(mongo.db.videos.find({"uploaded_by": {"$in": persona_ids}}))
    video_ids = [video["_id"] for video in videos]
    video_id_strings = [str(video_id) for video_id in video_ids]

    for video in videos:
        delete_video_assets(str(video["_id"]))
    mongo.db.videos.delete_many({"uploaded_by": {"$in": persona_ids}})
    mongo.db.moderation_queue.delete_many({
        "video_id": {"$in": video_ids + video_id_strings}
    })
    mongo.db.channels.delete_many({"created_by": {"$in": persona_ids}})
    return video_ids, video_id_strings


@auth_bp.route("/profile", methods=["GET"])
@_settings_access_required
def profile_page():
    account = _profile_account()
    if not account:
        return redirect(url_for("auth.login_page"))
    household_members = list(
        mongo.db.personas.find({"account_id": account["_id"]}).sort(
            [("created_at", 1), ("_id", 1)]
        )
    )
    primary_persona = next(
        (member for member in household_members if member.get("is_primary")),
        None,
    )
    settings = account.get("settings") or {}
    history_filter = request.args.get("period", "all")
    period_starts = {
        "today": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        "week": datetime.utcnow() - timedelta(days=7),
        "month": datetime.utcnow() - timedelta(days=30),
    }
    history_query = {"persona_id": str(session.get("persona_id"))}
    if history_filter in period_starts:
        history_query["watched_at"] = {"$gte": period_starts[history_filter]}
    records = list(mongo.db.watch_history.find(history_query).sort("watched_at", -1).limit(100))
    video_ids = [
        ObjectId(record["video_id"])
        for record in records
        if ObjectId.is_valid(record.get("video_id", ""))
    ]
    active_silo = session.get("age_silo")
    videos = {
        str(video["_id"]): video
        for video in mongo.db.videos.find({
            "_id": {"$in": video_ids},
            "age_silo": active_silo,
            "status": "published",
        })
    } if video_ids else {}
    for video in videos.values():
        video["_id"] = str(video["_id"])
    history_items = [
        {"record": record, "video": videos[record["video_id"]]}
        for record in records
        if record.get("video_id") in videos
    ]
    history_groups = []
    for item in history_items:
        watched_at = item["record"].get("watched_at")
        group_key = watched_at.strftime("%Y-%m-%d") if isinstance(watched_at, datetime) else ""
        if not history_groups or history_groups[-1]["key"] != group_key:
            history_groups.append({
                "key": group_key,
                "label": watched_at.strftime("%d %b").lstrip("0") if isinstance(watched_at, datetime) else "Earlier",
                "items": [],
            })
        history_groups[-1]["items"].append(item)
    return render_template(
        "auth/profile.html",
        account=account,
        persona=primary_persona or {},
        household_members=household_members,
        settings=settings,
        history_items=history_items,
        history_videos=videos,
        history_filter=history_filter,
        history_groups=history_groups,
    )


@auth_bp.route("/profile/achievements", methods=["GET"])
@_settings_access_required
def profile_achievements():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    persona_id = session.get("persona_id")
    if not ObjectId.is_valid(str(persona_id or "")):
        return jsonify({"ok": False, "error": "Active profile not found."}), 404
    persona = mongo.db.personas.find_one({
        "_id": ObjectId(str(persona_id)),
        "account_id": account["_id"],
    })
    if not persona:
        return jsonify({"ok": False, "error": "Active profile not found."}), 404

    return jsonify({"ok": True, **evaluate_user_badges(persona["_id"])})


@auth_bp.route("/profile/update", methods=["POST"])
@_settings_access_required
def update_profile():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Profile details are required."}), 400
    name = data.get("name", "")
    age_group = data.get("age_group", "")
    phone_number = data.get("phone_number", "")
    if not isinstance(name, str) or not isinstance(age_group, str) or not isinstance(phone_number, str):
        return jsonify({"ok": False, "error": "Enter a name and a valid age group."}), 400
    name = name.strip()
    age_group = age_group.strip().lower()
    phone_number = phone_number.strip()
    if not name or len(name) > 40 or age_group not in AGE_GROUP_SILOS:
        return jsonify({"ok": False, "error": "Enter a name and a valid age group."}), 400
    normalized_phone = _normalize_international_phone(phone_number) if phone_number else ""
    if phone_number and not normalized_phone:
        return jsonify({"ok": False, "error": "Enter a valid phone number with its country code."}), 400
    primary = mongo.db.personas.find_one(
        {"account_id": account["_id"], "is_primary": True}
    )
    if not primary:
        return jsonify({"ok": False, "error": "Primary household profile not found."}), 404
    mongo.db.accounts.update_one(
        {"_id": account["_id"]},
        {"$set": {
            "full_name": name,
            "age_group": age_group,
            "phone_number": normalized_phone,
        }},
    )
    mongo.db.personas.update_one(
        {"_id": primary["_id"]},
        {"$set": {
            "name": name,
            "age_group": age_group,
            "age_silo": AGE_GROUP_SILOS[age_group],
        }},
    )
    if str(primary["_id"]) == str(session.get("persona_id")):
        session["persona_name"] = name
        session["age_silo"] = AGE_GROUP_SILOS[age_group]
    return jsonify({"ok": True, "message": "Profile details saved."})


@auth_bp.route("/profile/settings", methods=["POST"])
@_settings_access_required
def update_profile_settings():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "A settings update is required."}), 400
    settings = account.get("settings") or {}
    if "theme" in data:
        if not isinstance(data["theme"], str) or data["theme"] not in {"light", "dark", "system"}:
            return jsonify({"ok": False, "error": "Choose light, dark, or system theme."}), 400
        settings["theme"] = data["theme"]
    if "watch_limit_minutes" in data:
        limit = data["watch_limit_minutes"]
        if limit is not None and (type(limit) is not int or limit not in {30, 60, 90, 120}):
            return jsonify({"ok": False, "error": "Choose a valid daily watch-time limit."}), 400
        settings["watch_limit_minutes"] = limit
    if "notifications_enabled" in data:
        if not isinstance(data["notifications_enabled"], bool):
            return jsonify({"ok": False, "error": "Notification setting must be on or off."}), 400
        settings["notifications_enabled"] = data["notifications_enabled"]
    mongo.db.accounts.update_one(
        {"_id": account["_id"]},
        {"$set": {"settings": settings}},
    )
    return jsonify({"ok": True, "settings": settings})


@auth_bp.route("/profile/password/verify", methods=["POST"])
@_settings_access_required
def verify_profile_password():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Profile details are required."}), 400
    if not _password_matches(account, data.get("password", "")):
        return jsonify({"ok": False, "error": "Password is incorrect."}), 401
    return jsonify({
        "ok": True,
        "message": "Password verified. Passwords are stored securely and cannot be viewed; you can change or reset it below.",
    })


def _send_password_recovery_link(account, reset_url):
    mail_host = (current_app.config.get("SMTP_HOST") or "").strip()
    sender = (
        (current_app.config.get("SMTP_FROM") or "").strip()
        or (current_app.config.get("SMTP_USERNAME") or "").strip()
    )
    if not mail_host or not sender:
        return (
            "Email delivery is not configured. Set SMTP_HOST and SMTP_FROM "
            "(or SMTP_USERNAME) in spectra/.env, then restart the app."
        )
    message = EmailMessage()
    message["Subject"] = "Reset your Spectra password"
    message["From"] = sender
    message["To"] = account["email"]
    message.set_content(
        "Use the secure link below to choose a new Spectra password and confirm it. "
        "For your security, do not reply to this email with your password.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 30 minutes and can only be used once. "
        "If you did not request a password reset, you can ignore this email."
    )
    try:
        with smtplib.SMTP(
            mail_host, int(current_app.config.get("SMTP_PORT", 587)), timeout=10
        ) as smtp:
            smtp.starttls()
            username = current_app.config.get("SMTP_USERNAME")
            if username:
                smtp.login(username, current_app.config.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError):
        current_app.logger.exception(
            "Failed to email password recovery link for account %s", account["_id"]
        )
        return "The recovery link could not be emailed. Check your SMTP settings."
    return None


@auth_bp.route("/profile/recovery/request", methods=["POST"])
@_settings_access_required
def request_profile_recovery():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    return _start_password_recovery(account, request_ip=request.remote_addr or "unknown")


def _start_password_recovery(account, request_ip=None):
    now = datetime.utcnow()
    recent_count = mongo.db.password_recovery_requests.count_documents({
        "account_id": account["_id"],
        "created_at": {"$gte": now - timedelta(hours=1)},
    })
    if recent_count >= 3:
        return jsonify({"ok": False, "error": "Too many recovery requests. Try again later."}), 429
    token = secrets.token_urlsafe(32)
    request_id = ObjectId()
    mongo.db.password_recovery_requests.insert_one({
        "_id": request_id,
        "account_id": account["_id"],
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "created_at": now,
        "expires_at": now + timedelta(minutes=30),
        "used": False,
        "request_ip": request_ip,
    })
    reset_url = url_for("auth.reset_password_page", token=token, _external=True)
    delivery_error = _send_password_recovery_link(account, reset_url)
    if delivery_error:
        mongo.db.password_recovery_requests.delete_one({"_id": request_id})
        return jsonify({"ok": False, "error": delivery_error}), 503
    return jsonify({
        "ok": True,
        "message": "If your account exists, a password reset link was sent to your registered email.",
    })


@auth_bp.route("/recover", methods=["GET"])
def recover_password_page():
    return render_template("auth/recover.html")


@auth_bp.route("/recover/<token>", methods=["GET"])
def reset_password_page(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    challenge = mongo.db.password_recovery_requests.find_one({
        "token_hash": token_hash,
        "used": False,
        "expires_at": {"$gt": datetime.utcnow()},
    })
    if not challenge:
        return render_template("auth/recover.html", reset_token="", invalid_reset_link=True)
    return render_template("auth/recover.html", reset_token=token, invalid_reset_link=False)


@auth_bp.route("/recovery/request", methods=["POST"])
def request_password_recovery():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Enter your registered email address."}), 400
    email = data.get("email", "")
    if not isinstance(email, str) or not email.strip():
        return jsonify({"ok": False, "error": "Enter your registered email address."}), 400
    account = mongo.db.accounts.find_one({"email": email.strip().lower()})
    now = datetime.utcnow()
    remote_addr = request.remote_addr or "unknown"
    recent_ip_requests = mongo.db.password_recovery_requests.count_documents({
        "request_ip": remote_addr,
        "created_at": {"$gte": now - timedelta(hours=1)},
    })
    if recent_ip_requests >= 10:
        return jsonify({"ok": False, "error": "Too many recovery requests. Try again later."}), 429
    if not account:
        mongo.db.password_recovery_requests.insert_one({
            "account_id": None,
            "request_ip": remote_addr,
            "created_at": now,
            "expires_at": now + timedelta(hours=1),
            "used": True,
        })
        return jsonify({
            "ok": True,
            "message": "If an account exists for that email, a password reset link will be sent.",
        })
    return _start_password_recovery(account, request_ip=remote_addr)


@auth_bp.route("/profile/recovery/verify", methods=["POST"])
@auth_bp.route("/recovery/verify", methods=["POST"])
def verify_profile_recovery():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Password reset details are required."}), 400
    token = str(data.get("token") or "")
    new_password = str(data.get("new_password") or "")
    confirm_password = str(data.get("confirm_password") or "")
    if not token:
        return jsonify({"ok": False, "error": "The reset link is invalid or expired. Request a new link."}), 400
    if not isinstance(new_password, str) or not isinstance(confirm_password, str):
        return jsonify({"ok": False, "error": "Enter valid new password values."}), 400
    if len(new_password) < 6 or new_password != confirm_password:
        return jsonify({"ok": False, "error": "Enter matching passwords with at least 6 characters."}), 400
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    challenge = mongo.db.password_recovery_requests.find_one({
        "token_hash": token_hash,
        "used": False,
        "expires_at": {"$gt": datetime.utcnow()},
    })
    if not challenge:
        return jsonify({"ok": False, "error": "The reset link is invalid or expired. Request a new link."}), 400
    account = mongo.db.accounts.find_one({"_id": challenge["account_id"]})
    if not account:
        return jsonify({"ok": False, "error": "The reset link is invalid or expired. Request a new link."}), 400
    claimed = mongo.db.password_recovery_requests.update_one(
        {
            "_id": challenge["_id"],
            "used": False,
            "expires_at": {"$gt": datetime.utcnow()},
        },
        {"$set": {"used": True, "used_at": datetime.utcnow()}},
    )
    if not claimed.matched_count:
        return jsonify({"ok": False, "error": "The reset link is invalid or expired. Request a new link."}), 400
    mongo.db.accounts.update_one(
        {"_id": account["_id"]},
        {"$set": {"password_hash": generate_password_hash(new_password)}},
    )
    return jsonify({"ok": True, "message": "Password reset successfully."})


@auth_bp.route("/profile/password", methods=["POST"])
@_settings_access_required
def update_profile_password():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Password details are required."}), 400
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""
    confirm_password = data.get("confirm_password") or ""
    if not _password_matches(account, old_password):
        return jsonify({"ok": False, "error": "Current password is incorrect."}), 401
    if not isinstance(new_password, str) or not isinstance(confirm_password, str):
        return jsonify({"ok": False, "error": "Enter valid new password values."}), 400
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "New password must be at least 6 characters."}), 400
    if new_password != confirm_password:
        return jsonify({"ok": False, "error": "New passwords do not match."}), 400

    mongo.db.accounts.update_one(
        {"_id": account["_id"]},
        {"$set": {"password_hash": generate_password_hash(new_password)}},
    )
    return jsonify({"ok": True, "message": "Password updated successfully."})


@auth_bp.route("/profile/avatar", methods=["POST"])
@_settings_access_required
def update_profile_avatar():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Profile photo is required."}), 400
    avatar_data = data.get("avatar_data") or ""
    if not isinstance(avatar_data, str) or not avatar_data.startswith(("data:image/png;", "data:image/jpeg;", "data:image/webp;")) or len(avatar_data) > 4_000_000:
        return jsonify({"ok": False, "error": "Choose a PNG, JPG, or WEBP image smaller than 3 MB."}), 400

    primary = mongo.db.personas.find_one(
        {"account_id": account["_id"], "is_primary": True}
    )
    if not primary:
        return jsonify({"ok": False, "error": "Primary profile not found."}), 404
    mongo.db.personas.update_one(
        {"_id": primary["_id"]},
        {"$set": {"avatar_data": avatar_data}},
    )
    return jsonify({"ok": True, "message": "Profile image updated successfully."})


@auth_bp.route("/profile/members", methods=["POST"])
@_settings_access_required
def add_profile_member():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "A family member is required."}), 400
    name = data.get("name", "")
    age_group = data.get("age_group", "kids")
    pin = str(data.get("pin") or "").strip()
    if not isinstance(name, str) or not isinstance(age_group, str):
        return jsonify({"ok": False, "error": "Enter a valid name and age group."}), 400
    name = name.strip()
    age_group = age_group.strip().lower()
    if not _password_matches(account, data.get("password", "")):
        return jsonify({"ok": False, "error": "Enter the correct account password to add a family member."}), 401
    if not name or len(name) > 40 or len(pin) != 5 or not pin.isascii() or not pin.isdigit():
        return jsonify({"ok": False, "error": "Enter a name and a 5-digit passkey."}), 400
    if age_group not in AGE_GROUP_SILOS:
        return jsonify({"ok": False, "error": "Invalid age group."}), 400
    avatar_data = data.get("avatar_data") or ""
    if not isinstance(avatar_data, str):
        return jsonify({"ok": False, "error": "Invalid profile photo."}), 400
    if avatar_data and (
        not avatar_data.startswith(("data:image/png;", "data:image/jpeg;", "data:image/webp;"))
        or len(avatar_data) > 4_000_000
    ):
        return jsonify({"ok": False, "error": "Choose a PNG, JPG, or WEBP image smaller than 3 MB."}), 400

    if mongo.db.personas.find_one({"account_id": account["_id"], "pin_hash": _hash_pin(pin)}):
        return jsonify({"ok": False, "error": "That passkey is already used in this household."}), 409

    _create_persona(str(account["_id"]), name, age_group, pin)
    if avatar_data:
        mongo.db.personas.update_one(
            {"account_id": account["_id"], "name": name, "pin_hash": _hash_pin(pin)},
            {"$set": {"avatar_data": avatar_data}},
        )
    return jsonify({"ok": True, "message": "Family member added successfully."}), 201


@auth_bp.route("/profile/members/<member_id>", methods=["PATCH", "DELETE"])
@_settings_access_required
def update_or_delete_profile_member(member_id):
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    if not ObjectId.is_valid(member_id):
        return jsonify({"ok": False, "error": "Invalid family member."}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "A family member update is required."}), 400
    if not _password_matches(account, data.get("password", "")):
        return jsonify({"ok": False, "error": "Enter the correct account password to manage family members."}), 401
    member = mongo.db.personas.find_one({
        "_id": ObjectId(member_id),
        "account_id": account["_id"],
        "is_primary": {"$ne": True},
    })
    if not member:
        return jsonify({"ok": False, "error": "Family member not found."}), 404
    if request.method == "DELETE":
        persona_id = str(member["_id"])
        video_ids, video_id_strings = _remove_persona_owned_videos([persona_id])
        mongo.db.personas.delete_one({"_id": member["_id"]})
        for collection in (
            "comments", "feedbacks", "viewer_activity", "watch_history",
            "watch_later", "notifications", "playlists",
        ):
            mongo.db[collection].delete_many({"$or": [
                {"persona_id": persona_id},
                {"user_id": persona_id},
                {"video_id": {"$in": video_ids + video_id_strings}},
            ]})
        return jsonify({"ok": True, "message": "Family member deleted."})

    updates = {}
    if "name" in data:
        name = data.get("name", "")
        if not isinstance(name, str):
            return jsonify({"ok": False, "error": "Enter a valid name up to 40 characters."}), 400
        name = name.strip()
        if not name or len(name) > 40:
            return jsonify({"ok": False, "error": "Enter a name up to 40 characters."}), 400
        updates["name"] = name
    if "age_group" in data:
        age_group = data.get("age_group", "")
        if not isinstance(age_group, str):
            return jsonify({"ok": False, "error": "Invalid age group."}), 400
        age_group = age_group.strip().lower()
        if age_group not in AGE_GROUP_SILOS:
            return jsonify({"ok": False, "error": "Invalid age group."}), 400
        updates["age_group"] = age_group
        updates["age_silo"] = AGE_GROUP_SILOS[age_group]
    if "pin" in data:
        pin = str(data.get("pin") or "").strip()
        if len(pin) != 5 or not pin.isascii() or not pin.isdigit():
            return jsonify({"ok": False, "error": "Passkey must be exactly 5 digits."}), 400
        duplicate = mongo.db.personas.find_one({
            "account_id": account["_id"],
            "pin_hash": _hash_pin(pin),
            "_id": {"$ne": member["_id"]},
        })
        if duplicate:
            return jsonify({"ok": False, "error": "That passkey is already used in this household."}), 409
        updates["pin_hash"] = _hash_pin(pin)
    if "avatar_data" in data:
        avatar_data = data.get("avatar_data") or ""
        if not isinstance(avatar_data, str) or (avatar_data and (
            not avatar_data.startswith(("data:image/png;", "data:image/jpeg;", "data:image/webp;"))
            or len(avatar_data) > 4_000_000
        )):
            return jsonify({"ok": False, "error": "Choose a PNG, JPG, or WEBP image smaller than 3 MB."}), 400
        updates["avatar_data"] = avatar_data
    if not updates:
        return jsonify({"ok": False, "error": "Nothing to update."}), 400
    mongo.db.personas.update_one({"_id": member["_id"]}, {"$set": updates})
    return jsonify({"ok": True, "message": "Family member updated."})


@auth_bp.route("/profile/delete", methods=["POST"])
@_settings_access_required
def delete_profile_account():
    account = _profile_account()
    if not account:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Account password is required."}), 400
    if not _password_matches(account, data.get("password", "")):
        return jsonify({"ok": False, "error": "Account password is incorrect."}), 401

    account_id = account["_id"]
    personas = list(mongo.db.personas.find({"account_id": account_id}, {"_id": 1}))
    persona_ids = [str(persona["_id"]) for persona in personas]
    persona_object_ids = [persona["_id"] for persona in personas]
    video_ids, video_id_strings = _remove_persona_owned_videos(persona_ids)
    mongo.db.channels.update_many(
        {"subscribers": {"$in": persona_object_ids}},
        {"$pull": {"subscribers": {"$in": persona_object_ids}}},
    )
    mongo.db.personas.delete_many({"account_id": account_id})
    for collection in (
        "comments", "feedbacks", "viewer_activity", "watch_history",
        "watch_later", "notifications", "playlists", "password_recovery_requests",
    ):
        mongo.db[collection].delete_many({"$or": [
            {"account_id": account_id},
            {"persona_id": {"$in": persona_ids + persona_object_ids}},
            {"user_id": {"$in": persona_ids + [str(account_id)]}},
            {"video_id": {"$in": video_ids + video_id_strings}},
        ]})
    mongo.db.accounts.delete_one({"_id": account_id})
    session.clear()
    return jsonify({"ok": True, "redirect": url_for("auth.login_page")})



# ── Demo seed ─────────────────────────────────────────────────────────────────

@auth_bp.route("/seed-demo", methods=["POST"])
def seed_demo():
    """Seed demo account + personas for first-run testing."""
    _ensure_demo_account()
    return jsonify({"ok": True, "message": "Demo account seeded."})


def _ensure_demo_account():
    """Create the demo Spectra household if it doesn't exist."""
    from werkzeug.security import generate_password_hash

    email = "demo@spectra.tv"
    if mongo.db.accounts.find_one({"email": email}):
        return

    account_id = str(ObjectId())
    mongo.db.accounts.insert_one(
        {
            "_id": ObjectId(account_id),
            "full_name": "Darshini",
            "email": email,
            "password_hash": generate_password_hash("spectra123"),
            "age_group": "unrestricted",
            "created_at": datetime.utcnow(),
        }
    )

    demo_personas = [
        ("Darshini", "unrestricted", "12345", True),  # Admin
        ("Kids Profile", "kids", "54321", False),      # Kids Safe
        ("Preschool", "preschool", "99999", False),    # Preschool
        ("Teen Profile", "teens", "13579", False),      # Teens
    ]
    for name, age_group, pin, is_primary in demo_personas:
        _create_persona(account_id, name, age_group, pin, is_primary)
