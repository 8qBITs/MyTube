from datetime import datetime

from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash

from . import db


class AppConfig(db.Model):
    """
    Single-row table for global app settings.
    """
    __tablename__ = "app_config"

    id = db.Column(db.Integer, primary_key=True)

    # Whether self-registration is allowed
    registration_enabled = db.Column(db.Boolean, default=True, nullable=False)

    # DeepSeek API key for AI title/description generation
    deepseek_api_key = db.Column(db.String(255), nullable=True)

    # DeepSeek prompts
    deepseek_system_prompt = db.Column(db.Text, nullable=True)
    deepseek_user_prompt_template = db.Column(db.Text, nullable=True)

    # Site-wide branding
    site_name = db.Column(db.String(255), nullable=True)
    footer_text = db.Column(db.String(255), nullable=True)

    # Video transcoding backend: cpu / intel / amd / nvidia
    transcoding_backend = db.Column(db.String(32), nullable=True)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_banned = db.Column(db.Boolean, default=False, nullable=False)

    # Optional public-facing profile fields
    username = db.Column(db.String(64), nullable=True)
    gender = db.Column(db.String(32), nullable=True)
    about_me = db.Column(db.Text, nullable=True)

    # Privacy settings for public profile
    liked_videos_public = db.Column(db.Boolean, default=False, nullable=False)
    comments_public = db.Column(db.Boolean, default=False, nullable=False)

    # Registration metadata
    registered_ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships (cascade deletes to avoid orphan rows / integrity errors)
    videos = db.relationship(
        "Video",
        backref="uploader",
        lazy=True,
        cascade="all, delete-orphan",
    )
    watch_history = db.relationship(
        "WatchHistory",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    video_likes = db.relationship(
        "VideoLike",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    comments = db.relationship(
        "Comment",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    comment_likes = db.relationship(
        "CommentLike",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Video(db.Model):
    __tablename__ = "videos"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    filename = db.Column(db.String(512), nullable=False)
    content_type = db.Column(db.String(128), default="video/mp4", nullable=False)

    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    uploader_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Optional thumbnail
    thumbnail_filename = db.Column(db.String(512), nullable=True)

    # View count
    view_count = db.Column(db.Integer, default=0, nullable=False)

    # Relationships
    watch_entries = db.relationship(
        "WatchHistory",
        backref="video",
        lazy=True,
        cascade="all, delete-orphan",
    )
    likes = db.relationship(
        "VideoLike",
        backref="video",
        lazy=True,
        cascade="all, delete-orphan",
    )
    comments = db.relationship(
        "Comment",
        backref="video",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Video {self.id} {self.title!r}>"


class WatchHistory(db.Model):
    """
    Tracks which videos a user has watched and when they last watched them.
    """
    __tablename__ = "watch_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)

    # Last time the user watched this video
    last_watched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "video_id", name="uq_watch_history_user_video"),
    )

    def __repr__(self) -> str:
        return f"<WatchHistory user={self.user_id} video={self.video_id}>"


class VideoLike(db.Model):
    """
    Per-user like or dislike for a video.
    Unique per (user, video).
    """
    __tablename__ = "video_likes"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)

    # True = like, False = dislike
    is_like = db.Column(db.Boolean, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "video_id", name="uq_user_video_like"),
    )

    def __repr__(self) -> str:
        return f"<VideoLike user={self.user_id} video={self.video_id} is_like={self.is_like}>"


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)

    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # If True, the comment is shown as anonymous (user id may be null)
    anonymous = db.Column(db.Boolean, default=False, nullable=False)

    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Self-referential relationship for replies
    parent_comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=True)
    replies = db.relationship(
        "Comment",
        backref=db.backref("parent", remote_side=[id]),
        lazy=True,
        cascade="all, delete-orphan",
    )

    # Whether an admin has "hearted" this comment
    admin_hearted = db.Column(db.Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<Comment {self.id} video={self.video_id}>"


class CommentLike(db.Model):
    """
    Per-user like for a comment.
    Unique per (user, comment).
    """
    __tablename__ = "comment_likes"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "comment_id", name="uq_user_comment_like"),
    )

    def __repr__(self) -> str:
        return f"<CommentLike user={self.user_id} comment={self.comment_id}>"


def init_default_admin_and_config():
    """
    Helper to initialize a default admin user and config row
    if they don't exist yet. Call this from app factory / CLI.
    """
    # Config row
    cfg = AppConfig.query.first()
    if cfg is None:
        cfg = AppConfig(
            registration_enabled=current_app.config.get(
                "REGISTRATION_ENABLED_DEFAULT", True
            ),
            deepseek_api_key=None,
            deepseek_system_prompt=(
                "You are an assistant that writes concise, engaging video "
                "titles and descriptions for a video website."
            ),
            deepseek_user_prompt_template=(
                "You help write YouTube-style video titles and descriptions.\n\n"
                "Given this video file name: \"{filename}\",\n"
                "1. Generate a short, catchy title (max 80 characters).\n"
                "2. Generate a 2–3 sentence description.\n\n"
                "Respond ONLY as JSON like:\n"
                "{\n"
                '  \"title\": \"...\",\n'
                '  \"description\": \"...\"\n'
                "}\n"
            ),
            site_name="MyTube",
            footer_text="© MyTube",
            transcoding_backend="cpu",
        )
        db.session.add(cfg)

    # Default admin
    if User.query.filter_by(is_admin=True).count() == 0:
        admin = User(email="admin@example.com", is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)

    db.session.commit()
