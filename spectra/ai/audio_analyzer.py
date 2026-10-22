"""Audio transcription with faster-whisper and Hugging Face BERT toxicity scoring."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import wave
from threading import Lock
from typing import Dict, Any, List

import numpy as np

# Lazy-loaded models
_whisper_model = None
_bert_pipeline = None
_whisper_model_lock = Lock()
_bert_pipeline_lock = Lock()


def _get_whisper():
    global _whisper_model
    with _whisper_model_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            cpu_threads = max(1, min(4, os.cpu_count() or 1))
            _whisper_model = WhisperModel(
                "tiny",
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )
    return _whisper_model


def _get_bert():
    global _bert_pipeline
    with _bert_pipeline_lock:
        if _bert_pipeline is None:
            from transformers import pipeline as hf_pipeline
            _bert_pipeline = hf_pipeline(
                "text-classification",
                model=os.getenv("BERT_MODEL", "unitary/toxic-bert"),
                top_k=None,
            )
    return _bert_pipeline


TOXICITY_LABELS = {"toxic", "insult", "threat", "obscene", "identity_hate", "severe_toxic"}


def _measure_loud_audio(audio_path: str) -> tuple[Dict[str, Any], np.ndarray]:
    """Measure 100 ms audio levels without imposing an unspecified policy cutoff."""
    peak_dbfs = None
    max_rms_dbfs = None
    pcm_data = bytearray()

    with wave.open(audio_path, "rb") as audio:
        if audio.getsampwidth() != 2:
            raise ValueError("Audio loudness measurement requires 16-bit PCM WAV.")
        sample_rate = audio.getframerate()
        window_frames = max(1, round(sample_rate * 0.1))
        frame_offset = 0

        while True:
            frame_count = min(window_frames, audio.getnframes() - frame_offset)
            if frame_count <= 0:
                break
            frame_data = audio.readframes(frame_count)
            pcm_data.extend(frame_data)
            samples = np.frombuffer(frame_data, dtype="<i2").astype(np.float32) / 32768.0
            frame_offset += frame_count
            if not samples.size:
                continue

            rms = float(np.sqrt(np.mean(np.square(samples))))
            peak = float(np.max(np.abs(samples)))
            window_peak_dbfs = 20 * np.log10(max(peak, 1e-8))
            peak_dbfs = (
                window_peak_dbfs
                if peak_dbfs is None
                else max(peak_dbfs, window_peak_dbfs)
            )
            rms_dbfs = 20 * np.log10(max(rms, 1e-8))
            max_rms_dbfs = (
                rms_dbfs if max_rms_dbfs is None else max(max_rms_dbfs, rms_dbfs)
            )

    audio_samples = np.frombuffer(pcm_data, dtype="<i2").astype(np.float32)
    audio_samples /= 32768.0
    audio_levels = {
        "available": True,
        "peak_dbfs": round(float(peak_dbfs), 2) if peak_dbfs is not None else None,
        "max_window_rms_dbfs": (
            round(float(max_rms_dbfs), 2) if max_rms_dbfs is not None else None
        ),
    }
    return audio_levels, audio_samples


def transcribe_audio(video_path: str) -> Dict[str, Any]:
    """
    Isolate audio track from video, run Whisper transcription.

    Returns:
        {
            "text": str,
            "segments": [{"id", "start", "end", "text"}, ...]
        }
    """
    with tempfile.NamedTemporaryFile(
        prefix="spectra-audio-", suffix=".wav", delete=False
    ) as tmp:
        audio_path = tmp.name

    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=index", "-of", "csv=p=0", video_path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if not probe.stdout.strip():
            return {
                "text": "",
                "segments": [],
                "audio_levels": {
                    "available": False,
                    "peak_dbfs": None,
                    "max_window_rms_dbfs": None,
                },
            }
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", video_path, "-map", "0:a:0", "-vn", "-ac", "1",
                "-ar", "16000", "-c:a", "pcm_s16le", audio_path,
            ],
            check=True,
            capture_output=True,
        )
        audio_levels, audio_samples = _measure_loud_audio(audio_path)
        if audio_levels["peak_dbfs"] is None:
            audio_levels["available"] = False
            return {"text": "", "segments": [], "audio_levels": audio_levels}

        model = _get_whisper()
        try:
            segment_iterator, _ = model.transcribe(
                audio_samples,
                language="en",
                word_timestamps=False,
                beam_size=1,
            )
        except RuntimeError as exc:
            if "reshape tensor of 0 elements" not in str(exc):
                raise
            logging.getLogger(__name__).exception(
                "Whisper received an empty decoder sequence for %s", video_path
            )
            return {
                "text": "",
                "segments": [],
                "audio_levels": audio_levels,
                "analysis_error": (
                    "Audio transcription could not be completed; run the analysis again."
                ),
            }

        segments = [
            {
                "id": index,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            for index, seg in enumerate(segment_iterator)
        ]
        return {
            "text": " ".join(segment["text"] for segment in segments).strip(),
            "segments": segments,
            "audio_levels": audio_levels,
        }
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


def analyze_toxicity(
    segments: List[Dict[str, Any]],
    threshold: float = 0.60,
) -> Dict[str, Any]:
    """
    Run BERT toxicity scoring on each transcript segment.

    Returns:
        {
            "violations": [{"timestamp_sec": float, "label": str, "score": float, "text": str}],
            "safe_pct": float,
            "unsafe_pct": float,
        }
    """
    text_segments = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if text:
            text_segments.append((segment, text))
    if not text_segments:
        return {
            "violations": [],
            "analyzed_segment_count": 0,
            "flagged_segment_count": 0,
            "safe_segment_count": 0,
            "safe_pct": None,
            "unsafe_pct": None,
        }

    bert = _get_bert()
    violations: List[Dict[str, Any]] = []
    unsafe_seg_ids: set = set()
    predictions = bert([text[:512] for _, text in text_segments], batch_size=8)
    if len(predictions) != len(text_segments):
        raise RuntimeError("Toxicity classifier returned an unexpected number of results.")

    for (seg, text), preds in zip(text_segments, predictions):
        # preds is List[List[Dict]] when top_k=None
        labels = preds[0] if isinstance(preds[0], list) else preds
        for pred in labels:
            label = pred["label"].lower()
            score = pred["score"]
            if label in TOXICITY_LABELS and score >= threshold:
                violations.append(
                    {
                        "timestamp_sec": seg["start"],
                        "end_sec": seg["end"],
                        "label": label,
                        "score": round(score, 3),
                        "text": text,
                    }
                )
                unsafe_seg_ids.add(seg["id"])
                break  # one violation per segment is enough

    total = len(text_segments)
    unsafe = len(unsafe_seg_ids)
    safe = total - unsafe

    return {
        "violations": violations,
        "analyzed_segment_count": total,
        "flagged_segment_count": unsafe,
        "safe_segment_count": safe,
        "safe_pct": round((safe / total) * 100, 2) if total else 100.0,
        "unsafe_pct": round((unsafe / total) * 100, 2) if total else 0.0,
    }


def export_vtt(segments: List[Dict[str, Any]], output_path: str) -> str:
    """Convert Whisper segment dicts to WebVTT (.vtt) file."""

    def _fmt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_fmt(seg['start'])} --> {_fmt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path
