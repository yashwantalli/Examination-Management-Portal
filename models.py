"""Database models for the Examination Management Portal (EMP).

All tables are created programmatically by ``db.create_all()`` (see app.py).
SQLite foreign-key enforcement is switched on for every connection.
"""
import sqlite3
from datetime import datetime, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, event, text
from sqlalchemy.engine import Engine
from config import Config
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _enable_sqlite_fk(dbapi_connection, connection_record):  # pragma: no cover - driver hook
    # Only SQLite understands PRAGMA; running it on PostgreSQL is a syntax error.
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def local_now():
    """Current wall-clock time in the configured portal timezone (naive datetime).

    Booking windows are typed in by people in one timezone, so every comparison
    uses that same wall clock regardless of where the server runs (e.g. a UTC cloud host).
    """
    from flask import current_app, has_app_context
    from zoneinfo import ZoneInfo
    tz = current_app.config["TIMEZONE"] if has_app_context() else Config.TIMEZONE
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None, microsecond=0)


# --------------------------------------------------------------------------- users
class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin','examiner','student')", name="ck_users_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=local_now)

    examiner_profile = db.relationship(
        "ExaminerProfile", uselist=False, back_populates="user", cascade="all, delete-orphan"
    )
    student_profile = db.relationship(
        "StudentProfile", uselist=False, back_populates="user", cascade="all, delete-orphan"
    )

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_examiner(self):
        return self.role == "examiner"

    @property
    def is_student(self):
        return self.role == "student"

    @property
    def is_approved_examiner(self):
        return (
            self.is_examiner
            and self.examiner_profile is not None
            and self.examiner_profile.approval_status == "approved"
        )

    def to_dict(self):
        d = {"id": self.id, "name": self.name, "email": self.email, "role": self.role,
             "is_active": self.is_active}
        if self.examiner_profile:
            d["department"] = self.examiner_profile.department
            d["approval_status"] = self.examiner_profile.approval_status
        if self.student_profile:
            d["student_number"] = self.student_profile.student_number
            d["program"] = self.student_profile.program
        return d


class ExaminerProfile(db.Model):
    __tablename__ = "examiner_profiles"
    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('pending','approved','rejected','deactivated')",
            name="ck_examiner_approval",
        ),
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    department = db.Column(db.String(120), nullable=False)
    contact_details = db.Column(db.String(120))
    approval_status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    reviewed_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="examiner_profile")


class StudentProfile(db.Model):
    __tablename__ = "student_profiles"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    student_number = db.Column(db.String(30), nullable=False, unique=True, index=True)
    program = db.Column(db.String(120))
    contact_details = db.Column(db.String(120))

    user = db.relationship("User", back_populates="student_profile")


# ------------------------------------------------------------------ courses & exams
class Course(db.Model):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="ck_course_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    course_code = db.Column(db.String(20), nullable=False, unique=True, index=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="active")
    created_at = db.Column(db.DateTime, nullable=False, default=local_now)

    examinations = db.relationship("Examination", back_populates="course")


EXAM_TYPES = ["Viva", "Practical", "Oral", "Written", "Project Review"]


class Examination(db.Model):
    __tablename__ = "examinations"
    __table_args__ = (
        CheckConstraint("status IN ('draft','active','inactive','completed')", name="ck_exam_status"),
        CheckConstraint("maximum_marks > 0", name="ck_exam_max_marks"),
        CheckConstraint("passing_marks >= 0 AND passing_marks <= maximum_marks", name="ck_exam_pass"),
        CheckConstraint("duration_minutes > 0", name="ck_exam_duration"),
        CheckConstraint("cancellation_hours >= 0", name="ck_exam_cancel_hours"),
        CheckConstraint("slot_creation_end > slot_creation_start", name="ck_exam_slot_window"),
        CheckConstraint("booking_end > booking_start", name="ck_exam_booking_window"),
    )
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    exam_type = db.Column(db.String(40), nullable=False, default="Viva")
    description = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    maximum_marks = db.Column(db.Integer, nullable=False, default=100)
    passing_marks = db.Column(db.Integer, nullable=False, default=40)
    slot_creation_start = db.Column(db.DateTime, nullable=False)
    slot_creation_end = db.Column(db.DateTime, nullable=False)
    booking_start = db.Column(db.DateTime, nullable=False)
    booking_end = db.Column(db.DateTime, nullable=False)
    cancellation_hours = db.Column(db.Integer, nullable=False, default=24)
    slot_creation_open = db.Column(db.Boolean, nullable=False, default=True)
    booking_open = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=local_now)

    course = db.relationship("Course", back_populates="examinations")
    criteria = db.relationship(
        "RubricCriterion", back_populates="examination", order_by="RubricCriterion.id",
        cascade="all, delete-orphan",
    )
    assignments = db.relationship("ExaminerAssignment", back_populates="examination",
                                  cascade="all, delete-orphan")
    slots = db.relationship("Slot", back_populates="examination", order_by="Slot.exam_date")

    # ---- timeline helpers
    def slot_creation_allowed(self, at=None):
        at = at or local_now()
        return (self.status == "active" and self.slot_creation_open
                and self.slot_creation_start <= at <= self.slot_creation_end)

    def booking_allowed(self, at=None):
        at = at or local_now()
        return (self.status == "active" and self.booking_open
                and self.booking_start <= at <= self.booking_end)

    @property
    def free_seats(self):
        now = local_now()
        return sum(s.available_seats for s in self.slots if s.status == "open" and s.start_dt > now)

    @property
    def rubric_total(self):
        return sum(c.maximum_marks for c in self.criteria)

    def active_examiners(self):
        return [a.examiner for a in self.assignments if a.status == "active"]

    def to_dict(self, detail=False):
        d = {
            "id": self.id, "name": self.name, "type": self.exam_type,
            "course": {"id": self.course.id, "code": self.course.course_code, "name": self.course.name},
            "duration_minutes": self.duration_minutes, "maximum_marks": self.maximum_marks,
            "passing_marks": self.passing_marks, "status": self.status,
            "slot_creation_window": {"start": _iso(self.slot_creation_start),
                                     "end": _iso(self.slot_creation_end), "open": self.slot_creation_open},
            "booking_window": {"start": _iso(self.booking_start), "end": _iso(self.booking_end),
                               "open": self.booking_open},
            "cancellation_hours": self.cancellation_hours,
            "booking_allowed_now": self.booking_allowed(),
        }
        if detail:
            d["rubric"] = [c.to_dict() for c in self.criteria]
            d["examiners"] = [{"id": u.id, "name": u.name} for u in self.active_examiners()]
        return d


class RubricCriterion(db.Model):
    __tablename__ = "rubric_criteria"
    __table_args__ = (
        UniqueConstraint("examination_id", "criterion_name", name="uq_rubric_name"),
        CheckConstraint("maximum_marks > 0", name="ck_rubric_max"),
        CheckConstraint("weightage >= 0 AND weightage <= 100", name="ck_rubric_weight"),
    )
    id = db.Column(db.Integer, primary_key=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examinations.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    criterion_name = db.Column(db.String(120), nullable=False)
    maximum_marks = db.Column(db.Integer, nullable=False)
    weightage = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.String(300))

    examination = db.relationship("Examination", back_populates="criteria")

    def to_dict(self):
        return {"id": self.id, "criterion": self.criterion_name, "maximum_marks": self.maximum_marks,
                "weightage": self.weightage, "description": self.description}


class ExaminerAssignment(db.Model):
    __tablename__ = "examiner_assignments"
    __table_args__ = (
        UniqueConstraint("examination_id", "examiner_id", name="uq_assignment"),
        CheckConstraint("status IN ('active','removed')", name="ck_assignment_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examinations.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="active")
    assigned_at = db.Column(db.DateTime, nullable=False, default=local_now)

    examination = db.relationship("Examination", back_populates="assignments")
    examiner = db.relationship("User")


# ------------------------------------------------------------------ slots & bookings
class Slot(db.Model):
    __tablename__ = "slots"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_slot_capacity"),
        CheckConstraint("available_seats >= 0 AND available_seats <= capacity", name="ck_slot_seats"),
        CheckConstraint("end_time > start_time", name="ck_slot_times"),
        CheckConstraint("status IN ('open','full','closed','cancelled')", name="ck_slot_status"),
        Index("ix_slot_exam_date", "examination_id", "exam_date"),
    )
    id = db.Column(db.Integer, primary_key=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examinations.id"), nullable=False, index=True)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    exam_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    venue = db.Column(db.String(120))
    capacity = db.Column(db.Integer, nullable=False)
    available_seats = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=local_now)

    examination = db.relationship("Examination", back_populates="slots")
    examiner = db.relationship("User")
    bookings = db.relationship("Booking", back_populates="slot", order_by="Booking.booked_at")

    @property
    def start_dt(self):
        return datetime.combine(self.exam_date, self.start_time)

    @property
    def end_dt(self):
        return datetime.combine(self.exam_date, self.end_time)

    @property
    def active_bookings(self):
        return [b for b in self.bookings if b.status in ("booked", "completed")]

    @property
    def booked_count(self):
        return self.capacity - self.available_seats

    @property
    def cancellation_deadline(self):
        return self.start_dt - timedelta(hours=self.examination.cancellation_hours)

    def label(self):
        return f"{self.exam_date:%d %b %Y} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def to_dict(self):
        return {
            "id": self.id, "examination_id": self.examination_id,
            "examiner": {"id": self.examiner.id, "name": self.examiner.name},
            "date": self.exam_date.isoformat(), "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"), "venue": self.venue,
            "capacity": self.capacity, "available_seats": self.available_seats, "status": self.status,
        }


class Booking(db.Model):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("status IN ('booked','cancelled','completed')", name="ck_booking_status"),
        # A student may hold at most ONE active (booked/completed) booking per examination.
        # Both dialect options are required: without postgresql_where the index would be a plain
        # unique index on PostgreSQL and a student could never re-book after cancelling.
        Index("uq_active_booking_per_exam", "student_id", "examination_id", unique=True,
              sqlite_where=text("status IN ('booked','completed')"),
              postgresql_where=text("status IN ('booked','completed')")),
    )
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("slots.id"), nullable=False, index=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examinations.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="booked", index=True)
    booked_at = db.Column(db.DateTime, nullable=False, default=local_now)
    cancelled_at = db.Column(db.DateTime)
    cancel_reason = db.Column(db.String(255))
    reschedule_count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=local_now, onupdate=local_now)

    student = db.relationship("User")
    slot = db.relationship("Slot", back_populates="bookings")
    examination = db.relationship("Examination")
    evaluation = db.relationship("Evaluation", uselist=False, back_populates="booking")
    events = db.relationship("BookingEvent", back_populates="booking", order_by="BookingEvent.id")

    @property
    def can_student_cancel(self):
        return self.status == "booked" and local_now() < self.slot.cancellation_deadline

    def to_dict(self):
        d = {
            "id": self.id, "status": self.status, "booked_at": _iso(self.booked_at),
            "cancelled_at": _iso(self.cancelled_at),
            "student": {"id": self.student.id, "name": self.student.name},
            "examination": {"id": self.examination.id, "name": self.examination.name},
            "slot": self.slot.to_dict(), "reschedule_count": self.reschedule_count,
        }
        ev = self.evaluation
        d["result_published"] = bool(ev and ev.published_at)
        return d


class BookingEvent(db.Model):
    """Immutable audit trail of everything that happens to a booking."""
    __tablename__ = "booking_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('booked','cancelled','rescheduled','examiner_reassigned','evaluated','published')",
            name="ck_event_action"),
    )
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, index=True)
    action = db.Column(db.String(30), nullable=False)
    from_slot_id = db.Column(db.Integer, db.ForeignKey("slots.id"))
    to_slot_id = db.Column(db.Integer, db.ForeignKey("slots.id"))
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=local_now, index=True)

    booking = db.relationship("Booking", back_populates="events")
    actor = db.relationship("User")
    from_slot = db.relationship("Slot", foreign_keys=[from_slot_id])
    to_slot = db.relationship("Slot", foreign_keys=[to_slot_id])


# ------------------------------------------------------------------ evaluations
class Evaluation(db.Model):
    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint("total_marks >= 0", name="ck_eval_total"),
        CheckConstraint("status IN ('submitted','published')", name="ck_eval_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, unique=True)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    total_marks = db.Column(db.Float, nullable=False, default=0)
    remarks = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="submitted")
    evaluated_at = db.Column(db.DateTime, nullable=False, default=local_now)
    published_at = db.Column(db.DateTime)

    booking = db.relationship("Booking", back_populates="evaluation")
    examiner = db.relationship("User")
    scores = db.relationship("EvaluationScore", back_populates="evaluation",
                             cascade="all, delete-orphan", order_by="EvaluationScore.id")

    @property
    def passed(self):
        return self.total_marks >= self.booking.examination.passing_marks

    @property
    def percentage(self):
        return round(100.0 * self.total_marks / self.booking.examination.maximum_marks, 1)


class EvaluationScore(db.Model):
    __tablename__ = "evaluation_scores"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "rubric_criterion_id", name="uq_eval_criterion"),
        CheckConstraint("marks_awarded >= 0", name="ck_score_nonneg"),
    )
    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluations.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    rubric_criterion_id = db.Column(db.Integer, db.ForeignKey("rubric_criteria.id"), nullable=False)
    marks_awarded = db.Column(db.Float, nullable=False)
    comment = db.Column(db.String(255))

    evaluation = db.relationship("Evaluation", back_populates="scores")
    criterion = db.relationship("RubricCriterion")


# ------------------------------------------------------------------ notifications
class Notification(db.Model):
    """E-mail outbox. Every notification is stored; it is also sent if SMTP is configured."""
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("status IN ('logged','sent','failed')", name="ck_notification_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    recipient_email = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="logged")
    created_at = db.Column(db.DateTime, nullable=False, default=local_now, index=True)

    user = db.relationship("User")


def _iso(value):
    return value.isoformat() if value else None
