"""Student controllers."""
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_

from models import EXAM_TYPES, Booking, Course, Examination, Slot, db, local_now
from routes import flash_rule_error, get_or_404, role_required
from scorecard import build_scorecard_pdf
from services import RuleError, cancel_booking, create_booking

bp = Blueprint("student", __name__, url_prefix="/student")


@bp.before_request
@role_required("student")
def _guard():
    return None


def _my_bookings():
    return Booking.query.filter_by(student_id=current_user.id)


@bp.route("/")
def dashboard():
    now = local_now()
    active = _my_bookings().filter_by(status="booked").join(Slot).order_by(Slot.exam_date, Slot.start_time).all()
    upcoming = [b for b in active if b.slot.end_dt >= now]
    results = [b for b in _my_bookings().filter_by(status="completed").all()
               if b.evaluation and b.evaluation.published_at]
    open_exams = [e for e in Examination.query.filter_by(status="active").all() if e.booking_allowed(now)]
    booked_exam_ids = {b.examination_id for b in _my_bookings().filter(Booking.status.in_(["booked", "completed"]))}
    stats = {
        "Upcoming exams": len(upcoming),
        "Open for booking": len([e for e in open_exams if e.id not in booked_exam_ids]),
        "Completed": _my_bookings().filter_by(status="completed").count(),
        "Results published": len(results),
    }
    return render_template("student/dashboard.html", upcoming=upcoming, results=results[:5],
                           open_exams=[e for e in open_exams if e.id not in booked_exam_ids][:5], stats=stats)


@bp.route("/exams")
def exams():
    q = request.args.get("q", "").strip()
    course_id = request.args.get("course_id", type=int)
    exam_type = request.args.get("type", "")
    only_open = request.args.get("open") == "1"
    query = (Examination.query.join(Course)
             .filter(Examination.status == "active", Course.status == "active"))
    if q:
        query = query.filter(or_(Examination.name.ilike(f"%{q}%"), Course.name.ilike(f"%{q}%"),
                                 Course.course_code.ilike(f"%{q}%")))
    if course_id:
        query = query.filter(Examination.course_id == course_id)
    if exam_type:
        query = query.filter(Examination.exam_type == exam_type)
    items = query.order_by(Examination.booking_end).all()
    if only_open:
        items = [e for e in items if e.booking_allowed()]
    my = {b.examination_id: b for b in _my_bookings().filter(Booking.status.in_(["booked", "completed"]))}
    return render_template("student/exams.html", exams=items, my=my, q=q, course_id=course_id,
                           exam_type=exam_type, only_open=only_open, types=EXAM_TYPES,
                           courses=Course.query.filter_by(status="active").order_by(Course.course_code).all())


@bp.route("/exams/<int:exam_id>")
def exam_detail(exam_id):
    exam = get_or_404(Examination, exam_id)
    if exam.status != "active" or exam.course.status != "active":
        abort(404)
    now = local_now()
    slots = [s for s in exam.slots if s.start_dt > now and s.status in ("open", "full")]
    slots.sort(key=lambda s: s.start_dt)
    mine = _my_bookings().filter(Booking.examination_id == exam.id,
                                 Booking.status.in_(["booked", "completed"])).first()
    return render_template("student/exam_detail.html", exam=exam, slots=slots, mine=mine,
                           can_book=exam.booking_allowed(now))


@bp.post("/slots/<int:slot_id>/book")
def book(slot_id):
    slot = get_or_404(Slot, slot_id)
    try:
        b = create_booking(current_user, slot)
        flash(f"Booked! Booking #{b.id} for {slot.examination.name} on {slot.label()}. "
              "A confirmation e-mail has been sent.", "success")
        return redirect(url_for("student.bookings"))
    except RuleError as e:
        flash_rule_error(e)
        return redirect(url_for("student.exam_detail", exam_id=slot.examination_id))


@bp.post("/bookings/<int:booking_id>/cancel")
def cancel(booking_id):
    booking = get_or_404(Booking, booking_id)
    if booking.student_id != current_user.id:
        abort(403)
    try:
        cancel_booking(booking, current_user, request.form.get("reason", "").strip() or None)
        flash("Booking cancelled. Your seat has been released.", "info")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(url_for("student.bookings"))


@bp.route("/bookings")
def bookings():
    status = request.args.get("status", "")
    query = _my_bookings()
    if status:
        query = query.filter_by(status=status)
    rows = query.order_by(Booking.booked_at.desc()).all()
    return render_template("student/bookings.html", rows=rows, status=status)


@bp.route("/bookings/<int:booking_id>")
def booking_detail(booking_id):
    booking = get_or_404(Booking, booking_id)
    if booking.student_id != current_user.id:
        abort(403)
    return render_template("student/booking_detail.html", booking=booking)


@bp.route("/results")
def results():
    rows = [b for b in _my_bookings().filter_by(status="completed").order_by(Booking.booked_at.desc())
            if b.evaluation and b.evaluation.published_at]
    return render_template("student/results.html", rows=rows)


def _published_booking(booking_id):
    booking = get_or_404(Booking, booking_id)
    if booking.student_id != current_user.id:
        abort(403)
    if not booking.evaluation or not booking.evaluation.published_at:
        abort(404)  # unpublished results stay hidden from students
    return booking


@bp.route("/results/<int:booking_id>")
def result_detail(booking_id):
    booking = _published_booking(booking_id)
    return render_template("student/result_detail.html", booking=booking, ev=booking.evaluation)


@bp.route("/results/<int:booking_id>/scorecard.pdf")
def scorecard(booking_id):
    booking = _published_booking(booking_id)
    pdf = build_scorecard_pdf(booking)
    name = f"scorecard_{booking.examination.course.course_code}_{booking.id}.pdf"
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=name)
