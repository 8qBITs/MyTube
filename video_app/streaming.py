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


# ------------------ Range-based original streaming ------------------


def range_request_response(video_path: str, content_type: str = None, quality: Optional[int] = None):
    """
    Stream a video file.

    - If `quality` is None, behaves like the original implementation:
      raw file bytes with HTTP range support.
    - If `quality` is one of {1080, 720, 480} and ffmpeg is available,
      attempts real-time transcoding down to <= that height (never upscales)
      and streams an MP4. In transcoding mode, Range headers are ignored
      and we return a regular 200 response with chunked output.
    """
    if not os.path.exists(video_path):
        abort(404)

    # If a valid quality is requested and ffmpeg is present, use real-time transcoding.
    if quality in (1080, 720, 480) and _ffmpeg_available():
        return _transcoded_stream_response(video_path, target_height=quality)

    # Fallback: original range-based streaming from disk.
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


# ------------------ Real-time transcoding helpers ------------------


def _get_transcoding_backend() -> str:
    """
    Read the configured transcoding backend from AppConfig.

    Returns one of: "cpu", "intel", "amd", "nvidia".
    Defaults to "cpu" if unset or invalid.
    """
    try:
        from .models import AppConfig  # local import to avoid circulars
        cfg = AppConfig.query.first()
        backend = (cfg.transcoding_backend if cfg and cfg.transcoding_backend else "cpu").lower()
    except Exception:
        backend = "cpu"

    if backend not in ("cpu", "intel", "amd", "nvidia"):
        backend = "cpu"
    return backend


def _build_ffmpeg_transcode_cmd(video_path: str, target_height: int, backend: str):
    """
    Build an ffmpeg command to transcode `video_path` to MP4 with H.264 video + AAC audio.

    - target_height is one of 1080/720/480.
    - We never upscale: use scale=-2:min(ih,target_height) so output height is
      at most both the input height and target_height.
    """
    # Base scale filter: keep aspect ratio, width divisible by 2, height <= min(ih, target_height)
    scale_filter = f"scale=-2:min(ih,{int(target_height)})"

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

    vcodec = "libx264"  # CPU default
    hwaccel_args = []

    if backend == "nvidia":
        # NVIDIA NVENC
        vcodec = "h264_nvenc"
        hwaccel_args = ["-hwaccel", "cuda"]
    elif backend == "intel":
        # Intel Quick Sync
        vcodec = "h264_qsv"
        hwaccel_args = ["-hwaccel", "qsv"]
    elif backend == "amd":
        # Generic AMD path via VAAPI; may need server-specific tweaking
        vcodec = "h264_vaapi"
        hwaccel_args = ["-hwaccel", "vaapi"]

    # Assemble command
    cmd += hwaccel_args
    cmd += [
        "-i",
        video_path,
        "-vf",
        scale_filter,
        "-c:v",
        vcodec,
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "frag_keyframe+empty_moov",
        "-f",
        "mp4",
        "-",
    ]
    return cmd


def _transcoded_stream_response(video_path: str, target_height: int):
    """
    Stream a live-transcoded MP4 at up to `target_height` (1080/720/480).

    - Honors the configured hardware backend (cpu/intel/amd/nvidia).
    - Never upscales above source resolution.
    - If hardware encoding fails to start, falls back to CPU libx264.
    """
    backend = _get_transcoding_backend()
    cmd = _build_ffmpeg_transcode_cmd(video_path, target_height, backend)

    def start_process(cmd_list):
        try:
            return subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # pragma: no cover - defensive
            current_app.logger.exception("ffmpeg start error (%s): %s", backend, exc)
            return None

    proc = start_process(cmd)

    # If hardware backend failed to start, fall back to CPU libx264.
    if not proc and backend != "cpu":
        current_app.logger.warning("Falling back to CPU transcoding for %s", video_path)
        cpu_cmd = _build_ffmpeg_transcode_cmd(video_path, target_height, "cpu")
        proc = start_process(cpu_cmd)

    if not proc:
        # Last resort: just fall back to original range-based streaming
        current_app.logger.error("Failed to start ffmpeg for transcoding; falling back to raw file stream.")
        return range_request_response(video_path, guess_mime_type(video_path), quality=None)

    def generate():
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

    rv = Response(
        generate(),
        status=200,
        mimetype="video/mp4",
        direct_passthrough=True,
    )
    # We don't know Content-Length in advance for live transcoding.
    # Do not advertise range support here.
    return rv
