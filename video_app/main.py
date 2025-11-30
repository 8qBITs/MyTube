import os
from datetime import datetime

from flask import (
    Blueprint,
    render_template,
    current_app,
    abort,
    send_from_directory,
    request,
    redirect,
    url_for,
    flash,
)

from .models import Video, WatchHistory, VideoLike, Comment, CommentLike, User
from .streaming import range_request_response, guess_mime_type
from .auth import current_user, login_required
from . import db

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    user = current_user()
    tab = request.args.get("tab", "home")
    query = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "newest")
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    per_page = 6

    # Not logged-in: show welcome screen
    if not user:
        return render_template(
            "index.html",
            videos=None,
            show_videos=False,
            is_banned=False,
            tab=tab,
            query=query,
            watched_ids=set(),
            is_history_view=False,
            is_liked_view=False,
        )

    # Banned: show banned screen
    if user.is_banned:
        return render_template(
            "index.html",
            videos=None,
            show_videos=False,
            is_banned=True,
            tab=tab,
            query=query,
            watched_ids=set(),
            is_history_view=False,
            is_liked_view=False,
        )

    is_history_view = tab == "history"
    is_liked_view = tab == "liked"

    # Base list of videos depending on tab
    if is_history_view:
        # History: keep order by last watched
        history_entries = (
            WatchHistory.query.filter_by(user_id=user.id)
            .order_by(WatchHistory.last_watched_at.desc())
            .all()
        )
        video_ids = [h.video_id for h in history_entries]
        if not video_ids:
            videos = []
        else:
            videos_by_id = {
                v.id: v for v in Video.query.filter(Video.id.in_(video_ids)).all()
            }
            videos = [videos_by_id[vid] for vid in video_ids if vid in videos_by_id]

    elif is_liked_view:
        # Liked videos tab
        like_entries = (
            VideoLike.query.filter_by(user_id=user.id, is_like=True)
            .order_by(VideoLike.created_at.desc())
            .all()
        )
        video_ids = [l.video_id for l in like_entries]
        if not video_ids:
            videos = []
        else:
            videos_by_id = {
                v.id: v for v in Video.query.filter(Video.id.in_(video_ids)).all()
            }
            videos = [videos_by_id[vid] for vid in video_ids if vid in videos_by_id]

    else:
        # Home: all videos
        videos = Video.query.order_by(Video.uploaded_at.desc()).all()

    # Search filter (in-memory on already selected list)
    if query:
        q_lower = query.lower()
        videos = [
            v
            for v in videos
            if (v.title and q_lower in v.title.lower())
            or (v.description and q_lower in v.description.lower())
        ]

    # Sorting
    def uploaded_key(v):
        return v.uploaded_at or datetime.min

    if sort == "oldest":
        videos.sort(key=uploaded_key)
    elif sort == "title_asc":
        videos.sort(key=lambda v: (v.title or "").lower())
    elif sort == "title_desc":
        videos.sort(key=lambda v: (v.title or "").lower(), reverse=True)
    else:  # "newest" or unknown
        videos.sort(key=uploaded_key, reverse=True)

    # Pagination (6 per page)
    total_videos = len(videos)
    total_pages = max(1, (total_videos + per_page - 1) // per_page) if total_videos else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    end = start + per_page
    page_videos = videos[start:end]

    # Watched IDs for badges
    watched_ids = set(
        vid_id
        for (vid_id,) in db.session.query(WatchHistory.video_id)
        .filter_by(user_id=user.id)
        .all()
    )

    return render_template(
        "index.html",
        videos=page_videos,
        show_videos=True,
        is_banned=False,
        tab=tab,
        query=query,
        watched_ids=watched_ids,
        is_history_view=is_history_view,
        is_liked_view=is_liked_view,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        sort=sort,
    )


@main_bp.route("/video/<int:video_id>")
@login_required
def video_detail(video_id):
    user = current_user()
    if user.is_banned:
        flash(
            "Your account is banned. You cannot view or play videos at this time.",
            "danger",
        )
        return redirect(url_for("main.index"))

    video = Video.query.get_or_404(video_id)

    # Increment view count
    video.view_count = (video.view_count or 0) + 1
    db.session.commit()

    # Update watch history
    hist = WatchHistory.query.filter_by(user_id=user.id, video_id=video.id).first()
    if not hist:
        hist = WatchHistory(user_id=user.id, video_id=video.id)
        db.session.add(hist)
    hist.last_watched_at = datetime.utcnow()
    db.session.commit()

    # Determine MIME type (AVI, MP4, WEBM, etc.)
    mime_type = video.content_type or guess_mime_type(video.filename)

    # Likes / dislikes counts
    likes_count = VideoLike.query.filter_by(video_id=video.id, is_like=True).count()
    dislikes_count = VideoLike.query.filter_by(video_id=video.id, is_like=False).count()

    # User like/dislike state
    user_like = VideoLike.query.filter_by(video_id=video.id, user_id=user.id).first()
    if not user_like:
        user_like_state = "none"
    elif user_like.is_like:
        user_like_state = "like"
    else:
        user_like_state = "dislike"

    # Top-level comments for this video
    comments = (
        Comment.query.filter_by(video_id=video.id, parent_comment_id=None)
        .order_by(Comment.created_at.desc())
        .all()
    )
    comment_ids = [c.id for c in comments]

    # Like counts per comment
    if comment_ids:
        rows = (
            db.session.query(CommentLike.comment_id, db.func.count(CommentLike.id))
            .filter(CommentLike.comment_id.in_(comment_ids))
            .group_by(CommentLike.comment_id)
            .all()
        )
        likes_by_comment = {cid: cnt for cid, cnt in rows}
    else:
        likes_by_comment = {}

    # Which comments the current user has liked
    user_comment_likes = set(
        cid
        for (cid,) in db.session.query(CommentLike.comment_id)
        .filter_by(user_id=user.id)
        .all()
    )

    # Related / "Up next" videos (like YouTube sidebar)
    related_videos = (
        Video.query.filter(Video.id != video.id)
        .order_by(Video.uploaded_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "video_detail.html",
        video=video,
        mime_type=mime_type,
        likes_count=likes_count,
        dislikes_count=dislikes_count,
        user_like_state=user_like_state,
        comments=comments,
        likes_by_comment=likes_by_comment,
        user_comment_likes=user_comment_likes,
        related_videos=related_videos,
    )


@main_bp.route("/video/<int:video_id>/like", methods=["POST"])
@login_required
def like_video(video_id):
    user = current_user()
    if user.is_banned:
        flash(
            "Your account is banned. You cannot interact with videos at this time.",
            "danger",
        )
        return redirect(url_for("main.video_detail", video_id=video_id))

    video = Video.query.get_or_404(video_id)
    action = request.form.get("action")

    if action not in ("like", "dislike"):
        flash("Invalid action.", "danger")
        return redirect(url_for("main.video_detail", video_id=video_id))

    existing = VideoLike.query.filter_by(video_id=video.id, user_id=user.id).first()

    if not existing:
        like_row = VideoLike(
            video_id=video.id,
            user_id=user.id,
            is_like=(action == "like"),
        )
        db.session.add(like_row)
    else:
        # Toggle logic
        if action == "like":
            if existing.is_like:
                db.session.delete(existing)  # remove like
            else:
                existing.is_like = True  # change dislike -> like
        else:  # action == "dislike"
            if not existing.is_like:
                db.session.delete(existing)  # remove dislike
            else:
                existing.is_like = False  # change like -> dislike

    db.session.commit()
    return redirect(url_for("main.video_detail", video_id=video_id))


@main_bp.route("/video/<int:video_id>/comment", methods=["POST"])
@login_required
def comment_video(video_id):
    user = current_user()
    if user.is_banned:
        flash(
            "Your account is banned. You cannot comment on videos at this time.",
            "danger",
        )
        return redirect(url_for("main.video_detail", video_id=video_id))

    video = Video.query.get_or_404(video_id)
    text = (request.form.get("comment_text") or "").strip()
    anonymous = bool(request.form.get("anonymous"))
    parent_id = request.form.get("parent_comment_id")

    if not text:
        flash("Comment cannot be empty.", "warning")
        return redirect(url_for("main.video_detail", video_id=video_id))

    parent_comment = None
    if parent_id:
        try:
            pid_int = int(parent_id)
        except ValueError:
            pid_int = None
        if pid_int:
            parent_comment = Comment.query.filter_by(
                id=pid_int, video_id=video.id
            ).first()

    comment = Comment(
        video_id=video.id,
        user_id=None if anonymous else user.id,
        text=text,
        anonymous=anonymous,
        parent_comment_id=parent_comment.id if parent_comment else None,
    )
    db.session.add(comment)
    db.session.commit()

    flash("Comment posted.", "success")
    return redirect(url_for("main.video_detail", video_id=video_id))


@main_bp.route("/comment/<int:comment_id>/like", methods=["POST"])
@login_required
def like_comment(comment_id):
    user = current_user()
    if user.is_banned:
        flash(
            "Your account is banned. You cannot like comments at this time.",
            "danger",
        )
    comment = Comment.query.get_or_404(comment_id)
    video_id = comment.video_id

    if user.is_banned:
        return redirect(url_for("main.video_detail", video_id=video_id))

    existing = CommentLike.query.filter_by(
        comment_id=comment.id, user_id=user.id
    ).first()

    if existing:
        db.session.delete(existing)  # unlike
    else:
        like_row = CommentLike(comment_id=comment.id, user_id=user.id)
        db.session.add(like_row)

    db.session.commit()
    return redirect(url_for("main.video_detail", video_id=video_id))


@main_bp.route("/comment/<int:comment_id>/heart", methods=["POST"])
@login_required
def heart_comment(comment_id):
    """Admin-only: toggle 'hearted' flag on a comment."""
    user = current_user()
    if not user.is_admin:
        flash("Only admins can heart comments.", "danger")
        return redirect(url_for("main.index"))

    comment = Comment.query.get_or_404(comment_id)
    comment.admin_hearted = not bool(comment.admin_hearted)
    db.session.commit()

    return redirect(url_for("main.video_detail", video_id=comment.video_id))


@main_bp.route("/stream/<int:video_id>")
@login_required
def stream_video(video_id):
    """
    Stream a video file with HTTP range support, addressed by video_id.
    Matches: url_for('main.stream_video', video_id=video.id)
    """
    user = current_user()
    if user.is_banned:
        flash(
            "Your account is banned. You cannot view or play videos at this time.",
            "danger",
        )
        return redirect(url_for("main.index"))

    video = Video.query.get_or_404(video_id)
    video_path = os.path.join(current_app.config["VIDEO_UPLOAD_DIR"], video.filename)

    # Use explicit content_type if set, otherwise guess by filename (AVI support, etc.)
    content_type = video.content_type or guess_mime_type(video.filename)

    return range_request_response(video_path, content_type)


@main_bp.route("/thumbnails/<path:thumb_name>")
def thumbnail(thumb_name):
    """Serve generated thumbnails."""
    thumb_dir = current_app.config["THUMBNAIL_DIR"]
    return send_from_directory(thumb_dir, thumb_name)


# -------- Public user profile with pagination --------

@main_bp.route("/user/<int:user_id>")
def user_profile(user_id):
    """
    Public profile view.
    - Shows username (if set), gender, about_me.
    - Shows liked videos if user.liked_videos_public or viewer is the owner.
    - Shows comments list if user.comments_public or viewer is the owner.
    Anonymous comments are never listed here.
    Supports pagination for both liked videos and comments.
    """
    viewer = current_user()
    profile_user = User.query.get_or_404(user_id)
    is_owner = viewer is not None and viewer.id == profile_user.id

    # Pagination params
    try:
        liked_page = int(request.args.get("liked_page", "1"))
    except ValueError:
        liked_page = 1
    try:
        comments_page = int(request.args.get("comments_page", "1"))
    except ValueError:
        comments_page = 1

    liked_per_page = 6
    comments_per_page = 10

    # ----- Liked videos -----
    liked_videos = []
    liked_total = 0
    liked_total_pages = 1
    show_liked_to_viewer = is_owner or profile_user.liked_videos_public

    if show_liked_to_viewer:
        like_q = VideoLike.query.filter_by(user_id=profile_user.id, is_like=True)
        liked_total = like_q.count()
        if liked_total > 0:
            liked_total_pages = (liked_total + liked_per_page - 1) // liked_per_page
        else:
            liked_total_pages = 1

        if liked_page < 1:
            liked_page = 1
        if liked_page > liked_total_pages:
            liked_page = liked_total_pages

        like_entries = (
            like_q.order_by(VideoLike.created_at.desc())
            .limit(liked_per_page)
            .offset((liked_page - 1) * liked_per_page)
            .all()
        )
        video_ids = [l.video_id for l in like_entries]
        if video_ids:
            videos_by_id = {
                v.id: v for v in Video.query.filter(Video.id.in_(video_ids)).all()
            }
            liked_videos = [videos_by_id[vid] for vid in video_ids if vid in videos_by_id]
    else:
        liked_page = 1
        liked_total_pages = 1
        liked_total = 0

    # ----- Comments -----
    user_comments = []
    comments_total = 0
    comments_total_pages = 1
    show_comments_to_viewer = is_owner or profile_user.comments_public

    if show_comments_to_viewer:
        comments_q = Comment.query.filter_by(user_id=profile_user.id, anonymous=False)
        comments_total = comments_q.count()
        if comments_total > 0:
            comments_total_pages = (comments_total + comments_per_page - 1) // comments_per_page
        else:
            comments_total_pages = 1

        if comments_page < 1:
            comments_page = 1
        if comments_page > comments_total_pages:
            comments_page = comments_total_pages

        user_comments = (
            comments_q.order_by(Comment.created_at.desc())
            .limit(comments_per_page)
            .offset((comments_page - 1) * comments_per_page)
            .all()
        )
    else:
        comments_page = 1
        comments_total_pages = 1
        comments_total = 0

    return render_template(
        "user_profile.html",
        profile_user=profile_user,
        is_owner=is_owner,
        # liked videos
        show_liked_to_viewer=show_liked_to_viewer,
        liked_videos=liked_videos,
        liked_page=liked_page,
        liked_total_pages=liked_total_pages,
        liked_total=liked_total,
        liked_per_page=liked_per_page,
        # comments
        show_comments_to_viewer=show_comments_to_viewer,
        user_comments=user_comments,
        comments_page=comments_page,
        comments_total_pages=comments_total_pages,
        comments_total=comments_total,
        comments_per_page=comments_per_page,
    )
