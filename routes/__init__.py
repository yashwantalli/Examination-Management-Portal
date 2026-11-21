"""Shared helpers for the role-specific blueprints."""
from functools import wraps

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user

from models import db


def role_required(*roles, allow_pending=False):
    """Allow the view only for logged-in users having one of ``roles``.

    Examiners must also be approved by the Admin unless ``allow_pending`` is set.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            is_api = request.path.startswith("/api/")
            if not current_user.is_authenticated:
                if is_api:
                    return jsonify(error="Authentication required."), 401
                return redirect(url_for("auth.login", next=request.full_path))
            if current_user.role not in roles:
                if is_api:
                    return jsonify(error="Your role is not allowed to use this resource."), 403
                abort(403)
            if current_user.is_examiner and not allow_pending and not current_user.is_approved_examiner:
                if is_api:
                    return jsonify(error="Examiner account is awaiting admin approval."), 403
                return redirect(url_for("examiner.pending"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


def flash_rule_error(err):
    flash(err.message, "danger")
