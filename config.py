import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'video_app.sqlite'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    VIDEO_UPLOAD_DIR = os.environ.get(
        "VIDEO_UPLOAD_DIR", str(BASE_DIR / "video_storage")
    )

    # Directory to store generated thumbnails
    THUMBNAIL_DIR = os.environ.get(
        "THUMBNAIL_DIR", str(BASE_DIR / "video_thumbnails")
    )

    # Max size 15 GB (fixed syntax)
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024 * 1024

    REGISTRATION_ENABLED_DEFAULT = True