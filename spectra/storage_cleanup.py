"""Safe cleanup helpers for uploads, downloadable media, audio, and HLS media."""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterable

LOGGER = logging.getLogger(__name__)
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi"}
STALE_STORAGE_SECONDS = 7 * 24 * 60 * 60


def _contained_path(root: Path, path: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(path))) == str(root)
    except ValueError:
        return False


def delete_uploaded_files(upload_dir: str, filenames: Iterable[str | None]) -> int:
    """Remove only named media files directly inside the configured upload directory."""
    root = Path(upload_dir).resolve()
    removed = 0
    for filename in set(filenames):
        if not filename or Path(filename).name != filename:
            continue
        path = root / filename
        if path.suffix.lower() not in VIDEO_SUFFIXES or not _contained_path(root, path):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            LOGGER.exception("Could not remove processed upload %s", path)
        else:
            removed += 1
    return removed


def preserve_downloadable_file(
    upload_dir: str,
    download_dir: str,
    video_id: str,
    filename: str | None,
) -> str:
    """Copy processed source media into managed download storage and return its safe name."""
    if not re.fullmatch(r"[0-9a-fA-F]{24}", video_id):
        raise ValueError("A valid video id is required for download storage.")
    if not filename or Path(filename).name != filename:
        raise ValueError("A safe source filename is required for download storage.")

    upload_root = Path(upload_dir).resolve()
    source = upload_root / filename
    if (
        source.suffix.lower() not in VIDEO_SUFFIXES
        or source.is_symlink()
        or not source.is_file()
        or not _contained_path(upload_root, source.resolve())
    ):
        raise FileNotFoundError(f"Processed video source is unavailable: {filename}")

    download_root = Path(download_dir).resolve()
    download_root.mkdir(parents=True, exist_ok=True)
    download_filename = f"{video_id}-{time.time_ns()}{source.suffix.lower()}"
    destination = download_root / download_filename
    temporary = download_root / f".{download_filename}.tmp"
    if not _contained_path(download_root, destination):
        raise ValueError("Download destination must remain inside managed storage.")

    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.exception("Could not remove incomplete download copy %s", temporary)
        raise
    return download_filename


def delete_download_files(download_dir: str, filenames: Iterable[str | None]) -> int:
    """Remove only named MP4 files directly inside the managed download directory."""
    root = Path(download_dir).resolve()
    removed = 0
    for filename in set(filenames):
        if not filename or Path(filename).name != filename:
            continue
        path = root / filename
        if path.suffix.lower() not in VIDEO_SUFFIXES or not _contained_path(root, path):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            LOGGER.exception("Could not remove downloadable video %s", path)
        else:
            removed += 1
    return removed


def _is_stale(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except FileNotFoundError:
        return False
    except OSError:
        LOGGER.exception("Could not inspect media file age for %s", path)
        return False


def cleanup_temp_storage(
    upload_dir: str,
    hls_dir: str,
    referenced_uploads: Iterable[str] = (),
    retained_hls_ids: Iterable[str] = (),
    temp_dir: str | None = None,
    stale_after_seconds: int = STALE_STORAGE_SECONDS,
    download_dir: str | None = None,
    referenced_downloads: Iterable[str] = (),
) -> dict[str, int]:
    """Delete stale orphan media without touching referenced uploads or active HLS."""
    cutoff = time.time() - max(0, stale_after_seconds)
    upload_root = Path(upload_dir).resolve()
    hls_root = Path(hls_dir).resolve()
    download_root = Path(download_dir).resolve() if download_dir else None
    temp_root = Path(temp_dir or tempfile.gettempdir()).resolve()
    keep_uploads = set(referenced_uploads)
    keep_hls = set(retained_hls_ids)
    keep_downloads = set(referenced_downloads)
    removed = {
        "uploads": 0,
        "hls_directories": 0,
        "downloads": 0,
        "audio_files": 0,
    }

    if upload_root.is_dir():
        for path in upload_root.iterdir():
            if (
                path.is_file()
                and not path.is_symlink()
                and path.name not in keep_uploads
                and path.suffix.lower() in VIDEO_SUFFIXES
                and _is_stale(path, cutoff)
            ):
                try:
                    path.unlink()
                except OSError:
                    LOGGER.exception("Could not remove stale upload %s", path)
                else:
                    removed["uploads"] += 1

    if hls_root.is_dir():
        for path in hls_root.iterdir():
            if path.is_symlink() or not path.is_dir() or path.name in keep_hls:
                continue
            segments = [
                segment
                for segment in path.rglob("*.ts")
                if not segment.is_symlink() and _contained_path(hls_root, segment)
            ]
            if segments and all(_is_stale(segment, cutoff) for segment in segments):
                try:
                    shutil.rmtree(path)
                except OSError:
                    LOGGER.exception("Could not remove orphan HLS directory %s", path)
                else:
                    removed["hls_directories"] += 1

    if download_root and download_root.is_dir():
        for path in download_root.iterdir():
            if (
                path.is_file()
                and not path.is_symlink()
                and (
                    (
                        path.name not in keep_downloads
                        and path.suffix.lower() in VIDEO_SUFFIXES
                    )
                    or (path.name.startswith(".") and path.name.endswith(".tmp"))
                )
                and _is_stale(path, cutoff)
            ):
                try:
                    path.unlink()
                except OSError:
                    LOGGER.exception("Could not remove orphan download %s", path)
                else:
                    removed["downloads"] += 1

    if temp_root.is_dir():
        for path in temp_root.glob("spectra-audio-*.wav"):
            if path.is_file() and not path.is_symlink() and _is_stale(path, cutoff):
                try:
                    path.unlink()
                except OSError:
                    LOGGER.exception("Could not remove stale audio file %s", path)
                else:
                    removed["audio_files"] += 1

    return removed
