import os
import json
from uuid import uuid4

import requests
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)

from . import db
from .models import User, Video, AppConfig
from .auth import admin_required, current_user
from .streaming import generate_video_thumbnail
from .torrent_downloader import TorrentManager, LIBTORRENT_AVAILABLE

admin_bp = Blueprint("admin", __name__, template_folder="templates/admin")


DEFAULT_DEEPSEEK_SYSTEM_PROMPT = (
    "You are an assistant that writes concise, engaging video "
    "titles and descriptions for a video website."
)

DEFAULT_DEEPSEEK_USER_PROMPT_TEMPLATE = (
    "You help write YouTube-style video titles and descriptions.\n\n"
    "Given this video file name: \"{filename}\",\n"
    "1. Generate a short, catchy title (max 80 characters).\n"
    "2. Generate a 2–3 sentence description.\n\n"
    "Respond ONLY as JSON like:\n"
    "{\n"
    '  \"title\": \"...\",\n'
    '  \"description\": \"...\"\n'
    "}\n"
)


def _get_site_config():
    """
    Small helper so we can easily pass branding info into templates.
    """
    cfg = AppConfig.query.first()
    site_name = (cfg.site_name if cfg and cfg.site_name else "MyTube")
    footer_text = (cfg.footer_text if cfg and cfg.footer_text else "© MyTube")
    return site_name, footer_text


def _get_torrent_manager() -> "TorrentManager":
    """
    Lazily create a TorrentManager and store it on app.extensions.
    """
    mgr = current_app.extensions.get("torrent_manager")
    if mgr is None:
        temp_root = current_app.config.get(
            "TORRENT_TEMP_DIR",
            os.path.join(current_app.instance_path, "torrents"),
        )
        mgr = TorrentManager(temp_root=temp_root)
        current_app.extensions["torrent_manager"] = mgr
    return mgr


def _extract_json_block(text: str) -> str | None:
    """
    DeepSeek sometimes wraps JSON in ```json ... ``` or adds extra text.
    Try to pull out a single JSON object from the text.

    Returns the raw JSON string or None if nothing reasonable is found.
    """
    if not text:
        return None

    s = text.strip()

    # Strip ``` fences if present
    if s.startswith("```"):
        lines = s.splitlines()
        # Drop first line (``` or ```json)
        lines = lines[1:]
        # Drop last line if it's ``` alone
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    # If the whole thing already looks like JSON, use it
    if s.startswith("{") and s.endswith("}"):
        return s

    # Otherwise, try to find the first {...} block
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = s[start : end + 1].strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate

    return None


# -------------------- Dashboard & Settings --------------------


@admin_bp.route("/")
@admin_required
def dashboard():
    video_count = Video.query.count()
    user_count = User.query.count()
    cfg = AppConfig.query.first()

    transcoding_backend = (cfg.transcoding_backend if cfg and cfg.transcoding_backend else "cpu").lower()
    if transcoding_backend not in ("cpu", "intel", "amd", "nvidia"):
        transcoding_backend = "cpu"

    registration_enabled = cfg.registration_enabled if cfg else True

    site_name, footer_text = _get_site_config()

    return render_template(
        "admin/dashboard.html",
        video_count=video_count,
        user_count=user_count,
        registration_enabled=registration_enabled,
        transcoding_backend=transcoding_backend,
        site_name=site_name,
        footer_text=footer_text,
    )


@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    """
    Global settings page (registration, DeepSeek, branding, transcoding).
    """
    cfg = AppConfig.query.first()
    if cfg is None:
        cfg = AppConfig(registration_enabled=True, transcoding_backend="cpu")
        db.session.add(cfg)
        db.session.commit()

    if request.method == "POST":
        cfg.registration_enabled = bool(request.form.get("registration_enabled"))

        deepseek_api_key = (request.form.get("deepseek_api_key") or "").strip()
        cfg.deepseek_api_key = deepseek_api_key or None

        cfg.deepseek_system_prompt = (
            (request.form.get("deepseek_system_prompt") or "").strip()
            or DEFAULT_DEEPSEEK_SYSTEM_PROMPT
        )
        cfg.deepseek_user_prompt_template = (
            (request.form.get("deepseek_user_prompt_template") or "").strip()
            or DEFAULT_DEEPSEEK_USER_PROMPT_TEMPLATE
        )

        cfg.site_name = (request.form.get("site_name") or "").strip() or None
        cfg.footer_text = (request.form.get("footer_text") or "").strip() or None

        backend = (request.form.get("transcoding_backend") or "cpu").strip().lower()
        if backend not in ("cpu", "intel", "amd", "nvidia"):
            backend = "cpu"
        cfg.transcoding_backend = backend

        db.session.commit()
        flash("Settings updated.", "success")
        return redirect(url_for("admin.settings"))

    # GET: render settings form with current values
    site_name, footer_text = _get_site_config()

    deepseek_system_prompt = (
        cfg.deepseek_system_prompt if cfg.deepseek_system_prompt else DEFAULT_DEEPSEEK_SYSTEM_PROMPT
    )
    deepseek_user_prompt_template = (
        cfg.deepseek_user_prompt_template
        if cfg.deepseek_user_prompt_template
        else DEFAULT_DEEPSEEK_USER_PROMPT_TEMPLATE
    )

    transcoding_backend = (cfg.transcoding_backend if cfg.transcoding_backend else "cpu").lower()
    if transcoding_backend not in ("cpu", "intel", "amd", "nvidia"):
        transcoding_backend = "cpu"

    return render_template(
        "admin/settings.html",
        registration_enabled=cfg.registration_enabled,
        deepseek_api_key=(cfg.deepseek_api_key or ""),
        deepseek_system_prompt=deepseek_system_prompt,
        deepseek_user_prompt_template=deepseek_user_prompt_template,
        site_name=site_name,
        footer_text=footer_text,
        transcoding_backend=transcoding_backend,
    )


# -------------------- Videos --------------------


@admin_bp.route("/videos")
@admin_required
def videos():
    """
    Page for listing/managing videos.
    """
    videos = Video.query.order_by(Video.uploaded_at.desc()).all()
    site_name, footer_text = _get_site_config()
    return render_template(
        "admin/videos.html",
        videos=videos,
        site_name=site_name,
        footer_text=footer_text,
    )


@admin_bp.route("/upload", methods=["GET", "POST"])
@admin_required
def upload_video():
    site_name, footer_text = _get_site_config()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        file = request.files.get("file")

        if not title or not file:
            flash("Title and video file are required.", "danger")
            return render_template("admin/upload.html", site_name=site_name, footer_text=footer_text)

        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        max_size = current_app.config.get("MAX_CONTENT_LENGTH", 15 * 1024 * 1024 * 1024)
        if size > max_size:
            flash("File exceeds size limit.", "danger")
            return render_template("admin/upload.html", site_name=site_name, footer_text=footer_text)

        ext = os.path.splitext(file.filename or "")[1]
        new_name = f"{uuid4().hex}{ext}"
        video_dir = current_app.config["VIDEO_UPLOAD_DIR"]
        os.makedirs(video_dir, exist_ok=True)
        save_path = os.path.join(video_dir, new_name)
        file.save(save_path)

        video = Video(
            title=title,
            description=description,
            filename=new_name,
            uploader_id=current_user().id,
        )

        # Generate thumbnail if possible
        thumb = generate_video_thumbnail(save_path)
        if thumb:
            video.thumbnail_filename = thumb

        db.session.add(video)
        db.session.commit()
        flash("Video uploaded.", "success")
        return redirect(url_for("admin.videos"))

    return render_template("admin/upload.html", site_name=site_name, footer_text=footer_text)


@admin_bp.route("/mass_upload", methods=["GET", "POST"])
@admin_required
def mass_upload():
    """
    Mass upload endpoint used by JS on the mass_upload page.
    POST: single file (AJAX), returns JSON.
    """
    site_name, footer_text = _get_site_config()

    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return {"error": "No file provided."}, 400

        original_name = file.filename or ""
        title = os.path.splitext(os.path.basename(original_name))[0].strip() or "Untitled"

        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        max_size = current_app.config.get("MAX_CONTENT_LENGTH", 15 * 1024 * 1024 * 1024)
        if size > max_size:
            return {"error": "File exceeds size limit."}, 400

        ext = os.path.splitext(original_name)[1]
        new_name = f"{uuid4().hex}{ext}"
        video_dir = current_app.config["VIDEO_UPLOAD_DIR"]
        os.makedirs(video_dir, exist_ok=True)
        save_path = os.path.join(video_dir, new_name)
        file.save(save_path)

        video = Video(
            title=title,
            description="",
            filename=new_name,
            uploader_id=current_user().id,
        )

        thumb = generate_video_thumbnail(save_path)
        if thumb:
            video.thumbnail_filename = thumb

        db.session.add(video)
        db.session.commit()

        return {
            "success": True,
            "video_id": video.id,
            "title": video.title,
            "filename": video.filename,
        }

    return render_template("admin/mass_upload.html", site_name=site_name, footer_text=footer_text)


@admin_bp.route("/discover", methods=["GET", "POST"])
@admin_required
def discover_videos():
    """
    Discover files present on disk but not in the DB.
    """
    site_name, footer_text = _get_site_config()

    video_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    os.makedirs(video_dir, exist_ok=True)

    existing_filenames = {v.filename for v in Video.query.all()}
    all_files = [
        f for f in os.listdir(video_dir)
        if os.path.isfile(os.path.join(video_dir, f))
    ]
    missing_files = [f for f in all_files if f not in existing_filenames]

    if request.method == "POST":
        selected = request.form.getlist("filenames")
        if not selected:
            flash("No files selected.", "warning")
            return redirect(url_for("admin.discover_videos"))

        count = 0
        for fname in selected:
            path = os.path.join(video_dir, fname)
            if not os.path.exists(path):
                continue

            title = os.path.splitext(os.path.basename(fname))[0].strip() or "Untitled"

            video = Video(
                title=title,
                description="",
                filename=fname,
                uploader_id=current_user().id,
            )

            thumb = generate_video_thumbnail(path)
            if thumb:
                video.thumbnail_filename = thumb

            db.session.add(video)
            count += 1

        db.session.commit()
        flash(f"Imported {count} file(s).", "success")
        return redirect(url_for("admin.videos"))

    return render_template(
        "admin/discover.html",
        missing_files=missing_files,
        site_name=site_name,
        footer_text=footer_text,
    )


# -------------------- Users --------------------


@admin_bp.route("/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    """
    List users and handle creation, admin toggle, ban/unban, delete.
    """
    site_name, footer_text = _get_site_config()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create_user":
            # NOTE: template uses new_email / new_password / new_is_admin
            email = (request.form.get("new_email") or "").strip().lower()
            password = request.form.get("new_password") or ""
            is_admin = bool(request.form.get("new_is_admin"))

            if not email or not password:
                flash("Email and password are required.", "danger")
                return redirect(url_for("admin.manage_users"))

            if User.query.filter_by(email=email).first():
                flash("User with that email already exists.", "danger")
                return redirect(url_for("admin.manage_users"))

            user = User(email=email, is_admin=is_admin)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("User created.", "success")
            return redirect(url_for("admin.manage_users"))

        user_id = request.form.get("user_id")
        if not user_id:
            flash("Missing user id.", "danger")
            return redirect(url_for("admin.manage_users"))

        user = User.query.get_or_404(user_id)

        if action == "make_admin":
            if user.id == current_user().id:
                flash("You cannot change your own admin status.", "danger")
            else:
                user.is_admin = True
                db.session.commit()
                flash("User is now an admin.", "success")

        elif action == "remove_admin":
            if user.id == current_user().id:
                flash("You cannot change your own admin status.", "danger")
            else:
                user.is_admin = False
                db.session.commit()
                flash("Admin status removed from user.", "success")

        elif action == "ban":
            if user.id == current_user().id:
                flash("You cannot ban yourself.", "danger")
            else:
                user.is_banned = True
                db.session.commit()
                flash("User banned.", "success")

        elif action == "unban":
            if user.id == current_user().id:
                flash("You cannot unban yourself.", "danger")
            else:
                user.is_banned = False
                db.session.commit()
                flash("User unbanned.", "success")

        elif action == "delete":
            if user.is_admin:
                flash("Cannot delete an admin user.", "danger")
            else:
                db.session.delete(user)
                db.session.commit()
                flash("User deleted.", "success")

        return redirect(url_for("admin.manage_users"))

    users = User.query.order_by(User.created_at.desc()).all()
    return render_template(
        "admin/users.html",
        users=users,
        site_name=site_name,
        footer_text=footer_text,
    )


# -------------------- Video edit / AI / thumbnails --------------------


@admin_bp.route("/videos/<int:video_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_video(video_id):
    video = Video.query.get_or_404(video_id)
    site_name, footer_text = _get_site_config()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()

        if not title:
            flash("Title cannot be empty.", "danger")
            return render_template(
                "admin/edit_video.html",
                video=video,
                site_name=site_name,
                footer_text=footer_text,
            )

        video.title = title
        video.description = description
        db.session.commit()
        flash("Video updated.", "success")
        return redirect(url_for("admin.videos"))

    return render_template("admin/edit_video.html", video=video, site_name=site_name, footer_text=footer_text)


@admin_bp.route("/videos/<int:video_id>/delete", methods=["POST"])
@admin_required
def delete_video(video_id):
    video = Video.query.get_or_404(video_id)

    video_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    path = os.path.join(video_dir, video.filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            current_app.logger.warning("Could not remove video file %s", path)

    if video.thumbnail_filename:
        thumb_dir = current_app.config["THUMBNAIL_DIR"]
        thumb_path = os.path.join(thumb_dir, video.thumbnail_filename)
        if os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                current_app.logger.warning("Could not remove thumbnail %s", thumb_path)

    db.session.delete(video)
    db.session.commit()
    flash("Video deleted.", "success")
    return redirect(url_for("admin.videos"))


@admin_bp.route("/videos/<int:video_id>/ai_metadata", methods=["POST"])
@admin_required
def ai_video_metadata(video_id):
    """
    Use DeepSeek chat model to generate a title + description
    based on the video filename. Applies changes directly to the video.
    """
    video = Video.query.get_or_404(video_id)
    cfg = AppConfig.query.first()

    if not cfg or not cfg.deepseek_api_key:
        flash(
            "DeepSeek API key is not configured in Admin → Settings.",
            "danger",
        )
        return redirect(request.referrer or url_for("admin.edit_video", video_id=video.id))

    api_key = cfg.deepseek_api_key

    system_prompt = cfg.deepseek_system_prompt or DEFAULT_DEEPSEEK_SYSTEM_PROMPT
    user_template = cfg.deepseek_user_prompt_template or DEFAULT_DEEPSEEK_USER_PROMPT_TEMPLATE

    # Only replace the {filename} placeholder, do NOT use .format()
    user_prompt = user_template.replace("{filename}", video.filename)

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 300,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        current_app.logger.exception("DeepSeek API error: %s", exc)
        flash("Failed to contact DeepSeek API.", "danger")
        return redirect(request.referrer or url_for("admin.edit_video", video_id=video.id))

    try:
        content = data["choices"][0]["message"]["content"]
        raw_json = _extract_json_block(content)
        if not raw_json:
            raise ValueError("No JSON object found in DeepSeek response")

        parsed = json.loads(raw_json)
        new_title = parsed.get("title") or video.title or os.path.splitext(video.filename)[0]
        new_description = parsed.get("description") or (video.description or "")
    except Exception as exc:
        # Log both the error and the raw content to make debugging easy
        current_app.logger.exception("DeepSeek response parse error: %s", exc)
        try:
            current_app.logger.error("DeepSeek raw content: %r", content)
        except Exception:
            pass

        flash("DeepSeek returned an unexpected response (couldn't parse JSON).", "danger")
        return redirect(request.referrer or url_for("admin.edit_video", video_id=video.id))

    video.title = new_title.strip()
    video.description = new_description.strip()
    db.session.commit()

    flash("AI-generated title and description applied.", "success")
    return redirect(request.referrer or url_for("admin.edit_video", video_id=video.id))


@admin_bp.route("/videos/<int:video_id>/regenerate_thumbnail", methods=["POST"])
@admin_required
def regenerate_thumbnail(video_id):
    """
    Regenerate thumbnail for a single video.
    Used by the bulk 'Regenerate thumbnails' feature on the videos page.
    Returns JSON so the JS overlay can show progress.
    """
    video = Video.query.get_or_404(video_id)

    video_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    thumb_dir = current_app.config["THUMBNAIL_DIR"]

    video_path = os.path.join(video_dir, video.filename)
    if not os.path.exists(video_path):
        return {"success": False, "error": "Video file is missing on disk."}, 400

    old_thumb = video.thumbnail_filename

    new_thumb = generate_video_thumbnail(video_path)
    if not new_thumb:
        current_app.logger.warning("Failed to generate thumbnail for video %s", video.id)
        return {"success": False, "error": "Thumbnail generation failed."}, 500

    video.thumbnail_filename = new_thumb
    db.session.commit()

    # Remove old thumbnail file if present
    if old_thumb:
        old_path = os.path.join(thumb_dir, old_thumb)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                current_app.logger.warning("Could not remove old thumbnail %s", old_path)

    return {"success": True, "thumbnail": new_thumb}


# -------------------- Torrent Downloader UI --------------------


@admin_bp.route("/torrents", methods=["GET", "POST"])
@admin_required
def torrents():
    """
    Torrent downloader admin page.
    - Start new downloads from magnet links.
    - Show current jobs and progress.
    """
    site_name, footer_text = _get_site_config()
    mgr = _get_torrent_manager()

    default_exts = current_app.config.get(
        "TORRENT_VIDEO_EXTS_DEFAULT",
        ".mp4,.mkv,.webm,.avi,.mov,.flv,.wmv",
    )

    if request.method == "POST":
        magnet = (request.form.get("magnet_link") or "").strip()
        ext_str = (request.form.get("video_exts") or default_exts).strip()

        if not LIBTORRENT_AVAILABLE:
            flash("python-libtorrent is not installed on the server.", "danger")
            return redirect(url_for("admin.torrents"))

        if not magnet:
            flash("Magnet link is required.", "danger")
            return redirect(url_for("admin.torrents"))

        video_exts = [e.strip() for e in ext_str.split(",") if e.strip()]
        dest_dir = current_app.config["VIDEO_UPLOAD_DIR"]

        mgr.add_job(magnet_uri=magnet, dest_dir=dest_dir, video_exts=video_exts)
        flash("Torrent download started.", "success")
        return redirect(url_for("admin.torrents"))

    jobs = mgr.list_jobs()
    return render_template(
        "admin/torrents.html",
        jobs=jobs,
        site_name=site_name,
        footer_text=footer_text,
        default_video_exts=default_exts,
        libtorrent_available=LIBTORRENT_AVAILABLE,
    )


@admin_bp.route("/torrents/status")
@admin_required
def torrents_status():
    """
    JSON endpoint polled by the UI to update torrent progress.
    """
    mgr = _get_torrent_manager()
    return {"jobs": mgr.list_jobs()}


@admin_bp.route("/torrents/<job_id>/delete", methods=["POST"])
@admin_required
def torrents_delete(job_id):
    """
    Delete a torrent job:
    - Cancels if still running.
    - Deletes temp data.
    - Removes from list.
    (Final moved video files in VIDEO_UPLOAD_DIR are not removed.)
    """
    mgr = _get_torrent_manager()
    ok = mgr.delete_job(job_id)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"success": ok}

    if ok:
        flash("Torrent job removed.", "success")
    else:
        flash("Torrent job not found.", "warning")

    return redirect(url_for("admin.torrents"))
