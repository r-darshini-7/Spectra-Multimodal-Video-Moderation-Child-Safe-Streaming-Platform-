"""MongoDB GridFS storage for persistent media assets."""
from __future__ import annotations

import mimetypes
import os
import posixpath
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Generator, Iterator

from flask import Flask, Response, request
from gridfs import GridFSBucket
from werkzeug.utils import secure_filename

from .extensions import mongo

BUCKET_NAME = "media"
CHUNK_SIZE_BYTES = 2 * 1024 * 1024


def _validate_asset_key(asset_key: str) -> str:
    if (
        not asset_key
        or asset_key.startswith("/")
        or "\\" in asset_key
        or posixpath.normpath(asset_key) != asset_key
        or asset_key in {".", ".."}
        or any(part in {"", ".", ".."} for part in asset_key.split("/"))
    ):
        raise ValueError("Media asset keys must be safe relative paths.")
    return asset_key


def _bucket() -> GridFSBucket:
    return GridFSBucket(mongo.db, bucket_name=BUCKET_NAME)


def asset_exists(asset_key: str) -> bool:
    return (
        mongo.db[f"{BUCKET_NAME}.files"].find_one(
            {"_id": _validate_asset_key(asset_key)},
            {"_id": 1},
        )
        is not None
    )


def store_stream(
    asset_key: str,
    source: BinaryIO,
    *,
    video_id: str | None = None,
    asset_type: str | None = None,
) -> int:
    """Replace one GridFS asset from a seekable or streaming binary source."""
    asset_key = _validate_asset_key(asset_key)
    bucket = _bucket()
    files = mongo.db[f"{BUCKET_NAME}.files"]
    if files.find_one({"_id": asset_key}, {"_id": 1}):
        bucket.delete(asset_key)

    metadata = {"asset_key": asset_key}
    if video_id:
        metadata["video_id"] = str(video_id)
    if asset_type:
        metadata["asset_type"] = asset_type

    total = 0
    upload = bucket.open_upload_stream_with_id(
        asset_key,
        posixpath.basename(asset_key),
        chunk_size_bytes=CHUNK_SIZE_BYTES,
        metadata=metadata,
    )
    try:
        while True:
            chunk = source.read(CHUNK_SIZE_BYTES)
            if not chunk:
                break
            upload.write(chunk)
            total += len(chunk)
        upload.close()
    except Exception:
        upload.abort()
        raise
    return total


def store_file(
    asset_key: str,
    file_path: str,
    *,
    video_id: str | None = None,
    asset_type: str | None = None,
) -> int:
    with open(file_path, "rb") as source:
        return store_stream(
            asset_key,
            source,
            video_id=video_id,
            asset_type=asset_type,
        )


def copy_asset(
    source_key: str,
    destination_key: str,
    *,
    video_id: str | None = None,
    asset_type: str | None = None,
) -> int:
    with _bucket().open_download_stream(_validate_asset_key(source_key)) as source:
        return store_stream(
            destination_key,
            source,
            video_id=video_id,
            asset_type=asset_type,
        )


@contextmanager
def materialize_asset(
    asset_key: str,
    suffix: str = "",
) -> Generator[str, None, None]:
    """Create a temporary local working copy for tools that require a file path."""
    fd, path = tempfile.mkstemp(prefix="spectra-media-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as target:
            with _bucket().open_download_stream(_validate_asset_key(asset_key)) as source:
                shutil.copyfileobj(source, target, CHUNK_SIZE_BYTES)
        yield path
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def delete_asset(asset_key: str) -> None:
    asset_key = _validate_asset_key(asset_key)
    if asset_exists(asset_key):
        _bucket().delete(asset_key)


def delete_video_assets(video_id: str) -> int:
    files = mongo.db[f"{BUCKET_NAME}.files"]
    asset_ids = [
        item["_id"]
        for item in files.find(
            {
                "$or": [
                    {"metadata.video_id": str(video_id)},
                    {
                        "_id": {
                            "$regex": (
                                rf"^(uploads|downloads|thumbnails|vtt)/"
                                rf"(?:rem_)?{re.escape(video_id)}(?:[.-]|$)"
                            )
                        }
                    },
                    {"_id": {"$regex": rf"^hls/{re.escape(video_id)}/"}},
                ]
            },
            {"_id": 1},
        )
    ]
    for asset_id in asset_ids:
        _bucket().delete(asset_id)
    return len(asset_ids)


def store_directory(
    directory: str,
    prefix: str,
    *,
    video_id: str,
    asset_type: str,
) -> list[str]:
    """Store every regular file in a generated media directory."""
    root = Path(directory).resolve()
    stored: list[str] = []
    try:
        for path in root.rglob("*"):
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(root)
            ):
                continue
            relative = path.relative_to(root).as_posix()
            key = _validate_asset_key(posixpath.join(prefix, relative))
            store_file(key, str(path), video_id=video_id, asset_type=asset_type)
            stored.append(key)
    except Exception:
        for key in stored:
            delete_asset(key)
        raise
    return stored


def migrate_local_media(
    directories: dict[str, str],
    video_records: list[dict],
) -> int:
    """Copy pre-GridFS media into MongoDB while retaining the local source files."""
    filename_video_ids: dict[str, str] = {}
    for video in video_records:
        video_id = str(video["_id"])
        for field in (
            "original_filename",
            "source_filename",
            "remediated_filename",
            "sanitized_filename",
            "download_filename",
            "thumbnail",
            "vtt_file",
        ):
            filename = video.get(field)
            if filename:
                filename_video_ids[filename] = video_id

    migrated = 0
    for media_type, directory in directories.items():
        root = Path(directory).resolve()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if (
                path.is_symlink()
                or not path.is_file()
                or path.name.endswith(".part")
                or not path.resolve().is_relative_to(root)
            ):
                continue
            relative = path.relative_to(root).as_posix()
            key = _validate_asset_key(posixpath.join(media_type, relative))
            if not asset_exists(key):
                video_id = (
                    relative.split("/", 1)[0]
                    if media_type == "hls"
                    else filename_video_ids.get(path.name)
                )
                store_file(key, str(path), video_id=video_id, asset_type=media_type)
                migrated += 1
    return migrated


def migrate_configured_local_media(app: Flask) -> int:
    """Import legacy media from configured local directories into GridFS."""
    video_records = list(
        mongo.db.videos.find(
            {},
            {
                "_id": 1,
                "original_filename": 1,
                "source_filename": 1,
                "remediated_filename": 1,
                "sanitized_filename": 1,
                "download_filename": 1,
                "thumbnail": 1,
                "vtt_file": 1,
            },
        )
    )
    return migrate_local_media(
        {
            "uploads": app.config["UPLOAD_FOLDER"],
            "downloads": app.config["DOWNLOAD_FOLDER"],
            "hls": app.config["HLS_OUTPUT_FOLDER"],
            "thumbnails": os.path.join(app.static_folder, "thumbnails"),
            "vtt": os.path.join(app.static_folder, "vtt"),
        },
        video_records,
    )


def send_asset(
    asset_key: str,
    *,
    download_name: str | None = None,
) -> Response:
    """Stream a GridFS asset with byte-range support for browser video playback."""
    asset_key = _validate_asset_key(asset_key)
    asset = _bucket().open_download_stream(asset_key)
    length = asset.length
    start = 0
    end = length - 1
    status = 200
    range_header = request.headers.get("Range")

    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or length == 0:
            asset.close()
            return Response(status=416, headers={"Content-Range": f"bytes */{length}"})
        first, last = match.groups()
        if first:
            start = int(first)
            end = int(last) if last else length - 1
        else:
            suffix_length = int(last or 0)
            if suffix_length <= 0:
                asset.close()
                return Response(status=416, headers={"Content-Range": f"bytes */{length}"})
            start = max(0, length - suffix_length)
        if start >= length or end < start:
            asset.close()
            return Response(status=416, headers={"Content-Range": f"bytes */{length}"})
        end = min(end, length - 1)
        asset.seek(start)
        status = 206

    content_length = max(0, end - start + 1)
    suffix = posixpath.splitext(asset_key)[1].lower()
    content_type = {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
        ".vtt": "text/vtt",
    }.get(suffix) or mimetypes.guess_type(asset_key)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": content_type,
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{length}"
    if download_name:
        safe_name = secure_filename(download_name) or "download"
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'

    if request.method == "HEAD":
        asset.close()
        return Response(status=status, headers=headers)

    def chunks() -> Iterator[bytes]:
        remaining = content_length
        try:
            while remaining:
                chunk = asset.read(min(CHUNK_SIZE_BYTES, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            asset.close()

    return Response(chunks(), status=status, headers=headers, direct_passthrough=True)
