# Spectra

**A multimodal video moderation and child-safe streaming platform.**

Spectra is a Flask web application for reviewing and streaming videos with moderation signals from both video frames and audio transcripts. It includes separate viewer and administrative experiences, family-oriented profiles, and tools for managing and discovering content.

## Features

- **Multimodal moderation:** samples video frames for visual analysis and transcribes audio for text analysis.
- **Child-focused streaming:** supports age-appropriate viewing areas and HLS video playback.
- **Viewer accounts and profiles:** registration, sign-in, profile selection, watch history, liked videos, playlists, and a watch-later list.
- **Search and discovery:** video search, creator/channel pages, and suggestions.
- **Notifications:** in-app updates for relevant creator and viewing activity.
- **Personal analytics:** viewing time, weekly and time-of-day patterns, favorite content, creators, and completion insights.
- **Administrative tools:** review and manage videos and channels.
- **Managed media cleanup:** utilities for removing stale uploads, orphaned HLS output, and temporary audio files.

## Technology

- Python and Flask with Jinja2 templates
- MongoDB via Flask-PyMongo
- faster-whisper (CPU, int8) for audio transcription
- Ultralytics YOLO and OpenCV for sampled-frame visual analysis
- ChromaDB ephemeral client for moderation policy/vector checks
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

### 4. Run the development server

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
| `UPLOAD_FOLDER` | Incoming video files | `spectra/static/uploads` |
| `HLS_OUTPUT_FOLDER` | Generated HLS playlists and segments | `spectra/static/hls` |
| `DOWNLOAD_FOLDER` | Managed downloadable video copies | `spectra/static/downloads` |
| `MAX_CONTENT_LENGTH` | Maximum request size in bytes | `2147483648` (2 GiB) |
| `WHISPER_MODEL` | Model name listed in the example; the current audio analyzer initializes `tiny` directly | `base` in the example (not currently consumed by the analyzer) |
| `YOLO_MODEL` | Visual model weights | `yolo11n.pt` |
| `BERT_MODEL` | Text moderation model | `unitary/toxic-bert` |
| `SENTENCE_TRANSFORMER_MODEL` | Sentence embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional email recovery delivery | Not configured |

Media directories and AI model weights are generated or downloaded locally. Back up any media or database content you need to retain. The repository ignore rules exclude local uploads, HLS segments, download media, and Chroma storage.

## Project layout

```text
spectra/
  ai/                 Audio, frame, and moderation processing
  routes/             Authentication, viewer, interaction, and admin routes
  static/             CSS, JavaScript, and runtime media directories
  templates/          Jinja2 pages
  __init__.py         Flask application factory and configuration
  extensions.py       Flask extensions, including MongoDB
  notifications.py    Notification helpers
  storage_cleanup.py  Safe cleanup for temporary and stale media
  run.py              Development entry point
  requirements.txt    Python dependencies
```

## Development notes

- Run the app from the repository root so the package imports and `.env` path resolve as expected.
- Keep secrets in `spectra/.env`, not in commits.
- Check that MongoDB and FFmpeg are available when debugging upload, moderation, or playback workflows.
- Test changes against a development database and non-production media.
- No automated test command is currently documented in this repository.