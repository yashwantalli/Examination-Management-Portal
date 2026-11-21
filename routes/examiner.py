"""Examiner controllers. Every view is limited to the examiner's own slots and students."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from models import Booking, Examination, ExaminerAssignment, Slot, local_now
from routes import flash_rule_error, get_or_404, role_required
from services import RuleError, create_slot, delete_slot, save_evaluation, update_slot

bp = Blueprint("examiner", __name__, url_prefix="/examiner")


def _assigned_exams():
    return (Examination.query.join(ExaminerAssignment)
            .filter(ExaminerAssignment.examiner_id == current_user.id,
                    ExaminerAssignment.status == "active")
            .order_by(Examination.booking_start.desc()).all())


def _own_slot(slot_id):
    slot = get_or_404(Slot, slot_id)
    if slot.examiner_id != current_user.id:
        abort(403)
    return slot


@bp.route("/pending")
@role_required("examiner", allow_pending=True)
def pending():
    if current_user.is_approved_examiner:
        return redirect(url_for("examiner.dashboard"))
    return render_template("examiner/pending.html", status=current_user.examiner_profile.approval_status)


@bp.route("/")
@role_required("examiner")
def dashboard():
    exams = _assigned_exams()
    slots = (Slot.query.filter_by(examiner_id=current_user.id)
             .order_by(Slot.exam_date, Slot.start_time).all())
    now = local_now()
    upcoming = [s for s in slots if s.end_dt >= now and s.status != "cancelled"]
    pending_evals = (Booking.query.join(Slot)
                     .filter(Slot.examiner_id == current_user.id, Booking.status == "booked")
                     .order_by(Slot.exam_date, Slot.start_time).all())
    pending_evals = [b for b in pending_evals if b.slot.start_dt <= now]
    stats = {
        "Assigned exams": len(exams),
        "My slots": len(slots),
        "Booked students": sum(s.booked_count for s in slots),
        "Pending evaluations": len(pending_evals),
    }
    return render_template("examiner/dashboard.html", exams=exams, upcoming=upcoming[:8],
                           pending_evals=pending_evals, stats=stats)


@bp.route("/exams/<int:exam_id>")
@role_required("examiner")
def exam_detail(exam_id):
    exam = get_or_404(Examination, exam_id)
    if exam not in _assigned_exams():
        abort(403)
    my_slots = [s for s in exam.slots if s.examiner_id == current_user.id]
    return render_template("examiner/exam_detail.html", exam=exam, slots=my_slots)


@bp.route("/slots")
@role_required("examiner")
def slots():
    my = (Slot.query.filter_by(examiner_id=current_user.id)
          .order_by(Slot.exam_date.desc(), Slot.start_time).all())
    return render_template("examiner/slots.html", slots=my)


@bp.route("/slots/new", methods=["GET", "POST"])
@role_required("examiner")
def slot_new():
    exams = _assigned_exams()
    form = request.form
    if request.method == "POST":
        exam = get_or_404(Examination, form.get("examination_id", type=int) or 0)
        if exam not in exams:
            abort(403)
        try:
            slot = create_slot(current_user, exam, form.get("exam_date"), form.get("start_time"),
                               form.get("end_time"), form.get("capacity"), form.get("venue"))
            flash(f"Slot created for {exam.name} on {slot.label()}.", "success")
            return redirect(url_for("examiner.slots"))
        except RuleError as e:
            flash_rule_error(e)
    selected = request.values.get("exam_id", type=int) or form.get("examination_id", type=int)
    return render_template("examiner/slot_form.html", slot=None, exams=exams, form=form, selected=selected)


@bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"])
@role_required("examiner")
def slot_edit(slot_id):
    slot = _own_slot(slot_id)
    form = request.form
    if request.method == "POST":
        try:
            update_slot(current_user, slot, form.get("exam_date"), form.get("start_time"),
                        form.get("end_time"), form.get("capacity"), form.get("venue"))
            flash("Slot updated.", "success")
            return redirect(url_for("examiner.slot_detail", slot_id=slot.id))
        except RuleError as e:
            flash_rule_error(e)
    return render_template("examiner/slot_form.html", slot=slot, exams=[slot.examination], form=form,
                           selected=slot.examination_id)


@bp.post("/slots/<int:slot_id>/delete")
@role_required("examiner")
def slot_delete(slot_id):
    slot = _own_slot(slot_id)
    try:
        delete_slot(current_user, slot)
        flash("Slot removed.", "success")
        return redirect(url_for("examiner.slots"))
    except RuleError as e:
        flash_rule_error(e)
        return redirect(url_for("examiner.slot_detail", slot_id=slot.id))


@bp.route("/slots/<int:slot_id>")
@role_required("examiner")
def slot_detail(slot_id):
    slot = _own_slot(slot_id)
    return render_template("examiner/slot_detail.html", slot=slot)


@bp.route("/bookings/<int:booking_id>/evaluate", methods=["GET", "POST"])
@role_required("examiner")
def evaluate(booking_id):
    booking = get_or_404(Booking, booking_id)
    if booking.slot.examiner_id != current_user.id:
        abort(403)  # an examiner may only see students booked into their own slots
    exam = booking.examination
    if request.method == "POST":
        scores = {c.id: request.form.get(f"score_{c.id}") for c in exam.criteria}
        comments = {c.id: request.form.get(f"comment_{c.id}") for c in exam.criteria}
        try:
            ev = save_evaluation(current_user, booking, scores, request.form.get("remarks"), comments)
            flash(f"Evaluation saved for {booking.student.name}: {ev.total_marks:g}/{exam.maximum_marks}. "
                  "The student will see it after the administrator publishes results.", "success")
            return redirect(url_for("examiner.slot_detail", slot_id=booking.slot_id))
        except RuleError as e:
            flash_rule_error(e)
    existing = {}
    if booking.evaluation:
        existing = {s.rubric_criterion_id: s for s in booking.evaluation.scores}
    return render_template("examiner/evaluate.html", booking=booking, exam=exam, existing=existing,
                           form=request.form)


@bp.route("/evaluations")
@role_required("examiner")
def evaluations():
    rows = (Booking.query.join(Slot).filter(Slot.examiner_id == current_user.id,
                                            Booking.status == "completed")
            .order_by(Slot.exam_date.desc()).all())
    return render_template("examiner/evaluations.html", rows=rows)
