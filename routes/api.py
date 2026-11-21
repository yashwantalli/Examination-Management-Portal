"""JSON API resources (documented in api.yaml).

Authentication uses the same Flask-Login session as the web UI. Obtain the session
cookie with POST /api/v1/auth/login, then call the other endpoints with it.
Write requests must use Content-Type: application/json.
"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_user, logout_user
from sqlalchemy import or_

from models import Booking, Course, ExaminerProfile, Examination, Slot, StudentProfile, User, db
from routes import role_required
from routes.admin import dashboard_stats
from services import RuleError, cancel_booking, create_booking, set_examiner_status

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.errorhandler(RuleError)
def _rule_error(e):
    return jsonify(error=e.message), e.status


def _404(what="Resource"):
    return jsonify(error=f"{what} not found."), 404


# ------------------------------------------------------------------ auth
@bp.post("/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(str(data.get("password", ""))):
        return jsonify(error="Invalid email or password."), 401
    if not user.is_active:
        return jsonify(error="Account is deactivated."), 403
    login_user(user)
    return jsonify(message="Logged in.", user=user.to_dict())


@bp.post("/auth/logout")
@role_required("admin", "examiner", "student", allow_pending=True)
def api_logout():
    logout_user()
    return jsonify(message="Logged out.")


@bp.get("/me")
@role_required("admin", "examiner", "student", allow_pending=True)
def me():
    return jsonify(current_user.to_dict())


# ------------------------------------------------------------------ examinations
@bp.get("/examinations")
@role_required("admin", "examiner", "student")
def examinations():
    query = Examination.query.join(Course)
    if not current_user.is_admin:
        query = query.filter(Examination.status == "active", Course.status == "active")
    elif request.args.get("status"):
        query = query.filter(Examination.status == request.args["status"])
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(or_(Examination.name.ilike(f"%{q}%"), Course.course_code.ilike(f"%{q}%")))
    if request.args.get("course_id", type=int):
        query = query.filter(Examination.course_id == request.args.get("course_id", type=int))
    items = query.order_by(Examination.id).all()
    if request.args.get("open") == "true":
        items = [e for e in items if e.booking_allowed()]
    return jsonify([e.to_dict() for e in items])


def _visible_exam(exam_id):
    exam = db.session.get(Examination, exam_id)
    if exam is None or (not current_user.is_admin and exam.status != "active"):
        return None
    return exam


@bp.get("/examinations/<int:exam_id>")
@role_required("admin", "examiner", "student")
def examination(exam_id):
    exam = _visible_exam(exam_id)
    if not exam:
        return _404("Examination")
    return jsonify(exam.to_dict(detail=True))


@bp.get("/examinations/<int:exam_id>/slots")
@role_required("admin", "examiner", "student")
def examination_slots(exam_id):
    exam = _visible_exam(exam_id)
    if not exam:
        return _404("Examination")
    slots = sorted(exam.slots, key=lambda s: s.start_dt)
    if request.args.get("available") == "true":
        slots = [s for s in slots if s.status == "open" and s.available_seats > 0]
    return jsonify([s.to_dict() for s in slots])


# ------------------------------------------------------------------ examiners & students (admin)
@bp.get("/examiners")
@role_required("admin")
def examiners():
    query = User.query.join(ExaminerProfile)
    if request.args.get("status"):
        query = query.filter(ExaminerProfile.approval_status == request.args["status"])
    return jsonify([u.to_dict() for u in query.order_by(User.name).all()])


@bp.patch("/examiners/<int:user_id>")
@role_required("admin")
def examiner_update(user_id):
    user = db.session.get(User, user_id)
    if not user or not user.is_examiner:
        return _404("Examiner")
    data = request.get_json(silent=True) or {}
    set_examiner_status(user, str(data.get("approval_status", "")), current_user)
    return jsonify(user.to_dict())


@bp.get("/students")
@role_required("admin")
def students():
    query = User.query.join(StudentProfile)
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%"),
                                 StudentProfile.student_number.ilike(f"%{q}%")))
    return jsonify([u.to_dict() for u in query.order_by(User.name).all()])


# ------------------------------------------------------------------ bookings
def _visible_bookings():
    query = Booking.query
    if current_user.is_student:
        query = query.filter(Booking.student_id == current_user.id)
    elif current_user.is_examiner:
        query = query.join(Slot).filter(Slot.examiner_id == current_user.id)
    return query


@bp.get("/bookings")
@role_required("admin", "examiner", "student")
def bookings():
    query = _visible_bookings()
    if request.args.get("status"):
        query = query.filter(Booking.status == request.args["status"])
    if request.args.get("examination_id", type=int):
        query = query.filter(Booking.examination_id == request.args.get("examination_id", type=int))
    return jsonify([b.to_dict() for b in query.order_by(Booking.id.desc()).all()])


@bp.get("/bookings/<int:booking_id>")
@role_required("admin", "examiner", "student")
def booking(booking_id):
    b = _visible_bookings().filter(Booking.id == booking_id).first()
    if not b:
        return _404("Booking")
    return jsonify(b.to_dict())


@bp.post("/bookings")
@role_required("student")
def booking_create():
    data = request.get_json(silent=True) or {}
    try:
        slot = db.session.get(Slot, int(data.get("slot_id")))
    except (TypeError, ValueError):
        slot = None
    if slot is None:
        return jsonify(error="A valid slot_id is required."), 400
    b = create_booking(current_user, slot)
    return jsonify(b.to_dict()), 201


@bp.delete("/bookings/<int:booking_id>")
@role_required("admin", "student")
def booking_cancel(booking_id):
    b = _visible_bookings().filter(Booking.id == booking_id).first()
    if not b:
        return _404("Booking")
    cancel_booking(b, current_user, request.args.get("reason"))
    return jsonify(b.to_dict())


# ------------------------------------------------------------------ statistics
@bp.get("/stats")
@role_required("admin")
def stats():
    counts, pending, trend, workload, completion = dashboard_stats()
    return jsonify({
        "counts": {k.lower(): v for k, v in counts.items()},
        "pending_examiners": pending,
        "booking_trend": [{"day": d, "bookings": c} for d, c in trend],
        "examiner_workload": [{"examiner": n, "bookings": c} for n, c in workload],
        "examination_completion": [{"examination": n, "evaluated": d, "booked": t} for n, d, t in completion],
    })
