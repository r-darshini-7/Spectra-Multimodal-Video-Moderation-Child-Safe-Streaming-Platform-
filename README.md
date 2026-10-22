# Spectra

**A multimodal video moderation and child-safe streaming platform.**

Spectra is a Flask web application for reviewing and streaming videos with moderation signals from both video frames and audio transcripts. It includes separate viewer and administrative experiences, family-oriented profiles, and tools for managing and discovering content.

## Features

- **Multimodal moderation:** samples video frames for visual analysis and transcribes audio for text analysis. Each selected age tier receives an independent scan score, policy result, and remediation feedback; publishing is limited to tiers that pass.
- **Child-focused streaming:** supports age-appropriate viewing areas and HLS video playback.
- **Viewer accounts and profiles:** registration, sign-in, profile selection, watch history, liked videos, playlists, and a watch-later list.
- **Search and discovery:** video search, creator/channel pages, and suggestions.
- **Notifications:** in-app updates for relevant creator and viewing activity.
- **Personal analytics:** viewing time, weekly and time-of-day patterns, favorite content, creators, and completion insights.
- **Administrative tools:** review and manage videos and channels.
- **Admin notification center:** authenticated admin alerts for moderation decisions, pipeline failures and repeated latency, creator safety warnings, and user reports, with unread counts, category filters, polling, and direct review links.
- **Temporary processing cleanup:** removes stale temporary audio-extraction files.

## Technology

- Python and Flask with Jinja2 templates
- MongoDB via Flask-PyMongo and GridFS for media
- faster-whisper (CPU, int8) for audio transcription
- Ultralytics YOLO and OpenCV for sampled-frame visual analysis
- ChromaDB ephemeral client for moderation policy/vector checks (rebuilt from MongoDB policy rules)
- Chart.js for viewer analytics

## Requirements

- Python 3.10 or newer (use a Python version supported by the packages in `spectra/requirements.txt`)
- MongoDB, local or remotely hosted
- FFmpeg available on `PATH` for audio extraction and HLS/media processing
- Windows, macOS, or Linux

AI dependencies such as PyTorch, Transformers, and faster-whisper can require substantial disk space. The first moderation run may also download model weights.

## Getting started

Run commands from the repository root.

### 1. Create and activate a virtual environment

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python` for the following commands.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. Install Python dependencies

```bash
python -m pip install -r spectra/requirements.txt
```

### 3. Configure the application

The app reads its environment file from `spectra/.env`. On first startup, `spectra/run.py` copies `spectra/.env.example` there if it does not already exist. You can also create it yourself:

```powershell
Copy-Item spectra\.env.example spectra\.env
```

On macOS or Linux:

```bash
cp spectra/.env.example spectra/.env
```

Set at least the following values in `spectra/.env`:

```dotenv
FLASK_SECRET_KEY=replace-with-a-long-random-secret
MONGODB_URI=mongodb://localhost:27017/
MONGODB_DB=spectra_db
```

Generate a secret with Python:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated value into `FLASK_SECRET_KEY`. Do not commit `.env` or put production credentials in source control. The example file also lists optional SMTP settings for account recovery and configurable model and media-storage paths.

Start MongoDB before launching Spectra. For a local MongoDB installation, the default URI and database name shown above are ready to use.

### 4. Import existing local media (if any)

Existing uploads, HLS files, downloads, thumbnails, and captions in the configured media directories can be imported into MongoDB GridFS. Run this once before serving existing videos:

```powershell
python manage.py migrate-media
```

The command is safe to rerun: already-imported assets are skipped, and local source files are retained. New media is saved directly to MongoDB.

### 5. Run the development server

From the repository root:

```powershell
python spectra\run.py
```

On macOS or Linux, use `python spectra/run.py`.

Open [http://localhost:5000](http://localhost:5000) in a browser. The development server runs with Flask debug mode; use a production WSGI server and production-safe configuration when deploying.

## Configuration

Configuration is loaded from environment variables and `spectra/.env`. Common options from `spectra/.env.example`:

| Variable | Purpose | Default |
| --- | --- | --- |
| `FLASK_SECRET_KEY` | Flask session signing key | Generated temporarily for local development if unset |
| `MONGODB_URI` | MongoDB server URI; the configured database name is appended | `mongodb://localhost:27017/` |
| `MONGODB_DB` | Database name | `spectra_db` |
| `UPLOAD_FOLDER` | Legacy upload directory imported by `manage.py migrate-media` | `spectra/static/uploads` |
| `HLS_OUTPUT_FOLDER` | Legacy HLS directory imported by `manage.py migrate-media` | `spectra/static/hls` |
| `DOWNLOAD_FOLDER` | Legacy download directory imported by `manage.py migrate-media` | `spectra/static/downloads` |
| `MAX_CONTENT_LENGTH` | Maximum request size in bytes | `2147483648` (2 GiB) |
| `WHISPER_MODEL` | Model name listed in the example; the current audio analyzer initializes `tiny` directly | `base` in the example (not currently consumed by the analyzer) |
| `YOLO_MODEL` | Visual model weights | `yolo11n.pt` |
| `BERT_MODEL` | Text moderation model | `unitary/toxic-bert` |
| `SENTENCE_TRANSFORMER_MODEL` | Sentence embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional email recovery delivery | Not configured |

Application records and persistent media are stored in MongoDB. Video uploads, HLS playlists and segments, thumbnails, captions, and downloadable copies are stored in the `media` GridFS bucket. Media-processing tools use temporary local working files, which are removed after processing. Run `python manage.py migrate-media` to copy any existing local media into GridFS; the source files are retained so the import is non-destructive. Once MongoDB has been backed up and verified, those legacy copies can be removed manually. ChromaDB's in-memory vector index is rebuilt from the policy rules stored in MongoDB. Back up the MongoDB database, including GridFS collections, and AI model weights you need to retain.

## Project layout

```text
spectra/
  ai/                 Audio, frame, and moderation processing
  routes/             Authentication, viewer, interaction, and admin routes
  static/             CSS, JavaScript, and runtime media directories
  templates/          Jinja2 pages
  __init__.py         Flask application factory and configuration
  extensions.py       Flask extensions, including MongoDB
  media_storage.py    GridFS media storage and legacy media import
  notifications.py    Notification helpers
  storage_cleanup.py  Safe cleanup for temporary and stale media
  run.py              Development entry point
  requirements.txt    Python dependencies
```

## Development notes

- Run the app from the repository root so the package imports and `.env` path resolve as expected.
- Keep secrets in `spectra/.env`, not in commits.
- Check that MongoDB and FFmpeg are available when debugging upload, moderation, or playback workflows. GridFS stores durable media; local processing files are temporary.
- Test changes against a development database and non-production media.
- Age-tier scan-score minimums are configured in `spectra/ai/rag_policy_agent.py`; age-specific policy checks are evaluated independently for every selected tier.
- The current automated visual checks recognize configured weapon-object labels, not general violence or mature-topic content; those need dedicated detectors before they can be included in tier decisions.
- No automated test command is currently documented in this repository.