"""Guards for PostgreSQL compatibility that SQLite alone would never catch."""
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

from models import Booking


def _active_booking_index():
    return next(i for i in Booking.__table__.indexes if i.name == "uq_active_booking_per_exam")


def test_active_booking_index_is_partial_on_postgresql():
    sql = str(CreateIndex(_active_booking_index()).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in sql and "WHERE status IN ('booked','completed')" in sql


def test_active_booking_index_is_partial_on_sqlite():
    sql = str(CreateIndex(_active_booking_index()).compile(dialect=sqlite.dialect()))
    assert "WHERE status IN ('booked','completed')" in sql
