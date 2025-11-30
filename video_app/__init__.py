import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def create_app(config_class=None):
    app = Flask(__name__, instance_relative_config=True)

    from config import Config

    app.config.from_object(config_class or Config)

    # Ensure upload, thumbnail and instance directories exist
    os.makedirs(app.config["VIDEO_UPLOAD_DIR"], exist_ok=True)
    os.makedirs(app.config["THUMBNAIL_DIR"], exist_ok=True)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)

    from . import models  # noqa: F401
    from .models import AppConfig
    from .auth import auth_bp, current_user
    from .admin import admin_bp
    from .main import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_globals():
        """
        Inject globals into every template:
        - current_user (callable from auth.py)
        - registration_enabled (from AppConfig)
        - site_name and footer_text (branding, from AppConfig)
        """
        user = current_user()

        cfg = AppConfig.query.first()

        registration_enabled = cfg.registration_enabled if cfg else True
        site_name = (cfg.site_name if cfg and cfg.site_name else "MyTube")
        footer_text = (cfg.footer_text if cfg and cfg.footer_text else "© MyTube")

        return {
            "current_user": user,
            "registration_enabled": registration_enabled,
            "site_name": site_name,
            "footer_text": footer_text,
        }

    with app.app_context():
        db.create_all()
        models.init_default_admin_and_config()

    return app
