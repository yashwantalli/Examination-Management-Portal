"""Admin (superuser) controllers."""
from collections import OrderedDict
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from models import (EXAM_TYPES, Booking, BookingEvent, Course, Evaluation, ExaminerAssignment,
                    ExaminerProfile, Examination, Notification, RubricCriterion, Slot, StudentProfile,
                    User, db, local_now)
from routes import flash_rule_error, get_or_404, role_required
from services import (RuleError, assign_examiner, cancel_booking, publish_results,
                      reassign_slot_examiner, reschedule_booking, set_examiner_status,
                      unassign_examiner)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@role_required("admin")
def _guard():
    return None


# =========================================================================== dashboard
def dashboard_stats():
    counts = OrderedDict([
        ("Courses", Course.query.count()),
        ("Examinations", Examination.query.count()),
        ("Examiners", User.query.filter_by(role="examiner").count()),
        ("Students", User.query.filter_by(role="student").count()),
        ("Slots", Slot.query.count()),
        ("Bookings", Booking.query.count()),
    ])
    pending_examiners = ExaminerProfile.query.filter_by(approval_status="pending").count()

    # Booking trend: bookings created per day for the last 14 days.
    today = local_now().date()
    start = today - timedelta(days=13)
    rows = (db.session.query(func.date(Booking.booked_at), func.count(Booking.id))
            .filter(Booking.booked_at >= datetime.combine(start, datetime.min.time()))
            .group_by(func.date(Booking.booked_at)).all())
    by_day = {str(d): c for d, c in rows}
    trend = [((start + timedelta(days=i)).strftime("%d %b"), by_day.get(str(start + timedelta(days=i)), 0))
             for i in range(14)]

    # Examiner workload: active bookings in each examiner's slots.
    workload_rows = (db.session.query(User.name, func.count(Booking.id))
                     .join(Slot, Slot.examiner_id == User.id)
                     .outerjoin(Booking, (Booking.slot_id == Slot.id) & Booking.status.in_(["booked", "completed"]))
                     .group_by(User.id).order_by(func.count(Booking.id).desc()).limit(8).all())
    workload = [(n, c) for n, c in workload_rows]

    # Examination completion: evaluated vs. active bookings.
    completion = []
    for exam in Examination.query.order_by(Examination.id).all():
        total = Booking.query.filter(Booking.examination_id == exam.id,
                                     Booking.status.in_(["booked", "completed"])).count()
        done = Booking.query.filter_by(examination_id=exam.id, status="completed").count()
        if total:
            completion.append((exam.name, done, total))
    return counts, pending_examiners, trend, workload, completion


@bp.route("/")
def dashboard():
    counts, pending_examiners, trend, workload, completion = dashboard_stats()
    upcoming = (Slot.query.filter(Slot.exam_date >= local_now().date(), Slot.status.in_(["open", "full"]))
                .order_by(Slot.exam_date, Slot.start_time).limit(6).all())
    recent = BookingEvent.query.order_by(BookingEvent.id.desc()).limit(8).all()
    return render_template("admin/dashboard.html", counts=counts, pending_examiners=pending_examiners,
                           trend=trend, workload=workload, completion=completion,
                           upcoming=upcoming, recent=recent)


# =========================================================================== courses
@bp.route("/courses", methods=["GET", "POST"])
def courses():
    if request.method == "POST":
        code = request.form.get("course_code", "").strip().upper()
        name = request.form.get("name", "").strip()
        if not code or not name:
            flash("Course code and name are required.", "danger")
        elif Course.query.filter_by(course_code=code).first():
            flash(f"Course code {code} already exists.", "danger")
        else:
            db.session.add(Course(course_code=code, name=name,
                                  description=request.form.get("description", "").strip() or None))
            db.session.commit()
            flash(f"Course {code} created.", "success")
        return redirect(url_for("admin.courses"))
    q = request.args.get("q", "").strip()
    query = Course.query
    if q:
        query = query.filter(or_(Course.name.ilike(f"%{q}%"), Course.course_code.ilike(f"%{q}%")))
    return render_template("admin/courses.html", courses=query.order_by(Course.course_code).all(), q=q)


@bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
def course_edit(course_id):
    course = get_or_404(Course, course_id)
    if request.method == "POST":
        code = request.form.get("course_code", "").strip().upper()
        name = request.form.get("name", "").strip()
        clash = Course.query.filter(Course.course_code == code, Course.id != course.id).first()
        if not code or not name:
            flash("Course code and name are required.", "danger")
        elif clash:
            flash(f"Course code {code} already exists.", "danger")
        else:
            course.course_code, course.name = code, name
            course.description = request.form.get("description", "").strip() or None
            db.session.commit()
            flash("Course updated.", "success")
            return redirect(url_for("admin.courses"))
    return render_template("admin/course_form.html", course=course)


@bp.post("/courses/<int:course_id>/toggle")
def course_toggle(course_id):
    course = get_or_404(Course, course_id)
    course.status = "inactive" if course.status == "active" else "active"
    db.session.commit()
    flash(f"Course {course.course_code} is now {course.status}.", "info")
    return redirect(request.referrer or url_for("admin.courses"))


@bp.post("/courses/<int:course_id>/delete")
def course_delete(course_id):
    course = get_or_404(Course, course_id)
    if course.examinations:
        flash("This course has examinations, so it cannot be deleted. Deactivate it instead.", "warning")
    else:
        db.session.delete(course)
        db.session.commit()
        flash("Course deleted.", "success")
    return redirect(url_for("admin.courses"))


# =========================================================================== examinations
def _parse_dt(field):
    raw = request.form.get(field, "")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _exam_from_form(exam):
    f = request.form
    errors = []
    course = db.session.get(Course, f.get("course_id", type=int) or 0)
    if not course:
        errors.append("Please choose a course.")
    name = f.get("name", "").strip()
    if len(name) < 3:
        errors.append("Examination name is too short.")
    exam_type = f.get("exam_type")
    if exam_type not in EXAM_TYPES:
        errors.append("Choose a valid examination type.")
    nums = {}
    for key, lo, hi in (("duration_minutes", 5, 600), ("maximum_marks", 1, 1000),
                        ("passing_marks", 0, 1000), ("cancellation_hours", 0, 720)):
        v = f.get(key, type=int)
        if v is None or v < lo or v > hi:
            errors.append(f"{key.replace('_', ' ').capitalize()} must be between {lo} and {hi}.")
        nums[key] = v
    if not errors and nums["passing_marks"] > nums["maximum_marks"]:
        errors.append("Passing marks cannot exceed maximum marks.")
    dts = {k: _parse_dt(k) for k in ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end")}
    if any(v is None for v in dts.values()):
        errors.append("All four timeline dates are required.")
    else:
        if dts["slot_creation_end"] <= dts["slot_creation_start"]:
            errors.append("Slot creation must end after it starts.")
        if dts["booking_end"] <= dts["booking_start"]:
            errors.append("Booking must end after it starts.")
        if dts["booking_start"] < dts["slot_creation_start"]:
            errors.append("Booking cannot open before slot creation opens.")
    if exam.id and exam.criteria and nums.get("maximum_marks") and exam.rubric_total > nums["maximum_marks"]:
        errors.append(f"The rubric already totals {exam.rubric_total} marks; maximum marks cannot be lower.")
    status = f.get("status", "active")
    if status not in ("draft", "active", "inactive", "completed"):
        errors.append("Invalid status.")
    if errors:
        return errors
    exam.course_id, exam.name, exam.exam_type = course.id, name, exam_type
    exam.description = f.get("description", "").strip() or None
    for k, v in nums.items():
        setattr(exam, k, v)
    for k, v in dts.items():
        setattr(exam, k, v)
    exam.slot_creation_open = bool(f.get("slot_creation_open"))
    exam.booking_open = bool(f.get("booking_open"))
    exam.status = status
    return []


@bp.route("/exams")
def exams():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    query = Examination.query.join(Course)
    if q:
        query = query.filter(or_(Examination.name.ilike(f"%{q}%"), Course.course_code.ilike(f"%{q}%"),
                                 Course.name.ilike(f"%{q}%")))
    if status:
        query = query.filter(Examination.status == status)
    return render_template("admin/exams.html", exams=query.order_by(Examination.booking_start.desc()).all(),
                           q=q, status=status)


@bp.route("/exams/new", methods=["GET", "POST"])
def exam_new():
    exam = Examination()
    if request.method == "POST":
        errors = _exam_from_form(exam)
        if not errors:
            db.session.add(exam)
            db.session.commit()
            flash("Examination created. Now add rubric criteria and assign examiners.", "success")
            return redirect(url_for("admin.exam_detail", exam_id=exam.id))
        for e in errors:
            flash(e, "danger")
    else:
        base = local_now().replace(minute=0, second=0)
        exam.slot_creation_start, exam.slot_creation_end = base, base + timedelta(days=7)
        exam.booking_start, exam.booking_end = base + timedelta(days=1), base + timedelta(days=14)
        exam.duration_minutes, exam.maximum_marks, exam.passing_marks = 30, 100, 40
        exam.cancellation_hours, exam.slot_creation_open, exam.booking_open = 24, True, True
        exam.status, exam.exam_type = "active", "Viva"
    return render_template("admin/exam_form.html", exam=exam, courses=_course_choices(), types=EXAM_TYPES)


@bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
def exam_edit(exam_id):
    exam = get_or_404(Examination, exam_id)
    if request.method == "POST":
        errors = _exam_from_form(exam)
        if not errors:
            db.session.commit()
            flash("Examination updated.", "success")
            return redirect(url_for("admin.exam_detail", exam_id=exam.id))
        db.session.rollback()
        for e in errors:
            flash(e, "danger")
    return render_template("admin/exam_form.html", exam=exam, courses=_course_choices(), types=EXAM_TYPES)


def _course_choices():
    return Course.query.order_by(Course.course_code).all()


@bp.route("/exams/<int:exam_id>")
def exam_detail(exam_id):
    exam = get_or_404(Examination, exam_id)
    assigned_ids = {a.examiner_id for a in exam.assignments if a.status == "active"}
    available = (User.query.join(ExaminerProfile).filter(ExaminerProfile.approval_status == "approved",
                                                         User.is_active.is_(True))
                 .order_by(User.name).all())
    available = [u for u in available if u.id not in assigned_ids]
    bookings = (Booking.query.filter_by(examination_id=exam.id).order_by(Booking.booked_at.desc()).all())
    unpublished = (Evaluation.query.join(Booking).filter(Booking.examination_id == exam.id,
                                                         Evaluation.published_at.is_(None)).count())
    return render_template("admin/exam_detail.html", exam=exam, available=available,
                           bookings=bookings, unpublished=unpublished)


@bp.post("/exams/<int:exam_id>/toggle/<what>")
def exam_toggle(exam_id, what):
    exam = get_or_404(Examination, exam_id)
    if what == "booking":
        exam.booking_open = not exam.booking_open
        msg = f"Student booking {'opened' if exam.booking_open else 'closed'}."
    elif what == "slots":
        exam.slot_creation_open = not exam.slot_creation_open
        msg = f"Slot creation {'opened' if exam.slot_creation_open else 'closed'}."
    elif what == "status":
        exam.status = "inactive" if exam.status == "active" else "active"
        msg = f"Examination is now {exam.status}."
    else:
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    db.session.commit()
    flash(msg, "info")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


@bp.post("/exams/<int:exam_id>/delete")
def exam_delete(exam_id):
    exam = get_or_404(Examination, exam_id)
    if exam.slots:
        flash("This examination already has slots. Deactivate it instead of deleting.", "warning")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    db.session.delete(exam)
    db.session.commit()
    flash("Examination deleted.", "success")
    return redirect(url_for("admin.exams"))


@bp.post("/exams/<int:exam_id>/publish")
def exam_publish(exam_id):
    exam = get_or_404(Examination, exam_id)
    try:
        n = publish_results(exam, current_user)
        flash(f"Published {n} result(s). Students have been notified by e-mail.", "success")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


# --------------------------------------------------------------------------- rubric
def _rubric_locked(exam):
    return Evaluation.query.join(Booking).filter(Booking.examination_id == exam.id).count() > 0


def _criterion_from_form(exam, crit):
    f = request.form
    name = f.get("criterion_name", "").strip()
    max_m = f.get("maximum_marks", type=int)
    weight = f.get("weightage", type=int)
    if len(name) < 2:
        return "Criterion name is required."
    if max_m is None or max_m < 1:
        return "Criterion maximum marks must be at least 1."
    if weight is None or not 0 <= weight <= 100:
        return "Weightage must be between 0 and 100."
    dup = RubricCriterion.query.filter(RubricCriterion.examination_id == exam.id,
                                       RubricCriterion.criterion_name == name,
                                       RubricCriterion.id != (crit.id or 0)).first()
    if dup:
        return "A criterion with this name already exists."
    other = sum(c.maximum_marks for c in exam.criteria if c.id != crit.id)
    if other + max_m > exam.maximum_marks:
        return (f"Rubric total would be {other + max_m}, which exceeds the examination maximum "
                f"of {exam.maximum_marks}.")
    other_w = sum(c.weightage for c in exam.criteria if c.id != crit.id)
    if other_w + weight > 100:
        return f"Total weightage would be {other_w + weight}%, which exceeds 100%."
    crit.criterion_name, crit.maximum_marks, crit.weightage = name, max_m, weight
    crit.description = f.get("description", "").strip() or None
    return None


@bp.post("/exams/<int:exam_id>/criteria")
def criterion_add(exam_id):
    exam = get_or_404(Examination, exam_id)
    if _rubric_locked(exam):
        flash("Evaluations already exist, so the rubric is locked.", "warning")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")
    crit = RubricCriterion(examination_id=exam.id)
    err = _criterion_from_form(exam, crit)
    if err:
        flash(err, "danger")
    else:
        db.session.add(crit)
        db.session.commit()
        flash("Criterion added.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")


@bp.route("/criteria/<int:crit_id>/edit", methods=["GET", "POST"])
def criterion_edit(crit_id):
    crit = get_or_404(RubricCriterion, crit_id)
    exam = crit.examination
    if _rubric_locked(exam):
        flash("Evaluations already exist, so the rubric is locked.", "warning")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")
    if request.method == "POST":
        err = _criterion_from_form(exam, crit)
        if err:
            db.session.rollback()
            flash(err, "danger")
        else:
            db.session.commit()
            flash("Criterion updated.", "success")
            return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")
    return render_template("admin/criterion_form.html", crit=crit, exam=exam)


@bp.post("/criteria/<int:crit_id>/delete")
def criterion_delete(crit_id):
    crit = get_or_404(RubricCriterion, crit_id)
    exam = crit.examination
    if _rubric_locked(exam):
        flash("Evaluations already exist, so the rubric is locked.", "warning")
    else:
        db.session.delete(crit)
        db.session.commit()
        flash("Criterion removed.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")


# --------------------------------------------------------------------------- assignments
@bp.post("/exams/<int:exam_id>/assign")
def exam_assign(exam_id):
    exam = get_or_404(Examination, exam_id)
    examiner = db.session.get(User, request.form.get("examiner_id", type=int) or 0)
    try:
        if not examiner:
            raise RuleError("Choose an examiner.")
        assign_examiner(exam, examiner)
        flash(f"{examiner.name} assigned.", "success")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#examiners")


@bp.post("/exams/<int:exam_id>/unassign/<int:user_id>")
def exam_unassign(exam_id, user_id):
    exam = get_or_404(Examination, exam_id)
    examiner = get_or_404(User, user_id)
    try:
        unassign_examiner(exam, examiner)
        flash(f"{examiner.name} removed from this examination.", "info")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#examiners")


# =========================================================================== people
@bp.route("/examiners")
def examiners():
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    query = User.query.join(ExaminerProfile)
    if status:
        query = query.filter(ExaminerProfile.approval_status == status)
    if q:
        query = query.filter(_person_filter(q))
    users = query.order_by(ExaminerProfile.approval_status.desc(), User.name).all()
    return render_template("admin/examiners.html", users=users, status=status, q=q)


@bp.post("/examiners/<int:user_id>/status")
def examiner_status(user_id):
    examiner = get_or_404(User, user_id)
    try:
        set_examiner_status(examiner, request.form.get("status", ""), current_user)
        flash(f"{examiner.name} is now {examiner.examiner_profile.approval_status}.", "success")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(request.referrer or url_for("admin.examiners"))


@bp.route("/students")
def students():
    q = request.args.get("q", "").strip()
    query = User.query.join(StudentProfile)
    if q:
        query = query.filter(or_(_person_filter(q), StudentProfile.student_number.ilike(f"%{q}%")))
    return render_template("admin/students.html", users=query.order_by(User.name).all(), q=q)


@bp.route("/users/<int:user_id>")
def user_detail(user_id):
    user = get_or_404(User, user_id)
    if user.is_admin:
        return redirect(url_for("admin.dashboard"))
    bookings, slots = [], []
    if user.is_student:
        bookings = Booking.query.filter_by(student_id=user.id).order_by(Booking.booked_at.desc()).all()
    else:
        slots = Slot.query.filter_by(examiner_id=user.id).order_by(Slot.exam_date.desc()).all()
    return render_template("admin/user_detail.html", user=user, bookings=bookings, slots=slots)


@bp.post("/users/<int:user_id>/toggle-active")
def user_toggle(user_id):
    user = get_or_404(User, user_id)
    if user.is_admin:
        flash("The admin account cannot be deactivated.", "danger")
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash(f"{user.name} is now {'active' if user.is_active else 'deactivated'}.", "info")
    return redirect(request.referrer or url_for("admin.dashboard"))


def _person_filter(q):
    cond = or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%"))
    if q.isdigit():
        cond = or_(cond, User.id == int(q))
    return cond


# =========================================================================== slots & bookings
@bp.route("/slots")
def slots():
    exam_id = request.args.get("exam_id", type=int)
    status = request.args.get("status", "")
    query = Slot.query
    if exam_id:
        query = query.filter_by(examination_id=exam_id)
    if status:
        query = query.filter_by(status=status)
    return render_template("admin/slots.html",
                           slots=query.order_by(Slot.exam_date.desc(), Slot.start_time).all(),
                           exams=Examination.query.order_by(Examination.name).all(),
                           exam_id=exam_id, status=status)


@bp.route("/slots/<int:slot_id>", methods=["GET", "POST"])
def slot_detail(slot_id):
    slot = get_or_404(Slot, slot_id)
    if request.method == "POST":
        new_examiner = db.session.get(User, request.form.get("examiner_id", type=int) or 0)
        try:
            if not new_examiner:
                raise RuleError("Choose an examiner.")
            reassign_slot_examiner(slot, new_examiner, current_user, request.form.get("note", "").strip())
            flash(f"Slot reassigned to {new_examiner.name}. Affected students were notified.", "success")
        except RuleError as e:
            flash_rule_error(e)
        return redirect(url_for("admin.slot_detail", slot_id=slot.id))
    candidates = [u for u in slot.examination.active_examiners() if u.id != slot.examiner_id]
    return render_template("admin/slot_detail.html", slot=slot, candidates=candidates)


@bp.route("/bookings")
def bookings():
    status = request.args.get("status", "")
    exam_id = request.args.get("exam_id", type=int)
    q = request.args.get("q", "").strip()
    student_id = request.args.get("student_id", type=int)
    query = Booking.query.join(User, Booking.student_id == User.id)
    if student_id:
        query = query.filter(Booking.student_id == student_id)
    if status:
        query = query.filter(Booking.status == status)
    if exam_id:
        query = query.filter(Booking.examination_id == exam_id)
    if q:
        cond = _person_filter(q)
        if q.isdigit():
            cond = or_(cond, Booking.id == int(q))
        query = query.filter(cond)
    return render_template("admin/bookings.html", bookings=query.order_by(Booking.booked_at.desc()).all(),
                           exams=Examination.query.order_by(Examination.name).all(),
                           status=status, exam_id=exam_id, q=q, student_id=student_id)


@bp.route("/bookings/<int:booking_id>")
def booking_detail(booking_id):
    booking = get_or_404(Booking, booking_id)
    targets = [s for s in booking.examination.slots
               if s.id != booking.slot_id and s.status == "open" and s.start_dt > local_now()]
    return render_template("admin/booking_detail.html", booking=booking, targets=targets)


@bp.post("/bookings/<int:booking_id>/reschedule")
def booking_reschedule(booking_id):
    booking = get_or_404(Booking, booking_id)
    new_slot = db.session.get(Slot, request.form.get("slot_id", type=int) or 0)
    try:
        if not new_slot:
            raise RuleError("Choose a target slot.")
        reschedule_booking(booking, new_slot, current_user, request.form.get("note", "").strip() or None)
        flash("Booking rescheduled and the student notified.", "success")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(url_for("admin.booking_detail", booking_id=booking.id))


@bp.post("/bookings/<int:booking_id>/cancel")
def booking_cancel(booking_id):
    booking = get_or_404(Booking, booking_id)
    try:
        cancel_booking(booking, current_user, request.form.get("reason", "").strip() or "Cancelled by admin")
        flash("Booking cancelled and the seat released.", "success")
    except RuleError as e:
        flash_rule_error(e)
    return redirect(url_for("admin.booking_detail", booking_id=booking.id))


# =========================================================================== search & history
@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    results = {"students": [], "examiners": [], "exams": [], "bookings": []}
    if q:
        results["students"] = (User.query.join(StudentProfile)
                               .filter(or_(_person_filter(q), StudentProfile.student_number.ilike(f"%{q}%")))
                               .limit(20).all())
        results["examiners"] = User.query.join(ExaminerProfile).filter(_person_filter(q)).limit(20).all()
        exam_cond = or_(Examination.name.ilike(f"%{q}%"), Course.course_code.ilike(f"%{q}%"),
                        Course.name.ilike(f"%{q}%"))
        if q.isdigit():
            exam_cond = or_(exam_cond, Examination.id == int(q))
        results["exams"] = Examination.query.join(Course).filter(exam_cond).limit(20).all()
        bcond = or_(User.name.ilike(f"%{q}%"), Examination.name.ilike(f"%{q}%"))
        if q.isdigit():
            bcond = or_(bcond, Booking.id == int(q))
        results["bookings"] = (Booking.query.join(User, Booking.student_id == User.id)
                               .join(Examination, Booking.examination_id == Examination.id)
                               .filter(bcond).order_by(Booking.booked_at.desc()).limit(20).all())
    return render_template("admin/search.html", q=q, results=results)


@bp.route("/activity")
def activity():
    action = request.args.get("action", "")
    query = BookingEvent.query
    if action:
        query = query.filter_by(action=action)
    events = query.order_by(BookingEvent.id.desc()).limit(300).all()
    return render_template("admin/activity.html", events=events, action=action)


@bp.route("/outbox")
def outbox():
    items = Notification.query.order_by(Notification.id.desc()).limit(200).all()
    return render_template("shared/notifications.html", items=items, show_recipient=True)
