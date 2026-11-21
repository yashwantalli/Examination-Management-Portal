"""Registration, login, logout and profile editing."""
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from extensions import LOGIN_LIMIT, REGISTER_LIMIT, limiter
from models import ExaminerProfile, Notification, StudentProfile, User, db

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_password(pw):
    if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Password must be at least 8 characters and contain letters and numbers."
    return None


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMIT, methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            current_app.logger.warning("Failed login for %r from %s", email, request.remote_addr)
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", email=email), 401
        if not user.is_active:
            flash("This account has been deactivated. Contact the administrator.", "danger")
            return render_template("auth/login.html", email=email), 403
        login_user(user)
        current_app.logger.info("Login: user %s (%s)", user.id, user.role)
        flash(f"Welcome back, {user.name}!", "success")
        nxt = _safe_next(request.args.get("next"))
        return redirect(nxt or url_for(f"{user.role}.dashboard"))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit(REGISTER_LIMIT, methods=["POST"])
def register():
    """Public registration — only Student or Examiner. Admin sign-up is not offered."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    role = request.values.get("role", "student")
    if role not in ("student", "examiner"):
        role = "student"
    form = request.form
    if request.method == "POST":
        name = form.get("name", "").strip()
        email = form.get("email", "").strip().lower()
        pw, pw2 = form.get("password", ""), form.get("confirm_password", "")
        errors = []
        if len(name) < 2:
            errors.append("Please enter your full name.")
        if not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address.")
        elif User.query.filter_by(email=email).first():
            errors.append("An account with this email already exists.")
        pw_err = validate_password(pw)
        if pw_err:
            errors.append(pw_err)
        if pw != pw2:
            errors.append("Passwords do not match.")
        if role == "student":
            number = form.get("student_number", "").strip().upper()
            if not re.match(r"^[A-Z0-9-]{4,20}$", number):
                errors.append("Student ID must be 4–20 letters/digits.")
            elif StudentProfile.query.filter_by(student_number=number).first():
                errors.append("This Student ID is already registered.")
        else:
            if len(form.get("department", "").strip()) < 2:
                errors.append("Please enter your department.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/register.html", role=role, form=form), 400

        user = User(name=name, email=email, role=role, is_active=True)
        user.set_password(pw)
        if role == "student":
            user.student_profile = StudentProfile(student_number=number,
                                                  program=form.get("program", "").strip() or None,
                                                  contact_details=form.get("contact", "").strip() or None)
        else:
            user.examiner_profile = ExaminerProfile(department=form.get("department").strip(),
                                                    contact_details=form.get("contact", "").strip() or None,
                                                    approval_status="pending")
        db.session.add(user)
        db.session.commit()
        if role == "examiner":
            flash("Registration received. You can log in, but examiner features unlock after the "
                  "administrator approves your account.", "info")
        else:
            flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", role=role, form=form)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u = current_user
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip() or None
        if len(name) < 2:
            flash("Name is too short.", "danger")
            return redirect(url_for("auth.profile"))
        u.name = name
        if u.student_profile:
            u.student_profile.contact_details = contact
            u.student_profile.program = request.form.get("program", "").strip() or None
        if u.examiner_profile:
            u.examiner_profile.contact_details = contact
            dept = request.form.get("department", "").strip()
            if dept:
                u.examiner_profile.department = dept
        new_pw = request.form.get("new_password", "")
        if new_pw:
            if not u.check_password(request.form.get("current_password", "")):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("auth.profile"))
            err = validate_password(new_pw)
            if err:
                flash(err, "danger")
                return redirect(url_for("auth.profile"))
            u.set_password(new_pw)
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")


@bp.route("/notifications")
@login_required
def notifications():
    items = (Notification.query.filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(100).all())
    return render_template("shared/notifications.html", items=items, show_recipient=False)
