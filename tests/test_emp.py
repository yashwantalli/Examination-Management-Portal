"""Automated tests for the core business rules, access control and API.

Run:  python -m pytest -q
Each test uses a fresh in-memory SQLite database.
"""
import os
import sys
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from config import TestConfig  # noqa: E402
from models import (Booking, Course, ExaminerAssignment, ExaminerProfile, Examination,  # noqa: E402
                    Notification, RubricCriterion, Slot, StudentProfile, User, db, local_now)
from services import (RuleError, cancel_booking, create_booking, create_slot,  # noqa: E402
                      publish_results, reassign_slot_examiner, reschedule_booking, save_evaluation,
                      update_slot)

PW = "Passw0rd!"


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(role, email, name="User", approval="approved", number=None):
    u = User(name=name, email=email, role=role, is_active=True)
    u.set_password(PW)
    if role == "examiner":
        u.examiner_profile = ExaminerProfile(department="CS", approval_status=approval)
    if role == "student":
        u.student_profile = StudentProfile(student_number=number or email.split("@")[0].upper()[:12] + "01")
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def world(app):
    """A course, one examination with open windows and rubric, two examiners and three students."""
    now = local_now()
    course = Course(course_code="CS301", name="DBMS")
    exam = Examination(course=course, name="DBMS Viva", exam_type="Viva", duration_minutes=30,
                       maximum_marks=50, passing_marks=20, cancellation_hours=24,
                       slot_creation_start=now - timedelta(days=1), slot_creation_end=now + timedelta(days=5),
                       booking_start=now - timedelta(days=1), booking_end=now + timedelta(days=10),
                       status="active")
    exam.criteria = [RubricCriterion(criterion_name="SQL", maximum_marks=20, weightage=40),
                     RubricCriterion(criterion_name="Theory", maximum_marks=30, weightage=60)]
    db.session.add_all([course, exam])
    db.session.commit()
    ex1 = make_user("examiner", "ex1@t.io", "Examiner One")
    ex2 = make_user("examiner", "ex2@t.io", "Examiner Two")
    for x in (ex1, ex2):
        db.session.add(ExaminerAssignment(examination_id=exam.id, examiner_id=x.id))
    db.session.commit()
    students = [make_user("student", f"s{i}@t.io", f"Student {i}", number=f"STU00{i}") for i in range(3)]
    admin = User.query.filter_by(role="admin").first()
    return {"exam": exam, "ex1": ex1, "ex2": ex2, "students": students, "admin": admin, "now": now}


def future_slot(world, examiner=None, days=3, hour=10, capacity=2):
    day = (world["now"] + timedelta(days=days)).date()
    return create_slot(examiner or world["ex1"], world["exam"], day.isoformat(), f"{hour:02d}:00",
                       f"{hour:02d}:45", capacity, "Lab 1")


def past_slot_with_booking(world, examiner=None):
    """Insert a slot that already happened plus a booking in it (history data)."""
    yday = (local_now() - timedelta(days=1)).date()
    from datetime import time
    slot = Slot(examination_id=world["exam"].id, examiner_id=(examiner or world["ex1"]).id, exam_date=yday,
                start_time=time(10, 0), end_time=time(11, 0), capacity=3, available_seats=2, status="open")
    db.session.add(slot)
    db.session.flush()
    b = Booking(student_id=world["students"][0].id, slot_id=slot.id, examination_id=world["exam"].id)
    db.session.add(b)
    db.session.commit()
    return slot, b


def login(client, email, pw=PW):
    return client.post("/login", data={"email": email, "password": pw})


# ----------------------------------------------------------------------------- schema & auth
def test_admin_is_seeded_and_tables_exist(app):
    admin = User.query.filter_by(role="admin").one()
    assert admin.email == TestConfig.ADMIN_EMAIL
    assert admin.check_password(TestConfig.ADMIN_PASSWORD)
    assert admin.password_hash != TestConfig.ADMIN_PASSWORD  # hashed, never plain text


def test_public_registration_cannot_create_admin(client):
    r = client.post("/register", data={"role": "admin", "name": "Mallory", "email": "m@t.io",
                                       "password": "abcd1234", "confirm_password": "abcd1234",
                                       "student_number": "MAL001"})
    assert r.status_code == 302
    assert User.query.filter_by(email="m@t.io").one().role == "student"
    assert User.query.filter_by(role="admin").count() == 1


def test_student_registration_validation(client):
    r = client.post("/register", data={"role": "student", "name": "A", "email": "bad",
                                       "password": "short", "confirm_password": "x", "student_number": "!"})
    assert r.status_code == 400
    assert User.query.count() == 1  # only admin


def test_pending_examiner_is_blocked_until_approved(client, app):
    client.post("/register", data={"role": "examiner", "name": "New Examiner", "email": "new@t.io",
                                   "department": "CS", "password": "abcd1234", "confirm_password": "abcd1234"})
    login(client, "new@t.io", "abcd1234")
    r = client.get("/examiner/")
    assert r.status_code == 302 and "/examiner/pending" in r.headers["Location"]
    assert client.get("/examiner/slots/new").status_code == 302
    u = User.query.filter_by(email="new@t.io").one()
    u.examiner_profile.approval_status = "approved"
    db.session.commit()
    assert client.get("/examiner/").status_code == 200


def test_wrong_role_gets_403(client, world):
    login(client, "s0@t.io")
    assert client.get("/admin/").status_code == 403
    assert client.get("/examiner/").status_code == 403
    client.post("/logout")
    login(client, "ex1@t.io")
    assert client.get("/student/").status_code == 403
    assert client.get("/admin/exams").status_code == 403


def test_anonymous_redirected_to_login(client):
    r = client.get("/student/bookings")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_deactivated_user_cannot_log_in(client, world):
    s = world["students"][0]
    s.is_active = False
    db.session.commit()
    assert login(client, s.email).status_code == 403


# ----------------------------------------------------------------------------- slots & timeline
def test_slot_creation_respects_window_and_switch(world):
    exam = world["exam"]
    future_slot(world)  # inside window: OK
    exam.slot_creation_open = False
    db.session.commit()
    with pytest.raises(RuleError, match="closed"):
        future_slot(world, hour=12)
    exam.slot_creation_open = True
    exam.slot_creation_end = local_now() - timedelta(minutes=1)
    db.session.commit()
    with pytest.raises(RuleError, match="closed"):
        future_slot(world, hour=14)


def test_slot_rules_duration_overlap_and_assignment(world):
    day = (world["now"] + timedelta(days=3)).date().isoformat()
    with pytest.raises(RuleError, match="at least 30"):
        create_slot(world["ex1"], world["exam"], day, "10:00", "10:15", 2)
    future_slot(world, hour=10)
    with pytest.raises(RuleError, match="already have a slot"):
        create_slot(world["ex1"], world["exam"], day, "10:30", "11:15", 2)
    outsider = make_user("examiner", "out@t.io")
    with pytest.raises(RuleError, match="not assigned"):
        create_slot(outsider, world["exam"], day, "15:00", "16:00", 2)


def test_slot_with_bookings_cannot_change_time_or_shrink(world):
    slot = future_slot(world, capacity=3)
    create_booking(world["students"][0], slot)
    create_booking(world["students"][1], slot)
    with pytest.raises(RuleError, match="locked"):
        update_slot(world["ex1"], slot, slot.exam_date.isoformat(), "13:00", "13:45", 3)
    with pytest.raises(RuleError, match="Capacity cannot be lower"):
        update_slot(world["ex1"], slot, slot.exam_date.isoformat(), "10:00", "10:45", 1)


# ----------------------------------------------------------------------------- bookings
def test_booking_window_enforced(world):
    slot = future_slot(world)
    exam, s = world["exam"], world["students"][0]
    exam.booking_start = local_now() + timedelta(hours=2)
    db.session.commit()
    with pytest.raises(RuleError, match="Booking opens"):
        create_booking(s, slot)
    exam.booking_start = local_now() - timedelta(days=2)
    exam.booking_end = local_now() - timedelta(minutes=1)
    db.session.commit()
    with pytest.raises(RuleError, match="ended"):
        create_booking(s, slot)
    exam.booking_end = local_now() + timedelta(days=5)
    exam.booking_open = False
    db.session.commit()
    with pytest.raises(RuleError, match="closed by the administrator"):
        create_booking(s, slot)


def test_capacity_and_full_status(world):
    slot = future_slot(world, capacity=2)
    a, b, c = world["students"]
    create_booking(a, slot)
    create_booking(b, slot)
    db.session.refresh(slot)
    assert slot.available_seats == 0 and slot.status == "full"
    with pytest.raises(RuleError, match="full"):
        create_booking(c, slot)
    assert Booking.query.filter_by(slot_id=slot.id, status="booked").count() == 2


def test_no_duplicate_booking_for_same_exam(world):
    s1 = future_slot(world, hour=10)
    s2 = future_slot(world, examiner=world["ex2"], hour=14)
    create_booking(world["students"][0], s1)
    with pytest.raises(RuleError, match="already have a booking"):
        create_booking(world["students"][0], s2)


def test_database_unique_index_blocks_duplicate_active_booking(world):
    s1 = future_slot(world, hour=10)
    st = world["students"][0]
    create_booking(st, s1)
    db.session.add(Booking(student_id=st.id, slot_id=s1.id, examination_id=world["exam"].id, status="booked"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_cancel_then_rebook_and_deadline(world):
    slot = future_slot(world, capacity=1)
    st = world["students"][0]
    b = create_booking(st, slot)
    cancel_booking(b, st)
    db.session.refresh(slot)
    assert slot.available_seats == 1 and slot.status == "open" and b.status == "cancelled"
    b2 = create_booking(st, slot)  # allowed again after cancelling
    world["exam"].cancellation_hours = 24 * 30  # deadline now in the past
    db.session.commit()
    with pytest.raises(RuleError, match="deadline"):
        cancel_booking(b2, st)
    cancel_booking(b2, world["admin"], "Admin override")  # admin may still cancel
    assert b2.status == "cancelled"
    actions = [e.action for e in b.events] + [e.action for e in b2.events]
    assert actions.count("cancelled") == 2


def test_booking_sends_confirmation_email(world):
    slot = future_slot(world)
    create_booking(world["students"][0], slot)
    n = Notification.query.filter_by(user_id=world["students"][0].id, category="booking_confirmation").one()
    assert "confirmed" in n.subject.lower()


# ----------------------------------------------------------------------------- admin controls
def test_reschedule_preserves_capacity_and_history(world):
    s1 = future_slot(world, hour=10, capacity=1)
    s2 = future_slot(world, examiner=world["ex2"], hour=14, capacity=1)
    b = create_booking(world["students"][0], s1)
    with pytest.raises(RuleError, match="Only the administrator"):
        reschedule_booking(b, s2, world["students"][0])
    reschedule_booking(b, s2, world["admin"], "Examiner unavailable")
    db.session.refresh(s1)
    db.session.refresh(s2)
    assert b.slot_id == s2.id and b.reschedule_count == 1
    assert s1.available_seats == 1 and s1.status == "open"
    assert s2.available_seats == 0 and s2.status == "full"
    assert [e.action for e in b.events] == ["booked", "rescheduled"]
    create_booking(world["students"][1], s1)  # s1 is now full again
    with pytest.raises(RuleError, match="no free seats"):
        reschedule_booking(b, s1, world["admin"])


def test_reassign_examiner(world):
    slot = future_slot(world)
    b = create_booking(world["students"][0], slot)
    reassign_slot_examiner(slot, world["ex2"], world["admin"], "Leave")
    assert slot.examiner_id == world["ex2"].id
    assert b.events[-1].action == "examiner_reassigned"
    outsider = make_user("examiner", "zz@t.io")
    with pytest.raises(RuleError, match="not assigned"):
        reassign_slot_examiner(slot, outsider, world["admin"])


# ----------------------------------------------------------------------------- evaluation
def test_evaluation_ownership_and_marks_validation(world, client):
    slot, b = past_slot_with_booking(world)
    crit = world["exam"].criteria
    with pytest.raises(RuleError, match="own slots"):
        save_evaluation(world["ex2"], b, {crit[0].id: 10, crit[1].id: 10}, "")
    with pytest.raises(RuleError, match="between 0 and 20"):
        save_evaluation(world["ex1"], b, {crit[0].id: 25, crit[1].id: 10}, "")
    with pytest.raises(RuleError, match="Enter marks"):
        save_evaluation(world["ex1"], b, {crit[0].id: 5}, "")
    ev = save_evaluation(world["ex1"], b, {crit[0].id: 15, crit[1].id: 20.5}, "Good")
    assert ev.total_marks == 35.5 and b.status == "completed"
    ev = save_evaluation(world["ex1"], b, {crit[0].id: 16, crit[1].id: 20}, "Updated")  # update, not duplicate
    assert ev.total_marks == 36 and len(ev.scores) == 2
    # HTTP: another examiner cannot even open the evaluation page
    login(client, "ex2@t.io")
    assert client.get(f"/examiner/bookings/{b.id}/evaluate").status_code == 403


def test_cannot_evaluate_before_slot_starts(world):
    slot = future_slot(world)
    b = create_booking(world["students"][0], slot)
    with pytest.raises(RuleError, match="after the slot has started"):
        save_evaluation(world["ex1"], b, {c.id: 1 for c in world["exam"].criteria}, "")


def test_results_hidden_until_published(world, client):
    slot, b = past_slot_with_booking(world)
    save_evaluation(world["ex1"], b, {c.id: 10 for c in world["exam"].criteria}, "ok")
    login(client, "s0@t.io")
    assert client.get(f"/student/results/{b.id}").status_code == 404
    assert client.get(f"/student/results/{b.id}/scorecard.pdf").status_code == 404
    assert publish_results(world["exam"], world["admin"]) == 1
    assert client.get(f"/student/results/{b.id}").status_code == 200
    pdf = client.get(f"/student/results/{b.id}/scorecard.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    with pytest.raises(RuleError, match="locked"):
        save_evaluation(world["ex1"], b, {c.id: 1 for c in world["exam"].criteria}, "")
    # other students cannot read it
    client.post("/logout")
    login(client, "s1@t.io")
    assert client.get(f"/student/results/{b.id}").status_code == 403


# ----------------------------------------------------------------------------- HTTP flows
def test_student_books_via_form(world, client):
    slot = future_slot(world)
    login(client, "s0@t.io")
    r = client.post(f"/student/slots/{slot.id}/book", follow_redirects=True)
    assert r.status_code == 200 and b"Booked!" in r.data
    r = client.post(f"/student/slots/{slot.id}/book", follow_redirects=True)
    assert b"already have a booking" in r.data


def test_csrf_protection(world):
    from config import Config

    class CsrfOn(TestConfig):
        CSRF_ENABLED = True
    app2 = create_app(CsrfOn)
    with app2.app_context():
        c = app2.test_client()
        assert c.post("/login", data={"email": "x", "password": "y"}).status_code == 400
    assert Config.CSRF_ENABLED


# ----------------------------------------------------------------------------- API
def test_api_auth_and_booking(world, client):
    slot = future_slot(world)
    assert client.get("/api/v1/examinations").status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "s0@t.io", "password": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "s0@t.io", "password": PW})
    assert r.status_code == 200 and r.json["user"]["role"] == "student"
    exams = client.get("/api/v1/examinations").json
    assert exams[0]["name"] == "DBMS Viva" and exams[0]["booking_allowed_now"] is True
    assert client.get(f"/api/v1/examinations/{exams[0]['id']}/slots?available=true").json[0]["id"] == slot.id
    r = client.post("/api/v1/bookings", json={"slot_id": slot.id})
    assert r.status_code == 201 and r.json["status"] == "booked"
    r = client.post("/api/v1/bookings", json={"slot_id": slot.id})
    assert r.status_code == 409
    assert client.get("/api/v1/students").status_code == 403
    bid = client.get("/api/v1/bookings").json[0]["id"]
    assert client.delete(f"/api/v1/bookings/{bid}").json["status"] == "cancelled"


def test_api_admin_endpoints(world, client):
    pending = make_user("examiner", "p@t.io", approval="pending")
    client.post("/api/v1/auth/login", json={"email": TestConfig.ADMIN_EMAIL, "password": TestConfig.ADMIN_PASSWORD})
    assert len(client.get("/api/v1/students").json) == 3
    assert client.get("/api/v1/examiners?status=pending").json[0]["id"] == pending.id
    r = client.patch(f"/api/v1/examiners/{pending.id}", json={"approval_status": "approved"})
    assert r.status_code == 200 and r.json["approval_status"] == "approved"
    stats = client.get("/api/v1/stats").json
    assert stats["counts"]["examinations"] == 1
