"""
Media Remediation Engine — OpenCV selective blur + MoviePy audio muting.
Also handles FFmpeg multi-bitrate HLS transcoding.
"""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
from typing import List, Dict, Any


# ── HLS Transcoding ───────────────────────────────────────────────────────────

HLS_RENDITIONS = [
    {"name": "1080p", "res": "1920x1080", "bitrate": "5000k", "audio": "192k"},
    {"name": "720p",  "res": "1280x720",  "bitrate": "3000k", "audio": "128k"},
    {"name": "480p",  "res": "854x480",   "bitrate": "1500k", "audio": "128k"},
    {"name": "360p",  "res": "640x360",   "bitrate": "800k",  "audio": "96k"},
]


def transcode_to_hls(video_path: str, output_dir: str, video_id: str) -> str:
    """
    Transcode a video to multi-bitrate HLS using FFmpeg.
    Produces per-rendition subdirs and a master .m3u8 playlist.

    Returns path to master.m3u8.
    """
    if video_id in {"", ".", ".."} or os.path.basename(video_id) != video_id:
        raise ValueError("Video id must be a single directory name.")
    output_root = os.path.realpath(output_dir)
    requested_base = os.path.join(output_root, video_id)
    if os.path.islink(requested_base):
        raise ValueError("HLS output directory cannot be a symbolic link.")
    base = os.path.realpath(requested_base)
    if os.path.commonpath((output_root, base)) != output_root:
        raise ValueError("HLS output path must remain inside the configured directory.")
    os.makedirs(base, exist_ok=True)
    for root, directories, filenames in os.walk(base, followlinks=False):
        if any(os.path.islink(os.path.join(root, name)) for name in directories):
            raise ValueError("HLS output cannot contain symbolic-link directories.")
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(root, name))
        ]
        for filename in filenames:
            segment_path = os.path.join(root, filename)
            if (
                filename.lower().endswith(".ts")
                and not os.path.islink(segment_path)
                and os.path.commonpath((base, os.path.realpath(segment_path))) == base
            ):
                os.remove(segment_path)

    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=bit_rate:stream=codec_type,codec_name,width,height",
                "-of", "json", video_path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        source_info = json.loads(probe.stdout)
        video_stream = next(
            stream for stream in source_info.get("streams", [])
            if stream.get("codec_type") == "video"
        )
        audio_streams = [
            stream for stream in source_info.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, StopIteration):
        video_stream = None
        audio_streams = []

    if video_stream and video_stream.get("codec_name") == "h264" and all(
        stream.get("codec_name") == "aac" for stream in audio_streams
    ):
        source_dir = os.path.join(base, "source")
        os.makedirs(source_dir, exist_ok=True)
        playlist = os.path.join(source_dir, "index.m3u8")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", video_path,
                    "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                    "-hls_time", "6", "-hls_playlist_type", "vod",
                    "-hls_segment_filename", os.path.join(source_dir, "seg%03d.ts"),
                    "-f", "hls", playlist,
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            shutil.rmtree(source_dir, ignore_errors=True)
        else:
            bitrate = source_info.get("format", {}).get("bit_rate") or "8000000"
            resolution = "x".join(
                str(video_stream[key]) for key in ("width", "height")
                if video_stream.get(key)
            )
            stream_info = f"BANDWIDTH={int(bitrate)}"
            if resolution:
                stream_info += f",RESOLUTION={resolution}"
            master_path = os.path.join(base, "master.m3u8")
            with open(master_path, "w") as master_file:
                master_file.write(
                    f"#EXTM3U\n#EXT-X-VERSION:3\n"
                    f"#EXT-X-STREAM-INF:{stream_info}\nsource/index.m3u8\n"
                )
            return master_path

    master_lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    stream_args: List[str] = []

    for r in HLS_RENDITIONS:
        rdir = os.path.join(base, r["name"])
        os.makedirs(rdir, exist_ok=True)
        playlist = os.path.join(rdir, "index.m3u8")
        segment_pattern = os.path.join(rdir, "seg%03d.ts")

        stream_args += [
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-s:v", r["res"],
            "-b:v", r["bitrate"],
            "-b:a", r["audio"],
            f"-hls_time", "6",
            f"-hls_list_size", "0",
            f"-hls_segment_filename", segment_pattern,
            f"-f", "hls",
            playlist,
        ]

        bw = int(r["bitrate"].rstrip("k")) * 1000
        master_lines.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={r["res"]}'
        )
        master_lines.append(f"{r['name']}/index.m3u8")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-preset", "ultrafast",
        "-g", "48",
        "-keyint_min", "48",
        "-sc_threshold", "0",
    ] + stream_args

    subprocess.run(cmd, check=True, capture_output=True)

    master_path = os.path.join(base, "master.m3u8")
    with open(master_path, "w") as f:
        f.write("\n".join(master_lines))

    return master_path


# ── Visual Blur Remediation ───────────────────────────────────────────────────

def apply_blur_segments(
    video_path: str,
    output_path: str,
    blur_timestamps: List[Dict[str, Any]],
    blur_radius: int = 31,
) -> str:
    """
    Apply Gaussian blur over flagged frame ranges using OpenCV + FFmpeg.
    `blur_timestamps` = [{"start_sec": float, "end_sec": float}, ...]
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_video = tmp.name

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(tmp_video, fourcc, fps, (width, height))

    # Build a set of frame indices to blur
    blur_frames: set = set()
    for seg in blur_timestamps:
        start_f = int(seg["start_sec"] * fps)
        end_f = int(seg.get("end_sec", seg["start_sec"] + 2) * fps)
        for fi in range(max(0, start_f - 5), end_f + 5):
            blur_frames.add(fi)

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx in blur_frames:
            frame = cv2.GaussianBlur(frame, (blur_radius, blur_radius), 0)
        writer.write(frame)
        frame_idx += 1

    cap.release()
    writer.release()

    # Re-mux with original audio using FFmpeg
    cmd = [
        "ffmpeg", "-y",
        "-i", tmp_video,
        "-i", video_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-c:a", "copy",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(tmp_video)
    return output_path


# ── Audio Mute Remediation ────────────────────────────────────────────────────

def apply_mute_segments(
    video_path: str,
    output_path: str,
    mute_timestamps: List[Dict[str, Any]],
) -> str:
    """
    Mute audio in flagged time ranges using MoviePy.
    `mute_timestamps` = [{"start_sec": float, "end_sec": float}, ...]
    """
    from moviepy import VideoFileClip
    from moviepy.audio.AudioClip import AudioArrayClip

    clip = VideoFileClip(video_path)
    try:
        if clip.audio is None:
            clip.write_videofile(output_path, logger=None)
            return output_path

        original_audio = clip.audio
        duration = clip.duration

        # Chunk decoding avoids MoviePy's frame reader seeking past AAC's final sample.
        audio_array = original_audio.to_soundarray(fps=44100, buffersize=4096)
        sr = 44100

        for seg in mute_timestamps:
            s = int(seg["start_sec"] * sr)
            e = int(min(seg.get("end_sec", seg["start_sec"] + 3), duration) * sr)
            if s < len(audio_array):
                audio_array[s:e] = 0

        muted_audio = AudioArrayClip(audio_array, fps=sr)
        muted_audio = muted_audio.with_duration(duration)

        final = clip.with_audio(muted_audio)
        final.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
    finally:
        clip.close()
    return output_path
