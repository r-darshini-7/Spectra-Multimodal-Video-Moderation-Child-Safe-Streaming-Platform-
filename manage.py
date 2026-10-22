#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from getpass import getpass

from dotenv import load_dotenv
from pymongo.errors import DuplicateKeyError
from pymongo import MongoClient
from werkzeug.security import generate_password_hash


load_dotenv(os.path.join(os.path.dirname(__file__), "spectra", ".env"))


def create_superuser():
    username = input("Admin Username: ").strip().lower()
    email = input("Admin Email: ").strip().lower()
    if not username or not email:
        raise ValueError("Admin username and email are required.")

    while True:
        password = getpass("Master Password (12+ characters): ")
        confirmation = getpass("Confirm Master Password: ")
        if password != confirmation:
            print("Passwords do not match; please try again.")
            continue
        if len(password) < 12:
            print("Password must contain at least 12 characters.")
            continue
        break

    now = datetime.now(timezone.utc)
    user = {
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(
            password, method="pbkdf2:sha256", salt_length=16
        ),
        "role": "admin",
        "is_superuser": True,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGODB_DB", "spectra_db")
    with MongoClient(mongo_uri, serverSelectionTimeoutMS=5000) as client:
        users = client[db_name]["users"]
        users.create_index("username", unique=True, partialFilterExpression={"username": {"$type": "string"}})
        users.create_index("email", unique=True, partialFilterExpression={"email": {"$type": "string"}})
        if users.find_one({"$or": [{"email": email}, {"username": username}]}):
            raise ValueError(f"Super-admin '{username}' or email '{email}' already exists.")
        try:
            result = users.insert_one(user)
        except DuplicateKeyError as exc:
            raise ValueError("A super-admin with this username or email already exists.") from exc
    print(f"Super-admin created successfully (id: {result.inserted_id}).")


def migrate_media():
    from flask import Flask
    from spectra.extensions import mongo
    from spectra.media_storage import migrate_configured_local_media

    static_folder = os.path.join(os.path.dirname(__file__), "spectra", "static")
    app = Flask("spectra-media-migration", static_folder=static_folder)
    mongo_uri = (
        os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
        + os.getenv("MONGODB_DB", "spectra_db")
    )
    app.config["MONGO_URI"] = mongo_uri
    app.config["MONGODB_URI"] = mongo_uri
    app.config["UPLOAD_FOLDER"] = os.getenv(
        "UPLOAD_FOLDER", os.path.join(static_folder, "uploads")
    )
    app.config["HLS_OUTPUT_FOLDER"] = os.getenv(
        "HLS_OUTPUT_FOLDER", os.path.join(static_folder, "hls")
    )
    app.config["DOWNLOAD_FOLDER"] = os.getenv(
        "DOWNLOAD_FOLDER", os.path.join(static_folder, "downloads")
    )
    mongo.init_app(app)
    with app.app_context():
        imported = migrate_configured_local_media(app)
    print(
        f"Imported {imported} legacy media file(s) into MongoDB GridFS. "
        "Local source files were retained."
    )


def main(argv=None):
    if argv not in (None, [], ["createsuperuser"], ["migrate-media"]):
        print(
            "Usage: python manage.py {createsuperuser|migrate-media}",
            file=sys.stderr,
        )
        return 2
    if argv == []:
        print("Usage: python manage.py {createsuperuser|migrate-media}")
        return 0
    try:
        if argv == ["migrate-media"]:
            migrate_media()
        else:
            create_superuser()
    except ValueError as exc:
        print(f"[Spectra] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[Spectra] Failed to create super-admin: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
