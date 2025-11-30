from functools import wraps
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)
from . import db
from .models import User, AppConfig

auth_bp = Blueprint("auth", __name__)


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            flash("Admin access only.", "danger")
            return redirect(url_for("main.index"))
        return view(*args, **kwargs)

    return wrapped


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # "Stay signed in" checkbox (value="1" in the form)
        remember = request.form.get("remember") == "1"

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id

            # Make this session persistent (uses PERMANENT_SESSION_LIFETIME from config)
            session.permanent = remember

            flash("Logged in.", "success")
            next_url = request.args.get("next") or url_for("main.index")
            return redirect(next_url)
        flash("Invalid credentials.", "danger")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out.", "info")
    return redirect(url_for("main.index"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    app_cfg = AppConfig.query.first()
    if not app_cfg or not app_cfg.registration_enabled:
        flash("Registrations are disabled.", "warning")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password required.", "danger")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return render_template("register.html")

        # Get IP address used for registration (self-registration only)
        ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip_addr and "," in ip_addr:
            # take first IP in X-Forwarded-For chain
            ip_addr = ip_addr.split(",")[0].strip()

        # Username not set during registration; stays None
        user = User(email=email, registered_ip=ip_addr)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Registered. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html")


# -------- Profile / Change password --------

@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()

    if request.method == "POST":
        form_type = request.form.get("form_type", "password")

        # Update profile info: username, gender, about_me, privacy
        if form_type == "profile":
            username = (request.form.get("username") or "").strip()
            gender = (request.form.get("gender") or "").strip()
            about_me = (request.form.get("about_me") or "").strip()

            user.username = username or None
            user.gender = gender or None
            user.about_me = about_me or None

            # Privacy toggles
            user.liked_videos_public = bool(request.form.get("liked_videos_public"))
            user.comments_public = bool(request.form.get("comments_public"))

            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("auth.profile"))

        # Update password
        elif form_type == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not current_password or not new_password or not confirm_password:
                flash("All password fields are required.", "danger")
                return render_template("profile.html", user=user)

            if not user.check_password(current_password):
                flash("Current password is incorrect.", "danger")
                return render_template("profile.html", user=user)

            if new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                return render_template("profile.html", user=user)

            if len(new_password) < 6:
                flash("New password must be at least 6 characters long.", "danger")
                return render_template("profile.html", user=user)

            user.set_password(new_password)
            db.session.commit()
            flash("Password updated successfully.", "success")
            return redirect(url_for("auth.profile"))

    return render_template("profile.html", user=user)
