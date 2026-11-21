"""Real concurrency tests: many simultaneous booking requests against PostgreSQL.

These prove the atomic seat decrement and the partial unique index hold under genuine
parallel transactions, which SQLite's in-memory test database cannot demonstrate.
Run with:  TEST_DATABASE_URL=postgresql://user:pass@localhost/emp_test python -m pytest tests/test_concurrency.py
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from models import Booking, Slot, User, db
from services import RuleError, create_booking
from tests.test_emp import future_slot, make_user, world  # noqa: F401  (world is a fixture)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgres"),
    reason="needs PostgreSQL: set TEST_DATABASE_URL",
)


def _book_in_parallel(app, jobs):
    """Run (student_id, slot_id) booking attempts at the same instant; return per-job outcomes."""
    barrier = threading.Barrier(len(jobs))

    def attempt(job):
        student_id, slot_id = job
        barrier.wait(timeout=10)  # release every thread at once, before any DB connection is held
        with app.app_context():
            student, slot = db.session.get(User, student_id), db.session.get(Slot, slot_id)
            try:
                create_booking(student, slot)
                return "booked"
            except RuleError as err:
                return f"refused:{err.status}"
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(attempt, jobs))


def test_last_seat_goes_to_exactly_one_of_many_students(app, world):  # noqa: F811
    slot = future_slot(world, capacity=1)
    students = [make_user("student", f"race{i}@t.io", f"Racer {i}", number=f"RACE{i:03d}") for i in range(10)]
    slot_id, ids = slot.id, [s.id for s in students]

    outcomes = _book_in_parallel(app, [(sid, slot_id) for sid in ids])

    assert outcomes.count("booked") == 1
    assert outcomes.count("refused:409") == 9
    db.session.expire_all()
    assert Booking.query.filter_by(slot_id=slot_id, status="booked").count() == 1
    assert db.session.get(Slot, slot_id).available_seats == 0


def test_capacity_is_never_exceeded_under_load(app, world):  # noqa: F811
    slot = future_slot(world, capacity=5)
    students = [make_user("student", f"load{i}@t.io", f"Loader {i}", number=f"LOAD{i:03d}") for i in range(16)]
    slot_id, ids = slot.id, [s.id for s in students]

    outcomes = _book_in_parallel(app, [(sid, slot_id) for sid in ids])

    assert outcomes.count("booked") == 5
    db.session.expire_all()
    assert Booking.query.filter_by(slot_id=slot_id, status="booked").count() == 5
    assert db.session.get(Slot, slot_id).available_seats == 0


def test_one_student_cannot_hold_two_bookings_for_an_exam_even_when_racing(app, world):  # noqa: F811
    slots = [future_slot(world, hour=9 + i, capacity=3) for i in range(4)]
    student_id = world["students"][0].id
    slot_ids = [s.id for s in slots]

    outcomes = _book_in_parallel(app, [(student_id, sid) for sid in slot_ids])

    assert outcomes.count("booked") == 1
    db.session.expire_all()
    assert Booking.query.filter_by(student_id=student_id, status="booked").count() == 1
    # No seat may leak: exactly one seat is taken across all four slots.
    taken = sum(s.capacity - db.session.get(Slot, s.id).available_seats for s in slots)
    assert taken == 1


def test_a_cancelled_student_can_book_again_on_postgres(app, world):  # noqa: F811
    """Regression test for the partial-index bug: Postgres once blocked re-booking forever."""
    from services import cancel_booking
    slot = future_slot(world, capacity=2)
    student = world["students"][0]
    booking = create_booking(student, slot)
    cancel_booking(booking, student)
    again = create_booking(student, slot)
    assert again.id != booking.id and again.status == "booked"
