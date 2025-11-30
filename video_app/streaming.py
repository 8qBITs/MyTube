import os
import subprocess
import uuid
from typing import Optional
from flask import Response, request, abort, current_app


def guess_mime_type(filename):
    fn = filename.lower()
    if fn.endswith(".mp4"):
        return "video/mp4"
    if fn.endswith(".webm"):
        return "video/webm"
    if fn.endswith(".mkv"):
        return "video/x-matroska"
    if fn.endswith(".avi"):
        return "video/x-msvideo"
    if fn.endswith(".mov"):
        return "video/quicktime"
    return "application/octet-stream"


def range_request_response(video_path: str, content_type: str = None):
    if not os.path.exists(video_path):
        abort(404)

    file_size = os.path.getsize(video_path)
    range_header = request.headers.get("Range", None)

    if not content_type:
        content_type = guess_mime_type(video_path)

    if range_header:
        try:
            bytes_unit, range_spec = range_header.split("=")
        except ValueError:
            abort(416)

        if bytes_unit != "bytes":
            abort(416)

        start_str, end_str = range_spec.split("-")
        try:
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
        except ValueError:
            abort(416)

        if start >= file_size:
            abort(416)

        end = min(end, file_size - 1)
        length = end - start + 1

        def generate():
            with open(video_path, "rb") as f:
                f.seek(start)
                remaining = length
                chunk_size = 8192
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        rv = Response(
            generate(),
            status=206,
            mimetype=content_type,
            direct_passthrough=True,
        )
        rv.headers.add("Content-Range", f"bytes {start}-{end}/{file_size}")
        rv.headers.add("Accept-Ranges", "bytes")
        rv.headers.add("Content-Length", str(length))
        return rv

    def generate_full():
        with open(video_path, "rb") as f:
            while True:
                data = f.read(8192)
                if not data:
                    break
                yield data

    rv = Response(
        generate_full(),
        status=200,
        mimetype=content_type,
        direct_passthrough=True,
    )
    rv.headers.add("Content-Length", str(file_size))
    rv.headers.add("Accept-Ranges", "bytes")
    return rv


# ------------------ Thumbnail helpers ------------------


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return True
    except OSError:
        return False


def _ffprobe_available() -> bool:
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return True
    except OSError:
        return False


def _get_video_duration(video_path: str) -> Optional[float]:
    """
    Return video duration in seconds using ffprobe, or None if not available/fails.
    """
    if not _ffprobe_available():
        return None

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None

    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        return None


def generate_video_thumbnail(video_path: str) -> Optional[str]:
    """
    Generate a JPEG thumbnail for the given video.

    - Uses ffprobe to get duration.
    - Captures a frame at 25% of the video duration (with a minimum of 1s).
    - Scales the frame to ~720p width (1280px) while preserving aspect ratio.
      (For a 16:9 video this will be 1280x720.)
    - Returns the thumbnail filename (relative, to THUMBNAIL_DIR) or None on failure.
    - Requires `ffmpeg` (and preferably `ffprobe`) to be installed.
    """
    if not os.path.exists(video_path):
        return None

    thumb_dir = current_app.config["THUMBNAIL_DIR"]
    os.makedirs(thumb_dir, exist_ok=True)

    thumb_name = f"{uuid.uuid4().hex}.jpg"
    thumb_path = os.path.join(thumb_dir, thumb_name)

    if not _ffmpeg_available():
        return None

    # Determine capture time: 25% into the video, min 1 second
    duration = _get_video_duration(video_path)
    if duration and duration > 0:
        target_time = max(1.0, duration * 0.25)
    else:
        target_time = 1.0

    # Format as HH:MM:SS.mmm
    hours = int(target_time // 3600)
    minutes = int((target_time % 3600) // 60)
    seconds = target_time % 60
    time_str = f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"

    # Scale to 1280px width (for 16:9 this is 1280x720 ~ 720p)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        time_str,
        "-i",
        video_path,
        "-vframes",
        "1",
        "-vf",
        "scale=1280:-1",
        "-q:v",
        "5",
        thumb_path,
    ]

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError:
        if os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass
        return None

    return thumb_name
