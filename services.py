"""Business rules of the portal.

Every function validates its input, raises ``RuleError`` with a user-friendly
message when a rule is broken, and commits only when everything succeeded.
Routes (HTML and JSON API) call these functions so the rules live in one place.
"""
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import current_app
from sqlalchemy import and_, update
from sqlalchemy.exc import IntegrityError

from models import (Booking, BookingEvent, Evaluation, EvaluationScore, ExaminerAssignment,
                    Notification, Slot, User, db, local_now)


class RuleError(Exception):
    """A business rule was violated. ``status`` is used by the JSON API."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ======================================================================= e-mail
def notify(user, subject, body, category):
    """Store an e-mail in the outbox and send it when SMTP is configured."""
    note = Notification(user_id=user.id, recipient_email=user.email, subject=subject,
                        body=body, category=category, status="logged")
    cfg = current_app.config
    if cfg.get("MAIL_SERVER"):
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = cfg["MAIL_SENDER"]
            msg["To"] = user.email
            msg.set_content(body)
            with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=10) as smtp:
                if cfg.get("MAIL_USE_TLS"):
                    smtp.starttls()
                if cfg.get("MAIL_USERNAME"):
                    smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
                smtp.send_message(msg)
            note.status = "sent"
        except Exception as exc:  # pragma: no cover - depends on external server
            current_app.logger.warning("E-mail to %s failed: %s", user.email, exc)
            note.status = "failed"
    db.session.add(note)
    return note


def _log(booking, action, actor, note=None, from_slot=None, to_slot=None):
    db.session.add(BookingEvent(booking=booking, action=action, actor_id=actor.id if actor else None,
                                note=note, from_slot_id=from_slot.id if from_slot else None,
                                to_slot_id=to_slot.id if to_slot else None))


def _refresh_slot_status(slot):
    if slot.status in ("open", "full"):
        slot.status = "full" if slot.available_seats == 0 else "open"


def _take_seat(slot):
    """Atomically decrement a seat. Fails if the slot is full (safe under concurrency)."""
    result = db.session.execute(
        update(Slot)
        .where(and_(Slot.id == slot.id, Slot.available_seats > 0, Slot.status == "open"))
        .values(available_seats=Slot.available_seats - 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.session.rollback()
        raise RuleError("This slot is full. Please choose another slot.", 409)
    db.session.refresh(slot)
    _refresh_slot_status(slot)


def _release_seat(slot):
    db.session.execute(
        update(Slot).where(and_(Slot.id == slot.id, Slot.available_seats < Slot.capacity))
        .values(available_seats=Slot.available_seats + 1)
        .execution_options(synchronize_session=False)
    )
    db.session.refresh(slot)
    _refresh_slot_status(slot)


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _student_conflict(student_id, slot, ignore_booking_id=None):
    q = Booking.query.filter(Booking.student_id == student_id, Booking.status == "booked")
    for b in q:
        if b.id == ignore_booking_id:
            continue
        if b.slot.exam_date == slot.exam_date and _overlaps(
                b.slot.start_dt, b.slot.end_dt, slot.start_dt, slot.end_dt):
            return b
    return None


def _examiner_conflict(examiner_id, exam_date, start_dt, end_dt, ignore_slot_id=None):
    q = Slot.query.filter(Slot.examiner_id == examiner_id, Slot.exam_date == exam_date,
                          Slot.status != "cancelled")
    for s in q:
        if s.id != ignore_slot_id and _overlaps(s.start_dt, s.end_dt, start_dt, end_dt):
            return s
    return None


# ======================================================================= bookings
def create_booking(student, slot, now=None):
    now = now or local_now()
    exam = slot.examination
    if not student.is_student or not student.is_active:
        raise RuleError("Only active student accounts can book slots.", 403)
    if exam.status != "active" or exam.course.status != "active":
        raise RuleError("This examination is not open.", 409)
    if not exam.booking_open:
        raise RuleError("Booking has been closed by the administrator for this examination.", 409)
    if now < exam.booking_start:
        raise RuleError(f"Booking opens on {exam.booking_start:%d %b %Y %H:%M}.", 409)
    if now > exam.booking_end:
        raise RuleError("The booking window for this examination has ended.", 409)
    if slot.start_dt <= now:
        raise RuleError("This slot has already started or finished.", 409)
    if slot.status != "open" or slot.available_seats <= 0:
        raise RuleError("This slot is full. Please choose another slot.", 409)
    existing = Booking.query.filter(Booking.student_id == student.id,
                                    Booking.examination_id == exam.id,
                                    Booking.status.in_(["booked", "completed"])).first()
    if existing:
        raise RuleError("You already have a booking for this examination. "
                        "Cancel it first if you want a different slot.", 409)
    clash = _student_conflict(student.id, slot)
    if clash:
        raise RuleError(f"This slot clashes with your booking for {clash.examination.name}.", 409)

    _take_seat(slot)
    booking = Booking(student_id=student.id, slot_id=slot.id, examination_id=exam.id,
                      status="booked", booked_at=now)
    db.session.add(booking)
    try:
        db.session.flush()
    except IntegrityError:  # unique partial index caught a race condition
        db.session.rollback()
        raise RuleError("You already have a booking for this examination.", 409)
    _log(booking, "booked", student, to_slot=slot)
    notify(student, f"Booking confirmed: {exam.name}",
           f"Dear {student.name},\n\nYour booking #{booking.id} for {exam.name} "
           f"({exam.course.course_code}) is confirmed.\nSlot: {slot.label()}\n"
           f"Venue: {slot.venue or 'To be announced'}\nExaminer: {slot.examiner.name}\n\n"
           f"You can cancel until {slot.cancellation_deadline:%d %b %Y %H:%M}.\n\n— EMP",
           "booking_confirmation")
    db.session.commit()
    return booking


def cancel_booking(booking, actor, reason=None, now=None):
    now = now or local_now()
    if booking.status != "booked":
        raise RuleError("Only active bookings can be cancelled.", 409)
    if booking.evaluation:
        raise RuleError("This booking has already been evaluated.", 409)
    if actor.is_student:
        if booking.student_id != actor.id:
            raise RuleError("You can only cancel your own bookings.", 403)
        if now >= booking.slot.cancellation_deadline:
            raise RuleError(
                f"The cancellation deadline ({booking.slot.cancellation_deadline:%d %b %Y %H:%M}) "
                "has passed.", 409)
    elif not actor.is_admin:
        raise RuleError("Not allowed.", 403)

    booking.status = "cancelled"
    booking.cancelled_at = now
    booking.cancel_reason = reason or ("Cancelled by student" if actor.is_student else "Cancelled by admin")
    _release_seat(booking.slot)
    _log(booking, "cancelled", actor, note=booking.cancel_reason, from_slot=booking.slot)
    notify(booking.student, f"Booking cancelled: {booking.examination.name}",
           f"Dear {booking.student.name},\n\nYour booking #{booking.id} for "
           f"{booking.examination.name} on {booking.slot.label()} was cancelled.\n"
           f"Reason: {booking.cancel_reason}\n\n— EMP", "booking_cancellation")
    db.session.commit()
    return booking


def reschedule_booking(booking, new_slot, admin, note=None, now=None):
    now = now or local_now()
    if not admin.is_admin:
        raise RuleError("Only the administrator can reschedule bookings.", 403)
    if booking.status != "booked" or booking.evaluation:
        raise RuleError("Only active, not-yet-evaluated bookings can be rescheduled.", 409)
    if new_slot.id == booking.slot_id:
        raise RuleError("The booking is already in that slot.", 400)
    if new_slot.examination_id != booking.examination_id:
        raise RuleError("A booking can only move to a slot of the same examination.", 400)
    if new_slot.start_dt <= now:
        raise RuleError("The target slot has already started.", 409)
    if new_slot.status != "open" or new_slot.available_seats <= 0:
        raise RuleError("The target slot has no free seats.", 409)
    clash = _student_conflict(booking.student_id, new_slot, ignore_booking_id=booking.id)
    if clash:
        raise RuleError(f"The student has a clashing booking ({clash.examination.name}).", 409)

    old_slot = booking.slot
    _take_seat(new_slot)
    _release_seat(old_slot)
    booking.slot_id = new_slot.id
    booking.reschedule_count += 1
    db.session.flush()
    db.session.refresh(booking)
    _log(booking, "rescheduled", admin, note=note, from_slot=old_slot, to_slot=new_slot)
    notify(booking.student, f"Booking rescheduled: {booking.examination.name}",
           f"Dear {booking.student.name},\n\nYour booking #{booking.id} was moved by the "
           f"administrator.\nOld slot: {old_slot.label()}\nNew slot: {new_slot.label()} "
           f"with {new_slot.examiner.name}\nVenue: {new_slot.venue or 'To be announced'}\n"
           f"{('Note: ' + note) if note else ''}\n\n— EMP", "booking_rescheduled")
    db.session.commit()
    return booking


def reassign_slot_examiner(slot, new_examiner, admin, note=None):
    if not admin.is_admin:
        raise RuleError("Only the administrator can reassign examiners.", 403)
    if new_examiner.id == slot.examiner_id:
        raise RuleError("That examiner already owns this slot.", 400)
    if not new_examiner.is_approved_examiner or not new_examiner.is_active:
        raise RuleError("The new examiner must be an approved, active examiner.", 400)
    assigned = ExaminerAssignment.query.filter_by(examination_id=slot.examination_id,
                                                  examiner_id=new_examiner.id, status="active").first()
    if not assigned:
        raise RuleError("The new examiner is not assigned to this examination.", 400)
    if any(b.evaluation for b in slot.bookings):
        raise RuleError("Students in this slot have already been evaluated.", 409)
    clash = _examiner_conflict(new_examiner.id, slot.exam_date, slot.start_dt, slot.end_dt,
                               ignore_slot_id=slot.id)
    if clash:
        raise RuleError(f"{new_examiner.name} already has a slot at that time.", 409)

    old = slot.examiner
    slot.examiner_id = new_examiner.id
    db.session.flush()
    db.session.refresh(slot)
    for b in slot.bookings:
        if b.status == "booked":
            _log(b, "examiner_reassigned", admin,
                 note=f"{old.name} → {new_examiner.name}" + (f" ({note})" if note else ""),
                 from_slot=slot, to_slot=slot)
            notify(b.student, f"Examiner changed: {slot.examination.name}",
                   f"Dear {b.student.name},\n\nThe examiner for your slot {slot.label()} is now "
                   f"{new_examiner.name}.\n\n— EMP", "examiner_reassigned")
    notify(new_examiner, f"New slot assigned: {slot.examination.name}",
           f"Dear {new_examiner.name},\n\nYou have been assigned slot {slot.label()} for "
           f"{slot.examination.name}.\n\n— EMP", "examiner_reassigned")
    db.session.commit()
    return slot


# ======================================================================= slots
def _parse_slot_fields(exam_date, start_time, end_time, capacity):
    try:
        d = datetime.strptime(exam_date, "%Y-%m-%d").date()
        st = datetime.strptime(start_time, "%H:%M").time()
        et = datetime.strptime(end_time, "%H:%M").time()
        cap = int(capacity)
    except (TypeError, ValueError):
        raise RuleError("Please provide a valid date, start time, end time and capacity.")
    if cap < 1 or cap > 500:
        raise RuleError("Capacity must be between 1 and 500.")
    return d, st, et, cap


def _validate_slot_times(exam, examiner_id, d, st, et, now, ignore_slot_id=None):
    start_dt, end_dt = datetime.combine(d, st), datetime.combine(d, et)
    if end_dt <= start_dt:
        raise RuleError("End time must be after start time.")
    if (end_dt - start_dt) < timedelta(minutes=exam.duration_minutes):
        raise RuleError(f"A slot must be at least {exam.duration_minutes} minutes long "
                        "(the examination duration).")
    if start_dt <= now:
        raise RuleError("Slots must be scheduled in the future.")
    if start_dt < exam.booking_start:
        raise RuleError("Slots cannot be earlier than the start of the booking window.")
    clash = _examiner_conflict(examiner_id, d, start_dt, end_dt, ignore_slot_id=ignore_slot_id)
    if clash:
        raise RuleError(f"You already have a slot at that time ({clash.examination.name}, "
                        f"{clash.label()}).", 409)


def _check_examiner_can_manage(examiner, exam, now):
    if not examiner.is_approved_examiner:
        raise RuleError("Your examiner account is not approved yet.", 403)
    assigned = ExaminerAssignment.query.filter_by(examination_id=exam.id, examiner_id=examiner.id,
                                                  status="active").first()
    if not assigned:
        raise RuleError("You are not assigned to this examination.", 403)
    if not exam.slot_creation_allowed(now):
        raise RuleError("Slot creation is closed for this examination "
                        f"(window: {exam.slot_creation_start:%d %b %H:%M} – "
                        f"{exam.slot_creation_end:%d %b %H:%M}).", 409)


def create_slot(examiner, exam, exam_date, start_time, end_time, capacity, venue=None, now=None):
    now = now or local_now()
    _check_examiner_can_manage(examiner, exam, now)
    d, st, et, cap = _parse_slot_fields(exam_date, start_time, end_time, capacity)
    _validate_slot_times(exam, examiner.id, d, st, et, now)
    slot = Slot(examination_id=exam.id, examiner_id=examiner.id, exam_date=d, start_time=st,
                end_time=et, capacity=cap, available_seats=cap, venue=(venue or "").strip() or None,
                status="open")
    db.session.add(slot)
    db.session.commit()
    return slot


def update_slot(examiner, slot, exam_date, start_time, end_time, capacity, venue=None, now=None):
    now = now or local_now()
    if slot.examiner_id != examiner.id:
        raise RuleError("You can only edit your own slots.", 403)
    _check_examiner_can_manage(examiner, slot.examination, now)
    booked = len([b for b in slot.bookings if b.status == "booked"])
    d, st, et, cap = _parse_slot_fields(exam_date, start_time, end_time, capacity)
    if booked and (d != slot.exam_date or st != slot.start_time or et != slot.end_time):
        raise RuleError("Students have already booked this slot, so its date and time are locked. "
                        "Ask the administrator to reschedule them.", 409)
    if cap < booked:
        raise RuleError(f"Capacity cannot be lower than the {booked} existing booking(s).", 409)
    _validate_slot_times(slot.examination, examiner.id, d, st, et, now, ignore_slot_id=slot.id)
    slot.exam_date, slot.start_time, slot.end_time = d, st, et
    slot.capacity, slot.available_seats = cap, cap - booked
    slot.venue = (venue or "").strip() or None
    _refresh_slot_status(slot)
    db.session.commit()
    return slot


def delete_slot(examiner, slot, now=None):
    now = now or local_now()
    if slot.examiner_id != examiner.id:
        raise RuleError("You can only remove your own slots.", 403)
    _check_examiner_can_manage(examiner, slot.examination, now)
    if slot.bookings:
        raise RuleError("This slot has booking history and cannot be removed.", 409)
    db.session.delete(slot)
    db.session.commit()


# ======================================================================= evaluation
def save_evaluation(examiner, booking, scores, remarks, comments=None, now=None):
    """scores: {criterion_id: value-as-string}. Creates or updates the single evaluation."""
    now = now or local_now()
    comments = comments or {}
    slot, exam = booking.slot, booking.examination
    if not examiner.is_approved_examiner:
        raise RuleError("Your examiner account is not approved.", 403)
    if slot.examiner_id != examiner.id:
        raise RuleError("You can only evaluate students booked in your own slots.", 403)
    if booking.status not in ("booked", "completed"):
        raise RuleError("This booking was cancelled.", 409)
    if slot.start_dt > now:
        raise RuleError("You can evaluate a student only after the slot has started.", 409)
    if not exam.criteria:
        raise RuleError("This examination has no rubric yet. Ask the administrator to add one.", 409)
    ev = booking.evaluation
    if ev and ev.published_at:
        raise RuleError("The result has already been published and is locked.", 409)

    parsed = {}
    for c in exam.criteria:
        raw = scores.get(c.id, scores.get(str(c.id)))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise RuleError(f"Enter marks for '{c.criterion_name}'.")
        if value < 0 or value > c.maximum_marks:
            raise RuleError(f"Marks for '{c.criterion_name}' must be between 0 and {c.maximum_marks}.")
        parsed[c.id] = value
    try:
        extra = {int(k) for k in scores.keys()} - {c.id for c in exam.criteria}
    except (TypeError, ValueError):
        extra = True
    if extra:
        raise RuleError("Marks were submitted for a criterion that is not in this examination's rubric.")
    total = round(sum(parsed.values()), 2)
    if total > exam.maximum_marks:
        raise RuleError(f"Total marks ({total}) exceed the examination maximum ({exam.maximum_marks}).")

    if ev is None:
        ev = Evaluation(booking_id=booking.id, examiner_id=examiner.id)
        db.session.add(ev)
    ev.scores.clear()
    db.session.flush()
    for c in exam.criteria:
        ev.scores.append(EvaluationScore(rubric_criterion_id=c.id, marks_awarded=parsed[c.id],
                                         comment=(comments.get(c.id) or comments.get(str(c.id)) or "").strip() or None))
    ev.total_marks = total
    ev.remarks = (remarks or "").strip() or None
    ev.status = "submitted"
    ev.evaluated_at = now
    first_time = booking.status != "completed"
    booking.status = "completed"
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise RuleError("An evaluation for this booking already exists.", 409)
    _log(booking, "evaluated", examiner, note=("Marks submitted" if first_time else "Marks updated")
         + f": {total}/{exam.maximum_marks}")
    db.session.commit()
    return ev


def publish_results(exam, admin, now=None):
    now = now or local_now()
    if not admin.is_admin:
        raise RuleError("Only the administrator can publish results.", 403)
    pending = (Evaluation.query.join(Booking).filter(Booking.examination_id == exam.id,
                                                     Evaluation.published_at.is_(None)).all())
    if not pending:
        raise RuleError("There are no new evaluations to publish for this examination.", 409)
    for ev in pending:
        ev.published_at = now
        ev.status = "published"
        _log(ev.booking, "published", admin, note="Result published")
        notify(ev.booking.student, f"Result published: {exam.name}",
               f"Dear {ev.booking.student.name},\n\nYour result for {exam.name} "
               f"({exam.course.course_code}) is now available.\nTotal: {ev.total_marks:g}/"
               f"{exam.maximum_marks} — {'PASS' if ev.passed else 'NOT PASSED'}\n\n"
               "Log in to the portal to view the detailed scorecard.\n\n— EMP", "result_published")
    db.session.commit()
    return len(pending)


# ======================================================================= examiners
def set_examiner_status(examiner, status, admin):
    if status not in ("approved", "rejected", "deactivated", "pending"):
        raise RuleError("Unknown approval status.")
    if not examiner.is_examiner:
        raise RuleError("User is not an examiner.", 404)
    examiner.examiner_profile.approval_status = status
    examiner.examiner_profile.reviewed_at = local_now()
    if status == "approved":
        examiner.is_active = True
        notify(examiner, "Your examiner account is approved",
               f"Dear {examiner.name},\n\nThe administrator approved your examiner account. "
               "You can now log in and manage your slots.\n\n— EMP", "examiner_approval")
    elif status in ("rejected", "deactivated"):
        notify(examiner, f"Examiner account {status}",
               f"Dear {examiner.name},\n\nYour examiner account was {status} by the administrator."
               "\n\n— EMP", "examiner_approval")
    db.session.commit()
    return examiner


def assign_examiner(exam, examiner):
    if not examiner.is_approved_examiner:
        raise RuleError("Only approved examiners can be assigned.")
    a = ExaminerAssignment.query.filter_by(examination_id=exam.id, examiner_id=examiner.id).first()
    if a and a.status == "active":
        raise RuleError("Examiner is already assigned.", 409)
    if a:
        a.status = "active"
    else:
        db.session.add(ExaminerAssignment(examination_id=exam.id, examiner_id=examiner.id))
    notify(examiner, f"Assigned to {exam.name}",
           f"Dear {examiner.name},\n\nYou were assigned to {exam.name} ({exam.course.course_code}). "
           f"Slot creation window: {exam.slot_creation_start:%d %b %Y %H:%M} – "
           f"{exam.slot_creation_end:%d %b %Y %H:%M}.\n\n— EMP", "assignment")
    db.session.commit()


def unassign_examiner(exam, examiner):
    a = ExaminerAssignment.query.filter_by(examination_id=exam.id, examiner_id=examiner.id,
                                           status="active").first()
    if not a:
        raise RuleError("Examiner is not assigned.", 404)
    live = Slot.query.filter(Slot.examination_id == exam.id, Slot.examiner_id == examiner.id,
                             Slot.status.in_(["open", "full"])).count()
    if live:
        raise RuleError("This examiner still owns slots for the examination. "
                        "Reassign those slots first.", 409)
    a.status = "removed"
    db.session.commit()


def get_user(user_id, role=None):
    u = db.session.get(User, user_id)
    if u is None or (role and u.role != role):
        raise RuleError("User not found.", 404)
    return u
